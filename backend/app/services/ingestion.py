# /backend/app/services/ingestion.py
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple

import httpx
from htmldate import find_date

from pydantic import BaseModel, HttpUrl, ValidationError
from tenacity import retry, stop_after_attempt, wait_random_exponential
from trafilatura import extract
from tavily import TavilyClient
import markdown as md

from app.services.logger_service import get_logger
from app.services.search_planner import SearchPlan, plan_searches

logger = get_logger("ingestion")


# =============================================================================
# 1. Data contracts: input and output models
# =============================================================================

class BriefingRequest(BaseModel):
    """Input model for a pre-meeting briefing request."""

    company_name: str
    meeting_time: datetime
    meeting_purpose: str


class FactItem(BaseModel):
    """A single fact or opinion item.

    The content field must be an excerpt from the source text or a short
    sentence tightly bound to the source text.
    """

    content: str
    source_url: HttpUrl
    publish_date: Optional[datetime] = None


class FactBase(BaseModel):
    """Core output of the ingestion layer."""

    company_id: str
    last_refresh_date: datetime
    hard_facts: List[FactItem]
    analyst_opinions: List[FactItem]
    conflicts: List[dict] = []


# =============================================================================
# 2. Custom exception
# =============================================================================

class TavilySearchError(Exception):
    """Raised when Tavily search fails after retries."""


# =============================================================================
# 3. Constants
# =============================================================================

# Tavily rejects path patterns in include_domains for topic='news', and `site:`
# operators are ignored, so section scoping has to happen client-side. These
# paths are quote/profile shells that always come back with empty raw_content.
JUNK_URL_PATHS = (
    "/markets/companies/",
    "/quote/",
    "/quotes/",
    "/profile/company/",
    "/key-details",
    "/analytics",
    "/company/",
)

MIN_CLEAN_TEXT_LENGTH = 100

MIN_PARAGRAPH_LENGTH = 80

# Only the opening and closing paragraphs are treated as hard facts.
HARD_FACT_HEAD_PARAGRAPHS = 3

# Middle paragraphs are only inspected for these leverage signals, so that
# commentator framing never reaches the fact base.
NEGATIVE_SIGNAL_KEYWORDS = (
    "layoff",
    "layoffs",
    "job cut",
    "job cuts",
    "cut jobs",
    "reduction in force",
    "restructur",
    "cash flow",
    "cash-flow",
    "cash crunch",
    "liquidity",
    "loss",
    "losses",
    "net loss",
    "wider loss",
    "decline",
    "declined",
    "declines",
    "declining",
    "slump",
    "slumped",
    "plunge",
    "plunged",
    "fell",
    "falls",
    "drop",
    "dropped",
    "miss",
    "missed",
    "misses",
    "shortfall",
    "below expectations",
    "cost cut",
    "cost cuts",
    "cost-cutting",
    "margin pressure",
    "margin squeeze",
    "price cut",
    "price reduction",
    "discount",
    "resign",
    "resigned",
    "resignation",
    "departure",
    "departed",
    "step down",
    "stepped down",
    "downsize",
    "downturn",
    "warn",
    "warned",
    "warning",
    "probe",
    "lawsuit",
    "litigation",
    "investigation",
    "sanction",
    "recall",
    "delay",
    "delayed",
    "halts",
    "halted",
    "suspend",
    "suspended",
    "shutdown",
    "closure",
    "default",
    "insolven",
    "bankrupt",
    "distress",
    "downgrade",
    "downgraded",
    "weak",
    "weaker",
    "slowdown",
    "underperform",
    "pressure",
    "burden",
    "overcapacit",
)

# Cap the number of recovered negative sentences per source so one long
# article cannot flood the fact base.
NEGATIVE_SIGNAL_MAX_PER_SOURCE = 4

