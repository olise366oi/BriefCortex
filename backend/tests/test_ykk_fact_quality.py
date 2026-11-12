"""What a measured YKK run put into the fact base that was not evidence.

Four separate defects, all found on one run against a Japanese company:

1. **Another company's earnings, filed as ours.** `Boreo Oyj (FRA:YKK) Q3 2026
   Earnings Call Transcript` names Boreo; "YKK" there is Boreo's Frankfurt
   symbol. Nine of thirty-seven hard facts were Boreo's revenue, EBIT and cash
   flow. The word-boundary check accepted the ticker, because ":" is not a
   letter. The same collision `services/ticker.py` refuses to price.
2. **A 2013 docket, filed as current.** "Infringement action filed 26 Aug 2013"
   and "The case closed 7 Dec 2017" were inside a 30-day window: the reporting
   period check only knew full month names, and English dates are written both
   "Aug 2013" and "26 Aug 2013".
3. **Navigation rails and page footers, filed as facts.** A Japanese comparison
   site's card and brokerage rails ("・「年会費無料＆高還元」で選ぶ！") and
   Law360's own copyright rail ("© 2026, Portfolio Media, Inc. | About | ...").
4. **A patent database, treated as press.** Patsnap's case page contributed
   nine facts, six of them the page's own chrome.

None of these is a coverage problem: retrieval reached the company. They are
fact-quality problems, and each one is a broken claim a reader could act on.
"""
from __future__ import annotations

import pytest

from app.services.fact_extraction import (
    extract_analyst_opinions,
    extract_hard_facts,
    is_structural_noise,
)
from app.services.freshness import is_stale_period
from app.services.ingestion import title_names_company
from app.services.media_trust import is_refused_host, is_roundup_url
from app.services.publisher_filter import mentions_company, names_company

WINDOW_START = "2026-10-20"

# --- 1. an exchange-prefixed ticker is not the company's name -------------

BOREO_HEADLINE = "Boreo Oyj (FRA:YKK) Q3 2026 Earnings Call Transcript"
BOREO_BODY = (
    "Boreo Oyj (FRA:YKK) achieved 26% sales growth in Q3 2026, with 25% organic, "
    "marking the eighth consecutive quarter of organic growth."
)


def test_an_ticker_in_another_company_headline_does_not_name_us():
    """Boreo's Frankfurt symbol is YKK; the article is not about YKK."""
    assert names_company(BOREO_HEADLINE, "YKK") is False


def test_the_headline_still_names_the_company_it_is_about():
    # The exclusion is the ticker, not the name: Boreo's own brief keeps its story.
    assert names_company(BOREO_HEADLINE, "Boreo Oyj") is True


def test_the_headline_rule_and_the_result_filter_agree():
    result = {"title": BOREO_HEADLINE}

    assert title_names_company(result, "YKK") is False
    assert names_company(str(result["title"]), "YKK") is False


def test_a_headline_that_really_names_the_company_still_counts():
    assert title_names_company({"title": "YKK AP opens an Osaka showroom"}, "YKK") is True


def test_a_ticker_in_the_body_alone_does_not_rescue_the_article():
    result = {"title": "Q3 2026 Earnings Call Transcript", "raw_content": BOREO_BODY}

    assert mentions_company(result, "YKK") is False
    assert mentions_company(result, "Boreo Oyj") is True


def test_a_name_that_is_genuinely_written_still_counts():
    assert names_company("Acme Corp reports", "ACME") is True


@pytest.mark.parametrize(
    "headline",
    [
        "Boreo Oyj (FRA:YKK) Q3 2026 Earnings Call Transcript",
        "Boreo Oyj (ETR:YKK) Q3 2026 Earnings Call Transcript",
    ],
)
def test_the_ticker_pattern_covers_the_forms_that_appear(headline):
    assert names_company(headline, "YKK") is False


