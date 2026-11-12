# /backend/app/services/token_usage.py
"""Token accounting for one briefing run.

The pipeline makes model calls in many stages — planning, entity separation,
subject routing, dedup confirmation, the pre-delivery audit and the three
generation stages. Without a per-run total there is no way to tell which stage is
worth optimising, and "token cost" stays an anecdote.

Usage is collected through a ContextVar, so every task the run creates (the
`asyncio.gather` children included) reports into the same totals, and a run that
never opened a collector (a unit test, a CLI one-shot) is simply a no-op.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Dict, Iterator, Optional

_TOTALS: "ContextVar[Optional[Dict[str, int]]]" = ContextVar(
    "llm_token_totals", default=None
)


@contextmanager
def usage_run() -> Iterator[Dict[str, int]]:
    """Collect token usage for the duration of one briefing run."""
    totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    token = _TOTALS.set(totals)
    try:
        yield totals
    finally:
        _TOTALS.reset(token)


def record(prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """Add one call's usage to the active run, when one is active."""
    totals = _TOTALS.get()
    if totals is None:
        return
    totals["calls"] += 1
    totals["prompt_tokens"] += int(prompt_tokens or 0)
    totals["completion_tokens"] += int(completion_tokens or 0)
