# /backend/app/services/entity_disambiguation.py
"""Drop "possible facts" that belong to a different company with the same name.

The incidental lane harvests every sentence that names our company, from
articles about someone else. That is the right recall for a company nobody
writes about, but it also collects sentences about unrelated businesses that
share the name: an "AMD Instinct" GPU is not the AI assistant called Instinct,
and a veterinary "Instinct EMR" is not it either.

These are cheap to reject without a model, using two signals:
- the sentence matches a known different meaning of this word, and
- the sentence shares none of the caller's own description of their company.

Whatever survives is still shown as a lead, never as a finding, so a false accept
costs a reader one line rather than a wrong conclusion.
"""
from __future__ import annotations

import re
from typing import List, Sequence

from app.models.fact_base import IncidentalFact
from app.services.logger_service import get_logger

logger = get_logger("entity_disambiguation")

# Product lines and other-business markers that use one of these words in an
# unrelated sense. Each entry is (pattern, why it is a different thing).
OTHER_MEANING_PATTERNS = (
    (re.compile(r"\bAMD\s+Instinct", re.I), "AMD GPU product line"),
    (re.compile(r"Instinct\s+(MI3|MI4|MI2)\d", re.I), "AMD GPU product line"),
    (re.compile(r"\bInstinct\s+EMR", re.I), "veterinary software product"),
    (re.compile(r"\bInstinct\s+(Science|Raw|Sport|Nature)", re.I), "different company"),
    (re.compile(r"\bDeep\s+Instinct", re.I), "different company"),
    (re.compile(r"\bInstinct\s+(pet|veterinary|clinic)", re.I), "different company"),
)

# A sentence needs at least one of the caller's own words to be about their
# company. Two words is a weak bar on purpose: the caller's description is short.
MIN_DESCRIPTION_HITS = 1

# Words too common to identify anything.
STOPWORDS = frozenset(
    ("the", "and", "for", "with", "that", "this", "from", "their", "they",
     "users", "user", "startup", "company", "its", "has", "have", "been",
     "which", "about", "into", "other", "than", "more", "most", "also")
)


def _description_tokens(description: str) -> set[str]:
    """Return the identifying words of a text, stemmed so inflections match.

    "texts" and "text" must compare equal: a caller writing "users text it" and
    an article writing "he texts the assistant" are describing the same thing,
    and a plain set difference would reject the article.
    """
    words = re.split(r"[^a-z0-9]+", (description or "").lower())
    tokens: set[str] = set()
    for word in words:
        if len(word) <= 2 or word in STOPWORDS:
            continue
        tokens.add(_stem(word))
    return tokens


def _stem(word: str) -> str:
    """Crude plural/verb-form folding, enough for description matching."""
    for suffix in ("ings", "ing", "ies", "es", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            base = word[: -len(suffix)]
            if suffix == "ies":
                base += "y"
            return base
    return word


def _matches_other_meaning(text: str) -> str:
    """Return why this sentence is about a different thing, or an empty string."""
    for pattern, reason in OTHER_MEANING_PATTERNS:
        if pattern.search(text):
            return reason
    return ""


def filter_by_entity(
    facts: Sequence[IncidentalFact],
    description: str,
    company: str,
) -> List[IncidentalFact]:
    """Return only the possible facts that plausibly concern the caller's company.

    The other-meaning check runs first and always: it is a statement about what
    a word means, not about the caller's wording, so it applies even when no
    description was supplied. The description check is recall-relative and only
    runs when there is a description to compare against.
    """
    tokens = _description_tokens(description)
    kept: List[IncidentalFact] = []
    rejected: List[tuple[str, str]] = []

    for fact in facts:
        reason = _matches_other_meaning(fact.content)
        if reason:
            rejected.append((fact.content[:70], reason))
            continue

        if tokens:
            hits = len(tokens & _description_tokens(fact.content))
            if hits < MIN_DESCRIPTION_HITS:
                rejected.append((fact.content[:70], "shares nothing with the description"))
                continue

        kept.append(fact)

    logger.info(
        "Possible facts filtered | company=%r | before=%s | kept=%s | rejected=%s",
        company,
        len(facts),
        len(kept),
        len(rejected),
    )
    for text, reason in rejected[:10]:
        logger.info("Possible fact rejected | %s | %s", reason, text)

    return kept
