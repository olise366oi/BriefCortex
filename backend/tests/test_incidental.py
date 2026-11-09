"""Tests for incidental facts: extraction, isolation, and classification plumbing."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.models.fact_base import FactBase, FactItem, IncidentalFact
from app.services.report_stages import render_findings_input
from app.services.incidental import (
    article_subject_mentions,
    company_name_hits,
    extract_incidental_sentences,
    opening_sentence,
)

COMPETITOR_ARTICLE = """
Intesa Sanpaolo said on Tuesday it expects a decision from the EU antitrust
regulator on its bid for Monte dei Paschi, according to a filing.

The Italian lender is also weighing a move that could force UniCredit to
respond, two people familiar with the matter said, speaking on condition of
anonymity because the deliberations are private.

UniCredit declined to comment on the speculation.
"""

OWN_ARTICLE = """
UniCredit reported third quarter net income of 2.6 billion euros, up from
2.1 billion a year earlier, according to the company statement.
"""


def test_hits_are_counted_at_word_companys():
    assert company_name_hits("UniCredit and UniCredit", "UniCredit") == 2
    # A longer word that merely contains the name is not a hit.
    assert company_name_hits("unicredited", "UniCredit") == 0


def test_only_sentences_about_our_company_are_taken():
    found = extract_incidental_sentences(COMPETITOR_ARTICLE, "UniCredit")

    texts = [item.content for item in found]
    assert len(texts) == 2
    assert all("UniCredit" in text for text in texts)
    # The opening sentence about Intesa is not ours to keep.
    assert not any("Monte dei Paschi" in text for text in texts)


def test_subject_position_is_distinguished_from_passing_mention():
    found = extract_incidental_sentences(COMPETITOR_ARTICLE, "UniCredit")
    by_text = {item.content: item.is_subject for item in found}

    passing = next(t for t in by_text if "force UniCredit to respond" in t)
    leading = next(t for t in by_text if t.startswith("UniCredit"))

    assert by_text[passing] is False
    assert by_text[leading] is True


def test_known_sentences_are_not_mined_twice():
    already = [item.content for item in extract_incidental_sentences(COMPETITOR_ARTICLE, "UniCredit")]
    found = extract_incidental_sentences(COMPETITOR_ARTICLE, "UniCredit", exclude=already)

    assert found == []


def test_a_cap_limits_how_many_sentences_come_back():
    body = "\n\n".join(f"UniCredit said thing number {i}." for i in range(20))
    found = extract_incidental_sentences(body, "UniCredit", max_sentences=3)

    assert len(found) == 3


def test_empty_inputs_are_safe():
    assert extract_incidental_sentences("", "UniCredit") == []
    assert extract_incidental_sentences("Some text.", "") == []
    assert opening_sentence("") == ""


def test_article_subject_mentions_counts_the_whole_article():
    assert article_subject_mentions(COMPETITOR_ARTICLE, "UniCredit") >= 2
    assert article_subject_mentions(OWN_ARTICLE, "UniCredit") >= 1


def test_opening_sentence_is_the_first_sentence_of_the_first_paragraph():
    assert opening_sentence(COMPETITOR_ARTICLE).startswith("Intesa Sanpaolo")
    assert opening_sentence(OWN_ARTICLE).startswith("UniCredit reported")


def _fact_base(**overrides) -> FactBase:
    defaults = dict(
        company_id="unicredit-abc",
        last_refresh_date=datetime.now(timezone.utc),
        hard_facts=[
            FactItem(content="Net income was 2.6bn euros.", source_url="https://x.com/a")
        ],
        analyst_opinions=[],
        conflicts=[],
        incidental_facts=[],
    )
    defaults.update(overrides)
    return FactBase(**defaults)


def test_incidental_facts_never_reach_the_prompt():
    incidental = [
        IncidentalFact(
            content="A move could force UniCredit to respond.",
            source_url="https://www.reuters.com/business/intesa",
            provenance="unverified",
        )
    ]

    prompt = render_findings_input(
        _fact_base(incidental_facts=incidental), "", "UniCredit"
    )

    # The sentence is not in any block the model is told to reason over.
    assert "force UniCredit to respond" not in prompt
    assert "<HARD_FACTS>" in prompt
    assert "<HIRING>" in prompt


def test_incidental_facts_never_reach_the_findings_input():
    # A fact about us found inside an article about another company is a lead,
    # not a finding: it travels beside the brief, never inside the evidence the
    # model reasons over.
    incidental = [
        IncidentalFact(
            content="A move could force UniCredit to respond.",
            source_url="https://www.reuters.com/business/intesa",
        )
    ]

    message = render_findings_input(_fact_base(incidental_facts=incidental), "", "UniCredit")

    assert "force UniCredit to respond" not in message


def test_incidental_facts_survive_in_the_fact_base_for_the_api():
    incidental = [
        IncidentalFact(
            content="A move could force UniCredit to respond.",
            source_url="https://www.reuters.com/business/intesa",
            provenance="corroborated",
            corroborating_url="https://www.bloomberg.com/news/articles/unicredit",
        )
    ]

    base = _fact_base(incidental_facts=incidental)

    assert len(base.incidental_facts) == 1
    assert base.incidental_facts[0].provenance == "corroborated"