def test_a_headline_that_names_the_company_beside_its_own_venue_is_kept():
    # "YKK AP (TSE:4216)" uses the company's own name and a numeric Japanese
    # code, which is not the collision this rule exists for.
    assert names_company("YKK AP (TSE:4216) announces results", "YKK") is True


# --- 2. a date in either written order is a date --------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Infringement action filed **26 Aug 2013** in the United States District Court.",
        "The case closed 7 Dec 2017.",
        "The case closed 7 December 2017.",
        "Published 26 August 2013 by the court.",
        "The docket was sealed in Sept. 2015.",
        # Only written numerically, which no month-name pattern reaches.
        "(Signed by Judge Gregory H. Woods on 7/30/2020) (mro)",
        "Judgment entered 2020-07-30 by the clerk.",
        # Court PDFs stamp a two-digit year and nothing else.
        "Case 2:08-md-01912-RBS Document 136 Filed 01/24/14 Page 1 of 6",
    ],
)
def test_a_stale_date_is_caught_however_it_is_written(sentence):
    assert is_stale_period(sentence, WINDOW_START) is True


@pytest.mark.parametrize(
    "sentence",
    [
        "Operating cash flow turned negative at EUR1.1 million due to inventory build-up.",
        "YKK AP株式会社は11月2日、スペースをオープンしました。",
        "Gross margin improved to 69.9%, the lowest since April 2025.",
        # A percentage pair is not a date, and reading it as one would drop every
        # earnings sentence that compares margins.
        "Revenue rose to EUR3.5 million, up 8.5% from 5.1% a year earlier.",
        "The agreement was signed on 11/4/2026.",
    ],
)
def test_current_reporting_is_not_dropped(sentence):
    assert is_stale_period(sentence, WINDOW_START) is False


def test_a_comparison_anchor_clears_the_date_once():
    # "lowest since April 2025" states where things stand now. Both the full and
    # abbreviated patterns match "April 2025", so reading it twice turned the
    # cleared date back into a stale one.
    assert is_stale_period(
        "Gross margin improved 280 basis points to 69.9%, the lowest since April 2025.",
        WINDOW_START,
    ) is False


def test_a_stale_and_a_current_date_in_one_sentence_stays_stale():
    assert is_stale_period(
        "The dispute, filed Aug 2013, closed in Nov 2026.", WINDOW_START
    ) is True


# --- 3. navigation rails and footers are not statements -------------------

DIAMOND_RAIL = (
    "・クレジットカード比較 : ・「達人おすすめ」の最強クレジットカード！ : "
    "・「還元率の高さ」で選ぶ！ : ・「人気」で選ぶ！おすすめクレジットカード : "
    "・「年会費無料＆高還元」で選ぶ！ : ・「マイルの貯まりやすさ」で選ぶ！"
)

LAW360_FOOTER = (
    "© 2026, Portfolio Media, Inc. | About | Contact Us | Careers at Law360 | "
    "Terms | Privacy Policy | Trust Center | Cookie Settings | Ad Choices"
)

YKK_AP_REPORTING = (
    "YKK AP株式会社（本社：東京都千代田区）は11月2日、大阪市中央区にビル建築商品"
    "専門のプレゼンテーションスペース「AZ five OSAKA」をオープンしました。"
)

# A thirty-row comparison table arrives as one block, so the length test in
# `is_structural_noise` never fired and the whole page became one "fact".
BROKERAGE_TABLE = """|  |  |
| --- |
| **【2026年11月4日時点】  【証券会社おすすめ比較】** |
| 株式売買手数料（税込） | 投資信託 | 外国株 |
| 1約定ごと | 1日定額 |
| 10万円 | 20万円 | 50万円 | 50万円 |
"""

TABLE_WITH_ONE_PROSE_ROW = (
    "| Company | Revenue | Growth |\n"
    "| --- | --- | --- |\n"
    "| Zydus Lifesciences Ltd | 2,400 crore | 12% |\n"
    "| The company reported fourth-quarter revenue of Rs 2,400 crore, up 12% "
    "from a year earlier, and guided to double-digit growth on new launches. |\n"
    "| Peer A | 900 crore | 4% |\n"
)


