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
    # North American boards label a posting with these instead. A Canadian
    # aggregator's every role arrived as "… Job Title: QC In-Line Inspector",
    # which matched none of the labels above, so seven postings were stored as
    # hard facts and the hiring lane reported zero.
    "job title",
    "job posting",
    "position title",
    "job id",
    "job reference",
    "titre du poste",
    "lieu de travail",
    "date de debut",
    "poste",
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
    """Queries that surface a company's job postings and hiring news.

    Measured: a generic "company careers jobs openings" query returns other
    companies' hiring stories and the company's marketing careers page, never an
    actual posting (0 of 48 results were careers URLs worth reading). A query
    phrased the way a job seeker types it does reach real posting detail pages,
    which is where schema.org data lives, so those come first.
    """
    return [
        f"{company} engineer job opening apply",
        f"{company} careers open positions apply now",
        f"{company} hiring expansion new roles",
    ]


# LinkedIn's own job board. Measured on the same query as the general search:
# roughly five times the attributable role titles (587 vs 106 for AMD, 893 vs 44
# for Zydus), at 1.9-4.5s instead of 0.3-0.6s. The trade is accepted because a
# hiring lane with five roles is not a signal, and the wall-clock cost stays
# inside the request budget.
LINKEDIN_JOB_DOMAINS = ("linkedin.com/jobs",)


