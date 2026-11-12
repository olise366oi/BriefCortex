# /backend/app/services/freshness.py
"""Publication-date parsing, fallback recovery, and reporting-period checks."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from htmldate import find_date

from app.services.logger_service import get_logger
from app.services.urls import url_path

logger = get_logger("freshness")

DATE_FETCH_TIMEOUT_SECONDS = 6.0
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

# Publishers write dates in the order the writer's own language uses: "26 Aug
# 2013" and "Aug. 2013" both appeared in one measured YKK run. The full-month
# pattern above missed both, so a docket that filed in 2013 and closed in 2017
# was filed as current evidence in a 30-day window.
ABBREVIATED_MONTH_INDEX = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11,
    "dec": 12,
}

STALE_SHORT_DATE_RE = re.compile(
    r"\b(?:(\d{1,2})(?:st|nd|rd|th)?\s+,?\s+)?"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+(20\d\d)\b",
    re.IGNORECASE,
)

# Numeric dates, which no month-name pattern reaches: "(Signed by Judge Gregory
# H. Woods on 7/30/2020)" and "2020-07-30". A measured YKK run kept a 2020
# judgment in a 30-day window because its only date was written this way.
#
# Two-digit years are included because court PDFs stamp them: a 2014 judgment
# carried "Filed 01/24/14" and nothing else, so the whole filing read as current.
# They are read as 20xx, which is the only range a document stamped this way can
# mean, and only in the slash form — an ISO year is never abbreviated.
STALE_NUMERIC_DATE_RE = re.compile(
    r"\b(?:(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d))"
    r"|(?:(?<!\d)(\d{1,2})/(\d{1,2})/(\d{2})(?!\d))"
    r"|\b(20\d\d)-(\d{1,2})-(\d{1,2})\b"
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
    path = url_path(url)
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


# The document's own filing stamp. A court PDF carries "Filed 01/24/14" or
# "Document 136 Filed 01/24/2014" in its header and nowhere else — no slug date,
# no page metadata — so a 2014 judgment arrived undated, survived the window
# filter, and was reported as current evidence about a live dispute.
_DOCUMENT_STAMP_RE = re.compile(
    r"(?:Filed|Dated)\s*:?\s*(?:[A-Z][a-z]+,?\s+)?"
    r"(?:(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?!\d)"
    r"|(20\d\d)-(\d{1,2})-(\d{1,2})\b)"
    r"|\(Signed\s+by[^()\n]{0,80}?\s+on\s+(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?!\d)\)",
    re.IGNORECASE,
)


def date_from_body(text: str) -> Optional[datetime]:
    """Recover a publication date from the document's own text.

    Used only when neither the search result, the URL slug nor the page metadata
    yielded one. A filing states its date in the header, and a date the document
    states about itself is better evidence than leaving it undated — an undated
    result is kept by the window filter, which is how a 2014 judgment reached a
    brief on a 2026 meeting.
    """
    if not text:
        return None

    match = _DOCUMENT_STAMP_RE.search(text)
    if not match:
        return None

    groups = match.groups()
    numeric = groups[:6]
    signed = groups[6:]

    if signed and signed[0]:
        month_text, day_text, year_text = signed
    elif numeric[0]:
        month_text, day_text, year_text = numeric[0], numeric[1], numeric[2]
    elif numeric[3]:
        year, month, day = int(numeric[3]), int(numeric[4]), int(numeric[5])
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None
    else:
        return None

    year = int(year_text)
    if year < 100:
        year += 2000

    try:
        return datetime(year, int(month_text), int(day_text), tzinfo=timezone.utc)
    except ValueError:
        return None


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

    # Every date the sentence states, in whichever form the writer used. The two
    # patterns overlap — "April 2025" is also matched by the abbreviation one —
    # so a pair is judged once: a comparison anchor is clearable, and reading the
    # same date twice would turn a cleared date into a stale one.
    date_hits: List[tuple[int, int, str]] = []
    for month_text, year_text in STALE_DATE_RE.findall(text):
        date_hits.append((int(year_text), MONTH_INDEX[month_text.lower()], month_text))

    for _day_text, month_text, year_text in STALE_SHORT_DATE_RE.findall(text):
        month = ABBREVIATED_MONTH_INDEX.get(month_text.lower())
        if month is None:
            continue
        date_hits.append((int(year_text), month, month_text))

    # Numeric dates, in both orders. A month-name comparison anchor cannot clear
    # one of these, because there is no month word to look behind from, so a
    # numeric date before the window is simply stale.
    #
    # "7/30/2020" is unambiguous only because 30 cannot be a month; where both
    # numbers fit, the US order is assumed, which is what the measured dockets
    # (and the SEC filings) use.
    for first, second, four_year, short_first, short_second, short_year, iso_year, iso_month, _iso_day in (
        STALE_NUMERIC_DATE_RE.findall(text)
    ):
        if four_year:
            year, left, right = int(four_year), int(first), int(second)
        elif short_year:
            year = 2000 + int(short_year)
            left, right = int(short_first), int(short_second)
        else:
            year, left, right = int(iso_year), int(iso_month), 0
        if not four_year and not short_year:
            month = int(iso_month)
        elif left > 12 and right <= 12:
            month = right
        else:
            month = left
        if 1 <= month <= 12:
            date_hits.append((year, month, ""))

    judged: set[tuple[int, int]] = set()
    for year, month, month_text in date_hits:
        if (year, month) in judged:
            continue
        judged.add((year, month))

        if year < window_year:
            if month_text and _is_comparison_anchor(text, month_text, str(year)):
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
        if recovered is None:
            # A filing states its own date and often nowhere else.
            recovered = date_from_body(str(result.get("raw_content") or ""))
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
