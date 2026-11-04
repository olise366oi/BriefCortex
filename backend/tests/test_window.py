"""Tests for the meeting-anchored window and the incremental slice."""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.services.window import compute_evidence_window

MEETING = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 4)


def test_window_spans_thirty_dates_ending_at_meeting():
    window = compute_evidence_window(MEETING, today=TODAY)

    assert (window.window_end - window.window_start).days + 1 == 30
    assert window.window_end == date(2026, 11, 10)
    assert window.window_start == date(2026, 10, 12)


def test_upcoming_meeting_only_fetches_observable_days():
    # A meeting six days out has 24 days of evidence available today.
    window = compute_evidence_window(MEETING, today=TODAY)

    assert window.fetch_end == TODAY
    assert window.fetch_span_days == 24
    assert window.pending_days == 6
    assert window.is_complete is False
    assert window.has_gap is True


def test_past_meeting_covers_the_whole_window():
    window = compute_evidence_window(
        datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc), today=TODAY
    )

    assert window.is_complete is True
    assert window.has_gap is False
    assert window.pending_days == 0
    assert window.fetch_start == window.window_start
    assert window.fetch_end == window.window_end


def test_stored_coverage_shrinks_the_fetch_to_the_increment():
    window = compute_evidence_window(
        MEETING, today=TODAY, coverage_end=date(2026, 11, 2)
    )

    assert window.fetch_start == date(2026, 11, 3)
    assert window.fetch_end == date(2026, 11, 4)
    assert window.fetch_span_days == 2


def test_coverage_older_than_the_window_falls_back_to_the_window_start():
    window = compute_evidence_window(
        MEETING, today=TODAY, coverage_end=date(2026, 1, 1)
    )

    assert window.fetch_start == window.window_start


def test_coverage_beyond_today_fetches_nothing_new():
    window = compute_evidence_window(
        MEETING, today=TODAY, coverage_end=TODAY
    )

    assert window.needs_fetch is False
    assert window.fetch_span_days == 0
    assert window.fetch_start == window.fetch_end == TODAY


def test_a_completed_fetch_never_produces_a_reversed_range():
    # Coverage one day past the observable end must not yield start > end.
    window = compute_evidence_window(
        MEETING, today=TODAY, coverage_end=date(2026, 11, 6)
    )

    assert window.needs_fetch is False
    assert window.fetch_start <= window.fetch_end


def test_a_meeting_beyond_the_window_still_fetches_recent_days():
    # A meeting 30 days out has a window that starts in the future. Returning
    # nothing would make a covered company look uncovered, so the latest 30
    # days are read instead and flagged preliminary.
    far = datetime(2026, 12, 4, 9, 0, tzinfo=timezone.utc)

    window = compute_evidence_window(far, today=TODAY)

    assert window.needs_fetch is True
    assert window.preliminary is True
    assert window.fetch_end == TODAY
    assert window.fetch_span_days == 30


def test_a_meeting_just_inside_the_window_is_not_preliminary():
    inside = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

    window = compute_evidence_window(inside, today=TODAY)

    assert window.needs_fetch is True
    assert window.preliminary is False
    assert window.fetch_span_days == 24


def test_a_preliminary_window_still_respects_stored_coverage():
    far = datetime(2026, 12, 4, 9, 0, tzinfo=timezone.utc)

    window = compute_evidence_window(
        far, today=TODAY, coverage_end=date(2026, 11, 2)
    )

    assert window.preliminary is True
    assert window.fetch_start == date(2026, 11, 3)


def test_naive_meeting_time_is_accepted():
    window = compute_evidence_window(datetime(2026, 11, 10, 9, 0), today=TODAY)

    assert window.window_end == date(2026, 11, 10)
    assert window.fetch_span_days == 24
