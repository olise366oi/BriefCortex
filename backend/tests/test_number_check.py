"""Every figure a claim states must exist somewhere in the evidence.

Measured failure this guards: a live brief reported "net loss of C$26.3m" and
"inventory of C$497m" — both true — next to figures no source stated. The model
audit caught some of those; it costs a model call per claim and it was also
starving itself (a window too small to hold the figure it was judging against).
A string comparison catches fabrication for nothing.

The check is one-sided on purpose: it flags a figure only when **no** evidence
fact contains it. A claim that consolidates several facts legitimately states
figures from all of them, and flagging those is exactly the bug that emptied a
correct brief before.
"""
from __future__ import annotations

from app.services.number_check import figures_in, unsupported_numbers

EVIDENCE = (
    "Canada Goose reported unaudited second-quarter revenue of C$274.0 million "
    "for the period ended September 27, up from C$258.8 million a year earlier, "
    "while gross profit rose to C$182.9 million.",
    "The net loss widened to C$26.3 million from C$17.4 million, including a "
    "C$7.8 million discontinued-operations loss.",
    "Neil Bowden said inventory was $497 million, up 8% year-over-year.",
    "Management said Q2 gross margin expanded 280 basis points (210 year to date).",
    "| 1,348.4 | 1,333.8 | 14.6 | 1.1% |",
)


# --- the figures a real claim carries must pass --------------------------

def test_a_true_claim_is_not_flagged():
    claim = "revenue rose to C$274.0 million from C$258.8 million"

    assert unsupported_numbers(claim, EVIDENCE) == []


def test_a_figure_stated_with_a_different_currency_marker_passes():
    # The evidence writes "$497 million", the claim writes "C$497m". Same figure.
    assert unsupported_numbers("inventory stood at C$497m", EVIDENCE) == []


def test_a_figure_with_thousands_separators_passes():
    assert unsupported_numbers("revenue of C$1,348.4 million", EVIDENCE) == []


def test_a_basis_point_expansion_passes():
    assert unsupported_numbers(
        "margin expanded 280 basis points (210 year to date)", EVIDENCE
    ) == []


# --- fabrication must be caught ------------------------------------------

def test_an_invented_figure_is_caught():
    missing = unsupported_numbers("revenue rose 12.4% to C$312.5 million", EVIDENCE)

    assert "12.4" in missing
    assert "312.5" in missing


def test_an_invented_rate_is_caught_at_two_digits():
    # "up 11%" is two digits; a percentage is meaningful at that width.
    assert "11" in unsupported_numbers("inventory was C$521m, up 11%", EVIDENCE)


def test_a_whole_claim_of_invented_figures_is_reported():
    missing = unsupported_numbers("margin expanded 340 basis points", EVIDENCE)

    assert missing == ["340"]


# --- one-sided by design -------------------------------------------------

def test_a_figure_from_another_source_in_the_set_is_not_flagged():
    # A claim that consolidates several facts is not a fabrication. This was the
    # failure that emptied a correct brief.
    claim = "revenue C$274.0 million, inventory $497 million, margin 280 basis points"

    assert unsupported_numbers(claim, EVIDENCE) == []


def test_a_claim_with_no_figures_is_never_flagged():
    assert unsupported_numbers("The company appointed a new chief executive.", EVIDENCE) == []


def test_single_digits_are_ignored():
    # "part 2", "Q2" and similar appear in any sentence and prove nothing.
    assert ambiguous_single_digits() == []


def ambiguous_single_digits():
    return unsupported_numbers("It was the second of 3 proposals.", ["unrelated text"])


# --- extraction ----------------------------------------------------------

def test_figures_are_normalised_for_comparison():
    assert figures_in("C$274.0 million") == ["274.0"]
    assert figures_in("1,348.4") == ["1348.4"]
    assert figures_in("up 5.9%") == ["5.9"]


def test_an_empty_evidence_set_flags_every_figure():
    # Nothing can be verified against nothing; the audit refuses rather than
    # passes, consistent with the reuse rule.
    assert unsupported_numbers("revenue C$274.0 million", []) == ["274.0"]
