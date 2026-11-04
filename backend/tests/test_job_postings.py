"""Tests for job-postings extraction and identity disambiguation."""
from __future__ import annotations

import json

from app.services.company_identity import (
    AMBIGUITY_MARGIN,
    IdentityCandidate,
    is_ambiguous,
    needs_caller_choice,
)
from app.services.hiring import is_hiring_line
from app.services.job_postings import postings_from_html


def _page(*blocks: dict) -> str:
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(block)}</script>'
        for block in blocks
    )
    return f"<html><head>{scripts}</head><body>...</body></html>"


POSTING_BLOCK = {
    "@context": "http://schema.org",
    "@type": "JobPosting",
    "title": "2027 Undergrad Hardware Design Verification Engineering Intern/Co-Op",
    "description": (
        "<p>ADVANCE YOUR CAREER. Work on verification of next-generation "
        "hardware designs alongside experienced engineers.</p>"
    ),
    "jobLocation": {
        "@type": "Place",
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Austin",
            "addressRegion": "Texas",
            "addressCountry": "United States",
        },
    },
    "employmentType": "INTERN",
    "datePosted": "2026-10-24",
}


def test_a_posting_is_read_from_structured_data():
    postings = postings_from_html(_page(POSTING_BLOCK))

    assert len(postings) == 1
    assert postings[0].title.startswith("2027 Undergrad Hardware Design")
    assert postings[0].location == "Austin, Texas, United States"
    assert postings[0].employment_type == "INTERN"


def test_description_html_is_stripped():
    posting = postings_from_html(_page(POSTING_BLOCK))[0]

    assert "<p>" not in posting.description
    assert "ADVANCE YOUR CAREER" in posting.description


def test_rendered_line_carries_role_location_and_type():
    line = postings_from_html(_page(POSTING_BLOCK))[0].to_line()

    assert line.startswith("2027 Undergrad Hardware Design")
    assert "location: Austin, Texas, United States" in line
    assert "type: INTERN" in line


def test_a_page_without_postings_yields_nothing():
    # The shell of a JavaScript careers app: navigation only, no JobPosting.
    shell = _page({"@type": "WebSite", "url": "https://careers.example.com/jobs"})

    assert postings_from_html(shell) == []


def test_a_filler_employment_type_is_dropped():
    block = dict(POSTING_BLOCK, employmentType="UNAVAILABLE")

    posting = postings_from_html(_page(block))[0]

    assert posting.employment_type is None
    assert "type:" not in posting.to_line()


def test_postings_inside_a_graph_are_found():
    graph = {"@graph": [POSTING_BLOCK, {"@type": "WebSite"}]}

    assert len(postings_from_html(_page(graph))) == 1


def test_duplicate_titles_are_kept_once():
    page = _page(POSTING_BLOCK, POSTING_BLOCK)

    assert len(postings_from_html(page)) == 1


def test_malformed_block_does_not_break_extraction():
    page = (
        '<script type="application/ld+json">{not json}</script>'
        + _page(POSTING_BLOCK)
    )

    assert len(postings_from_html(page)) == 1


def test_short_description_is_dropped():
    block = dict(POSTING_BLOCK, description="Too short.")

    posting = postings_from_html(_page(block))[0]

    assert posting.description == ""


def _candidate(name: str, jurisdiction: str, confidence: float) -> IdentityCandidate:
    return IdentityCandidate(
        name=name,
        jurisdiction=jurisdiction,
        legal_form=None,
        status="ACTIVE",
        confidence=confidence,
    )


def test_a_clear_winner_is_not_ambiguous():
    candidates = [
        _candidate("ADVANCED MICRO DEVICES INC", "US", 1.0),
        _candidate("AMD", "FR", 0.4),
    ]

    assert is_ambiguous(candidates) is False


def test_near_tie_asks_the_caller_to_choose():
    # "AMD" really does match both a US chipmaker and an unrelated French entity,
    # so the caller must pick instead of receiving a guess.
    candidates = [
        _candidate("AMD", "FR", 0.9),
        _candidate("AMD", "US", 0.9 - AMBIGUITY_MARGIN / 2),
    ]

    assert is_ambiguous(candidates) is True


def test_a_single_candidate_is_not_ambiguous():
    assert is_ambiguous([_candidate("AMD", "FR", 1.0)]) is False


def test_no_candidates_is_not_ambiguous():
    assert is_ambiguous([]) is False


def test_a_short_name_with_one_strong_match_still_needs_a_choice():
    # "AMD" is registered exactly once in the registry, against an unrelated
    # French company. A perfect score on a three-letter token proves nothing
    # about which company the caller meant.
    candidates = [_candidate("AMD", "FR", 1.0)]

    assert needs_caller_choice("AMD", candidates) is True


def test_a_long_name_with_one_strong_match_is_settled():
    candidates = [_candidate("ZYDUS LIFESCENCES LIMITED", "IN", 1.0)]

    assert needs_caller_choice("Zydus Lifesciences", candidates) is False


def test_a_long_name_with_rival_strong_matches_is_settled_by_score():
    # Long names are specific enough that a wide margin decides it.
    candidates = [
        _candidate("ADVANCED MICRO DEVICES INC", "US", 1.0),
        _candidate("AMD FRUITS", "IN", 0.52),
    ]

    assert needs_caller_choice("Advanced Micro Devices", candidates) is False


def test_two_strong_matches_on_a_short_name_need_a_choice():
    candidates = [
        _candidate("AMD", "FR", 1.0),
        _candidate("AMD", "US", 1.0),
    ]

    assert needs_caller_choice("AMD", candidates) is True


def test_different_events_keywords_are_not_labour_statistics():
    # The macro release must not read as a company's hiring plan.
    for line in (
        "Tuesday brings September's Job Openings and Labor Turnover Survey",
        "US job openings rose unexpectedly, the Bureau of Labor Statistics said",
        "Jobless claims and nonfarm payrolls are released this week",
    ):
        assert is_hiring_line(line) is False, line
