# /backend/app/services/llm_client.py
"""Shared OpenAI-compatible client and retry policy.

Every service that talks to the model needs the same three things: which
provider to use, which failures are worth retrying, and a stable place to build
the client. Keeping them here means a new caller does not have to copy any of it.
"""
from __future__ import annotations

from typing import Tuple

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import ValidationError

from app.core.config import get_settings
from app.services.logger_service import get_logger

logger = get_logger("llm")

# The economical DeepSeek tier. DeepSeek exposes only deepseek-flash and
# deepseek-v4-pro; flash is the cheap one, so it is the default.
DEFAULT_MODEL = "deepseek-flash"

# Retrying an auth failure or an unknown model just burns the same window three
# times and hides the real cause, so those fail on the first attempt.
PERMANENT_ERRORS = (
    AuthenticationError,
    PermissionDeniedError,
    NotFoundError,
    BadRequestError,
)


class LLMUnavailableError(RuntimeError):
    """Raised when no provider is configured or the request is rejected outright."""


def is_permanent_error(exc: Exception) -> bool:
    """True when retrying the same request cannot possibly succeed."""
    return isinstance(exc, PERMANENT_ERRORS)


def is_retryable_error(exc: Exception) -> bool:
    """Only transient failures are worth another attempt.

    A schema violation also counts, because the model can correct itself when the
    validation error is fed back in the next turn.
    """
    if isinstance(exc, PERMANENT_ERRORS):
        return False
    return isinstance(
        exc,
        (
            ValidationError,
            ValueError,
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
            InternalServerError,
        ),
    )


def build_client() -> Tuple[AsyncOpenAI, str]:
    """Build an async OpenAI-compatible client from Settings.

    Configuration priority:
    1. Gateway: GATEWAY_BASE_URL — all traffic passes through Bifrost, which
       holds the provider keys and applies caching and logging.
    2. Fallback to DEEPSEEK_API_KEY (base_url set to DeepSeek).
    3. Fallback to OPENAI_API_KEY (default OpenAI base_url, model gpt-4o-mini).
    """
    settings = get_settings()

    if settings.GATEWAY_BASE_URL:
        # The gateway authorises on its own keys, so a placeholder is enough;
        # the real upstream credential never leaves the gateway container.
        model = settings.GATEWAY_MODEL or f"deepseek/{DEFAULT_MODEL}"
        logger.info("LLM client selected | provider=gateway | model=%s", model)
        client = AsyncOpenAI(
            api_key="gateway-managed",
            base_url=settings.GATEWAY_BASE_URL,
        )
        return client, model

    if settings.DEEPSEEK_API_KEY:
        model = settings.DEEPSEEK_MODEL or DEFAULT_MODEL
        logger.info("LLM client selected | provider=deepseek | model=%s", model)
        client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )
        return client, model

    if settings.OPENAI_API_KEY:
        model = settings.OPENAI_MODEL or "gpt-4o-mini"
        logger.info("LLM client selected | provider=openai | model=%s", model)
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        return client, model

    raise LLMUnavailableError(
        "No LLM API key found. Set DEEPSEEK_API_KEY or OPENAI_API_KEY, "
        "or point GATEWAY_BASE_URL at the Bifrost gateway."
    )
