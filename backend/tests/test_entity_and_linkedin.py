"""Tests for the same-name filter on possible facts, and LinkedIn hiring scope."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.fact_base import IncidentalFact
from app.services.entity_disambiguation import filter_by_entity
from app.services.hiring import (
    LINKEDIN_JOB_DOMAINS,
    is_hiring_line,
    linkedin_hiring_queries,
)

DESCRIPTION = (
    "AI personal assistant startup in Silicon Valley, users text or call it"
)


def _fact(text: str) -> IncidentalFact:
    return IncidentalFact(
        content=text,
        source_url="https://x/1",
        publish_date=datetime.now(timezone.utc),
    )


def test_an_amd_gpu_is_not_our_ai_assistant():
    # Measured: 20 "possible facts" for Instinct were mostly AMD's Instinct GPU
    # product line, which shares the word and nothing else.
    facts = [_fact("AMD Instinct MI300X GPUs power large-scale antibody screening.")]

    assert filter_by_entity(facts, DESCRIPTION, "Instinct") == []


def test_a_veterinary_product_is_not_our_ai_assistant():
    facts = [_fact("Instinct EMR is a cloud practice management system for clinics.")]

    assert filter_by_entity(facts, DESCRIPTION, "Instinct") == []


def test_a_genuine_mention_of_our_company_survives():
    facts = [
        _fact("Austin Welch said he texts Instinct to order groceries for him.")
    ]

    kept = filter_by_entity(facts, DESCRIPTION, "Instinct")

    assert len(kept) == 1


def test_without_a_description_the_other_meaning_check_still_applies():
    # The other-meaning check is about what a word means, not about the caller's
    # wording, so it runs even with no description at all.
    facts = [_fact("AMD Instinct MI300X GPUs power antibody screening.")]

    assert filter_by_entity(facts, "", "Instinct") == []


def test_without_a_description_an_ordinary_mention_is_kept():
    facts = [_fact("Customers said they now message Instinct instead of opening an app.")]

    assert len(filter_by_entity(facts, "", "Instinct")) == 1


def test_linkedin_queries_are_separate_from_general_hiring_queries():
    # They run against a fixed domain list, so sharing the general query list
    # would silently restrict every other query in it.
    queries = linkedin_hiring_queries("AMD")

    assert all("AMD" in query for query in queries)
    assert LINKEDIN_JOB_DOMAINS == ("linkedin.com/jobs",)


def test_a_role_title_alone_counts_as_hiring():
    # Measured: a LinkedIn jobs index publishes role titles as headings and
    # nothing else, so requiring a field label discarded every posting on it.
    assert is_hiring_line("Distributed Systems Software Engineer, Collectives")
    assert is_hiring_line("Sr Director Product Marketing – Data Center GPU")


def test_board_chrome_is_not_a_role():
    for line in (
        "# 7,000+ Amd Technical Project Manager Jobs in United States",
        "Sign in to set job alerts for “Director of Pricing” roles.",
        "Work in collaboration with other departments",
        "Experience in people management",
    ):
        assert is_hiring_line(line) is False, line


def test_an_appointment_is_news_not_a_posting():
    assert is_hiring_line("AMD appoints new chief financial officer") is False
