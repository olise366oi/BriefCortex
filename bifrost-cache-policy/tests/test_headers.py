"""The header contract a Bifrost gateway reads, and the decision object.

Bifrost caches only when a request carries `x-bf-cache-key`, and it honours
`x-bf-cache-ttl` as a per-request override of its configured default. Getting
these three headers right is the entire integration: everything else the
gateway does itself.
"""
from __future__ import annotations

from bifrost_cache_policy.headers import (
    CACHE_KEY_HEADER,
    CACHE_TTL_HEADER,
    CONTENT_CLASS_HEADER,
    build_cache_headers,
    build_cache_key,
    cache_key_to_clear,
    decide,
)

EARNINGS_FACT = "NIO reported third quarter earnings and cut its full-year guidance."
HIRING_FACT = "NIO appointed a new chief financial officer this month."
FILING_FACT = "NIO filed its annual report, disclosing segment revenue by brand."


# --- the key -------------------------------------------------------------


def test_the_key_is_stable_for_the_same_request():
    one = build_cache_key("nio-abc", "pricing", [EARNINGS_FACT])
    two = build_cache_key("nio-abc", "pricing", [EARNINGS_FACT])

    assert one == two


def test_the_key_changes_with_the_objective():
    assert build_cache_key("nio-abc", "pricing", [EARNINGS_FACT]) != build_cache_key(
        "nio-abc", "renewal", [EARNINGS_FACT]
    )


def test_the_key_changes_with_the_evidence():
    assert build_cache_key("nio-abc", "pricing", [EARNINGS_FACT]) != build_cache_key(
        "nio-abc", "pricing", [HIRING_FACT]
    )


def test_the_key_changes_with_the_partition():
    # Two companies with identical briefs must never share a cache entry.
    assert build_cache_key("nio-abc", "pricing", [EARNINGS_FACT]) != build_cache_key(
        "nio-xyz", "pricing", [EARNINGS_FACT]
    )


def test_the_key_ignores_evidence_order():
    # Retrieval returns facts in whatever order its lanes finished.
    assert build_cache_key("nio-abc", "pricing", [EARNINGS_FACT, HIRING_FACT]) == (
        build_cache_key("nio-abc", "pricing", [HIRING_FACT, EARNINGS_FACT])
    )


def test_the_key_is_header_safe():
    # Bifrost clears by putting the key in a URL path, so it must not carry
    # separators or whitespace that need escaping in the common case.
    key = build_cache_key("Nio Auto Group", "reduce price by 15% / hold out", [EARNINGS_FACT])

    assert " " not in key
    assert "/" not in key
    assert key.isascii()


# --- headers -------------------------------------------------------------


def test_headers_carry_the_three_fields_the_gateway_reads():
    headers = build_cache_headers("nio-abc", "pricing", [EARNINGS_FACT])

    assert set(headers) == {CACHE_KEY_HEADER, CACHE_TTL_HEADER, CONTENT_CLASS_HEADER}


def test_labels_the_class_for_observability():
    headers = build_cache_headers("nio-abc", "pricing", [EARNINGS_FACT])

    assert headers[CONTENT_CLASS_HEADER] == "volatile"
    assert headers[CACHE_TTL_HEADER] == "24h"


def test_a_filing_request_gets_the_longest_ttl():
    headers = build_cache_headers("nio-abc", "pricing", [FILING_FACT])

    assert headers[CONTENT_CLASS_HEADER] == "primary"
    assert headers[CACHE_TTL_HEADER] == "168h"


def test_the_ttl_is_a_bifrost_duration_string():
    # The gateway parses "24h"; a bare number would be read as seconds.
    for fact_text in (EARNINGS_FACT, HIRING_FACT, FILING_FACT):
        ttl = build_cache_headers("nio-abc", "", [fact_text])[CACHE_TTL_HEADER]
        assert ttl.endswith("h")
        assert ttl[:-1].isdigit()


def test_the_header_values_are_plain_strings():
    # They go straight onto an HTTP request; a non-str would be silently coerced
    # or rejected by the SDK, so the type is part of the contract.
    headers = build_cache_headers("nio-abc", "pricing", [EARNINGS_FACT])

    for name, value in headers.items():
        assert isinstance(name, str)
        assert isinstance(value, str)


# --- decision object -----------------------------------------------------


def test_the_decision_carries_class_and_ttl():
    decision = decide(
        "nio-x",
        "",
        ["Shares rallied 6% after the earnings beat.", "Guidance was raised."],
    )

    assert decision.content_class == "volatile"
    assert decision.ttl == "24h"
    assert decision.cache_key
    assert decision.headers[CONTENT_CLASS_HEADER] == "volatile"


def test_the_decision_key_matches_the_header():
    decision = decide("nio-x", "pricing", [EARNINGS_FACT])

    assert decision.cache_key == decision.headers[CACHE_KEY_HEADER]


def test_the_decision_names_the_key_to_clear_on_the_gateway():
    decision = decide("nio-x", "pricing", [EARNINGS_FACT])

    # DELETE /api/cache/clear-by-key/{cacheKey}
    assert cache_key_to_clear(decision) == decision.cache_key


def test_the_decision_can_be_built_with_a_custom_vocabulary():
    decision = decide(
        "nio-x",
        "",
        ["The company announced a reorganisation of its sales unit."],
        {"volatile": (), "structural": ("reorganisation",), "primary": ()},
    )

    assert decision.content_class == "structural"
    assert decision.ttl == "72h"
