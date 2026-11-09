"""LinkedIn's jobs index must be readable, and only for the company being briefed.

Three defects, all measured on the live path:

1. **Every result was refused before its role was read.** `linkedin.com` is on the
   refused-host list — correctly, a social post is a claim about a claim — but the
   refusal ran before the careers-URL test, so the LinkedIn lane returned zero
   usable results on every run.
2. **Its index is an `ItemList`, not a `JobPosting`.** The reader only looked for
   `JobPosting`, so a page carrying 60 roles parsed as zero.
3. **The index is padded with other employers.** A page for "Canada Goose jobs"
   listed Callaway Golf, Acushnet and Cole Haan; only 3 of 60 entries were ours.
   LinkedIn names the employer in `disambiguatingDescription` and puts the
   *location* in `description` — reading `description` as the employer attributed
   Callaway's roles to Canada Goose.
"""
from __future__ import annotations

import json

import pytest

from app.services.job_postings import (
    _item_list_employer,
    _item_list_location,
    postings_from_html,
)
from app.services.publisher_filter import filter_results_for_company

LINKEDIN_JOBS = "https://www.linkedin.com/jobs/canada-goose-jobs"


def _page(entries: list[dict]) -> str:
    payload = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": entries,
    }
    return (
        '<html><head><script type="application/ld+json">'
        + json.dumps(payload)
        + "</script></head><body></body></html>"
    )


def _entry(title: str, employer: str, place: str, position: int = 1) -> dict:
    return {
        "@type": "ListItem",
        "position": position,
        "url": f"https://www.linkedin.com/jobs/view/{title.lower().replace(' ', '-')}",
        "name": title,
        "disambiguatingDescription": employer,
        "description": place,
    }


# --- the refusal must not swallow the jobs page --------------------------

def test_the_jobs_page_survives_the_refused_host_list():
    # The regression: linkedin.com was refused before the careers test ran.
    results = [
        {"url": LINKEDIN_JOBS, "title": "Canada Goose jobs", "raw_content": "x"}
    ]

    assert filter_results_for_company(results, "Canada Goose"), (
        "the LinkedIn jobs page must reach the hiring lane"
    )


def test_a_linkedin_post_is_still_refused():
    # Only the jobs section is a job board; a feed post is still not a source.
    results = [
        {"url": "https://www.linkedin.com/posts/x", "title": "Canada Goose", "raw_content": "x"}
    ]

    assert filter_results_for_company(results, "Canada Goose") == []


# --- the index is an ItemList -------------------------------------------

def test_an_item_list_is_read():
    page = _page([_entry("Plant Manager", "Canada Goose", "Montreal, QC")])

    postings = postings_from_html(page)

    assert [p.title for p in postings] == ["Plant Manager"]


def test_another_employers_role_is_dropped():
    page = _page(
        [
            _entry("Human Resources Business Partner", "Callaway Golf Company", "Fort Worth, TX", 1),
            _entry("Plant Manager", "Canada Goose", "Montreal, QC", 2),
            _entry("Lead Compensation Partner", "Acushnet Company", "Fairhaven, MA", 3),
        ]
    )

    postings = postings_from_html(page, "Canada Goose")

    assert [p.title for p in postings] == ["Plant Manager"]


def test_the_employer_is_read_from_disambiguating_description():
    entry = _entry("Plant Manager", "Canada Goose", "Montreal, QC")

    assert _item_list_employer(entry) == "Canada Goose"


def test_the_location_is_not_mistaken_for_the_employer():
    # Reading `description` as the employer is what attributed Callaway's roles
    # to Canada Goose.
    entry = _entry("Plant Manager", "Canada Goose", "Montreal, QC")

    assert _item_list_employer(entry) != "Montreal, QC"
    assert _item_list_location(entry) == "Montreal, QC"


def test_an_entry_naming_no_employer_is_dropped_when_one_was_asked_for():
    entry = _entry("Analyst", "", "Toronto, ON")

    postings = postings_from_html(_page([entry]), "Canada Goose")

    assert postings == []


def test_a_full_name_employer_matches_the_short_name():
    # LinkedIn writes "Canada Goose"; a caller may write the registered form.
    entry = _entry("Plant Manager", "Canada Goose Holdings Inc.", "Montreal, QC")

    postings = postings_from_html(_page([entry]), "Canada Goose")

    assert [p.title for p in postings] == ["Plant Manager"]


def test_job_posting_blocks_are_still_read():
    # The ItemList path must not displace the JobPosting path.
    page = (
        '<html><head><script type="application/ld+json">'
        + json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "QC In Line Inspector",
                "description": "x" * 80,
                "jobLocation": {"address": {"addressLocality": "Winnipeg"}},
            }
        )
        + "</script></head></body></html>"
    )

    postings = postings_from_html(page)

    assert [p.title for p in postings] == ["QC In Line Inspector"]


def test_a_job_posting_for_another_employer_is_dropped():
    page = (
        '<html><head><script type="application/ld+json">'
        + json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "Front End Developer",
                "description": "x" * 80,
                "hiringOrganization": {"name": "Callaway Golf Company"},
            }
        )
        + "</script></body></html>"
    )

    assert postings_from_html(page, "Canada Goose") == []
