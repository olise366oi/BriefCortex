"""Staged generation, own-profile isolation, and long-document selection.

The properties under test are the reason the pipeline is split:

* the analysis cannot read evidence the audit removed;
* the recommendation cannot read raw evidence, only the assessment;
* the caller's own profile reaches the recommendation and nothing earlier;
* a 300k filing does not go into the fact base whole.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.fact_base import FactBase, FactItem
from app.services.evidence_fingerprint import evidence_fingerprint
from app.services.long_document import (
    MAX_SELECTED_CHARS,
    is_long_document,
    segment,
    select_relevant,
)
from app.services.own_profile import (
    OwnCompanyProfile,
    ProfileStore,
    resolve_profile,
)
from app.services.report_stages import (
    FINDINGS_PROMPT,
    ANALYSIS_PROMPT,
    RECOMMENDATION_PROMPT,
    AnalysisStage,
    FindingsStage,
    RecommendationStage,
    render_analysis_input,
    render_findings_input,
    render_recommendation_input,
)


def _fact_base(facts: list[str]) -> FactBase:
    return FactBase(
        company_id="c",
        last_refresh_date=datetime.now(timezone.utc),
        hard_facts=[
            FactItem(content=text, source_url=f"https://x/{i}")
            for i, text in enumerate(facts)
        ],
        analyst_opinions=[],
    )


# --- stage schemas -------------------------------------------------------

def test_each_stage_has_its_own_shape():
    # The findings stage also carries changes_since_previous, because a revision
    # has to be able to state where its reading differs from the earlier one.
    assert list(FindingsStage.model_fields.keys()) == [
        "news",
        "hires",
        "changes_since_previous",
    ]
    assert list(AnalysisStage.model_fields.keys()) == ["analysis"]
    assert list(RecommendationStage.model_fields.keys()) == ["executive_summary"]


def test_the_prompts_do_not_overlap_in_purpose():
    assert "not" in FINDINGS_PROMPT and "judgement" in FINDINGS_PROMPT
    assert "Do not give advice" in ANALYSIS_PROMPT
    assert "has already been made" in RECOMMENDATION_PROMPT


# --- what each stage may read -------------------------------------------

def test_the_analysis_input_carries_only_surviving_findings():
    findings = render_analysis_input(
        news=[], hires=[], quote=None, company="Acme",
        coverage_level="thin", lanes_missing=["hiring"],
    )

    assert "<VERIFIED_NEWS>" in findings
    assert "thin" in findings
    # No raw article text, no evidence block that the audit has not cleared.
    assert "<HARD_FACTS>" not in findings


def test_the_analysis_input_includes_the_quote_when_present():
    findings = render_analysis_input(
        news=[], hires=[], quote="NVDA 181.60 USD", company="Acme",
        coverage_level="strong", lanes_missing=[],
    )

    assert "NVDA 181.60 USD" in findings
    assert "<MARKET>" in findings


def test_the_recommendation_input_carries_the_assessment():
    from app.models.briefing import Analysis

    text = render_recommendation_input(
        Analysis(assessment="Margin is expanding.", risks=["Customer concentration"]),
        "negotiate pricing", "Acme", "", "strong",
    )

    assert "Margin is expanding." in text
    assert "Customer concentration" in text
    assert "<OUR_COMPANY_PROFILE>" not in text


def test_the_own_profile_reaches_the_recommendation_only():
    from app.models.briefing import Analysis

    with_profile = render_recommendation_input(
        Analysis(assessment="a", risks=[]),
        "", "Acme", "WE ARE A SMALL SUPPLIER WITH NO ALTERNATIVE", "strong",
    )
    assert "<OUR_COMPANY_PROFILE>" in with_profile

    # The findings stage is where the evidence is read, so the profile must not
    # appear there: what we are cannot shape what the evidence says.
    findings = render_findings_input(_fact_base(["Revenue rose."]), "", "Acme")
    assert "OUR COMPANY" not in findings


# --- profile resolution --------------------------------------------------

def test_a_named_profile_is_resolved():
    store = ProfileStore()
    store.add(OwnCompanyProfile(name="Acme", description="a mid-size supplier"))

    assert resolve_profile(store, "Acme").description == "a mid-size supplier"


def test_an_inline_profile_is_accepted_without_saving():
    store = ProfileStore()

    profile = resolve_profile(store, "", "one-off: we have no alternative supplier")

    assert profile is not None
    assert "no alternative" in profile.description


def test_no_profile_is_a_normal_case():
    assert resolve_profile(ProfileStore(), "", "") is None


def test_an_unknown_name_falls_through_to_inline():
    store = ProfileStore()

    profile = resolve_profile(store, "Missing", "inline text")

    assert profile is not None
    assert profile.description == "inline text"


# --- the profile changes the cache identity -----------------------------

def test_a_profile_changes_the_evidence_fingerprint():
    base = _fact_base(["Revenue rose."])

    without = evidence_fingerprint(base, "", "")
    with_profile = evidence_fingerprint(base, "", "", own_profile="we are a supplier")

    assert without != with_profile


# --- long document -------------------------------------------------------

FILING = (
    "Forward-Looking Statements\n\n"
    + "This report contains forward-looking statements. " * 40
    + "\n\nResults of Operations\n\n"
    + "Revenue for the quarter was 57.0 billion dollars, up 62 percent. " * 20
    + "\n\nExhibit Index\n\n"
    + "Exhibit 31.1 Certification pursuant to Section 302. " * 40
)


def test_a_filing_is_recognised_as_long():
    assert is_long_document(FILING * 10)


def test_a_short_article_is_not_filtered():
    assert is_long_document("A short news paragraph.") is False


def test_selection_keeps_the_substantive_section():
    selected = select_relevant(FILING * 10, "Acme", "revenue and data centre")

    assert "57.0 billion" in selected
    assert len(selected) <= MAX_SELECTED_CHARS


def test_selection_drops_boilerplate():
    selected = select_relevant(FILING * 10, "Acme", "revenue")

    assert "forward-looking statements" not in selected.lower()
    assert "exhibit index" not in selected.lower()


def test_segmentation_keeps_headings_attached_to_their_paragraph():
    blocks = segment(FILING)

    assert blocks
    assert any("Results of Operations" in block.heading for block in blocks)
