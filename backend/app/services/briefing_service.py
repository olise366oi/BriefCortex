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
    EvidenceConflict,
    EvidenceItem,
    EvidenceSummary,
    IncidentalItem,
)
from app.models.fact_base import FactBase, FactItem
from app.repositories.briefing_repository import BriefingRepository, EvidenceRepository
from app.services.analysis import generate_briefing_report
from app.services.cache import DEFAULT_TTL_HOURS as CACHE_TTL_HOURS
from app.services.ingestion import _build_company_id, ingest_company_data
from app.services.logger_service import get_logger
from app.services.window import compute_evidence_window

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


async def _generate_live_report(
    record: BriefingRecord,
    db: Session,
) -> tuple[BriefingReport, EvidenceSummary]:
    """Run ingestion + analysis, reusing evidence already stored for the company.

    The stored facts are read before retrieval so the search can target only the
    days not yet covered, and the newly collected ones are persisted afterwards
    for the next run.
    """
    request = BriefingRequest(
        company_name=record.company_name,
        meeting_time=record.meeting_time,
        meeting_purpose=record.meeting_purpose,
    )

    company_id = _build_company_id(record.company_name)
    evidence = EvidenceRepository(db)
    stored = evidence.list_by_kind(company_id, "hard")
    stored_opinions = evidence.list_by_kind(company_id, "opinion")
    coverage_end = evidence.coverage_end(company_id)
    known_urls = evidence.known_urls(company_id)

    stored_items = [
        FactItem(
            content=row.content,
            source_url=row.source_url,
            publish_date=row.publish_date,
        )
        for row in stored
    ]
    stored_opinion_items = [
        FactItem(
            content=row.content,
            source_url=row.source_url,
            publish_date=row.publish_date,
        )
        for row in stored_opinions
    ]

    logger.info(
        "Evidence reuse | company_id=%s | stored_facts=%s | stored_opinions=%s | known_urls=%s | coverage_end=%s",
        company_id,
        len(stored_items),
        len(stored_opinion_items),
        len(known_urls),
        coverage_end,
    )

    fact_base = await ingest_company_data(
        request,
        coverage_end=coverage_end,
        stored_facts=stored_items,
        stored_opinions=stored_opinion_items,
        known_urls=known_urls,
    )

    def _payload(items):
        return [
            {
                "content": item.content,
                "source_url": str(item.source_url),
                "publish_date": item.publish_date,
            }
            for item in items
        ]

    added_facts = evidence.add_many(
        company_id, _payload(fact_base.hard_facts), kind="hard"
    )
    added_opinions = evidence.add_many(
        company_id, _payload(fact_base.analyst_opinions), kind="opinion"
    )

    # URL identity outlives the fact bodies it describes, so a purged article
    # still counts as read next time instead of being fetched again.
    tracked = set(known_urls or set())
    tracked.update(str(item.source_url) for item in fact_base.hard_facts)
    new_urls = evidence.record_urls(company_id, tracked)

    # The watermark records that these days were searched, even if they
    # produced nothing, so the next run starts after them instead of redoing
    # the same quiet days.
    window = compute_evidence_window(
        record.meeting_time, coverage_end=coverage_end
    )
    evidence.record_coverage(company_id, window.fetch_end)

    logger.info(
        "Evidence stored | company_id=%s | new_facts=%s | new_opinions=%s "
        "| new_urls=%s | searched_through=%s",
        company_id,
        added_facts,
        added_opinions,
        new_urls,
        window.fetch_end,
    )

    report = await generate_briefing_report(
        fact_base=fact_base,
        meeting_purpose=record.meeting_purpose,
    )
    return report, _build_evidence_summary(fact_base)


def _build_evidence_summary(fact_base: FactBase) -> EvidenceSummary:
    """Describe what the brief was built on, without touching the report."""
    return EvidenceSummary(
        hard_fact_count=len(fact_base.hard_facts),
        opinion_count=len(fact_base.analyst_opinions),
        conflict_count=len(fact_base.conflicts),
        hiring_count=len(fact_base.hiring),
        conflicts=[
            EvidenceConflict(
                conflict_type=str(conflict.get("conflict_type", "")),
                fact_a=str(conflict.get("fact_a", "")),
                source_a=str(conflict.get("source_a", "")),
                fact_b=str(conflict.get("fact_b", "")),
                source_b=str(conflict.get("source_b", "")),
                description=str(conflict.get("description", "")),
            )
            for conflict in fact_base.conflicts
        ],
        hard_facts=[
            EvidenceItem(
                content=item.content,
                source_url=str(item.source_url),
                publish_date=item.publish_date,
                also_reported_by=list(item.also_reported_by),
            )
            for item in fact_base.hard_facts
        ],
        analyst_opinions=[
            EvidenceItem(
                content=item.content,
                source_url=str(item.source_url),
                publish_date=item.publish_date,
                also_reported_by=list(item.also_reported_by),
            )
            for item in fact_base.analyst_opinions
        ],
        hiring=[
            EvidenceItem(
                content=item.content,
                source_url=str(item.source_url),
                publish_date=item.publish_date,
                also_reported_by=list(item.also_reported_by),
            )
            for item in fact_base.hiring
        ],
        incidental_facts=[
            IncidentalItem(
                content=item.content,
                source_url=str(item.source_url),
                publish_date=item.publish_date,
                provenance=item.provenance,
                corroborating_url=item.corroborating_url,
                article_subject=item.article_subject,
            )
            for item in fact_base.incidental_facts
        ],
    )


def _to_status_response(record: BriefingRecord) -> BriefingStatusResponse:
    report = None
    if record.report is not None:
        report = BriefingReport.model_validate(record.report)

    evidence = None
    if record.evidence is not None:
        evidence = EvidenceSummary.model_validate(record.evidence)

    return BriefingStatusResponse(
        id=record.id,
        status=BriefingJobStatus(record.status),
        company_name=record.company_name,
        meeting_time=record.meeting_time,
        meeting_purpose=record.meeting_purpose,
        report=report,
        evidence=evidence,
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
            evidence = None
        else:
            report, evidence = await _generate_live_report(record, db)

        repo.update(
            briefing_id,
            status=BriefingJobStatus.COMPLETED.value,
            report=report.model_dump(mode="json"),
            evidence=evidence.model_dump(mode="json") if evidence else None,
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

    company = request.company_name.strip()
    purpose = request.meeting_purpose.strip()

    # An identical request inside the TTL is already answered. Reuse that
    # record instead of paying for retrieval and inference again; the poll
    # endpoint serves it directly because its status is already completed.
    cached = repo.find_cached(company, purpose, request.meeting_time)
    if cached is not None and _is_fresh(cached):
        logger.info(
            "Request served from cache | id=%s | company=%r | purpose=%r",
            cached.id,
            company,
            purpose,
        )
        return cached.id

    record = repo.create(
        company_name=company,
        meeting_time=request.meeting_time,
        meeting_purpose=purpose,
        status=BriefingJobStatus.PENDING.value,
    )

    _spawn_job(record.id, settings)
    return record.id


def _is_fresh(record: BriefingRecord) -> bool:
    """True when a completed record is still inside the cache TTL."""
    created = record.created_at
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_hours = (
        datetime.now(timezone.utc) - created
    ).total_seconds() / 3600
    return age_hours < CACHE_TTL_HOURS


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
