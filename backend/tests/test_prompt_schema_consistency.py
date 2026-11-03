import re
from pathlib import Path

from app.services.analysis import BriefingReport, SYSTEM_PROMPT

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_SOURCE = PROJECT_ROOT / "app" / "services" / "analysis.py"

SECTIONS = ["news", "hires", "launches", "open_questions", "risks"]
SUMMARY_SECTION = "executive_summary"


def _prompt_schema_block() -> str:
    return SYSTEM_PROMPT[SYSTEM_PROMPT.index("Schema:") + len("Schema:"):].strip()


def test_top_level_keys_match():
    real_top = list(BriefingReport.model_json_schema()["properties"].keys())
    expected = SECTIONS + [SUMMARY_SECTION]
    assert real_top == expected, real_top


def test_section_shape_match():
    real = BriefingReport.model_json_schema()
    schema_block = _prompt_schema_block()
    for name in SECTIONS:
        ref = real["properties"][name]["$ref"].split("/")[-1]
        definition = real["$defs"][ref]
        assert list(definition["properties"].keys()) == ["items"], name
        assert definition["properties"]["items"]["type"] == "array", name
        assert re.search(r'"%s":\s*\{"items":\s*\[' % name, schema_block), name


def test_claim_fields_match():
    real = BriefingReport.model_json_schema()
    claim_props = list(real["$defs"]["Claim"]["properties"].keys())
    assert claim_props == ["claim", "source_url"], claim_props
    assert re.search(r'\{"claim": str, "source_url": str\}', _prompt_schema_block())


def test_executive_summary_fields_match():
    real = BriefingReport.model_json_schema()
    summary_props = list(real["$defs"]["ExecutiveSummary"]["properties"].keys())
    assert summary_props == ["summary", "recommendation", "confidence"], summary_props
    assert re.search(
        r'"executive_summary":\s*\{"summary": str, "recommendation": str, "confidence": str\}',
        _prompt_schema_block(),
    )


def test_no_ghost_fields():
    source = ANALYSIS_SOURCE.read_text(encoding="utf-8")
    assert '"verified"' not in source
    assert re.search(r"negotiation_levers|target_concessions", source) is None


def test_no_legacy_input_tags():
    assert re.search(r"<NEWS>|<HIRES>|<LAUNCHES>|<OPEN_QUESTIONS>|<RISKS>", SYSTEM_PROMPT) is None
