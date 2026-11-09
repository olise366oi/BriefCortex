"""Reused evidence must not shrink because a publication date was unrecoverable.

Measured failure: a company whose facts were 74% undated lost 16 of its 35
stored facts on the next run, because the reuse filter dropped anything without
a date. That turned the evidence store lossy — each run quietly discarded facts
it had already paid to collect, and the only visible symptom was a smaller
number under a "strong coverage" label.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.fact_base import FactItem
from app.services.ingestion import _merge_reused_facts


def _fact(content: str, date=None) -> FactItem:
    return FactItem(content=content, source_url="https://x/1", publish_date=date)


def test_an_undated_stored_fact_survives_the_merge():
    stored = [_fact("NIO cut guidance for the year.")]
    fresh = [_fact("NIO opened a store in Tokyo.", datetime.now(timezone.utc))]

    merged = _merge_reused_facts(stored, fresh)

    assert len(merged) == 2
    assert "cut guidance" in merged[0].content


def test_stored_facts_come_first_so_a_run_that_fetched_nothing_still_reads():
    stored = [_fact("NIO announced layoffs.")]
    fresh: list[FactItem] = []

    merged = _merge_reused_facts(stored, fresh)

    assert merged == stored


def test_an_exact_duplicate_keeps_the_stored_entry():
    stored = [_fact("NIO reported Q3 earnings.")]
    fresh = [_fact("NIO reported Q3 earnings.", datetime.now(timezone.utc))]

    merged = _merge_reused_facts(stored, fresh)

    assert len(merged) == 1
    assert merged[0] is stored[0]


def test_a_fresh_telling_that_extends_a_stored_one_replaces_it():
    # find_prefix_twin requires the stored sentence to be at least 80 characters,
    # so a short sentence is two facts rather than one extended telling.
    stored = [
        _fact(
            "NIO reported a net loss of C$263 million for the quarter ended "
            "September 27, 2026, compared with a loss of C$174 million a year earlier."
        )
    ]
    fresh = [
        _fact(
            "NIO reported a net loss of C$263 million for the quarter ended "
            "September 27, 2026, compared with a loss of C$174 million a year "
            "earlier, as higher costs weighed on results.",
            datetime.now(timezone.utc),
        )
    ]

    merged = _merge_reused_facts(stored, fresh)

    assert len(merged) == 1
    assert "higher costs" in merged[0].content


def test_an_undated_fact_and_a_dated_one_both_survive():
    stored = [
        _fact("NIO's margin held at 69.9%."),
        _fact("NIO opened five stores.", datetime.now(timezone.utc)),
    ]
    fresh = [_fact("A plant closure was announced.", datetime.now(timezone.utc))]

    merged = _merge_reused_facts(stored, fresh)

    assert len(merged) == 3