# Tavily raw_content is page markdown, so the article body arrives wrapped in
# navigation trees, share rows, related-story blocks and legal boilerplate.
# Every publisher below was inspected on a live page to record exactly where
# the body opens and which non-body modules trail it.
#
# body_end:  lowercased substrings that close the body; the first hit wins.
# body_line: regexes for chrome lines that must never enter the body.
# paywalled: the page never delivers prose, only a subscription offer.
SITE_BODY_RULES: dict[str, dict] = {
    # Reuters delivers a table article; only the trust-principles footer trails.
    "reuters.com": {
        "body_end": ("our standards:", "the thomson reuters trust principles"),
        "body_line": (),
    },
    # Bloomberg opens with a ~190-line corporate mega-menu full of
    # utm-tagged company links before the headline appears.
    "bloomberg.com": {
        "body_end": (),
        "body_line": (
            r"\[skip to content\]",
            r"utm_source=bloomber",
            r"^\[(sign in|subscribe)\]",
            r"^\[\]\(",
            r"^### (for customers|support|company|communications|bloomberg)",
        ),
    },
    "wsj.com": {
        "body_end": ("## videos", "### most popular", "#### recommended videos"),
        "body_line": (r"copyright \u00a9202", r"^!\["),
    },
    # FT returns the paywall, not the article: the headline is immediately
    # followed by "Subscribe to unlock this article".
    "ft.com": {
        "body_end": ("subscribe to unlock this article", "## try unlimited access"),
        "body_line": (),
        "paywalled": True,
    },
    "nikkei.com": {
        "body_end": ("## read next", "## latest on", "## most popular"),
        "body_line": (r"^!\[", r"^\[\!\["),
    },
    # CNBC arrives as one newline-free blob; the cut is a character offset.
    "cnbc.com": {
        "body_end": (
            "see here for a full list of the stocks",
            "as a subscriber to the cnbc investing club",
            "the above investing club information is subject",
            "cnbc investing club with jim cramer",
        ),
        "body_line": (),
    },
    "finance.yahoo.com": {
        "body_end": (
            "## recommended stories",
            "## more stories",
            "terms and privacy policy",
            "this article originally published on",
        ),
        "body_line": (r"^\[\]\(", r"^!\[\]", r"^\[\!\["),
    },
    "businessinsider.com": {
        "body_end": ("## read next", "read comments start the conversation"),
        "body_line": (r"^\[\]\(", r"^\[(subscribe|the long play|watch bi)"),
    },
    # A plain press release: it ends at the company contact block.
    "markets.businessinsider.com": {
        "body_end": ("\ncontact\nlisa", "executive vice president and chief financial officer"),
        "body_line": (r"^\[\]\(",),
    },
    "autonews.gasgoo.com": {
        "body_end": ("### related documents", "### related articles", "### related posts"),
        "body_line": (
            r"^\[\!\[",
            r"scan\s+for\s+mobile",
            r"^\[f share\]",
            r"^edited by",
        ),
    },
    "insideevs.com": {
        "body_end": ("share this story", "## comments", "## guides", "## latest"),
        "body_line": (
            r"^\*\s+\[(news|reviews|features|videos|podcasts|photos|newsletter|best evs)\]",
            r"^\[\!\[",
            r"^!\[\]",
            r"- the insideevs team",
        ),
    },
    "cnevpost.com": {
        "body_end": ("### about author", "### related posts"),
        "body_line": (
            r"^\|",
            r"add cn(e)?vpost as a preferred source",
            r"view more channels",
            r"^\[\+\s*add us on google",
        ),
    },
    "electrek.co": {
        "body_end": ("ftc: we use income earning", "## comments", "## guides"),
        "body_line": (
            r"advertisement - scroll",
            r"^\[\!\[",
            r"^!\[\]",
            r"^\[\]",
            r"^\[\*\*(add|subscribe)",
        ),
    },
}

BODY_HEADING_RE = re.compile(r"^#\s+\S")

# A page body is only usable when it carries at least this much prose, which
# screens out paywalls that return a headline plus an offer pitch.
MIN_BODY_CHARS = 400

# Tavily returns 10 results by default and accepts up to 20. Above 20 the
# response is unreliable (measured: 25 -> 16, 30 -> 1, 40 -> 10), so 20 is the
# hard ceiling and additional coverage comes from running more queries.
TAVILY_MAX_RESULTS = 20

# Coverage loop: keep searching while a round still adds usable sources and the
# round budget lasts. The round budget is deliberately generous: the loop exits
# early the moment a round stops contributing new usable sources, so a high
# ceiling costs nothing when coverage is already saturated.
MAX_SEARCH_ROUNDS = 20
MAX_EXTRA_QUERIES = 6

# A round is judged by URLs that carry an article body, not by raw hit count,
# because a round of quote shells adds nothing to the fact base.
MIN_USABLE_PER_ROUND = 1

