from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import router
from app.core.config import get_settings
from app.core.database import init_db
from app.services.logger_service import configure_logging, get_logger

settings = get_settings()
configure_logging()
logger = get_logger("main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(force=True)
    logger.info(
        "Starting %s | MOCK_DATA=%s | ENABLE_LOGS=%s",
        settings.APP_NAME,
        settings.MOCK_DATA,
        settings.ENABLE_LOGS,
    )
    init_db()
    yield


app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}
