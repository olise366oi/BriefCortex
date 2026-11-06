# /backend/app/services/bifrost_headers.py
"""Content-aware cache headers derived from the evidence in the prompt.

The rule itself lives in `bifrost_cache_policy`, the standalone package this
repository publishes: a gateway extension has to be usable without importing a
briefing pipeline, so the decision is computed by code that knows nothing about
`FactBase`. This module is the briefing-shaped wrapper over it.
"""
from __future__ import annotations

from typing import Dict, List

from bifrost_cache_policy import build_cache_headers as _build_cache_headers

# Re-exported so the classifier's vocabulary stays inspectable from here; the
# precedence rule and the matching live in the package.
from bifrost_cache_policy.ttl import (  # noqa: F401
    CLASS_KEYWORDS,
    DEFAULT_CLASS,
    FACT_CLASSES,
    TTL_BY_CLASS,
    classify_fact_texts,
    classify_ttl,
)


def cache_partition(company_id: str) -> str:
    """Return the gateway partition for one company's briefs."""
    return company_id


def build_bifrost_headers(
    company_id: str,
    meeting_purpose: str,
    fact_texts: List[str],
    keywords: Dict[str, tuple[str, ...]] | None = None,
) -> Dict[str, str]:
    """Build the cache key and content-aware TTL for one briefing request.

    The key covers both halves of the request — the meeting objective and the
    evidence — so a different objective or a refreshed fact base produces a
    different key instead of a stale hit.
    """
    return _build_cache_headers(
        cache_partition(company_id),
        meeting_purpose,
        list(fact_texts),
        keywords,
    )


__all__ = [
    "build_bifrost_headers",
    "cache_partition",
    "classify_fact_texts",
    "classify_ttl",
    "FACT_CLASSES",
    "TTL_BY_CLASS",
    "CLASS_KEYWORDS",
    "DEFAULT_CLASS",
]
