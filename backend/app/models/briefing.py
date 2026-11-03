from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, HttpUrl


class BriefingRequest(BaseModel):
    """Input model for a pre-meeting briefing request."""

    company_name: str
    meeting_time: datetime
    meeting_purpose: str


class BriefingAcceptedResponse(BaseModel):
    request_id: str
    status: str = "accepted"


class BriefingJobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Claim(BaseModel):
    claim: str
    source_url: HttpUrl


class NewsModule(BaseModel):
    items: List[Claim] = []


class HiresModule(BaseModel):
    items: List[Claim] = []


class LaunchesModule(BaseModel):
    items: List[Claim] = []


class OpenQuestionModule(BaseModel):
    items: List[Claim] = []


class RisksModule(BaseModel):
    items: List[Claim] = []


class ExecutiveSummary(BaseModel):
    summary: str
    recommendation: str
    confidence: str


class BriefingReport(BaseModel):
    news: NewsModule = NewsModule()
    hires: HiresModule = HiresModule()
    launches: LaunchesModule = LaunchesModule()
    open_questions: OpenQuestionModule = OpenQuestionModule()
    risks: RisksModule = RisksModule()
    executive_summary: ExecutiveSummary


class BriefingStatusResponse(BaseModel):
    request_id: str
    status: BriefingJobStatus
    company_name: str
    meeting_time: datetime
    meeting_purpose: str
    report: Optional[BriefingReport] = None
    error: Optional[str] = None
