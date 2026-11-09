# /backend/app/services/job_postings.py
"""Read job postings from a careers page's structured data.

Corporate careers pages are usually single-page apps: the HTML a crawler
receives is a shell, and the roles are rendered by JavaScript. Tavily therefore
returns 300–5900 characters of navigation text and no roles at all (measured on
careers.amd.com: eleven URLs, zero role lines).

The pages do, however, publish schema.org `JobPosting` blocks for search engines
and job aggregators. That markup is the page's own machine-readable statement of
its openings, so it is read directly: one request, no JavaScript, and no
dependence on how Tavily chose to render the page.
"""
from __future__ import annotations

import html as html_module
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

from app.services.logger_service import get_logger

logger = get_logger("job_postings")

FETCH_TIMEOUT_SECONDS = 20

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Schema.org blocks are embedded as JSON in a script tag. Some pages ship several
# blocks, and a block may hold a single object or an array.
JSONLD_BLOCK_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.S | re.IGNORECASE,
)

# Description is HTML; only its text is wanted.
TAG_RE = re.compile(r"<[^>]+>")

# A posting's description is long. Anything shorter is a stub.
MIN_DESCRIPTION_CHARS = 40

# Cap roles per page so one aggregator listing cannot fill the hiring lane.
MAX_POSTINGS_PER_PAGE = 10


@dataclass(frozen=True)
class JobPosting:
    """One opening, as the page itself describes it."""

    title: str
    description: str
    location: Optional[str]
    employment_type: Optional[str]
    posted_date: Optional[str]

    def to_line(self) -> str:
        """Render the posting as one readable line for the hiring lane."""
        parts = [f"{self.title}"]
        if self.location:
            parts.append(f"location: {self.location}")
        if self.employment_type:
            parts.append(f"type: {self.employment_type}")
        if self.description:
            parts.append(self.description)
        return " | ".join(part for part in parts if part)


# Employment types that carry no meaning for a reader.
EMPTY_EMPLOYMENT_TYPES = ("unavailable", "other", "none", "")

# A posting's description is long. Only the opening is needed to say what the
# role is; the rest is requirements boilerplate.
MAX_DESCRIPTION_CHARS = 320


def _strip_html(value: str) -> str:
    """Return plain text from an HTML fragment, collapsing whitespace."""
    text = TAG_RE.sub(" ", value or "")
    text = html_module.unescape(text)
    return " ".join(text.split())


def _clean_employment_type(value: object) -> Optional[str]:
    """Return a usable employment type, or None when the page sent a filler."""
    if isinstance(value, list):
        value = value[0] if value else None
    text = _strip_html(str(value or ""))
    if text.strip().lower() in EMPTY_EMPLOYMENT_TYPES:
        return None
    return text.strip() or None


def _iter_ld_blocks(page_html: str):
    """Yield every decoded JSON-LD document on the page."""
    for raw in JSONLD_BLOCK_RE.findall(page_html):
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            logger.info("Skipped malformed JSON-LD block")


def _walk(node):
    """Yield every dict inside a JSON-LD document, including nested graphs."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _location_text(node: dict) -> Optional[str]:
    """Return the posting location, which schema.org allows in several shapes."""
    location = node.get("jobLocation")
    if isinstance(location, list):
        location = location[0] if location else None
    if not isinstance(location, dict):
        return None

    address = location.get("address")
    if isinstance(address, dict):
        parts = [
            str(address.get(key) or "").strip()
            for key in ("addressLocality", "addressRegion", "addressCountry")
        ]
        joined = ", ".join(part for part in parts if part)
        if joined:
            return joined

    name = location.get("name")
    return str(name).strip() if name else None


# A posting detail URL, which carries a JobPosting block even when the board's
# index page is a JavaScript shell. Most boards publish no JobPosting on the
# index at all (measured: 0 on Lever, Greenhouse, SmartRecruiters and iCIMS
# indexes), so detail pages are where the roles actually live.
#
# The shapes differ by platform, and the old numeric-id rule matched none of
# them: Lever uses /{org}/{uuid}, SmartRecruiters /{org}/{digits}-{slug},
# Greenhouse /{org}/jobs/{digits}, iCIMS /jobs/{digits}/{slug}/job. Index,
# pagination and login links are excluded explicitly, because they look similar
# and cost a wasted fetch each.
POSTING_DETAIL_RE = re.compile(
    r"""href=["'](?P<href>(?:https?://[^"'/]+)?(?:/[^"'#]*)+)["']""",
    re.IGNORECASE,
)