# Tavily filters by publication date, so an article published inside the window
# can still report numbers from an older reporting period ("in the first five
# months of 2026", "the third quarter of 2025"). Those periods are the fact's
# subject, so they decay.
#
# Comparisons such as "lowest since April 2025" are observations made inside
# the window and are kept, so bare dates are not matched here: they are
# resolved against the window by _is_stale_period.
STALE_PERIOD_RE = re.compile(
    r"""
    \b(?:
        in\s+(?:the\s+)?(?:first|second|third|fourth)\s+
            (?:five|six|seven|eight|nine|ten|eleven)\s+months?
      | during\s+20\d\d
      | back\s+in\s+(?:late|early|mid)\s*20\d\d
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Reporting periods written as "<period> of <year>" or "<period> <year>".
# The year is captured so a period inside the window is not treated as stale.
STALE_QUARTER_RE = re.compile(
    r"\b(?:first|second|third|fourth|last|previous)\s+(?:quarter|half)\s+"
    r"(?:of\s+)?(20\d\d)\b",
    re.IGNORECASE,
)

# Explicit past calendar dates used as the reporting period, e.g.
# "September 2025 deliveries". The month is captured so dates inside the
# window are not mistaken for old periods.
STALE_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(20\d\d)\b"
)

MONTH_INDEX = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


# =============================================================================
# 4. Date window calculation
# =============================================================================

def _calculate_date_window(
    last_refresh_date: Optional[datetime] = None,
) -> Tuple[str, str]:
    """Calculate the Tavily search window in YYYY-MM-DD format.

    Rules:
    1. The total window is fixed at 30 days.
    2. If last_refresh_date exists and is less than 30 days ago:
       start_date = last_refresh_date, end_date = today.
    3. If last_refresh_date is missing or at least 30 days ago:
       start_date = today - 30 days, end_date = today.

    Persistence of last_refresh_date is handled by the FastAPI/service layer.
    The current BriefingRequest contract does not include last_refresh_date,
    so the main function treats it as missing and always looks back 30 days.
    """
    today = datetime.now(timezone.utc).date()

    if last_refresh_date is not None:
        if last_refresh_date.tzinfo is None:
            last_date = last_refresh_date.date()
        else:
            last_date = last_refresh_date.astimezone(timezone.utc).date()

        days_since_last_refresh = (today - last_date).days
        if 0 <= days_since_last_refresh < 30:
            return last_date.isoformat(), today.isoformat()

    start_date = today - timedelta(days=30)
    return start_date.isoformat(), today.isoformat()


# =============================================================================
# 5. Tavily search with exponential backoff retry
# =============================================================================

@retry(
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True,
)
def _tavily_search_with_retry(
    query: str,
    start_date: str,
    end_date: str,
    company_name: str,
    domains: List[str],
    max_results: int = TAVILY_MAX_RESULTS,
) -> List[dict]:
    """Call Tavily search with exponential backoff retry.

    Retries at most 3 times. On final failure, TavilySearchError is raised
    instead of silently returning an empty result.
    """
    try:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise RuntimeError("TAVILY_API_KEY environment variable is missing")

        logger.info(
            "Tavily search starting | query=%r | window=%s..%s | domains=%s | max=%s",
            query,
            start_date,
            end_date,
            domains,
            max_results,
        )
        client = TavilyClient(api_key=api_key)

        response = client.search(
            query=query,
            search_depth="advanced",
            include_raw_content=True,
            start_date=start_date,
            end_date=end_date,
            include_domains=domains,
            topic="news",
            max_results=max_results,
        )

        if not isinstance(response, dict):
            logger.warning("Tavily search returned non-dict response")
            return []

        results = response.get("results", [])
        if not isinstance(results, list):
            logger.warning("Tavily search results were not a list")
            return []

        logger.info("Tavily search returned %s raw results", len(results))

        kept = _filter_results_for_company(results, company_name)
        logger.info(
            "Section+company filter kept %s of %s results", len(kept), len(results)
        )
        return kept

    except Exception as exc:
        logger.exception(
            "Tavily search failed | query=%r | window=%s..%s",
            query,
            start_date,
            end_date,
        )
        raise TavilySearchError(
            f"Tavily search failed "
            f"(query={query!r}, start_date={start_date}, end_date={end_date}): {exc}"
        ) from exc


async def _search_query(
    query: str,
    domains: List[str],
    start_date: str,
    end_date: str,
    company_name: str,
) -> List[dict]:
    """Run one planned query against the planned publisher set."""
    return await asyncio.to_thread(
        _tavily_search_with_retry,
        query,
        start_date,
        end_date,
        company_name,
        domains,
        TAVILY_MAX_RESULTS,
    )


def _usable_in_batch(batch: List[dict]) -> int:
    """Count results that actually carry an article body."""
    return sum(1 for result in batch if (result.get("raw_content") or "").strip())


def _merge_batch(
    merged: List[dict],
    seen_urls: set[str],
    batch: List[dict],
) -> int:
    """Append new URLs from a batch and return how many were added."""
    added = 0
    for result in batch:
        url = str(result.get("url") or "").strip().rstrip("/")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        merged.append(result)
        added += 1
    return added


async def _search_plan(
    plan: "SearchPlan",
    start_date: str,
    end_date: str,
    company_name: str,
) -> List[dict]:
    """Run the planned queries, then keep searching until a round adds nothing.

    Coverage rule: a round counts only when it contributes previously unseen
    URLs that carry an article body. The loop stops when a round adds no usable
    source, when the round budget is spent, or when the extra queries run out.
    """
    merged: List[dict] = []
    seen_urls: set[str] = set()

    batches = await asyncio.gather(
        *[
            _search_query(query, plan.domains, start_date, end_date, company_name)
            for query in plan.queries
        ]
    )

    round_added = 0
    round_usable = 0
    for batch in batches:
        round_usable += _usable_in_batch(batch)
        round_added += _merge_batch(merged, seen_urls, batch)

    logger.info(
        "Search round 1 | queries=%s | per_query=%s | usable=%s | new_urls=%s | total=%s",
        len(plan.queries),
        [len(batch) for batch in batches],
        round_usable,
        round_added,
        len(merged),
    )

    merged = await _refill_missing_bodies(
        merged, plan, start_date, end_date, company_name
    )

    round_index = 1
    pending = list(plan.fallback_queries)

    while (
        round_index < MAX_SEARCH_ROUNDS
        and round_usable >= MIN_USABLE_PER_ROUND
        and pending
    ):
        round_index += 1
        take = pending[:MAX_EXTRA_QUERIES]
        pending = pending[MAX_EXTRA_QUERIES:]

        batches = await asyncio.gather(
            *[
                _search_query(query, plan.domains, start_date, end_date, company_name)
                for query in take
            ]
        )

        round_usable = 0
        round_added = 0
        for batch in batches:
            round_usable += _usable_in_batch(batch)
            round_added += _merge_batch(merged, seen_urls, batch)

        logger.info(
            "Search round %s | queries=%s | usable=%s | new_urls=%s | total=%s",
            round_index,
            take,
            round_usable,
            round_added,
            len(merged),
        )

        merged = await _refill_missing_bodies(
            merged, plan, start_date, end_date, company_name
        )

    logger.info(
        "Search plan executed | rounds=%s | merged=%s | fallback_exhausted=%s",
        round_index,
        len(merged),
        not pending,
    )
    return merged


async def _refill_missing_bodies(
    merged: List[dict],
    plan: "SearchPlan",
    start_date: str,
    end_date: str,
    company_name: str,
) -> List[dict]:
    """Re-search with the publishers whose bodies did arrive.

    Tavily returns some hits without article bodies often enough that key
    stories would otherwise be lost. Rather than guessing which outlet is
    reliable, the empty-body publishers observed in this run are dropped and
    the same queries are re-run, so a syndicated telling from a working outlet
    can take the slot the empty page was holding.
    """
    empty_hosts = {
        _host_of(str(result.get("url") or ""))
        for result in merged
        if not (result.get("raw_content") or "").strip()
    }

    with_body = _usable_in_batch(merged)
    if not empty_hosts:
        return merged

    retry_domains = [
        domain for domain in plan.domains if domain not in empty_hosts
    ]
    if not retry_domains:
        logger.info(
            "Body refill skipped | every planned publisher returned an empty body"
        )
        return merged

    logger.info(
        "Bodies missing | results=%s | with_body=%s | empty_publishers=%s | retry_with=%s",
        len(merged),
        with_body,
        sorted(empty_hosts),
        retry_domains,
    )

    already_seen = {
        str(result.get("url") or "").strip().rstrip("/") for result in merged
    }

    retry = await asyncio.gather(
        *[
            _search_query(query, retry_domains, start_date, end_date, company_name)
            for query in plan.queries
        ]
    )

    added = 0
    for batch in retry:
        for result in batch:
            url = str(result.get("url") or "").strip().rstrip("/")
            if not url or url in already_seen:
                continue
            already_seen.add(url)
            merged.append(result)
            added += 1

    logger.info("Body refill | new_results=%s | total=%s", added, len(merged))
    return merged


# =============================================================================
# 4b. Section-level URL filtering
# =============================================================================

def _url_path(url: str) -> str:
    """Return the path portion of a URL, lowercased, without query or fragment."""
    lowered = url.strip().lower()

    for separator in ("?", "#"):
        if separator in lowered:
            lowered = lowered.split(separator, 1)[0]

    if "//" in lowered:
        lowered = lowered.split("//", 1)[1]

    slash = lowered.find("/")
    return lowered[slash:] if slash != -1 else "/"


# Editorial section rules per publisher. A URL is kept only when its path
# contains one of the keep prefixes and none of the drop prefixes, so every
# source is scoped to a readable reporting section instead of only reuters.
#
# The keep prefixes below are the section URLs pinned per publisher:
#   bloomberg /markets /finance /industries /technology
#   wsj       /business /economy /tech /finance
#   cnbc      /markets /business /technology
#   ft        /companies /technology /markets
#   nikkei    /business /markets /economy
#   yahoo     /markets /topic/latest-news /daily-alpha
#   insider   /business /economy /retail /law /tech /startups /innovation
#   insider-markets /stocks
#
# An empty keep tuple means "any path is acceptable", which suits trade
# publishers whose article paths carry no section information.
SITE_SECTION_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "reuters.com": (
        ("/business/", "/markets/", "/legal/"),
        ("/world/", "/technology/"),
    ),
    "bloomberg.com": (
        (
            "/markets/",
            "/finance/",
            "/industries/",
            "/technology/",
            "/news/articles/",
        ),
        ("/quote/", "/profile/", "/opinion/", "/video/"),
    ),
    "wsj.com": (
        ("/business/", "/economy/", "/tech/", "/finance/", "/markets/"),
        ("/livecoverage/", "/podcasts/", "/video/", "/opinion/"),
    ),
    "cnbc.com": (
        ("/markets/", "/business/", "/technology/", "/2026/", "/2025/"),
        ("/quotes/", "/video/", "/investingclub/", "/pro/"),
    ),
    "ft.com": (
        ("/companies/", "/technology/", "/markets/", "/content/"),
        ("/data/announce/", "/search/", "/opinion/"),
    ),
    "nikkei.com": (
        ("/business/", "/markets/", "/economy/", "/spotlight/"),
        ("/opinion/", "/politics/", "/video/"),
    ),
    "finance.yahoo.com": (
        ("/markets/", "/topic/latest-news/", "/daily-alpha/", "/news/"),
        ("/quote/", "/video/", "/finance/"),
    ),
    "businessinsider.com": (
        (
            "/business",
            "/economy",
            "/retail",
            "/law",
            "/tech",
            "/startups",
            "/innovation",
            "/news/",
        ),
        ("/recent-company-layoffs", "/lists/", "/video/", "/podcasts/"),
    ),
    "markets.businessinsider.com": (
        ("/stocks", "/news/stocks/"),
        ("/quote/", "/video/"),
    ),
    "autonews.gasgoo.com": (
        ("/articles/news/", "/articles/market-industry/", "/articles/ev/"),
        ("/articles/rankings/", "/articles/video/", "/articles/other/"),
    ),
    "insideevs.com": (
        ("/news/",),
        ("/reviews/", "/features/", "/video/"),
    ),
    "cnevpost.com": ((), ()),
    "electrek.co": ((), ()),
}


def _site_rule(url: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the (keep, drop) section prefixes that apply to this URL.

    The longest matching domain wins, so markets.businessinsider.com keeps its
    own rule instead of inheriting businessinsider.com's.
    """
    host = url.strip().lower()
    for separator in ("?", "#"):
        if separator in host:
            host = host.split(separator, 1)[0]
    if "//" in host:
        host = host.split("//", 1)[1]
    host = host.split("/", 1)[0]

    best_domain = ""
    best_rule: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())

    for domain, rule in SITE_SECTION_RULES.items():
        if not host.endswith(domain):
            continue
        if len(domain) > len(best_domain):
            best_domain = domain
            best_rule = rule

    return best_rule


