"""TTL grading: the TTL is a property of the evidence, not of the request.

These cases are the ones that broke a substring matcher on real press text:
"third quarter" contains "hir", and "shareholders" contains "shares". Both
would silently downgrade a filing to a 24-hour cache entry.
"""
from __future__ import annotations

from bifrost_cache_policy.ttl import (
    CLASS_KEYWORDS,
    FACT_CLASSES,
    TTL_BY_CLASS,
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
    # "shares" must not fire inside "shareholders", so the filing class wins.
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


def test_volatile_class_for_market_material():
    assert classify_fact_texts(["Shares rallied 6% after the earnings beat."]) == "volatile"
    assert classify_ttl("volatile") == "24h"


def test_most_perishable_member_wins():
    # A set is only as cacheable as its most perishable member.
    assert classify_fact_texts([FILING_FACT, HIRING_FACT, EARNINGS_FACT]) == "volatile"


def test_filing_only_evidence_keeps_the_longest_ttl():
    assert classify_fact_texts([FILING_FACT]) == "primary"
    assert classify_ttl(classify_fact_texts([FILING_FACT])) == "168h"


def test_unknown_evidence_falls_back_to_the_shortest_ttl():
    # Fail closed: evidence nobody classified must not be kept for a week.
    assert classify_fact_texts(["The company met its delivery schedule."]) == "volatile"


def test_an_empty_evidence_set_falls_back_to_the_shortest_ttl():
    assert classify_fact_texts([]) == "volatile"


def test_precedence_order_is_volatile_then_structural_then_primary():
    assert FACT_CLASSES == ("volatile", "structural", "primary")


def test_every_class_has_a_ttl_and_a_vocabulary():
    for fact_class in FACT_CLASSES:
        assert fact_class in TTL_BY_CLASS
        assert CLASS_KEYWORDS[fact_class]


def test_class_lookup_is_case_insensitive():
    assert classify_ttl("STRUCTURAL") == "72h"
    assert classify_ttl(" primary ") == "168h"


def test_an_unknown_class_name_falls_back_rather_than_raising():
    assert classify_ttl("nonsense") == TTL_BY_CLASS["volatile"]


def test_the_vocabulary_can_be_widened_by_the_caller():
    # A deployment adds its own signal without forking the precedence rule.
    vocabulary = dict(CLASS_KEYWORDS)
    vocabulary["structural"] = ("reorganisation", "restructuring")
    vocabulary["volatile"] = ()

    assert classify_fact_texts(["The company announced a reorganisation."], vocabulary) == (
        "structural"
    )


def test_a_wider_vocabulary_does_not_lose_whole_word_matching():
    vocabulary = dict(CLASS_KEYWORDS)
    vocabulary["structural"] = ("restructuring",)

    assert classify_fact_texts(["The restructuring completed."], vocabulary) == "structural"
    assert classify_fact_texts(["The restructuring team met."], vocabulary) == "structural"
    # Still a whole-word test: a longer word containing the keyword is not a hit.
    assert classify_fact_texts(["Restructuringside effects."], vocabulary) == "volatile"
