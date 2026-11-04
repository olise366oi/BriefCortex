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
    SITE_BODY_RULES,
    SITE_SECTION_RULES,
)
from app.services.urls import host_of, url_path

MIN_BODY_CHARS = 400

BODY_HEADING_RE = re.compile(r"^#\s+\S")


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


def mentions_company(result: dict, company_name: str) -> bool:
    """Check whether a result is actually about the requested company.

    Tavily matches query terms loosely, so unrelated articles land in the
    result set. Latin names are matched on word boundaries: NIO must not match
    "senior" or "opinion". CJK names have no word boundaries, so those fall
    back to a plain substring check.
    """
    name = company_name.strip().lower()
    if not name:
        return True

    haystack = " ".join(
        str(result.get(key) or "") for key in ("title", "url", "raw_content")
    ).lower()

    if name.isascii():
        pattern = rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])"
        return re.search(pattern, haystack) is not None

    return name in haystack


def filter_results_for_company(results: List[dict], company_name: str) -> List[dict]:
    """Keep only results that pass section scoping and mention the company."""
    kept: List[dict] = []
    for result in results:
        if not isinstance(result, dict):
            continue

        url = result.get("url")
        if not isinstance(url, str) or not url.strip():
            continue

        if not is_kept_result(url):
            continue

        if not mentions_company(result, company_name):
            continue

        kept.append(result)

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


def extract_clean_text(raw_content: Optional[str], url: str) -> Optional[str]:
    """Extract the article body from Tavily raw_content.

    Tavily returns page markdown, so the body is located first with the
    publisher's own rule: navigation, share rows, related-story blocks and
    paywall offers never reach the fact base. Trafilatura then strips any
    residual chrome from the remaining markdown.
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
        return None

    try:
        # Reader comments are never evidence, and the fact split cannot tell a
        # comment from reporting once both are prose. Turning them off closes
        # the case where the body boundary missed a comment block.
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
        )
    except Exception:
        return None

    if not clean_text:
        return None

    clean_text = clean_text.strip()
    if len(clean_text) < MIN_CLEAN_TEXT_LENGTH:
        return None

    return clean_text
