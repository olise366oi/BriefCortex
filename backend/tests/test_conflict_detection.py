import pytest

from app.services.conflict_detection import CONFLICT_RULES, detect_fact_conflicts
from app.services.ingestion import FactBase, FactItem

SOURCE_A = "https://www.reuters.com/business/nio-cash-flow"
SOURCE_B = "https://www.bloomberg.com/news/nio-headcount"
SOURCE_C = "https://www.cnbc.com/2024/nio-outlook"


def _fact_base(hard_facts, analyst_opinions=None) -> FactBase:
    return FactBase(
        company_id="company-nio",
        last_refresh_date=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
        hard_facts=hard_facts,
        analyst_opinions=analyst_opinions or [],
    )


def test_layoff_vs_hiring_conflict():
    fact_base = _fact_base(
        [
            FactItem(content="NIO announced layoffs to cut costs.", source_url=SOURCE_A),
            FactItem(content="The company is hiring at scale to expand its team.", source_url=SOURCE_B),
        ]
    )

    conflicts = detect_fact_conflicts(fact_base)

    assert len(conflicts) == 1
    assert conflicts[0]["conflict_type"] == "headcount_up_down"
    assert conflicts[0]["source_a"] == SOURCE_A
    assert conflicts[0]["source_b"] == SOURCE_B
    assert conflicts[0]["fact_a"] == "NIO announced layoffs to cut costs."
    assert conflicts[0]["fact_b"] == "The company is hiring at scale to expand its team."


def test_no_conflict_within_same_fact():
    fact_base = _fact_base(
        [
            FactItem(
                content="The company announced layoffs and then resumed hiring, leaving headcount roughly flat.",
                source_url=SOURCE_A,
            )
        ]
    )

    assert detect_fact_conflicts(fact_base) == []


def test_english_keywords():
    fact_base = _fact_base(
        [
            FactItem(content="NIO announced layoffs across its European unit.", source_url=SOURCE_A),
            FactItem(content="The company is on a hiring spree in China.", source_url=SOURCE_B),
        ]
    )

    conflicts = detect_fact_conflicts(fact_base)

    assert len(conflicts) == 1
    assert conflicts[0]["conflict_type"] == "headcount_up_down"


def test_opinions_ignored():
    fact_base = _fact_base(
        [FactItem(content="NIO reported an 8% revenue decline.", source_url=SOURCE_A)],
        [
            FactItem(content="Analysts believe the company is cutting jobs.", source_url=SOURCE_B),
            FactItem(content="Other analysts say the company is hiring at scale.", source_url=SOURCE_C),
        ],
    )

    assert detect_fact_conflicts(fact_base) == []


def test_dedup_symmetric():
    # Only a reverse-order match exists between this pair (i hits B, j hits A).
    # The engine walks ordered pairs i < j, so (j, i) must not produce a second row.
    fact_base = _fact_base(
        [
            FactItem(content="NIO is on a hiring spree.", source_url=SOURCE_A),
            FactItem(content="NIO announced layoffs in Europe.", source_url=SOURCE_B),
        ]
    )

    conflicts = detect_fact_conflicts(fact_base)

    assert len(conflicts) == 1
    assert conflicts[0]["source_a"] == SOURCE_A
    assert conflicts[0]["source_b"] == SOURCE_B


def test_single_fact_internal_contradiction_skipped():
    fact_base = _fact_base(
        [
            FactItem(
                content="NIO cut jobs in Q2 but went on a hiring spree in Q3.",
                source_url=SOURCE_A,
            ),
            FactItem(content="NIO announced layoffs in Europe.", source_url=SOURCE_B),
        ]
    )

    assert detect_fact_conflicts(fact_base) == []


def test_reverse_direction_description_correct():
    fact_base = _fact_base(
        [
            FactItem(content="NIO is on a hiring spree.", source_url=SOURCE_A),
            FactItem(content="NIO announced layoffs affecting 400 roles.", source_url=SOURCE_B),
        ]
    )

    conflicts = detect_fact_conflicts(fact_base)

    assert len(conflicts) == 1
    description = conflicts[0]["description"]
    assert "fact A indicates 'hiring" in description
    assert "fact B indicates 'layoff" in description
    assert description.index("fact A indicates 'hiring") < description.index(
        "fact B indicates 'layoff"
    )


def test_rif_word_boundary_no_false_positive():
    fact_base = _fact_base(
        [
            FactItem(content="tariff increase on imported parts raised costs.", source_url=SOURCE_A),
            FactItem(content="The company is on a hiring spree.", source_url=SOURCE_B),
        ]
    )

    assert detect_fact_conflicts(fact_base) == []


@pytest.mark.parametrize(
    "phrase",
    [
        "The company will cut jobs in Q3",
        "Q3 job cuts were disclosed",
        "It will cut roles in procurement",
        "Several roles cut last quarter",
    ],
)
def test_cut_roles_phrases(phrase):
    fact_base = _fact_base(
        [
            FactItem(content=phrase, source_url=SOURCE_A),
            FactItem(content="The company is on a hiring spree.", source_url=SOURCE_B),
        ]
    )

    conflicts = detect_fact_conflicts(fact_base)

    assert len(conflicts) == 1
    assert conflicts[0]["conflict_type"] == "headcount_up_down"


def test_rif_positive_hit():
    fact_base = _fact_base(
        [
            FactItem(content="The company announced a RIF of 400 roles.", source_url=SOURCE_A),
            FactItem(content="The company is on a hiring spree.", source_url=SOURCE_B),
        ]
    )

    conflicts = detect_fact_conflicts(fact_base)

    assert len(conflicts) == 1
    assert conflicts[0]["conflict_type"] == "headcount_up_down"


def test_rules_have_four_rule_names():
    names = [rule_name for rule_name, _group_a, _group_b in CONFLICT_RULES]
    assert names == [
        "headcount_up_down",
        "cost_up_down",
        "expand_contract",
        "profit_loss",
    ]
