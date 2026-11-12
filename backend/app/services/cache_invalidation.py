# /backend/app/services/cache_invalidation.py
"""Invalidate a reusable brief when newer evidence contradicts what it assumed.

A cache keyed on the evidence is only safe if the evidence is the same. The
failure mode it cannot see on its own: the stored facts and the freshly
retrieved ones are both internally consistent, but they disagree with each
other. A brief written while the company was hiring is wrong the moment a layoff
is reported, even though every individual fact in it was correct when written.

The rule lives in `bifrost_cache_policy`, the standalone package this repository
publishes: on the gateway side the same check has to run without a briefing
pipeline, so it works on duck-typed items rather than `FactItem`. This module
keeps the briefing-shaped surface and the logging.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from bifrost_cache_policy import (
    CONFLICT_RULES,
    Conflict,
    detect_cross_set_conflicts as _detect_cross_set_conflicts,
    text_of,
)

from app.models.fact_base import FactBase, FactItem
from app.services.logger_service import get_logger

logger = get_logger("cache_invalidation")


@dataclass
class InvalidationVerdict:
    """Whether a stored brief may be reused, and why not."""

    reusable: bool = True
    conflicts: List[Dict[str, str]] = field(default_factory=list)

    @property
    def reason(self) -> str:
        if self.reusable:
            return "no cross-set contradiction"
        return f"{len(self.conflicts)} fact(s) overturn the stored reading"


def _as_items(raw: Sequence[dict]) -> List[FactItem]:
    """Turn stored evidence payloads back into fact items."""
    items: List[FactItem] = []
    for entry in raw:
        content = str(entry.get("content") or "")
        url = str(entry.get("source_url") or "")
        if not content or not url:
            continue
        try:
            items.append(FactItem(content=content, source_url=url))
        except Exception:
            continue
    return items


def _canonical(text: str) -> str:
    """Collapse whitespace and case, so formatting is not a new fact."""
    return " ".join((text or "").lower().split())


def detect_cross_set_conflicts(
    old_facts: Sequence[FactItem],
    new_facts: Sequence[FactItem],
) -> List[Dict[str, str]]:
    """Return contradictions where a new fact points the other way from an old one."""
    return _detect_cross_set_conflicts(old_facts, new_facts)


def _only_added_facts_contradict(
    old_facts: Sequence[FactItem],
    new_facts: Sequence[FactItem],
) -> List[Dict[str, str]]:
    """Conflicts introduced by facts that were not already stored.

    Running the cross-set check over two identical sets reports the set's own
    internal tension as a contradiction: measured on an unchanged Blue Star fact
    base, it "overturned" ten stored facts and regenerated the brief for nothing.
    Only a fact new since the stored brief can overturn it.
    """
    stored = {_canonical(item.content) for item in old_facts}
    added = [item for item in new_facts if _canonical(item.content) not in stored]
    if not added:
        logger.info(
            "Invalidation | no new facts since the stored brief | stored reading stands"
        )
        return []
    return detect_cross_set_conflicts(old_facts, added)


def check_reuse(
    stored_evidence: Dict | None,
    fact_base: FactBase,
) -> InvalidationVerdict:
    """Decide whether a stored brief survives contact with the new evidence.

    ``stored_evidence`` is the evidence payload saved beside the report. When it
    is missing the stored reading cannot be cross-checked, and the brief is
    regenerated rather than reused on trust: an unverifiable cache hit is exactly
    the case this exists to prevent.
    """
    if not stored_evidence:
        logger.info("Invalidation | storedEvidence=absent | cannot cross-check")
        return InvalidationVerdict(
            reusable=False,
            conflicts=[
                {
                    "conflict_type": "unknown",
                    "description": "stored evidence absent, cross-check impossible",
                }
            ],
        )

    raw_old = list(stored_evidence.get("hard_facts") or [])
    old_facts = _as_items(raw_old)
    if not old_facts:
        logger.info("Invalidation | storedFacts=0 | cannot cross-check")
        return InvalidationVerdict(reusable=False, conflicts=[])

    conflicts = _only_added_facts_contradict(old_facts, fact_base.hard_facts)

    if conflicts:
        logger.info(
            "Cache invalidated | stored=%s | new=%s | conflicts=%s",
            len(old_facts),
            len(fact_base.hard_facts),
            len(conflicts),
        )
        for conflict in conflicts[:5]:
            logger.info(
                "Overturned | type=%s | old=%s | new=%s",
                conflict["conflict_type"],
                conflict["old_fact"][:70],
                conflict["new_fact"][:70],
            )

    return InvalidationVerdict(reusable=not conflicts, conflicts=conflicts)


__all__ = [
    "CONFLICT_RULES",
    "Conflict",
    "InvalidationVerdict",
    "check_reuse",
    "detect_cross_set_conflicts",
    "text_of",
]
