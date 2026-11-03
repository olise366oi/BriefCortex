from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Set

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
from app.services.analysis import generate_briefing_report
from app.services.ingestion import ingest_company_data
from app.services.logger_service import get_logger

logger = get_logger("briefing_service")

MOCK_REPORT_PATH = Path(__file__).resolve().parents[1] / "mock" / "nio_briefing_report.json"

# Jobs are held in a set so the event loop cannot garbage-collect a task while
# it is still running, and so shutdown can wait for in-flight work.
_background_tasks: Set[asyncio.Task] = set()

# A job whose record has been PROCESSING for longer than this is treated as
# abandoned - the process that owned it is gone.
STALE_JOB_MINUTES = 10

USER_FACING_ERROR = "Brief generation failed. Please try again."


def _load_mock_report() -> BriefingReport:
    payload = json.loads(MOCK_REPORT_PATH.read_text(encoding="utf-8"))
    return BriefingReport.model_validate(payload)


async def _generate_live_report(record: BriefingRecord) -> BriefingReport:
    """Run ingestion + analysis and return the API report schema."""
    request = BriefingRequest(
        company_name=record.company_name,
        meeting_time=record.meeting_time,
        meeting_purpose=record.meeting_purpose,
    )
    fact_base = await ingest_company_data(request)
    return await generate_briefing_report(
        fact_base=fact_base,
        meeting_purpose=record.meeting_purpose,
    )


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
    repo = BriefingRepository(db)
    try:
        record = repo.update(briefing_id, status=BriefingJobStatus.PROCESSING.value)
        if record is None:
            return

        if settings.MOCK_DATA:
            await asyncio.sleep(settings.MOCK_PROCESSING_DELAY_SECONDS)
            report = _load_mock_report()
        else:
            report = await _generate_live_report(record)

        repo.update(
            briefing_id,
            status=BriefingJobStatus.COMPLETED.value,
            report=report.model_dump(mode="json"),
        )
    except Exception:
        # The traceback goes to the log; the stored message stays safe to show.
        logger.exception("Briefing job failed | id=%s", briefing_id)
        try:
            repo.update(
                briefing_id,
                status=BriefingJobStatus.FAILED.value,
                error=USER_FACING_ERROR,
            )
        except Exception:
            logger.exception("Could not mark briefing as failed | id=%s", briefing_id)
    finally:
        db.close()


def _spawn_job(briefing_id: uuid.UUID, settings: Settings) -> None:
    """Start a job and keep a reference until it finishes."""
    task = asyncio.create_task(_process_job(briefing_id, settings))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _fail_stale_jobs(db: Session) -> None:
    """Mark abandoned processing records as failed so the UI stops polling."""
    repo = BriefingRepository(db)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_JOB_MINUTES)
    for record in repo.list_by_status(BriefingJobStatus.PROCESSING.value):
        updated_at = record.updated_at
        if updated_at is None:
            continue
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if updated_at < cutoff:
            logger.warning(
                "Marking stale briefing as failed | id=%s | updated_at=%s",
                record.id,
                updated_at,
            )
            repo.update(
                record.id,
                status=BriefingJobStatus.FAILED.value,
                error=USER_FACING_ERROR,
            )


async def create_briefing(db: Session, request: BriefingRequest) -> uuid.UUID:
    settings = get_settings()
    repo = BriefingRepository(db)

    record = repo.create(
        company_name=request.company_name.strip(),
        meeting_time=request.meeting_time,
        meeting_purpose=request.meeting_purpose.strip(),
        status=BriefingJobStatus.PENDING.value,
    )

    _spawn_job(record.id, settings)
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


async def recover_stale_jobs() -> None:
    """Fail jobs left in PROCESSING by a previous process."""
    db = SessionLocal()
    try:
        _fail_stale_jobs(db)
    finally:
        db.close()


async def wait_for_jobs(timeout_seconds: float = 30.0) -> None:
    """Let in-flight jobs finish before the event loop closes."""
    if not _background_tasks:
        return
    logger.info("Waiting for %s in-flight briefing job(s)", len(_background_tasks))
    await asyncio.wait(list(_background_tasks), timeout=timeout_seconds)
