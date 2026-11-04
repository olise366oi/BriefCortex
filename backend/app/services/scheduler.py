# /backend/app/services/scheduler.py
"""Background maintenance that has to happen without a human asking.

Retention is a policy, not a request: nothing in the briefing path calls it, so
without a loop here the stored evidence would grow forever and the URL decay
bands would never advance.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.services.logger_service import get_logger
from app.services.retention import RETENTION_INTERVAL_HOURS
from app.services.retention_runner import run_retention

logger = get_logger("scheduler")


class RetentionScheduler:
    """Runs the retention policy once at startup, then on an interval.

    The first pass runs immediately so a long-idle deployment cleans up as soon
    as it boots instead of waiting a full interval.
    """

    def __init__(self, interval_hours: float = RETENTION_INTERVAL_HOURS) -> None:
        self._interval_seconds = max(interval_hours, 0.0) * 3600
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()

    async def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await asyncio.to_thread(run_retention)
            except Exception:
                # A failed pass must not kill the loop: the next one retries.
                logger.exception("Retention pass failed")

            try:
                await asyncio.wait_for(self._stopped.wait(), self._interval_seconds)
            except asyncio.TimeoutError:
                continue

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopped.set()
        await asyncio.wait([self._task], timeout=10)
        self._task = None