# Path shapes that identify one posting rather than a board or a filter page.
POSTING_DETAIL_PATTERNS = (
    # Lever: /{org}/{uuid}. Greenhouse and SmartRecruiters: numeric ids.
    re.compile(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE),
    re.compile(r"/[0-9]{6,}(?:-|/|$|\?)", re.IGNORECASE),
    re.compile(r"/jobs?/\d", re.IGNORECASE),
    # Slug-based boards, which must also sit under a jobs/careers path:
    # /join-us/jobs/{dept}/{city}/{role-slug}/, /careers/{role-slug}.
    # Anchoring on the jobs segment is what stops an unrelated multi-hyphen
    # path (webinar.amd.com/meet-the-experts-hub/en/home) being fetched as a
    # posting.
    re.compile(
        r"/(?:jobs?|careers?|positions?|openings?)/[^/]+(?:/[^/]+)*[a-z0-9]+(?:-[a-z0-9]+){2,}(?:/|$|\?)",
        re.IGNORECASE,
    ),
)

# Links that are never a single posting.
NOT_A_POSTING_RE = re.compile(
    r"(?:page=|start=|search=|categories=|country=|login|signin|sign-in|/jobs?$|/careers?$"
    r"|/locations?$|/talentcommunity|/candidate-portal|/undefined)",
    re.IGNORECASE,
)

# How many detail pages to open when the index yields nothing. A handful is
# enough to prove the company is hiring; the exact list is not the goal.
# How many detail pages to open when the index yields nothing. A handful is
# enough to prove the company is hiring; the exact list is not the goal.
MAX_DETAIL_FETCHES = 5


def postings_from_html(
    page_html: str,
    employer: str = "",
) -> List[JobPosting]:
    """Extract every posting described by a page's structured data.

    Two shapes are read, because boards publish openings as different structured
    data:

    * **`JobPosting`** — one object per role, which is what a board's detail page
      and most vendor indexes use.
    * **`ItemList`** — an index of roles, each entry carrying a title, a URL and
      the employer it belongs to. LinkedIn publishes its jobs page this way.
      Measured: 60 entries, and the entries are a *mix* — a page for "Canada
      Goose jobs" listed Callaway Golf, Acushnet and Cole Haan alongside three
      Canada Goose roles, because the index is padded with related listings.

    ``employer`` is what separates those: an `ItemList` entry naming a different
    employer is dropped, since attributing another company's opening to this one
    is the failure the subject routing exists to prevent. A `JobPosting` block has
    no employer field on most boards, so it is not filtered here.
    """
    found: List[JobPosting] = []
    seen_titles: set[str] = set()
    wanted = " ".join((employer or "").split()).lower()

    for document in _iter_ld_blocks(page_html):
        # An ItemList holds its roles in `itemListElement`, so each entry is read
        # as its own posting. Passing the list node itself to the single-entry
        # reader found no title and silently produced nothing.
        for node in _walk(document):
            kind = str(node.get("@type") or "").lower()
            if kind == "itemlist":
                for entry in node.get("itemListElement") or []:
                    if not isinstance(entry, dict):
                        continue
                    posting = _posting_from_itemlist(entry, wanted)
                    if posting is None or posting.title.lower() in seen_titles:
                        continue
                    seen_titles.add(posting.title.lower())
                    found.append(posting)
                    if len(found) >= MAX_POSTINGS_PER_PAGE:
                        return found
                continue

            if kind != "jobposting":
                continue
            posting = _posting_from_jobposting(node, wanted)
            if posting is None or posting.title.lower() in seen_titles:
                continue
            seen_titles.add(posting.title.lower())
            found.append(posting)
            if len(found) >= MAX_POSTINGS_PER_PAGE:
                return found

    return found


