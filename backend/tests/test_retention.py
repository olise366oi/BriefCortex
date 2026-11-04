"""Tests for the retention policy and the URL fingerprint store."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.db import models  # noqa: F401
from app.repositories.briefing_repository import EvidenceRepository
from app.services.retention import (
    STATE_ACTIVE,
    STATE_DORMANT,
    STATE_PURGE,
    build_retention_plan,
    fingerprint_state,
    should_delete_fact,
)

TODAY = date(2026, 11, 4)
NOW = datetime.now(timezone.utc)


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


def test_fact_cutoff_is_the_start_of_the_window():
    plan = build_retention_plan(TODAY)
    assert plan.fact_cutoff == date(2026, 10, 6)


def test_fingerprint_decay_bands():
    plan = build_retention_plan(TODAY)

    assert fingerprint_state(NOW - timedelta(days=1), plan) == STATE_ACTIVE
    assert fingerprint_state(NOW - timedelta(days=45), plan) == STATE_ACTIVE
    assert fingerprint_state(NOW - timedelta(days=70), plan) == STATE_DORMANT
    assert fingerprint_state(NOW - timedelta(days=95), plan) == STATE_PURGE


def test_facts_inside_the_window_are_kept():
    plan = build_retention_plan(TODAY)
    assert should_delete_fact(NOW - timedelta(days=10), plan) is False


def test_facts_outside_the_window_are_deleted():
    plan = build_retention_plan(TODAY)
    assert should_delete_fact(NOW - timedelta(days=40), plan) is True


def test_undated_facts_are_never_deleted():
    plan = build_retention_plan(TODAY)
    assert should_delete_fact(None, plan) is False


def _fact(company_id: str, content: str, days_old: int, kind: str = "hard") -> dict:
    return {
        "content": content,
        "source_url": "https://www.reuters.com/business/unicredit",
        "publish_date": NOW - timedelta(days=days_old),
        "kind": kind,
    }


def test_retention_deletes_expired_bodies_and_keeps_fresh_ones(db):
    repo = EvidenceRepository(db)
    repo.add_many("unicredit-abc", [_fact("unicredit-abc", "Old body text.", 40)])
    repo.add_many("unicredit-abc", [_fact("unicredit-abc", "Fresh body text.", 5)])

    result = repo.apply_retention(build_retention_plan(TODAY))

    assert result["facts_deleted"] == 1
    remaining = [row.content for row in repo.list_by_kind("unicredit-abc", "hard")]
    assert remaining == ["Fresh body text."]


def test_url_identity_survives_a_purged_body(db):
    repo = EvidenceRepository(db)
    repo.record_urls("unicredit-abc", ["https://www.reuters.com/business/unicredit"])
    repo.add_many("unicredit-abc", [_fact("unicredit-abc", "Old body text.", 40)])

    repo.apply_retention(build_retention_plan(TODAY))

    # The body is gone but the company still remembers reading that URL.
    assert repo.list_by_kind("unicredit-abc", "hard") == []
    assert "https://www.reuters.com/business/unicredit" in repo.known_urls(
        "unicredit-abc"
    )


def test_uris_idle_for_over_ninety_days_are_purged(db):
    repo = EvidenceRepository(db)
    repo.record_urls("unicredit-abc", ["https://www.reuters.com/old-article"])

    from app.db.models import UrlFingerprint
    from sqlalchemy import select

    row = db.scalars(select(UrlFingerprint)).one()
    row.last_seen = NOW - timedelta(days=95)
    db.commit()

    result = repo.apply_retention(build_retention_plan(TODAY))

    assert result["urls_purged"] == 1
    assert repo.known_urls("unicredit-abc") == set()


def test_record_urls_refreshes_last_seen(db):
    repo = EvidenceRepository(db)
    repo.record_urls("unicredit-abc", ["https://www.reuters.com/a"])

    from app.db.models import UrlFingerprint
    from sqlalchemy import select

    row = db.scalars(select(UrlFingerprint)).one()
    row.last_seen = NOW - timedelta(days=80)
    db.commit()

    repo.record_urls("unicredit-abc", ["https://www.reuters.com/a"])

    refreshed = db.scalars(select(UrlFingerprint)).one()
    assert fingerprint_state(refreshed.last_seen, build_retention_plan(TODAY)) == STATE_ACTIVE


def test_record_urls_is_idempotent(db):
    repo = EvidenceRepository(db)
    first = repo.record_urls("unicredit-abc", ["https://www.reuters.com/a"])
    second = repo.record_urls("unicredit-abc", ["https://www.reuters.com/a"])

    assert first == 1
    assert second == 0


def test_dormant_url_is_hidden_from_the_hot_set(db):
    repo = EvidenceRepository(db)
    repo.record_urls("unicredit-abc", ["https://www.reuters.com/dormant-article"])

    from app.db.models import UrlFingerprint
    from sqlalchemy import select

    row = db.scalars(select(UrlFingerprint)).one()
    row.last_seen = NOW - timedelta(days=70)
    db.commit()

    plan = build_retention_plan(TODAY)
    # Hidden from the per-run hot set, so retrieval stops preloading it...
    assert "https://www.reuters.com/dormant-article" not in repo.known_urls(
        "unicredit-abc", plan
    )
    # ...but the on-demand lookup still answers correctly.
    assert repo.was_seen("unicredit-abc", "https://www.reuters.com/dormant-article")


def test_active_url_stays_in_the_hot_set(db):
    repo = EvidenceRepository(db)
    repo.record_urls("unicredit-abc", ["https://www.reuters.com/fresh-article"])

    plan = build_retention_plan(TODAY)
    assert "https://www.reuters.com/fresh-article" in repo.known_urls(
        "unicredit-abc", plan
    )


def test_returning_url_becomes_active_again(db):
    repo = EvidenceRepository(db)
    url = "https://www.reuters.com/returning-article"
    repo.record_urls("unicredit-abc", [url])

    from app.db.models import UrlFingerprint
    from sqlalchemy import select

    row = db.scalars(select(UrlFingerprint)).one()
    row.last_seen = NOW - timedelta(days=75)
    db.commit()

    plan = build_retention_plan(TODAY)
    assert url not in repo.known_urls("unicredit-abc", plan)

    # Seeing it again pulls it straight back into the hot set.
    repo.record_urls("unicredit-abc", [url])
    assert url in repo.known_urls("unicredit-abc", plan)


def test_was_seen_is_false_for_an_unknown_url(db):
    repo = EvidenceRepository(db)
    repo.record_urls("unicredit-abc", ["https://www.reuters.com/known"])

    assert repo.was_seen("unicredit-abc", "https://www.reuters.com/known")
    assert not repo.was_seen("unicredit-abc", "https://www.reuters.com/unknown")
    assert not repo.was_seen("other-company", "https://www.reuters.com/known")
