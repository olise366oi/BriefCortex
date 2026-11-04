# /backend/app/services/hiring.py
"""Hiring activity as its own evidence lane.

Open roles are a leading indicator of where a company is moving people and
budget: a cluster of procurement roles usually lands before the reorganisation
does. Postings are therefore kept as a separate block rather than mixed into
the fact base, so the model can read them as intent while the brief's leverage
still rests on reported facts.
"""
from __future__ import annotations

import re
from typing import List

from app.services.keywords import match_word

# Strong signals: a page that advertises its own hiring. These decide whether
# the text is a posting at all.
POSTING_MARKERS = (
    "job opening",
    "job openings",
    "job description",
    "job title",
    "careers page",
    "we are hiring",
    "now hiring",
    "join our team",
    "apply now",
    "apply for this job",
    "vacancy",
    "vacancies",
    "recruitment",
)

# Weak signals only count alongside a strong one, because ordinary reporting
# also says "position" and "team".
WEAK_MARKERS = ("career", "careers", "position", "positions", "headcount", "job")

# A hiring URL is the clearest signal of all, and needs no text analysis.
# The trailing slash is optional: publisher sites serve both /careers and
# /careers/, and only matching one of them silently misses half of them.
POSTING_URL_PATTERNS = (
    re.compile(r"/careers?(?:/|$)", re.IGNORECASE),
    re.compile(r"/jobs?(?:/|$)", re.IGNORECASE),
    re.compile(r"/job-", re.IGNORECASE),
    re.compile(r"/vacanc", re.IGNORECASE),
    re.compile(r"/recruit", re.IGNORECASE),
    re.compile(r"careers\.", re.IGNORECASE),
)


def hiring_queries(company: str) -> List[str]:
    """Queries that surface a company's recent job postings and hiring news."""
    return [
        f"{company} careers jobs openings",
        f"{company} job vacancies recruitment",
        f"{company} hiring expansion new roles",
    ]


def is_posting(url: str, text: str) -> bool:
    """True when this page is a job posting rather than an article about one.

    A careers URL settles it. Otherwise the text needs either two strong hiring
    markers or one strong marker plus a weak one. That keeps ordinary reporting
    out ("the position of the company"), while a real ad ("we are hiring ...
    apply now") passes on its own strong signals.
    """
    if any(pattern.search(url or "") for pattern in POSTING_URL_PATTERNS):
        return True

    lowered = (text or "").lower()
    strong_hits = sum(1 for marker in POSTING_MARKERS if match_word(lowered, marker))
    if strong_hits >= 2:
        return True
    if strong_hits == 1:
        return any(match_word(lowered, marker) for marker in WEAK_MARKERS)
    return False
