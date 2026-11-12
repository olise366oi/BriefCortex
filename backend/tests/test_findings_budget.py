"""The findings stage reads a bounded slice of the evidence.

The stage returns at most eight claims; sending every collected fact is most of
the prompt and most of the cost. The cap keeps the newest facts and discloses the
ratio, so a truncated list is never mistaken for the whole record.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.fact_base import CoverageRecord, FactBase, FactItem
from app.services import report_stages


def _fact(index: int) -> FactItem:
    return FactItem(
        content=f"Wipro item {index} of the quarter.",
        source_url=f"https://example.com/{index}",
        publish_date=datetime(2026, 11, index + 1, tzinfo=timezone.utc),
    )


def _base(count: int) -> FactBase:
    return FactBase(
        company_id="company-1",
        last_refresh_date=datetime.now(timezone.utc),
        hard_facts=[_fact(index) for index in range(count)],
        analyst_opinions=[],
        coverage=CoverageRecord(level="thin", total_facts=count, distinct_sources=count),
    )


def test_over_budget_keeps_the_newest_and_discloses_the_ratio(monkeypatch):
    monkeypatch.setattr(report_stages, "MAX_FINDINGS_FACTS", 2)

    text = report_stages.render_findings_input(_base(3), "pricing", "Wipro")

    assert "facts shown: 2 of 3" in text
    assert "Wipro item 2" in text  # newest
    assert "Wipro item 1" in text
    assert "Wipro item 0" not in text  # oldest dropped


def test_under_budget_sends_every_fact():
    text = report_stages.render_findings_input(_base(3), "pricing", "Wipro")

    assert "facts shown: 3 of 3" in text
    assert "[H3]" in text


def test_a_zero_cap_disables_truncation(monkeypatch):
    monkeypatch.setattr(report_stages, "MAX_FINDINGS_FACTS", 0)

    text = report_stages.render_findings_input(_base(3), "pricing", "Wipro")

    assert "facts shown: 3 of 3" in text
    assert "Wipro item 0" in text
