from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class BriefingRequest(BaseModel):
    """Input model for a pre-meeting briefing request.

    Also the ingestion layer's input, so the request is defined once.
    """

    company_name: str
    meeting_time: datetime
    meeting_purpose: str


class BriefingAcceptedResponse(BaseModel):
    id: UUID
    status: str = "accepted"


class BriefingJobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Claim(BaseModel):
    claim: str
    source_url: HttpUrl


class ClaimModule(BaseModel):
    """One report section. Every section has the same shape."""

    items: List[Claim] = Field(default_factory=list)


class ExecutiveSummary(BaseModel):
    summary: str
    recommendation: str
    confidence: str


class BriefingReport(BaseModel):
    """The generated brief, and the schema the model must produce."""

    news: ClaimModule = Field(default_factory=ClaimModule)
    hires: ClaimModule = Field(default_factory=ClaimModule)
    launches: ClaimModule = Field(default_factory=ClaimModule)
    open_questions: ClaimModule = Field(default_factory=ClaimModule)
    risks: ClaimModule = Field(default_factory=ClaimModule)
    executive_summary: ExecutiveSummary


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


class EvidenceSummary(BaseModel):
    """What the brief was built on, exposed so the UI can show its provenance."""

    hard_fact_count: int = 0
    opinion_count: int = 0
    conflict_count: int = 0
    conflicts: List[EvidenceConflict] = Field(default_factory=list)
    analyst_opinions: List[EvidenceItem] = Field(default_factory=list)
    incidental_facts: List[IncidentalItem] = Field(default_factory=list)


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
