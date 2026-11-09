"""A job that dies because the database went away must still be closed out.

Measured failure: a managed Postgres closed an idle connection during a long
retrieval, the next query raised inside `_generate_live_report`, and the failure
handler could not write the `failed` status either — the session was in a broken
transaction, so every statement raised `PendingRollbackError`. The row stayed in
`processing` and the UI polled a job that would never finish.
"""
from __future__ import annotations

import uuid

from sqlalchemy.exc import PendingRollbackError

from app.services import briefing_service


class _BrokenSession:
    """A session whose transaction is invalid: rollback and writes both fail."""

    def __init__(self, rollback_works: bool = True) -> None:
        self.rolled_back = False
        self._rollback_works = rollback_works

    def rollback(self) -> None:
        if not self._rollback_works:
            raise PendingRollbackError("transaction is invalid")
        self.rolled_back = True

    def close(self) -> None:
        pass


def _stub_repo(should_fail: bool):
    class _Repo:
        def __init__(self, _db) -> None:
            pass

        def update(self, *_args, **_kwargs):
            if should_fail:
                raise PendingRollbackError("can't reconnect until rolled back")
            return object()

    return _Repo


def test_the_broken_session_is_rolled_back_before_the_failure_is_written(monkeypatch):
    # The order matters: rolling back is the only thing that makes the session
    # usable again, so it has to happen before the update that records the failure.
    calls: list[str] = []

    class _Tracking(_BrokenSession):
        def rollback(self) -> None:
            calls.append("rollback")
            super().rollback()

    session = _Tracking()

    class _Repo:
        def __init__(self, db) -> None:
            self.db = db

        def update(self, *_a, **_k):
            calls.append("update")
            return object()

    monkeypatch.setattr(briefing_service, "BriefingRepository", _Repo)

    briefing_service._mark_failed(session, uuid.uuid4())

    assert calls == ["rollback", "update"]


def test_a_session_that_cannot_even_roll_back_falls_back_to_a_fresh_connection(
    monkeypatch,
):
    fresh_sessions: list = []

    def _session_factory():
        class _Fresh:
            def close(self) -> None:
                pass

        fresh = _Fresh()
        fresh_sessions.append(fresh)
        return fresh

    class _Repo:
        def __init__(self, db) -> None:
            self.db = db

        def update(self, *_a, **_k):
            if getattr(self.db, "_is_broken", False):
                raise PendingRollbackError("still broken")
            return object()

    monkeypatch.setattr(briefing_service, "SessionLocal", _session_factory)
    monkeypatch.setattr(briefing_service, "BriefingRepository", _Repo)

    broken = _BrokenSession(rollback_works=False)
    broken._is_broken = True

    briefing_service._mark_failed(broken, uuid.uuid4())

    assert fresh_sessions, "a fresh session should have been opened"


def test_marking_failed_never_raises(monkeypatch):
    # It runs inside the failure path; raising here would mask the original error.
    class _Repo:
        def __init__(self, _db) -> None:
            pass

        def update(self, *_a, **_k):
            raise RuntimeError("no database at all")

    def _session_factory():
        class _Fresh:
            def rollback(self) -> None:
                pass

            def close(self) -> None:
                pass

        return _Fresh()

    monkeypatch.setattr(briefing_service, "BriefingRepository", _Repo)
    monkeypatch.setattr(briefing_service, "SessionLocal", _session_factory)

    # No pytest.raises: getting here without an exception is the assertion.
    briefing_service._mark_failed(_BrokenSession(), uuid.uuid4())


def test_the_database_configuration_tolerates_a_dropped_connection():
    # pool_pre_ping is what turns a silently dead pooled connection into a
    # reconnect; without it the first query after an idle period fails outright.
    from app.core.database import engine

    assert engine.pool._pre_ping is True
