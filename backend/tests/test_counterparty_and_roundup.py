"""Two leaks: multi-company columns, and the counterparty nobody searched.

Both measured on the Canada Goose procurement brief.

1. A "small caps to watch" column contributed eight facts, seven of them about
   other companies (MDA Space's revenue, Savaria's price move, a table of six
   unrelated tickers). The page is a legitimate format whose numbers have no
   single owner.
2. Every query carried only the briefed company, so the supplier named in the
   objective was never looked up. The brief concluded "silent on YKK" — which was
   true, and was the pipeline's own fault.
"""
from __future__ import annotations

from app.services.counterparty import counterparties, counterparty_queries
from app.services.media_trust import is_roundup_url
from app.services.publisher_filter import filter_results_for_company

ROUNDUP_URLS = (
    "https://www.theglobeandmail.com/investing/markets/inside-the-market/"
    "article-small-caps-to-watch-earnings",
    "https://www.cnbc.com/2026/10/30/stocks-to-watch-today.html",
    "https://www.reuters.com/markets/market-movers-wall-street",
)

SINGLE_STORY_URLS = (
    "https://www.theglobeandmail.com/investing/markets/stocks/GOOS-N/"
    "pressreleases/8699611/canada-goose-reports/",
    "https://finance.yahoo.com/markets/stocks/articles/"
    "canada-goose-q2-earnings-call-112125070.html",
    "https://www.bnnbloomberg.ca/business/company-news/2026/11/05/"
    "canada-goose-reports-263m-q3-loss-revenue-up-from-year-ago",
)


def test_a_multi_company_column_is_recognised():
    for url in ROUNDUP_URLS:
        assert is_roundup_url(url) is True, url


def test_a_single_company_story_is_not_a_roundup():
    for url in SINGLE_STORY_URLS:
        assert is_roundup_url(url) is False, url


def test_a_roundup_never_reaches_the_result_set():
    results = [
        {
            "url": ROUNDUP_URLS[0],
            "title": "Small caps to watch: Canada Goose",
            "raw_content": "Canada Goose Holdings Inc. (GOOS-T) reported ...",
        },
        {
            "url": SINGLE_STORY_URLS[0],
            "title": "Canada Goose reports second quarter",
            "raw_content": "Canada Goose reported ...",
        },
    ]

    kept = filter_results_for_company(results, "Canada Goose")

    assert len(kept) == 1
    assert kept[0]["url"] == SINGLE_STORY_URLS[0]


def test_the_counterparty_named_in_an_objective_is_found():
    assert counterparties("buy zipper supplies from YKK", "Canada Goose") == ["YKK"]
    assert counterparties("renew the deal with YKK Holdings", "Canada Goose") == [
        "YKK Holdings"
    ]


def test_the_briefed_company_is_never_its_own_counterparty():
    assert counterparties("renew the deal with Canada Goose", "Canada Goose") == []


def test_an_objective_naming_nobody_yields_nothing():
    for purpose in ("assess acquiring YKK", "reduce price by 15%", ""):
        assert counterparties(purpose, "Canada Goose") == [], purpose


def test_a_lowercase_phrase_is_not_mistaken_for_a_company():
    # "reduce price by 15% from current levels" must not invent a firm called
    # "Current".
    assert counterparties("reduce price by 15% from current levels", "Canada Goose") == []


def test_the_counterparty_queries_ask_about_the_supplier_itself():
    queries = counterparty_queries("YKK")

    assert queries
    assert all(query.startswith("YKK ") for query in queries)
    # Not a relationship query ("Canada Goose YKK zipper pricing"): those return
    # the briefed company's coverage.
    assert not any("Canada Goose" in query for query in queries)
