from fastapi import APIRouter

from app.api.endpoints.briefing import router as briefing_router

router = APIRouter()
router.include_router(briefing_router, prefix="/briefings", tags=["briefings"])
