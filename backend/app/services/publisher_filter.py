# /backend/app/services/publisher_filter.py
"""URL filtering, article-body location, and text extraction."""
from __future__ import annotations

import re
from typing import List, Optional

import markdown as md
from trafilatura import extract

from app.services.source_rules import (
    JUNK_URL_PATHS,
    MIN_CLEAN_TEXT_LENGTH,
    MIN_PARAGRAPH_LENGTH,
    SITE_BODY_RULES,
    SITE_SECTION_RULES,
)
from app.services.hiring import is_careers_url, is_job_board
from app.services.long_document import is_long_document, select_relevant
from app.services.logger_service import get_logger
from app.services.media_trust import is_refused_host, is_roundup_url
from app.services.urls import host_of, url_path

logger = get_logger("publisher_filter")

MIN_BODY_CHARS = 400

BODY_HEADING_RE = re.compile(r"^#\s+\S")

# A recruitment section on a publisher's own site. "Canadians will understand
# this section" — a publisher's recruiting pitch is not press either, and a
# YKK run still lost nothing, because the news lanes open with a domain list
# and these pages never rank there.
RECRUITMENT_PATH_RE = re.compile(
    r"(/recruit[/\-.]|/careers?[/\-.]|/jobs?[/\-.]|/hiring[/\-.]|"
    r"/internship|/employment)"
)

# Pages that are a company's own advert for staff, wherever they live.
RECRUITMENT_HOST_PATHS = (
    "/careers",
    "/jobs",
    "/recruit",
    "/hiring",
    "/work-with-us",
    "/join-us",
)


def is_recruitment_page(url: str) -> bool:
    """True when a URL is a hiring page rather than reporting.

    Careers pages are the hiring lane's own source and are read as postings;
    every other recruitment page is a publisher's advert for staff, which is
    not evidence about the company being briefed.
    """
    if is_careers_url(url):
        return False
    path = url_path(url)
    if RECRUITMENT_PATH_RE.search(path):
        return True
    return any(path.startswith(prefix) for prefix in RECRUITMENT_HOST_PATHS)


def _longest_domain_match(host: str, rules: dict):
    """Return the rule whose domain is the longest suffix of host."""
    best_domain = ""
    best_rule = None
    for domain, rule in rules.items():
        if not host.endswith(domain):
            continue
        if len(domain) > len(best_domain):
            best_domain = domain
            best_rule = rule
    return best_rule


