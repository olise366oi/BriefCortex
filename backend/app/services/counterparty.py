"""The counterparty a negotiation objective names, and where to look for it.

A brief is about one company, but the meeting usually involves two: "buy zipper
supplies from YKK" needs evidence about YKK as much as about the caller. Every
query used to carry only the company being briefed, so the supplier side was
never searched — the generated brief said so in as many words ("silent on YKK"),
which was true and useless.

This module reads the objective for the other party's name. It does not decide
what evidence matters; it only gives the retrieval layer a subject to look up.
"""
from __future__ import annotations

import re
from typing import List

# "from YKK", "with YKK Holdings", "against Zydus Lifesciences", "vs Siemens".
#
# Capitalisation is the signal. A lowercase preposition followed by a capitalised
# phrase is how a proper name is introduced in an objective, and requiring the
# capital is what keeps "reduce price by 15% from current levels" from being read
# as a company called "Current".
_PARTNER = re.compile(
    r"(?:from|with|against|versus|vs|to)\s+"
    r"([A-Z][A-Za-z0-9&.\-'/]*(?:\s+[A-Z][A-Za-z0-9&.\-'/]*){0,3})"
)

# Words that begin a capitalised phrase without naming an entity.
_NOT_A_NAME = frozenset(
    (
        "the", "a", "an", "our", "their", "its", "this", "that", "these", "those",
        "current", "existing", "same", "next", "last", "us", "them", "you",
        "january", "february", "march", "april", "may", "june", "july", "august",
        "september", "october", "november", "december",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    )
)

# A trailing word that is part of the sentence rather than of the name.
_TRAILING_NOISE = frozenset(
    ("for", "on", "at", "in", "by", "about", "regarding", "terms", "meeting", "call")
)


def counterparties(meeting_purpose: str, company: str) -> List[str]:
    """Return the other companies an objective names, excluding our own.

    Returns an empty list when the objective names nobody, which is the common
    case: a fundamentals brief, or an objective written as a goal rather than as
    a relationship ("assess acquiring YKK" has no preposition to anchor on and is
    read as our own transaction).

    At most two names are returned, longest first, so "YKK Holdings" wins over a
    fragment of it.
    """
    purpose = " ".join((meeting_purpose or "").split())
    own = " ".join((company or "").split()).lower()

    found: List[str] = []
    for match in _PARTNER.finditer(purpose):
        words = match.group(1).split()
        # Drop a trailing word that belongs to the sentence, not the name.
        while words and words[-1].lower() in _TRAILING_NOISE:
            words.pop()
        if not words:
            continue
        if words[0].lower() in _NOT_A_NAME:
            continue
        name = " ".join(words).strip(" .,;:")
        if not name:
            continue
        if name.lower() == own or name.lower() in own:
            continue
        if name not in found:
            found.append(name)

    found.sort(key=len, reverse=True)
    return found[:2]


def counterparty_queries(name: str) -> List[str]:
    """Queries about the other company itself, not about our relationship to it.

    A relationship query ("Canada Goose YKK zipper pricing") returns the briefed
    company's coverage and mentions the supplier in passing. These ask about the
    supplier's own condition, which is what a negotiation needs to know: capacity,
    pricing power, who else it supplies.
    """
    stripped = " ".join((name or "").split())
    if not stripped:
        return []
    return [
        f"{stripped} results revenue guidance outlook",
        f"{stripped} capacity expansion production investment",
        f"{stripped} price increase cost pressure margin",
        f"{stripped} customers contracts supply agreement",
        f"{stripped} restructuring layoffs management change",
    ]
