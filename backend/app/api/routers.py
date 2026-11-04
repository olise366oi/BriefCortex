from fastapi import APIRouter

from app.api.endpoints.briefing import router as briefing_router
from app.api.endpoints.debug import router as debug_router
from app.core.config import get_settings

router = APIRouter()
router.include_router(briefing_router, prefix="/briefings", tags=["briefings"])

# The debug console is a development tool: it exposes whole pipeline internals
# and bypasses the job queue, so it is not mounted when the service runs
# against sample data or outside debug mode.
if get_settings().DEBUG:
    router.include_router(debug_router, prefix="/debug", tags=["debug"])
