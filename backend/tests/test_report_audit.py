"""Tests for the pre-delivery audit and the output shape sent to a reader.

Three failures reached a reader before this existed:
- a brief for one company whose every finding was about a different company;
- a "changes since the previous brief" block on a first brief, comparing with
  nothing;
- unconfirmed facts mined from other companies' articles presented as findings.

The audit removes claims rather than caveating them: a caveat still puts the
sentence in front of the reader.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.briefing import BriefingReport, Claim, ClaimModule
from app.models.fact_base import FactBase, FactItem, IncidentalFact
from app.services.report_audit import (
    SECTION_NAMES,
    AuditOutcome,
    _apply,
    _flatten,
    audit_report,
)


def _report(**overrides) -> BriefingReport:
    base = {
        "executive_summary": {
            "summary": "s",
            "recommendation": "r",
            "confidence": "c",
        },
    }
    base.update(overrides)
    return BriefingReport.model_validate(base)


def _fact_base(urls: list[str]) -> FactBase:
    return FactBase(
        company_id="c",
        last_refresh_date=datetime.now(timezone.utc),
        hard_facts=[
            FactItem(content=f"fact {index}", source_url=url) for index, url in enumerate(urls)
        ],
        analyst_opinions=[],
    )


def test_flatten_walks_every_section():
    report = _report(
        news={"items": [{"claim": "n1", "source_url": "https://x/1"}]},
        hires={"items": [{"claim": "h1", "source_url": "https://x/2"}]},
    )

    flat = _flatten(report)

    assert [section for section, _i, _c in flat] == ["news", "hires"]


def test_apply_removes_only_the_rejected_claim():
    report = _report(
        news={
            "items": [
                {"claim": "keep me", "source_url": "https://x/1"},
                {"claim": "drop me", "source_url": "https://x/2"},
            ]
        }
    )

    rebuilt = _apply(report, {1: "wrong_subject"})

    assert [item.claim for item in rebuilt.news.items] == ["keep me"]


def test_apply_with_nothing_removed_returns_the_same_report():
    report = _report(news={"items": [{"claim": "a", "source_url": "https://x/1"}]})

    assert _apply(report, {}) is report


def test_a_claim_citing_a_url_outside_the_evidence_is_removed(monkeypatch):
    # Deterministic: no model needed, a URL that is not in the evidence cannot be
    # a source for a claim. The model pass is stubbed out so this test asserts
    # only that rule and never spends a real API call.
    import asyncio

    from app.services import report_audit

    async def no_model_failures(*args, **kwargs):
        return {}

    monkeypatch.setattr(report_audit, "_audit_claims", no_model_failures)

    report = _report(
        news={
            "items": [
                {"claim": "real", "source_url": "https://x/known"},
                {"claim": "invented", "source_url": "https://x/unknown"},
            ]
        }
    )
    fact_base = _fact_base(["https://x/known"])

    audited, outcome = asyncio.run(audit_report(report, fact_base, company="A"))

    assert [item.claim for item in audited.news.items] == ["real"]
    assert outcome.total_removed == 1
    assert outcome.removed[0]["verdict"] == "unknown_source"


@pytest.mark.parametrize("section", SECTION_NAMES)
def test_every_section_is_auditable(section):
    report = _report(**{section: {"items": [{"claim": "c", "source_url": "https://x/1"}]}})

    flat = _flatten(report)

    assert section in [name for name, _i, _c in flat]


def test_a_first_brief_has_no_change_section():
    report = _report()

    assert report.changes_since_previous == []
    assert report.possible_facts == []


def test_possible_facts_carry_their_url_and_subject():
    from app.models.briefing import LooseClaim

    report = _report(
        possible_facts=[
            LooseClaim(
                claim="A mentioned B's margin",
                source_url="https://x/other",
                article_subject="Other Co",
            )
        ]
    )

    assert report.possible_facts[0].source_url.host == "x"
    assert report.possible_facts[0].article_subject == "Other Co"
