from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class BriefingRequest(BaseModel):
    """Input model for a pre-meeting briefing request.

    Also the ingestion layer's input, so the request is defined once.

    ``company_description`` is the caller's own wording for the company, used
    when a name alone cannot identify it: a registry lookup returns several
    unrelated entities, or two different businesses trade under the same name.
    It is free text on purpose, because the registry has no entry for a private
    startup and the caller knows things the registry does not.
    """

    company_name: str
    meeting_time: datetime
    meeting_purpose: str
    company_description: str = ""
    # Exchange ticker, when the caller knows it. Without one no quote is fetched,
    # which is correct for a private company and avoids guessing a symbol.
    ticker: str = ""
    # Which of the caller's own company profiles to use for the recommendation.
    # Empty means none: the advice is then unauthored by the caller's position.
    own_profile_name: str = ""
    # A one-off profile, for a caller who does not want to save one first.
    own_profile_inline: str = ""


class BriefingAcceptedResponse(BaseModel):
    id: UUID
    status: str = "accepted"


class EntityCandidateRequest(BaseModel):
    """Input for the confirmation step that follows a rejected identity.

    ``round`` counts how many times the caller has already been shown candidates,
    so the prompt can change with each round: repeating the same list back to
    someone who just said "none of these" wastes their time and reads as not
    listening.
    """

    company_name: str
    description: str = ""
    round: int = 1
    # Candidates already rejected, so they are not offered again.
    rejected: List[str] = Field(default_factory=list)


class EntityCandidateOut(BaseModel):
    """One company the caller might mean, with the facts that identify it."""

    name: str
    matching_facts: str = ""
    reason: str = ""
    evidence_url: str = ""
    confidence: str = "low"


class EntityCandidateList(BaseModel):
    candidates: List[EntityCandidateOut] = Field(default_factory=list)


class BriefingJobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Claim(BaseModel):
    """One reported development, with the day it was reported.

    The date matters because news is read in time order: two outlets covering
    the same event weeks apart are one story developing, and a reader deciding
    what is current needs the day, not just the outlet.
    """

    claim: str
    source_url: HttpUrl
    # ISO date (YYYY-MM-DD) of the report, or the best available date. Empty when
    # the source carries no date at all, which is stated rather than guessed.
    date: str = ""
    # The earliest date this same event was reported. Set when several reports
    # cover one event over time, so the reader sees the span of the development
    # ("first reported 2026-10-07, latest 2026-10-29") rather than one arbitrary
    # day. Empty when the claim is a single report.
    first_seen: str = ""
    # True when this event is what the reader came for: a filed result, a
    # regulatory action, a funding round, a named partnership. Such items lead,
    # ahead of recency.
    key_event: bool = False


class LooseClaim(BaseModel):
    """A fact about the company found inside an article about someone else.

    Kept apart from the confirmed claims on purpose: the sentence is about our
    company, but the article is not, and no follow-up reporting was found to
    confirm it. It is shown with its link so a reader can judge it, never counted
    as a finding and never used as leverage.
    """

    claim: str
    source_url: HttpUrl
    article_subject: Optional[str] = None


class ClaimModule(BaseModel):
    """One report section. Every section has the same shape."""

    items: List[Claim] = Field(default_factory=list)


class ExecutiveSummary(BaseModel):
    summary: str
    recommendation: str
    confidence: str


class Analysis(BaseModel):
    """The assessment that sits between the facts and the advice.

    A recommendation with no stated reasoning is an opinion the reader cannot
    audit. This section says what the evidence amounts to and where the risk
    sits, so the advice that follows is a conclusion rather than an assertion.
    """

    # What the facts add up to: the direction, and what is actually demonstrated.
    assessment: str = ""
    # Named risks, each with the evidence behind it.
    risks: List[str] = Field(default_factory=list)


class RevisionNote(BaseModel):
    """How this brief differs from the previous one, and why.

    A second brief that reuses earlier evidence must not simply repeat the
    earlier conclusion: the reader needs to see what moved. Each entry pairs a
    change with the evidence that caused it, so a shift reads as reasoning
    rather than as the model contradicting itself.
    """

    change: str
    why: str


class AuditRemoval(BaseModel):
    """One claim the audit removed, kept so the removal is explainable.

    A removed claim is not shown to the reader as a finding, but the reader is
    told that something was removed and why. Silently dropping it would make the
    brief look cleaner than the evidence allows.
    """

    section: str
    claim: str
    verdict: str
    remedy: str


