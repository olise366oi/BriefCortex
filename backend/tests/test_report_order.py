"""Ordering rule: key events lead, then newest first, undated last."""
from __future__ import annotations

from app.models.briefing import BriefingReport
from app.services.report_order import order_news


def _report(items: list[dict]) -> BriefingReport:
    return BriefingReport.model_validate(
        {
            "executive_summary": {"summary": "s", "recommendation": "r", "confidence": "c"},
            "news": {"items": items},
        }
    )


def _claim(text: str, date: str = "", key: bool = False) -> dict:
    return {
        "claim": text,
        "source_url": "https://x/1",
        "date": date,
        "key_event": key,
    }


def test_key_events_come_before_newer_routine_items():
    # The user's example: in pure date order A,B,C,D,E the middle pair matters,
    # so C and D lead the rest while each group stays newest-first.
    report = _report(
        [
            _claim("A", "2026-10-01"),
            _claim("B", "2026-10-05"),
            _claim("C", "2026-10-09", key=True),
            _claim("D", "2026-10-20", key=True),
            _claim("E", "2026-10-25"),
        ]
    )

    ordered = [item.claim for item in order_news(report).news.items]

    assert ordered == ["D", "C", "E", "B", "A"]


def test_without_key_events_order_is_newest_first():
    report = _report(
        [_claim("old", "2026-10-01"), _claim("new", "2026-10-25")]
    )

    ordered = [item.claim for item in order_news(report).news.items]

    assert ordered == ["new", "old"]


def test_undated_items_sort_after_dated_ones():
    report = _report(
        [_claim("undated"), _claim("dated", "2026-10-10")]
    )

    ordered = [item.claim for item in order_news(report).news.items]

    assert ordered == ["dated", "undated"]


def test_a_key_event_without_a_date_still_leads_routine_items():
    report = _report(
        [_claim("routine", "2026-10-25"), _claim("filing", key=True)]
    )

    ordered = [item.claim for item in order_news(report).news.items]

    assert ordered == ["filing", "routine"]
