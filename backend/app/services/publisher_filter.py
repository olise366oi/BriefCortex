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

    candidates = [name]
    for alias in aliases or []:
        alias_text = (alias or "").strip().lower()
        # A one-word alias is fine; an empty or identical one adds nothing.
        if alias_text and alias_text != name:
            candidates.append(alias_text)

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

        if not mentions_company(result, company_name, aliases):
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


def extract_clean_text(raw_content: Optional[str], url: str) -> Optional[str]:
    """Extract the article body from Tavily raw_content.

    Tavily returns page markdown, so the body is located first with the
    publisher's own rule: navigation, share rows, related-story blocks and
    paywall offers never reach the fact base. Trafilatura then strips any
    residual chrome from the remaining markdown.

    When Trafilatura produces nothing, the input was plain text rather than
    markdown, so the body is rebuilt from its prose lines instead of being
    thrown away. Dropping the article was the previous behaviour and it lost
    every result under ``include_raw_content="text"``.
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

    return clean_text