@pytest.mark.parametrize("text", [DIAMOND_RAIL, LAW360_FOOTER, BROKERAGE_TABLE])
def test_a_rail_or_footer_or_table_is_structural_noise(text):
    assert is_structural_noise(text) is True


VENDOR_SIGNUP_COPY = (
    "By subscribing, you agree to receive occasional emails and marketing "
    "communications from us.",
    "We respect your privacy and you can unsubscribe at any time.",
)

SUPPLIER_REPORTING = (
    "That's why we chose YKK, the global leader in fastening technology, as our "
    "go-to manufacturer of zippers, snaps, and closures.",
    "For over 80 years, they have been delivering products made to ensure "
    "flawless performance under the harshest conditions.",
)


@pytest.mark.parametrize("text", VENDOR_SIGNUP_COPY)
def test_a_vendors_signup_copy_is_not_evidence(text):
    # Measured on YKK: two of eleven facts from a motorcycle-apparel page were
    # its newsletter consent and its unsubscribe note.
    assert is_structural_noise(text) is True


@pytest.mark.parametrize("text", SUPPLIER_REPORTING)
def test_the_supplier_reporting_beside_it_survives(text):
    assert is_structural_noise(text) is False


# --- 6. a publisher's own hiring advert is not reporting ------------------

JIJI_INTERNSHIP_AD = (
    "2027年卒の学生の皆様を対象にビジネス職の1Dayインターンシップを開催します。"
    "現役社員と一緒に報道機関のビジネスを疑似体験しませんか？ 東京と大阪で開催します。"
)


def test_a_wires_internship_advert_is_not_a_fact():
    # It sits in a press-release feed and reads like prose, so a measured YKK run
    # filed it as a hard fact about the company the brief was about.
    assert is_structural_noise(JIJI_INTERNSHIP_AD) is True


def test_the_advert_is_not_a_statement_when_extracted():
    facts = extract_hard_facts("\n\n".join((YKK_AP_REPORTING, JIJI_INTERNSHIP_AD)))

    assert any("AZ five OSAKA" in fact for fact in facts)
    assert not any("インターンシップ" in fact for fact in facts)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.jiji.co.jp/recruit/internship",
        "https://example.com/internship/summer-2026",
    ],
)
def test_a_recruitment_page_is_not_read_as_reporting(url):
    from app.services.publisher_filter import is_recruitment_page

    assert is_recruitment_page(url) is True


@pytest.mark.parametrize(
    "url",
    [
        # A publisher's article about hiring is still reporting.
        "https://www.jiji.com/jc/article?k=000000418.000004185&g=prt",
        # An employer's own board is the hiring lane's source, not a refusal —
        # `/careers` and `/jobs` belong to `is_careers_url` and are read as
        # postings instead.
        "https://canadagoose.wd3.myworkdayjobs.com/en-US/CanadaGoose",
        "https://example.com/careers/engineering",
        "https://example.com/jobs/1234",
    ],
)
def test_real_reporting_and_careers_boards_are_not_recruitment_pages(url):
    from app.services.publisher_filter import is_recruitment_page

    assert is_recruitment_page(url) is False


# --- 7. a filing states its own date ------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Case 2:08-md-01912-RBS Document 136 Filed 01/24/14 Page 1 of 6", "2014-01-24"),
        ("Document 136 Filed 01/24/2014 Page 1 of 6", "2014-01-24"),
        ("(Signed by Judge Gregory H. Woods on 7/30/2020) (mro)", "2020-07-30"),
        ("Dated: 2020-07-30", "2020-07-30"),
    ],
)
def test_a_filing_date_is_recovered_from_the_document(text, expected):
    from app.services.freshness import date_from_body

    assert date_from_body(text).date().isoformat() == expected


