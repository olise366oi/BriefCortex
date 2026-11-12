"""Dedup verdicts are cached across runs.

A repeated run meets the same syndicated sentences. Asking the model again is the
same answer at full price, and a non-deterministic verdict changes the merged
fact set — and with it the evidence fingerprint that decides whether inference
runs at all.
"""
from __future__ import annotations

import asyncio

from app.services import dedup


def _pair() -> dedup.CandidatePair:
    return dedup.CandidatePair(existing_index=0, incoming_index=1, similarity=0.9)


class _Response:
    def __init__(self, content: str) -> None:
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _CountingClient:
    def __init__(self, same: bool) -> None:
        self.calls = 0
        self._payload = (
            '{"pairs": [{"index": 0, "same": %s, "reason": "test"}]}'
            % ("true" if same else "false")
        )
        self.chat = type("Chat", (), {"completions": self})()

    async def create(self, **_kwargs):
        self.calls += 1
        return _Response(self._payload)


def test_a_confirmed_pair_is_not_asked_again(monkeypatch):
    client = _CountingClient(same=True)
    monkeypatch.setattr(dedup, "build_client", lambda: (client, "test-model"))

    first = asyncio.run(dedup.confirm_same_facts(["a", "b"], ["a", "b"], [_pair()]))
    second = asyncio.run(dedup.confirm_same_facts(["a", "b"], ["a", "b"], [_pair()]))

    assert len(first) == 1
    assert len(second) == 1
    assert client.calls == 1


def test_a_rejected_pair_is_not_asked_again(monkeypatch):
    client = _CountingClient(same=False)
    monkeypatch.setattr(dedup, "build_client", lambda: (client, "test-model"))

    first = asyncio.run(dedup.confirm_same_facts(["a", "b"], ["a", "b"], [_pair()]))
    second = asyncio.run(dedup.confirm_same_facts(["a", "b"], ["a", "b"], [_pair()]))

    assert first == []
    assert second == []
    assert client.calls == 1


def test_the_pair_key_is_order_independent():
    assert dedup._pair_key("Alpha fact", "beta fact") == dedup._pair_key(
        "Beta  FACT", "alpha fact"
    )


def test_clearing_the_cache_forces_a_new_call(monkeypatch):
    client = _CountingClient(same=True)
    monkeypatch.setattr(dedup, "build_client", lambda: (client, "test-model"))

    asyncio.run(dedup.confirm_same_facts(["a", "b"], ["a", "b"], [_pair()]))
    dedup.clear_verdict_cache()
    asyncio.run(dedup.confirm_same_facts(["a", "b"], ["a", "b"], [_pair()]))

    assert client.calls == 2
