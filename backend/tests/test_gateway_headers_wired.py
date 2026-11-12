"""The gateway cache headers must actually reach the outgoing request.

Bifrost caches only when a request carries `x-bf-cache-key`; with no key it
bypasses its own cache by design. The headers were computed on every run but
attached only on the dead single-stage path, so a deployment behind the gateway
never cached anything while the README said the TTL travelled per request.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone

from app.models.fact_base import FactBase, FactItem
from app.services import analysis as analysis_module


def _fact_base() -> FactBase:
    return FactBase(
        company_id="nio-abc",
        last_refresh_date=datetime.now(timezone.utc),
        hard_facts=[
            FactItem(
                content="NIO reported Q3 earnings and cut guidance.",
                source_url="https://x/1",
            )
        ],
        analyst_opinions=[],
    )


def _reply_for(prompt: str) -> str:
    """A reply shaped for whichever stage is being called."""
    if "executive_summary" in prompt:
        return (
            '{"executive_summary": {"summary": "s", "recommendation": "r", '
            '"confidence": "c"}}'
        )
    if '"analysis"' in prompt:
        return '{"analysis": {"assessment": "a", "risks": []}}'
    return '{"news": {"items": []}, "hires": {"items": []}}'


def test_the_headers_reach_the_provider_call(monkeypatch):
    """Every stage call must carry the cache key the gateway requires."""
    seen: list[dict] = []

    class _Completions:
        async def create(self, **kwargs):
            seen.append(dict(kwargs.get("extra_headers") or {}))

            system = kwargs["messages"][0]["content"]
            payload = _reply_for(system)

            class _Msg:
                content = payload

            class _Choice:
                message = _Msg()

            class _Resp:
                choices = [_Choice()]

            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(
        analysis_module, "build_client", lambda: (_Client(), "fake-model")
    )
    # The audit needs no model; keep it out of the way.
    async def _no_audit(report, *_args, **_kwargs):
        from app.services.report_audit import AuditOutcome

        return report, AuditOutcome()

    monkeypatch.setattr(analysis_module, "audit_report", _no_audit)

    asyncio.run(analysis_module.generate_briefing_report(_fact_base(), "pricing"))
    assert seen, "no provider call was made"
    for headers in seen:
        assert headers.get("x-bf-cache-key"), headers
        assert headers.get("x-bf-cache-ttl") == "24h", headers
        assert headers.get("x-bf-content-class") == "volatile", headers


def test_the_stage_caller_accepts_extra_headers():
    # A signature check, not a behaviour one: without this parameter the headers
    # cannot be sent at all, which is exactly how they stopped being sent.
    assert "extra_headers" in inspect.signature(
        analysis_module._call_stage_with_retry
    ).parameters


def test_the_cache_headers_are_built_for_the_evidence_set():
    headers = analysis_module.build_bifrost_headers(
        "nio-abc",
        "pricing",
        ["NIO reported Q3 earnings and cut guidance."],
    )

    assert set(headers) == {
        "x-bf-cache-key",
        "x-bf-cache-ttl",
        "x-bf-content-class",
    }
    assert headers["x-bf-content-class"] == "volatile"


def test_the_audit_also_carries_the_cache_headers(monkeypatch):
    """The audit is a model call too; without headers its verdict cannot replay.

    A replayed audit is also what keeps the audited findings identical, so the
    analysis and recommendation stages can replay as well.
    """
    from types import SimpleNamespace

    from app.services import report_audit

    seen: list[dict] = []

    class _Completions:
        async def create(self, **kwargs):
            seen.append(dict(kwargs.get("extra_headers") or {}))

            class _Msg:
                content = '{"verdicts": []}'

            class _Choice:
                message = _Msg()

            class _Resp:
                choices = [_Choice()]

            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(
        report_audit, "build_client", lambda: (_Client(), "fake-model")
    )

    headers = {
        "x-bf-cache-key": "key-1",
        "x-bf-cache-ttl": "24h",
        "x-bf-content-class": "volatile",
    }
    claim = SimpleNamespace(claim="NIO cut guidance.", source_url="https://x/1")

    asyncio.run(
        report_audit._audit_claims(
            [("news", 0, claim)],
            "NIO",
            "",
            "evidence",
            _fact_base(),
            extra_headers=headers,
        )
    )

    assert seen == [headers]
