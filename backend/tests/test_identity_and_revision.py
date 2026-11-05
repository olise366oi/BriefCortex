"""Tests for the four behaviours added after the Instinct trial run.

Each case comes from what the live run actually did wrong:
- a caller's "none of these" verdict still produced an Indian entity
- a caller-supplied jurisdiction was ignored and an unrelated country used
- two businesses sharing one name were merged into one fact base
- a later brief had no way to say where it differed from the earlier one
"""
from __future__ import annotations

from app.models.briefing import BriefingReport
from app.services.analysis import _build_user_message, _format_previous_reading
from app.services.company_identity import jurisdiction_from_description
from app.services.article_subject import SUBJECT_PROMPT


def test_a_description_naming_a_city_yields_the_country():
    assert jurisdiction_from_description("San Francisco AI assistant startup") == "US"
    assert jurisdiction_from_description("headquartered in London") == "GB"


def test_a_description_naming_nothing_yields_no_country():
    # Guessing from the industry would aim legal searches at the wrong country.
    assert jurisdiction_from_description("AI personal assistant startup") is None
    assert jurisdiction_from_description("") is None


def test_country_hints_do_not_fire_on_substrings():
    # "china" inside "chinatown" is a place in the US, not a jurisdiction claim,
    # so the longest phrase must be matched first.
    assert jurisdiction_from_description("San Francisco, near Chinatown") == "US"


PREVIOUS_SUMMARY = "The company is buying distribution ahead of controls."
PREVIOUS_RECOMMENDATION = "Treat the agent business as a financing story."


def _previous_report() -> BriefingReport:
    return BriefingReport.model_validate(
        {
            "executive_summary": {
                "summary": PREVIOUS_SUMMARY,
                "recommendation": PREVIOUS_RECOMMENDATION,
                "confidence": "medium",
            },
            "news": {
                "items": [
                    {"claim": "Raised $1B at a $10B valuation.", "source_url": "https://x/1"}
                ]
            },
        }
    )


def test_a_first_brief_carries_no_previous_reading():
    assert _format_previous_reading(None) == ""


def test_the_previous_reading_is_labelled_as_non_evidence():
    block = _format_previous_reading(_previous_report())

    assert block.startswith("<PREVIOUS_FINDINGS>")
    assert "</PREVIOUS_FINDINGS>" in block
    # The label is the mechanism that stops the old conclusion being cited.
    assert "not " in block and "evidence" in block


def test_the_previous_findings_are_carried_so_they_are_not_lost():
    # The user's requirement: the second brief must include what the first run
    # found, not replace it.
    block = _format_previous_reading(_previous_report())

    assert "Raised $1B at a $10B valuation." in block


def test_the_previous_judgement_is_withheld_into_its_own_block():
    # Analysis and recommendation must not sit in the reading block, or the new
    # assessment is anchored to the old one.
    block = _format_previous_reading(_previous_report())

    assert "<PREVIOUS_JUDGEMENT_NOT_FOR_THIS_BRIEF>" in block
    assert "Do NOT reuse this judgement" in block
    findings, _, judgement = block.partition("<PREVIOUS_JUDGEMENT_NOT_FOR_THIS_BRIEF>")
    assert PREVIOUS_SUMMARY not in findings
    assert PREVIOUS_RECOMMENDATION not in findings


def test_revision_mode_is_requested_when_a_previous_brief_exists():
    from app.models.fact_base import FactBase

    fact_base = FactBase(
        company_id="x",
        last_refresh_date=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        hard_facts=[],
        analyst_opinions=[],
    )

    with_previous = _build_user_message(fact_base, "", _previous_report())
    without = _build_user_message(fact_base, "", None)

    assert "REVISION" in with_previous
    assert "changes_since_previous" in with_previous
    assert "REVISION" not in without
    assert "<PREVIOUS_READING>" not in without


def test_the_subject_prompt_handles_shared_names():
    # Measured: Instinct Science facts were filed as the AI startup's, because
    # the classifier was told only the name.
    assert "share" in SUBJECT_PROMPT
    assert "unrelated" in SUBJECT_PROMPT


def test_the_subject_prompt_rejects_a_counterparty_story():
    # Measured: "Zocdoc announced a partnership with Instinct" was filed as
    # Instinct's own news, which made the whole brief about Zocdoc.
    assert "counterparty" in SUBJECT_PROMPT
    assert "whose action" in SUBJECT_PROMPT
