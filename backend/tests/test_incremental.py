"""Tests for analyst-opinion recovery and cross-run fact merging."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.fact_base import FactItem
from app.services.fact_extraction import extract_analyst_opinions, extract_hard_facts
from app.services.ingestion import _merge_reused_facts

LEAD = (
    "NIO reported third quarter revenue of 18.7 billion yuan, down from "
    "21.1 billion yuan a year earlier, according to the company filing.",
    "Vehicle deliveries came to 61,855 units in the quarter, a decline of "
    "about 12 percent against the same period last year.",
    "Cash and cash equivalents stood at 41.3 billion yuan at the end of "
    "the quarter, according to the same filing.",
)

# Commentary that carries no negative signal: it must never reach hard facts.
OPINIONS = (
    "Analysts describe the brand as a design-led challenger with a loyal "
    "following among younger urban buyers in the coastal provinces.",
    "The company presents its battery swap network as a strategic moat that "
    "differentiates it from more conventional electric vehicle makers.",
)

NEGATIVE = (
    "The company said margin pressure widened as discounting rose, and "
    "management warned that cash flow would stay negative for the year."
)

CLOSING = (
    "Management will host an earnings call to discuss the results with "
    "investors later this week, the filing said."
)


def _body(*paragraphs: str) -> str:
    return "\n\n".join(paragraphs)


def test_opinions_are_recovered_from_middle_paragraphs():
    body = _body(*LEAD, *OPINIONS, CLOSING)

    hard = extract_hard_facts(body)
    opinions = extract_analyst_opinions(body, hard)

    assert opinions, "middle commentary should be recoverable as opinions"
    for opinion in opinions:
        assert opinion in OPINIONS, opinion
        assert opinion not in hard


def test_hard_facts_never_contain_commentary():
    body = _body(*LEAD, *OPINIONS, CLOSING)
    hard = extract_hard_facts(body)

    for opinion in OPINIONS:
        assert opinion not in hard


def test_negative_sentence_is_a_fact_not_an_opinion():
    body = _body(*LEAD, NEGATIVE, *OPINIONS, CLOSING)

    hard = extract_hard_facts(body)
    opinions = extract_analyst_opinions(body, hard)

    assert any("margin pressure" in fact for fact in hard)
    assert not any("margin pressure" in opinion for opinion in opinions)


def test_opinions_respect_the_per_source_cap():
    filler = [
        f"Commentary number {i} about the brand strategy and its positioning."
        for i in range(8)
    ]
    body = _body(*LEAD, *filler, CLOSING)

    opinions = extract_analyst_opinions(body, extract_hard_facts(body))

    assert len(opinions) <= 3


def test_short_article_yields_no_opinions():
    body = _body(*LEAD)

    assert extract_analyst_opinions(body, extract_hard_facts(body)) == []


def _item(content: str, day: int) -> FactItem:
    return FactItem(
        content=content,
        source_url="https://www.reuters.com/business/nio-earnings",
        publish_date=datetime(2026, 10, day, tzinfo=timezone.utc),
    )


def test_reused_facts_survive_a_run_that_fetches_nothing():
    stored = [_item("NIO reported revenue of 18.7 billion yuan.", 12)]

    merged = _merge_reused_facts(stored, [])

    assert len(merged) == 1
    assert merged[0].content == stored[0].content


def test_reused_and_fresh_facts_are_merged_without_duplicates():
    stored = [_item("NIO reported revenue of 18.7 billion yuan.", 12)]
    fresh = [
        _item("NIO reported revenue of 18.7 billion yuan.", 20),
        _item("Deliveries reached 61,855 units in the quarter.", 20),
    ]

    merged = _merge_reused_facts(stored, fresh)

    assert len(merged) == 2
    assert merged[0].publish_date.month == 10


def test_extended_telling_replaces_the_stored_one():
    shorter = (
        "NIO reported third quarter revenue of 18.7 billion yuan, down from "
        "21.1 billion yuan a year earlier, according to the company filing."
    )
    longer = shorter + " Of that total, 16.9 billion yuan came from vehicle sales."
    stored = [_item(shorter, 12)]
    fresh = [_item(longer, 20)]

    merged = _merge_reused_facts(stored, fresh)

    assert len(merged) == 1
    assert merged[0].content == longer
