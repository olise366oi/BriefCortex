"""Tests for the two-stage semantic deduplication."""
from __future__ import annotations

from app.services.dedup import (
    CANDIDATE_SIMILARITY,
    CandidatePair,
    find_candidates,
    similarity,
)

# Two tellings of the same delivery figure, written differently.
SAME_FACT_A = "NIO delivered 34,246 vehicles in October 2026."
SAME_FACT_B = "The company delivered 34,246 vehicles in October."

# Same wording, different event: two separate rounds of job cuts.
DIFFERENT_FACT_A = "The company cut 400 jobs in March."
DIFFERENT_FACT_B = "The company cut 400 jobs in June."

# Same template, different subject.
DIFFERENT_SUBJECT_A = "NIO brand deliveries fell 15% in October."
DIFFERENT_SUBJECT_B = "ONVO brand deliveries fell 15% in October."


def test_identical_sentences_score_one():
    assert similarity(SAME_FACT_A, SAME_FACT_A) == 1.0


def test_punctuation_and_case_do_not_change_similarity():
    assert similarity("NIO delivered 34,246 vehicles.", "nio delivered 34246 vehicles") > 0.9


def test_unrelated_sentences_score_low():
    assert similarity("NIO cut jobs.", "The regulator fined a bank.") < 0.3


def test_empty_text_scores_zero():
    assert similarity("", "anything") == 0.0
    assert similarity("anything", "") == 0.0


def test_similar_wording_still_becomes_a_candidate():
    # The pair is lexically close but the model must still decide: the
    # requirement is that the cheap filter does not silently drop it.
    pairs = find_candidates([DIFFERENT_FACT_A], [DIFFERENT_FACT_B])
    assert pairs, "lexically close pairs must reach the model"


def test_candidates_respect_the_similarity_threshold():
    pairs = find_candidates(["NIO cut jobs."], ["The regulator fined a bank."])
    assert all(pair.similarity >= CANDIDATE_SIMILARITY for pair in pairs)


def test_candidates_are_ordered_strongest_first():
    existing = [
        "NIO delivered 34,246 vehicles in October 2026, a decline of 15 percent.",
        "NIO reported a net loss in the quarter.",
    ]
    incoming = ["NIO delivered 34,246 vehicles in October 2026."]

    pairs = find_candidates(existing, incoming)

    assert pairs[0].existing_index == 0
    assert pairs[0].similarity >= pairs[-1].similarity


def test_candidate_count_is_capped():
    existing = [f"NIO delivered 1,000 vehicles in month {i}." for i in range(40)]
    incoming = ["NIO delivered 1,000 vehicles in month 0."]

    pairs = find_candidates(existing, incoming, max_candidates=5)

    assert len(pairs) <= 5


def test_no_candidates_when_nothing_is_similar():
    assert find_candidates(["NIO cut jobs."], ["The regulator fined a bank."]) == []


def test_candidate_pair_carries_both_positions():
    pair = CandidatePair(existing_index=3, incoming_index=1, similarity=0.8)
    assert pair.existing_index == 3
    assert pair.incoming_index == 1