def test_a_document_with_no_date_stays_undated():
    from app.services.freshness import date_from_body

    assert date_from_body("No date appears anywhere in this document at all.") is None


@pytest.mark.parametrize(
    "text",
    [
        YKK_AP_REPORTING,
        # Real Japanese reporting uses "・" for lists, so presence alone is not
        # the test; a long statement that happens to contain one is still a fact.
        "本施設は、主にオフィスビルやマンションなどのビル建築向け商品を展示し、"
        "窓や玄関ドアなどの開口部技術・システムを体感・検証・提案するための"
        "プロユーザー向けプレゼンテーションスペースです。",
        "Boreo achieved 26% sales growth in Q3 2026, with 25% organic, marking the "
        "eighth consecutive quarter of organic growth.",
    ],
)
def test_a_statement_containing_a_separator_survives(text):
    assert is_structural_noise(text) is False


def test_rails_and_footers_never_reach_the_fact_list():
    document = "\n\n".join((YKK_AP_REPORTING, DIAMOND_RAIL, LAW360_FOOTER))

    facts = extract_hard_facts(document)

    assert any("AZ five OSAKA" in fact for fact in facts)
    assert not any("クレジットカード" in fact for fact in facts)
    assert not any("Portfolio Media" in fact for fact in facts)


def test_rails_do_not_reach_the_opinion_lane_either():
    document = "\n\n".join((YKK_AP_REPORTING, DIAMOND_RAIL, LAW360_FOOTER, YKK_AP_REPORTING))

    opinions = extract_analyst_opinions(document, [])

    assert not any("クレジットカード" in opinion for opinion in opinions)
    assert not any("Portfolio Media" in opinion for opinion in opinions)


def test_a_long_table_block_never_becomes_one_fact():
    facts = extract_hard_facts(BROKERAGE_TABLE)

    assert facts == []


def test_a_sentence_inside_a_table_block_still_survives():
    # The point of the line-ratio test: it must not delete the reporting that
    # sits in a table's one prose cell.
    assert is_structural_noise(TABLE_WITH_ONE_PROSE_ROW) is False

    facts = extract_hard_facts(TABLE_WITH_ONE_PROSE_ROW)

    assert any("2,400 crore" in fact for fact in facts), facts


# --- 5. a court filing's caption is not a statement -----------------------

DOCKET_FRAGMENTS = (
    "Defendants.",
    "No.",
    "564.",
    "YKK ZIPPER INDONESIA, YKK BANGLADESH PTE. LTD., YKK KOREA CO., LTD., "
    "YKK FRANCE SARL, DALIAN YKK ZIPPER CO., LTD.",
)

DOCKET_JUDGMENT = (
    "For the reasons discussed above, YKK's motion for summary judgment is "
    "GRANTED as to Plaintiffs' CUTPA and breach of contract claims."
)


@pytest.mark.parametrize("text", DOCKET_FRAGMENTS)
def test_a_docket_caption_is_not_a_fact(text):
    from app.services.fact_extraction import _is_document_fragment

    assert _is_document_fragment(text) is True


def test_the_judgment_itself_survives():
    from app.services.fact_extraction import _is_document_fragment

    assert _is_document_fragment(DOCKET_JUDGMENT) is False


def test_a_docket_block_yields_the_judgment_and_not_the_caption():
    facts = extract_hard_facts("\n\n".join(DOCKET_FRAGMENTS + (DOCKET_JUDGMENT,)))

    assert any("GRANTED" in fact for fact in facts), facts
    assert not any(fact.strip() in DOCKET_FRAGMENTS for fact in facts), facts


# --- 4. a patent database is not press ------------------------------------


def test_a_patent_database_is_refused_as_a_source():
    assert is_refused_host("https://www.patsnap.com/litigation/case/123") is True


def test_a_patent_database_url_is_not_a_roundup_either():
    # Two different reasons to drop a page; neither should mask the other.
    assert is_roundup_url("https://www.patsnap.com/litigation/case/123") is False
