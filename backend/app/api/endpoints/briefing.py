from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.briefing import (
    BriefingAcceptedResponse,
    BriefingListResponse,
    BriefingRequest,
    BriefingStatusResponse,
    EntityCandidateList,
    EntityCandidateRequest,
)
from app.services import briefing_service
from app.services.entity_search import suggest_candidates

router = APIRouter()


@router.post(
    "/candidates",
    response_model=EntityCandidateList,
)
async def list_entity_candidates(
    payload: EntityCandidateRequest,
) -> EntityCandidateList:
    """Return the companies this name might mean, so the caller can confirm one.

    Called after the registry returns several unrelated entities, or none, and
    the caller described their company in their own words. Each candidate carries
    the facts that identify it plus a URL, so the caller confirms an entity
    rather than re-typing a name.
    """
    candidates = await suggest_candidates(
        payload.company_name,
        payload.description,
        payload.rejected,
        payload.round,
    )
    return EntityCandidateList(candidates=candidates)


@router.post(
    "",
    response_model=BriefingAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_briefing(
    request: BriefingRequest,
    db: Session = Depends(get_db),
) -> BriefingAcceptedResponse:
    """Accept a briefing request and process it asynchronously."""
    briefing_id = await briefing_service.create_briefing(db, request)
    return BriefingAcceptedResponse(id=briefing_id, status="accepted")


@router.get("", response_model=BriefingListResponse)
async def list_briefings(db: Session = Depends(get_db)) -> BriefingListResponse:
    """Return all saved briefings, newest first."""
    return briefing_service.list_briefings(db)


@router.get("/{briefing_id}", response_model=BriefingStatusResponse)
async def get_briefing(
    briefing_id: UUID,
    db: Session = Depends(get_db),
) -> BriefingStatusResponse:
    """Fetch a briefing by id (also used for status polling)."""
    return briefing_service.get_briefing(db, briefing_id)