def _posting_from_jobposting(
    node: dict,
    wanted_employer: str = "",
) -> Optional[JobPosting]:
    title = _strip_html(str(node.get("title") or node.get("name") or ""))
    if not title:
        return None

    # A detail page states whose role it is in `hiringOrganization`. When the page
    # was reached from a mixed index (LinkedIn pads its list with related
    # employers), the only thing separating our role from Callaway Golf's is this
    # field. A block that names an employer we are not briefing is dropped; a
    # block that names none is kept, because most boards omit it on their own
    # careers pages and those are the employer's by construction.
    if wanted_employer:
        hiring = node.get("hiringOrganization")
        if isinstance(hiring, dict):
            hiring = hiring.get("name")
        if hiring:
            claimed = " ".join(str(hiring).split()).lower()
            if wanted_employer not in claimed and claimed not in wanted_employer:
                return None

    description = _strip_html(str(node.get("description") or ""))
    if len(description) < MIN_DESCRIPTION_CHARS:
        description = ""
    else:
        description = description[:MAX_DESCRIPTION_CHARS].rstrip()

    return JobPosting(
        title=title,
        description=description,
        location=_location_text(node),
        employment_type=_clean_employment_type(node.get("employmentType")),
        posted_date=str(node.get("datePosted") or "").strip() or None,
    )


# An ItemList entry names its employer in one of these fields. LinkedIn uses
# `disambiguatingDescription`; schema.org defines no employer for a list item, so
# the field is whatever the site chose. `description` is deliberately **not** in
# this list: LinkedIn puts the *location* there ("Fort Worth, TX"), and reading it
# as the employer attributed Callaway Golf's roles to Canada Goose.
_ITEM_LIST_EMPLOYER_FIELDS = (
    "disambiguatingDescription",
    "hiringOrganization",
    "employer",
)

# An ItemList entry may carry the location in `description`. Only used when it
# looks like a place rather than an organization name.
_ITEM_LIST_LOCATION_FIELDS = ("description", "jobLocation")


def _item_list_employer(node: dict) -> str:
    """Return the employer an ItemList entry names, or an empty string."""
    for field_name in _ITEM_LIST_EMPLOYER_FIELDS:
        value = node.get(field_name)
        if isinstance(value, dict):
            value = value.get("name")
        if value:
            text = " ".join(str(value).split())
            if text:
                return text
    return ""


def _item_list_location(node: dict) -> Optional[str]:
    """Return the posting's location from an ItemList entry, if it states one."""
    for field_name in _ITEM_LIST_LOCATION_FIELDS:
        value = node.get(field_name)
        if isinstance(value, dict):
            value = _location_text(node)
        if value:
            text = " ".join(str(value).split())
            if text:
                return text
    return None


def _posting_from_itemlist(node: dict, wanted_employer: str) -> Optional[JobPosting]:
    """Return one posting from an ItemList entry, when it belongs to the employer."""
    title = _strip_html(str(node.get("name") or node.get("title") or ""))
    if not title:
        return None

    employer = _item_list_employer(node)
    if wanted_employer and employer:
        # Containment either way, because a page writes "Canada Goose" while a
        # title writes "Canada Goose Holdings Inc.".
        if wanted_employer not in employer.lower() and employer.lower() not in wanted_employer:
            return None
    elif wanted_employer and not employer:
        # An entry that names no employer cannot be attributed, so it is dropped
        # rather than guessed.
        return None

    return JobPosting(
        title=title,
        description="",
        location=_item_list_location(node),
        employment_type=None,
        posted_date=None,
    )


