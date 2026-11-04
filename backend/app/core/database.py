from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings
from app.services.logger_service import get_logger

logger = get_logger("database")


class Base(DeclarativeBase):
    pass


settings = get_settings()

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class DatabaseUnavailableError(RuntimeError):
    """Raised when the configured database cannot be reached at startup."""


def _redacted_url() -> str:
    """Render the database URL without credentials so it can be logged safely."""
    return engine.url.render_as_string(hide_password=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_connection() -> None:
    """Fail with an actionable message when the database is unreachable.

    Without this the first request would surface a raw driver traceback, which
    says nothing about the two things a reader actually needs: which URL was
    used, and how to bring the database up.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError(
            f"Cannot connect to the database at {_redacted_url()}.\n"
            "Start one of the following, then retry:\n"
            "  - local Postgres:  docker compose up -d db\n"
            "  - a hosted Postgres: set DATABASE_URL in backend/.env\n"
            f"Underlying error: {type(exc).__name__}: {exc}"
        ) from exc


def init_db() -> None:
    """Verify the database is reachable and its schema is current.

    Schema changes are owned by Alembic, so this deliberately does not create
    or alter tables: doing both would let two sources of truth drift apart.
    The startup check only reports the problem and points at the command that
    fixes it.
    """
    from app.db import models  # noqa: F401

    check_connection()

    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        current = context.get_current_revision()

    from pathlib import Path

    alembic_cfg = Config(
        str(Path(__file__).resolve().parents[2] / "alembic.ini")
    )
    head = alembic_cfg.get_main_option("script_location")
    from alembic.script import ScriptDirectory

    script = ScriptDirectory(head)
    expected = script.get_current_head()

    if current != expected:
        logger.warning(
            "Database schema is at revision %s but the code expects %s. "
            "Run `python -m alembic upgrade head` from backend/.",
            current,
            expected,
        )
