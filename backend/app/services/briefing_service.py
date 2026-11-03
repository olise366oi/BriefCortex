from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.db.models import BriefingRecord
from app.models.briefing import (
    BriefingJobStatus,
    BriefingListItem,
    BriefingListResponse,
    BriefingReport,
    BriefingRequest,
    BriefingStatusResponse,
)
from app.repositories.briefing_repository import BriefingRepository

MOCK_REPORT_PATH = Path(__file__).resolve().parents[1] / "mock" / "nio_briefing_report.json"


def _load_mock_report() -> BriefingReport:
    payload = json.loads(MOCK_REPORT_PATH.read_text(encoding="utf-8"))
    return BriefingReport.model_validate(payload)


def _to_status_response(record: BriefingRecord) -> BriefingStatusResponse:
    report = None
    if record.report is not None:
        report = BriefingReport.model_validate(record.report)

    return BriefingStatusResponse(
        id=record.id,
        status=BriefingJobStatus(record.status),
        company_name=record.company_name,
        meeting_time=record.meeting_time,
        meeting_purpose=record.meeting_purpose,
        report=report,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_list_item(record: BriefingRecord) -> BriefingListItem:
    return BriefingListItem(
        id=record.id,
        company_name=record.company_name,
        meeting_time=record.meeting_time,
        meeting_purpose=record.meeting_purpose,
        status=BriefingJobStatus(record.status),
        created_at=record.created_at,
    )


async def _process_job(briefing_id: uuid.UUID, settings: Settings) -> None:
    db = SessionLocal()
    try:
        repo = BriefingRepository(db)
        repo.update(briefing_id, status=BriefingJobStatus.PROCESSING.value)

        await asyncio.sleep(settings.MOCK_PROCESSING_DELAY_SECONDS)

        if not settings.MOCK_DATA:
            raise RuntimeError("Live briefing pipeline is not wired yet")

        report = _load_mock_report()
        repo.update(
            briefing_id,
            status=BriefingJobStatus.COMPLETED.value,
            report=report.model_dump(mode="json"),
        )
    except Exception as exc:
        repo = BriefingRepository(db)
        repo.update(
            briefing_id,
            status=BriefingJobStatus.FAILED.value,
            error=str(exc),
        )
    finally:
        db.close()


async def create_briefing(db: Session, request: BriefingRequest) -> uuid.UUID:
    settings = get_settings()
    repo = BriefingRepository(db)

    record = repo.create(
        company_name=request.company_name.strip(),
        meeting_time=request.meeting_time,
        meeting_purpose=request.meeting_purpose.strip(),
        status=BriefingJobStatus.PENDING.value,
    )

    asyncio.create_task(_process_job(record.id, settings))
    return record.id


def list_briefings(db: Session) -> BriefingListResponse:
    repo = BriefingRepository(db)
    records = repo.list_all()
    return BriefingListResponse(items=[_to_list_item(record) for record in records])


def get_briefing(db: Session, briefing_id: uuid.UUID) -> BriefingStatusResponse:
    repo = BriefingRepository(db)
    record = repo.get_by_id(briefing_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Briefing '{briefing_id}' not found",
        )
    return _to_status_response(record)
