"""Follow-up search rounds must not hold the brief for a marginal tail."""
from __future__ import annotations

import asyncio

from app.services import ingestion
from app.services.search_planner import SearchPlan


def _plan() -> SearchPlan:
    return SearchPlan(
        queries=["toro earnings"],
        domains=["reuters.com"],
        fallback_queries=[f"fallback {index}" for index in range(20)],
    )


def _patch_rounds(monkeypatch, usable: int, added: int, calls: list):
    async def fake_run_round(
        queries, domains, start, end, company, merged, seen, aliases, **kwargs
    ):
        calls.append(list(queries))
        return ([], usable, added)

    monkeypatch.setattr(ingestion, "_run_round", fake_run_round)


def test_a_rich_opening_round_caps_the_tail(monkeypatch):
    calls: list = []
    _patch_rounds(monkeypatch, usable=60, added=0, calls=calls)

    asyncio.run(ingestion._search_plan(_plan(), "2026-11-01", "2026-11-10", "Toro"))

    # The opening lane plus one follow-up: 60 usable is rich, and a round that
    # adds nothing below LOW_YIELD_NEW_URLS ends the loop.
    assert len(calls) == 2


def test_a_thin_opening_round_keeps_following_up(monkeypatch):
    calls: list = []
    _patch_rounds(monkeypatch, usable=8, added=1, calls=calls)
    plan = SearchPlan(
        queries=["toro earnings"],
        domains=["reuters.com"],
        fallback_queries=[f"fallback {index}" for index in range(60)],
    )

    asyncio.run(ingestion._search_plan(plan, "2026-11-01", "2026-11-10", "Toro"))

    # 8 usable is not a rich opening, so the round cap (MAX_SEARCH_ROUNDS) is the
    # bound: the opening lane plus seven follow-up rounds.
    assert len(calls) == ingestion.MAX_SEARCH_ROUNDS
