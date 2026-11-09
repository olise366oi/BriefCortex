"""The supplement round fires only on a genuinely empty opening round, once."""
from __future__ import annotations

import asyncio

import pytest

from app.services import ingestion
from app.services.search_planner import ReplanResult, SearchPlan


def _plan(queries=("Q1",), domains=("reuters.com",)) -> SearchPlan:
    return SearchPlan(
        industry="test",
        categories=["financial_wire"],
        domains=list(domains),
        queries=list(queries),
        fallback_queries=[],
        fundamentals=False,
        rationale="",
    )


def _run(coro):
    return asyncio.run(coro)


class _Tracker:
    def __init__(self) -> None:
        self.replan_calls = 0
        self.rounds: list[list[str]] = []


@pytest.fixture
def tracker(monkeypatch):
    t = _Tracker()

    async def _fake_replan(*_args, **_kwargs):
        t.replan_calls += 1
        return ReplanResult(queries=["WIDER Q"], reason="broaden", window_too_narrow=False)

    async def _fake_round(queries, *_args, **_kwargs):
        t.rounds.append(list(queries))
        return ([], 1, 1)  # one usable result

    monkeypatch.setattr(ingestion, "replan_after_empty", _fake_replan)
    monkeypatch.setattr(ingestion, "_run_round", _fake_round)
    return t


def _supplement(tracker, tried=("Q1",)):
    return _run(
        ingestion._supplement_empty_round(
            _plan(),
            "2026-10-17",
            "2026-11-09",
            "Test Co",
            [],
            set(),
            None,
            meeting_purpose="pricing",
            tried_queries=list(tried),
        )
    )


def test_it_proposes_different_queries_and_runs_one_round(tracker):
    added = _supplement(tracker)

    assert tracker.replan_calls == 1
    assert tracker.rounds == [["WIDER Q"]]
    assert added == 1


def test_a_narrow_window_does_not_run_a_round(tracker, monkeypatch):
    async def _narrow(*_a, **_k):
        tracker.replan_calls += 1
        return ReplanResult(queries=["WIDER Q"], window_too_narrow=True)

    monkeypatch.setattr(ingestion, "replan_after_empty", _narrow)

    added = _supplement(tracker)

    assert added == 0
    assert tracker.rounds == [], "a narrow window cannot be fixed by searching"


def test_no_proposed_queries_means_no_round(tracker, monkeypatch):
    async def _empty(*_a, **_k):
        tracker.replan_calls += 1
        return ReplanResult(queries=[], reason="private firm")

    monkeypatch.setattr(ingestion, "replan_after_empty", _empty)

    added = _supplement(tracker)

    assert added == 0
    assert tracker.rounds == []


def test_the_supplement_runs_on_the_open_web(tracker):
    # The reason a company is invisible to the financial press is usually that it
    # is covered elsewhere, so no domain restriction.
    _supplement(tracker)

    assert tracker.rounds, "a round should have run"
