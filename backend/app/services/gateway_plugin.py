# /backend/app/services/gateway_plugin.py
"""The gateway cache decision this pipeline asks for, as one object.

The rules live in `bifrost_cache_policy`, the standalone package this repository
publishes, so a gateway-side consumer can apply them without importing a
briefing pipeline. This module keeps the briefing-shaped entry point: one
`GatewayDecision` per request, and a `FactBase` in place of raw text lists.

Bifrost's `semantic_cache` plugin, run in direct (hash) mode (`dimension: 1`,
no embedding provider), applies the key and the TTL this decision carries. The
near-duplicate test stays on the client side because an embedding at that
dimension cannot discriminate between prompts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Sequence

from bifrost_cache_policy import (
    NEAR_DUPLICATE_THRESHOLD,
    decide,
    is_near_duplicate as _is_near_duplicate,
    request_signature as _request_signature,
    similarity as _similarity,
)

from app.models.fact_base import FactBase
from app.services.bifrost_headers import cache_partition

# Re-exported so callers and tests keep one import site for the rule.
from bifrost_cache_policy.keys import STOPWORDS  # noqa: F401


def similarity(left: str, right: str) -> float:
    """Return a 0..1 overlap score between two prompts."""
    return _similarity(left, right)


def is_near_duplicate(
    left: str,
    right: str,
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> bool:
    """True when two prompts are close enough to share a cached response."""
    return _is_near_duplicate(left, right, threshold)


def request_signature(prompt: str) -> str:
    """Return a stable key for a prompt, normalised through its token set."""
    return _request_signature(prompt)


@dataclass(frozen=True)
class GatewayDecision:
    """What a gateway plugin needs to know about one request."""

    cache_key: str
    ttl: str
    content_class: str
    headers: Dict[str, str]


def decide_gateway_cache(
    company_id: str,
    meeting_purpose: str,
    fact_texts: Sequence[str],
) -> GatewayDecision:
    """Return the cache decision for one inference request."""
    decision = decide(cache_partition(company_id), meeting_purpose, list(fact_texts))
    return GatewayDecision(
        cache_key=decision.cache_key,
        ttl=decision.ttl,
        content_class=decision.content_class,
        headers=decision.headers,
    )


def decide_for_fact_base(
    fact_base: FactBase,
    meeting_purpose: str,
) -> GatewayDecision:
    """Return the cache decision for a request built from a fact base."""
    return decide_gateway_cache(
        fact_base.company_id,
        meeting_purpose,
        [item.content for item in fact_base.hard_facts],
    )


__all__ = [
    "GatewayDecision",
    "decide_gateway_cache",
    "decide_for_fact_base",
    "is_near_duplicate",
    "request_signature",
    "similarity",
    "NEAR_DUPLICATE_THRESHOLD",
    "STOPWORDS",
]
