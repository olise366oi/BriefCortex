# /backend/app/services/cache.py
"""Exact-request cache for finished briefs.

Retrieval and inference cost seconds and real money, so an identical request
should not repeat them. The key is the full request — company, purpose and
meeting time — because a brief is written for one meeting: the same company
with a different objective is a different brief, and a different meeting date
changes the evidence window.

The cache deliberately stores nothing that could be stale beyond its window.
Entries expire, and the underlying evidence window is anchored to the meeting,
so a hit can only serve a request that would have produced the same brief.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

# A finished brief stays valid for this long. Production news moves daily, so
# anything older is re-run rather than served.
DEFAULT_TTL_HOURS = 6


def request_key(
    company_name: str,
    meeting_purpose: str,
    meeting_time: datetime,
) -> str:
    """Build the cache key for one brief request.

    Every field that changes the output is in the key. Meeting time is keyed to
    the day, not the minute: two meetings on the same day share an evidence
    window, so they share a brief.
    """
    normalized = "|".join(
        (
            " ".join(company_name.lower().split()),
            " ".join(meeting_purpose.lower().split()),
            meeting_time.astimezone(timezone.utc).date().isoformat()
            if meeting_time.tzinfo
            else meeting_time.date().isoformat(),
        )
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheEntry:
    """A stored brief and the moment it stops being servable."""

    report: dict
    evidence: dict | None
    created_at: datetime

    def is_valid(self, now: datetime, ttl_hours: int = DEFAULT_TTL_HOURS) -> bool:
        """True when the entry is still inside its TTL."""
        created = self.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        reference = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        age_hours = (reference - created).total_seconds() / 3600
        return age_hours < ttl_hours
