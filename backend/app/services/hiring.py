# /backend/app/services/hiring.py
"""Hiring volume as a retrieval dimension.

Open roles are a leading indicator of where a company is moving people and
budget, which usually shows up in the org before it shows up in a filing. This
module only *finds* postings and counts them; what a role family implies about
strategy is the model's job, not ours.
"""
from __future__ import annotations

from typing import List

from app.services.keywords import match_word

# A page advertises itself as a posting with these words. Without one of them it
# is discourse about hiring rather than a hiring signal.
POSTING_MARKERS = (
    "job",
    "jobs",
    "career",
    "careers",
    "vacancy",
    "vacancies",
    "position",
    "positions",
    "we are hiring",
    "join our team",
    "apply now",
    "headcount",
)


def hiring_queries(company: str) -> List[str]:
    """Queries that surface a company's recent job postings."""
    return [
        f"{company} careers jobs openings",
        f"{company} job vacancies recruitment",
    ]


def is_posting(text: str) -> bool:
    """True when the text reads like a job posting rather than an article."""
    lowered = text.lower()
    return any(match_word(lowered, marker) for marker in POSTING_MARKERS)
