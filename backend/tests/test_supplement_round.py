"""One replanned round when the opening search came back thin.

The opening round asks the planner what to search. When it returns fewer usable
bodies than `SUPPLEMENT_BELOW_USABLE`, that plan was not enough — repeating it
returns the same thin result, so the model is asked for a *different* angle, once.

The supplement searches the company's **national press list first**: a company
invisible to the international wire list is usually covered where it lives, and
an English query against Japanese titles returns the English edition of one paper
while a Japanese query against the same titles returns nine usable bodies.
"""
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
        self.rounds: list[tuple[list[str], list[str]]] = []


@pytest.fixture
def tracker(monkeypatch):
    t = _Tracker()

    async def _fake_replan(*_args, **_kwargs):
        t.replan_calls += 1
        return ReplanResult(queries=["WIDER Q"], reason="broaden", window_too_narrow=False)

    async def _fake_round(queries, domains, *_args, **_kwargs):
        t.rounds.append((list(queries), list(domains or [])))
        return ([], 1, 1)  # one usable result

    monkeypatch.setattr(ingestion, "replan_after_empty", _fake_replan)
    monkeypatch.setattr(ingestion, "_run_round", _fake_round)
    return t


def _supplement(tracker, tried=("Q1",), national=("nikkei.com", "jiji.com")):
    return _run(
        ingestion._supplement_thin_round(
            _plan(),
            "2026-10-17",
            "2026-11-09",
            "Test Co",
            [],
            set(),
            None,
            meeting_purpose="pricing",
            tried_queries=list(tried),
            national_domains=list(national),
        )
    )


# --- the round runs, and where it runs -----------------------------------

def test_it_proposes_different_queries_and_runs_one_round(tracker):
    added = _supplement(tracker)

    assert tracker.replan_calls == 1
    assert [q for q, _d in tracker.rounds] == [["WIDER Q"]]
    assert added == 1


def test_the_national_press_list_is_searched_first(tracker):
    # The reason a company is invisible to the wire list is usually that it is
    # covered where it lives.
    _supplement(tracker)

    assert tracker.rounds, "a round should have run"
    first_domains = tracker.rounds[0][1]
    assert "nikkei.com" in first_domains
    assert "jiji.com" in first_domains


def test_an_empty_national_list_falls_back_to_the_open_web(tracker, monkeypatch):
    async def _empty(requested, domains, *_args, **_kwargs):
        tracker.rounds.append((list(requested), list(domains or [])))
        return ([], 0, 0)  # nothing at all

    monkeypatch.setattr(ingestion, "_run_round", _empty)

    added = _supplement(tracker)

    assert added == 0
    # The first pass used the national list, the second had no domain restriction.
    assert tracker.rounds[0][1] == ["nikkei.com", "jiji.com"]
    assert tracker.rounds[1][1] == []


def test_a_refused_host_is_never_searched_in_the_supplement(tracker):
    _supplement(tracker, national=("nikkei.com", "reddit.com"))

    first_domains = tracker.rounds[0][1]
    assert "nikkei.com" in first_domains
    assert "reddit.com" not in first_domains


# --- the answer may be "no" ----------------------------------------------

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


# --- the threshold -------------------------------------------------------

def test_the_threshold_fires_below_six_usable_bodies():
    # Fewer than six sources cannot carry a conclusion; the coverage ledger already
    # calls that "partial", so a second angle is worth one more paid round.
    assert ingestion.SUPPLEMENT_BELOW_USABLE == 6
