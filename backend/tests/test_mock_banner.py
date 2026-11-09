"""The demo dataset must announce itself, and name who was not researched.

Measured: a brief for Anthropic and a brief for SpaceX came back with the identical
NIO paragraph, with no indication it was a sample. A demo report is legitimate;
a demo report that looks like a finding is not.
"""
from __future__ import annotations

from app.services.briefing_service import _load_mock_report


def test_the_sample_banner_names_the_company_that_was_not_researched():
    report = _load_mock_report("SpaceX")
    summary = report.executive_summary.summary

    assert summary.startswith("SAMPLE DATA")
    assert "SpaceX" in summary
    assert "NIO" in summary


def test_the_sample_recommendation_says_not_to_act_on_it():
    report = _load_mock_report("Anthropic")

    assert report.executive_summary.recommendation.startswith("SAMPLE DATA")
    assert "do not act on this recommendation" in (
        report.executive_summary.recommendation
    )


def test_the_sample_confidence_says_the_company_was_not_researched():
    confidence = _load_mock_report("Anthropic").executive_summary.confidence

    assert "Demo mode" in confidence
    assert "Anthropic" in confidence
    assert "was not researched" in confidence


def test_two_different_companies_no_longer_get_an_identical_brief():
    # The failure this guards: the bodies were byte-identical.
    one = _load_mock_report("Anthropic").executive_summary
    two = _load_mock_report("SpaceX").executive_summary

    assert one.summary != two.summary
    assert one.confidence != two.confidence


def test_the_sample_works_without_a_company_name():
    # An internal caller may load the sample directly; it must still be marked.
    report = _load_mock_report("")

    assert report.executive_summary.summary.startswith("SAMPLE DATA")
