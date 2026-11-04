# /backend/app/services/incidental.py
"""Facts about our company that appear inside someone else's story.

A deal team still wants the sentence "Intesa's bid implies UniCredit will be
forced to respond" even though the article is about Intesa. Dropping the whole
article throws that away; absorbing the whole article pollutes the fact base.

This module takes the middle path: it reads an article about another subject,
keeps only the sentences that are actually about our company, and hands them
back tagged as incidental. What they mean, and whether they are safe to act on,
is decided later — by the classifier and by the reader, not by a rule here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Sequence

from app.services.fact_extraction import first_sentence, normalize_text


@dataclass(frozen=True)
class IncidentalSentence:
    """One sentence about our company, found inside another subject's article."""

    content: str
    # True when the sentence's own subject is our company, false when our
    # company appears only as a comparison or a mention.
    is_subject: bool


def _subject_pattern(company: str) -> re.compile:
    """Build a matcher for the company name at a word boundary."""
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(company)}(?![A-Za-z0-9])", re.IGNORECASE)


def company_name_hits(text: str, company: str) -> int:
    """Count how many times the company is named in a piece of text."""
    if not company.strip():
        return 0
    return len(_subject_pattern(company.strip()).findall(text))


def _looks_like_subject(sentence: str, company: str) -> bool:
    """True when the company leads the sentence rather than trailing it.

    A sentence that opens with the company name is reporting on it. One that
    names it in the back half is usually comparing it or naming it in passing.
    """
    match = _subject_pattern(company.strip()).search(sentence)
    if match is None:
        return False
    # Compare against the first clause rather than the whole sentence: a long
    # sentence that happens to name the company early can still be about someone
    # else, so the mention has to sit near the opening.
    opening = sentence[: max(match.end(), 1)]
    return match.start() <= max(len(opening) // 2, 12) and match.start() < 40


def extract_incidental_sentences(
    clean_text: str,
    company: str,
    *,
    exclude: Sequence[str] = (),
    max_sentences: int = 6,
) -> List[IncidentalSentence]:
    """Return the sentences in this article that are about our company.

    Every paragraph is read, not just the opening, because the mention we want
    often sits deep in a story about a competitor. ``exclude`` holds sentences
    already collected for this company, so an article is not mined twice.
    """
    if not clean_text or not company.strip():
        return []

    name = company.strip()
    already_known = {normalize_text(text) for text in exclude}

    found: List[IncidentalSentence] = []
    seen: set[str] = set()

    for paragraph in re.split(r"\n\s*\n", clean_text):
        paragraph = paragraph.strip()
        if not paragraph or not company_name_hits(paragraph, name):
            continue

        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            # Reflow the sentence: source markdown breaks lines mid-sentence, and
            # a stored fact must read as one line.
            sentence = " ".join(sentence.split())
            if not sentence or not company_name_hits(sentence, name):
                continue

            key = normalize_text(sentence)
            if key in seen or key in already_known:
                continue
            seen.add(key)

            found.append(
                IncidentalSentence(
                    content=sentence,
                    is_subject=_looks_like_subject(sentence, name),
                )
            )
            if len(found) >= max_sentences:
                return found

    return found


def article_subject_mentions(clean_text: str, company: str) -> int:
    """How often our company is named in this article overall.

    Used only to describe an article, never to admit or reject one.
    """
    return company_name_hits(clean_text, company)


# Page chrome that sits above the article body and carries no subject signal.
# Seen across publishers: release timestamps, datelines from the wire service,
# source attributions, distributor names and markdown table/list fragments.
CHROME_LINE_PATTERNS = (
    re.compile(r"^\s*[A-Z][a-z]{2}\s+\d{1,2},\s+20\d{2}(,\s*\d{1,2}:\d{2}\s*(ET|GMT|UTC)?)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*\|\s*source\s*:", re.IGNORECASE),
    re.compile(r"^\s*source\s*:", re.IGNORECASE),
    re.compile(r"^\s*cnw\s*group\s*$", re.IGNORECASE),
    re.compile(r"^\s*(pr|globe|business)\s*newswire\s*$", re.IGNORECASE),
    re.compile(r"^\s*[A-Z][A-Za-z.\s&'-]{0,40}\s*/\s*(PR|Globe|Business)?Newswire\s*/\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d{1,2}:\d{2}\s*(AM|PM)?\s*(ET|GMT|UTC)\s*$", re.IGNORECASE),
    re.compile(r"^\s*research and markets\s*$", re.IGNORECASE),
    re.compile(r"^\s*delveinsight[^.]*\s*$", re.IGNORECASE),
)

# A wire-service marker embedded in the prose line: "(PRNewswire/)" or
# "/PRNewswire/ --". Kept as chrome because it names the distributor, not the
# story. Replaced by a single space so the surrounding words stay separate.
WIRE_TAG_RE = re.compile(
    r"(?:\(|/)?\s*(?:PR|Globe|Business)?Newswire\s*/?\)?\s*(?:--|—|–)?",
    re.IGNORECASE,
)

# A real opening sentence is at least this long; anything shorter is a label,
# a ticker line or a fragment rather than prose.
MIN_OPENING_CHARS = 40


def is_chrome_line(line: str) -> bool:
    """True when a line is page furniture rather than article prose."""
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith(("|", "![")):
        return True
    return any(pattern.match(stripped) for pattern in CHROME_LINE_PATTERNS)


def opening_sentence(clean_text: str) -> str:
    """Return the article's first real sentence, for subject classification.

    Publishers put a release timestamp, a wire-service dateline, a distributor
    name or a source attribution above the body. Passing that text to the
    subject classifier makes every article look like it is about someone else:
    measured on a live run, one file opened with "CNW Group" and another with
    "Assertio also today announced", which routed seven of seven articles away
    from their real subject. Furniture is therefore skipped until prose begins.
    """
    if not clean_text:
        return ""

    for paragraph in re.split(r"\n\s*\n", clean_text):
        for line in paragraph.splitlines():
            line = line.strip()
            if is_chrome_line(line):
                continue
            if len(line) < MIN_OPENING_CHARS:
                continue
            # Drop a leading wire-service tag inside the prose line, so the
            # opening reads as the article and not as its distribution stamp.
            line = re.sub(r"\s+", " ", WIRE_TAG_RE.sub(" ", line)).strip(" -")
            if len(line) < MIN_OPENING_CHARS:
                continue
            return first_sentence(line)

    return ""
