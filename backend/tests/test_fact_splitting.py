"""One stored fact should be one statement, not a paragraph of them.

Measured on a live brief: 15 of 78 stored facts bundled three or more distinct
statements. That made precise citation impossible (a claim built from the third
sentence of a bundle had no single item to point at) and hid figures from the
auditor (a number at character 500 sat inside one "fact" nobody could slice).
"""
from __future__ import annotations

from app.services.fact_extraction import extract_hard_facts

RESULTS_PARAGRAPH = (
    "Canada Goose reported second-quarter fiscal 2027 results: revenue rose 5.9% "
    "to C$274.0 million from C$258.8 million a year earlier. Gross profit rose to "
    "C$182.9 million. The net loss widened to C$26.3 million from C$17.4 million, "
    "including a C$7.8 million discontinued-operations loss."
)

SECOND_PARAGRAPH = (
    "Management said Q2 gross margin expanded 280 basis points and was much more "
    "structural, driven by product mix, pricing and sourcing."
)

THIRD_PARAGRAPH = (
    "The company also completed the sale of its Baffin Limited business on "
    "August 10, 2026, to concentrate on its core outerwear business."
)


def test_each_statement_becomes_its_own_fact():
    facts = extract_hard_facts(
        "\n\n".join((RESULTS_PARAGRAPH, SECOND_PARAGRAPH, THIRD_PARAGRAPH))
    )

    assert len(facts) >= 4, facts
    joined = " ".join(facts)
    for figure in ("C$274.0", "C$182.9", "C$26.3", "280 basis points", "Baffin"):
        assert figure in joined, figure


def test_a_currency_figure_does_not_split_its_own_sentence():
    # "C$274.0 million from C$258.8 million" must stay in one sentence: splitting
    # on the "$" produced fragments that were not statements.
    facts = extract_hard_facts(RESULTS_PARAGRAPH)

    revenue_facts = [fact for fact in facts if "C$274.0" in fact]
    assert len(revenue_facts) == 1
    assert "C$258.8" in revenue_facts[0]


def test_a_short_complete_sentence_survives():
    # "Gross profit rose to C$182.9 million." is 37 characters and a fact.
    # Joining it upward buried a figure inside another sentence.
    facts = extract_hard_facts(RESULTS_PARAGRAPH)

    assert any("C$182.9" in fact and fact.count("C$") == 1 for fact in facts), facts


def test_a_single_sentence_article_still_yields_one_fact():
    facts = extract_hard_facts(SECOND_PARAGRAPH)

    assert len(facts) == 1
    assert facts[0].startswith("Management said")


def test_a_table_is_kept_whole_rather_than_shredded():
    # Structure with no sentence boundary has no information boundary either.
    table = "| Symbol | Name | Last |\n| --- | --- | --- |\n| GOOS-T | Canada Goose | 12.89 |"

    facts = extract_hard_facts(table)

    assert facts == [], "a table row is not prose and must not become a fact"
