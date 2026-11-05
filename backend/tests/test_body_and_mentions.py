"""Regression tests for body recovery and company-name matching.

Both were measured on live results, not invented:
- plain-text raw_content produced an empty body for every listed publisher
- a search for "IBM" discarded a headline that said "International Business
  Machines"
"""
from __future__ import annotations

from app.services.publisher_filter import (
    extract_clean_text,
    filter_results_for_company,
    mentions_company,
    rebuild_plain_prose,
)

# yahoo carries a body rule, so this also covers a listed publisher.
URL = "https://finance.yahoo.com/markets/stocks/articles/amd-q3-earnings"

MARKDOWN_BODY = """# AMD Stock Rises After Q3 Beat

AMD reported third-quarter revenue of 13.4 billion dollars, ahead of consensus
estimates. The company raised its outlook for the fourth quarter, citing
data-centre demand and accelerating Instinct GPU shipments.

Analysts said the data-centre segment carried the quarter, with Instinct GPU
shipments more than doubling from a year earlier. Management guided fourth
quarter revenue to 15 billion dollars, above the 14.2 billion consensus.

The stock rose 4 percent in extended trading on the result. Gross margin came
in at 54 percent, a point higher than the prior quarter, as the mix shifted
toward higher-margin server parts. Operating expenses rose 8 percent on
headcount additions in the engineering organisation.

Executives said supply constraints on advanced packaging would ease in the
first half of next year, and that customer demand for MI350 accelerators
remained ahead of the company's ability to ship them.
"""

# What include_raw_content="text" delivers: no headings, no paragraph breaks.
PLAIN_BODY = """AMD Stock Rises After Q3 Beat
AMD reported third-quarter revenue of 13.4 billion dollars, ahead of consensus estimates.
The company raised its outlook for the fourth quarter, citing data-centre demand and accelerating Instinct GPU shipments.
Analysts said the data-centre segment carried the quarter, with Instinct GPU shipments more than doubling from a year earlier.
Management guided fourth quarter revenue to 15 billion dollars, above the 14.2 billion consensus.
The stock rose 4 percent in extended trading on the result, and gross margin came in at 54 percent.
Operating expenses rose 8 percent on headcount additions in the engineering organisation.
Sign in
Subscribe
"""


def test_markdown_body_is_extracted():
    clean = extract_clean_text(MARKDOWN_BODY, URL)

    assert clean
    assert "13.4 billion" in clean


def test_plain_text_body_is_recovered_instead_of_dropped():
    # Measured: Trafilatura returned 0 chars for this shape, so the article was
    # lost entirely under include_raw_content="text".
    clean = extract_clean_text(PLAIN_BODY, URL)

    assert clean
    assert "13.4 billion" in clean


def test_plain_text_furniture_is_not_carried_over():
    clean = extract_clean_text(PLAIN_BODY, URL)

    assert "Sign in" not in clean
    assert "Subscribe" not in clean


def test_plain_prose_drops_short_lines():
    rebuilt = rebuild_plain_prose(PLAIN_BODY)

    assert "Sign in" not in rebuilt
    assert "13.4 billion" in rebuilt


def test_empty_content_stays_empty():
    assert rebuild_plain_prose("") == ""
    assert extract_clean_text("", URL) is None
    assert extract_clean_text(None, URL) is None


def test_a_headline_using_the_legal_name_is_kept():
    # Measured: the IBM query dropped "Why is International Business Machines
    # stock rallying today?" because only the ticker was matched.
    result = {
        "title": "Why is International Business Machines stock rallying today?",
        "url": "https://example.com/market/story-1234",
        "raw_content": "",
    }

    assert mentions_company(result, "IBM", ["International Business Machines"]) is True
    assert mentions_company(result, "IBM") is False


def test_aliases_do_not_rescue_unrelated_articles():
    result = {
        "title": "Domino's Q3 earnings beat expectations",
        "url": "https://example.com/dominos",
        "raw_content": "",
    }

    assert mentions_company(result, "AMD", ["Advanced Micro Devices"]) is False


def test_an_empty_alias_changes_nothing():
    result = {
        "title": "AMD beats on data-centre demand",
        "url": "https://example.com/amd",
        "raw_content": "",
    }

    assert mentions_company(result, "AMD", ["", None]) is True


def test_filter_keeps_a_legal_name_match():
    results = [
        {
            "title": "International Business Machines raises guidance",
            "url": "https://example.com/ibm-guidance",
            "raw_content": "",
        }
    ]

    kept = filter_results_for_company(results, "IBM", ["International Business Machines"])

    assert len(kept) == 1


def test_company_name_still_needs_a_word_boundary():
    result = {
        "title": "Senior engineer opinion on chip demand",
        "url": "https://example.com/story",
        "raw_content": "",
    }

    assert mentions_company(result, "NIO") is False
