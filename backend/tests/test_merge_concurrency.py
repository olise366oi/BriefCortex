"""The merge must reach the same grouping without a model round per fact.

Measured on a Wipro run (172 hard facts), the semantic merge took 335 seconds —
64% of the whole ingestion — because the old loop walked one anchor at a time,
asked the model about its partners, and re-asked about the remaining ones after
every accept. The pairs do not depend on merge order, so they are proposed once
across the whole set and confirmed in parallel; grouping is then a union-find.

Three properties have to survive that rewrite:

1. A transitive chain still collapses: A=B and B=C means A, B and C are one fact,
   even though A and C may never have been compared directly.
2. The fuller telling wins and every source survives.
3. Nothing is merged without the model's confirmation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.models.fact_base import FactItem
from app.services import dedup


def _fact(content: str, url: str) -> FactItem:
    return FactItem(
        content=content,
        source_url=url,
        publish_date=datetime(2026, 11, 3, tzinfo=timezone.utc),
    )


SHORT = "Wipro reported revenue of Rs 24,752.7 crore for the September quarter."
FULLER = (
    "Wipro reported revenue of Rs 24,752.7 crore for the September quarter, up "
    "9.1% from a year earlier, while net profit fell 1.9% to Rs 3,199.7 crore."
)
THIRD = "Wipro's September-quarter revenue came in at Rs 24,752.7 crore."


@pytest.fixture()
def model_confirms_everything(monkeypatch):
    """Stand in for the model, so the grouping logic is what gets exercised."""

    async def confirm(existing, incoming, pairs):
        return list(pairs)

    monkeypatch.setattr(dedup, "confirm_same_facts", confirm)


def test_a_transitive_chain_collapses_into_one_fact(model_confirms_everything):
    facts = [
        _fact(SHORT, "https://a.com/1"),
        _fact(FULLER, "https://b.com/2"),
        _fact(THIRD, "https://c.com/3"),
    ]

    merged, stats = asyncio.run(dedup.merge_duplicate_facts(facts))

    assert len(merged) == 1
    assert merged[0].content == FULLER, "the fuller telling wins"
    assert set(merged[0].all_sources()) == {
        "https://a.com/1",
        "https://b.com/2",
        "https://c.com/3",
    }
    assert stats["merged"] == 2


def test_the_model_is_asked_once_per_batch_not_once_per_fact(monkeypatch):
    """The regression: one confirmation round per anchor, serially."""
    rounds: list[int] = []

    async def counting_confirm(existing, incoming, pairs):
        rounds.append(len(pairs))
        return []

    monkeypatch.setattr(dedup, "confirm_same_facts", counting_confirm)

    facts = [_fact(f"Wipro reported item {i} of the quarter.", f"https://a.com/{i}")
             for i in range(30)]

    asyncio.run(dedup.merge_duplicate_facts(facts))

    # One call with every pair, not thirty anchor rounds.
    assert len(rounds) == 1, rounds


def test_unconfirmed_pairs_are_left_alone(monkeypatch):
    async def confirm_nothing(existing, incoming, pairs):
        return []

    monkeypatch.setattr(dedup, "confirm_same_facts", confirm_nothing)

    facts = [_fact(SHORT, "https://a.com/1"), _fact(FULLER, "https://b.com/2")]

    merged, stats = asyncio.run(dedup.merge_duplicate_facts(facts))

    assert len(merged) == 2
    assert stats["merged"] == 0


def test_no_candidates_means_no_model_call(monkeypatch):
    called = {"n": 0}

    async def counting_confirm(existing, incoming, pairs):
        called["n"] += 1
        return []

    monkeypatch.setattr(dedup, "confirm_same_facts", counting_confirm)

    facts = [
        _fact("Wipro cut jobs in Bengaluru.", "https://a.com/1"),
        _fact("A regulator fined an unrelated bank.", "https://b.com/2"),
    ]

    asyncio.run(dedup.merge_duplicate_facts(facts))

    assert called["n"] == 0


def test_a_single_fact_is_returned_untouched():
    facts = [_fact(SHORT, "https://a.com/1")]

    merged, stats = asyncio.run(dedup.merge_duplicate_facts(facts))

    assert merged == facts
    assert stats["considered"] == 1


# --- the global candidate pass -------------------------------------------


def test_global_candidates_only_pair_distinct_facts():
    facts = [SHORT, FULLER, "A regulator fined an unrelated bank in Germany."]

    pairs = dedup.find_candidates_global(facts)

    assert pairs, "the two tellings should pair"
    for pair in pairs:
        assert pair.existing_index != pair.incoming_index
        assert pair.similarity >= dedup.CANDIDATE_SIMILARITY


def test_global_candidates_bound_the_partners_each_fact_gets():
    # A page of near-identical syndicated sentences would otherwise propose
    # thousands of pairs, none of which the model needs to see twice.
    facts = [f"Wipro reported revenue of Rs 24,75{i}.7 crore for the quarter."
             for i in range(20)]

    pairs = dedup.find_candidates_global(facts)

    partners: dict[int, int] = {}
    for pair in pairs:
        partners[pair.existing_index] = partners.get(pair.existing_index, 0) + 1
        partners[pair.incoming_index] = partners.get(pair.incoming_index, 0) + 1

    assert all(count <= dedup.MAX_PARTNERS_PER_FACT for count in partners.values())


def test_global_candidates_are_ordered_strongest_first():
    facts = [SHORT, FULLER, "The regulator fined a bank."]

    pairs = dedup.find_candidates_global(facts)

    scores = [pair.similarity for pair in pairs]
    assert scores == sorted(scores, reverse=True)


# --- confirmation batches run concurrently --------------------------------


def test_confirmation_batches_are_issued_together():
    """Waiting for one batch before starting the next is pure latency."""
    in_flight = 0
    peak = 0

    class _Choice:
        def __init__(self, content: str) -> None:
            self.message = type("M", (), {"content": content})()

    class _Response:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        async def create(self, **_kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return _Response('{"pairs": []}')

    class _Client:
        chat = type("Chat", (), {"completions": _Completions()})()

    pairs = [
        dedup.CandidatePair(existing_index=0, incoming_index=index, similarity=0.9)
        for index in range(1, 40)
    ]

    original = dedup.build_client
    dedup.build_client = lambda: (_Client(), "test-model")
    try:
        asyncio.run(dedup.confirm_same_facts(["a"] * 40, ["b"] * 40, pairs))
    finally:
        dedup.build_client = original

    # Serial execution never has two calls in flight; concurrent execution does.
    assert peak > 1, f"batches ran one at a time (peak in flight = {peak})"


def test_an_empty_pair_list_never_calls_the_model(monkeypatch):
    called = {"n": 0}

    async def counting(existing, incoming, pairs):
        called["n"] += 1
        return []

    monkeypatch.setattr(dedup, "build_client", lambda: (_ for _ in ()).throw(
        AssertionError("must not build a client")
    ))

    assert asyncio.run(dedup.confirm_same_facts(["a"], ["b"], [])) == []


def test_global_candidates_agree_with_the_public_similarity():
    """The candidate pass normalizes once; scores must not drift from ``similarity``."""
    facts = [SHORT, FULLER, THIRD, "A regulator fined an unrelated bank in Germany."]

    pairs = dedup.find_candidates_global(facts)

    assert pairs
    for pair in pairs:
        expected = dedup.similarity(
            facts[pair.existing_index], facts[pair.incoming_index]
        )
        assert pair.similarity == pytest.approx(expected)


def test_stored_facts_are_not_re_merged(monkeypatch):
    """A stored fact set is already merged; re-asking the model changes it.

    Measured: a later run with no new facts re-confirmed six stored pairs, so
    the merged set (and its evidence fingerprint) changed and the brief
    regenerated although nothing new had arrived.
    """

    async def boom(*_args, **_kwargs):
        raise AssertionError("stored facts must not be re-confirmed")

    monkeypatch.setattr(dedup, "confirm_same_facts", boom)

    facts = [
        _fact(SHORT, "https://a.com/1"),
        _fact(FULLER, "https://b.com/2"),
        _fact(THIRD, "https://c.com/3"),
    ]

    merged, stats = asyncio.run(
        dedup.merge_duplicate_facts(facts, protected=len(facts))
    )

    assert merged == facts
    assert stats["merged"] == 0
