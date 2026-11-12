"""What a measured Wipro run put into the fact base that was not evidence.

Wipro is an Indian IT-services company, so its coverage lives in Indian business
press — a different publisher set from the Japanese and Canadian companies
earlier runs were measured on. Four defects were specific to that set:

1. **A publisher's company hub and its stock-statistics page.** 55 of 267 hard
   facts came from `economictimes.indiatimes.com/<company>/stocks/companyid-*.cms`
   (PE ratio, VWAP, dividend yield, and "Investments in securities are subject to
   market risks") and from `/topic/...` pages, which are an index of the site's
   coverage rather than one story.
2. **A share-price live blog.** Five facts, including "The data points are
   updated as on 03:40:13 PM IST, 12 Nov 2026", came from a page whose whole
   purpose is quote ticks.
3. **Third-party job boards.** Naukri and a "how to apply for Wipro jobs" page
   contributed thirteen template lines to the *news* lane, because
   `is_careers_url` does not match `naukri.com/wipro-jobs-careers-1384`.
4. **Sector and index columns.** "Nifty IT index surges" and "IT stocks crash"
   report a dozen companies in sequence, so their figures and their layoffs
   belong to the peers; four false headcount conflicts came from one of them.

Each is a broken claim a reader could act on, so each gets its own gate below.
"""
from __future__ import annotations

import pytest

from app.services.fact_extraction import is_structural_noise
from app.services.hiring import is_careers_url, is_job_board
from app.services.media_trust import is_roundup_url
from app.services.publisher_filter import (
    filter_results_for_company,
    is_kept_result,
)

# --- 1. a company hub and a stock-statistics page are not stories ---------

COMPANY_HUB_URLS = (
    "https://economictimes.indiatimes.com/wipro-ltd/stocks/companyid-12799.cms",
    "https://economictimes.indiatimes.com/topic/wipro-management-issues",
    "https://economictimes.indiatimes.com/topic/wipro-deal",
    "https://www.financialexpress.com/about/wipro",
)

REAL_STORY_URLS = (
    "https://www.livemint.com/market/stock-market-news/"
    "it-stocks-crash-infosys-tcs-wipro-hcl-tech-nifty-it-2026.html",
    "https://www.moneycontrol.com/news/business/earnings/"
    "wipro-consolidated-september-2026-net-sales-at-rs.html",
    "https://economictimes.indiatimes.com/tech/technology/"
    "us-firm-contentnexus-files-patent-suit-against-wipro/articleshow/131251038.cms",
    "https://www.globeandmail.com/investing/markets/stocks/GOOS-N/"
    "pressreleases/8699611/canada-goose-reports/",
)


@pytest.mark.parametrize("url", COMPANY_HUB_URLS)
def test_a_hub_or_statistics_page_is_not_kept(url):
    assert is_kept_result(url) is False


@pytest.mark.parametrize("url", REAL_STORY_URLS)
def test_a_real_story_in_the_same_directory_is_kept(url):
    # The exclusion has to be the page shape, not the directory: a bare
    # "/markets/stocks/" also dropped a Canada Goose press release.
    assert is_kept_result(url) is True


@pytest.mark.parametrize(
    "text",
    [
        "| Share Price | Value | | --- | --- | | Today/Current/Last | 190.10 |",
        "Dividend Yield calculates the amount of full year dividend declared by "
        "a company as a percentage.",
        "Earnings per share (TTM) tells the profit after tax earned on a per "
        "share basis by a stock over time.",
        "All other things being equal, higher the dividend yield of the stock, "
        "the better it is for investors.",
        "Investments in securities are subject to market risks.",
        "These are indicative and should not be interpreted as investment advice.",
    ],
)
def test_a_statistics_page_explains_itself_rather_than_reporting(text):
    assert is_structural_noise(text) is True


