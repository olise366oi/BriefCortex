from fastapi import APIRouter, status

from app.models.briefing import (
    BriefingAcceptedResponse,
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
async def create_briefing(request: BriefingRequest) -> BriefingAcceptedResponse:
    """Accept a briefing request and process it asynchronously."""
    request_id = await briefing_service.create_briefing(request)
    return BriefingAcceptedResponse(request_id=request_id, status="accepted")


@router.get("/{request_id}", response_model=BriefingStatusResponse)
async def get_briefing(request_id: str) -> BriefingStatusResponse:
    """Poll the status of an accepted briefing request."""
    return briefing_service.get_briefing_status(request_id)
