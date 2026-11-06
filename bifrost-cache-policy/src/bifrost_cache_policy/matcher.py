"""Whole-word keyword matching, vendored so this package has no dependencies.

A keyword matches at a word boundary with its common inflections, so "miss"
cannot match inside "mission" while "missed" and "missing" still match. A
keyword written with a trailing "*" is a deliberate stem: it matches any word
that starts with it.

The boundary rule is the reason this is not `keyword in text`. Substring
matching reads "third quarter" as a hiring signal because "hir" sits inside it,
and "shareholders" as a share price because "shares" sits inside it. Both were
measured on live press text before the rule was written.
"""
from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=None)
def _compile(keyword: str) -> re.Pattern[str]:
    keyword = keyword.strip().lower()

    if keyword.endswith("*"):
        return re.compile(r"\b" + re.escape(keyword[:-1]) + r"\w*", re.IGNORECASE)

    if keyword.endswith("e"):
        # Final-e stems drop the e before -ing/-ed ("plunge" -> "plunging").
        dropped = re.escape(keyword[:-1])
        return re.compile(
            r"\b"
            + re.escape(keyword)
            + r"(?:s|d)?\b"
            + r"|\b"
            + dropped
            + r"(?:ing|ed|es)\b",
            re.IGNORECASE,
        )

    return re.compile(
        r"\b" + re.escape(keyword) + r"(?:s|es|ed|ing)?\b", re.IGNORECASE
    )


def match_word(text: str, keyword: str) -> bool:
    """Return True when the keyword appears in text as a whole word."""
    return _compile(keyword).search(text) is not None


def matched_keyword(text: str, keywords: tuple[str, ...]) -> str | None:
    """Return the first keyword that matches text as a whole word."""
    for keyword in keywords:
        if match_word(text, keyword):
            return keyword
    return None
