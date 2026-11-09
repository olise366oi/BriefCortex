from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Set

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
    CoverageItem,
    EvidenceConflict,
    EvidenceItem,
    EvidenceSummary,
    IncidentalItem,
)
from app.models.fact_base import FactBase, FactItem
from app.repositories.briefing_repository import BriefingRepository, EvidenceRepository
from app.services.analysis import generate_briefing_report
from app.services.cache import DEFAULT_TTL_HOURS as CACHE_TTL_HOURS
from app.services.cache_invalidation import InvalidationVerdict, check_reuse
from app.services.evidence_fingerprint import evidence_fingerprint
from app.services.own_profile import ProfileStore, resolve_profile
from app.services.own_company_scan import scan_own_company
from app.services.ingestion import _build_company_id, ingest_company_data
from app.services.logger_service import get_logger
from app.services.window import compute_evidence_window

logger = get_logger("briefing_service")

# The caller's own company profiles, held for this process. Read by the
# recommendation stage only, never by the analysis.
_profile_store = ProfileStore()

MOCK_REPORT_PATH = Path(__file__).resolve().parents[1] / "mock" / "nio_briefing_report.json"

# Jobs are held in a set so the event loop cannot garbage-collect a task while
# it is still running, and so shutdown can wait for in-flight work.
_background_tasks: Set[asyncio.Task] = set()

# A job whose record has been PROCESSING for longer than this is treated as
# abandoned - the process that owned it is gone.
STALE_JOB_MINUTES = 10

USER_FACING_ERROR = "Brief generation failed. Please try again."


def _load_mock_report(company_name: str = "") -> BriefingReport:
    """Return the bundled sample brief, marked as sample data.

    The sample is one fixed report about NIO. Served unchanged, it appeared under
    any company the caller asked for — a brief for Anthropic and a brief for
    SpaceX came back with the same NIO paragraph and no indication it was
    fabricated. A demo dataset has to announce itself: the reader must be able to
    tell a sample from a finding, and the company the sample describes from the
    company they asked about.
    """
    payload = json.loads(MOCK_REPORT_PATH.read_text(encoding="utf-8"))
    report = BriefingReport.model_validate(payload)

    asked = " ".join((company_name or "").split())
    sample_banner = (
        "SAMPLE DATA — this brief is the bundled demo report about NIO, not a "
        "reading of the company you asked for."
    )
    summary = ""
    if asked:
        summary = (
            f"{sample_banner} You asked about {asked}; the paragraphs below are "
            "NIO's and are shown so the interface can be exercised. "
        )
    else:
        summary = f"{sample_banner} "

    existing = report.executive_summary
    marked = existing.model_copy(
        update={
            "summary": summary + (existing.summary or ""),
            "recommendation": (
                "SAMPLE DATA — do not act on this recommendation."
                + (" " + existing.recommendation if existing.recommendation else "")
            ),
            "confidence": (
                f"Demo mode: {asked or 'the requested company'} was not researched. "
                + (existing.confidence or "")
            ),
        }
    )
    return report.model_copy(update={"executive_summary": marked})


