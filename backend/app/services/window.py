# /backend/app/services/window.py
"""Meeting-anchored evidence window and the incremental slice inside it."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

WINDOW_DAYS = 30


def _as_date(value: datetime) -> date:
    """Return the date of a datetime, normalizing to UTC when it carries a zone."""
    if value.tzinfo is None:
        return value.date()
    return value.astimezone(timezone.utc).date()


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


@dataclass(frozen=True)
class EvidenceWindow:
    """The 30 days that end at the meeting, and the part still to fetch.

    The window is anchored to the meeting date rather than to today, because a
    brief is about the 30 days *before the meeting*. A meeting six days out
    therefore already has 24 days of evidence available; the remaining 6 days
    can only be fetched once they have happened.
    """

    window_start: date
    window_end: date
    available_end: date
    fetch_start: date
    fetch_end: date
    needs_fetch: bool
    is_complete: bool

    @property
    def has_gap(self) -> bool:
        """True when part of the window cannot be observed yet."""
        return not self.is_complete

    @property
    def fetch_span_days(self) -> int:
        """How many days this run has to retrieve."""
        if not self.needs_fetch:
            return 0
        return (self.fetch_end - self.fetch_start).days + 1

    @property
    def pending_days(self) -> int:
        """Days between the last observable day and the meeting date."""
        return max((self.window_end - self.available_end).days, 0)


def compute_evidence_window(
    meeting_time: datetime,
    *,
    today: date | None = None,
    coverage_end: date | None = None,
) -> EvidenceWindow:
    """Define the window for a meeting and the slice that still needs fetching.

    ``coverage_end`` is the last date already searched for this company; it
    makes a re-run fetch only the increment instead of the whole window. When
    nothing is covered yet, the whole observable part of the window is fetched.
    """
    meeting_date = _as_date(meeting_time)
    today_date = today or today_utc()

    # The window holds WINDOW_DAYS calendar dates ending on the meeting day, so
    # a meeting six days out leaves exactly 24 observable dates.
    window_end = meeting_date
    window_start = meeting_date - timedelta(days=WINDOW_DAYS - 1)

    # Evidence cannot exist after today, and the brief never looks past the
    # meeting, so the observable end is whichever comes first.
    available_end = min(today_date, meeting_date)

    fetch_start = window_start
    if coverage_end is not None:
        fetch_start = max(window_start, coverage_end + timedelta(days=1))

    # Coverage may already reach the observable end. In that case there is
    # nothing to retrieve, so the range collapses onto a single date and the
    # caller skips searching entirely.
    needs_fetch = fetch_start <= available_end
    if not needs_fetch:
        fetch_start = available_end

    return EvidenceWindow(
        window_start=window_start,
        window_end=window_end,
        available_end=available_end,
        fetch_start=fetch_start,
        fetch_end=available_end,
        needs_fetch=needs_fetch,
        is_complete=available_end >= window_end,
    )
