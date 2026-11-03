"""Tests for the content-aware cache headers."""
from __future__ import annotations

from app.services.bifrost_headers import (
    build_bifrost_headers,
    classify_fact_texts,
    classify_ttl,
)

EARNINGS_FACT = "NIO reported third quarter earnings and cut its full-year guidance."
HIRING_FACT = "NIO appointed a new chief financial officer this month."
FILING_FACT = "NIO filed its annual report, disclosing segment revenue by brand."


def test_ordinal_is_not_a_hiring_signal():
    # "third quarter" contains "hir" as a substring; whole-word matching must
    # not read it as a hiring signal.
    assert classify_fact_texts([EARNINGS_FACT]) == "volatile"


def test_shareholders_are_not_share_prices():
    # "shares" must not fire inside "shareholders", so the filing class wins here.
    assert (
        classify_fact_texts([FILING_FACT, "NIO's largest shareholders met the board."])
        == "primary"
    )


def test_structural_class_for_appointments():
    assert classify_fact_texts([HIRING_FACT]) == "structural"
    assert classify_ttl("structural") == "72h"


def test_primary_class_for_filings():
    assert classify_fact_texts([FILING_FACT]) == "primary"
    assert classify_ttl("primary") == "168h"


def test_most_perishable_member_wins():
    assert classify_fact_texts([FILING_FACT, HIRING_FACT, EARNINGS_FACT]) == "volatile"


def test_cache_key_changes_with_purpose_and_evidence():
    headers = build_bifrost_headers("nio-abc", "pricing", [EARNINGS_FACT])
    same = build_bifrost_headers("nio-abc", "pricing", [EARNINGS_FACT])
    other_purpose = build_bifrost_headers("nio-abc", "renewal", [EARNINGS_FACT])
    other_evidence = build_bifrost_headers("nio-abc", "pricing", [HIRING_FACT])

    assert headers["x-bf-cache-key"] == same["x-bf-cache-key"]
    assert headers["x-bf-cache-key"] != other_purpose["x-bf-cache-key"]
    assert headers["x-bf-cache-key"] != other_evidence["x-bf-cache-key"]


def test_labels_the_class_for_observability():
    headers = build_bifrost_headers("nio-abc", "pricing", [EARNINGS_FACT])
    assert headers["x-bf-content-class"] == "volatile"
    assert headers["x-bf-cache-ttl"] == "24h"
