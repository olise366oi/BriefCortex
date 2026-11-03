# /backend/app/services/freshness.py
"""Publication-date parsing, fallback recovery, and reporting-period checks."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from htmldate import find_date

from app.services.logger_service import get_logger

logger = get_logger("freshness")

DATE_FETCH_TIMEOUT_SECONDS = 10.0
DATE_FETCH_MAX_CONCURRENCY = 5

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Publishers encode the publication date in the URL slug, which survives anti
# bot walls that block the page itself (measured: reuters returns 401,
# bloomberg 403 to a direct fetch).
URL_DATE_PATTERNS = (
    re.compile(r"(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?:/|$|-)"),
    re.compile(r"/(20\d{2})/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/"),
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

STALE_QUARTER_RE = re.compile(
    r"\b(?:first|second|third|fourth|last|previous)\s+(?:quarter|half)\s+"
    r"(?:of\s+)?(20\d\d)\b",
    re.IGNORECASE,
)

STALE_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(20\d\d)\b"
)

COMPARISON_MARKERS = (
    "since",
    "below",
    "above",
    "than",
    "compared",
    "record",
    "prior to",
)


def _url_path(url: str) -> str:
    lowered = url.strip().lower()
    for separator in ("?", "#"):
        if separator in lowered:
            lowered = lowered.split(separator, 1)[0]
    if "//" in lowered:
        lowered = lowered.split("//", 1)[1]
    slash = lowered.find("/")
    return lowered[slash:] if slash != -1 else "/"


def parse_publish_date(value: object) -> Optional[datetime]:
    """Parse a Tavily published_date.

    Tavily returns RFC 2822 ("Wed, 21 Oct 2026 00:00:00 GMT"), not ISO 8601,
    so fromisoformat alone matches nothing. ISO strings are still accepted for
    other sources. A malformed value yields None; it never drops the whole
    result.
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

    from email.utils import parsedate_to_datetime

    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None

    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _date_from_url_slug(url: str) -> Optional[datetime]:
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


def _date_from_page(url: str) -> Optional[datetime]:
    try:
        with httpx.Client(
            timeout=DATE_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = client.get(url)
            if response.status_code >= 400:
                logger.info(
                    "Date fetch blocked | url=%s | status=%s", url, response.status_code
                )
                return None
            html = response.text
    except Exception as exc:
        logger.info("Date fetch failed | url=%s | %s", url, exc)
        return None

    if not html:
        return None

    try:
        found = find_date(html, original_date=True, extensive_search=True)
    except Exception as exc:
        logger.info("Date extraction failed | url=%s | %s", url, exc)
        return None

    if not found:
        return None

    try:
        return datetime.fromisoformat(str(found)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def date_from_url(url: str) -> Optional[datetime]:
    """Recover a publication date, slug first and page metadata second."""
    slug_date = _date_from_url_slug(url)
    if slug_date is not None:
        return slug_date
    return _date_from_page(url)


def is_stale_period(text: str, window_start: str) -> bool:
    """Return True when a sentence reports a period before the search window.

    Tavily already filters by publication date, so this catches the remaining
    mismatch: a fresh article whose fact describes an older reporting period.
    Comparisons such as "lowest since April 2025" describe where things stand
    now, so they are kept.
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
            if _is_comparison_anchor(text, month_text, year_text):
                continue
            return True

        if year == window_year and month < window_month:
            return True

    return False


def _is_comparison_anchor(text: str, month_text: str, year_text: str) -> bool:
    lowered = text.lower()
    anchor = f"{month_text.lower()} {year_text}"

    position = lowered.find(anchor)
    if position == -1:
        return False

    window = lowered[max(0, position - 40):position]
    return any(marker in window for marker in COMPARISON_MARKERS)


async def resolve_publish_dates(results: List[dict]) -> List[dict]:
    """Fill in published_date for results Tavily returned without one."""
    import asyncio

    missing = [
        result
        for result in results
        if not str(result.get("published_date") or "").strip()
    ]

    if not missing:
        return results

    logger.info(
        "Date fallback needed | results=%s | missing=%s", len(results), len(missing)
    )

    semaphore = asyncio.Semaphore(DATE_FETCH_MAX_CONCURRENCY)

    async def fill(result: dict) -> None:
        url = str(result.get("url") or "")
        if not url:
            return
        async with semaphore:
            recovered = await asyncio.to_thread(date_from_url, url)
        if recovered is not None:
            result["published_date"] = recovered.isoformat()
            logger.info("Date recovered | url=%s | %s", url, recovered.date())

    await asyncio.gather(*[fill(result) for result in missing])

    still_missing = sum(
        1 for result in missing if parse_publish_date(result.get("published_date")) is None
    )
    logger.info(
        "Date fallback complete | recovered=%s | still_missing=%s",
        len(missing) - still_missing,
        still_missing,
    )
    return results
