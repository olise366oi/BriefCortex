# /backend/app/services/market_data.py
"""Fetch the market price for a public company, separately from the news.

Price is a fact with a clock: a brief that says "shares trade at 205" is wrong
tomorrow, and no news article is obliged to state the current number. It used to
be handled implicitly — whatever price an article happened to mention — which
meant the brief either had one stale number or none at all.

This fetches it deliberately, from a public quote endpoint, and returns a
timestamped record. A company with no ticker simply yields no record rather than
an invented one.

Source: Yahoo Finance's public chart endpoint. It needs no key, returns JSON, and
carries the previous close so a daily change can be computed. It is a quote
service, not a reporting service: the value is the market's number, not an
editorial claim, and it is never merged into `hard_facts`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.services.logger_service import get_logger

logger = get_logger("market_data")

QUOTE_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT_SECONDS = 12

# Suffixes the quote endpoint uses for non-US listings, tried in order when a
# bare ticker returns nothing. A company registered in India trades as .NS.
EXCHANGE_SUFFIXES = ("", ".NS", ".BO", ".L", ".DE", ".PA", ".T", ".HK", ".SI")


@dataclass(frozen=True)
class Quote:
    """One market price reading, with the moment it was taken."""

    ticker: str
    price: float
    currency: str
    previous_close: Optional[float]
    as_of: datetime
    source_url: str

    @property
    def change_percent(self) -> Optional[float]:
        if not self.previous_close:
            return None
        return (self.price - self.previous_close) / self.previous_close * 100

    def to_line(self) -> str:
        """Render as one readable line for the brief."""
        delta = self.change_percent
        parts = [f"{self.ticker} {self.price:,.2f} {self.currency}"]
        if delta is not None:
            parts.append(f"{delta:+.2f}% versus previous close")
        parts.append(f"as of {self.as_of.strftime('%Y-%m-%d %H:%M UTC')}")
        return " | ".join(parts)


def _fetch_chart(ticker: str) -> Optional[dict]:
    """Return the raw chart payload for one ticker, or None."""
    params = urllib.parse.urlencode({"range": "1d", "interval": "1d"})
    request = urllib.request.Request(
        f"{QUOTE_ENDPOINT.format(ticker=urllib.parse.quote(ticker))}?{params}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        logger.info("Quote rejected | ticker=%s | status=%s", ticker, exc.code)
        return None
    except Exception as exc:
        logger.info("Quote unreadable | ticker=%s | %s", ticker, exc)
        return None


def _parse(payload: dict, ticker: str) -> Optional[Quote]:
    """Turn a chart payload into a Quote, or None when it carries no price."""
    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        price = meta.get("regularMarketPrice")
        if price is None:
            closes = result.get("indicators", {}).get("quote", [{}])[0].get("close") or []
            price = next((value for value in reversed(closes) if value is not None), None)
        if price is None:
            return None

        stamp = meta.get("regularMarketTime")
        as_of = (
            datetime.fromtimestamp(int(stamp), tz=timezone.utc)
            if stamp
            else datetime.now(timezone.utc)
        )
        return Quote(
            ticker=str(meta.get("symbol") or ticker),
            price=float(price),
            currency=str(meta.get("currency") or ""),
            previous_close=meta.get("previousClose") or meta.get("chartPreviousClose"),
            as_of=as_of,
            source_url=(
                f"https://finance.yahoo.com/quote/{urllib.parse.quote(ticker)}"
            ),
        )
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def fetch_quote(ticker: str) -> Optional[Quote]:
    """Return the current quote for a ticker, trying common exchange suffixes."""
    base = (ticker or "").strip().upper()
    if not base or not base.isascii():
        return None

    for suffix in EXCHANGE_SUFFIXES:
        payload = _fetch_chart(base + suffix)
        if payload is None:
            continue
        quote = _parse(payload, base + suffix)
        if quote is not None:
            logger.info(
                "Quote fetched | ticker=%s | price=%s %s",
                quote.ticker,
                quote.price,
                quote.currency,
            )
            return quote
    logger.info("No quote for ticker | ticker=%s", base)
    return None
