# /backend/app/services/report_order.py
"""Put a generated brief's news into the order a reader expects.

The rule is fixed, so it is applied here rather than requested from the model:
key developments lead, and within each group the newest comes first. A model can
mark what matters and supply dates, but ordering it consistently across every
brief is cheaper to enforce than to ask for.
"""
from __future__ import annotations

from app.models.briefing import BriefingReport, ClaimModule

# No date sorts below every real date: an undated item is not known to be new.
UNDATED_SORT_KEY = "0000-00-00"


def _sort_key(claim) -> tuple:
    """Key events first, then newest first, then stable by text."""
    date = claim.date or UNDATED_SORT_KEY
    return (0 if claim.key_event else 1, _inverted(date), claim.claim[:40])


def _inverted(iso_date: str) -> str:
    """Sort dates descending by inverting the lexical order.

    ISO dates sort ascending as strings, so reversing the characters of a
    fixed-width date gives a descending key without parsing. Undated items carry
    the sentinel, which inverts to a high value and therefore sorts last.
    """
    return "".join(chr(255 - ord(ch)) if ord(ch) < 128 else ch for ch in iso_date)


def order_news(report: BriefingReport) -> BriefingReport:
    """Return the report with news and hires in reader order."""
    ordered_news = ClaimModule(items=sorted(report.news.items, key=_sort_key))
    ordered_hires = ClaimModule(items=sorted(report.hires.items, key=_sort_key))
    return report.model_copy(update={"news": ordered_news, "hires": ordered_hires})
