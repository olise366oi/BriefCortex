# /backend/app/services/hiring.py
"""What counts as hiring evidence.

Hiring is a property of a *line*, not of a page. A stock-market recap that says
"jobs data" is not a careers page, and a careers page is mostly apply buttons.
Judging the page therefore files whole markets columns into the hiring lane
(measured: 40 of 40 items, of which 28 were noise), so the decision is made per
line instead.

A hiring line states something about a role: a field label, or an explicit
invitation to apply. Everything else on the page — buttons, location chips,
social links, hashtags, generated summaries — is furniture and is dropped.
"""
from __future__ import annotations

import re
from typing import List

from app.services.keywords import match_word

# A careers URL is the one reliable page-level signal, so pages on it skip fact
# mining entirely: a job board has no article to read.
CAREERS_URL_PATTERNS = (
    re.compile(r"/careers?(?:/|$)", re.IGNORECASE),
    re.compile(r"/jobs?(?:/|$|\d)", re.IGNORECASE),
    re.compile(r"/job-", re.IGNORECASE),
    re.compile(r"/vacanc", re.IGNORECASE),
    re.compile(r"/recruit", re.IGNORECASE),
    re.compile(r"careers\.", re.IGNORECASE),
)

# Structured fields of a posting. These are the payload — a role's requirements —
# and they appear in both proper job boards and social walk-in notices.
HIRING_FIELD_LABELS = (
    "qualification",
    "experience",
    "job location",
    "salary",
    "requirement",
    "responsibilities",
    "department",
    "walk-in",
    "interview date",
    "interview time",
    "eligibility",
    "designation",
    "no. of vacancies",
)

# An explicit invitation to apply. A story that merely mentions hiring does not
# contain one of these.
HIRING_STATEMENTS = (
    "inviting eligible candidates",
    "inviting applications",
    "job opportunities",
    "job openings",
    "walk-in interview",
    "we are hiring",
    "now hiring",
    "apply now",
    "apply for this job",
    "join our team",
    "vacancies available",
    "openings available",
)

# Distribution and engagement furniture. A line carrying these is a channel
# promo, not a role.
SOCIAL_NOISE = (
    "telegram",
    "youtube channel",
    "subscribe",
    "follow @",
    "instagram",
    "lnkd.in",
    "stay connected",
    "follow us",
    "link in bio",
    "t.me",
)

# Advice and commentary about careers in general, not a role at this company.
CAREER_ADVICE = (
    "your career",
    "career guidance",
    "career path",
    "interview questions",
    "tips for",
    "how to",
    "planning your",
)

# Disclaimers and platform legalese around a posting. They talk about hiring
# without stating anything about a role.
DISCLAIMERS = (
    "not confirmation of",
    "hiring-awareness post",
    "verify openings",
    "please verify",
    "for informational purposes",
    "not an official",
    "ai-powered fit",
    "by clicking apply",
    "agree to share your profile",
    "privacy policy",
    "terms and conditions",
)


def hiring_queries(company: str) -> List[str]:
    """Queries that surface a company's job postings and hiring news."""
    return [
        f"{company} careers jobs openings",
        f"{company} job vacancies recruitment",
        f"{company} hiring expansion new roles",
    ]


def is_careers_url(url: str) -> bool:
    """True when the URL is a job board or a company careers section."""
    return any(pattern.search(url or "") for pattern in CAREERS_URL_PATTERNS)


def is_hiring_line(text: str) -> bool:
    """True when a single line states something about a role.

    Requires a structured field label or an explicit apply invitation, and
    rejects channel promos, career advice and hashtag spam. A market column
    saying "jobs data" contains neither, so it never reaches the hiring lane.
    """
    line = " ".join((text or "").split())
    if not line:
        return False

    lowered = line.lower()

    if any(marker in lowered for marker in SOCIAL_NOISE):
        return False
    if any(marker in lowered for marker in CAREER_ADVICE):
        return False
    if any(marker in lowered for marker in DISCLAIMERS):
        return False
    # Hashtag spam: mostly '#'-joined tokens.
    if lowered.count("#") >= 2:
        return False

    has_field = any(match_word(lowered, label) for label in HIRING_FIELD_LABELS)
    has_statement = any(phrase in lowered for phrase in HIRING_STATEMENTS)

    return has_field or has_statement
