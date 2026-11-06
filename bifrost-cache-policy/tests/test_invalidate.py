"""Cross-set invalidation: newer evidence overturning a stored answer.

The fingerprint cannot see this on its own. The stored facts and the fresh ones
are each internally consistent and still disagree, so reuse has to be refused
rather than served. Everything here is regex, so it runs in microseconds and
needs no model.
"""
from __future__ import annotations

from dataclasses import dataclass

from bifrost_cache_policy.invalidate import (
    CONFLICT_RULES,
    check_reuse,
    detect_cross_set_conflicts,
    text_of,
)


@dataclass
class Item:
    """A stand-in for an application's own evidence object."""

    content: str
    source_url: str = "https://x/1"


def _mapping(text: str, url: str = "https://x/1") -> dict:
    return {"content": text, "source_url": url}


# --- conflict detection --------------------------------------------------


def test_a_layoff_overturns_a_hiring_assumption():
    old = [_mapping("The company is hiring across the engineering team.")]
    new = [_mapping("The company announced layoffs across engineering.", "https://x/2")]

    conflicts = detect_cross_set_conflicts(old, new)

    assert conflicts
    assert conflicts[0]["conflict_type"] == "headcount_up_down"


def test_both_sides_carry_their_source():
    old = [_mapping("The company is hiring.", "https://x/old")]
    new = [_mapping("The company announced layoffs.", "https://x/new")]

    conflict = detect_cross_set_conflicts(old, new)[0]

    assert conflict["old_source"] == "https://x/old"
    assert conflict["new_source"] == "https://x/new"


def test_agreeing_facts_do_not_conflict():
    old = [_mapping("The company is hiring across the engineering team.")]
    new = [_mapping("The company is recruiting more engineers.", "https://x/2")]

    assert detect_cross_set_conflicts(old, new) == []


def test_a_sentence_stating_both_directions_is_not_a_conflict():
    old = [_mapping("The company cut jobs in Q2 but resumed hiring in Q3.")]
    new = [_mapping("Hiring continued through the quarter.", "https://x/2")]

    assert detect_cross_set_conflicts(old, new) == []


def test_identical_text_is_not_a_conflict():
    old = [_mapping("The company is hiring.")]
    new = [_mapping("The company is hiring.")]

    assert detect_cross_set_conflicts(old, new) == []


def test_an_empty_new_set_cannot_contradict_anything():
    assert detect_cross_set_conflicts([_mapping("The company is hiring.")], []) == []


def test_every_rule_can_fire():
    cases = {
        "headcount_up_down": (
            "The company announced layoffs.",
            "The company is hiring.",
        ),
        "cost_up_down": (
            "The company announced a price hike.",
            "The company announced a price cut.",
        ),
        "expand_contract": (
            "The company opened a new plant.",
            "The company announced a facility closure.",
        ),
        "profit_loss": (
            "The company is profitable.",
            "The company reported a net loss.",
        ),
    }

    assert set(cases) == {name for name, _a, _b in CONFLICT_RULES}
    for rule_name, (old_text, new_text) in cases.items():
        conflicts = detect_cross_set_conflicts(
            [_mapping(old_text)], [_mapping(new_text, "https://x/2")]
        )
        assert conflicts, rule_name
        assert conflicts[0]["conflict_type"] == rule_name


def test_whole_word_matching_still_applies_across_sets():
    # "RIF" is a layoff signal; "Rife" is not.
    old = [_mapping("The company is hiring.")]
    new = [_mapping("Speculation was rife about the unit.", "https://x/2")]

    assert detect_cross_set_conflicts(old, new) == []


# --- duck typing ---------------------------------------------------------


def test_evidence_objects_work_without_being_mappings():
    # The package does not need to know what an evidence object looks like.
    old = [Item("The company is hiring.")]
    new = [Item("The company announced layoffs.", "https://x/2")]

    conflicts = detect_cross_set_conflicts(old, new)

    assert conflicts
    assert conflicts[0]["old_fact"] == "The company is hiring."
    assert conflicts[0]["new_source"] == "https://x/2"


def test_bare_strings_work_too():
    assert detect_cross_set_conflicts(["The company is hiring."], ["Layoffs announced."])


def test_text_of_handles_every_supported_shape():
    assert text_of("plain") == "plain"
    assert text_of({"content": "mapped"}) == "mapped"
    assert text_of(Item("attributed")) == "attributed"
    assert text_of(object()) == ""


def test_rules_can_be_replaced_by_the_caller():
    rules = (("supply_up_down", ("shortage",), ("glut",)),)

    conflicts = detect_cross_set_conflicts(
        [_mapping("A shortage of cells hit the market.")],
        [_mapping("A glut of cells hit the market.", "https://x/2")],
        rules,
    )

    assert conflicts
    assert conflicts[0]["conflict_type"] == "supply_up_down"


# --- reuse verdict -------------------------------------------------------


def test_a_matching_evidence_set_stays_reusable():
    facts = [_mapping("The company is hiring.")]
    verdict = check_reuse({"hard_facts": facts}, [Item("The company is hiring.")])

    assert verdict.reusable is True
    assert verdict.reason == "no cross-set contradiction"


def test_a_contradicting_evidence_set_is_not_reusable():
    old = [_mapping("The company is hiring.")]
    new = [Item("The company announced layoffs.", "https://x/2")]

    verdict = check_reuse({"hard_facts": old}, new)

    assert verdict.reusable is False
    assert "overturn" in verdict.reason


def test_missing_stored_evidence_is_not_reused_on_trust():
    # An unverifiable cache hit is exactly what this exists to prevent.
    verdict = check_reuse(None, [Item("The company is hiring.")])

    assert verdict.reusable is False
    assert verdict.conflicts == []


def test_empty_stored_evidence_is_not_reused_on_trust():
    verdict = check_reuse({"hard_facts": []}, [Item("The company is hiring.")])

    assert verdict.reusable is False


def test_a_bare_sequence_is_accepted_as_stored_evidence():
    verdict = check_reuse([_mapping("The company is hiring.")], [Item("The company is hiring.")])

    assert verdict.reusable is True
