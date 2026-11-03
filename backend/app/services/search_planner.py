# /backend/app/services/search_planner.py
from __future__ import annotations

import json
import os
from typing import Dict, List

from pydantic import BaseModel, Field

from app.services.logger_service import get_logger

logger = get_logger("search_planner")

# =============================================================================
# Source registry
# =============================================================================
# The model decides which categories are relevant; it may only pick domains
# from this registry, so a hallucinated host can never reach Tavily.

SOURCE_REGISTRY: Dict[str, List[str]] = {
    "financial_wire": ["reuters.com", "bloomberg.com"],
    "financial_business": [
        "wsj.com",
        "ft.com",
        "cnbc.com",
        "finance.yahoo.com",
        "businessinsider.com",
        "markets.businessinsider.com",
    ],
    "asia_market": ["nikkei.com", "36kr.com"],
    "auto_industry": [
        "autonews.gasgoo.com",
        "cnevpost.com",
        "insideevs.com",
        "electrek.co",
    ],
    # Primary sources: regulatory filings, press releases and investor
    # relations material. Authoritative but sparse, so the planner only picks
    # this category when primary evidence is what the brief actually needs.
    "official_primary": [
        "sec.gov",
        "prnewswire.com",
        "globenewswire.com",
        "businesswire.com",
    ],
}

CATEGORY_DESCRIPTIONS = {
    "financial_wire": "global wire services: breaking corporate, M&A, regulatory and legal news",
    "financial_business": "business and financial press: markets, earnings, corporate strategy",
    "asia_market": "Asia-Pacific business coverage",
    "auto_industry": "automotive trade press: OEM sales, production, supply chain",
    "official_primary": (
        "primary sources: regulatory filings, company press releases and "
        "investor relations disclosures"
    ),
}

DEFAULT_CATEGORIES = ("financial_wire", "financial_business", "asia_market")

MAX_QUERIES = 4
MAX_DOMAIN_CATEGORIES = 3

FUNDAMENTALS_FRAMING = (
    "No commercial objective was supplied, so the brief must cover the company's "
    "recent business direction, what is working well, and what deserves caution."
)


# =============================================================================
# Plan model
# =============================================================================

class SearchPlan(BaseModel):
    """How the retrieval layer should attack this company."""

    industry: str = "general"
    categories: List[str] = Field(default_factory=list)
    domains: List[str] = Field(default_factory=list)
    queries: List[str] = Field(default_factory=list)
    fallback_queries: List[str] = Field(default_factory=list)
    fundamentals: bool = False
    rationale: str = ""


PLANNER_PROMPT = """You plan web research for a pre-meeting intelligence brief.

You decide two things:
1. Which search queries to run against Tavily.
2. Which publisher categories to search, chosen ONLY from the allowed list.

ALLOWED CATEGORIES:
{categories}

RULES:
- If a commercial objective is supplied, every query must combine the company name with that objective. The queries must retrieve evidence the negotiator can actually use: pricing, contract terms, volumes, cost pressure, supplier disputes, financial stress, leadership changes.
- If NO objective is supplied, the brief is a fundamentals brief: business direction, what is working, what deserves caution. Produce queries covering those three angles instead of negotiation angles.
- Write 2 to {max_queries} concise queries. Each query is company + topic keywords, not a sentence.
- Select 1 to {max_categories} categories that genuinely carry this company's industry news. A bank is not covered by automotive trade press, an automaker is. When unsure, prefer financial categories only.
- Never invent a website. Categories are the only lever for allowed sources: any category you name outside the allowed list is discarded, so a fabricated host can never reach the search API. When primary evidence would sit on the company's own site or in its filings, name the company in the query instead (for example "<company> annual report segment margin" or "<company> investor relations guidance") rather than inventing a host.

Reply with JSON only:
{{"industry": "short industry label", "categories": ["category_id", ...], "queries": ["query", ...], "fundamentals": true|false, "rationale": "one sentence"}}"""


# =============================================================================
# Helpers
# =============================================================================

def _resolve_domains(categories: List[str]) -> List[str]:
    """Map category ids to concrete domains, validating against the registry."""
    domains: List[str] = []
    for category in categories:
        for domain in SOURCE_REGISTRY.get(category, []):
            if domain not in domains:
                domains.append(domain)
    return domains


def _fallback_queries(company: str, purpose: str, fundamentals: bool) -> List[str]:
    """Extra queries used to widen coverage after the first round.

    A pool wide enough that the coverage loop can run many rounds before it
    runs dry. Rounds stop on their own once they stop adding usable sources, so
    a long pool never implies a long run.
    """
    cleaned_purpose = purpose.strip()

    if fundamentals or not cleaned_purpose:
        return [
            f"{company} annual report segment revenue",
            f"{company} investor relations guidance outlook",
            f"{company} management commentary strategy shift",
            f"{company} analyst downgrade upgrade target",
            f"{company} regulatory investigation compliance",
            f"{company} restructuring programme cost base",
            f"{company} quarterly results net interest income",
            f"{company} capital return buyback dividend",
            f"{company} credit quality provisioning",
            f"{company} cost income ratio efficiency",
            f"{company} market share competition",
            f"{company} leadership succession board change",
            f"{company} litigation settlement fine",
            f"{company} merger acquisition stake",
            f"{company} guidance cut profit warning",
            f"{company} press release results announcement",
            f"{company} balance sheet capital ratio",
            f"{company} strategy update investor day",
            f"{company} risk disclosure annual filing",
            f"{company} sector outlook peers comparison",
        ]

    return [
        f"{company} {cleaned_purpose} pricing pressure",
        f"{company} {cleaned_purpose} supplier dispute",
        f"{company} contract renewal terms negotiation",
        f"{company} procurement leadership change",
        f"{company} volume commitment capacity",
        f"{company} annual report {cleaned_purpose}",
        f"{company} {cleaned_purpose} cost reduction target",
        f"{company} {cleaned_purpose} service level penalty",
        f"{company} {cleaned_purpose} incumbent vendor",
        f"{company} {cleaned_purpose} tender bid award",
        f"{company} {cleaned_purpose} contract expiry renewal",
        f"{company} {cleaned_purpose} escalation clause",
        f"{company} {cleaned_purpose} payment terms",
        f"{company} layoffs cost cuts margin pressure",
        f"{company} earnings guidance decline",
        f"{company} restructuring programme savings",
        f"{company} press release announcement",
        f"{company} regulatory filing disclosure",
        f"{company} analyst commentary estimates",
        f"{company} peer benchmark comparison",
    ]


