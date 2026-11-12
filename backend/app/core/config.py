from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved against the module, not the process CWD, so the backend reads the
# same .env whether it is started from the repo root or from backend/.
#
# A real .env wins; .env.example is the fallback. The example file is committed
# and carries the schema — key names with empty values, never credentials — so a
# clean checkout runs without anyone copying it first. Machine-level environment
# variables still override both, because pydantic-settings reads them last.
ENV_FILES = (
    Path(__file__).resolve().parents[2] / ".env.example",
    Path(__file__).resolve().parents[2] / ".env",
)
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg2://postgres:postgres@localhost:5432/briefcortex"
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables and backend/.env."""

    APP_NAME: str = "BriefCortex API"
    DEBUG: bool = True
    # Next.js picks 3001 when 3000 is taken; both hosts are the same dev machine.
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ]
    DATABASE_URL: str = DEFAULT_DATABASE_URL

    # Live retrieval is the default. MOCK_DATA must be switched on explicitly,
    # so a missing .env can never quietly serve the bundled sample report.
    MOCK_DATA: bool = False
    MOCK_PROCESSING_DELAY_SECONDS: float = 2.0
    ENABLE_LOGS: bool = True

    TAVILY_API_KEY: Optional[str] = None
    DEEPSEEK_API_KEY: Optional[str] = None
    DEEPSEEK_MODEL: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: Optional[str] = None

    # When set, every model call goes through the Bifrost gateway instead of
    # straight to a provider, so caching and logging apply to all of them.
    GATEWAY_BASE_URL: Optional[str] = None
    GATEWAY_MODEL: Optional[str] = None

    @property
    def llm_route(self) -> str:
        """Which path model calls take, for startup logging."""
        if self.GATEWAY_BASE_URL:
            return "gateway"
        if self.DEEPSEEK_API_KEY:
            return "deepseek"
        if self.OPENAI_API_KEY:
            return "openai"
        return "none"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        """Pin a bare ``postgresql://`` URL to the driver that is installed.

        SQLAlchemy 2 resolves the bare scheme to psycopg 3, which needs libpq
        and fails on a machine that only has psycopg2 (measured: "no pq wrapper
        available" at import time). The committed URL is written bare, so it is
        pointed at the psycopg2 driver here instead of forcing every caller to
        rewrite it. An explicit ``+driver`` is always left alone.

        A blank value falls back to the committed default rather than erasing it.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            # A bare ``DATABASE_URL=`` in .env.example declares that the key
            # exists; it does not configure anything. pydantic-settings loaded
            # that empty string as a value, which silently replaced the default
            # above and left SQLAlchemy unable to parse a URL at import time.
            return DEFAULT_DATABASE_URL
        if text.startswith("postgresql://"):
            return "postgresql+psycopg2://" + text[len("postgresql://"):]
        return value

    model_config = SettingsConfigDict(
        # Explicit precedence: env vars > .env > .env.example.
        env_file=[str(path) for path in ENV_FILES],
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def has_llm_credentials(self) -> bool:
        return bool(
            self.GATEWAY_BASE_URL
            or self.DEEPSEEK_API_KEY
            or self.OPENAI_API_KEY
        )

    @property
    def has_search_credentials(self) -> bool:
        return bool(self.TAVILY_API_KEY)

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


@lru_cache()
def get_settings() -> Settings:
    return Settings()