def linkedin_hiring_queries(company: str) -> List[str]:
    """Queries for the LinkedIn job board only.

    Kept separate from hiring_queries because they run against a fixed domain
    list: adding the domain to a general query would silently restrict every
    other query that shares the list.
    """
    return [
        f"{company} hiring jobs open roles",
        f"{company} careers apply now position",
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

    Accepts three shapes, because a posting announces itself in three ways:
    a structured field label ("Qualification:"), an explicit invitation to
    apply ("apply now"), and a role title on its own ("Sr. Software Engineer —
    GPU Kernelel Development"). The third shape is what a job board publishes
    (measured: a LinkedIn jobs index lists role titles as headings and nothing
    else), so requiring a field label discarded every posting on it.

    Rejects channel promos, career advice and hashtag spam. A market column
    saying "jobs data" contains none of the three shapes, so it never reaches the
    hiring lane.
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
    # Compensation reporting, not a posting: "Senior software engineer: $160,158".
    if SALARY_LINE_RE.search(line):
        return False
    # Hashtag spam: mostly '#'-joined tokens.
    if lowered.count("#") >= 2:
        return False

    # A structured field label counts only when it opens the line, the way a
    # posting writes it ("Qualification: B.Sc."). A label appearing mid-sentence
    # is body prose: "work in collaboration with other departments" is not a
    # posting, and matching "department" anywhere let it through.
    if _opens_with_field_label(line):
        return True

    # Aggregator pages concatenate the location block and the role into one line,
    # so the label lands mid-line: "Canada Goose Inc. Scarborough ON Location:
    # … Job Title: QC In-Line Inspector". Only the labels that name a role are
    # accepted here — an inline "Department:" is every article that mentions a
    # department and would flood the lane.
    if _carries_role_label(line):
        return True

    if any(phrase in lowered for phrase in HIRING_STATEMENTS):
        return True

    return _looks_like_role_title(line)


# Labels that name an open role wherever they appear on the line. Deliberately
# narrower than HIRING_FIELD_LABELS: the rest describe what a role requires.
INLINE_ROLE_LABELS = (
    "job title",
    "position title",
    "designation",
    "titre du poste",
    "poste",
)


def _carries_role_label(line: str) -> bool:
    """True when a role-naming label appears anywhere in the line."""
    for label in INLINE_ROLE_LABELS:
        pattern = re.compile(
            rf"{re.escape(label)}(?:e?s)?[\W_]*(?:[:\-\u2013\uff1a]|\*\*)"
            rf"[\W_]*(\S.*)",
            re.IGNORECASE,
        )
        match = pattern.search(line)
        if match and len(match.group(1).strip()) >= MIN_ROLE_TITLE_CHARS:
            return True
    return False


def _opens_with_field_label(line: str) -> bool:
    """True when the line starts with a field label followed by content.

    The label must be followed by a separator a posting actually writes
    ("Qualification: ...", "**Experience:** ..."). A sentence that merely begins
    with the word ("Experience in people management") is body prose, and treating
    it as a posting let requirement bullets into the hiring lane.
    """
    for label in HIRING_FIELD_LABELS:
        # Two separators a posting actually writes: "Qualification: ..." and
        # "**Job location** Ankleshwar". A sentence that merely begins with the
        # word ("Experience in people management") has neither.
        pattern = re.compile(
            rf"^[\W_]*{re.escape(label)}(?:e?s)?[\W_]*(?:[:\-\u2013\uff1a]|\*\*)[\W_]*(\S.*)$",
            re.IGNORECASE,
        )
        match = pattern.match(line)
        if match and len(match.group(1).strip()) >= MIN_ROLE_TITLE_CHARS:
            return True
    return False


# Role nouns that name a job rather than describe a news event.
ROLE_TITLE_NOUNS = (
    "engineer", "manager", "director", "analyst", "specialist", "developer",
    "architect", "designer", "consultant", "intern", "officer", "lead",
    "scientist", "recruiter", "administrator", "coordinator", "supervisor",
    "technician", "associate", "executive", "controller", "accountant",
)

# A line carrying these is reporting an event, not advertising a role. Without
# this guard "AMD appoints new chief financial officer" reads as a posting.
ROLE_TITLE_REJECTS = (
    "appointed", "appoints", "named", "names", "resigned", "resigns",
    "stepped down", "steps down", "hired as", "promoted", "promotes",
    "said", "says", "announced", "announces", "reported", "reports",
    "according to", "will ", "has ", "have ",
    # Board chrome that counts roles rather than naming one: a jobs index
    # publishes "7,000+ Amd Technical Project Manager Jobs in United States".
    " jobs in ", "jobs)", "open roles", "job openings", "get notified",
    # Verbs a posting's body uses. A role title is a noun phrase.
    "work in", "collaborate", "responsible for", "minimum", "preferred",
    "experience in", "experience with", "experience developing",
    "sign in", "job alerts",
    "associated members", "open roles for", "currently ",
)

# A salary band on a company profile page ("Principal systems software
# engineer: $240,000") names a role and a number, but it is compensation
# reporting rather than an open posting. Measured on a LinkedIn company page:
# most of its "roles" were these wage summaries.
SALARY_LINE_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\s*(?:-|–|to)\s*\$\s?\d[\d,]*)?(?:\s*(?:/|per)\s*\w+)?",
    re.IGNORECASE,
)

# A role title is short. A paragraph that happens to contain "engineer" is prose.
MAX_ROLE_TITLE_CHARS = 120

# Lines this short are section headings on a posting ("Requirements",
# "Responsibilities"), not the role itself. A real title carries words.
MIN_ROLE_TITLE_CHARS = 12


def _looks_like_role_title(line: str) -> bool:
    """True when the line reads as a job title rather than as a sentence."""
    if not (MIN_ROLE_TITLE_CHARS <= len(line) <= MAX_ROLE_TITLE_CHARS):
        return False

    lowered = line.lower()
    if any(marker in lowered for marker in ROLE_TITLE_REJECTS):
        return False

    # A title is a noun phrase. A sentence carries a verb after its subject, and
    # a posting's body ("Lead development of characterisation methodologies")
    # reads as prose, not as a role.
    if re.search(r"(?<![a-z0-9])(work|collaborate|lead|manage|develop|provide|"
                 r"ensure|support|perform|requires|minimum|experience|"
                 r"join|apply)(?![a-z0-9])", lowered):
        return False

    return any(match_word(lowered, noun) for noun in ROLE_TITLE_NOUNS)