def _matches_section(path: str, prefix: str) -> bool:
    """Match a section prefix against a URL path.

    Section prefixes are written with a trailing slash so that /business/ does
    not match /businesswire-x. Section landing pages themselves come without
    the slash (/markets, /business), so the trimmed form is accepted too as
    long as it is a whole path segment.
    """
    if path.startswith(prefix):
        return True

    bare = prefix.rstrip("/")
    if not bare:
        return False

    if path == bare:
        return True

    return path.startswith(bare + "/")


def _is_kept_result(url: str) -> bool:
    """Decide whether a result URL points at readable editorial reporting.

    Quote, profile and company shells carry no article body, so Tavily returns
    them with empty raw_content. Each publisher then gets its own section
    rule: opinion columns, live blogs, stock-quote tables and video pages are
    dropped so only reporting reaches the fact base.
    """
    path = _url_path(url)

    for junk in JUNK_URL_PATHS:
        if junk in path:
            return False

    keep, drop = _site_rule(url)

    for dropped in drop:
        if _matches_section(path, dropped):
            return False

    if not keep:
        return True

    return any(_matches_section(path, section) for section in keep)


def _mentions_company(result: dict, company_name: str) -> bool:
    """Check whether a result is actually about the requested company.

    Tavily matches query terms loosely, so unrelated articles land in the
    result set. Requiring the company name in the title, URL, or body keeps
    other companies' news from being read as this company's facts.

    Latin names are matched on word boundaries: NIO must not match "senior"
    or "opinion". CJK names have no word boundaries, so those use a plain
    substring check.
    """
    name = company_name.strip().lower()
    if not name:
        return True

    haystack = " ".join(
        str(result.get(key) or "") for key in ("title", "url", "raw_content")
    ).lower()

    if name.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", haystack) is not None

    return name in haystack


