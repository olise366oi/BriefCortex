from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import HTTPException, status

from app.core.config import Settings, get_settings
from app.models.briefing import (
    BriefingJobStatus,
    BriefingReport,
    BriefingRequest,
    BriefingStatusResponse,
)

MOCK_REPORT_PATH = Path(__file__).resolve().parents[1] / "mock" / "nio_briefing_report.json"


@dataclass
class BriefingJob:
    request_id: str
    company_name: str
    meeting_time: datetime
    meeting_purpose: str
    status: BriefingJobStatus = BriefingJobStatus.PENDING
    report: Optional[BriefingReport] = None
    error: Optional[str] = None


@dataclass
class BriefingStore:
    jobs: Dict[str, BriefingJob] = field(default_factory=dict)


_store = BriefingStore()


def _load_mock_report() -> BriefingReport:
    payload = json.loads(MOCK_REPORT_PATH.read_text(encoding="utf-8"))
    return BriefingReport.model_validate(payload)


async def _process_job(request_id: str, settings: Settings) -> None:
    job = _store.jobs.get(request_id)
    if job is None:
        return

    job.status = BriefingJobStatus.PROCESSING

    try:
        await asyncio.sleep(settings.MOCK_PROCESSING_DELAY_SECONDS)
        if not settings.MOCK_DATA:
            raise RuntimeError("Live briefing pipeline is not wired yet")

        job.report = _load_mock_report()
        job.status = BriefingJobStatus.COMPLETED
    except Exception as exc:
        job.status = BriefingJobStatus.FAILED
        job.error = str(exc)


async def create_briefing(request: BriefingRequest) -> str:
    settings = get_settings()
    request_id = str(uuid.uuid4())

    _store.jobs[request_id] = BriefingJob(
        request_id=request_id,
        company_name=request.company_name.strip(),
        meeting_time=request.meeting_time,
        meeting_purpose=request.meeting_purpose.strip(),
    )

    asyncio.create_task(_process_job(request_id, settings))
    return request_id


def get_briefing_status(request_id: str) -> BriefingStatusResponse:
    job = _store.jobs.get(request_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Briefing request '{request_id}' not found",
        )

    return BriefingStatusResponse(
        request_id=job.request_id,
        status=job.status,
        company_name=job.company_name,
        meeting_time=job.meeting_time,
        meeting_purpose=job.meeting_purpose,
        report=job.report,
        error=job.error,
    )
