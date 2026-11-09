"""The auditor must see the sentence a claim was built from.

Measured failure: seven claims were removed from one brief as "unsupported" while
every one of their figures was present in the evidence set. The auditor was
judging against a window too small to contain them — a per-fact 300-character
slice hid "$497 million" inside a 700-character earnings-call fact, and a six-fact
cap hid figures stated further down the article. Rejecting a true claim empties
a correct brief, which is the failure mode this file guards.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.fact_base import FactBase, FactItem
from app.services.report_audit import MAX_CITED_CHARS, _cited_evidence


def _base(*facts: FactItem) -> FactBase:
    return FactBase(
        company_id="cg",
        last_refresh_date=datetime.now(timezone.utc),
        hard_facts=list(facts),
        analyst_opinions=[],
    )


class _Claim:
    def __init__(self, source_url: str, claim: str) -> None:
        self.source_url = source_url
        self.claim = claim


def test_a_figure_deep_inside_a_long_fact_is_visible_to_the_auditor():
    # "$497 million" sat past character 300 of this fact. The old per-fact cap
    # showed the question and truncated before the answer, so the claim built
    # from the answer was rejected as unsupported.
    long_fact = (
        "**Q**: What is the state of inventory and the balance sheet heading into "
        "peak season? **A**: Neil Bowden, CFO, said inventory was $497 million, "
        "up 8% year-over-year, reflecting an expanded assortment, a larger "
        "wholesale order book, lower-than-planned D2C sales, and planned production "
        "growth for peak season, with inventory turnover improving 8%."
    )
    base = _base(
        FactItem(content=long_fact, source_url="https://x/call"),
    )

    window = _cited_evidence(
        _Claim("https://x/call", "CFO said inventory stood at C$497m, up 8%."),
        base,
    )

    assert "$497 million" in window
    assert len(window) > 300


def test_every_fact_under_the_cited_url_reaches_the_window():
    # Figures stated further down an article were invisible under the six-fact
    # cap.
    facts = [
        FactItem(content=f"Fact {index}: revenue up {index}9 percent.", source_url="https://x/a")
        for index in range(1, 9)
    ]
    base = _base(*facts)

    window = _cited_evidence(_Claim("https://x/a", "revenue rose"), base)

    assert "Fact 8" in window, "the eighth fact under the same article was dropped"


def test_the_url_match_tolerates_a_tracking_query():
    # Search results are stored with tracking parameters; a model echoes the
    # URL without them, or with them. Either way it is the same article.
    base = _base(
        FactItem(
            content="Revenue rose 5.9% to C$274.0m.",
            source_url="https://x/a.html?fr=sycsrp_catchall",
        )
    )

    assert "274.0" in _cited_evidence(_Claim("https://x/a.html", "revenue"), base)
    assert "274.0" in _cited_evidence(
        _Claim("https://x/a.html?fr=sycsrp_catchall", "revenue"), base
    )


def test_a_claim_citing_nothing_gets_an_explicit_marker():
    # A marker, not silence: the auditor must be able to tell "no source" apart
    # from "source we have never seen".
    assert _cited_evidence(_Claim("", "claim"), _base()) == "(no source cited)"
    assert (
        _cited_evidence(_Claim("https://x/unknown", "claim"), _base())
        == "(source not in evidence)"
    )


def test_the_window_stays_bounded():
    # Bounded so one long transcript cannot dominate the audit prompt. The
    # separator between facts is not budgeted, so the bound is compared with
    # a small tolerance rather than assumed exact.
    facts = [
        FactItem(content="x" * 3000, source_url="https://x/long") for _ in range(5)
    ]

    window = _cited_evidence(_Claim("https://x/long", "claim"), _base(*facts))

    assert len(window) <= MAX_CITED_CHARS + 4 * len(" || ")
