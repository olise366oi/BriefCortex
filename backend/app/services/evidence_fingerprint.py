# /backend/app/services/evidence_fingerprint.py
"""A stable identity for one evidence set, so a brief can be reused.

The report cache used to key on the calendar: the same company with the same
objective but a meeting one day later missed, even though the facts behind the
brief were identical. That is why the miss rate looked high — the key was
tracking the request, not the evidence.

This module gives an evidence set a fingerprint. Two runs whose facts and
postings carry the same content, whatever the meeting date, produce the same
fingerprint, and the second run can reuse the first brief instead of paying
for retrieval and inference again.

The fingerprint covers what the brief is actually built from:
- hard facts, in a canonical order
- postings
- the company description, which changes what is attributed to the company
- the meeting purpose, which changes what the report argues
It deliberately excludes the meeting date, because the date only decides the
window the facts came from, and the facts are already in the fingerprint.
"""
from __future__ import annotations

import hashlib
from typing import Sequence

from app.models.fact_base import FactBase

# Bumped when the report schema or the prompt changes materially. A cached brief
# written by an older pipeline must not be served as if it were current.
PROMPT_VERSION = "2026-11-05.1"


def _canonical(text: str) -> str:
    """Collapse whitespace and lowercase, so formatting noise is not a change."""
    return " ".join((text or "").lower().split())


def evidence_fingerprint(
    fact_base: FactBase,
    meeting_purpose: str = "",
    company_description: str = "",
) -> str:
    """Return a stable digest of everything the brief is derived from."""
    facts = sorted(_canonical(item.content) for item in fact_base.hard_facts)
    postings = sorted(_canonical(item.content) for item in fact_base.hiring)

    payload = "\n".join(
        (
            PROMPT_VERSION,
            fact_base.company_id,
            _canonical(meeting_purpose),
            _canonical(company_description),
            "facts:",
            *facts,
            "postings:",
            *postings,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def section_fingerprint(kind: str, items: Sequence[str]) -> str:
    """Return a digest for one cached section of a report.

    The analysis and the recommendation are derived from the same facts, so
    they can be reused independently of whether new facts arrived elsewhere.
    """
    body = "\n".join(_canonical(text) for text in sorted(items, key=_canonical))
    payload = f"{PROMPT_VERSION}|{kind}|{body}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