def _filter_results_for_company(
    results: List[dict], company_name: str
) -> List[dict]:
    """Keep only results that mention the requested company."""
    kept: List[dict] = []
    for result in results:
        if not isinstance(result, dict):
            continue

        url = result.get("url")
        if not isinstance(url, str) or not url.strip():
            continue

        if not _is_kept_result(url):
            continue

        if not _mentions_company(result, company_name):
            continue

        kept.append(result)

    return kept


# =============================================================================
# 6b. Article body location inside page markdown
# =============================================================================

def _host_of(url: str) -> str:
    """Return the lowercased host of a URL, without www, query or fragment."""
    host = url.strip().lower()
    for separator in ("?", "#"):
        if separator in host:
            host = host.split(separator, 1)[0]
    if "//" in host:
        host = host.split("//", 1)[1]
    host = host.split("/", 1)[0]
    if host.startswith("www."):
        host = host[len("www."):]
    return host


def _site_body_rule(url: str) -> dict:
    """Return the body-scoping rule for this publisher.

    The longest matching domain wins, so markets.businessinsider.com keeps its
    own rule instead of inheriting businessinsider.com's.
    """
    host = _host_of(url)

    best_domain = ""
    best_rule: dict = {}
    for domain, rule in SITE_BODY_RULES.items():
        if not host.endswith(domain):
            continue
        if len(domain) > len(best_domain):
            best_domain = domain
            best_rule = rule

    return best_rule


def _locate_article_body(markdown: str, url: str) -> str:
    """Return only the article body from Tavily page markdown.

    The opening is the first top-level heading; pages without one (press
    releases, CNBC blobs) fall back to the first prose-length line. The closing
    is the first trailing marker from the publisher's own rule set. Chrome
    lines declared by that publisher are dropped from the remaining span.

    Cut points are resolved on the raw text rather than per line, because some
    publishers deliver the whole page as a single newline-free blob.
    """
    rule = _site_body_rule(url)
    lines = markdown.splitlines()

    start = 0
    for index, line in enumerate(lines):
        if BODY_HEADING_RE.match(line.strip()):
            start = index
            break
    else:
        for index, line in enumerate(lines):
            stripped = line.strip()
            if (
                len(stripped) >= 80
                and not stripped.startswith(("[", "*", "!", "#", "|"))
            ):
                start = index
                break

    offset = sum(len(line) + 1 for line in lines[:start])
    tail = markdown[offset:]

    cut = len(tail)
    for marker in rule.get("body_end", ()):
        found = tail.lower().find(marker.lower())
        if found != -1:
            cut = min(cut, found)

    body = tail[:cut]

    line_patterns = [
        re.compile(pattern, re.IGNORECASE)
        for pattern in rule.get("body_line", ())
    ]
    kept = [
        line
        for line in body.splitlines()
        if not any(pattern.match(line.strip()) for pattern in line_patterns)
    ]

    return "\n".join(kept).strip()


# =============================================================================
# 6. Trafilatura text extraction
# =============================================================================

def _extract_clean_text(raw_content: Optional[str], url: str) -> Optional[str]:
    """Extract the article body from Tavily raw_content.

    Tavily include_raw_content=True returns page markdown, not raw HTML, so the
    body is located first with the publisher's own rule: navigation trees,
    share rows, related-story blocks, contact blocks and paywall offers never
    reach the fact base. Trafilatura then converts the remaining markdown to
    HTML and strips any residual chrome.
    """
    if not raw_content or not isinstance(raw_content, str):
        return None

    content = _locate_article_body(raw_content, url)
    if not content:
        return None

    # A paywall page returns a headline plus an offer pitch. Reject anything
    # too short to be real prose so subscription boilerplate never becomes a
    # "fact".
    if len(content) < MIN_BODY_CHARS:
        return None

    try:
        html = md.markdown(content)
    except Exception:
        return None

    try:
        clean_text = extract(html, output_format="markdown")
    except Exception:
        return None

    if not clean_text:
        return None

    clean_text = clean_text.strip()
    if len(clean_text) < MIN_CLEAN_TEXT_LENGTH:
        return None

    return clean_text


