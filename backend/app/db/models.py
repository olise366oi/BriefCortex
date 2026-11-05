from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Postgres keeps JSONB; other backends (used by the test suite) fall back to
# generic JSON so the same models can be created anywhere.
JsonColumn = JSON().with_variant(JSONB(), "postgresql")


class BriefingRecord(Base):
    __tablename__ = "briefings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    meeting_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meeting_purpose: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    report: Mapped[Optional[Dict[str, Any]]] = mapped_column(JsonColumn, nullable=True)
    # Provenance stored beside the report: the conflicts the engine found and
    # the commentary the fact split withheld from the model.
    evidence: Mapped[Optional[Dict[str, Any]]] = mapped_column(JsonColumn, nullable=True)
    # Digest of the evidence this brief was built from. A later request whose
    # facts match can reuse this report even if its meeting date differs, which
    # the calendar-keyed cache could not do.
    evidence_fingerprint: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class EvidenceFact(Base):
    """One collected fact, kept so the next run can fetch only the increment.

    Facts outlive a single request: they belong to a company, not to a report
    or a meeting. A second briefing for the same company therefore reuses what
    was already retrieved and searches only the days after the stored coverage.
    """

    __tablename__ = "evidence_facts"
    __table_args__ = (
        # The same sentence collected twice is stored once, whatever kind it is.
        UniqueConstraint(
            "company_id", "kind", "content_hash", name="uq_evidence_company_kind_content"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    company_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    publish_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # "hard" or "opinion". Commentary is stored too, so a re-run that skips
    # retrieval still reproduces the full fact base instead of losing it.
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="hard")
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )


class EvidenceCoverage(Base):
    """How far retrieval has actually searched a company, independent of hits.

    The newest stored article says nothing about a day that produced no news, so
    coverage is tracked separately: without it a quiet day would be searched
    again on every run.
    """

    __tablename__ = "evidence_coverage"

    company_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    searched_through: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )


class UrlFingerprint(Base):
    """A URL this company has already been searched against, without its body.

    Fact bodies expire from the window, but the knowledge that a URL was already
    read must not: otherwise the next run pays to fetch the same article again.
    Only the identity is kept, so this table stays small and can outlive the
    content it once described.

    States decay with idle time:
      active    -> last seen within 60 days, used for de-duplication
      dormant   -> 60 to 90 days idle, still resolvable but not loaded eagerly
      purged    -> over 90 days idle, the row is removed
    """

    __tablename__ = "url_fingerprints"
    __table_args__ = (
        UniqueConstraint("company_id", "url_key", name="uq_url_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # Bucketed company identity (see company_key) so lookups stay a single index
    # hit rather than a scan across every company.
    company_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    url_key: Mapped[str] = mapped_column(String(512), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

