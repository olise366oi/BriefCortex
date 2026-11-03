"""Regression tests for the extraction rules that were previously untested."""
from __future__ import annotations

import pytest

from app.services.fact_extraction import (
    dedup_key,
    extract_hard_facts,
    find_prefix_twin,
    has_negative_signal,
    normalize_text,
)
from app.services.keywords import match_word

NEGATIVE_PARAGRAPHS = (
    "NIO missed its delivery guidance for the September quarter, "
    "reporting fewer vehicles than the company had promised.",
    "The company said margin pressure widened as discounting rose, "
    "and management warned that cash flow would stay negative.",
)

NEUTRAL_PARAGRAPHS = (
    "The core mission of the brand is to sell premium electric vehicles "
    "to affluent buyers across the coastal provinces of China.",
    "A mission statement on the company website describes the target "
    "buyer in detail and mentions no financial commitment at all.",
    "Executives host a quarterly town hall where employees ask questions "
    "about product planning and the design language of the next model.",
)

LEAD_PARAGRAPHS = (
    "NIO reported third quarter revenue of 18.7 billion yuan, down from "
    "21.1 billion yuan a year earlier, according to the company filing.",
    "Vehicle deliveries came to 61,855 units in the quarter, a decline of "
    "about 12 percent against the same period last year.",
)


@pytest.mark.parametrize(
    "text",
    [
        "This is our core mission for 2026.",
        "The mission statement mentions the target buyer.",
        "A commission reviewed the emission standard.",
    ],
)
def test_no_negative_signal_inside_longer_words(text):
    assert has_negative_signal(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "The company missed its own guidance.",
        "It reported net losses for a fourth quarter.",
        "Shares fell sharply after the release.",
        "Management issued a profit warning.",
        "Margins are plunging as discounts deepen.",
    ],
)
def test_negative_signals_still_detected(text):
    assert has_negative_signal(text) is True


def test_middle_paragraph_without_signal_is_dropped():
    # Three leading paragraphs and one closing paragraph are always read; the
    # neutral paragraphs sit in the middle, where only a negative signal can
    # rescue them.
    third_lead = (
        "Cash and cash equivalents stood at 41.3 billion yuan at the end of "
        "the quarter, according to the same filing."
    )
    body = "\n\n".join(
        LEAD_PARAGRAPHS + (third_lead,) + NEUTRAL_PARAGRAPHS + (LEAD_PARAGRAPHS[1],)
    )

    facts = extract_hard_facts(body)

    assert facts[:2] == list(LEAD_PARAGRAPHS)
    assert facts[-1] == LEAD_PARAGRAPHS[1]
    for neutral in NEUTRAL_PARAGRAPHS:
        assert neutral not in facts


def test_middle_paragraph_with_signal_is_recovered():
    body = "\n\n".join(LEAD_PARAGRAPHS + NEGATIVE_PARAGRAPHS + NEUTRAL_PARAGRAPHS)

    facts = extract_hard_facts(body)

    recovered = [fact for fact in facts if "missed its delivery guidance" in fact]
    assert recovered, facts


def test_dedup_key_is_exact_text_not_prefix():
    first = "NIO reported third quarter revenue of 18.7 billion yuan, down from 21.1 billion"
    second = "NIO reported third quarter revenue of 18.7 billion yuan, down from 21.1 billion"
    third = "NIO reported third quarter revenue of 18.7 billion yuan, up 4 percent on the prior year"

    # Same sentence collapses; a different fact sharing the opening words does not.
    assert dedup_key(first) == dedup_key(second)
    assert normalize_text(first)[:40] == normalize_text(third)[:40]
    assert dedup_key(first) != dedup_key(third)


def test_prefix_twin_requires_substantial_overlap():
    shorter = (
        "NIO reported third quarter revenue of 18.7 billion yuan, down from "
        "21.1 billion yuan a year earlier, according to the company filing."
    )
    longer = (
        shorter
        + " Of that total, 16.9 billion yuan came from vehicle sales and 1.8 "
        "billion from services."
    )

    assert find_prefix_twin([normalize_text(shorter)], longer) == 0


def test_short_shared_opening_is_not_a_twin():
    a = "NIO reported a decline in deliveries."
    b = "NIO reported a decline in deliveries, but the drop was limited to one model."

    assert find_prefix_twin([normalize_text(a)], b) is None


def test_similar_opening_different_facts_are_kept():
    a = "NIO reported third quarter revenue of 18.7 billion yuan, down from 21.1 billion."
    b = "NIO reported third quarter revenue of 18.7 billion yuan but deliveries fell 12 percent."

    assert find_prefix_twin([normalize_text(a)], b) is None


def test_match_word_respects_boundaries():
    assert match_word("one of the largest shareholders", "shares") is False
    assert match_word("the shares closed lower", "shares") is True
    assert match_word("tariff increase raised costs", "rif") is False
