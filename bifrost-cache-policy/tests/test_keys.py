"""Cache keys and near-duplicate signatures.

The key has to identify the evidence, not the run. Retrieval assembles facts in
whatever order its lanes finish, so a key that hashed the list as given would
differ on every run for identical evidence — a cache that never hits and gives
no reason why.
"""
from __future__ import annotations

from bifrost_cache_policy.keys import (
    evidence_digest,
    is_near_duplicate,
    objective_digest,
    request_signature,
    similarity,
)

EARNINGS_FACT = "NIO reported third quarter earnings and cut its full-year guidance."
HIRING_FACT = "NIO appointed a new chief financial officer this month."


# --- evidence digest ------------------------------------------------------


def test_the_same_facts_produce_the_same_digest():
    assert evidence_digest([EARNINGS_FACT, HIRING_FACT]) == evidence_digest(
        [EARNINGS_FACT, HIRING_FACT]
    )


def test_fact_order_does_not_change_the_digest():
    assert evidence_digest([EARNINGS_FACT, HIRING_FACT]) == evidence_digest(
        [HIRING_FACT, EARNINGS_FACT]
    )


def test_whitespace_and_case_do_not_change_the_digest():
    assert evidence_digest(["Revenue  rose 12%."]) == evidence_digest(
        ["revenue rose 12%."]
    )


def test_a_new_fact_changes_the_digest():
    assert evidence_digest([EARNINGS_FACT]) != evidence_digest(
        [EARNINGS_FACT, HIRING_FACT]
    )


def test_an_empty_evidence_set_still_produces_a_stable_digest():
    assert evidence_digest([]) == evidence_digest([])


# --- objective digest ----------------------------------------------------


def test_a_different_objective_changes_its_digest():
    assert objective_digest("pricing") != objective_digest("renewal")


def test_an_objective_digest_ignores_surrounding_whitespace():
    assert objective_digest("  pricing terms ") == objective_digest("pricing terms")


# --- near-duplicate detection --------------------------------------------


def test_a_reworded_request_is_a_near_duplicate():
    left = "NVIDIA brief for the supplier meeting: pricing pressure and margin"
    right = "NVIDIA briefing for supplier meeting about pricing pressure and margin"

    assert is_near_duplicate(left, right) is True


def test_word_order_does_not_change_the_verdict():
    left = "pre-meeting brief NVIDIA data center revenue guidance"
    right = "NVIDIA data center revenue guidance pre meeting brief"

    assert is_near_duplicate(left, right) is True


def test_a_different_objective_is_not_a_duplicate():
    left = "NVIDIA brief for the supplier meeting: reduce price by 15%"
    right = "NVIDIA brief for the supplier meeting: extend payment terms to 90 days"

    assert is_near_duplicate(left, right) is False


def test_a_different_company_is_not_a_duplicate():
    left = "NVIDIA brief for the supplier meeting on pricing"
    right = "Zydus Lifesciences brief for the supplier meeting on pricing"

    assert is_near_duplicate(left, right) is False


def test_unrelated_prompts_score_zero():
    assert similarity("summarise the attached article", "NVIDIA revenue guidance") == 0.0


def test_empty_prompts_are_not_duplicates():
    assert is_near_duplicate("", "") is False
    assert is_near_duplicate("NVIDIA pricing", "") is False


def test_the_threshold_can_be_overridden_per_call():
    left = "NVIDIA brief for the supplier meeting on pricing"
    right = "Zydus brief for the supplier meeting on pricing"

    # Different companies score well below the default bar, so a caller may only
    # share an answer by explicitly lowering it.
    assert is_near_duplicate(left, right) is False
    assert is_near_duplicate(left, right, threshold=0.5) is True


# --- request signature ---------------------------------------------------


def test_the_signature_ignores_word_order():
    assert request_signature("NVIDIA pricing pressure") == request_signature(
        "pricing pressure NVIDIA"
    )


def test_the_signature_changes_when_a_word_changes():
    assert request_signature("NVIDIA pricing pressure") != request_signature(
        "NVIDIA margin pressure"
    )


def test_the_signature_is_stable_across_punctuation():
    assert request_signature("NVIDIA pricing pressure.") == request_signature(
        "NVIDIA pricing pressure"
    )
