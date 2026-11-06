"""Invalidate a cached answer when newer evidence contradicts what it assumed.

A cache keyed on the evidence is only safe if the evidence is the same. The
failure mode it cannot see on its own: the stored facts and the freshly
retrieved ones are both internally consistent, but they disagree with each
other. A brief written while the company was hiring is wrong the moment a
layoff is reported, even though every individual fact in it was correct when
written.

The check is cross-item only: a single sentence that states both directions
("cut jobs in Q2 but resumed hiring in Q3") is a description of a turn, not a
contradiction with itself. It is regex-based and needs no model, so it runs on
the request path in microseconds.

Items are duck-typed: anything with a `.content` attribute, or a mapping with a
`content` key, or a bare string. The package therefore does not need to know
what an evidence object looks like in your application.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

from .matcher import matched_keyword

# Directions that cannot both be true at the same time. Each rule is a name and
# two keyword groups; a conflict is one item hitting group A while another hits
# group B.
CONFLICT_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "headcount_up_down",
        (
            "layoff",
            "reduction in force",
            "rif",
            "cut jobs",
            "job cuts",
            "cut roles",
            "roles cut",
        ),
        (
            "hiring",
            "recruiting",
            "hiring spree",
            "expanding headcount",
            "open roles",
        ),
    ),
    (
        "cost_up_down",
        ("price hike", "price increase", "raising prices"),
        ("price cut", "price reduction", "discount"),
    ),
    (
        "expand_contract",
        ("expansion", "new plant", "new factory"),
        ("shutdown", "closing plant", "facility closure"),
    ),
    (
        "profit_loss",
        ("profitable", "profit growth"),
        ("loss", "net loss", "wider loss"),
    ),
)


@dataclass(frozen=True)
class Conflict:
    """One cross-set contradiction, with both sides attributed."""

    conflict_type: str
    old_fact: str
    old_source: str
    new_fact: str
    new_source: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "conflict_type": self.conflict_type,
            "old_fact": self.old_fact[:200],
            "old_source": self.old_source,
            "new_fact": self.new_fact[:200],
            "new_source": self.new_source,
            "description": (
                f"{self.conflict_type}: the stored answer assumed one direction, "
                "newer evidence reports the opposite"
            ),
        }


def text_of(item: Any) -> str:
    """Return the text of an evidence item, whatever shape it arrived in."""
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping):
        return str(item.get("content") or "")
    return str(getattr(item, "content", "") or "")


def source_of(item: Any) -> str:
    """Return the source of an evidence item, or an empty string."""
    if isinstance(item, str):
        return ""
    if isinstance(item, Mapping):
        return str(item.get("source_url") or item.get("source") or "")
    return str(getattr(item, "source_url", "") or "")


def _hits(text: str, keywords: Sequence[str]) -> bool:
    return matched_keyword(text, tuple(keywords)) is not None


def detect_cross_set_conflicts(
    old_items: Sequence[Any],
    new_items: Sequence[Any],
    rules: Sequence[tuple[str, Sequence[str], Sequence[str]]] | None = None,
) -> List[Dict[str, str]]:
    """Return contradictions where a new item points the opposite way from an old one.

    Only cross-set pairs are compared, and identical texts are skipped: re-serving
    the same sentence is not new evidence.
    """
    rule_set = rules or CONFLICT_RULES
    conflicts: List[Dict[str, str]] = []
    new_texts = [(text_of(item), source_of(item)) for item in new_items]
    old_texts = [(text_of(item), source_of(item)) for item in old_items]

    for new_text, new_source in new_texts:
        if not new_text:
            continue
        for old_text, old_source in old_texts:
            if not old_text or new_text == old_text:
                continue

            for rule_name, group_a, group_b in rule_set:
                new_a = _hits(new_text, group_a)
                new_b = _hits(new_text, group_b)
                old_a = _hits(old_text, group_a)
                old_b = _hits(old_text, group_b)

                # A sentence stating both directions is not a contradiction.
                if (new_a and new_b) or (old_a and old_b):
                    continue

                if not ((new_a and old_b) or (new_b and old_a)):
                    continue

                conflicts.append(
                    Conflict(
                        conflict_type=rule_name,
                        old_fact=old_text,
                        old_source=old_source,
                        new_fact=new_text,
                        new_source=new_source,
                    ).as_dict()
                )

                # One overturning pair per new item is enough to invalidate.
                break

    return conflicts


@dataclass(frozen=True)
class Verdict:
    """Whether a stored answer may be reused, and why not."""

    reusable: bool = True
    conflicts: List[Dict[str, str]] = field(default_factory=list)

    @property
    def reason(self) -> str:
        if self.reusable:
            return "no cross-set contradiction"
        if self.conflicts:
            return f"{len(self.conflicts)} fact(s) overturn the stored reading"
        return "stored evidence could not be cross-checked"


def check_reuse(
    stored_evidence: Mapping | Sequence | None,
    new_items: Sequence[Any],
    rules: Sequence[tuple[str, Sequence[str], Sequence[str]]] | None = None,
) -> Verdict:
    """Decide whether a stored answer survives contact with the new evidence.

    ``stored_evidence`` is whatever was saved beside the answer; a mapping with
    a ``hard_facts`` key or a bare sequence both work. When it is absent the
    stored reading cannot be cross-checked, and the answer is regenerated rather
    than reused on trust: an unverifiable cache hit is exactly the case this
    exists to prevent.
    """
    if not stored_evidence:
        return Verdict(reusable=False)

    if isinstance(stored_evidence, Mapping):
        stored_items = list(
            stored_evidence.get("hard_facts")
            or stored_evidence.get("items")
            or []
        )
    else:
        stored_items = list(stored_evidence)

    if not stored_items:
        return Verdict(reusable=False)

    conflicts = detect_cross_set_conflicts(stored_items, new_items, rules)
    return Verdict(reusable=not conflicts, conflicts=conflicts)
