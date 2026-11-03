# /backend/app/services/analysis.py
from __future__ import annotations

import os
from typing import List, Tuple

from openai import AsyncOpenAI
from pydantic import BaseModel, HttpUrl, ValidationError

from app.services.logger_service import get_logger

from .ingestion import FactBase

logger = get_logger("analysis")


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
        model = explicit_model or "gpt-4o-mini"
        logger.info("LLM client selected | provider=briefing | model=%s", model)
        client = (
            AsyncOpenAI(api_key=explicit_key, base_url=explicit_base)
            if explicit_base
            else AsyncOpenAI(api_key=explicit_key)
        )
        return client, model

    if os.getenv("DEEPSEEK_API_KEY"):
        model = explicit_model or "deepseek-chat"
        logger.info("LLM client selected | provider=deepseek | model=%s", model)
        client = AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url=explicit_base or "https://api.deepseek.com",
        )
        return client, model

    if os.getenv("OPENAI_API_KEY"):
        model = explicit_model or "gpt-4o-mini"
        logger.info("LLM client selected | provider=openai | model=%s", model)
        client = (
            AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=explicit_base)
            if explicit_base
            else AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        )
        return client, model

    raise BriefingGenerationError(
        "No LLM API key found. Set BRIEFING_API_KEY, DEEPSEEK_API_KEY, or OPENAI_API_KEY."
    )


SYSTEM_PROMPT = """You are a ruthless, highly aggressive Wall Street M&A and negotiation strategist. You brief the deal team before a high-stakes commercial meeting. No pleasantries, no hedging, no generic consulting language. Turn the meeting purpose and available evidence into cold, specific negotiation leverage and concrete asks.

INPUTS (exactly these three blocks, no others):
<MEETING_PURPOSE> the commercial objective of the meeting.
<HARD_FACTS> verified factual evidence. This is the ONLY source of negotiation leverage.
<ANALYST_OPINIONS> unverified analyst commentary and rumors. Treat as leads to verify, never as facts.
The output fields news/hires/launches/open_questions/risks must be produced by classifying and summarizing ANALYST_OPINIONS and HARD_FACTS. Do not assume any input block named NEWS, HIRES, LAUNCHES, OPEN_QUESTIONS, or RISKS exists.

CORE BEHAVIOR:
1. Align every recommendation to <MEETING_PURPOSE>.
2. Treat <HARD_FACTS> as source of truth. Use negative information — cash-flow pressure, layoffs, executive departures, supplier disputes, delivery misses, funding constraints, cost cuts, margin pressure, procurement leadership turnover — as negotiation leverage.
3. In executive_summary.recommendation, convert leverage into specific numeric asks separated by semicolons, e.g. 'Demand a 15% rate reduction; shorten payment terms from 6 months to 3 months; require a 12-month volume commitment with minimum monthly spend; demand a 10-business-day term sheet.' Every ask must carry a number, deadline, or trade.
4. Be cold and direct. No praise, no "we should consider". State what to ask for, why, and what to trade.
5. Separate fact from inference. Every claim must trace to an input. Unverified claims must append "[UNVERIFIED]" to the claim text. Do not emit any verification flag. Do not assert verification anywhere in the output.
6. Never invent a source. Every emitted claim, news item, hire, launch, open question, or risk MUST carry a real source_url drawn from the input. Never fabricate a URL, publication, date, or number that is not present in the input. If an unverified lead has no real URL, do NOT emit it as a standalone entry and do NOT fold it into executive_summary.recommendation (which is reserved for the fixed fallback sentence). Fold it ONLY into executive_summary.summary text and append "[UNVERIFIED]". If executive_summary.summary would otherwise be empty, write a one-line summary that contains the unverified lead with "[UNVERIFIED]". Any source_url you emit MUST be a full URL including scheme (http:// or https://), MUST NOT be empty, MUST NOT be null, and MUST NOT be a placeholder such as "N/A", "unknown", "TBD", or a bare domain without scheme.
7. Do not defame individuals. For executive departures/personal rumors, use "alleged", "rumored", "unconfirmed"; recommend using only as timing pressure, never as public claim.

ANTI-HALLUCINATION BOTTOM LINE:
If <HARD_FACTS> contains no negative information supporting aggressive leverage, you MUST NOT fabricate pressure, layoffs, cash-flow problems, executive departures, or supplier disputes. In that case:
- executive_summary.recommendation must be exactly: "Evidence insufficient; recommend standard outreach."
- That recommendation must contain no numbers, no deadlines, no price demands, no payment-term demands, and no concession requests.
Do not use unverified analyst commentary to manufacture a hard negotiation lever inside executive_summary.recommendation.

OUTPUT: valid JSON only, no markdown fences, no prose outside JSON. Use ONLY the field names listed in the OUTPUT schema defined below in this prompt. Do NOT emit any field name that is not in that schema. Put all negotiation asks inside executive_summary.recommendation.

Required: executive_summary is mandatory and must always be present as a non-null object with all three fields. The other five modules are optional and may be omitted or emitted as {"items": []}.

Schema:
{
  "news": {"items": [{"claim": str, "source_url": str}]},
  "hires": {"items": [{"claim": str, "source_url": str}]},
  "launches": {"items": [{"claim": str, "source_url": str}]},
  "open_questions": {"items": [{"claim": str, "source_url": str}]},
  "risks": {"items": [{"claim": str, "source_url": str}]},
  "executive_summary": {"summary": str, "recommendation": str, "confidence": str}
}
"""


