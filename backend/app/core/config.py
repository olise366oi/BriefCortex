from __future__ import annotations

import os
from functools import lru_cache
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Make .env values visible to os.getenv() used by ingestion/analysis.
load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    APP_NAME: str = "BriefCortex API"
    DEBUG: bool = True
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000"]
    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/briefcortex"

    # MOCK_DATA=TRUE
    MOCK_DATA: bool = True
    MOCK_PROCESSING_DELAY_SECONDS: float = 2.0
    ENABLE_LOGS: bool = True

    TAVILY_API_KEY: Optional[str] = None
    BRIEFING_API_KEY: Optional[str] = None
    BRIEFING_BASE_URL: Optional[str] = None
    BRIEFING_MODEL: Optional[str] = None
    DEEPSEEK_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    def export_pipeline_env(self) -> None:
        """Ensure pipeline modules that read os.getenv see configured keys."""
        for key, value in {
            "TAVILY_API_KEY": self.TAVILY_API_KEY,
            "BRIEFING_API_KEY": self.BRIEFING_API_KEY,
            "BRIEFING_BASE_URL": self.BRIEFING_BASE_URL,
            "BRIEFING_MODEL": self.BRIEFING_MODEL,
            "DEEPSEEK_API_KEY": self.DEEPSEEK_API_KEY,
            "OPENAI_API_KEY": self.OPENAI_API_KEY,
        }.items():
            if value and not os.getenv(key):
                os.environ[key] = value

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                import json

                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


@lru_cache()
def get_settings() -> Settings:
    return Settings()
