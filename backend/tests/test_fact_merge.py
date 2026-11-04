"""Tests for merging facts into one entry that keeps every source."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.fact_base import FactItem
from app.services import dedup


def _fact(content: str, url: str, also=()) -> FactItem:
    return FactItem(
        content=content,
        source_url=url,
        publish_date=datetime(2026, 11, 3, tzinfo=timezone.utc),
        also_reported_by=list(also),
    )


@pytest.fixture()
def same_fact_everywhere(monkeypatch):
    """Make the model confirm every candidate pair, so merging is exercised."""

    async def confirm(existing, incoming, pairs):
        return list(pairs)

    monkeypatch.setattr(dedup, "confirm_same_facts", confirm)


SHORT = "NIO delivered 34,246 vehicles in October."
FULLER = (
    "NIO delivered 34,246 vehicles in October 2026, comprising 18,125 "
    "from the NIO brand and 16,121 from ONVO."
)


def test_all_sources_keeps_primary_first():
    fact = _fact(SHORT, "https://a.com/x", ["https://b.com/y", "https://a.com/x"])
    assert fact.all_sources() == ["https://a.com/x", "https://b.com/y"]


def test_identical_facts_collapse_into_one_with_both_sources(same_fact_everywhere):
    import asyncio

    facts = [
        _fact(SHORT, "https://a.com/x"),
        _fact(FULLER, "https://b.com/y"),
    ]

    merged, stats = asyncio.run(dedup.merge_duplicate_facts(facts))

    assert len(merged) == 1
    # The fuller telling wins because it carries more detail.
    assert merged[0].content == FULLER
    # Both links survive.
    assert merged[0].all_sources() == ["https://b.com/y", "https://a.com/x"]
    assert stats["merged"] == 1


def test_merged_entry_keeps_sources_from_a_third_outlet(same_fact_everywhere):
    import asyncio

    facts = [
        _fact(SHORT, "https://a.com/x"),
        _fact(FULLER, "https://b.com/y"),
        _fact(SHORT, "https://c.com/z"),
    ]

    merged, _stats = asyncio.run(dedup.merge_duplicate_facts(facts))

    assert len(merged) == 1
    assert set(merged[0].all_sources()) == {
        "https://a.com/x",
        "https://b.com/y",
        "https://c.com/z",
    }


def test_distinct_facts_are_not_merged(monkeypatch):
    import asyncio

    async def confirm_nothing(existing, incoming, pairs):
        return []

    monkeypatch.setattr(dedup, "confirm_same_facts", confirm_nothing)

    facts = [
        _fact("NIO cut 400 jobs in March.", "https://a.com/x"),
        _fact("NIO cut 400 jobs in June.", "https://b.com/y"),
    ]

    merged, stats = asyncio.run(dedup.merge_duplicate_facts(facts))

    assert len(merged) == 2
    assert stats["merged"] == 0
    assert stats["kept"] == 2


def test_a_single_fact_is_returned_untouched():
    import asyncio

    facts = [_fact(SHORT, "https://a.com/x")]

    merged, stats = asyncio.run(dedup.merge_duplicate_facts(facts))

    assert merged == facts
    assert stats["considered"] == 1


def test_dissimilar_facts_skip_the_model_entirely(monkeypatch):
    import asyncio

    called = {"n": 0}

    async def counting_confirm(existing, incoming, pairs):
        called["n"] += 1
        return []

    monkeypatch.setattr(dedup, "confirm_same_facts", counting_confirm)

    facts = [
        _fact("NIO cut jobs.", "https://a.com/x"),
        _fact("The regulator fined a bank.", "https://b.com/y"),
    ]

    asyncio.run(dedup.merge_duplicate_facts(facts))

    # Nothing was lexically close, so no model call was made.
    assert called["n"] == 0
