# /pre-meeting-agent/services/analysis.py
from __future__ import annotations

import os
from typing import List, Tuple

from openai import AsyncOpenAI
from pydantic import BaseModel, HttpUrl, ValidationError

from .ingestion import FactBase


class Claim(BaseModel):
    claim: str
    source_url: HttpUrl


class NewsModule(BaseModel):
    items: List[Claim] = []


class HiresModule(BaseModel):
    items: List[Claim] = []


class LaunchesModule(BaseModel):
    items: List[Claim] = []


class OpenQuestionModule(BaseModel):
    items: List[Claim] = []


class RisksModule(BaseModel):
    items: List[Claim] = []


class ExecutiveSummary(BaseModel):
    summary: str
    recommendation: str
    confidence: str


class BriefingReport(BaseModel):
    news: NewsModule = NewsModule()
    hires: HiresModule = HiresModule()
    launches: LaunchesModule = LaunchesModule()
    open_questions: OpenQuestionModule = OpenQuestionModule()
    risks: RisksModule = RisksModule()
    executive_summary: ExecutiveSummary


class BriefingGenerationError(Exception):
    """Raised when the LLM fails to produce a valid BriefingReport after retries."""


def _build_client() -> Tuple[AsyncOpenAI, str]:
    """Build an async OpenAI-compatible client.

    Configuration priority:
    1. Explicit: BRIEFING_API_KEY + optional BRIEFING_BASE_URL + BRIEFING_MODEL
    2. Fallback to DEEPSEEK_API_KEY (base_url set to DeepSeek, model deepseek-chat)
    3. Fallback to OPENAI_API_KEY (default OpenAI base_url, model gpt-4o-mini)
    """
    explicit_key = os.getenv("BRIEFING_API_KEY")
    explicit_base = os.getenv("BRIEFING_BASE_URL")
    explicit_model = os.getenv("BRIEFING_MODEL")

    if explicit_key:
        client = (
            AsyncOpenAI(api_key=explicit_key, base_url=explicit_base)
            if explicit_base
            else AsyncOpenAI(api_key=explicit_key)
        )
        return client, explicit_model or "gpt-4o-mini"

    if os.getenv("DEEPSEEK_API_KEY"):
        client = AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url=explicit_base or "https://api.deepseek.com",
        )
        return client, explicit_model or "deepseek-chat"

    if os.getenv("OPENAI_API_KEY"):
        client = (
            AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=explicit_base)
            if explicit_base
            else AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        )
        return client, explicit_model or "gpt-4o-mini"

    raise BriefingGenerationError(
        "No LLM API key found. Set BRIEFING_API_KEY, DEEPSEEK_API_KEY, or OPENAI_API_KEY."
    )


SYSTEM_PROMPT = """You are a pre-meeting intelligence analyst. You receive a FactBase object containing:
- hard_facts: verified excerpts from authoritative sources (Reuters, Bloomberg, WSJ, CNBC)
- analyst_opinions: first-sentence excerpts from middle paragraphs, representing analyst views
- conflicts: always empty at this stage; do not attempt to detect conflicts

Your task is to produce a BriefingReport as a JSON object for a meeting with the stated purpose.

RULES - read carefully:

1. ROUTING RULES (where each input goes):
   - hard_facts MUST go into news, hires, or launches, depending on topic.
     Never place a hard fact into risks or open_questions as the primary content.
   - analyst_opinions MUST go into risks or open_questions only.
     Never place an analyst opinion into news, hires, or launches.
   - open_questions items derived from analyst_opinions MUST be prefixed with "[UNVERIFIED] ".

2. NO FABRICATION:
   - Never present an analyst opinion as an objective fact.
   - Never invent sources, dates, or numbers not present in the input.
   - If a claim cannot be traced to a specific source_url in the input, omit it.

3. MEETING PURPOSE ALIGNMENT:
   - The user provides meeting_purpose. Weight your selection of facts toward
     topics that matter for that purpose.
   - Example: if purpose is "supplier price negotiation", prioritize cost,
     margin, supply-chain, and capacity facts over general PR news.

4. EXECUTIVE SUMMARY - NEGOTIATION RECOMMENDATION:
   - Based ONLY on facts present in the report, give a concrete recommendation.
   - Include specifics where facts support them: price pressure direction,
     payment terms stance, concession boundaries, walk-away conditions.
   - If facts are insufficient to justify a recommendation, you MUST output
     a conservative strategy and set confidence to "low".
   - Do not give generic advice. Every recommendation must trace to at least one claim
     in the report.

5. OUTPUT FORMAT:
   - Return a single JSON object matching exactly this schema:
     {
       "news": {"items": [{"claim": str, "source_url": str}]},
       "hires": {"items": [{"claim": str, "source_url": str}]},
       "launches": {"items": [{"claim": str, "source_url": str}]},
       "open_questions": {"items": [{"claim": str, "source_url": str}]},
       "risks": {"items": [{"claim": str, "source_url": str}]},
       "executive_summary": {"summary": str, "recommendation": str, "confidence": str}
     }
   - Do not add fields outside the schema.
   - Empty modules are allowed; do not fill them with invented content.
   - Return JSON only, no markdown fences, no commentary.
"""


def _format_fact_base_for_prompt(fact_base: FactBase) -> str:
    lines = ["=== HARD FACTS ==="]
    if not fact_base.hard_facts:
        lines.append("(none)")
    for i, item in enumerate(fact_base.hard_facts, 1):
        lines.append(f"[H{i}] ({item.source_url}) {item.content}")

    lines.append("\n=== ANALYST OPINIONS ===")
    if not fact_base.analyst_opinions:
        lines.append("(none)")
    for i, item in enumerate(fact_base.analyst_opinions, 1):
        lines.append(f"[O{i}] ({item.source_url}) {item.content}")

    return "\n".join(lines)


def _build_user_message(fact_base: FactBase, meeting_purpose: str) -> str:
    fact_block = _format_fact_base_for_prompt(fact_base)
    return (
        f"Meeting purpose: {meeting_purpose}\n\n"
        f"FactBase input:\n{fact_block}\n\n"
        "Produce a BriefingReport JSON object following all rules in the system prompt."
    )


async def _call_llm_with_retry(
    client: AsyncOpenAI,
    model: str,
    user_message: str,
    max_retries: int,
) -> BriefingReport:
    """Call the LLM and enforce BriefingReport schema with retries.

    On validation failure, feed the error back to the model and retry.
    On network or auth errors, retry up to max_retries then raise.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    last_error: Exception = RuntimeError("no attempt executed")

    for attempt in range(1, max_retries + 1):
        content = ""
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            content = response.choices[0].message.content or ""
            if not content:
                raise ValueError("LLM returned empty content")

            return BriefingReport.model_validate_json(content)

        except ValidationError as exc:
            last_error = exc
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous response failed schema validation: "
                        f"{exc}. Return ONLY a valid JSON object that strictly "
                        "matches the BriefingReport schema. No markdown fences, "
                        "no commentary."
                    ),
                }
            )

        except Exception as exc:
            last_error = exc

    raise BriefingGenerationError(
        f"LLM failed to produce a valid BriefingReport after {max_retries} attempts: {last_error}"
    ) from last_error


async def generate_briefing_report(
    fact_base: FactBase,
    meeting_purpose: str,
    max_retries: int = 3,
) -> BriefingReport:
    """Generate a validated BriefingReport from a FactBase.

    Raises BriefingGenerationError if the LLM fails after max_retries.
    """
    client, model = _build_client()
    user_message = _build_user_message(fact_base, meeting_purpose)
    return await _call_llm_with_retry(client, model, user_message, max_retries)
