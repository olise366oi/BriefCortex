"""Near-duplicate detection for the gateway cache, and promise separation.

`semantic_cache` is configured with `dimension: 1`, which cannot discriminate:
a one-dimensional embedding maps every prompt to a single number. This replaces
it with the token-overlap test that is calibrated below, and adds the promise
classifier that keeps stated intent out of the model's evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.fact_base import FactItem
from app.services.forward_looking import is_forward_looking, split_forward_looking
from app.services.gateway_plugin import (
    NEAR_DUPLICATE_THRESHOLD,
    decide_gateway_cache,
    is_near_duplicate,
    request_signature,
    similarity,
)


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


def test_the_signature_ignores_word_order():
    assert request_signature("NVIDIA pricing pressure") == request_signature(
        "pricing pressure NVIDIA"
    )


def test_the_signature_changes_when_a_word_changes():
    assert request_signature("NVIDIA pricing pressure") != request_signature(
        "NVIDIA margin pressure"
    )


def test_the_threshold_sits_between_the_measured_cases():
    # Same request scores >= 0.83, different cases <= 0.67.
    assert 0.67 <= NEAR_DUPLICATE_THRESHOLD <= 0.83


def test_the_cache_decision_carries_class_and_ttl():
    decision = decide_gateway_cache(
        "nio-x", "",
        ["Shares rallied 6% after the earnings beat.", "Guidance was raised."],
    )

    assert decision.content_class == "volatile"
    assert decision.ttl == "24h"
    assert decision.cache_key
    assert decision.headers["x-bf-content-class"] == "volatile"


# --- forward-looking separation ------------------------------------------


def _fact(text: str) -> FactItem:
    return FactItem(content=text, source_url="https://x/1",
                    publish_date=datetime.now(timezone.utc))


def test_a_target_is_a_promise():
    assert is_forward_looking("Management expects to reach a 40% margin by 2028.")


def test_a_plan_is_a_promise():
    assert is_forward_looking(
        "The company plans to double capacity over the next two years."
    )


def test_a_reported_result_is_not_a_promise():
    assert is_forward_looking("NVIDIA reported record revenue of $57 billion.") is False


def test_an_announcement_that_mentions_a_plan_is_still_an_event():
    # The event happened; the plan inside it is not the claim.
    assert is_forward_looking(
        "The company announced a plan to expand capacity in 2028."
    ) is False


def test_separation_moves_promises_out_of_the_evidence():
    events, promises = split_forward_looking(
        [
            _fact("NVIDIA reported record revenue."),
            _fact("We will reach $10 trillion market capitalisation by 2030."),
        ]
    )

    assert len(events) == 1
    assert len(promises) == 1
    assert "record revenue" in events[0].content
    assert "market capitalisation" in promises[0].content