class BriefingReport(BaseModel):
    """The generated brief, and the schema the model must produce.

    Read in this order: news, hiring, analysis, recommendation. A reader who
    sees the assessment before the facts has no way to dispute it; a reader who
    sees the facts first can.
    """

    # Everything reported about the company that is not a posting and not a
    # mention inside someone else's article. One lane, because the subdivision
    # ("launches", "risks", "open questions") was imposing a judgement before the
    # evidence had been weighed.
    news: ClaimModule = Field(default_factory=ClaimModule)
    # Job postings: intent, not outcome.
    hires: ClaimModule = Field(default_factory=ClaimModule)
    # What the evidence amounts to, and where the risk sits.
    analysis: Analysis = Field(default_factory=Analysis)
    executive_summary: ExecutiveSummary
    # Empty on a first brief. On a later one it explains every place the reading
    # moved, so a revision is never a silent rewrite.
    changes_since_previous: List[RevisionNote] = Field(default_factory=list)
    # Populated by the audit, never by the model. Claims that failed the
    # subject/support check are listed here instead of being delivered as
    # findings, so the reader knows the brief was filtered and why.
    removed_claims: List[AuditRemoval] = Field(default_factory=list)
    # Facts found inside articles about other companies, with no follow-up
    # reporting behind them. Populated from the fact base, never by the model:
    # the model is never asked to write about unconfirmed material it cannot
    # verify, and the reader sees these as leads beside the brief rather than
    # inside it.
    possible_facts: List[LooseClaim] = Field(default_factory=list)
    # Stated intent about the future: CEO pledges, targets, guidance about what
    # the company intends to do. Populated from the fact base, never by the
    # model, and deliberately not read into the analysis — a promise is not
    # evidence that something happened. Shown before possible_facts so the reader
    # sees what is being promised before what is merely rumoured.
    forward_looking: List[LooseClaim] = Field(default_factory=list)
    # Market price, when the company is listed. Timestamped and kept out of
    # hard_facts so it is obvious it is a quote, not reporting.
    quote: Optional[LooseClaim] = None


class EvidenceConflict(BaseModel):
    """Two hard facts that contradict each other, with both sources attached.

    Produced by the deterministic conflict engine, never by the model, so this
    is evidence about evidence: it is served to the UI beside the report rather
    than inside it.
    """

    conflict_type: str
    fact_a: str
    source_a: str
    fact_b: str
    source_b: str
    description: str


class EvidenceItem(BaseModel):
    """One collected sentence with the article it came from."""

    content: str
    source_url: str
    publish_date: Optional[datetime] = None
    # Other outlets that reported this same fact. The claim is stored once, but
    # every place it appeared stays available to the reader.
    also_reported_by: List[str] = Field(default_factory=list)


class IncidentalItem(BaseModel):
    """A fact about this company found inside an article about another company.

    Delivered as a trailing reference, never as evidence: the report the model
    writes never sees these, so a reader can weigh them without the brief
    silently leaning on them.
    """

    content: str
    source_url: str
    publish_date: Optional[datetime] = None
    provenance: str = "unverified"
    corroborating_url: Optional[str] = None
    article_subject: Optional[str] = None


class CoverageItem(BaseModel):
    """Retrieval coverage, delivered so a reader knows how thin the evidence is."""

    level: str = "none"
    total_facts: int = 0
    distinct_sources: int = 0
    can_support_leverage: bool = False
    lanes_missing: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    checks_to_run: List[str] = Field(default_factory=list)


class EvidenceSummary(BaseModel):
    """What the brief was built on, exposed so the UI can show its provenance."""

    hard_fact_count: int = 0
    opinion_count: int = 0
    conflict_count: int = 0
    hiring_count: int = 0
    conflicts: List[EvidenceConflict] = Field(default_factory=list)
    # The facts behind the brief. Each is stored once however many outlets
    # reported it, with the other outlets listed beside it.
    hard_facts: List[EvidenceItem] = Field(default_factory=list)
    analyst_opinions: List[EvidenceItem] = Field(default_factory=list)
    # Role postings: intent rather than outcome, listed separately.
    hiring: List[EvidenceItem] = Field(default_factory=list)
    incidental_facts: List[IncidentalItem] = Field(default_factory=list)
    # How much was actually retrieved, and what the reader must check themselves.
    coverage: Optional[CoverageItem] = None


class BriefingStatusResponse(BaseModel):
    id: UUID
    status: BriefingJobStatus
    company_name: str
    meeting_time: datetime
    meeting_purpose: str
    report: Optional[BriefingReport] = None
    evidence: Optional[EvidenceSummary] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BriefingListItem(BaseModel):
    id: UUID
    company_name: str
    meeting_time: datetime
    meeting_purpose: str
    status: BriefingJobStatus
    created_at: datetime


class BriefingListResponse(BaseModel):
    items: List[BriefingListItem] = Field(default_factory=list)
