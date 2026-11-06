# /backend/app/services/retention.py
"""Retention policy for stored evidence.

Two different things decay on two different clocks:

* **Fact bodies** are only useful inside the briefing window, so they are deleted
  once they fall out of it.
* **URL identity** is useful far longer, because it is what stops the next run
  from paying to re-read the same article. It decays in three bands instead of
  disappearing, so the hot working set stays small without losing correctness.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

WINDOW_DAYS = 30

# The caller's own company is kept longer than a counterparty's evidence: its
# market position is the context every deal is argued against, so a 60-day history
# is more useful than a 30-day one. Nothing is filtered by sentiment — positive and
# negative coverage are both kept, because a company that only remembers its good
# news cannot be assessed.
OWN_COMPANY_RETENTION_DAYS = 60

# URL identity decay, as idle time since the URL was last encountered.
DORMANT_AFTER_DAYS = 60
PURGE_AFTER_DAYS = 90

# How a fingerprint is treated at a given idle age.
STATE_ACTIVE = "active"    # loaded into the hot set used by retrieval
STATE_DORMANT = "dormant"  # hidden from the hot set, still consulted on demand
STATE_PURGE = "purge"      # deleted

# How many retention runs a day the scheduler should aim for. Cleanup is cheap
# but not free, so once a day is enough and keeps the bands stable.
RETENTION_INTERVAL_HOURS = 24


@dataclass(frozen=True)
class RetentionPlan:
    """The cutoffs one cleanup run applies."""

    fact_cutoff: date
    dormant_before: datetime
    purge_before: datetime


def build_retention_plan(
    today: date,
    *,
    window_days: int = WINDOW_DAYS,
    dormant_after_days: int = DORMANT_AFTER_DAYS,
    purge_after_days: int = PURGE_AFTER_DAYS,
    now: datetime | None = None,
) -> RetentionPlan:
    """Compute the cutoffs a cleanup run applies.

    The fact cutoff is the start of the briefing window: a fact older than that
    can no longer appear in any brief, so its body is dead weight. The URL
    cutoffs are idle ages, so they are timestamps rather than dates.
    """
    reference = now or datetime.now(timezone.utc)
    return RetentionPlan(
        fact_cutoff=today - timedelta(days=window_days - 1),
        dormant_before=reference - timedelta(days=dormant_after_days),
        purge_before=reference - timedelta(days=purge_after_days),
    )


def fingerprint_state(last_seen: datetime, plan: RetentionPlan) -> str:
    """Classify a URL fingerprint by how long it has been idle."""
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)

    if last_seen < plan.purge_before:
        return STATE_PURGE
    if last_seen < plan.dormant_before:
        return STATE_DORMANT
    return STATE_ACTIVE


def is_hidden_from_hot_set(last_seen: datetime, plan: RetentionPlan) -> bool:
    """True when a fingerprint should be kept out of the per-run hot set.

    This is the trigger for the hidden band: once a URL has been idle past the
    dormant threshold it stops being preloaded, so the set retrieval compares
    against stays proportional to recent activity rather than to history.
    """
    return fingerprint_state(last_seen, plan) in (STATE_DORMANT, STATE_PURGE)


def should_delete_fact(publish_date: datetime | None, plan: RetentionPlan) -> bool:
    """True when a fact has fallen outside the briefing window.

    An undated fact is kept: nothing proves it is stale, and dropping it would
    silently shrink the fact base.
    """
    if publish_date is None:
        return False
    if publish_date.tzinfo is None:
        publish_date = publish_date.replace(tzinfo=timezone.utc)
    return publish_date.astimezone(timezone.utc).date() < plan.fact_cutoff
