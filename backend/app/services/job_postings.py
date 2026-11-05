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


def postings_from_html(page_html: str) -> List[JobPosting]:
    """Extract every JobPosting described by a page's structured data."""
    found: List[JobPosting] = []
    seen_titles: set[str] = set()

    for document in _iter_ld_blocks(page_html):
        for node in _walk(document):
            if str(node.get("@type") or "").lower() != "jobposting":
                continue

            title = _strip_html(str(node.get("title") or node.get("name") or ""))
            if not title or title.lower() in seen_titles:
                continue

            description = _strip_html(str(node.get("description") or ""))
            if len(description) < MIN_DESCRIPTION_CHARS:
                description = ""
            else:
                description = description[:MAX_DESCRIPTION_CHARS].rstrip()

            seen_titles.add(title.lower())
            found.append(
                JobPosting(
                    title=title,
                    description=description,
                    location=_location_text(node),
                    employment_type=_clean_employment_type(node.get("employmentType")),
                    posted_date=str(node.get("datePosted") or "").strip() or None,
                )
            )

            if len(found) >= MAX_POSTINGS_PER_PAGE:
                return found

    return found


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


def postings_from_url(url: str) -> List[JobPosting]:
    """Fetch a careers page and return the openings it publishes.

    An index page on a JavaScript board carries no JobPosting block, but it does
    link to detail pages that do. When the index yields nothing, a few detail
    pages are opened instead, which is how the company's openings were recovered
    on careers.amd.com.
    """
    page_html = fetch_page(url)
    if not page_html:
        return []

    postings = postings_from_html(page_html)
    if postings:
        logger.info("Careers page read | url=%s | postings=%s", url, len(postings))
        return postings

    details = _detail_links(page_html, url, MAX_DETAIL_FETCHES)
    recovered: List[JobPosting] = []
    seen_titles: set[str] = set()
    for detail_url in details:
        detail_html = fetch_page(detail_url)
        if not detail_html:
            continue
        for posting in postings_from_html(detail_html):
            if posting.title.lower() in seen_titles:
                continue
            seen_titles.add(posting.title.lower())
            recovered.append(posting)

    logger.info(
        "Careers page read | url=%s | postings=%s | detail_pages=%s | recovered=%s",
        url,
        len(postings),
        len(details),
        len(recovered),
    )
    return recovered