# =============================================================================
# 7. Inverted-pyramid structural split
# =============================================================================

def _first_sentence(paragraph: str) -> str:
    """Return the first sentence of a paragraph without semantic rewriting."""
    paragraph = paragraph.strip()
    if not paragraph:
        return ""

    parts = re.split(r"(?<=[.!?])\s+", paragraph, maxsplit=1)
    first = parts[0].strip() if parts else paragraph

    return first or paragraph


def _has_negative_signal(text: str) -> bool:
    """Detect negotiation-relevant negative signals in a sentence."""
    lowered = text.lower()
    return any(keyword in lowered for keyword in NEGATIVE_SIGNAL_KEYWORDS)


def _extract_hard_facts(clean_text: str) -> List[str]:
    """Extract hard facts from clean text, ignoring commentator opinion.

    Section scoping per the product rule:
    - The opening 3 paragraphs and the closing paragraph are always read.
    - Middle paragraphs are never read as prose; only their first sentence is
      inspected, and just to recover negative signals the trader needs.
    - Middle sentences without a negative signal are dropped, so commentator
      framing cannot leak into the fact base.

    Paragraphs shorter than MIN_PARAGRAPH_LENGTH are dropped before splitting,
    because Tavily markdown contains headings, list items and quote blocks
    that are structurally not paragraphs.

    This is a pure structural split. No semantic judgment, summarization,
    rewriting, or conflict detection is performed here.
    """
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", clean_text)
        if paragraph.strip() and len(paragraph.strip()) >= MIN_PARAGRAPH_LENGTH
    ]

    if not paragraphs:
        return []

    if len(paragraphs) <= HARD_FACT_HEAD_PARAGRAPHS:
        return paragraphs

    lead = paragraphs[:HARD_FACT_HEAD_PARAGRAPHS]
    middle = paragraphs[HARD_FACT_HEAD_PARAGRAPHS:-1]
    tail = paragraphs[-1:]

    hard_texts = [text for text in lead + tail if text]

    negative_texts: List[str] = []
    for paragraph in middle:
        first_sentence = _first_sentence(paragraph)
        if not first_sentence or not _has_negative_signal(first_sentence):
            continue

        negative_texts.append(first_sentence)
        if len(negative_texts) >= NEGATIVE_SIGNAL_MAX_PER_SOURCE:
            break

    return hard_texts + negative_texts


# =============================================================================
# 8b. Publication date fallback
# =============================================================================

DATE_FETCH_TIMEOUT_SECONDS = 10.0
DATE_FETCH_MAX_CONCURRENCY = 5

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Publishers encode the publication date in the URL slug, which survives anti
# bot walls that block the page itself (measured: reuters returns 401,
# bloomberg 403 to a direct fetch).
#   reuters:   /business/finance/...-2026-10-15
#   bloomberg: /news/articles/2026-10-28/slug
#   globenewswire: /news-release/2026/10/30/...
URL_DATE_PATTERNS = (
    re.compile(r"(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?:/|$|-)"),
    re.compile(r"/(20\d{2})/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/"),
)