def _previous_report_for(
    repo: "BriefingRepository",
    company_name: str,
    exclude_id,
) -> Optional[BriefingReport]:
    """Return the most recent completed brief for this company, if any.

    A later run is a revision of an earlier one, so the earlier brief's
    conclusions must be available to compare against — and available nowhere
    else. Without this the second brief was generated as if it were the first:
    the reader got a fresh reading with no statement of what had changed, and the
    revision prompt that exists was never reached in production.
    """
    for candidate in repo.list_all():
        if candidate.id == exclude_id:
            continue
        if candidate.company_name.strip().lower() != company_name.strip().lower():
            continue
        if candidate.status != BriefingJobStatus.COMPLETED.value:
            continue
        if not candidate.report:
            continue
        try:
            return BriefingReport.model_validate(candidate.report)
        except Exception as exc:
            logger.warning(
                "Previous report unreadable | id=%s | %s", candidate.id, exc
            )
            return None
    return None


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
        company_description=getattr(record, "company_description", "") or "",
        ticker=getattr(record, "ticker", "") or "",
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

    # Keyed on the evidence rather than the calendar. Two runs whose facts match
    # are the same brief, so the second one skips inference entirely — the
    # calendar-keyed cache missed whenever a meeting moved by a day even though
    # nothing about the company had changed.
    description = getattr(record, "company_description", "") or ""
    # A profile changes the recommendation, so it is part of what the brief is
    # derived from. Without it in the fingerprint, two runs that differ only in
    # whose position the advice is written for would share one cached answer.
    profile = resolve_profile(
        _profile_store,
        getattr(record, "own_profile_name", "") or "",
        getattr(record, "own_profile_inline", "") or "",
    )
    profile_text = profile.to_prompt_block() if profile is not None else ""

    # Our own market position, read only when the caller asked for retrieval. It
    # is prompt context, not part of the brief's own evidence, so it does not
    # enter the fingerprint: two briefs on the same counterparty evidence are the
    # same brief even if our own share price moved overnight.
    own_block = ""
    own_company_name = getattr(record, "own_company_name", "") or ""
    own_company_ticker = getattr(record, "own_company_ticker", "") or ""
    own_source = getattr(record, "own_company_source", "") or "retrieval"
    if own_company_name and own_source == "retrieval":
        snapshot = await scan_own_company(own_company_name, own_company_ticker)
        own_block = snapshot.to_prompt_block()
        logger.info(
            "Own company context | company=%r | quote=%s | headlines=%s | usable=%s",
            own_company_name,
            snapshot.quote is not None,
            len(snapshot.headlines),
            bool(own_block),
        )
    elif own_company_name and own_source == "internal":
        # The caller supplies its own material instead of having the market read.
        # It joins the profile, because both describe our side of the deal.
        from app.services.internal_material import render_materials, shared_store

        materials = shared_store().list(own_company_name)
        rendered_materials = render_materials(materials)
        if rendered_materials:
            profile_text = "\n\n".join(
                part for part in (profile_text, rendered_materials) if part
            )
        logger.info(
            "Own company internal material | company=%r | items=%s | chars=%s",
            own_company_name,
            len(materials),
            len(rendered_materials),
        )

    fingerprint = evidence_fingerprint(
        fact_base, record.meeting_purpose, description, own_profile=profile_text
    )
    briefing_repo = BriefingRepository(db)
    reused = briefing_repo.find_by_fingerprint(record.company_name, fingerprint)
    if reused is not None:
        # Same facts, but they may contradict what the stored brief assumed. A
        # brief written while the company was hiring is wrong the moment a
        # layoff is reported, and the fingerprint cannot see that on its own.
        verdict = check_reuse(reused.evidence, fact_base)
        if verdict.reusable:
            logger.info(
                "Evidence-set cache hit | id=%s | fingerprint=%s | company=%r | "
                "skipped_inference=True",
                reused.id,
                fingerprint,
                record.company_name,
            )
            return (
                BriefingReport.model_validate(reused.report),
                _build_evidence_summary(fact_base),
            )

        logger.info(
            "Evidence-set cache invalidated | id=%s | company=%r | reason=%s",
            reused.id,
            record.company_name,
            verdict.reason,
        )

    report = await generate_briefing_report(
        fact_base=fact_base,
        meeting_purpose=record.meeting_purpose,
        previous=_previous_report_for(
            briefing_repo, record.company_name, record.id
        ),
        company=record.company_name,
        description=description,
        own_profile=profile_text,
        own_company_block=own_block,
    )
    briefing_repo.update(record.id, evidence_fingerprint=fingerprint)
    return report, _build_evidence_summary(fact_base)


