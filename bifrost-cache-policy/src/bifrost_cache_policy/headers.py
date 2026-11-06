"""The request headers a Bifrost gateway reads, and one object that carries them.

Bifrost's `semantic_cache` plugin caches only when a request carries a cache
key, and it honours `x-bf-cache-ttl` as a per-request override of its configured
default. Both headers are therefore the whole integration surface: compute them
from the evidence before the call leaves your process, and the gateway does the
storing, serving and expiry.

The cache key covers both halves of the request — the objective and the
evidence — so a different objective, or a refreshed fact base, produces a
different key instead of a stale hit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Sequence

from .keys import canonical, evidence_digest, objective_digest
from .ttl import classify_fact_texts, classify_ttl

CACHE_KEY_HEADER = "x-bf-cache-key"
CACHE_TTL_HEADER = "x-bf-cache-ttl"
CONTENT_CLASS_HEADER = "x-bf-content-class"


def build_cache_key(
    partition: str,
    meeting_purpose: str,
    fact_texts: Iterable[str],
) -> str:
    """Return the cache partition for one request.

    ``partition`` is whatever scopes the cache in your application — a company
    id, a tenant, a feature name. Bifrost scopes entries per partition key, so
    two tenants with identical prompts never see each other's answers.
    """
    return (
        f"{canonical(partition).replace(' ', '_')}"
        f"_{objective_digest(meeting_purpose)}"
        f"_{evidence_digest(fact_texts)}"
    )


def build_cache_headers(
    partition: str,
    meeting_purpose: str,
    fact_texts: Sequence[str],
    keywords: Dict[str, tuple[str, ...]] | None = None,
) -> Dict[str, str]:
    """Build the cache key and content-aware TTL for one request."""
    texts = list(fact_texts)
    fact_class = classify_fact_texts(texts, keywords)
    return {
        CACHE_KEY_HEADER: build_cache_key(partition, meeting_purpose, texts),
        CACHE_TTL_HEADER: classify_ttl(fact_class),
        CONTENT_CLASS_HEADER: fact_class,
    }


@dataclass(frozen=True)
class CacheDecision:
    """What a caller needs to know about one request's cacheability."""

    cache_key: str
    ttl: str
    content_class: str
    headers: Dict[str, str]


def decide(
    partition: str,
    meeting_purpose: str,
    fact_texts: Sequence[str],
    keywords: Dict[str, tuple[str, ...]] | None = None,
) -> CacheDecision:
    """Return the cache decision for one inference request, as one object."""
    headers = build_cache_headers(partition, meeting_purpose, fact_texts, keywords)
    fact_class = headers[CONTENT_CLASS_HEADER]
    return CacheDecision(
        cache_key=headers[CACHE_KEY_HEADER],
        ttl=headers[CACHE_TTL_HEADER],
        content_class=fact_class,
        headers=headers,
    )


def cache_key_to_clear(decision: CacheDecision) -> str:
    """Return the value for `DELETE /api/cache/clear-by-key/{cacheKey}`.

    Invalidation on the gateway is per key, not per entry: `check_reuse` decides
    *whether* a stored answer may still be served, and this is how the stale
    entry is removed once it may not.
    """
    return decision.cache_key
