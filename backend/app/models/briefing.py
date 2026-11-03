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


class BriefingStatusResponse(BaseModel):
    id: UUID
    status: BriefingJobStatus
    company_name: str
    meeting_time: datetime
    meeting_purpose: str
    report: Optional[BriefingReport] = None
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
