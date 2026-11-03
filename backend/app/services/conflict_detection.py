from __future__ import annotations

import re
from functools import lru_cache

from .ingestion import FactBase


CONFLICT_RULES: list[tuple[str, list[str], list[str]]] = [
    (
        "headcount_up_down",
        [
            "layoff",
            "layoffs",
            "reduction in force",
            "rif",
            "cut jobs",
            "job cuts",
            "cut roles",
            "roles cut",
        ],
        [
            "hiring",
            "recruiting",
            "hiring spree",
            "expanding headcount",
            "open roles",
        ],
    ),
    (
        "cost_up_down",
        ["price hike", "price increase", "raising prices"],
        ["price cut", "price reduction", "discount"],
    ),
    (
        "expand_contract",
        ["expansion", "new plant", "new factory"],
        ["shutdown", "closing plant", "facility closure"],
    ),
    (
        "profit_loss",
        ["profitable", "profit growth"],
        ["loss", "net loss", "wider loss"],
    ),
]


# Keyword matching path. Compiled with lru_cache (approach Y): the rule table stays
# a plain str list so it stays readable and testable, and the pattern is built once
# at compile time. Keywords are pure ASCII, so each gets \b boundaries and "rif"
# cannot match inside "tariff".
@lru_cache(maxsize=None)
def _compile_keyword(keyword: str) -> re.Pattern[str]:
    lowered = keyword.lower()
    return re.compile(r"\b" + re.escape(lowered) + r"\b", re.IGNORECASE)


def _kw_match(text: str, keyword: str) -> bool:
    """Single matching entry point for the whole engine."""
    return _compile_keyword(keyword).search(text) is not None


def _match_pair(
    content_i: str,
    content_j: str,
    group_a: list[str],
    group_b: list[str],
) -> tuple[str, str] | None:
    """Return (kw_i, kw_j): the keyword fact i hit, then the keyword fact j hit."""
    for keyword_a in group_a:
        if not _kw_match(content_i, keyword_a):
            continue
        for keyword_b in group_b:
            if _kw_match(content_j, keyword_b):
                return keyword_a, keyword_b

    for keyword_b in group_b:
        if not _kw_match(content_i, keyword_b):
            continue
        for keyword_a in group_a:
            if _kw_match(content_j, keyword_a):
                return keyword_b, keyword_a

    return None


def detect_fact_conflicts(fact_base: FactBase) -> list[dict]:
    """Detect contradictory hard facts with a pure string rule engine.

    Only cross-item contradictions count: a single fact that mentions both
    directions (e.g. "cut jobs then rehired") is never a conflict.
    analyst_opinions are deliberately excluded - opinions are not facts.
    """
    facts = fact_base.hard_facts
    rows: list[tuple[int, int, str, str, str]] = []

    for i in range(len(facts)):
        for j in range(i + 1, len(facts)):
            content_i = facts[i].content
            content_j = facts[j].content

            for rule_name, group_a, group_b in CONFLICT_RULES:
                i_hits_a = any(_kw_match(content_i, k) for k in group_a)
                i_hits_b = any(_kw_match(content_i, k) for k in group_b)
                j_hits_a = any(_kw_match(content_j, k) for k in group_a)
                j_hits_b = any(_kw_match(content_j, k) for k in group_b)

                # A fact that states both sides itself is not a cross-item conflict.
                if (i_hits_a and i_hits_b) or (j_hits_a and j_hits_b):
                    continue

                matched = _match_pair(content_i, content_j, group_a, group_b)
                if matched is None:
                    continue

                kw_i, kw_j = matched
                rows.append((i, j, rule_name, kw_i, kw_j))

    rows.sort(key=lambda row: (row[0], row[1], row[2]))

    conflicts: list[dict] = []
    for i, j, rule_name, kw_i, kw_j in rows:
        conflicts.append(
            {
                "conflict_type": rule_name,
                "fact_a": facts[i].content,
                "source_a": str(facts[i].source_url),
                "fact_b": facts[j].content,
                "source_b": str(facts[j].source_url),
                "description": (
                    f"{rule_name}: fact A indicates '{kw_i}' "
                    f"while fact B indicates '{kw_j}'"
                ),
            }
        )

    return conflicts
