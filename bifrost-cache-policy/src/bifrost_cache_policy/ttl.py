"""Cache TTL as a property of the evidence, not of the request.

A brief built from same-day market material is worthless tomorrow; one built
from a filing is still true next week. Deciding this per request keeps the
gateway's TTL correct without a model call, which is the whole point: a
gateway decides reuse before inference happens, so the rule it applies has to
be cheap and deterministic.

Classification runs on the fact texts themselves through whole-word matching,
so an ordinal such as "third quarter" can never be read as a hiring signal.
"""
from __future__ import annotations

from typing import Dict, Sequence

from .matcher import match_word

# Ordered by precedence: a fact set is only as cacheable as its most perishable
# member, so the first class with a hit wins.
FACT_CLASSES: tuple[str, ...] = ("volatile", "structural", "primary")

TTL_BY_CLASS: Dict[str, str] = {
    "volatile": "24h",
    "structural": "72h",
    "primary": "168h",
}

DEFAULT_CLASS = "volatile"

CLASS_KEYWORDS: Dict[str, tuple[str, ...]] = {
    "volatile": (
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
    ),
    "structural": (
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
    ),
    "primary": (
        "annual report",
        "10-k",
        "10-q",
        "proxy statement",
        "press release",
        "regulatory filing",
        "segment revenue",
        "balance sheet",
    ),
}


def classify_fact_texts(
    texts: Sequence[str],
    keywords: Dict[str, tuple[str, ...]] | None = None,
) -> str:
    """Return the most volatile class present in the fact texts.

    ``keywords`` lets a deployment widen the vocabulary without forking the
    rule: everything else — precedence, matching, TTL mapping — stays shared.
    """
    vocabulary = keywords or CLASS_KEYWORDS
    for fact_class in FACT_CLASSES:
        candidates = vocabulary.get(fact_class, ())
        if any(match_word(text, keyword) for text in texts for keyword in candidates):
            return fact_class
    return DEFAULT_CLASS


def classify_ttl(fact_class: str, ttl_by_class: Dict[str, str] | None = None) -> str:
    """Map a class name to a cache TTL."""
    table = ttl_by_class or TTL_BY_CLASS
    return table.get(fact_class.strip().lower(), table.get(DEFAULT_CLASS, "24h"))
