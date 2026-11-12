"""Per-run token accounting.

The pipeline's cost is spread over many stages; without a per-run total the only
observable is the monthly bill. These tests pin the collector and the wrapper
that feeds it.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services import llm_client, token_usage


class _Usage:
    prompt_tokens = 120
    completion_tokens = 30
    prompt_cache_hit_tokens = 40


class _Response:
    usage = _Usage()


class _Inner:
    async def create(self, **_kwargs):
        return _Response()


def _client():
    raw = SimpleNamespace(chat=SimpleNamespace(completions=_Inner()))
    return llm_client._LimitedClient(raw)


def test_usage_is_summed_for_one_run():
    with token_usage.usage_run() as totals:
        token_usage.record(100, 20)
        token_usage.record(50, 5)

    assert totals == {
        "calls": 2,
        "prompt_tokens": 150,
        "completion_tokens": 25,
        "cached_prompt_tokens": 0,
    }


def test_record_outside_a_run_is_a_noop():
    token_usage.record(999, 999)  # must not raise or leak into a later run

    with token_usage.usage_run() as totals:
        pass

    assert totals == {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_prompt_tokens": 0,
    }


def test_the_client_wrapper_records_what_the_provider_reports():
    with token_usage.usage_run() as totals:
        asyncio.run(_client().chat.completions.create())

    assert totals == {
        "calls": 1,
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "cached_prompt_tokens": 40,
    }


def test_mechanical_calls_opt_out_of_reasoning():
    # deepseek-flash bills reasoning as completion; a label does not need it.
    assert llm_client.no_reasoning() == {"reasoning_effort": "none"}
