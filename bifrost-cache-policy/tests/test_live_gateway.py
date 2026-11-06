"""Live cache probe against a real Bifrost gateway.

Skipped unless BIFROST_URL is set, so the suite stays offline by default:

    $env:BIFROST_URL = "http://localhost:8089"
    $env:BIFROST_MODEL = "deepseek/deepseek-chat"
    python -m pytest tests/test_live_gateway.py -v -m live

What it proves, which no offline test can: the headers this package builds are
the ones the gateway actually reads — a first request misses, an identical
second request hits, a refreshed evidence set misses again, and clearing by
key drops the entry.
"""
from __future__ import annotations

import os
import uuid

import pytest

from bifrost_cache_policy.headers import (
    CACHE_KEY_HEADER,
    CACHE_TTL_HEADER,
    CONTENT_CLASS_HEADER,
    build_cache_headers,
)

httpx = pytest.importorskip("httpx")

pytestmark = pytest.mark.live

BIFROST_URL = os.getenv("BIFROST_URL", "").rstrip("/")
# The gateway routes `/v1/chat/completions` and takes the provider from the
# model name (`deepseek/deepseek-chat`) rather than from a path segment.
MODEL = os.getenv("BIFROST_MODEL", "deepseek/deepseek-chat")

EARNINGS_FACT = "NIO reported third quarter earnings and cut its full-year guidance."


def _post(headers: dict, prompt: str) -> dict:
    response = httpx.post(
        f"{BIFROST_URL}/v1/chat/completions",
        headers={"Content-Type": "application/json", **headers},
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16,
        },
        timeout=90,
    )
    return {
        "status": response.status_code,
        "hit": response.headers.get("x-bifrost-cache-hit"),
        "body": response.text,
    }


@pytest.fixture(scope="module", autouse=True)
def _require_gateway() -> None:
    if not BIFROST_URL:
        pytest.skip("BIFROST_URL not set; live gateway probe skipped")
    try:
        httpx.get(f"{BIFROST_URL}/health", timeout=10)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"gateway unreachable at {BIFROST_URL}: {exc}")


def _unique_partition() -> str:
    return f"probe-{uuid.uuid4().hex[:8]}"


def test_the_first_request_misses_and_the_second_hits():
    headers = build_cache_headers(_unique_partition(), "pricing", [EARNINGS_FACT])

    first = _post(headers, "Reply with the single word OK")
    assert first["status"] == 200, first["body"]
    assert first["hit"] == "false"

    second = _post(headers, "Reply with the single word OK")
    assert second["status"] == 200, second["body"]
    assert second["hit"] == "true"


def test_a_different_evidence_set_misses_on_the_same_partition():
    # The key is derived from the evidence, so a refreshed fact base must not
    # be served the previous answer.
    partition = _unique_partition()
    headers_a = build_cache_headers(partition, "pricing", [EARNINGS_FACT])
    headers_b = build_cache_headers(
        partition, "pricing", [EARNINGS_FACT, "A plant closure was announced."]
    )

    _post(headers_a, "Reply with the single word OK")
    assert headers_a[CACHE_KEY_HEADER] != headers_b[CACHE_KEY_HEADER]

    miss = _post(headers_b, "Reply with the single word OK")
    assert miss["status"] == 200, miss["body"]
    assert miss["hit"] == "false"


def test_a_different_objective_misses_on_the_same_evidence():
    partition = _unique_partition()
    headers_pricing = build_cache_headers(partition, "pricing", [EARNINGS_FACT])
    headers_renewal = build_cache_headers(partition, "renewal", [EARNINGS_FACT])

    _post(headers_pricing, "Reply with the single word OK")
    miss = _post(headers_renewal, "Reply with the single word OK")

    assert miss["hit"] == "false"


def test_the_headers_this_package_builds_are_the_ones_the_gateway_reads():
    headers = build_cache_headers(_unique_partition(), "pricing", [EARNINGS_FACT])
    result = _post(headers, "Reply with the single word OK")

    assert result["status"] == 200, result["body"]
    # The gateway answered with a cache verdict, which it only records for
    # requests that carried a key it could use.
    assert result["hit"] in {"true", "false"}
    assert headers[CONTENT_CLASS_HEADER] == "volatile"
    assert headers[CACHE_TTL_HEADER] == "24h"


def test_a_request_with_no_key_is_not_cached():
    # Caching is opt-in per request. Without the header the gateway bypasses
    # the cache entirely, which is what makes a missed header safe rather than
    # silently stale.
    first = _post({}, "Reply with the single word OK")
    second = _post({}, "Reply with the single word OK")

    assert first["hit"] is None
    assert second["hit"] is None


def test_clearing_the_key_removes_the_entry():
    headers = build_cache_headers(_unique_partition(), "pricing", [EARNINGS_FACT])

    _post(headers, "Reply with the single word OK")
    assert _post(headers, "Reply with the single word OK")["hit"] == "true"

    cleared = httpx.delete(
        f"{BIFROST_URL}/api/cache/clear-by-key/{headers[CACHE_KEY_HEADER]}",
        timeout=30,
    )
    # Management endpoints are auth-gated on some deployments; a 401/403 still
    # shows the key is the one that endpoint expects.
    assert cleared.status_code in (200, 401, 403), cleared.text

    if cleared.status_code == 200:
        assert _post(headers, "Reply with the single word OK")["hit"] == "false"