def _fallback_plan(company: str, purpose: str) -> SearchPlan:
    """Heuristic plan used when the planner model is unavailable.

    Deliberately industry-agnostic: it searches financial categories only, so a
    bank or an automaker both get valid coverage without any hard-coded sector
    assumption.
    """
    cleaned_purpose = purpose.strip()

    if cleaned_purpose:
        queries = [
            f"{company} {cleaned_purpose}",
            f"{company} {cleaned_purpose} contract terms cost",
            f"{company} layoffs cost cuts margin pressure",
            f"{company} earnings guidance decline",
        ]
        fundamentals = False
    else:
        queries = [
            f"{company} business strategy latest developments",
            f"{company} quarterly results earnings guidance",
            f"{company} layoffs cost cuts margin pressure decline",
            f"{company} growth highlights expansion",
        ]
        fundamentals = True

    categories = list(DEFAULT_CATEGORIES)

    return SearchPlan(
        industry="general",
        categories=categories,
        domains=_resolve_domains(categories),
        queries=queries[:MAX_QUERIES],
        fallback_queries=_fallback_queries(company, purpose, fundamentals),
        fundamentals=fundamentals,
        rationale="heuristic fallback: financial categories only",
    )


def _sanitize(plan: SearchPlan, company: str, purpose: str) -> SearchPlan:
    """Constrain a model-produced plan to what the registry and Tavily allow."""
    categories = [c for c in plan.categories if c in SOURCE_REGISTRY]
    if not categories:
        categories = list(DEFAULT_CATEGORIES)
    categories = categories[:MAX_DOMAIN_CATEGORIES]

    domains = _resolve_domains(categories)

    queries: List[str] = []
    for query in plan.queries:
        text = query.strip()
        if not text:
            continue
        if company.lower() not in text.lower():
            text = f"{company} {text}"
        if text not in queries:
            queries.append(text)

    if not queries:
        return _fallback_plan(company, purpose)

    return SearchPlan(
        industry=plan.industry or "general",
        categories=categories,
        domains=domains,
        queries=queries[:MAX_QUERIES],
        fallback_queries=plan.fallback_queries or _fallback_queries(
            company, purpose, bool(plan.fundamentals) or not purpose.strip()
        ),
        fundamentals=bool(plan.fundamentals) or not purpose.strip(),
        rationale=plan.rationale,
    )


def _parse_plan(content: str) -> SearchPlan:
    """Parse the planner reply, tolerating a JSON object with extra keys."""
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("planner reply was not a JSON object")
    return SearchPlan.model_validate(payload)


# =============================================================================
# Entry point
# =============================================================================

async def plan_searches(company: str, purpose: str) -> SearchPlan:
    """Ask the model which queries and publishers to use for this company.

    Falls back to a financial-only heuristic plan when the model is
    unavailable or returns something unusable, so retrieval never breaks.
    """
    categories_text = "\n".join(
        f"- {name}: {description}"
        for name, description in CATEGORY_DESCRIPTIONS.items()
    )

    objective = purpose.strip() or FUNDAMENTALS_FRAMING
    user_message = (
        f"COMPANY: {company}\n"
        f"COMMERCIAL OBJECTIVE: {objective}\n"
        f"OBJECTIVE SUPPLIED: {'yes' if purpose.strip() else 'no'}\n\n"
        "Plan the research."
    )

    try:
        from app.services.analysis import _build_client

        client, model = _build_client()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": PLANNER_PROMPT.format(
                        categories=categories_text,
                        max_queries=MAX_QUERIES,
                        max_categories=MAX_DOMAIN_CATEGORIES,
                    ),
                },
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        content = response.choices[0].message.content or ""
        if not content:
            raise ValueError("planner returned empty content")

        plan = _sanitize(_parse_plan(content), company, purpose)
        logger.info(
            "Search plan | industry=%s | categories=%s | fundamentals=%s | queries=%s",
            plan.industry,
            plan.categories,
            plan.fundamentals,
            plan.queries,
        )
        return plan

    except Exception as exc:
        logger.warning("Search planner unavailable, using heuristic plan | %s", exc)
        plan = _fallback_plan(company, purpose)
        logger.info(
            "Search plan (fallback) | categories=%s | fundamentals=%s | queries=%s",
            plan.categories,
            plan.fundamentals,
            plan.queries,
        )
        return plan
