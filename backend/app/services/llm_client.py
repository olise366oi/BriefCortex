# /backend/app/services/llm_client.py
"""Shared OpenAI-compatible client and retry policy.

Every service that talks to the model needs the same three things: which
provider to use, which failures are worth retrying, and a stable place to build
the client. Keeping them here means a new caller does not have to copy any of it.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Tuple
from weakref import WeakKeyDictionary

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
from app.services import token_usage
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

# One process-wide cap on in-flight model calls. Each fan-out stage has its own
# bound (dedup 8, subject 6, date recovery 16), but those bounds are per stage:
# once stages run concurrently their peaks add up and the provider sees the sum.
# This is the only cap that sees every caller, and it is what keeps a parallel
# ingestion from turning into a burst of 429s.
MAX_CONCURRENT_LLM_CALLS = int(os.getenv("LLM_MAX_CONCURRENCY", "12"))

_LIMITERS: "WeakKeyDictionary[Any, asyncio.Semaphore]" = WeakKeyDictionary()


def _limiter() -> asyncio.Semaphore:
    """Return the model-call limiter for the running event loop.

    Keyed on the loop rather than created once per process: tests and CLI
    one-shots run several loops in one process, and an asyncio primitive used
    under a loop other than its own fails.
    """
    loop = asyncio.get_running_loop()
    limiter = _LIMITERS.get(loop)
    if limiter is None:
        limiter = asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)
        _LIMITERS[loop] = limiter
    return limiter


@asynccontextmanager
async def llm_slot():
    """Hold one model-call slot for the duration of one request."""
    async with _limiter():
        yield


class _LimitedCompletions:
    """``client.chat.completions`` with every call taking a slot first."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        async with llm_slot():
            response = await self._inner.create(*args, **kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            token_usage.record(prompt_tokens, completion_tokens)
            logger.debug(
                "LLM usage | prompt_tokens=%s | completion_tokens=%s",
                prompt_tokens,
                completion_tokens,
            )
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _LimitedChat:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.completions = _LimitedCompletions(inner.completions)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _LimitedClient:
    """An ``AsyncOpenAI`` whose completions share the process-wide cap.

    Wrapping rather than editing every call site keeps the cap honest: a stage
    added later inherits it automatically, and every existing caller keeps its
    shape.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.chat = _LimitedChat(inner.chat)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


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


def build_client() -> Tuple[Any, str]:
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

    _CLIENT_CACHE[cache_key] = _LimitedClient(client)
    return _CLIENT_CACHE[cache_key], model


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
