"""Tests for the coverage ledger: what retrieval reached and what it did not."""
from __future__ import annotations

import pytest

from app.models.fact_base import CoverageRecord
from app.services.coverage import (
    COVERAGE_NONE,
    COVERAGE_PARTIAL,
    COVERAGE_STRONG,
    COVERAGE_THIN,
    MIN_FACTS_FOR_LEVERAGE,
    STANDARD_CHECKS,
    build_coverage_report,
)
from app.services.company_identity import CompanyIdentity


def _identity(confident: bool = True, jurisdiction: str | None = "IN"):
    return CompanyIdentity(
        name="ZYDUS LIFESCIENCES LIMITED",
        jurisdiction=jurisdiction,
        legal_form="DDKQ",
        status="ACTIVE",
        confident=confident,
    )


def test_empty_retrieval_is_reported_as_no_coverage():
    report = build_coverage_report(
        total_facts=0,
        distinct_sources=0,
        identity=None,
        has_hiring=False,
        has_legal=False,
        has_opinions=False,
    )

    assert report.level == COVERAGE_NONE
    assert report.can_support_leverage is False
    # The point of the note: silence about the company is not a clean bill.
    assert any("not about the company" in note for note in report.notes)


def test_empty_coverage_lists_every_standard_check():
    report = build_coverage_report(
        total_facts=0,
        distinct_sources=0,
        identity=None,
        has_hiring=False,
        has_legal=False,
        has_opinions=False,
    )

    assert report.checks_to_run == list(STANDARD_CHECKS)


def test_thin_coverage_cannot_support_leverage():
    report = build_coverage_report(
        total_facts=2,
        distinct_sources=2,
        identity=_identity(),
        has_hiring=True,
        has_legal=False,
        has_opinions=True,
    )

    assert report.level == COVERAGE_THIN
    assert report.can_support_leverage is False


def test_partial_coverage_below_the_leverage_floor():
    report = build_coverage_report(
        total_facts=MIN_FACTS_FOR_LEVERAGE - 1,
        distinct_sources=3,
        identity=_identity(),
        has_hiring=True,
        has_legal=True,
        has_opinions=True,
    )

    assert report.level == COVERAGE_PARTIAL
    assert report.can_support_leverage is False


def test_strong_coverage_supports_leverage_and_skips_the_checklist():
    report = build_coverage_report(
        total_facts=MIN_FACTS_FOR_LEVERAGE + 10,
        distinct_sources=6,
        identity=_identity(),
        has_hiring=True,
        has_legal=True,
        has_opinions=True,
    )

    assert report.level == COVERAGE_STRONG
    assert report.can_support_leverage is True
    assert report.checks_to_run == []


def test_single_source_is_flagged_as_uncorroborated():
    report = build_coverage_report(
        total_facts=MIN_FACTS_FOR_LEVERAGE,
        distinct_sources=1,
        identity=_identity(),
        has_hiring=True,
        has_legal=True,
        has_opinions=True,
    )

    assert any("corroborate" in note for note in report.notes)


def test_unresolved_identity_is_named_as_a_missing_lane():
    report = build_coverage_report(
        total_facts=10,
        distinct_sources=4,
        identity=None,
        has_hiring=True,
        has_legal=True,
        has_opinions=True,
    )

    assert "company identity" in report.lanes_missing
    assert any("no jurisdiction-specific source" in note for note in report.notes)


def test_unconfident_identity_is_reported_separately():
    report = build_coverage_report(
        total_facts=10,
        distinct_sources=4,
        identity=_identity(confident=False),
        has_hiring=True,
        has_legal=True,
        has_opinions=True,
    )

    assert "confirmed identity" in report.lanes_missing
    assert any("ambiguous" in note for note in report.notes)


def test_empty_lanes_are_named():
    report = build_coverage_report(
        total_facts=10,
        distinct_sources=4,
        identity=_identity(),
        has_hiring=False,
        has_legal=False,
        has_opinions=False,
    )

    assert "legal and regulatory" in report.lanes_missing
    assert "hiring" in report.lanes_missing
    assert "analyst commentary" in report.lanes_missing


def test_coverage_record_defaults_are_safe():
    record = CoverageRecord()

    assert record.level == "none"
    assert record.can_support_leverage is False
    assert record.checks_to_run == []
