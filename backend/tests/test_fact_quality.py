"""What a paragraph has to be before it can become a fact.

Measured on a live run: a markdown table row from a geographic revenue table,
three vendor lines from a company-profile page ("Gain a 360-degree view … Learn
more"), and the non-IFRS boilerplate note all reached the fact base and were read
as evidence. Coverage reported "strong" while a third of the facts were page
furniture.
"""
from __future__ import annotations

import pytest

from app.services.fact_extraction import (
    extract_analyst_opinions,
    extract_hard_facts,
    is_structural_noise,
)

FURNITURE = (
    # A table row from an XBRL financial statement.
    "| Region | Fiscal 2024 | Fiscal 2023 | Fiscal 2022 |",
    "| --- | --- | --- |",
    "| North America | 70% DTC, 20% Wholesale, 10% Other |",
    # Vendor copy from a company-profile page.
    "Gain a 360-degree view of Canada Goose Holdings Inc and make more "
    "informed decisions for your business",
    "Gain access to our premium signals and make informed decisions for your "
    "business Learn more",
    "Sign up for first access to new arrivals, exclusive offers, and in-person events.",
    # Filing furniture: a statement about the document.
    "3 Constant currency revenue is a non-IFRS financial measure.",
    "| Exhibit No. | Description | | --- | --- | | 99.1 | Press release of "
    "Canada Goose Holdings Inc., dated September 10, 2026.",
)

REPORTING = (
    "Canada Goose reported a net loss of C$26.3 million for the quarter ended "
    "September 27, 2026, versus a loss of C$17.4 million a year earlier.",
    "The company announced a reduction of approximately 17% of its global "
    "corporate roles under its Transformation Program.",
    "Gross margin improved 280 basis points to 69.9% in the second quarter of "
    "fiscal 2027, driven by lower product costs and a favourable channel mix.",
    "Revenue grew 1.1% to C$1,348.4 million in the fiscal year ended March 30, 2025.",
)


@pytest.mark.parametrize("text", FURNITURE)
def test_page_furniture_is_not_a_paragraph(text):
    assert is_structural_noise(text) is True


@pytest.mark.parametrize("text", REPORTING)
def test_real_reporting_survives(text):
    assert is_structural_noise(text) is False


def test_furniture_never_reaches_the_fact_list():
    # The whole point of the gate: it runs before extraction, so a document made
    # only of furniture produces no facts at all rather than three bad ones.
    document = "\n\n".join(FURNITURE)

    assert extract_hard_facts(document) == []


def test_reporting_beside_furniture_keeps_only_the_reporting():
    document = "\n\n".join(
        (
            FURNITURE[0],
            REPORTING[0],
            FURNITURE[3],
            REPORTING[1],
        )
    )

    facts = extract_hard_facts(document)

    assert len(facts) == 2
    assert all("Gain a 360" not in fact for fact in facts)
    assert all("| Region |" not in fact for fact in facts)


def test_opinions_do_not_carry_table_rows():
    # A long table row survives paragraph splitting because it is long enough to
    # look like prose; the sentence extracted from it is still a row, and it
    # reached the opinion lane on a live run.
    document = "\n\n".join(
        (
            REPORTING[0],
            REPORTING[1],
            REPORTING[2],
            "| CAD $ millions | Year ended March 30, 2025 | Year ended March 31, "
            "2024 | $ Change | % Change | March 30, 2025 versus March 31, 2024 | "
            "1,348.4 | 1,333.8 | 14.6 | 1.1% |",
            REPORTING[3],
        )
    )

    opinions = extract_analyst_opinions(document, [])

    assert all("|" not in opinion for opinion in opinions), opinions
