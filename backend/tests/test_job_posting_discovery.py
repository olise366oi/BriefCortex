"""Regression tests for careers-page detail discovery.

All cases are shapes measured on live boards, not invented: the old numeric-id
rule matched none of the shapes that actually carry schema.org JobPosting data.
"""
from __future__ import annotations

from app.services.job_postings import _detail_links, _is_posting_detail


def _page(*hrefs: str) -> str:
    anchors = "".join(f'<a href="{href}">role</a>' for href in hrefs)
    return f"<html><body>{anchors}</body></html>"


def test_lever_uuid_shape_is_a_posting():
    # Lever: /{org}/{uuid}. The old /jobs/<digits> rule matched none of these.
    assert _is_posting_detail(
        "https://jobs.lever.co/shieldai/ac75662f-e937-4870-8040-6dbe733d0a47"
    ) is True


def test_smartrecruiters_numeric_slug_shape_is_a_posting():
    assert _is_posting_detail(
        "https://jobs.smartrecruiters.com/WesternDigital/744000159757015-technician-3-eng"
    ) is True


def test_icims_numeric_id_with_locale_query_is_a_posting():
    # AMD's real detail shape, complete with the lang parameter.
    assert _is_posting_detail(
        "https://careers.amd.com/careers-home/jobs/92586?lang=en-us"
    ) is True


def test_slug_posting_under_a_jobs_segment_is_a_posting():
    assert _is_posting_detail(
        "/join-us/jobs/technology/austin/supply-chain-specialist/"
    ) is True


def test_a_multi_hyphen_path_outside_jobs_is_not_a_posting():
    # Measured misfire: this marketing page was fetched as if it were a posting.
    assert _is_posting_detail(
        "https://webinar.amd.com/meet-the-experts-hub/en/home"
    ) is False


def test_board_and_filter_pages_are_not_postings():
    for url in (
        "https://careers.amd.com/careers-home/jobs",
        "https://careers.amd.com/careers-home/jobs/locations",
        "https://careers.amd.com/careers-home/undefined/categories",
        "https://careers.amd.com/candidate-portal",
        "/jobs?page=1&categories=Engineering",
        "https://careers-amd.icims.com/jobs/login?loginOnly=1",
    ):
        assert _is_posting_detail(url) is False, url


def test_detail_links_excludes_navigation_and_filters():
    page = _page(
        "https://careers.amd.com/careers-home/jobs/92586?lang=en-us",
        "https://careers.amd.com/careers-home/jobs/locations",
        "https://careers.amd.com/careers-home/undefined/categories",
        "https://careers.amd.com/candidate-portal",
    )

    links = _detail_links(page, "https://careers.amd.com/careers-home/jobs", 10)

    assert links == ["https://careers.amd.com/careers-home/jobs/92586?lang=en-us"]


def test_detail_links_keeps_absolute_links_on_another_host():
    # Greenhouse-style aggregator pages list roles that live on the employer's
    # own site; those are the real postings and must not be dropped.
    page = _page(
        "https://www.optiver.com/join-us/jobs/technology/austin/supply-chain-specialist/",
    )

    links = _detail_links(page, "https://boards.greenhouse.io/optiverus", 10)

    assert links == [
        "https://www.optiver.com/join-us/jobs/technology/austin/supply-chain-specialist/"
    ]


def test_relative_links_resolve_against_the_page_origin():
    page = _page("/careers/jobs/92586-senior-engineer")

    links = _detail_links(page, "https://example.com/careers", 10)

    assert links == ["https://example.com/careers/jobs/92586-senior-engineer"]
