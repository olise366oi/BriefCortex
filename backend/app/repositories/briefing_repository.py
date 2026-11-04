from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.db.models import (
    BriefingRecord,
    EvidenceCoverage,
    EvidenceFact,
    UrlFingerprint,
)
from app.services.retention import (
    RetentionPlan,
    build_retention_plan,
    fingerprint_state,
    is_hidden_from_hot_set,
    should_delete_fact,
)
from app.models.briefing import BriefingJobStatus


def _to_date(value: datetime) -> date:
    """Normalize a stored timestamp to a UTC date."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date()


def content_hash(content: str) -> str:
    """Stable identity for a fact sentence, used for cross-run deduplication."""
    normalized = " ".join(content.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class BriefingRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        company_name: str,
        meeting_time: datetime,
        meeting_purpose: str,
        status: str = "pending",
    ) -> BriefingRecord:
        record = BriefingRecord(
            company_name=company_name,
            meeting_time=meeting_time,
            meeting_purpose=meeting_purpose,
            status=status,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def get_by_id(self, briefing_id: uuid.UUID) -> Optional[BriefingRecord]:
        return self.db.get(BriefingRecord, briefing_id)

    def list_all(self) -> list[BriefingRecord]:
        stmt = select(BriefingRecord).order_by(BriefingRecord.created_at.desc())
        return list(self.db.scalars(stmt).all())

    def list_by_status(self, status: str) -> list[BriefingRecord]:
        stmt = select(BriefingRecord).where(BriefingRecord.status == status)
        return list(self.db.scalars(stmt).all())

    def find_cached(
        self,
        company_name: str,
        meeting_purpose: str,
        meeting_time: datetime,
    ) -> Optional[BriefingRecord]:
        """Return a completed brief for the same request, if one exists.

        Matching is on the request fields themselves rather than a stored hash,
        so the cache stays correct when the schema changes. Meeting time is
        compared by day: the evidence window is anchored to the date, so two
        meetings on the same day produce the same brief.
        """
        day = meeting_time.astimezone(timezone.utc).date() if meeting_time.tzinfo else meeting_time.date()
        start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=1)

        stmt = (
            select(BriefingRecord)
            .where(
                BriefingRecord.company_name == company_name,
                BriefingRecord.meeting_purpose == meeting_purpose,
                BriefingRecord.status == BriefingJobStatus.COMPLETED.value,
                BriefingRecord.meeting_time >= start,
                BriefingRecord.meeting_time < end,
            )
            .order_by(BriefingRecord.created_at.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def update(
        self,
        briefing_id: uuid.UUID,
        *,
        status: Optional[str] = None,
        report: Optional[dict[str, Any]] = None,
        evidence: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Optional[BriefingRecord]:
        record = self.get_by_id(briefing_id)
        if record is None:
            return None

        if status is not None:
            record.status = status
        if report is not None:
            record.report = report
        if evidence is not None:
            record.evidence = evidence
        if error is not None:
            record.error = error

        self.db.commit()
        self.db.refresh(record)
        return record


class EvidenceRepository:
    """Stored facts per company, plus how far their coverage reaches."""

    def __init__(self, db: Session):
        self.db = db
    def list_for_company(self, company_id: str) -> list[EvidenceFact]:
        stmt = (
            select(EvidenceFact)
            .where(EvidenceFact.company_id == company_id)
            .order_by(EvidenceFact.collected_at.asc())
        )
        return list(self.db.scalars(stmt).all())

    def list_by_kind(self, company_id: str, kind: str) -> list[EvidenceFact]:
        stmt = (
            select(EvidenceFact)
            .where(
                EvidenceFact.company_id == company_id,
                EvidenceFact.kind == kind,
            )
            .order_by(EvidenceFact.collected_at.asc())
        )
        return list(self.db.scalars(stmt).all())

    def coverage_end(self, company_id: str) -> Optional[date]:
        """Return the last date retrieval has actually searched for this company.

        Coverage is recorded when a run finishes, so a day that produced no
        news still counts as covered. If no watermark exists yet, the newest
        stored publication date is used as a best-effort fallback.
        """
        watermark = self.db.get(EvidenceCoverage, company_id)
        if watermark is not None:
            return _to_date(watermark.searched_through)

        stmt = select(EvidenceFact.publish_date).where(
            EvidenceFact.company_id == company_id,
            EvidenceFact.publish_date.is_not(None),
        )
        dates = [value for value in self.db.scalars(stmt).all() if value is not None]
        if not dates:
            return None

        return _to_date(max(dates))

    def record_coverage(self, company_id: str, searched_through: date) -> None:
        """Record how far this run searched, so the next one starts after it."""
        stamp = datetime(
            searched_through.year, searched_through.month, searched_through.day,
            tzinfo=timezone.utc,
        )
        watermark = self.db.get(EvidenceCoverage, company_id)
        if watermark is None:
            self.db.add(
                EvidenceCoverage(company_id=company_id, searched_through=stamp)
            )
        else:
            watermark.searched_through = stamp
        self.db.commit()

    def existing_hashes(self, company_id: str, kind: str = "hard") -> set[str]:
        stmt = select(EvidenceFact.content_hash).where(
            EvidenceFact.company_id == company_id,
            EvidenceFact.kind == kind,
        )
        return set(self.db.scalars(stmt).all())

    def known_urls(self, company_id: str, plan: RetentionPlan | None = None) -> set[str]:
        """Return the source URLs this company has already read.

        Retrieval compares its results against this set, so an article that has
        already been read is not counted as new coverage on a later run.

        Only *active* fingerprints are preloaded. A URL idle past the dormant
        threshold is filtered out here, which is what keeps the hot set
        proportional to recent activity instead of to everything ever seen.
        Fact bodies are always included: they are inside the window by
        construction, because retention deletes the rest.
        """
        retention = plan or build_retention_plan(date.today())

        fact_urls = self.db.scalars(
            select(EvidenceFact.source_url).where(
                EvidenceFact.company_id == company_id
            )
        ).all()

        rows = self.db.scalars(
            select(UrlFingerprint).where(
                UrlFingerprint.company_id == company_id
            )
        ).all()
        hot_urls = [
            row.url_key
            for row in rows
            if not is_hidden_from_hot_set(row.last_seen, retention)
        ]

        return {
            url.strip().rstrip("/")
            for url in list(fact_urls) + hot_urls
            if url
        }

    def was_seen(self, company_id: str, url: str) -> bool:
        """Check one URL against the full history, including hidden fingerprints.

        The hot set deliberately forgets idle URLs; this is the on-demand
        lookup that still answers "have we read this before?" for them.
        """
        key = url.strip().rstrip("/")
        if not key:
            return False

        if self.db.scalars(
            select(EvidenceFact.id).where(
                EvidenceFact.company_id == company_id,
                EvidenceFact.source_url == url,
            )
        ).first():
            return True

        return (
            self.db.scalars(
                select(UrlFingerprint.id).where(
                    UrlFingerprint.company_id == company_id,
                    UrlFingerprint.url_key == key,
                )
            ).first()
            is not None
        )

    def record_urls(self, company_id: str, urls: Iterable[str]) -> int:
        """Remember every URL this run touched, refreshing last_seen.

        A URL seen again gets a new last_seen, which pulls it out of the decay
        states; a new URL starts a fresh lifetime.
        """
        now = datetime.now(timezone.utc)
        keys = {url.strip().rstrip("/") for url in urls if url and url.strip()}
        if not keys:
            return 0

        existing = set(
            self.db.scalars(
                select(UrlFingerprint.url_key).where(
                    UrlFingerprint.company_id == company_id,
                    UrlFingerprint.url_key.in_(keys),
                )
            ).all()
        )

        if existing:
            self.db.execute(
                update(UrlFingerprint)
                .where(
                    UrlFingerprint.company_id == company_id,
                    UrlFingerprint.url_key.in_(existing),
                )
                .values(last_seen=now)
            )

        for key in keys - existing:
            self.db.add(
                UrlFingerprint(
                    company_id=company_id,
                    url_key=key,
                    first_seen=now,
                    last_seen=now,
                )
            )

        self.db.commit()
        return len(keys - existing)

    def apply_retention(self, plan: RetentionPlan) -> dict[str, int]:
        """Delete fact bodies outside the window and decay stale URL identity.

        Returns what was removed so a cleanup run can be logged and audited.
        """
        expired = [
            row
            for row in self.db.scalars(select(EvidenceFact)).all()
            if should_delete_fact(row.publish_date, plan)
        ]
        for row in expired:
            self.db.delete(row)

        fingerprints = self.db.scalars(select(UrlFingerprint)).all()
        purged = [
            row
            for row in fingerprints
            if fingerprint_state(row.last_seen, plan) == "purge"
        ]
        for row in purged:
            self.db.delete(row)

        dormant = sum(
            1
            for row in fingerprints
            if fingerprint_state(row.last_seen, plan) == "dormant"
        )

        self.db.commit()
        return {
            "facts_deleted": len(expired),
            "urls_purged": len(purged),
            "urls_dormant": dormant,
        }

    def add_many(
        self, company_id: str, facts: Iterable[dict], kind: str = "hard"
    ) -> int:
        """Insert unseen facts of one kind, returning how many were actually new."""
        existing = self.existing_hashes(company_id, kind)
        added = 0
        for fact in facts:
            digest = content_hash(fact["content"])
            if digest in existing:
                continue
            existing.add(digest)
            self.db.add(
                EvidenceFact(
                    company_id=company_id,
                    content_hash=digest,
                    content=fact["content"],
                    source_url=fact["source_url"],
                    publish_date=fact.get("publish_date"),
                    kind=kind,
                )
            )
            added += 1

        if added:
            self.db.commit()
        return added
