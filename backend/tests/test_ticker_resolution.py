"""The ticker resolver finds a real listing and refuses to invent one.

Measured: five stored briefs for Canada Goose — GOOS on NYSE, GOOS.TO on Toronto —
carried no price at all, because the quote fetch required a ticker the caller had
to type. And the opposite failure is worse: searching a company name for a private
group answers with symbols belonging to unrelated businesses (measured, "YKK"
matched Ykleiselektroniikka Oyj and Boreo Oyj, two Finnish firms).
"""
from __future__ import annotations

from app.services import ticker as ticker_module
from app.services.ticker import MIN_NAME_MATCH, TickerMatch, resolve_ticker


def _quote(symbol: str, kind: str, name: str) -> dict:
    return {"symbol": symbol, "quoteType": kind, "shortname": name, "exchDisp": "NYSE"}


def _patch(monkeypatch, quotes: list[dict]):
    monkeypatch.setattr(ticker_module, "_search", lambda _company: quotes)


# --- a real listing is found --------------------------------------------

def test_a_matching_symbol_is_accepted(monkeypatch):
    _patch(
        monkeypatch,
        [
            _quote("GOOS", "EQUITY", "Canada Goose Holdings Inc."),
            _quote("GOOS.TO", "EQUITY", "CANADA GOOSE HOLDINGS INC"),
        ],
    )

    match = resolve_ticker("Canada Goose")

    assert match is not None
    assert match.symbol in ("GOOS", "GOOS.TO")
    assert match.score >= MIN_NAME_MATCH


def test_the_most_direct_listing_wins_when_scores_tie(monkeypatch):
    # A bare symbol names the issuer's own line; a suffixed one is a foreign venue
    # for the same share. Both are faithful, the bare one is preferred.
    _patch(
        monkeypatch,
        [
            _quote("GOOS.TO", "EQUITY", "CANADA GOOSE HOLDINGS INC"),
            _quote("GOOS", "EQUITY", "Canada Goose Holdings Inc."),
        ],
    )

    assert resolve_ticker("Canada Goose").symbol == "GOOS"


# --- an unrelated symbol is refused -------------------------------------

def test_a_symbol_for_a_different_company_is_refused(monkeypatch):
    # The real trap: "YKK" is a private Japanese group, and the search answers with
    # two unrelated Finnish companies.
    _patch(
        monkeypatch,
        [
            _quote("YKK.SG", "EQUITY", "Yleiselektroniikka Oyj"),
            _quote("YKK.F", "EQUITY", "Boreo Oyj"),
        ],
    )

    assert resolve_ticker("YKK") is None


def test_no_candidates_means_no_ticker(monkeypatch):
    _patch(monkeypatch, [])

    assert resolve_ticker("Quiet Private Holdings") is None


def test_an_empty_name_resolves_to_nothing(monkeypatch):
    # Must not spend a network call on an empty name.
    _patch(monkeypatch, [_quote("X", "EQUITY", "X Corp")])

    assert resolve_ticker("   ") is None


# --- only an ordinary share price counts ---------------------------------

def test_a_currency_pair_is_not_a_listing(monkeypatch):
    _patch(
        monkeypatch,
        [_quote("NIO=X", "CURRENCY", "USD/NIO"), _quote("NIO", "EQUITY", "NIO Inc.")],
    )

    match = resolve_ticker("NIO")

    assert match is not None
    assert match.symbol == "NIO"


def test_a_tokenised_derivative_is_not_a_listing(monkeypatch):
    _patch(
        monkeypatch,
        [
            _quote("SNDK-USD", "CRYPTOCURRENCY", "SanDisk Corporation (Derivatives)"),
            _quote("SNDK", "EQUITY", "Sandisk Corporation"),
        ],
    )

    assert resolve_ticker("Sandisk").symbol == "SNDK"


def test_an_etf_tracking_the_stock_is_not_the_stock(monkeypatch):
    _patch(
        monkeypatch,
        [
            _quote("SNDK.TO", "ETF", "SANDISK CDR (CAD HEDGED)"),
            _quote("SNDK", "EQUITY", "Sandisk Corporation"),
        ],
    )

    assert resolve_ticker("Sandisk").symbol == "SNDK"


def test_a_candidate_below_the_name_bar_is_dropped(monkeypatch):
    _patch(monkeypatch, [_quote("ABC", "EQUITY", "Unrelated Widgets Corp")])

    assert resolve_ticker("Canada Goose") is None


def test_the_match_carries_its_evidence(monkeypatch):
    # The caller logs which issuer was matched, so a later wrong quote can be traced
    # to the search rather than to the fetch.
    _patch(monkeypatch, [_quote("GOOS", "EQUITY", "Canada Goose Holdings Inc.")])

    match = resolve_ticker("Canada Goose")

    assert isinstance(match, TickerMatch)
    assert match.name
    assert match.exchange