def fetch_page(url: str) -> Optional[str]:
    """Return the page HTML, or None when it cannot be read."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            if response.status >= 400:
                logger.info("Careers page blocked | url=%s | status=%s", url, response.status)
                return None
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        logger.info("Careers page rejected | url=%s | status=%s", url, exc.code)
        return None
    except Exception as exc:
        logger.info("Careers page unreadable | url=%s | %s", url, exc)
        return None


def _is_posting_detail(href: str) -> bool:
    """True when a link points at one posting rather than a board or a filter."""
    if NOT_A_POSTING_RE.search(href):
        return False
    return any(pattern.search(href) for pattern in POSTING_DETAIL_PATTERNS)


def _detail_links(page_html: str, base_url: str, limit: int) -> List[str]:
    """Return posting-detail URLs found on a board's index page.

    Absolute links are kept even when they point at another host: a board page
    hosted on an aggregator (boards.greenhouse.io/optiverus) can list roles that
    live on the employer's own site, and those are the real postings. Relative
    links are resolved against the page's own origin, because they belong to it
    by definition.
    """
    base = base_url.rstrip("/")
    origin = "/".join(base.split("/")[:3])

    links: List[str] = []
    seen: set[str] = set()
    for match in POSTING_DETAIL_RE.finditer(page_html):
        href = match.group("href").strip()
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = origin + href
        elif not href.startswith("http"):
            href = f"{base}/{href.lstrip('./')}"

        if not _is_posting_detail(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append(href)
        if len(links) >= limit:
            break
    return links


def postings_from_url(url: str, employer: str = "") -> List[JobPosting]:
    """Fetch a careers page and return the openings it publishes.

    Three shapes are handled, in the order that avoids wasted requests:

    1. **The URL is itself a vendor board** — Workday answers its own JSON listing,
       which carries every opening. This runs before any page fetch because the
       board's HTML is a shell (measured: 7,446 bytes, zero postings) while the
       listing returned all 96.
    2. **The page carries schema.org `JobPosting` markup** — read directly.
    3. **The page delegates to a vendor board** — an employer's own careers page
       frequently links out to Workday rather than listing roles itself (measured:
       two Workday URLs, no roles of its own). The board behind that link is read
       instead, which is how 96 roles were recovered from a page that showed none.

    Small boards that publish role titles as plain text fall through to the prose
    reader in `hiring._collect_posting`, which the caller runs when this returns
    nothing.
    """
    workday = _workday_cx_endpoint(url)
    if workday:
        postings = _postings_from_workday(workday)
        if postings:
            return postings

    page_html = fetch_page(url)
    if not page_html:
        return []

    postings = postings_from_html(page_html, employer)
    if postings:
        logger.info("Careers page read | url=%s | postings=%s", url, len(postings))
        return postings

    boards = _board_links(page_html)
    for board_url in boards:
        cx = _workday_cx_endpoint(board_url)
        if not cx:
            continue
        postings = _postings_from_workday(cx)
        if postings:
            logger.info(
                "Careers page delegates to a board | page=%s | board=%s | postings=%s",
                url, board_url, len(postings),
            )
            return postings

    details = _detail_links(page_html, url, MAX_DETAIL_FETCHES)
    recovered: List[JobPosting] = []
    seen_titles: set[str] = set()
    for detail_url in details:
        detail_html = fetch_page(detail_url)
        if not detail_html:
            continue
        for posting in postings_from_html(detail_html, employer):
            if posting.title.lower() in seen_titles:
                continue
            seen_titles.add(posting.title.lower())
            recovered.append(posting)

    logger.info(
        "Careers page read | url=%s | postings=%s | boards=%s | detail_pages=%s "
        "| recovered=%s",
        url,
        len(postings),
        len(boards),
        len(details),
        len(recovered),
    )
    return recovered


def _board_links(page_html: str) -> List[str]:
    """Return the vendor board URLs a careers page points at."""
    found: List[str] = []
    for match in BOARD_LINK_RE.finditer(page_html or ""):
        url = match.group(0).rstrip("/")
        if url not in found:
            found.append(url)
        if len(found) >= MAX_BOARD_LINKS:
            break
    return found


# A Workday tenant URL carries three things the listing endpoint needs:
#   https://<tenant>.wd<N>.myworkdayjobs.com/<Site Name>/...
_WORKDAY_RE = re.compile(
    r"https://(?P<tenant>[a-z0-9\-]+)\.(?:wd\d+\.)?myworkdayjobs\.com/"
    r"(?:wday/cxs/[^/]+/)?(?P<site>[^/?#]+)(?:/(?P<path>.*))?",
    re.IGNORECASE,
)

# An employer's own careers page often delegates to a vendor board rather than
# listing roles itself: measured, canadagoose.com/en-us/pages/careers is a Shopify
# page whose only job links are two absolute Workday URLs. Finding the board the
# page points at is what turns "this page lists nothing" into 96 roles.
BOARD_LINK_RE = re.compile(
    r"https://[a-z0-9\-]+\.(?:wd\d+\.)?myworkdayjobs\.com/[^\"'\s<>]+",
    re.IGNORECASE,
)

# How many board links to follow from one index page. A page links several times
# to the same board (header, footer, body), so duplicates are collapsed first.
MAX_BOARD_LINKS = 2

# Workday's listing endpoint answers a POST with a JSON body. One page of twenty
# is enough to characterise the boards a brief cares about; the whole list is not
# needed to say where a company is putting people.
WORKDAY_PAGE_SIZE = 20


# Workday sits locale prefixes in front of the site name:
#   /CanadaGooseCareers
#   /en-US/CanadaGooseCareers
# The listing endpoint wants the site name, so a leading locale segment is skipped.
_WORKDAY_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}/")


def _workday_cx_endpoint(url: str) -> Optional[str]:
    """Return Workday's JSON listing URL for a careers URL, or None.

    The tenant is the subdomain, the site is the first path segment after an
    optional locale ("CanadaGooseCareers"), and the tenant is repeated lowercased
    in the CX path — that triple is what the site's own front-end calls, and
    getting one of them wrong returns 404 or 502 rather than an empty list.
    """
    match = _WORKDAY_RE.search((url or "").strip())
    if not match:
        return None
    tenant = match.group("tenant").lower()
    site = match.group("site")
    path = match.group("path") or ""
    # "/en-US/CanadaGooseCareers" parses as site=en-US, path=CanadaGooseCareers.
    if _WORKDAY_LOCALE_RE.match(f"{site}/"):
        site = path.split("/")[0] if path else site
        if not site:
            return None
    return (
        f"https://{match.group('tenant')}.{_workday_host_suffix(url)}"
        f"/wday/cxs/{tenant}/{site}/jobs"
    )


def _workday_host_suffix(url: str) -> str:
    """Return the Workday host part after the tenant subdomain."""
    match = re.search(
        r"https?://[a-z0-9\-]+\.([a-z0-9.\-]+myworkdayjobs\.com)", url or "", re.I
    )
    return match.group(1).lower() if match else "myworkdayjobs.com"


def _postings_from_workday(endpoint: str) -> List[JobPosting]:
    """Read a Workday board's openings from its own JSON listing."""
    body = json.dumps(
        {"appliedFacets": {}, "limit": WORKDAY_PAGE_SIZE, "offset": 0}
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        logger.info("Workday listing unreadable | url=%s | %s", endpoint, exc)
        return []

    if not isinstance(payload, dict):
        return []

    rows = payload.get("jobPostings") or []
    postings: List[JobPosting] = []
    for row in rows[:MAX_POSTINGS_PER_PAGE]:
        if not isinstance(row, dict):
            continue
        title = " ".join(str(row.get("title") or "").split())
        if not title:
            continue
        postings.append(
            JobPosting(
                title=title,
                description="",
                location=" ".join(str(row.get("locationsText") or "").split()) or None,
                employment_type=None,
                posted_date=None,
            )
        )

    if postings:
        logger.info(
            "Workday board read | url=%s | total=%s | read=%s",
            endpoint,
            payload.get("total"),
            len(postings),
        )
    return postings
