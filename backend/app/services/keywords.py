# /backend/app/services/keywords.py
"""Word-boundary keyword matching shared by extraction and conflict rules.

A keyword matches at a word boundary with its common inflections, so "miss"
cannot match inside "mission" while "missed" and "missing" still match. A
keyword written with a trailing "*" is a deliberate stem: it matches any word
that starts with it, which is only used where every extension is relevant.
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
