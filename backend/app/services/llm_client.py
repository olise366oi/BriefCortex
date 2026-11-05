# /backend/app/services/llm_client.py
"""Shared OpenAI-compatible client and retry policy.

Every service that talks to the model needs the same three things: which
provider to use, which failures are worth retrying, and a stable place to build
the client. Keeping them here means a new caller does not have to copy any of it.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

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


# One client per (provider, model, key, base_url). Keyed on the key itself so a
# test that swaps credentials gets a client built from the new one rather than a
# stale cached connection.
_CLIENT_CACHE: Dict[tuple, AsyncOpenAI] = {}


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

    The client is cached per (provider, model) because callers build it once per
    article: the same key was being turned into a new connection pool for every
    page and every sentence batch (measured: 100+ selections on one briefing).
    A client is immutable once created, so reuse is safe.
    """
    route, model, api_key, base_url = _resolve_route()
    cache_key = (route, model, api_key, base_url)

    client = _CLIENT_CACHE.get(cache_key)
    if client is not None:
        return client, model

    logger.info("LLM client selected | provider=%s | model=%s", route, model)
    if base_url:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    else:
        client = AsyncOpenAI(api_key=api_key)

    _CLIENT_CACHE[cache_key] = client
    return client, model


def _resolve_route() -> Tuple[str, str, str, Optional[str]]:
    """Return (provider, model, api_key, base_url) for the configured provider."""
    settings = get_settings()

    if settings.GATEWAY_BASE_URL:
        # The gateway authorises on its own keys, so a placeholder is enough;
        # the real upstream credential never leaves the gateway container.
        model = settings.GATEWAY_MODEL or f"deepseek/{DEFAULT_MODEL}"
        return "gateway", model, "gateway-managed", settings.GATEWAY_BASE_URL

    if settings.DEEPSEEK_API_KEY:
        model = settings.DEEPSEEK_MODEL or DEFAULT_MODEL
        return "deepseek", model, settings.DEEPSEEK_API_KEY, "https://api.deepseek.com"

    if settings.OPENAI_API_KEY:
        model = settings.OPENAI_MODEL or "gpt-4o-mini"
        return "openai", model, settings.OPENAI_API_KEY, None

    raise LLMUnavailableError(
        "No LLM API key found. Set DEEPSEEK_API_KEY or OPENAI_API_KEY, "
        "or point GATEWAY_BASE_URL at the Bifrost gateway."
    )
