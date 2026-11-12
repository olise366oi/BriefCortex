# /backend/app/services/coverage.py
"""An honest account of what the evidence does and does not cover.

A brief built from two paragraphs is not the same product as a brief built from
forty, and a reader cannot tell them apart from the report alone. Worse: a
private company with no press coverage produces a clean, confident-looking brief
that is simply empty, which reads as "no risks found" when the truth is "no data
found". Those are opposite statements and only one of them is safe to act on.

This module records the second one. It counts what arrived, names which lanes
came back thin, and lists the checks a reader should run themselves when the
open web cannot answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.services.company_identity import CompanyIdentity

WINDOW_DAYS = 30

# A lane is "thin" below this. Two articles is not coverage; it is a hint.
THIN_THRESHOLD = 3

# Below this many facts the brief cannot support a numeric ask, whatever the
# model writes.
MIN_FACTS_FOR_LEVERAGE = 5

COVERAGE_STRONG = "strong"
COVERAGE_PARTIAL = "partial"
COVERAGE_THIN = "thin"
COVERAGE_NONE = "none"


@dataclass
class EvidenceCoverageReport:
    """What the retrieval reached, and where it came up short."""

    total_facts: int = 0
    distinct_sources: int = 0
    lanes_missing: List[str] = field(default_factory=list)
    checks_to_run: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # Queries the search API refused outright. Kept apart from "no reporting"
    # because a blocked run says nothing about the company.
    search_failures: List[str] = field(default_factory=list)

    @property
    def level(self) -> str:
        """One word a reader can act on."""
        if self.total_facts == 0:
            return COVERAGE_NONE
        if self.total_facts < THIN_THRESHOLD:
            return COVERAGE_THIN
        if self.total_facts < MIN_FACTS_FOR_LEVERAGE:
            return COVERAGE_PARTIAL
        return COVERAGE_STRONG

    @property
    def can_support_leverage(self) -> bool:
        """True when the fact base is thick enough to build a numeric ask on."""
        return self.total_facts >= MIN_FACTS_FOR_LEVERAGE


# Checks worth naming when the open web cannot answer. Each is something the
# reader can obtain directly from the counterparty or a registry, so the brief
# stays useful on a company nobody writes about.
STANDARD_CHECKS = (
    "Company registration status and legal form, from the local company registry",
    "Any litigation, insolvency or winding-up record in the company's jurisdiction",
    "Sanctions and export-control screening for the entity and its owners",
    "Latest filed accounts or audited financial statements",
    "Beneficial ownership and any state or group affiliation",
)


def build_coverage_report(
    *,
    total_facts: int,
    distinct_sources: int,
    identity: Optional[CompanyIdentity],
    has_hiring: bool,
    has_legal: bool,
    has_opinions: bool,
    preliminary: bool = False,
    window_start=None,
    window_end=None,
    search_failures: Optional[List[str]] = None,
) -> EvidenceCoverageReport:
    """Summarize retrieval coverage and the checks it leaves to the reader."""
    report = EvidenceCoverageReport(
        total_facts=total_facts,
        distinct_sources=distinct_sources,
        search_failures=list(search_failures or []),
    )

    if report.search_failures:
        # A refusal is not silence. Saying so keeps a quota-exhausted or
        # blocked run from being read as "this company has nothing to find",
        # which is the one conclusion the ledger exists to prevent.
        report.lanes_missing.append("search")
        report.notes.append(
            f"{len(report.search_failures)} search quer(y/ies) were refused by "
            "the search API, so this brief is thinner than the company's actual "
            "coverage. Retrieval was cut off; it did not come back empty."
        )

    if preliminary:
        # The meeting is far enough out that its own 30-day window has not
        # started. Saying so keeps the brief from being read as a pre-meeting
        # picture of the days that will actually lead up to it.
        report.notes.append(
            f"The meeting is more than {WINDOW_DAYS} days away, so its own "
            f"30-day window has not started. This brief covers the latest "
            f"available evidence ({window_start} to {window_end}) and must be "
            "refreshed closer to the meeting."
        )

    if total_facts == 0 and not report.search_failures:
        report.notes.append(
            "No reporting was found in the window. This is a statement about "
            "coverage, not about the company: nothing here says the company is "
            "safe, only that nothing was written about it."
        )
    elif total_facts < MIN_FACTS_FOR_LEVERAGE:
        report.notes.append(
            f"Only {total_facts} fact(s) were found. Treat any conclusion drawn "
            "from this as provisional."
        )

    if total_facts > 0 and distinct_sources < THIN_THRESHOLD:
        report.notes.append(
            f"Facts came from {distinct_sources} source(s); a single outlet can "
            "be wrong, so corroborate before acting."
        )

    if identity is None:
        report.lanes_missing.append("company identity")
        report.notes.append(
            "The company could not be matched to a registered legal entity, so "
            "no jurisdiction-specific source was consulted."
        )
    elif not identity.confident:
        report.lanes_missing.append("confirmed identity")
        report.notes.append(
            "The registry match was ambiguous, so no jurisdiction-specific "
            "regulator was named."
        )

    if not has_legal:
        report.lanes_missing.append("legal and regulatory")
    if not has_hiring:
        report.lanes_missing.append("hiring")
    if not has_opinions:
        report.lanes_missing.append("analyst commentary")

    # A thin brief is exactly the case where the reader has to do the work, so
    # the checklist appears whenever coverage cannot carry a conclusion.
    if not report.can_support_leverage:
        report.checks_to_run.extend(STANDARD_CHECKS)

    return report
