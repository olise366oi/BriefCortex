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
#
# The test is on the host prefix and on whole path *segments*. A loose "contains
# /jobs" check misfires on article slugs: "week-ahead-apple-amd-jobs-report" and
# "job-trends/eyepoint-will-halve-workforce" are news stories, not job boards,
# and treating them as boards wasted nine page fetches per run.
CAREERS_HOST_PREFIXES = ("careers.", "recruiting.", "jobs.")

# A segment that names a careers area in full.
CAREERS_SEGMENTS = frozenset(
    ("career", "careers", "job", "jobs", "vacancy", "vacancies", "recruitment", "recruiting")
)

# A segment that ends with a careers noun, as in "jobs-career" or
# "career-vacancies". Publisher slugs compose the words in either order.
CAREERS_SEGMENT_SUFFIXES = (
    "-career", "-careers", "-job", "-jobs",
    "-vacancy", "-vacancies", "-recruitment", "-recruiting",
)

# Aggregator company pages, which list one employer's openings.
AGGREGATOR_SEGMENT = "cmp"

_EXTENSION_RE = re.compile(r"\.(html?|php|aspx?|jsp)$", re.IGNORECASE)

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
    "walk-in interview",
    "we are hiring",
    "now hiring",
    "apply now",
    "apply for this job",
    "join our team",
    "vacancies available",
    "openings available",
    "positions available",
)

# Macro labour-market terms. "Job openings" is the name of a US statistics
# release (JOLTS), so a markets column reporting it would otherwise read as
# evidence that a company is hiring.
LABOUR_STATISTICS = (
    "jolts",
    "labor turnover",
    "labour turnover",
    "bureau of labor statistics",
    "bureau of labour statistics",
    "jobless claims",
    "nonfarm payroll",
    "unemployment rate",
    "payrolls",
    "labor market",
    "labour market",
    "job openings and labor",
    "measured vacancies",
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
    """True when the URL is a job board or a company careers section.

    Judged on whole path segments plus a few host prefixes, so an article slug
    that merely mentions jobs is not mistaken for one.
    """
    if not url:
        return False

    lowered = url.strip().lower()
    for separator in ("?", "#"):
        if separator in lowered:
            lowered = lowered.split(separator, 1)[0]

    if "//" in lowered:
        remainder = lowered.split("//", 1)[1]
    else:
        remainder = lowered

    host, _, path = remainder.partition("/")
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[len("www."):]

    if any(
        host == prefix.rstrip(".") or host.startswith(prefix)
        for prefix in CAREERS_HOST_PREFIXES
    ):
        return True

    for segment in path.split("/"):
        if not segment:
            continue
        segment = _EXTENSION_RE.sub("", segment)
        if segment in CAREERS_SEGMENTS:
            return True
        if segment.endswith(CAREERS_SEGMENT_SUFFIXES):
            return True
        if segment == AGGREGATOR_SEGMENT:
            return True

    return False


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
    # A macro labour release is not a company's hiring plan.
    if any(marker in lowered for marker in LABOUR_STATISTICS):
        return False
    # Hashtag spam: mostly '#'-joined tokens.
    if lowered.count("#") >= 2:
        return False

    has_field = any(match_word(lowered, label) for label in HIRING_FIELD_LABELS)
    has_statement = any(phrase in lowered for phrase in HIRING_STATEMENTS)

    return has_field or has_statement