def _build_evidence_summary(fact_base: FactBase) -> EvidenceSummary:
    """Describe what the brief was built on, without touching the report."""
    coverage = fact_base.coverage
    return EvidenceSummary(
        hard_fact_count=len(fact_base.hard_facts),
        opinion_count=len(fact_base.analyst_opinions),
        conflict_count=len(fact_base.conflicts),
        hiring_count=len(fact_base.hiring),
        coverage=CoverageItem(
            level=coverage.level,
            total_facts=coverage.total_facts,
            distinct_sources=coverage.distinct_sources,
            can_support_leverage=coverage.can_support_leverage,
            lanes_missing=list(coverage.lanes_missing),
            notes=list(coverage.notes),
            checks_to_run=list(coverage.checks_to_run),
        ),
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
            report = _load_mock_report(record.company_name)
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
        _mark_failed(db, briefing_id)
    finally:
        db.close()


def _mark_failed(db: Session, briefing_id: uuid.UUID) -> None:
    """Record the failure, on a connection that is still usable if one is not.

    A job can fail because the database went away mid-run — a managed Postgres
    closes an idle connection and the next query raises inside a long retrieval.
    The session that hit that error is then in a broken transaction: every further
    statement raises ``PendingRollbackError``, so the obvious retry (mark the row
    failed on the same session) fails too and the row is stranded in `processing`
    forever. Measured: exactly that, and the UI polled a job that would never
    finish.

    So the broken session is rolled back first, and if it still cannot write, a
    fresh session tries. Whatever happens here is reported, never raised: this
    runs inside the failure path and must not mask the original error.
    """
    try:
        db.rollback()
        BriefingRepository(db).update(
            briefing_id,
            status=BriefingJobStatus.FAILED.value,
            error=USER_FACING_ERROR,
        )
        return
    except Exception as exc:
        logger.warning(
            "Could not mark briefing failed on the job session | id=%s | %s",
            briefing_id,
            exc,
        )

    fresh: Optional[Session] = None
    try:
        fresh = SessionLocal()
        BriefingRepository(fresh).update(
            briefing_id,
            status=BriefingJobStatus.FAILED.value,
            error=USER_FACING_ERROR,
        )
        logger.info("Briefing marked failed on a fresh connection | id=%s", briefing_id)
    except Exception:
        logger.exception(
            "Could not mark briefing failed at all | id=%s | the stale-job sweep "
            "will close it on next startup",
            briefing_id,
        )
    finally:
        if fresh is not None:
            fresh.close()


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
    #
    # The stored brief is cross-checked against the facts on file before it is
    # served: an exact request is not the same as the same evidence.
    cached = repo.find_cached(company, purpose, request.meeting_time)
    if cached is not None and _is_fresh(cached):
        verdict = _stored_brief_still_holds(db, cached)
        if verdict.reusable:
            logger.info(
                "Request served from cache | id=%s | company=%r | purpose=%r",
                cached.id,
                company,
                purpose,
            )
            return cached.id

        logger.info(
            "Cache entry invalidated | id=%s | company=%r | reason=%s",
            cached.id,
            company,
            verdict.reason,
        )

    record = repo.create(
        company_name=company,
        meeting_time=request.meeting_time,
        meeting_purpose=purpose,
        status=BriefingJobStatus.PENDING.value,
        ticker=request.ticker,
        company_description=request.company_description,
        own_profile_name=request.own_profile_name,
        own_profile_inline=request.own_profile_inline,
        own_company_name=request.own_company_name,
        own_company_ticker=request.own_company_ticker,
        own_company_source=request.own_company_source,
    )

    _spawn_job(record.id, settings)
    return record.id


def _stored_brief_still_holds(db: Session, record: BriefingRecord) -> InvalidationVerdict:
    """Cross-check a stored brief against the facts currently on file.

    Called before a cached brief is served. The stored evidence beside the report
    is compared with what `EvidenceRepository` holds now, so a contradiction that
    arrived after the brief was written invalidates it instead of being served.
    """
    company_id = _build_company_id(record.company_name)
    current = EvidenceRepository(db).list_by_kind(company_id, "hard")

    current_items = [
        FactItem(content=row.content, source_url=row.source_url)
        for row in current
        if row.content and row.source_url
    ]

    fact_base = FactBase(
        company_id=company_id,
        last_refresh_date=datetime.now(timezone.utc),
        hard_facts=current_items,
        analyst_opinions=[],
    )
    return check_reuse(record.evidence, fact_base)


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
