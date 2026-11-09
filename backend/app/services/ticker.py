# /backend/app/services/ticker.py
"""Find the exchange ticker for a company, and refuse to guess when unsure.

Price is a fact with a clock, and a brief that says "shares trade at 205" is wrong
tomorrow. The quote is fetched only when the caller supplied a ticker, which meant
a brief on a listed company usually had no price at all: measured, five stored
briefs for Canada Goose (GOOS on NYSE, GOOS.TO on Toronto) carried no quote
because nobody typed a ticker.

Yahoo's search endpoint answers a company name with candidate symbols, needs no key
and no model call. But a symbol is not an identity: measured, searching "YKK" — a
private Japanese group — returned `YKK.SG` (Yleiselektroniikka Oyj) and `YKK.F`
(Boreo Oyj), two unrelated Finnish companies. Attaching those prices to a YKK brief
would be worse than having no price, so a candidate is accepted only when its
registered name actually matches the company being briefed.

The identity check reuses `company_identity._name_similarity`, the same scorer the
legal-entity registry uses, so "matches strongly enough to trust" means one thing in
this codebase rather than two.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

from app.services.company_identity import _name_similarity
from app.services.logger_service import get_logger

logger = get_logger("ticker")

SEARCH_ENDPOINT = "https://query1.finance.yahoo.com/v1/finance/search"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT_SECONDS = 12

# A candidate must be an ordinary equity on a real exchange. An ETF tracking the
# stock, a tokenised derivative, a currency pair and a crypto wrapper all answer
# the same search with the same name, and none of them is the share price.
EQUITY_TYPES = ("EQUITY", "MUTUALFUND")

# Below this name match the symbol belongs to some other company. Set at the
# registry's own bar so both lookups agree on what "this is the same company" means.
MIN_NAME_MATCH = 0.72

# Suffixes for venues Yahoo lists separately from the issuer. GOOS.TO is the same
# company's Toronto line; either is a faithful price for the brief.
PREFERRED_SUFFIXES = ("", ".TO", ".L", ".T", ".HK", ".NS", ".BO", ".AX", ".PA", ".DE")


@dataclass(frozen=True)
class TickerMatch:
    """A symbol that belongs to the company being briefed."""

    symbol: str
    exchange: str
    name: str
    score: float


def _search(company: str) -> List[dict]:
    params = urllib.parse.urlencode(
        {"q": company, "quotesCount": 8, "newsCount": 0, "listsCount": 0}
    )
    request = urllib.request.Request(
        f"{SEARCH_ENDPOINT}?{params}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        logger.info("Ticker search unavailable | company=%r | %s", company, exc)
        return []

    if not isinstance(payload, dict):
        return []
    quotes = payload.get("quotes")
    return [item for item in quotes if isinstance(item, dict)] if quotes else []


def _symbol_rank(symbol: str) -> int:
    """Rank a symbol by how directly it names the issuer's own listing."""
    upper = symbol.upper()
    for index, suffix in enumerate(PREFERRED_SUFFIXES):
        if suffix and upper.endswith(suffix):
            return index
    return 0 if upper.isalpha() or "." not in upper else len(PREFERRED_SUFFIXES)


def resolve_ticker(company: str) -> Optional[TickerMatch]:
    """Return the symbol that names this company, or None.

    None is the honest answer for a private company, for a name that several
    businesses share, and for a foreign issuer with no depositary listing: in each
    case any symbol found would belong to someone else.
    """
    name = " ".join((company or "").split())
    if not name:
        return None

    candidates: List[TickerMatch] = []
    for quote in _search(name):
        symbol = str(quote.get("symbol") or "").strip()
        kind = str(quote.get("quoteType") or "").upper()
        if not symbol or kind not in EQUITY_TYPES:
            continue
        # A multi-leg or derivative symbol ("NIO=X", "SNDK-USD") is not a listing.
        if any(ch in symbol for ch in ("=", "^", "-")):
            continue

        issuer = str(quote.get("shortname") or quote.get("longname") or "")
        score = _name_similarity(issuer, name)
        if score < MIN_NAME_MATCH:
            logger.info(
                "Ticker candidate rejected | company=%r | symbol=%s | issuer=%r | "
                "score=%.2f",
                name, symbol, issuer, score,
            )
            continue
        candidates.append(
            TickerMatch(
                symbol=symbol,
                exchange=str(quote.get("exchDisp") or quote.get("exchange") or ""),
                name=issuer,
                score=score,
            )
        )

    if not candidates:
        logger.info("No ticker matched the company name | company=%r", name)
        return None

    # Highest name match first, then the most direct listing.
    candidates.sort(key=lambda item: (-item.score, _symbol_rank(item.symbol)))
    chosen = candidates[0]
    logger.info(
        "Ticker resolved | company=%r | symbol=%s | exchange=%s | issuer=%r | score=%.2f",
        name, chosen.symbol, chosen.exchange, chosen.name, chosen.score,
    )
    return chosen
