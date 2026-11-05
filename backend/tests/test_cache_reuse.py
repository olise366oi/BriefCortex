"""Evidence-keyed reuse and its invalidation rule.

The old cache keyed on the request calendar, so a meeting one day later missed
even when the facts behind the brief were identical. The new key is the evidence,
which makes reuse possible — and makes cross-set contradiction the thing that has
to invalidate it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.fact_base import FactBase, FactItem
from app.services.cache_invalidation import check_reuse, detect_cross_set_conflicts
from app.services.evidence_fingerprint import PROMPT_VERSION, evidence_fingerprint


def _fact(text: str, url: str = "https://x/1") -> FactItem:
    return FactItem(content=text, source_url=url)


def _base(facts: list[FactItem]) -> FactBase:
    return FactBase(
        company_id="c",
        last_refresh_date=datetime.now(timezone.utc),
        hard_facts=facts,
        analyst_opinions=[],
    )


def _evidence(facts: list[FactItem]) -> dict:
    return {
        "hard_facts": [
            {"content": item.content, "source_url": str(item.source_url)}
            for item in facts
        ]
    }


# --- fingerprint ---------------------------------------------------------

def test_the_same_facts_produce_the_same_fingerprint():
    base = _base([_fact("Revenue rose 12%."), _fact("Margins held.")])

    assert evidence_fingerprint(base) == evidence_fingerprint(base)


def test_fact_order_does_not_change_the_fingerprint():
    one = _base([_fact("Revenue rose 12%."), _fact("Margins held.")])
    two = _base([_fact("Margins held."), _fact("Revenue rose 12%.")])

    assert evidence_fingerprint(one) == evidence_fingerprint(two)


def test_a_new_fact_changes_the_fingerprint():
    one = _base([_fact("Revenue rose 12%.")])
    two = _base([_fact("Revenue rose 12%."), _fact("A plant closed.")])

    assert evidence_fingerprint(one) != evidence_fingerprint(two)


def test_a_different_purpose_changes_the_fingerprint():
    base = _base([_fact("Revenue rose 12%.")])

    assert evidence_fingerprint(base, "pricing") != evidence_fingerprint(base, "")


def test_a_different_description_changes_the_fingerprint():
    base = _base([_fact("Revenue rose 12%.")])

    assert evidence_fingerprint(base, "", "SF startup") != evidence_fingerprint(
        base, "", "veterinary software"
    )


def test_the_fingerprint_carries_a_prompt_version():
    # A cached brief written by an older pipeline must not be served as current.
    assert PROMPT_VERSION


def test_whitespace_and_case_do_not_change_the_fingerprint():
    one = _base([_fact("Revenue  rose 12%.")])
    two = _base([_fact("revenue rose 12%.")])

    assert evidence_fingerprint(one) == evidence_fingerprint(two)


# --- cross-set conflict --------------------------------------------------

def test_a_layoff_overturns_a_hiring_assumption():
    old = [_fact("The company is hiring across the engineering team.")]
    new = [_fact("The company announced layoffs across engineering.", "https://x/2")]

    conflicts = detect_cross_set_conflicts(old, new)

    assert conflicts
    assert conflicts[0]["conflict_type"] == "headcount_up_down"


def test_agreeing_facts_do_not_conflict():
    old = [_fact("The company is hiring across the engineering team.")]
    new = [_fact("The company is recruiting more engineers.", "https://x/2")]

    assert detect_cross_set_conflicts(old, new) == []


def test_a_sentence_stating_both_directions_is_not_a_conflict():
    old = [_fact("The company cut jobs in Q2 but resumed hiring in Q3.")]
    new = [_fact("Hiring continued through the quarter.", "https://x/2")]

    assert detect_cross_set_conflicts(old, new) == []


def test_identical_text_is_not_a_conflict():
    old = [_fact("The company is hiring.")]
    new = [_fact("The company is hiring.")]

    assert detect_cross_set_conflicts(old, new) == []


# --- reuse verdict -------------------------------------------------------

def test_a_matching_evidence_set_stays_reusable():
    facts = [_fact("The company is hiring.")]
    verdict = check_reuse(_evidence(facts), _base(facts))

    assert verdict.reusable is True
    assert verdict.reason == "no cross-set contradiction"


def test_a_contradicting_evidence_set_is_not_reusable():
    old = [_fact("The company is hiring.")]
    new = [_fact("The company announced layoffs.", "https://x/2")]

    verdict = check_reuse(_evidence(old), _base(new))

    assert verdict.reusable is False
    assert "overturn" in verdict.reason


def test_missing_stored_evidence_is_not_reused_on_trust():
    # An unverifiable cache hit is exactly what this exists to prevent.
    verdict = check_reuse(None, _base([_fact("The company is hiring.")]))

    assert verdict.reusable is False
