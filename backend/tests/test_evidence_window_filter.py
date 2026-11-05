"""The evidence window must hold, even for the rounds that search undated.

The open-domain and LinkedIn rounds deliberately run without date bounds, because
the pages they need carry no publish date. That also let genuinely old articles
through (measured: a 2026-08-24 TechCrunch piece inside a 30-day window), which
no downstream check caught.
"""
from __future__ import annotations

from datetime import date

from app.services.ingestion import _drop_out_of_window

START = date(2026, 10, 7)
END = date(2026, 11, 1)


def _result(url: str, published):
    return {"url": url, "published_date": published}


def test_an_article_from_before_the_window_is_dropped():
    results = [_result("https://x/aug", "Mon, 24 Aug 2026 00:00:00 GMT")]

    assert _drop_out_of_window(results, START, END) == []


def test_an_article_from_after_the_window_is_dropped():
    results = [_result("https://x/dec", "Mon, 07 Dec 2026 00:00:00 GMT")]

    assert _drop_out_of_window(results, START, END) == []


def test_an_article_inside_the_window_is_kept():
    results = [_result("https://x/oct", "Wed, 21 Oct 2026 00:00:00 GMT")]

    assert len(_drop_out_of_window(results, START, END)) == 1


def test_an_undated_result_is_kept_because_structural_pages_have_no_date():
    # A regulator's case page and a job posting carry no date. Dropping them
    # would defeat the reason the undated rounds exist.
    results = [_result("https://x/job", None), _result("https://x/nodate", "")]

    assert len(_drop_out_of_window(results, START, END)) == 2


def test_window_boundaries_are_inclusive():
    results = [
        _result("https://x/first", "Wed, 07 Oct 2026 00:00:00 GMT"),
        _result("https://x/last", "Sun, 01 Nov 2026 00:00:00 GMT"),
        _result("https://x/before", "Tue, 06 Oct 2026 00:00:00 GMT"),
        _result("https://x/after", "Mon, 02 Nov 2026 00:00:00 GMT"),
    ]

    kept = _drop_out_of_window(results, START, END)

    assert [item["url"] for item in kept] == ["https://x/first", "https://x/last"]


def test_non_dict_entries_do_not_crash_the_filter():
    assert _drop_out_of_window([None, "junk"], START, END) == []  # type: ignore[list-item]
