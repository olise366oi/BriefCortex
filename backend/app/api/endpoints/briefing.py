from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.briefing import (
    BriefingAcceptedResponse,
    BriefingListResponse,
    BriefingRequest,
    BriefingStatusResponse,
)
from app.services import briefing_service

router = APIRouter()


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
