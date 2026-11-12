"""The process-wide model-call cap.

Per-stage bounds (dedup 8, subject 6, date recovery 16) are per stage: once the
stages run concurrently their peaks add up and the provider sees the sum. This is
the only cap that sees every caller.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services import llm_client


class _Inner:
    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0

    async def create(self, **_kwargs):
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        return object()


def _wrap(inner):
    raw = SimpleNamespace(chat=SimpleNamespace(completions=inner))
    return llm_client._LimitedClient(raw)


def test_total_in_flight_calls_are_capped(monkeypatch):
    monkeypatch.setattr(llm_client, "MAX_CONCURRENT_LLM_CALLS", 3)
    llm_client._LIMITERS.clear()
    inner = _Inner()
    client = _wrap(inner)

    async def run() -> None:
        await asyncio.gather(
            *[client.chat.completions.create() for _ in range(10)]
        )

    asyncio.run(run())

    assert inner.peak == 3


def test_a_failed_call_releases_its_slot(monkeypatch):
    monkeypatch.setattr(llm_client, "MAX_CONCURRENT_LLM_CALLS", 1)
    llm_client._LIMITERS.clear()

    class _Failing:
        async def create(self, **_kwargs):
            raise RuntimeError("boom")

    client = _wrap(_Failing())

    async def run() -> None:
        results = await asyncio.gather(
            client.chat.completions.create(),
            client.chat.completions.create(),
            return_exceptions=True,
        )
        assert all(isinstance(item, RuntimeError) for item in results)

    asyncio.run(run())


def test_the_cap_is_created_per_event_loop(monkeypatch):
    # Tests and CLI one-shots run several loops in one process; a limiter bound
    # to a previous loop would fail on the next one.
    monkeypatch.setattr(llm_client, "MAX_CONCURRENT_LLM_CALLS", 2)
    llm_client._LIMITERS.clear()
    inner = _Inner()
    client = _wrap(inner)

    for _ in range(3):
        asyncio.run(client.chat.completions.create())

    assert inner.peak == 1
