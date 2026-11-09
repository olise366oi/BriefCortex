"""One replanned round when the opening search found nothing.

The opening round asks the planner what to search. When every lane comes back
empty there is nothing to brief from, and repeating the same queries returns the
same nothing — so the model is asked for a *different* angle, once.

Two properties matter and both are asserted here:
- it must not re-run a query that already returned nothing;
- an empty answer ("this company has no coverage") must be honoured, not
  overridden by inventing queries.
"""
from __future__ import annotations

import asyncio

from app.services import search_planner
from app.services.search_planner import ReplanResult, replan_after_empty


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


def _fake_client(reply: str, calls: list[dict]):
    class _Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return _Resp(reply)

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    return _Client()


def _run(coro):
    return asyncio.run(coro)


# --- the model's new queries are used ------------------------------------

def test_replanning_returns_the_models_new_queries(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        search_planner,
        "build_client",
        lambda: (
            _fake_client(
                '{"queries": ["YKK Corporation results guidance", '
                '"YKK legal name zipper holding company"], '
                '"reason": "the trade name returned nothing; try the legal name"}',
                calls,
            ),
            "fake-model",
        ),
    )

    result = _run(
        replan_after_empty(
            "YKK",
            "procurement: buy zipper components",
            tried_queries=["YKK zipper pricing pressure"],
            searched_domains=["reuters.com", "nikkei.com"],
        )
    )

    assert isinstance(result, ReplanResult)
    assert result.queries == [
        "YKK Corporation results guidance",
        "YKK legal name zipper holding company",
    ]
    assert "legal name" in result.reason


def test_a_query_already_run_is_not_proposed_again(monkeypatch):
    # Repeating a search that returned nothing returns nothing again.
    calls: list[dict] = []
    monkeypatch.setattr(
        search_planner,
        "build_client",
        lambda: (
            _fake_client(
                '{"queries": ["YKK zipper pricing pressure", '
                '"YKK zipper pricing pressure", "YKK annual report"], '
                '"reason": "broaden"}',
                calls,
            ),
            "fake-model",
        ),
    )

    result = _run(
        replan_after_empty(
            "YKK",
            "",
            tried_queries=["YKK zipper pricing pressure"],
            searched_domains=[],
        )
    )

    assert result.queries == ["YKK annual report"]


# --- an empty answer is an answer ----------------------------------------

def test_an_empty_answer_is_honoured(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        search_planner,
        "build_client",
        lambda: (
            _fake_client(
                '{"queries": [], "reason": "private firm with no press coverage", '
                '"window_too_narrow": false}',
                calls,
            ),
            "fake-model",
        ),
    )

    result = _run(
        replan_after_empty("Quiet Holdings", "", tried_queries=["Quiet Holdings"], searched_domains=[])
    )

    assert result.queries == []
    assert result.window_too_narrow is False
    assert "private firm" in result.reason


def test_a_narrow_window_is_reported_as_such(monkeypatch):
    # Nothing published inside the 30-day window cannot be fixed by a better
    # query, and saying so is the honest answer.
    calls: list[dict] = []
    monkeypatch.setattr(
        search_planner,
        "build_client",
        lambda: (
            _fake_client(
                '{"queries": [], "reason": "nothing published in the window", '
                '"window_too_narrow": true}',
                calls,
            ),
            "fake-model",
        ),
    )

    result = _run(
        replan_after_empty("Dormant Co", "", tried_queries=["Dormant Co"], searched_domains=[])
    )

    assert result.window_too_narrow is True


# --- failure must not break retrieval -----------------------------------

def test_an_unreachable_model_yields_no_queries(monkeypatch):
    def _boom():
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(search_planner, "build_client", _boom)

    result = _run(
        replan_after_empty("YKK", "pricing", tried_queries=["q"], searched_domains=[])
    )

    assert result.queries == []
    assert result.window_too_narrow is False


def test_an_unusable_reply_yields_no_queries(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        search_planner,
        "build_client",
        lambda: (_fake_client('not json at all', calls), "fake-model"),
    )

    result = _run(
        replan_after_empty("YKK", "", tried_queries=["q"], searched_domains=[])
    )

    assert result.queries == []


# --- the prompt carries the reason the first round failed ---------------

def test_the_prompt_is_told_what_was_already_tried(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        search_planner,
        "build_client",
        lambda: (
            _fake_client('{"queries": ["x"], "reason": "r"}', calls),
            "fake-model",
        ),
    )

    _run(
        replan_after_empty(
            "YKK",
            "buy zippers",
            tried_queries=["YKK zipper pricing"],
            searched_domains=["reuters.com", "nikkei.com"],
        )
    )

    user_message = calls[0]["messages"][1]["content"]
    assert "YKK zipper pricing" in user_message
    assert "reuters.com" in user_message
    assert "buy zippers" in user_message


def test_at_most_three_queries_are_kept(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        search_planner,
        "build_client",
        lambda: (
            _fake_client(
                '{"queries": ["a", "b", "c", "d", "e"], "reason": "r"}', calls
            ),
            "fake-model",
        ),
    )

    result = _run(
        replan_after_empty("YKK", "", tried_queries=[], searched_domains=[])
    )

    assert len(result.queries) == 3
