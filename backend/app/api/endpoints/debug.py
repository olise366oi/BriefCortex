# /backend/app/api/endpoints/debug.py
"""Temporary inspection endpoint for watching a run end to end.

This exists so the pipeline can be observed while it is being built: submit a
company and read the full decision trace — identity, routing, lanes, coverage —
without waiting on the frontend. It is a development tool, not part of the
product surface, and it should be removed once the report view shows the same
detail.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter()

# One run at a time is enough for inspection, and it keeps a stray refresh from
# paying for two retrievals.
_run_lock = asyncio.Lock()


class DebugRunRequest(BaseModel):
    """Input for a debug run."""

    company_name: str
    meeting_purpose: str = ""
    meeting_in_days: int = Field(default=6, ge=-30, le=90)


class DebugFact(BaseModel):
    content: str
    source_url: str
    publish_date: str | None = None
    also_reported_by: List[str] = Field(default_factory=list)


class DebugRunResponse(BaseModel):
    company_name: str
    identity: Dict[str, Any]
    counts: Dict[str, int]
    coverage: Dict[str, Any]
    hard_facts: List[DebugFact]
    opinions: List[DebugFact]
    hiring: List[DebugFact]
    incidental: List[DebugFact]
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    report: Dict[str, Any] | None = None
    timings: Dict[str, float] = Field(default_factory=dict)
    error: str | None = None


def _to_debug_facts(items) -> List[DebugFact]:
    return [
        DebugFact(
            content=item.content,
            source_url=str(item.source_url),
            publish_date=item.publish_date.isoformat() if item.publish_date else None,
            also_reported_by=list(getattr(item, "also_reported_by", []) or []),
        )
        for item in items
    ]


@router.post("/run", response_model=DebugRunResponse)
async def run_debug(payload: DebugRunRequest) -> DebugRunResponse:
    """Run the pipeline once and return everything it decided.

    The trace is returned even when the report step fails, so a retrieval
    problem and a model problem can be told apart.
    """
    from app.models.briefing import BriefingRequest
    from app.services.analysis import generate_briefing_report
    from app.services.company_identity import resolve_identity
    from app.services.ingestion import ingest_company_data

    started = datetime.now(timezone.utc)
    meeting = started + timedelta(days=payload.meeting_in_days)

    async with _run_lock:
        identity = await asyncio.to_thread(resolve_identity, payload.company_name)
        identity_taken = datetime.now(timezone.utc)

        try:
            fact_base = await ingest_company_data(
                BriefingRequest(
                    company_name=payload.company_name,
                    meeting_time=meeting,
                    meeting_purpose=payload.meeting_purpose,
                )
            )
        except Exception as exc:
            return DebugRunResponse(
                company_name=payload.company_name,
                identity=_identity_dict(identity),
                counts={},
                coverage={},
                hard_facts=[],
                opinions=[],
                hiring=[],
                incidental=[],
                error=f"{type(exc).__name__}: {exc}",
                timings={
                    "identity_s": (identity_taken - started).total_seconds(),
                },
            )

        ingested = datetime.now(timezone.utc)

        report: Dict[str, Any] | None = None
        error: str | None = None
        try:
            generated = await generate_briefing_report(
                fact_base=fact_base,
                meeting_purpose=payload.meeting_purpose,
            )
            report = generated.model_dump(mode="json")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        finished = datetime.now(timezone.utc)

    return DebugRunResponse(
        company_name=payload.company_name,
        identity=_identity_dict(identity),
        counts={
            "hard_facts": len(fact_base.hard_facts),
            "opinions": len(fact_base.analyst_opinions),
            "hiring": len(fact_base.hiring),
            "incidental": len(fact_base.incidental_facts),
            "conflicts": len(fact_base.conflicts),
        },
        coverage=fact_base.coverage.model_dump(mode="json"),
        hard_facts=_to_debug_facts(fact_base.hard_facts),
        opinions=_to_debug_facts(fact_base.analyst_opinions),
        hiring=_to_debug_facts(fact_base.hiring),
        incidental=_to_debug_facts(fact_base.incidental_facts),
        conflicts=list(fact_base.conflicts),
        report=report,
        error=error,
        timings={
            "identity_s": (identity_taken - started).total_seconds(),
            "ingestion_s": (ingested - identity_taken).total_seconds(),
            "analysis_s": (finished - ingested).total_seconds(),
            "total_s": (finished - started).total_seconds(),
        },
    )


def _identity_dict(identity) -> Dict[str, Any]:
    if identity is None:
        return {"resolved": False}
    return {
        "resolved": True,
        "name": identity.name,
        "jurisdiction": identity.jurisdiction,
        "legal_form": identity.legal_form,
        "status": identity.status,
        "confident": identity.confident,
    }
