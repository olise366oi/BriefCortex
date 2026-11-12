from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import router
from app.core.config import ENV_FILE, get_settings
from app.core.database import DatabaseUnavailableError, init_db
from app.services import briefing_service
from app.services.logger_service import configure_logging, get_logger
from app.services.scheduler import RetentionScheduler

settings = get_settings()
configure_logging()
logger = get_logger("main")

retention_scheduler = RetentionScheduler()


def _log_startup_configuration() -> None:
    """Report the mode the pipeline will actually run in.

    The mock path returns the bundled sample report for any company, so the
    mode is logged loudly rather than left implicit in the environment.
    """
    logger.info(
        "Starting %s | MOCK_DATA=%s | ENABLE_LOGS=%s",
        settings.APP_NAME,
        settings.MOCK_DATA,
        settings.ENABLE_LOGS,
    )

    if not settings.DEBUG:
        # The demo ships no authentication. Saying so at startup is cheaper than
        # discovering it after the service has been exposed.
        logger.warning(
            "No authentication is enforced by this application. Keep it behind an "
            "authenticating reverse proxy and never expose it to an untrusted network."
        )

    if settings.MOCK_DATA:
        logger.warning(
            "MOCK_DATA is ON: every briefing returns the bundled sample report "
            "regardless of the requested company. Set MOCK_DATA=false in %s "
            "to run the live retrieval pipeline.",
            ENV_FILE,
        )
        return

    missing = [
        label
        for label, present in (
            ("TAVILY_API_KEY", settings.has_search_credentials),
            (
                "an LLM route (GATEWAY_BASE_URL, DEEPSEEK_API_KEY or OPENAI_API_KEY)",
                settings.has_llm_credentials,
            ),
        )
        if not present
    ]
    if missing:
        logger.error(
            "Live pipeline selected but credentials are missing: %s. "
            "Briefings will fail until %s is configured.",
            ", ".join(missing),
            ENV_FILE,
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(force=True)
    _log_startup_configuration()
    try:
        init_db()
    except DatabaseUnavailableError as exc:
        logger.error("%s", exc)
        raise
    await briefing_service.recover_stale_jobs()
    retention_scheduler.start()
    yield
    await retention_scheduler.stop()
    await briefing_service.wait_for_jobs()


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
