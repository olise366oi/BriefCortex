"""A refused query is a cut-off run, not a company nobody writes about.

Measured on a YKK run: the Tavily key was over its plan quota, one local-language
query raised `ForbiddenError`, and `asyncio.gather` propagated it out of the
retrieval stage, so the whole brief failed while the other three lanes were
answering normally. Two separate defects were behind that:

1. One query against one lane is not the brief. Its failure has to degrade to an
   empty result for that query.
2. A quota or key refusal answers the same way on every attempt, so retrying it
   three times with backoff only spends the request's time budget.

And the coverage ledger has to keep "retrieval was blocked" apart from "no
reporting was found", because those two readings lead to opposite decisions.
"""
from __future__ import annotations

import asyncio

import pytest
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    UsageLimitExceededError,
)

from app.services import coverage as coverage_module
from app.services import ingestion
from app.services.coverage import build_coverage_report
from app.services.tavily_search import TavilyQuotaError, TavilySearchError, _is_transient


# --- which failures are worth another attempt -----------------------------


@pytest.mark.parametrize(
    "error",
    [
        UsageLimitExceededError("quota"),
        ForbiddenError("refused"),
        InvalidAPIKeyError("key"),
        BadRequestError("bad request"),
    ],
)
def test_a_refusal_is_not_retried(error):
    assert _is_transient(error) is False


def test_a_connection_failure_is_still_retried():
    # The retry policy exists for a dropped connection; removing it to fix the
    # quota case would lose the behaviour it was built for.
    assert _is_transient(ConnectionError("reset")) is True


def test_the_quota_error_is_a_search_error():
    # Callers catch TavilySearchError; a distinct type must not escape that.
    assert issubclass(TavilyQuotaError, TavilySearchError)


# --- one query failing does not fail the run ------------------------------


def test_a_failed_query_returns_nothing_and_records_the_failure(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise TavilyQuotaError("quota exhausted")

    monkeypatch.setattr(ingestion, "tavily_search", _refuse)
    failures: list[str] = []

    results = asyncio.run(
        ingestion._search_query(
            "YKK 値上げ", ["nikkei.com"], "2026-10-20", "2026-11-12", "YKK",
            None, True, failures,
        )
    )

    assert results == []
    assert len(failures) == 1
    assert "TavilyQuotaError" in failures[0]


def test_the_other_queries_in_a_round_still_return_results(monkeypatch):
    """The regression: `gather` let one rejected query fail the whole round."""
    def _search(query, *_args, **_kwargs):
        if "値上げ" in query:
            raise TavilyQuotaError("quota exhausted")
        return [{"url": "https://nikkei.com/a", "raw_content": "body"}]

    monkeypatch.setattr(ingestion, "tavily_search", _search)
    failures: list[str] = []

    kept, usable, _added = asyncio.run(
        ingestion._run_round(
            ["YKK zipper pricing", "YKK 値上げ"],
            ["nikkei.com"],
            "2026-10-20",
            "2026-11-12",
            "YKK",
            [],
            set(),
            None,
            failures=failures,
        )
    )

    assert usable == 1, "the surviving query's body must still count"
    assert [hit["url"] for hit in kept] == ["https://nikkei.com/a"]
    assert len(failures) == 1


# --- the ledger separates "blocked" from "empty" --------------------------


def test_a_refused_search_is_reported_as_cut_off():
    report = build_coverage_report(
        total_facts=0,
        distinct_sources=0,
        identity=None,
        has_hiring=False,
        has_legal=False,
        has_opinions=False,
        search_failures=["TavilyQuotaError: quota exhausted"],
    )

    assert "search" in report.lanes_missing
    assert report.search_failures
    assert any("cut off" in note for note in report.notes)
    # The one conclusion the ledger exists to prevent.
    assert not any("nothing was written about it" in note for note in report.notes)


def test_a_genuinely_empty_run_still_says_no_reporting_was_found():
    report = build_coverage_report(
        total_facts=0,
        distinct_sources=0,
        identity=None,
        has_hiring=False,
        has_legal=False,
        has_opinions=False,
    )

    assert not report.search_failures
    assert any("not about the company" in note for note in report.notes)


def test_the_default_keeps_the_field_empty():
    report = build_coverage_report(
        total_facts=6,
        distinct_sources=3,
        identity=None,
        has_hiring=True,
        has_legal=True,
        has_opinions=True,
    )

    assert report.search_failures == []
    assert "search" not in report.lanes_missing


def test_the_fact_base_carries_the_refusals_through():
    from app.models.fact_base import CoverageRecord

    record = CoverageRecord(search_failures=["TavilyQuotaError: quota exhausted"])
    assert record.search_failures


def test_the_dataclass_defaults_to_an_empty_list():
    # A mutable default shared across instances would leak one run's failures
    # into the next.
    first = coverage_module.EvidenceCoverageReport()
    second = coverage_module.EvidenceCoverageReport()
    first.search_failures.append("boom")

    assert second.search_failures == []
