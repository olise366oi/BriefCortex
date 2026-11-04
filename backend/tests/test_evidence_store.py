"""Tests for the evidence store that makes incremental retrieval possible."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.db import models  # noqa: F401  (registers the tables on Base)
from app.repositories.briefing_repository import EvidenceRepository


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _fact(content: str, day: int) -> dict:
    return {
        "content": content,
        "source_url": "https://www.reuters.com/business/nio-earnings",
        "publish_date": datetime(2026, 10, day, tzinfo=timezone.utc),
    }


def test_new_facts_are_stored_and_reported_counted(db):
    repo = EvidenceRepository(db)

    added = repo.add_many(
        "nio-abc",
        [
            _fact("NIO reported revenue of 18.7 billion yuan.", 12),
            _fact("Deliveries reached 61,855 units in the quarter.", 20),
        ],
    )

    assert added == 2
    assert len(repo.list_for_company("nio-abc")) == 2


def test_the_same_sentence_is_stored_only_once(db):
    repo = EvidenceRepository(db)
    fact = _fact("NIO reported revenue of 18.7 billion yuan.", 12)

    first = repo.add_many("nio-abc", [fact])
    second = repo.add_many("nio-abc", [fact])

    assert first == 1
    assert second == 0
    assert len(repo.list_for_company("nio-abc")) == 1


def test_coverage_end_is_the_newest_published_fact(db):
    repo = EvidenceRepository(db)
    repo.add_many(
        "nio-abc",
        [
            _fact("Older fact about the quarter.", 12),
            _fact("Newest fact about the quarter.", 22),
        ],
    )

    assert repo.coverage_end("nio-abc") == date(2026, 10, 22)


def test_coverage_end_ignores_undated_facts(db):
    repo = EvidenceRepository(db)
    repo.add_many(
        "nio-abc",
        [
            {
                "content": "An undated fact about the company.",
                "source_url": "https://www.reuters.com/business/nio",
                "publish_date": None,
            }
        ],
    )

    assert repo.coverage_end("nio-abc") is None


def test_two_companies_keep_separate_evidence(db):
    repo = EvidenceRepository(db)
    repo.add_many("nio-abc", [_fact("NIO revenue and deliveries detail.", 12)])
    repo.add_many("unicredit-def", [_fact("UniCredit revenue and profits detail.", 12)])

    assert len(repo.list_for_company("nio-abc")) == 1
    assert len(repo.list_for_company("unicredit-def")) == 1
    assert repo.coverage_end("nio-abc") == date(2026, 10, 12)


def test_existing_hashes_lists_what_is_already_known(db):
    repo = EvidenceRepository(db)
    repo.add_many("nio-abc", [_fact("NIO revenue and deliveries detail.", 12)])

    hashes = repo.existing_hashes("nio-abc")

    assert len(hashes) == 1
    assert repo.existing_hashes("unknown-company") == set()


def test_a_watermark_covers_days_that_produced_nothing(db):
    # A quiet day yields no fact, so coverage must come from the watermark
    # rather than from the newest stored article.
    repo = EvidenceRepository(db)
    repo.record_coverage("nio-abc", date(2026, 11, 4))

    assert repo.coverage_end("nio-abc") == date(2026, 11, 4)
    assert repo.list_for_company("nio-abc") == []


def test_the_watermark_advances_on_a_later_run(db):
    repo = EvidenceRepository(db)
    repo.record_coverage("nio-abc", date(2026, 11, 4))
    repo.record_coverage("nio-abc", date(2026, 11, 6))

    assert repo.coverage_end("nio-abc") == date(2026, 11, 6)


def test_the_watermark_wins_over_the_newest_article(db):
    repo = EvidenceRepository(db)
    repo.add_many("nio-abc", [_fact("NIO revenue and deliveries detail.", 12)])
    repo.record_coverage("nio-abc", date(2026, 11, 4))

    assert repo.coverage_end("nio-abc") == date(2026, 11, 4)


def test_opinions_and_facts_are_stored_separately(db):
    repo = EvidenceRepository(db)
    repo.add_many("nio-abc", [_fact("NIO revenue and deliveries detail.", 12)], kind="hard")
    repo.add_many(
        "nio-abc",
        [
            {
                "content": "Analysts describe the brand as a design-led challenger.",
                "source_url": "https://www.reuters.com/business/nio",
                "publish_date": datetime(2026, 10, 12, tzinfo=timezone.utc),
            }
        ],
        kind="opinion",
    )

    assert len(repo.list_by_kind("nio-abc", "hard")) == 1
    assert len(repo.list_by_kind("nio-abc", "opinion")) == 1


def test_the_same_sentence_can_exist_as_both_kinds(db):
    # A sentence that survives the fact split must not be duplicated, but a
    # sentence stored as a fact does not block the same text as an opinion row.
    repo = EvidenceRepository(db)
    content = "NIO reported revenue of 18.7 billion yuan."
    repo.add_many("nio-abc", [{"content": content, "source_url": "https://x.com/a"}], kind="hard")
    repo.add_many("nio-abc", [{"content": content, "source_url": "https://x.com/a"}], kind="opinion")

    assert len(repo.list_by_kind("nio-abc", "hard")) == 1
    assert len(repo.list_by_kind("nio-abc", "opinion")) == 1
