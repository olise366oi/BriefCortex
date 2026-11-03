# /backend/app/services/bifrost_headers.py
"""Content-aware cache headers derived from the evidence in the prompt.

The TTL is a property of the evidence, not of the request: a brief built from
same-day market material expires in a day, while one built from filings can be
kept for a week. Classification runs on the fact texts themselves, so an
ordinal such as "third quarter" can never be mistaken for a hiring signal.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, Iterable, List

# Each class names a distinct decay rate, ordered by precedence: a fact set is
# only as cacheable as its most perishable member.
FACT_CLASSES: tuple[str, ...] = ("volatile", "structural", "primary")

TTL_BY_CLASS: Dict[str, str] = {
    "volatile": "24h",
    "structural": "72h",
    "primary": "168h",
}

DEFAULT_CLASS = "volatile"

# Matched with word boundaries, so "shares" never fires on "shareholders".
VOLATILE_PATTERNS = (
    "announced",
    "announces",
    "breaking",
    "earnings",
    "guidance",
    "profit warning",
    "stock",
    "shares",
    "share price",
    "rally",
    "surge",
    "plunge",
    "tumble",
    "slump",
    "recall",
    "probe",
    "lawsuit",
)

STRUCTURAL_PATTERNS = (
    "hiring",
    "hires",
    "hired",
    "headcount",
    "appointed",
    "appointment",
    "appoints",
    "chief executive",
    "chief financial",
    "succession",
    "product launch",
    "launched",
    "launches",
)

PRIMARY_PATTERNS = (
    "annual report",
    "10-k",
    "10-q",
    "proxy statement",
    "press release",
    "regulatory filing",
    "segment revenue",
    "balance sheet",
)


def _compile_all(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(
        re.compile(r"\b" + re.escape(pattern) + r"(?:s|es)?\b", re.IGNORECASE)
        for pattern in patterns
    )


_COMPILED: Dict[str, tuple[re.Pattern[str], ...]] = {
    "volatile": _compile_all(VOLATILE_PATTERNS),
    "structural": _compile_all(STRUCTURAL_PATTERNS),
    "primary": _compile_all(PRIMARY_PATTERNS),
}


def _count_hits(texts: List[str], fact_class: str) -> int:
    return sum(
        1
        for text in texts
        for pattern in _COMPILED[fact_class]
        if pattern.search(text)
    )


def classify_fact_texts(texts: List[str]) -> str:
    """Return the most volatile class present in the fact texts."""
    for fact_class in FACT_CLASSES:
        if _count_hits(texts, fact_class):
            return fact_class
    return DEFAULT_CLASS


def classify_ttl(fact_class: str) -> str:
    """Map a class name to a cache TTL."""
    return TTL_BY_CLASS.get(fact_class.strip().lower(), TTL_BY_CLASS[DEFAULT_CLASS])


def evidence_digest(texts: Iterable[str]) -> str:
    """Return a short stable digest over the fact texts."""
    joined = "\n".join(texts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def build_bifrost_headers(
    company_id: str,
    meeting_purpose: str,
    fact_texts: List[str],
) -> Dict[str, str]:
    """Build the cache key and content-aware TTL for one briefing request.

    The key covers both halves of the request — the meeting objective and the
    evidence — so a different objective or a refreshed fact base produces a
    different key instead of a stale hit.
    """
    purpose_digest = hashlib.sha256(
        meeting_purpose.strip().encode("utf-8")
    ).hexdigest()[:12]
    cache_key = f"{company_id}_{purpose_digest}_{evidence_digest(fact_texts)}"
    fact_class = classify_fact_texts(fact_texts)

    return {
        "x-bf-cache-key": cache_key,
        "x-bf-cache-ttl": classify_ttl(fact_class),
        "x-bf-content-class": fact_class,
    }