def _date_from_url_slug(url: str) -> Optional[datetime]:
    """Recover a publication date from the URL path.

    Cheap, offline, and works on publishers that block page fetches.
    """
    path = _url_path(url)

    for pattern in URL_DATE_PATTERNS:
        match = pattern.search(path)
        if not match:
            continue
        year, month, day = (int(part) for part in match.groups()[:3])
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def _date_from_url(url: str) -> Optional[datetime]:
    """Recover a publication date for a page.

    Tier 1 reads the date out of the URL slug, which always works but only for
    publishers that put it there. Tier 2 fetches the page and lets htmldate
    parse the markup, which covers publishers whose URLs carry no date. Any
    failure returns None: an unreachable page must not fail ingestion.
    """
    slug_date = _date_from_url_slug(url)
    if slug_date is not None:
        return slug_date

    try:
        with httpx.Client(
            timeout=DATE_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = client.get(url)
            if response.status_code >= 400:
                logger.info(
                    "Date fetch blocked | url=%s | status=%s",
                    url,
                    response.status_code,
                )
                return None
            html = response.text
    except Exception as exc:
        logger.info("Date fallback fetch failed | url=%s | %s", url, exc)
        return None

    if not html:
        return None

    try:
        found = find_date(html, original_date=True, extensive_search=True)
    except Exception as exc:
        logger.info("Date fallback extraction failed | url=%s | %s", url, exc)
        return None

    if not found:
        return None

    try:
        return datetime.fromisoformat(str(found)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def _resolve_publish_dates(results: List[dict]) -> List[dict]:
    """Fill in published_date for results Tavily returned without one."""
    missing = [
        result
        for result in results
        if not isinstance(result.get("published_date"), (str, datetime))
        or not str(result.get("published_date") or "").strip()
    ]

    if not missing:
        return results

    logger.info("Date fallback needed | results=%s | missing=%s", len(results), len(missing))

    semaphore = asyncio.Semaphore(DATE_FETCH_MAX_CONCURRENCY)

    async def fill(result: dict) -> None:
        url = str(result.get("url") or "")
        if not url:
            return
        async with semaphore:
            recovered = await asyncio.to_thread(_date_from_url, url)
        if recovered is not None:
            result["published_date"] = recovered.isoformat()
            logger.info("Date recovered | url=%s | %s", url, recovered.date())

    await asyncio.gather(*[fill(result) for result in missing])

    still_missing = sum(
        1
        for result in missing
        if _parse_publish_date(result.get("published_date")) is None
    )
    logger.info(
        "Date fallback complete | recovered=%s | still_missing=%s",
        len(missing) - still_missing,
        still_missing,
    )
    return results

def _parse_publish_date(value: object) -> Optional[datetime]:
    """Parse Tavily published_date.

    Tavily returns RFC 2822 timestamps ("Wed, 21 Oct 2026 00:00:00 GMT"), not
    ISO 8601, so fromisoformat alone returns None for every real reply. ISO
    strings are still handled for other sources. A bad date does not discard
    the whole result.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass

    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None

    if parsed is None:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed


def _build_fact_item(
    content: str,
    source_url: object,
    publish_date: Optional[datetime],
) -> Optional[FactItem]:
    """Build a FactItem and validate source_url via HttpUrl.

    Invalid URLs or validation failures cause the item to be dropped.
    """
    if not content or not source_url:
        return None

    try:
        return FactItem(
            content=content,
            source_url=source_url,
            publish_date=publish_date,
        )
    except ValidationError:
        return None


# Source credibility ranking, most authoritative first. Results from an
# earlier tier win when the same sentence is reported by several outlets.
# Only general business publishers are ranked here; trade publishers are a
# separate lane and never compete with wire services for the same fact.
SOURCE_PRIORITY = (
    "reuters.com",
    "bloomberg.com",
    "wsj.com",
    "ft.com",
    "nikkei.com",
    "cnbc.com",
    "businessinsider.com",
    "finance.yahoo.com",
    "sec.gov",
    "prnewswire.com",
    "globenewswire.com",
    "businesswire.com",
    "autonews.gasgoo.com",
    "insideevs.com",
    "electrek.co",
    "cnevpost.com",
)

DEDUP_TEXT_LENGTH = 60

# Sentences that only differ in trailing detail ("34,246 vehicles" vs
# "34,246 vehicles, comprising 18,125 NIO brand...") share a prefix. Dedup
# is keyed on that prefix so the fuller telling replaces the shorter one.
DEDUP_PREFIX_LENGTH = 40


def _source_tier(url: str) -> int:
    """Rank a source by credibility tier; unknown hosts rank last."""
    host = _host_of(url)

    for index, domain in enumerate(SOURCE_PRIORITY):
        if host.endswith(domain):
            return index
    return len(SOURCE_PRIORITY)


def _dedup_key(text: str) -> str:
    """Normalize a sentence for duplicate detection.

    Keyed on the leading words rather than the whole sentence: the same figure
    reported with extra breakdown shares its opening, so the longer telling can
    replace the shorter one instead of both being kept.
    """
    normalized = re.sub(r"[^\w\s]", "", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()

    if len(normalized) <= DEDUP_PREFIX_LENGTH:
        return normalized

    return normalized[:DEDUP_PREFIX_LENGTH]


def _publisher_of(url: str) -> str:
    """Return the publishing brand for grouping.

    markets.businessinsider.com and businessinsider.com are one brand, so
    their articles are read together and de-duplicated against each other
    before the next publisher in the priority list is opened.
    """
    host = _host_of(url)
    for domain in SOURCE_PRIORITY:
        if host.endswith(domain):
            return domain
    return host


def _order_by_publisher(results: List[dict]) -> List[dict]:
    """Order results by publisher credibility, keeping one publisher's sites together.

    Within a publisher, every allowed site is read in turn and their duplicate
    sentences are collapsed, so the next publisher is only opened once this
    publisher has supplied what it can.
    """
    usable = [r for r in results if isinstance(r, dict)]
    usable.sort(
        key=lambda r: (
            _source_tier(str(r.get("url") or "")),
            _publisher_of(str(r.get("url") or "")),
        )
    )
    return usable


def _is_stale_period(text: str, window_start: str) -> bool:
    """Decide whether a sentence reports a period before the search window.

    Tavily already filters by publication date, so this only catches the
    content-level mismatch: a fresh article whose fact describes an older
    reporting period. Comparisons made inside the window ("lowest since April
    2025") are statements about now, so they are kept.
    """
    if STALE_PERIOD_RE.search(text):
        return True

    window_year = int(window_start[:4])
    window_month = int(window_start[5:7])

    for year_text in STALE_QUARTER_RE.findall(text):
        if int(year_text) < window_year:
            return True

    for month_text, year_text in STALE_DATE_RE.findall(text):
        month = MONTH_INDEX[month_text.lower()]
        year = int(year_text)

        if year < window_year:
            # "lowest since April 2025" and "below its January 2021 record"
            # describe where things stand now, not a stale reporting period.
            if _is_comparison_anchor(text, month_text, year_text):
                continue
            return True

        if year == window_year and month < window_month:
            return True

    return False


def _is_comparison_anchor(text: str, month_text: str, year_text: str) -> bool:
    """Check whether an old date is used as a comparison anchor, not a period."""
    lowered = text.lower()
    anchor = f"{month_text.lower()} {year_text}"

    position = lowered.find(anchor)
    if position == -1:
        return False

    window = lowered[max(0, position - 40):position]
    return any(
        marker in window
        for marker in ("since", "below", "above", "than", "compared", "record", "prior to")
    )


def _pick_richer(existing: FactItem, candidate_text: str, source_url, publish_date) -> bool:
    """Decide whether a duplicate fact should replace the one already stored.

    Duplicate sentences are kept once, preferring the more detailed telling:
    the longer text wins, so a bare number from one outlet does not shadow the
    same figure with its breakdown from another.
    """
    return len(candidate_text) > len(existing.content)


def _build_company_id(company_name: str) -> str:
    """Build a stable company_id with a readable slug and a hash suffix."""
    normalized = company_name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]

    if slug:
        return f"{slug}-{digest}"

    return f"company-{digest}"


# =============================================================================
# 9. Main entry: ingest, clean, structure, validate
# =============================================================================

async def ingest_company_data(request: BriefingRequest) -> FactBase:
    """Ingest company data and return a validated FactBase.

    Steps:
    1. Calculate start_date and end_date.
    2. Build the Tavily query and call the retry-enabled search function.
    3. Extract clean text from each raw_content with Trafilatura.
    4. Split clean text into hard facts, dropping commentator opinion.
    5. Validate and return the result with FactBase.model_validate().

    analyst_opinions stays empty on purpose: commentator framing must not
    reach the LLM, so the report is generated from hard facts alone.
    """
    logger.info(
        "Ingestion started | company=%r | purpose=%r | meeting_time=%s",
        request.company_name,
        request.meeting_purpose,
        request.meeting_time.isoformat(),
    )

    last_refresh_date: Optional[datetime] = None
    start_date, end_date = _calculate_date_window(last_refresh_date)
    logger.info("Ingestion date window | start=%s | end=%s", start_date, end_date)

    company_label = request.company_name.strip()
    purpose_label = request.meeting_purpose.strip()

    plan = await plan_searches(company_label, purpose_label)
    logger.info(
        "Search plan | industry=%s | categories=%s | fundamentals=%s",
        plan.industry,
        plan.categories,
        plan.fundamentals,
    )

    results = await _search_plan(plan, start_date, end_date, company_label)

    results = await _resolve_publish_dates(results)
    results = _order_by_publisher(results)

    hard_facts: List[FactItem] = []
    analyst_opinions: List[FactItem] = []
    fact_index: dict[str, int] = {}
    extracted_sources = 0
    skipped_sources = 0
    duplicate_sources = 0
    stale_sources = 0
    stale_facts = 0
    seen_publishers: set[str] = set()

    for result in results:
        if not isinstance(result, dict):
            skipped_sources += 1
            continue

        raw_content = result.get("raw_content")
        source_url = str(result.get("url") or "")
        publish_date = _parse_publish_date(result.get("published_date"))

        publisher = _publisher_of(source_url)
        if publisher not in seen_publishers:
            seen_publishers.add(publisher)
            logger.info(
                "Reading publisher #%s | %s | accumulated hard_facts=%s",
                len(seen_publishers),
                publisher,
                len(hard_facts),
            )

        clean_text = _extract_clean_text(raw_content, source_url)
        if not clean_text:
            skipped_sources += 1
            logger.info("Skipped source (no clean text) | url=%s", source_url)
            continue

        extracted_sources += 1
        hard_texts = _extract_hard_facts(clean_text)

        fresh_texts: List[str] = []
        for text in hard_texts:
            if _is_stale_period(text, start_date):
                stale_facts += 1
                continue
            fresh_texts.append(text)

        if hard_texts and not fresh_texts:
            stale_sources += 1
            logger.info("Skipped source (only stale periods) | url=%s", source_url)
            continue

        logger.info(
            "Extracted source | url=%s | hard_paragraphs=%s | stale_dropped=%s",
            source_url,
            len(fresh_texts),
            len(hard_texts) - len(fresh_texts),
        )

        added = 0
        replaced = 0
        for text in fresh_texts:
            key = _dedup_key(text)
            fact_item = _build_fact_item(text, source_url, publish_date)
            if fact_item is None:
                continue

            existing_index = fact_index.get(key)
            if existing_index is None:
                fact_index[key] = len(hard_facts)
                hard_facts.append(fact_item)
                added += 1
                continue

            # A duplicate is kept once, but the more detailed telling wins.
            if _pick_richer(hard_facts[existing_index], text, source_url, publish_date):
                hard_facts[existing_index] = fact_item
                replaced += 1

        if added == 0 and replaced == 0:
            duplicate_sources += 1
            logger.info("Skipped source (duplicate content) | url=%s", source_url)
        elif replaced:
            logger.info(
                "Richer duplicate replaced earlier fact | url=%s | replaced=%s",
                source_url,
                replaced,
            )

    payload = {
        "company_id": _build_company_id(request.company_name),
        "last_refresh_date": datetime.now(timezone.utc),
        "hard_facts": hard_facts,
        "analyst_opinions": analyst_opinions,
        "conflicts": [],
    }

    fact_base = FactBase.model_validate(payload)

    from .conflict_detection import detect_fact_conflicts

    fact_base.conflicts = detect_fact_conflicts(fact_base)

    logger.info(
        "Ingestion complete | company_id=%s | sources_used=%s | sources_skipped=%s | "
        "sources_duplicate=%s | sources_stale=%s | stale_facts_dropped=%s | "
        "hard_facts=%s | analyst_opinions=%s | conflicts=%s",
        fact_base.company_id,
        extracted_sources,
        skipped_sources,
        duplicate_sources,
        stale_sources,
        stale_facts,
        len(fact_base.hard_facts),
        len(fact_base.analyst_opinions),
        len(fact_base.conflicts),
    )
    return fact_base
