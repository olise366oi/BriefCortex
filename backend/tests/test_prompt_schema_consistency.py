"""The prompts the pipeline actually sends must match the schema it validates.

This file used to read a single mega-prompt that no caller ever sent: the live
pipeline generates the report in three stages. The contract it enforces is still
the right one — a prompt asking for a field the model does not have, or a model
field the prompt must never request, is a defect — so the assertions now read the
three stage prompts and the Pydantic model together.
"""
import re
from pathlib import Path

from app.models.briefing import BriefingReport
from app.services.report_stages import (
    ANALYSIS_PROMPT,
    FINDINGS_PROMPT,
    RECOMMENDATION_PROMPT,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_SOURCE = PROJECT_ROOT / "app" / "services" / "analysis.py"

SECTIONS = ["news", "hires"]
SUMMARY_SECTION = "executive_summary"
ANALYSIS_SECTION = "analysis"
# Present only when a later brief has to state where it differs from an earlier
# one, so it is part of the schema but not part of the first brief's shape.
REVISION_SECTION = "changes_since_previous"
# Filled by the audit after generation, never by the model, so it is deliberately
# absent from the prompt schema: a model that could write this field could claim
# it had been audited.
AUDIT_SECTION = "removed_claims"
# Filled from the fact base's incidental lane: facts about us found inside articles
# about other companies. Also absent from the prompt, so the model never presents
# an unconfirmed sentence as a finding.
POSSIBLE_SECTION = "possible_facts"
# Stated intent about the future, and a market quote. Both are populated from the
# fact base and deliberately withheld from the model's prompt: a promise is not
# evidence that something happened.
FORWARD_SECTION = "forward_looking"
QUOTE_SECTION = "quote"

# The three prompts, joined: every field a prompt may request must appear in the
# stage that writes it, and the union is what the BriefingReport is assembled from.
ALL_PROMPTS = FINDINGS_PROMPT + "\n" + ANALYSIS_PROMPT + "\n" + RECOMMENDATION_PROMPT


def test_top_level_keys_match():
    real_top = list(BriefingReport.model_json_schema()["properties"].keys())
    expected = [
        "news",
        "hires",
        ANALYSIS_SECTION,
        SUMMARY_SECTION,
        REVISION_SECTION,
        AUDIT_SECTION,
        POSSIBLE_SECTION,
        FORWARD_SECTION,
        QUOTE_SECTION,
    ]
    assert real_top == expected, real_top


def test_forward_looking_and_quote_are_never_requested_from_the_model():
    # They may be *mentioned* (the analysis prompt is told a quote may be
    # supplied), but they must never appear as a field the model has to emit.
    schema_parts = [
        prompt[prompt.index("Reply with JSON only:"):]
        for prompt in (FINDINGS_PROMPT, ANALYSIS_PROMPT, RECOMMENDATION_PROMPT)
    ]
    for part in schema_parts:
        assert f'"{FORWARD_SECTION}"' not in part
        assert f'"{QUOTE_SECTION}"' not in part


def test_analysis_is_documented_in_its_own_stage():
    assert re.search(
        r'"analysis":\s*\{"assessment":\s*str,\s*"risks":\s*\[str\]\}',
        ANALYSIS_PROMPT,
    ), "analysis missing from the analysis-stage prompt"


def test_the_analysis_field_is_not_a_second_recommendation():
    # The reader must get the assessment before the advice, which only holds if
    # analysis is a different field from executive_summary.
    assert ANALYSIS_SECTION != SUMMARY_SECTION
    real = BriefingReport.model_json_schema()
    ref = real["properties"][ANALYSIS_SECTION]["$ref"].split("/")[-1]
    assert set(real["$defs"][ref]["properties"].keys()) == {"assessment", "risks"}


def test_the_audit_field_is_never_requested_from_the_model():
    # removed_claims is produced by report_audit, not by the generator. Asking the
    # model for it would let a reply declare itself clean.
    assert AUDIT_SECTION not in ALL_PROMPTS


def test_possible_facts_are_never_requested_from_the_model():
    # These come from the incidental lane, which the prompt never sees at all.
    assert POSSIBLE_SECTION not in ALL_PROMPTS


def test_revision_field_is_requested_only_on_a_revision():
    # The findings stage owns this field, because a revision extends findings.
    # It must be documented there and must be told to be omitted on a first brief.
    findings_schema = FINDINGS_PROMPT[FINDINGS_PROMPT.index("Reply with JSON only:"):]
    assert REVISION_SECTION in findings_schema
    assert "FIRST BRIEF" in FINDINGS_PROMPT
    assert "must be omitted entirely" in FINDINGS_PROMPT


def test_section_shape_match():
    real = BriefingReport.model_json_schema()
    findings_schema = FINDINGS_PROMPT[FINDINGS_PROMPT.index("Reply with JSON only:"):]
    for name in SECTIONS:
        ref = real["properties"][name]["$ref"].split("/")[-1]
        definition = real["$defs"][ref]
        assert list(definition["properties"].keys()) == ["items"], name
        assert definition["properties"]["items"]["type"] == "array", name
        assert re.search(r'"%s":\s*\{"items":\s*\[' % name, findings_schema), name


def test_claim_fields_match():
    real = BriefingReport.model_json_schema()
    claim_props = list(real["$defs"]["Claim"]["properties"].keys())
    # date, first_seen and key_event exist so news is delivered as a timeline
    # whose events can span several reports.
    assert claim_props == [
        "claim", "source_url", "date", "first_seen", "key_event"
    ], claim_props


def test_the_findings_prompt_requests_every_claim_field():
    block = FINDINGS_PROMPT[FINDINGS_PROMPT.index("Reply with JSON only:"):]
    for field in ("claim", "source_url", "date", "first_seen", "key_event"):
        assert field in block, field


def test_the_three_stages_are_separate_prompts():
    # Splitting generation is what lets the audit run between findings and
    # assessment; a single prompt would put the finding and the judgement in the
    # same reply again.
    assert FINDINGS_PROMPT != ANALYSIS_PROMPT != RECOMMENDATION_PROMPT
    assert "assessment" in ANALYSIS_PROMPT
    assert "recommendation" in RECOMMENDATION_PROMPT


def test_executive_summary_fields_match():
    real = BriefingReport.model_json_schema()
    summary_props = list(real["$defs"]["ExecutiveSummary"]["properties"].keys())
    assert summary_props == ["summary", "recommendation", "confidence"], summary_props
    schema_part = RECOMMENDATION_PROMPT[RECOMMENDATION_PROMPT.index("Reply with JSON only:"):]
    for field in ("summary", "recommendation", "confidence"):
        assert f'"{field}": str' in schema_part, field


def test_no_ghost_fields():
    source = ANALYSIS_SOURCE.read_text(encoding="utf-8")
    assert '"verified"' not in source
    assert re.search(r"negotiation_levers|target_concessions", source) is None


def test_no_legacy_input_tags():
    # The blocks the model is told to read are the ones the renderers emit.
    legacy = r"<NEWS>|<HIRES>|<LAUNCHES>|<OPEN_QUESTIONS>|<RISKS>"
    assert re.search(legacy, ALL_PROMPTS) is None
