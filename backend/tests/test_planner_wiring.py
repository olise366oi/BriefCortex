"""The planner must be able to run its own domain resolution.

A signature change broke the real call path while every unit test still passed,
because no test exercised `_fallback_plan` through to `_resolve_domains`. This
covers the wiring rather than the pieces.
"""
from __future__ import annotations

from app.services.media_trust import MediaPolicy
from app.services.search_planner import (
    _fallback_plan,
    _resolve_domains_for_jurisdiction,
)
from app.services.source_rules import MIN_CLEAN_TEXT_LENGTH  # noqa: F401 (import sanity)


JP_POLICY = MediaPolicy(
    jurisdiction="JP",
    national=("nikkei.com", "jiji.com"),
    jurisdiction_known=True,
    source="stored",
    confidence="high",
)


def test_national_press_comes_before_registry_domains():
    domains = _resolve_domains_for_jurisdiction(["financial_wire"], JP_POLICY)

    assert domains[0] == "nikkei.com"
    assert "jiji.com" in domains
    assert "reuters.com" in domains
    # Registry publishers follow the national titles.
    assert domains.index("nikkei.com") < domains.index("reuters.com")


def test_a_policy_without_national_press_still_resolves():
    policy = MediaPolicy(jurisdiction=None, jurisdiction_known=False)

    domains = _resolve_domains_for_jurisdiction(["financial_wire"], policy)

    assert "reuters.com" in domains
    assert "nikkei.com" not in domains


def test_the_fallback_plan_accepts_a_policy_and_returns_domains():
    # The regression this guards: the fallback path called the resolver with the
    # old three-argument signature and crashed at runtime.
    plan = _fallback_plan("YKK", "buy 50,000 zippers", JP_POLICY)

    assert plan.domains
    assert plan.domains[0] == "nikkei.com"
    assert plan.queries


def test_the_fallback_plan_without_a_policy_still_works():
    plan = _fallback_plan("YKK", "", None)

    assert plan.domains
    assert plan.fundamentals is True
