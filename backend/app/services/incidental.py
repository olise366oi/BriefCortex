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


def opening_sentence(clean_text: str) -> str:
    """Return the first sentence of the article, for subject classification."""
    for paragraph in re.split(r"\n\s*\n", clean_text):
        paragraph = paragraph.strip()
        if paragraph:
            return first_sentence(paragraph)
    return ""