# --- 2. a share-price live blog -------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "IRFC Share Price85.32-30.8% 02:47 PM | 12 Nov 2026-1.39(-1.6%)Upside",
        "| | | | --- | --- | | IRFC Share Price | 85.29 | -30.8% | 03:31 PM |",
        "The data points are updated as on 03:40:13 PM IST, 12 Nov 2026",
    ],
)
def test_a_quote_tick_is_not_a_fact(text):
    assert is_structural_noise(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Wipro reported a 9.1% year-on-year rise in Q2FY27 revenue to "
        "Rs 24,752.7 crore, while net profit fell 1.9% to Rs 3,199.7 crore.",
        "The company declared an interim dividend of Rs 5 per share for the "
        "quarter, payable to shareholders on record.",
        "The Nifty IT index gained 2.5% amid a selloff in global AI-linked "
        "stocks, with Wipro among the gainers.",
    ],
)
def test_real_indian_business_reporting_survives(text):
    assert is_structural_noise(text) is False


def test_a_live_blog_page_is_not_read():
    live_blog = (
        "https://economictimes.indiatimes.com/markets/stocks/stock-liveblog/"
        "wipro-share-price-today-live-02-nov-2026/liveblog/135443306.cms"
    )

    assert is_kept_result(live_blog) is False


# --- 3. third-party job boards --------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.naukri.com/wipro-jobs-careers-1384",
        "https://www.hiringstores.com/2024/08/how-to-apply-for-wipro-jobs-2024-",
        "https://www.foundit.in/search/jobs?q=wipro",
    ],
)
def test_an_aggregator_is_recognised_as_a_job_board(url):
    assert is_job_board(url) is True


@pytest.mark.parametrize(
    "url",
    [
        # The employer's own board, reached through a vendor: the hiring lane's
        # source, not an aggregator.
        "https://canadagoose.wd3.myworkdayjobs.com/en-US/CanadaGoose",
        "https://jobs.lever.co/somecompany",
        # A reporter's article about hiring.
        "https://www.financialexpress.com/business/industry/"
        "it-hiring-in-q2-infosys-hires-4480-staff-hcltech-tech.html",
    ],
)
def test_a_careers_board_is_not_an_aggregator(url):
    assert is_job_board(url) is False


def test_a_job_board_never_reaches_the_news_lane():
    # `naukri.com/wipro-jobs-careers-1384` has no `/jobs/` path segment, so it
    # used to fall through `is_careers_url` and reach the news lane directly.
    url = "https://www.naukri.com/wipro-jobs-careers-1384"
    results = [
        {
            "url": url,
            "title": "Wipro jobs",
            "raw_content": "Wipro3.672314 Reviews 0 Yrs Navi Mumbai Job description",
        },
        {
            "url": REAL_STORY_URLS[1],
            "title": "Wipro Q2 results",
            "raw_content": "Wipro reported revenue of Rs 24,752.7 crore.",
        },
    ]

    kept = filter_results_for_company(results, "Wipro")

    assert [item["url"] for item in kept] == [REAL_STORY_URLS[1]]


def test_the_aggregator_rule_does_not_swallow_a_careers_page():
    # The distinction the rule exists for: who published the listing.
    assert is_careers_url("https://canadagoose.wd3.myworkdayjobs.com/en-US/CanadaGoose") is True
    assert is_job_board("https://canadagoose.wd3.myworkdayjobs.com/en-US/CanadaGoose") is False


# --- 4. sector and index columns ------------------------------------------

INDEX_COLUMN_URLS = (
    "https://www.moneycontrol.com/news/business/markets/"
    "nifty-it-index-surges-2-5-amid-global-ai-sell-off.html",
    "https://www.livemint.com/market/stock-market-news/"
    "it-stocks-crash-infosys-tcs-wipro-hcl-tech-nifty-it-2026.html",
)

SINGLE_COMPANY_URLS = (
    "https://www.moneycontrol.com/news/business/earnings/"
    "wipro-consolidated-september-2026-net-sales-at-rs.html",
    "https://www.prnewswire.com/news-releases/"
    "labelbox-partners-with-wipro-to-scale-adoption-of-ai-agents.html",
)


@pytest.mark.parametrize("url", INDEX_COLUMN_URLS)
def test_an_index_or_sector_column_is_a_roundup(url):
    # "Coforge gained over 4%, Wipro and Persistent about 3% and 2%, and Infosys
    # more than 2%" — every sentence is true and none of them belongs to one
    # company.
    assert is_roundup_url(url) is True


@pytest.mark.parametrize("url", SINGLE_COMPANY_URLS)
def test_a_single_company_story_is_not_a_roundup(url):
    assert is_roundup_url(url) is False
