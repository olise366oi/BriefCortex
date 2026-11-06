# /backend/app/services/own_company_scan.py
"""Read the caller's own company from the market, not from the newsroom.

A briefing answers "how do we deal with B". That answer depends on where we
ourselves stand right now: a company whose shares have fallen 30% in a month
negotiates differently from one at a record high. So the caller's own company is
scanned as well — but deliberately narrowly.

What is scanned:
- the market quote and the news that moved it, which is the fastest honest read
  of a public company's current position.

What is not scanned:
- job postings. Our own hiring tells us nothing about the counterparty, and it
  would add a lane of retrieval for no bearing on the decision.
- the full evidence pipeline. The caller's company is context for the
  recommendation, not a subject of the brief, so it is kept small on purpose.

A caller who would rather supply internal material than have us read the market
chooses that at the request level; this module is only the retrieval path.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import List, Optional

from app.services.logger_service import get_logger
from app.services.market_data import Quote, fetch_quote
from app.services.tavily_search import tavily_search

logger = get_logger("own_company_scan")

# One query. The point is a current read, not a dossier.
OWN_NEWS_QUERY_TEMPLATE = "{company} share price results guidance outlook"

# The caller's own company is read over 60 days, not the 30 that a counterparty
# gets: its position is the baseline every deal is argued against, and one month
# is too short to characterise a trend. Positive and negative coverage are both
# kept — a company that only remembers its good news cannot be assessed.
OWN_COMPANY_WINDOW_DAYS = 60

# Enough to characterise the recent position without duplicating the main brief.
MAX_OWN_NEWS_RESULTS = 8


@dataclass
class OwnCompanySnapshot:
    """What the market says about the caller's own company right now."""

    company: str
    quote: Optional[Quote] = None
    # Headlines that moved the stock, newest first. Kept to the title and link:
    # this is orientation, not evidence for the brief.
    headlines: List[dict] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def has_content(self) -> bool:
        return self.quote is not None or bool(self.headlines)

    def to_prompt_block(self) -> str:
        """Render for the analysis stage, where it explains the deal's footing."""
        if not self.has_content:
            return ""

        lines = [f"OUR COMPANY: {self.company}"]
        if self.quote is not None:
            lines.append(f"market: {self.quote.to_line()}")
        if self.headlines:
            lines.append("recent market coverage:")
            for item in self.headlines[:5]:
                date = str(item.get("published_date") or "")[:10] or "undated"
                title = str(item.get("title") or "")[:140]
                lines.append(f"  - {date} {title}")
        return "\n".join(lines)


async def scan_own_company(
    company: str,
    ticker: str = "",
    window_days: int = OWN_COMPANY_WINDOW_DAYS,
) -> OwnCompanySnapshot:
    """Return the market position of the caller's own company.

    Runs the quote and the news query concurrently: they are independent, and
    waiting for one before starting the other doubles the cost for no reason.
    """
    name = (company or "").strip()
    if not name:
        return OwnCompanySnapshot(company="", error="no company name supplied")

    news_task = asyncio.to_thread(_fetch_own_news, name, window_days)
    quote_task = (
        asyncio.to_thread(fetch_quote, ticker) if (ticker or "").strip()
        else asyncio.sleep(0, result=None)
    )

    news, quote = await asyncio.gather(news_task, quote_task, return_exceptions=True)

    snapshot = OwnCompanySnapshot(company=name)
    if isinstance(quote, Exception):
        logger.info("Own quote failed | company=%r | %s", name, quote)
    else:
        snapshot.quote = quote

    if isinstance(news, Exception):
        logger.info("Own news failed | company=%r | %s", name, news)
        snapshot.error = str(news)
    else:
        snapshot.headlines = news or []

    logger.info(
        "Own company scanned | company=%r | quote=%s | headlines=%s",
        name,
        snapshot.quote is not None,
        len(snapshot.headlines),
    )
    return snapshot


def _fetch_own_news(company: str, window_days: int = OWN_COMPANY_WINDOW_DAYS) -> List[dict]:
    """Fetch recent market coverage for the caller's own company.

    Dated to the own-company window rather than the brief's, so the baseline is
    60 days of movement even when the counterparty brief looks at 30.
    """
    from datetime import date, timedelta

    end = date.today()
    start = end - timedelta(days=window_days - 1)
    query = OWN_NEWS_QUERY_TEMPLATE.format(company=company)
    try:
        results = tavily_search(
            query=query,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            company_name=company,
            domains=[],
        )
    except Exception as exc:
        logger.info("Own news query failed | company=%r | %s", company, exc)
        return []

    trimmed: List[dict] = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url:
            continue
        trimmed.append(
            {
                "title": str(item.get("title") or "")[:200],
                "url": url,
                "published_date": item.get("published_date"),
            }
        )
        if len(trimmed) >= MAX_OWN_NEWS_RESULTS:
            break
    return trimmed