def site_section_rule(url: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the (keep, drop) section prefixes for this publisher."""
    rule = _longest_domain_match(host_of(url), SITE_SECTION_RULES)
    return rule if rule is not None else ((), ())


def site_body_rule(url: str) -> dict:
    """Return the body-scoping rule for this publisher."""
    rule = _longest_domain_match(host_of(url), SITE_BODY_RULES)
    return rule if rule is not None else {}


def _matches_section(path: str, prefix: str) -> bool:
    """Match a section prefix against a URL path.

    Prefixes are written with a trailing slash so /business/ does not match
    /businesswire-x. Section landing pages arrive without the slash
    (/markets), so the trimmed form is accepted as a whole path segment.
    """
    if path.startswith(prefix):
        return True

    bare = prefix.rstrip("/")
    if not bare:
        return False
    if path == bare:
        return True
    return path.startswith(bare + "/")


def is_kept_result(url: str) -> bool:
    """Decide whether a result URL points at readable editorial reporting."""
    path = url_path(url)

    for junk in JUNK_URL_PATHS:
        if junk in path:
            return False

    keep, drop = site_section_rule(url)

    for dropped in drop:
        if _matches_section(path, dropped):
            return False

    if not keep:
        return True

    return any(_matches_section(path, section) for section in keep)


def mentions_company(
    result: dict,
    company_name: str,
    aliases: Optional[List[str]] = None,
) -> bool:
    """Check whether a result is actually about the requested company.

    Tavily matches query terms loosely, so unrelated articles land in the
    result set. Latin names are matched on word boundaries: NIO must not match
    "senior" or "opinion". CJK names have no word boundaries, so those fall
    back to a plain substring check.

    ``aliases`` carry the registered legal name, which a headline often uses
    instead of the short form: searching "IBM" otherwise discards "Why is
    International Business Machines stock rallying today?".
    """
    name = company_name.strip().lower()
    if not name:
        return True

    haystack = " ".join(
        str(result.get(key) or "") for key in ("title", "url", "raw_content")
    ).lower()

    return names_company(haystack, company_name, aliases)


# An exchange-prefixed ticker is not the company's name. "Boreo Oyj (FRA:YKK)
# Q3 2026 Earnings Call Transcript" names Boreo: YKK there is Boreo's Frankfurt
# symbol, and a three-letter code belongs to unrelated businesses on different
# venues — the same collision `services/ticker.py` refuses to price. Measured on
# a YKK run: the whole of Boreo's earnings transcript was filed as YKK's news,
# because ":" is not a letter and the word-boundary check accepted the symbol.
EXCHANGE_TICKER_RE = re.compile(
    r"(?<![a-z0-9])[a-z]{2,6}\s*[:\uff1a]\s*[a-z]{1,6}(?![a-z0-9])"
)


def _name_candidates(
    company_name: str,
    aliases: Optional[List[str]] = None,
) -> List[str]:
    """Every lowercased name the company is known by, blanks and repeats dropped."""
    name = company_name.strip().lower()
    candidates = [name] if name else []
    for alias in aliases or []:
        alias_text = (alias or "").strip().lower()
        if alias_text and alias_text not in candidates:
            candidates.append(alias_text)
    return candidates


def names_company(
    text: str,
    company_name: str,
    aliases: Optional[List[str]] = None,
) -> bool:
    """True when ``text`` names the company rather than quoting its ticker.

    Shared by the result filter and the headline rule so "this is the company"
    means one thing in this codebase. An exchange-prefixed ticker is removed
    before matching: the symbol identifies a listing, and the same symbol on
    another venue belongs to someone else.
    """
    candidates = _name_candidates(company_name, aliases)
    if not candidates:
        return True

    haystack = EXCHANGE_TICKER_RE.sub(" ", (text or "").lower())

    for candidate in candidates:
        if candidate.isascii():
            pattern = rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])"
            if re.search(pattern, haystack) is not None:
                return True
        elif candidate in haystack:
            return True

    return False


def filter_results_for_company(
    results: List[dict],
    company_name: str,
    aliases: Optional[List[str]] = None,
) -> List[dict]:
    """Keep only results that pass section scoping and mention the company.

    A refused host is dropped here, before any of the reading work happens: a
    forum thread, a social post or an aggregator of user submissions is not press,
    so it never reaches the fact base regardless of what it says or who links it.

    A multi-company column is dropped for a different reason. "Small caps to
    watch" is a legitimate page that reports a dozen firms in sequence; the
    numbers are true and none of them belong to one company, so the sentences
    cannot be attributed without reading which paragraph is whose. Measured: one
    such page put seven other companies' figures into one brief.
    """
    kept: List[dict] = []
    refused: List[str] = []
    roundups = 0
    recruitment = 0
    boards = 0
    for result in results:
        if not isinstance(result, dict):
            continue

        url = result.get("url")
        if not isinstance(url, str) or not url.strip():
            continue

        # A job-posting URL is judged before the refused-host list, because two
        # very different things live on refused hosts: a forum post, and an
        # employer's own openings. LinkedIn is refused as a source of *reporting*
        # — a social post is a claim about a claim — but its jobs section is a job
        # board, which is the one thing that lane exists to read. Measured: the
        # LinkedIn lane returned zero usable results on every run because every
        # result was refused before its role was ever looked at.
        #
        # A third-party job board is refused even though its path says "jobs": an
        # aggregator rewrites every posting into its own template, so nothing on
        # it is a statement the employer made. Measured on Wipro: Naukri and a
        # "how to apply" page put thirteen template lines into the news lane.
        if is_careers_url(url):
            if is_job_board(url):
                boards += 1
                continue
            if mentions_company(result, company_name, aliases):
                kept.append(result)
            continue

        # A job board is refused as reporting too, even when its path does not
        # look like a careers page: `naukri.com/wipro-jobs-careers-1384` has no
        # `/jobs/` segment, so it fell through to the reporting path and put four
        # rewritten template lines into the news lane of a measured Wipro run.
        if is_job_board(url):
            boards += 1
            continue

        if is_refused_host(url):
            refused.append(host_of(url))
            continue

        if is_roundup_url(url):
            roundups += 1
            continue

        if not is_kept_result(url):
            continue

        # A publisher's own recruitment page is not reporting about the company
        # it was returned for. Measured on YKK: a wire's Japanese internship
        # advert was filed as a hard fact, because the page reads like prose.
        if is_recruitment_page(url):
            recruitment += 1
            continue

        if not mentions_company(result, company_name, aliases):
            continue

        kept.append(result)

    if refused:
        logger.info(
            "Non-press hosts refused | count=%s | hosts=%s",
            len(refused),
            sorted(set(refused))[:8],
        )
    if roundups:
        logger.info(
            "Multi-company roundup columns skipped | count=%s", roundups
        )
    if recruitment:
        logger.info("Recruitment pages skipped | count=%s", recruitment)
    if boards:
        logger.info("Third-party job boards skipped | count=%s", boards)

    return kept


def locate_article_body(markdown: str, url: str) -> str:
    """Return only the article body from Tavily page markdown.

    The opening is the first top-level heading; pages without one (press
    releases, CNBC blobs) fall back to the first prose-length line. The closing
    is the first trailing marker from the publisher's own rule set. Cut points
    resolve on raw text rather than per line, because some publishers deliver
    the whole page as a single newline-free blob.
    """
    rule = site_body_rule(url)
    lines = markdown.splitlines()

    start = 0
    for index, line in enumerate(lines):
        if BODY_HEADING_RE.match(line.strip()):
            start = index
            break
    else:
        for index, line in enumerate(lines):
            stripped = line.strip()
            if len(stripped) >= 80 and not stripped.startswith(("[" , "*", "!", "#", "|")):
                start = index
                break

    offset = sum(len(line) + 1 for line in lines[:start])
    tail = markdown[offset:]

    cut = len(tail)
    for marker in rule.get("body_end", ()):
        found = tail.lower().find(marker.lower())
        if found != -1:
            cut = min(cut, found)

    body = tail[:cut]

    line_patterns = [
        re.compile(pattern, re.IGNORECASE) for pattern in rule.get("body_line", ())
    ]
    kept = [
        line
        for line in body.splitlines()
        if not any(pattern.match(line.strip()) for pattern in line_patterns)
    ]

    return "\n".join(kept).strip()


def rebuild_plain_prose(text: str) -> str:
    """Rebuild a body from plain text, which Trafilatura refuses to parse.

    Tavily returns markdown by default, but its ``text`` mode and some
    publishers deliver plain text with no headings and no paragraph breaks.
    Handed that, Trafilatura returns nothing at all (measured: 331 chars in,
    0 out), so the paragraphing is reconstructed from the prose lines instead.
    """
    lines = []
    for line in (text or "").splitlines():
        stripped = " ".join(line.split())
        if len(stripped) >= MIN_PARAGRAPH_LENGTH:
            lines.append(stripped)
    return "\n\n".join(lines)


def extract_clean_text(
    raw_content: Optional[str],
    url: str,
    company: str = "",
    objective: str = "",
) -> Optional[str]:
    """Extract the article body from Tavily raw_content.

    Tavily returns page markdown, so the body is located first with the
    publisher's own rule: navigation, share rows, related-story blocks and
    paywall offers never reach the fact base. Trafilatura then strips any
    residual chrome from the remaining markdown.

    When Trafilatura produces nothing, the input was plain text rather than
    markdown, so the body is rebuilt from its prose lines instead of being
    thrown away. Dropping the article was the previous behaviour and it lost
    every result under ``include_raw_content="text"``.

    A filing is 300,000 characters, most of it boilerplate. ``company`` and
    ``objective`` let the long-document reader keep the sections that answer the
    question instead of sending the whole filing into the fact base.
    """
    if not raw_content or not isinstance(raw_content, str):
        return None

    content = locate_article_body(raw_content, url)
    if not content:
        return None

    if len(content) < MIN_BODY_CHARS:
        return None

    try:
        html = md.markdown(content)
    except Exception:
        html = ""

    clean_text = ""
    if html:
        try:
            # Reader comments are never evidence, and the fact split cannot tell
            # a comment from reporting once both are prose. Turning them off
            # closes the case where the body boundary missed a comment block.
            #
            # favor_precision is deliberately NOT set: on publishers without a
            # body-boundary rule it collapses the article to a single line
            # (measured: 1466 chars -> 30 on an unlisted host). Comments off and
            # deduplication are safe everywhere; precision is not.
            clean_text = extract(
                html,
                output_format="markdown",
                include_comments=False,
                deduplicate=True,
            ) or ""
        except Exception:
            clean_text = ""

    if len(clean_text.strip()) < MIN_CLEAN_TEXT_LENGTH:
        # Plain text, not markdown: rebuild the paragraphs by hand.
        clean_text = rebuild_plain_prose(content)

    clean_text = clean_text.strip()
    if len(clean_text) < MIN_CLEAN_TEXT_LENGTH:
        return None

    # A filing or a long IR page: keep the sections that answer the question.
    if is_long_document(clean_text):
        clean_text = select_relevant(clean_text, company, objective)

    return clean_text
