"""The caller's own company is scanned for market position and nothing more.

Two things are asserted here, and they are the design:

* the snapshot carries a quote and market coverage, never postings;
* it reaches the analysis stage, where "how much room do we have" is part of
  what the evidence amounts to, and never the findings stage.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.briefing import Analysis
from app.services.market_data import Quote
from app.services.own_company_scan import OwnCompanySnapshot
from app.services.report_stages import (
    ANALYSIS_PROMPT,
    render_analysis_input,
    render_findings_input,
    render_recommendation_input,
)
from app.models.fact_base import FactBase, FactItem


def _quote() -> Quote:
    return Quote(
        ticker="GOOS",
        price=8.96,
        currency="USD",
        previous_close=8.91,
        as_of=datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc),
        source_url="https://finance.yahoo.com/quote/GOOS",
    )


def test_a_snapshot_renders_quote_and_headlines():
    snapshot = OwnCompanySnapshot(
        company="Canada Goose",
        quote=_quote(),
        headlines=[{"title": "Q3 earnings", "url": "https://x/1",
                    "published_date": "Wed, 05 Nov 2026 00:00:00 GMT"}],
    )

    block = snapshot.to_prompt_block()

    assert "Canada Goose" in block
    assert "GOOS 8.96 USD" in block
    assert "Q3 earnings" in block


def test_an_empty_snapshot_renders_nothing():
    assert OwnCompanySnapshot(company="Canada Goose").to_prompt_block() == ""


def test_the_own_company_window_is_sixty_days():
    # A counterparty gets 30 days; our own baseline gets 60, because one month is
    # too short to characterise the trend every deal is argued against.
    from app.services.own_company_scan import OWN_COMPANY_WINDOW_DAYS
    from app.services.retention import OWN_COMPANY_RETENTION_DAYS

    assert OWN_COMPANY_WINDOW_DAYS == 60
    assert OWN_COMPANY_RETENTION_DAYS == 60


def test_negative_coverage_is_kept_alongside_positive():
    # Nothing filters by sentiment: a company that only remembers its good news
    # cannot be assessed honestly.
    snapshot = OwnCompanySnapshot(
        company="Canada Goose",
        headlines=[
            {"title": "Canada Goose shares fall after guidance cut", "url": "https://x/1"},
            {"title": "Canada Goose opens new flagship store", "url": "https://x/2"},
        ],
    )

    block = snapshot.to_prompt_block()

    assert "shares fall" in block
    assert "opens new flagship" in block


def test_a_snapshot_without_a_quote_is_still_usable():
    # YKK is private, so no quote exists. The headlines alone are still context.
    snapshot = OwnCompanySnapshot(
        company="YKK",
        headlines=[{"title": "cyberattack", "url": "https://x/1"}],
    )

    assert snapshot.has_content is True
    assert "cyberattack" in snapshot.to_prompt_block()


def test_the_own_market_block_reaches_the_analysis():
    block = OwnCompanySnapshot(
        company="Canada Goose", quote=_quote()
    ).to_prompt_block()

    rendered = render_analysis_input(
        news=[], hires=[], quote=None, company="YKK",
        coverage_level="strong", lanes_missing=[],
        own_company_block=block,
    )

    assert "<OUR_COMPANY_MARKET_POSITION>" in rendered
    assert "GOOS 8.96 USD" in rendered


def test_the_own_market_block_never_reaches_the_findings():
    fact_base = FactBase(
        company_id="c",
        last_refresh_date=datetime.now(timezone.utc),
        hard_facts=[FactItem(content="YKK had an incident.", source_url="https://x/1")],
        analyst_opinions=[],
    )

    rendered = render_findings_input(fact_base, "", "YKK")

    assert "OUR_COMPANY_MARKET_POSITION" not in rendered


def test_the_analysis_prompt_forbids_attributing_our_view_to_them():
    # The point is how OUR position bears on the deal, not what they think of us.
    assert "not the counterparty's opinion of us" in ANALYSIS_PROMPT
    assert "Do not write" in ANALYSIS_PROMPT


def test_the_recommendation_still_gets_the_profile_not_the_market_block():
    rendered = render_recommendation_input(
        Analysis(assessment="thin record", risks=["cyber incident undisclosed"]),
        "first order of 50,000 zippers", "YKK",
        "we have no qualified second source", "strong",
    )

    assert "<OUR_COMPANY_PROFILE>" in rendered
    assert "no qualified second source" in rendered
    # The market block belongs to the analysis, not here.
    assert "OUR_COMPANY_MARKET_POSITION" not in rendered
