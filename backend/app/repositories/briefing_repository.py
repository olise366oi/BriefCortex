from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BriefingRecord


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

    def update(
        self,
        briefing_id: uuid.UUID,
        *,
        status: Optional[str] = None,
        report: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Optional[BriefingRecord]:
        record = self.get_by_id(briefing_id)
        if record is None:
            return None

        if status is not None:
            record.status = status
        if report is not None:
            record.report = report
        if error is not None:
            record.error = error

        self.db.commit()
        self.db.refresh(record)
        return record
