# /backend/app/services/forward_looking.py
"""Separate a company's promises from what it has actually done.

A large company produces a steady stream of stated intent: a CEO promising a
product in two years, a target margin, a market it intends to own. Those sentences
are real — they were said — but they are not events, and a brief that lists them
beside filed results teaches the reader to weight them equally.

Two things follow:

1. They are classified out of the fact base before the model reasons, so the
   analysis and the recommendation rest on reported events only. A promise cannot
   be cited as evidence that something happened.
2. They are shown to the reader in their own lane, with the source, so the reader
   can see what is being promised without the brief endorsing it.

The classifier is deterministic and conservative: it fires on the grammar of
intent ("will", "expects to", "plans to", "by 2028"), not on topic. A sentence
that reports a completed action never matches, however ambitious it sounds.
"""
from __future__ import annotations

import re
from typing import List, Sequence

from app.models.fact_base import FactItem

# Grammar of intent. A promise is made about the future, so these are the
# markers that carry it. Word boundaries keep "will" from matching "willing".
INTENT_PATTERN = re.compile(
    r"(?<![a-z0-9])("
    r"will|would|plans? to|planning to|intends? to|intend to|"
    r"expects? to|expected to|aims? to|hope[sd]? to|"
    r"is set to|are set to|is poised to|are poised to|"
    r"looking to|seeks? to|targets?|guidance|forecast|outlook|"
    r"by 20\d{2}|in 20\d{2}|over the next|within (?:the next|three|five)"
    r")(?![a-z0-9])",
    re.IGNORECASE,
)

# A promise usually also carries a future horizon or a goal noun. Requiring one
# keeps ordinary reported speech ("he said the quarter was strong") out. A horizon
# can be a calendar year, a relative window ("over the next two years"), or a
# goal noun, because a promise is expressed with whatever framing the speaker had.
HORIZON_PATTERN = re.compile(
    r"(?<![a-z0-9])("
    r"20\d{2}|"
    r"next (?:quarter|year|fiscal|decade|two years|three years|five years)|"
    r"over the next|within the next|by the end of|"
    r"long[- ]term|multi[- ]year|coming (?:years|months|quarters)|"
    r"target|goal|roadmap|pipeline|ambition|market capitalisation|market cap"
    r")(?![a-z0-9])",
    re.IGNORECASE,
)

# Reported events. A sentence containing one of these is describing what happened,
# even if it also mentions a plan, and stays in the fact base.
COMPLETED_ACTION_PATTERN = re.compile(
    r"(?<![a-z0-9])("
    r"reported|reports|announced|announces|posted|recorded|"
    r"filed|filing|launched|signed|completed|acquired|appointed|"
    r"rose|fell|grew|declined|dropped|missed|beat|"
    r"said on|according to|as of"
    r")(?![a-z0-9])",
    re.IGNORECASE,
)


def is_forward_looking(text: str) -> bool:
    """True when a sentence states intent about the future rather than an event."""
    line = " ".join((text or "").split())
    if not line:
        return False

    if not INTENT_PATTERN.search(line):
        return False

    # An event that merely mentions a plan is still an event.
    if COMPLETED_ACTION_PATTERN.search(line):
        return False

    return bool(HORIZON_PATTERN.search(line))


def split_forward_looking(
    facts: Sequence[FactItem],
) -> tuple[List[FactItem], List[FactItem]]:
    """Return (events, promises). Promises are removed from the model's evidence."""
    events: List[FactItem] = []
    promises: List[FactItem] = []

    for fact in facts:
        if is_forward_looking(fact.content):
            promises.append(fact)
        else:
            events.append(fact)

    return events, promises
