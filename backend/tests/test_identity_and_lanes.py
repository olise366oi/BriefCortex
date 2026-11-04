"""Tests for chrome filtering, hiring separation, caching and identity."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.cache import CacheEntry, DEFAULT_TTL_HOURS, request_key
from app.services.company_identity import CompanyIdentity, _name_similarity
from app.services.hiring import hiring_queries
from app.services.incidental import is_chrome_line, opening_sentence
from app.services.legal_signals import legal_queries

# The exact openings that routed seven of seven articles away from their real
# subject on a live run.
FAILED_OPENINGS = (
    "Oct 12, 2026, 08:45 ET",
    "| Source: DelveInsight Business Research LLP",
    "CNW Group",
    "| Source: Research and Markets Research and Markets",
)

PRESS_PAGE = """Oct 12, 2026, 08:45 ET

| Source: Zydus Lifesciences Limited

AHMEDABAD, India, Oct. 12, 2026 /PRNewswire/ -- Zydus Lifesciences Limited
(Zydus), an innovation-led global life-sciences company, today announced that
the US Food and Drug Administration has accepted its application.
"""


@pytest.mark.parametrize("line", FAILED_OPENINGS)
def test_page_furniture_is_recognized(line):
    assert is_chrome_line(line) is True


def test_real_prose_is_not_furniture():
    assert is_chrome_line(
        "Zydus Lifesciences Limited today announced FDA acceptance of its application."
    ) is False


def test_opening_skips_furniture_and_keeps_the_dateline():
    opening = opening_sentence(PRESS_PAGE)

    assert opening.startswith("AHMEDABAD")
    assert "Zydus Lifesciences" in opening
    # The wire tag is stripped, so the opening reads as the article.
    assert "PRNewswire" not in opening


def test_opening_is_empty_when_only_furniture_exists():
    assert opening_sentence("Oct 12, 2026, 08:45 ET\n\nCNW Group\n") == ""


def test_opening_returns_empty_for_empty_text():
    assert opening_sentence("") == ""


def test_hiring_queries_cover_careers_and_expansion():
    queries = hiring_queries("Zydus Lifesciences")

    assert any("careers" in query for query in queries)
    assert any("hiring" in query for query in queries)


def test_request_key_is_stable_for_the_same_request():
    moment = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

    first = request_key("Zydus Lifesciences", "", moment)
    second = request_key("Zydus Lifesciences", "", moment)

    assert first == second


def test_request_key_ignores_minutes_within_the_same_day():
    # The evidence window is anchored to the date, so two meetings on one day
    # must share a brief.
    morning = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    afternoon = datetime(2026, 11, 10, 16, 30, tzinfo=timezone.utc)

    assert request_key("Zydus", "", morning) == request_key("Zydus", "", afternoon)


def test_request_key_separates_different_purposes():
    moment = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

    assert request_key("Zydus", "pricing", moment) != request_key("Zydus", "", moment)


def test_request_key_separates_different_days():
    first = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    second = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)

    assert request_key("Zydus", "", first) != request_key("Zydus", "", second)


def test_cache_entry_expires():
    created = datetime.now(timezone.utc) - timedelta(hours=DEFAULT_TTL_HOURS + 1)
    entry = CacheEntry(report={}, evidence=None, created_at=created)

    assert entry.is_valid(datetime.now(timezone.utc)) is False


def test_fresh_cache_entry_is_valid():
    entry = CacheEntry(
        report={}, evidence=None, created_at=datetime.now(timezone.utc)
    )

    assert entry.is_valid(datetime.now(timezone.utc)) is True


def test_short_name_inside_a_long_candidate_scores_lower():
    # "Unicredit Futuro 2031" contains "Unicredit" but is a different entity.
    long_candidate = _name_similarity("Unicredit Futuro 2031", "UniCredit")
    exact = _name_similarity("UNICREDIT SPA", "UniCredit")

    assert exact > long_candidate


def test_legal_requirement_suffixes_are_ignored():
    assert _name_similarity("ZYDUS LIFESCIENCES LIMITED", "Zydus Lifesciences") == 1.0


def test_legal_queries_use_the_jurisdiction_regulator():
    identity = CompanyIdentity(
        name="ZYDUS LIFESCIENCES LIMITED",
        jurisdiction="IN",
        legal_form="DDKQ",
        status="ACTIVE",
        confident=True,
    )

    queries = legal_queries("Zydus Lifesciences", identity)

    assert any("Competition Commission of India" in query for query in queries)


def test_legal_queries_stay_generic_without_a_confident_identity():
    queries = legal_queries("Zydus Lifesciences", None)

    # No guessed country: a wrong regulator name returns another country's news.
    assert not any("SEBI" in query for query in queries)
    assert any("regulator" in query for query in queries)


def test_legal_queries_do_not_trust_an_unconfident_identity():
    identity = CompanyIdentity(
        name="UNICREDIT BB S.R.L.",
        jurisdiction="IT",
        legal_form=None,
        status="ACTIVE",
        confident=False,
    )

    queries = legal_queries("UniCredit", identity)

    assert not any("Consob" in query for query in queries)
