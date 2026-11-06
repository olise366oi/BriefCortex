# /backend/app/services/keywords.py
"""Word-boundary keyword matching shared by extraction and conflict rules.

A keyword matches at a word boundary with its common inflections, so "miss"
cannot match inside "mission" while "missed" and "missing" still match. A
keyword written with a trailing "*" is a deliberate stem: it matches any word
that starts with it, which is only used where every extension is relevant.

The matcher itself lives in `bifrost_cache_policy`, the standalone package this
repository publishes, because the gateway cache rules classify evidence with the
same operator and the two must not diverge. This module is the import site the
rest of the pipeline uses.
"""
from __future__ import annotations

from bifrost_cache_policy.matcher import (  # noqa: F401
    match_word,
    matched_keyword,
)

__all__ = ["match_word", "matched_keyword"]
