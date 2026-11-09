# /backend/app/services/number_check.py
"""Every figure in a claim must exist somewhere in the evidence.

The LLM audit asks one narrow question per claim and costs a model call. A figure
the evidence never states is cheaper to catch by string comparison, and it is the
failure mode that reached a reader: a brief reported a net loss of C$26.3m and an
inventory of C$497m, both true, alongside figures that were not in any source.

This check is deliberately one-sided. It flags a number only when **no** evidence
fact contains it — not when the number is missing from the one cited source. A
claim that consolidates several facts legitimately states figures from all of them,
and failing those would empty a correct brief, which is the failure the audit
already had to be repaired for. Misattribution stays the model audit's job;
fabrication is caught here for no model cost.
"""
from __future__ import annotations

import re
from typing import Iterable, List

# A number as a brief writes it: an optional currency or scale marker, the digits
# with the separators publishers use, and an optional unit.
#
# "1,348.4", "C$274.0 million", "5.9%", "280 bp", "$1.5bn" all have to reduce to
# a comparable value, because the evidence and the claim rarely write a figure the
# same way: a filing says "1,348.4" and the model writes "C$1,348.4 million".
_NUMBER = re.compile(
    r"""
    (?:[C€£$¥]|CAD|USD|EUR|GBP|RMB|JPY)?      # currency marker, optional
    \s*
    (?P<body>[0-9][0-9.,']*(?:\.\d+)?)
    \s*
    (?:%|percent|pct|bps|bp|basis\s+points|
       million|billion|trillion|bn|m|k)?      # scale or unit, optional
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Figures below this are not evidence of anything on their own and appear in
# almost any sentence ("Q2", "part 2"). A fabricated material figure is rarely a
# single digit — but a percentage carries meaning at two digits ("up 11%"), so
# percentages are exempt from the length floor.
MIN_MATERIAL_DIGITS = 3

_PERCENT = re.compile(r"%|percent|pct|bps|basis\s+points", re.IGNORECASE)

# A year is checked like any other figure, but it is carried by almost every
# fiscal reference, so it is compared as a whole year rather than as a value.
_YEAR = re.compile(r"(1[89]\d\d|20\d\d|21\d\d)")

_TRAILING_SEPARATORS = ".,'"


def normalize_figure(raw: str) -> str:
    """Return a comparable form of one written figure.

    Separators are removed and a trailing separator dropped, so "1,348.4" and
    "1348.4" compare equal while "5.9" stays distinct from "59".
    """
    body = raw.strip().strip(_TRAILING_SEPARATORS)
    body = body.replace(",", "").replace("'", "")
    # "274." from "C$274.0m." — a trailing point is punctuation, not a decimal.
    if body.endswith("."):
        body = body[:-1]
    return body


def figures_in(text: str) -> List[str]:
    """Return the material figures a piece of text states."""
    found: List[str] = []
    for match in _NUMBER.finditer(text or ""):
        value = normalize_figure(match.group("body"))
        if not value:
            continue
        digits = value.replace(".", "")
        # A percentage is meaningful at any width; a bare number is not.
        is_rate = bool(_PERCENT.search(match.group(0)))
        if len(digits) < MIN_MATERIAL_DIGITS and not is_rate:
            continue
        if value not in found:
            found.append(value)
    return found


def unsupported_numbers(
    claim_text: str,
    evidence_texts: Iterable[str],
) -> List[str]:
    """Return the figures a claim states that no evidence fact contains.

    Returns an empty list when everything the claim states is somewhere in the
    evidence, which is the common case and the one that must never be flagged.
    """
    claimed = figures_in(claim_text)
    if not claimed:
        return []

    available: set[str] = set()
    for text in evidence_texts:
        available.update(figures_in(text))

    # A year is also matched bare inside a date ("2026-11-09"), which the figure
    # regex reads as 2026, so both forms are already covered by the same form.
    missing = [value for value in claimed if value not in available]

    # A claim may state a figure the evidence writes with a different scale
    # ("2.7" vs "2.70"), so compare numeric values once as a fallback.
    if missing:
        values = {float(v) for v in available if _is_float(v)}
        missing = [
            v for v in missing
            if not (_is_float(v) and float(v) in values)
        ]
    return missing


def _is_float(value: str) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def year_references(text: str) -> List[str]:
    """Return the four-digit years a text references."""
    return [match.group(0) for match in _YEAR.finditer(text or "")]
