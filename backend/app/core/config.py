from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved against the module, not the process CWD, so the backend reads the
# same .env whether it is started from the repo root or from backend/.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables and backend/.env."""

    APP_NAME: str = "BriefCortex API"
    DEBUG: bool = True
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000"]
    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/briefcortex"

    # Live retrieval is the default. MOCK_DATA must be switched on explicitly,
    # so a missing .env can never quietly serve the bundled sample report.
    MOCK_DATA: bool = False
    MOCK_PROCESSING_DELAY_SECONDS: float = 2.0
    ENABLE_LOGS: bool = True

    TAVILY_API_KEY: Optional[str] = None
    BRIEFING_API_KEY: Optional[str] = None
    BRIEFING_BASE_URL: Optional[str] = None
    BRIEFING_MODEL: Optional[str] = None
    DEEPSEEK_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def has_llm_credentials(self) -> bool:
        return bool(self.BRIEFING_API_KEY or self.DEEPSEEK_API_KEY or self.OPENAI_API_KEY)

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