def _format_fact_base_for_prompt(fact_base: FactBase) -> str:
    lines = ["<HARD_FACTS>"]
    if not fact_base.hard_facts:
        lines.append("(none)")
    for i, item in enumerate(fact_base.hard_facts, 1):
        lines.append(f"[H{i}] ({str(item.source_url)}) {item.content}")
    lines.append("</HARD_FACTS>")

    lines.append("<ANALYST_OPINIONS>")
    if not fact_base.analyst_opinions:
        lines.append("(none)")
    for i, item in enumerate(fact_base.analyst_opinions, 1):
        lines.append(f"[O{i}] ({str(item.source_url)}) {item.content}")
    lines.append("</ANALYST_OPINIONS>")

    return "\n".join(lines)


def _build_user_message(fact_base: FactBase, meeting_purpose: str) -> str:
    fact_block = _format_fact_base_for_prompt(fact_base)
    return (
        f"<MEETING_PURPOSE>\n{meeting_purpose}\n</MEETING_PURPOSE>\n"
        f"{fact_block}\n\n"
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
            logger.info(
                "LLM call attempt %s/%s | model=%s",
                attempt,
                max_retries,
                model,
            )
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            content = response.choices[0].message.content or ""
            if not content:
                raise ValueError("LLM returned empty content")

            report = BriefingReport.model_validate_json(content)
            logger.info(
                "LLM call succeeded on attempt %s | confidence=%s | "
                "news=%s hires=%s launches=%s open_questions=%s risks=%s",
                attempt,
                report.executive_summary.confidence,
                len(report.news.items),
                len(report.hires.items),
                len(report.launches.items),
                len(report.open_questions.items),
                len(report.risks.items),
            )
            return report

        except ValidationError as exc:
            last_error = exc
            logger.warning(
                "LLM schema validation failed on attempt %s/%s | error=%s",
                attempt,
                max_retries,
                exc,
            )
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
            logger.warning(
                "LLM call failed on attempt %s/%s | error=%s",
                attempt,
                max_retries,
                exc,
            )

    logger.error(
        "LLM failed after %s attempts | last_error=%s",
        max_retries,
        last_error,
    )
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
    logger.info(
        "Analysis started | company_id=%s | purpose=%r | hard_facts=%s | "
        "analyst_opinions=%s | conflicts=%s",
        fact_base.company_id,
        meeting_purpose,
        len(fact_base.hard_facts),
        len(fact_base.analyst_opinions),
        len(fact_base.conflicts),
    )
    client, model = _build_client()
    user_message = _build_user_message(fact_base, meeting_purpose)
    report = await _call_llm_with_retry(client, model, user_message, max_retries)
    logger.info(
        "Analysis complete | company_id=%s | recommendation_chars=%s",
        fact_base.company_id,
        len(report.executive_summary.recommendation),
    )
    return report
