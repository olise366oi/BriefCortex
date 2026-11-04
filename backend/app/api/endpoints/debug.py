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
from pydantic import BaseModel, Field, model_validator

router = APIRouter()

# One run at a time is enough for inspection, and it keeps a stray refresh from
# paying for two retrievals.
_run_lock = asyncio.Lock()

DEFAULT_MEETING_DAYS = 6
MAX_MEETING_DAYS = 365


class DebugRunRequest(BaseModel):
    """Input for a debug run.

    The caller states either the meeting date or how far ahead it is; it never
    computes a date window itself. ``meeting_time`` is what the product
    receives, so a caller holding that value passes it straight through.
    ``meeting_in_days`` is a convenience for a console where nobody wants to
    pick a date. Supplying both is rejected: the two would disagree silently.
    """

    company_name: str
    meeting_purpose: str = ""
    meeting_time: datetime | None = None
    meeting_in_days: int | None = Field(default=None, ge=-30, le=MAX_MEETING_DAYS)
    # Set once the caller has chosen between ambiguous identity candidates.
    jurisdiction: str | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def resolve_meeting_time(self) -> "DebugRunRequest":
        if self.meeting_time is not None and self.meeting_in_days is not None:
            raise ValueError(
                "Provide either meeting_time or meeting_in_days, not both."
            )
        if self.meeting_time is None:
            days = (
                self.meeting_in_days
                if self.meeting_in_days is not None
                else DEFAULT_MEETING_DAYS
            )
            object.__setattr__(
                self,
                "meeting_time",
                datetime.now(timezone.utc) + timedelta(days=days),
            )
        return self


class DebugFact(BaseModel):
    content: str
    source_url: str
    publish_date: str | None = None
    also_reported_by: List[str] = Field(default_factory=list)


class DebugIdentityCandidate(BaseModel):
    """One entity the registry returned, so a caller can offer a choice."""

    name: str
    jurisdiction: str | None = None
    legal_form: str | None = None
    status: str | None = None
    confidence: float = 0.0


class DebugRunResponse(BaseModel):
    company_name: str
    identity: Dict[str, Any]
    identity_candidates: List[DebugIdentityCandidate] = Field(default_factory=list)
    needs_disambiguation: bool = False
    counts: Dict[str, int]
    coverage: Dict[str, Any]
    window: Dict[str, Any] = Field(default_factory=dict)
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


@router.post("/run", response_model=DebugRunResponse)
async def run_debug(payload: DebugRunRequest) -> DebugRunResponse:
    """Run the pipeline once and return everything it decided.

    The trace is returned even when the report step fails, so a retrieval
    problem and a model problem can be told apart. Identity candidates are
    always included: when the name matches more than one registered entity the
    caller should choose rather than accept a guess.
    """
    from app.models.briefing import BriefingRequest
    from app.services.analysis import generate_briefing_report
    from app.services.company_identity import needs_caller_choice, rank_candidates
    from app.services.ingestion import ingest_company_data
    from app.services.window import compute_evidence_window

    started = datetime.now(timezone.utc)
    meeting = payload.meeting_time or (started + timedelta(days=DEFAULT_MEETING_DAYS))

    async with _run_lock:
        candidates = await asyncio.to_thread(rank_candidates, payload.company_name)
        ambiguous = needs_caller_choice(payload.company_name, candidates)

        window = compute_evidence_window(meeting)
        window_info = {
            "window_start": window.window_start.isoformat(),
            "window_end": window.window_end.isoformat(),
            "available_end": window.available_end.isoformat(),
            "fetch_start": window.fetch_start.isoformat(),
            "fetch_end": window.fetch_end.isoformat(),
            "fetch_span_days": window.fetch_span_days,
            "pending_days": window.pending_days,
            "is_complete": window.is_complete,
            "preliminary": window.preliminary,
        }

        try:
            fact_base = await ingest_company_data(
                BriefingRequest(
                    company_name=payload.company_name,
                    meeting_time=meeting,
                    meeting_purpose=payload.meeting_purpose,
                ),
                preferred_jurisdiction=payload.jurisdiction,
            )
        except Exception as exc:
            return DebugRunResponse(
                company_name=payload.company_name,
                identity={"resolved": False},
                identity_candidates=[
                    DebugIdentityCandidate(
                        name=candidate.name,
                        jurisdiction=candidate.jurisdiction,
                        legal_form=candidate.legal_form,
                        status=candidate.status,
                        confidence=candidate.confidence,
                    )
                    for candidate in candidates
                ],
                needs_disambiguation=ambiguous,
                counts={},
                coverage={},
                window=window_info,
                hard_facts=[],
                opinions=[],
                hiring=[],
                incidental=[],
                error=f"{type(exc).__name__}: {exc}",
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
        identity=_identity_dict_from_fact_base(fact_base),
        identity_candidates=[
            DebugIdentityCandidate(
                name=candidate.name,
                jurisdiction=candidate.jurisdiction,
                legal_form=candidate.legal_form,
                status=candidate.status,
                confidence=candidate.confidence,
            )
            for candidate in candidates
        ],
        needs_disambiguation=ambiguous,
        counts={
            "hard_facts": len(fact_base.hard_facts),
            "opinions": len(fact_base.analyst_opinions),
            "hiring": len(fact_base.hiring),
            "incidental": len(fact_base.incidental_facts),
            "conflicts": len(fact_base.conflicts),
        },
        coverage=fact_base.coverage.model_dump(mode="json"),
        window=window_info,
        hard_facts=_to_debug_facts(fact_base.hard_facts),
        opinions=_to_debug_facts(fact_base.analyst_opinions),
        hiring=_to_debug_facts(fact_base.hiring),
        incidental=_to_debug_facts(fact_base.incidental_facts),
        conflicts=list(fact_base.conflicts),
        report=report,
        error=error,
        timings={
            "ingestion_s": (ingested - started).total_seconds(),
            "analysis_s": (finished - ingested).total_seconds(),
            "total_s": (finished - started).total_seconds(),
        },
    )


def _identity_dict_from_fact_base(fact_base) -> Dict[str, Any]:
    """Report the identity the run actually used."""
    coverage_notes = fact_base.coverage.notes
    resolved = not any("could not be matched" in note for note in coverage_notes)
    return {
        "resolved": resolved,
        "unresolved_reason": None if resolved else "no confident registry match",
    }
