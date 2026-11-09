"""Tests for chrome filtering, hiring separation, caching and identity."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.core.database import SessionLocal
from app.repositories.briefing_repository import BriefingRepository
from app.services.cache import DEFAULT_TTL_HOURS
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


def test_the_request_cache_matches_on_company_purpose_and_day():
    """The request cache matches on fields, so it lives in the repository.

    These cases used to exercise `cache.request_key`, a hash that nothing called:
    the repository compares company / purpose / meeting-day columns directly. The
    behaviour they describe is still required, so they now exercise what enforces
    it.
    """
    db = SessionLocal()
    try:
        repo = BriefingRepository(db)
        moment = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
        repo.create(
            company_name="Zydus Probe",
            meeting_time=moment,
            meeting_purpose="pricing",
            status="completed",
        )
        db.commit()

        # Same company, same purpose, same day → matches.
        assert repo.find_cached("Zydus Probe", "pricing", moment) is not None

        # A different objective is a different brief.
        assert repo.find_cached("Zydus Probe", "renewal", moment) is None

        # A different day is a different window.
        other = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
        assert repo.find_cached("Zydus Probe", "pricing", other) is None

        # Minutes inside one day share the window.
        later = datetime(2026, 11, 10, 16, 30, tzinfo=timezone.utc)
        assert repo.find_cached("Zydus Probe", "pricing", later) is not None
    finally:
        db.execute(text("delete from briefings where company_name = 'Zydus Probe'"))
        db.commit()
        db.close()


def test_the_cache_ttl_is_a_single_named_constant():
    # The TTL is the one thing `services/cache.py` still owns; the lookup moved
    # to the repository.
    assert DEFAULT_TTL_HOURS > 0


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
