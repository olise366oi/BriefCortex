# /backend/app/services/cache_invalidation.py
"""Invalidate a reusable brief when newer evidence contradicts what it assumed.

A cache keyed on the evidence is only safe if the evidence is the same. The failure
mode it cannot see on its own: the stored facts and the freshly retrieved ones are
both internally consistent, but they disagree with each other. A brief written while
the company was hiring is wrong the moment a layoff is reported, even though every
individual fact in it was correct when written.

So before a stored brief is reused, the old and new fact sets are checked against
each other with the same deterministic engine used everywhere else. A cross-set
contradiction invalidates the reuse and the brief is regenerated.

This is the application-side form of the gateway invalidation rule. The check is
regex-based and cross-item only, so it costs microseconds and needs no model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from app.models.fact_base import FactBase, FactItem
from app.services.conflict_detection import CONFLICT_RULES, _kw_match
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


def detect_cross_set_conflicts(
    old_facts: Sequence[FactItem],
    new_facts: Sequence[FactItem],
) -> List[Dict[str, str]]:
    """Return contradictions where a new fact points the other way from an old one.

    Only cross-set pairs are compared: a fact that states both directions itself
    is not a conflict, and that rule is applied identically here.
    """
    conflicts: List[Dict[str, str]] = []

    for new in new_facts:
        new_text = new.content
        for old in old_facts:
            old_text = old.content
            if new_text == old_text:
                continue

            for rule_name, group_a, group_b in CONFLICT_RULES:
                new_hits_a = any(_kw_match(new_text, k) for k in group_a)
                new_hits_b = any(_kw_match(new_text, k) for k in group_b)
                old_hits_a = any(_kw_match(old_text, k) for k in group_a)
                old_hits_b = any(_kw_match(old_text, k) for k in group_b)

                # A sentence stating both directions is not a contradiction.
                if (new_hits_a and new_hits_b) or (old_hits_a and old_hits_b):
                    continue

                if not ((new_hits_a and old_hits_b) or (new_hits_b and old_hits_a)):
                    continue

                conflicts.append(
                    {
                        "conflict_type": rule_name,
                        "old_fact": old_text[:200],
                        "old_source": str(old.source_url),
                        "new_fact": new_text[:200],
                        "new_source": str(new.source_url),
                        "description": (
                            f"{rule_name}: the stored brief assumed one direction, "
                            "newer evidence reports the opposite"
                        ),
                    }
                )

                # One overturning pair per new fact is enough to invalidate.
                break

    return conflicts


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

    conflicts = detect_cross_set_conflicts(old_facts, fact_base.hard_facts)

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
