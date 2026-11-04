# /backend/app/services/analysis.py
from __future__ import annotations

from typing import Tuple

from typing import Tuple

from pydantic import ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_random_exponential

from app.core.config import get_settings
from app.models.briefing import BriefingReport
from app.services.bifrost_headers import build_bifrost_headers
from app.services.llm_client import (
    LLMUnavailableError,
    build_client,
    is_permanent_error,
    is_retryable_error,
)
from app.services.logger_service import get_logger

from .ingestion import FactBase

logger = get_logger("analysis")


class BriefingGenerationError(Exception):
    """Raised when the LLM fails to produce a valid BriefingReport after retries."""


SYSTEM_PROMPT = """You are a ruthless, highly aggressive Wall Street M&A and negotiation strategist. You brief the deal team before a high-stakes commercial meeting. No pleasantries, no hedging, no generic consulting language. Turn the meeting purpose and available evidence into cold, specific negotiation leverage and concrete asks.

INPUTS (exactly these four blocks, no others):
<MEETING_PURPOSE> the commercial objective of the meeting. It may be "(none supplied)".
<HARD_FACTS> verified factual evidence. This is the ONLY source of negotiation leverage.
<ANALYST_OPINIONS> unverified analyst commentary and rumors. Treat as leads to verify, never as facts.
<HIRING> current job postings at the company. These signal intent, not outcome: read them to infer where people and budget are moving, never quote them as a reported fact.
The output fields news/hires/launches/open_questions/risks must be produced by classifying and summarizing ANALYST_OPINIONS, HARD_FACTS and HIRING. Do not assume any input block named NEWS, HIRES, LAUNCHES, OPEN_QUESTIONS, or RISKS exists.

OUTPUT STYLE:
- One pass, not a catalogue. The brief is a single coherent read of the company, so do not manufacture one observation per input item and do not restate the evidence item by item.
- Keep each module short: at most 3 items, and only where an item genuinely differs from the others. Fewer is better than padded.
- executive_summary.summary is one tight paragraph that says what matters and why. Never a list of findings.
- Length discipline: a reader skimming before a meeting must get the whole picture in one screen. If you can cut a sentence without losing meaning, cut it.

TWO MODES — read the MODE line in the user message and follow exactly one:

MODE negotiation (a real <MEETING_PURPOSE> is supplied):
- Follow CORE BEHAVIOR and ANTI-HALLUCINATION BOTTOM LINE below.

MODE fundamentals (the purpose block says "(none supplied)"):
- There is no deal to negotiate. Do NOT invent an objective and do NOT emit numeric asks, price demands, payment-term demands or concessions.
- Populate the modules with recent business facts: news = reported developments, launches = new products, capacity or market moves, hires = leadership and staffing changes, risks = signals of deterioration, open_questions = what the evidence does not settle.
- <HIRING> feeds the hires module and the directional read: a cluster of roles in one function is evidence of where effort is going.
- executive_summary.recommendation becomes one short directional read on the company, not a demand list. Two or three sentences: the business direction, what is demonstrably working, what deserves caution. Every statement must trace to <HARD_FACTS> or <HIRING> (or be marked [UNVERIFIED]).
- executive_summary.confidence must state what the evidence supports and what it does not.
- The ANTI-HALLUCINATION BOTTOM LINE fallback sentence does NOT apply in this mode; it applies only when a real objective exists but the evidence cannot support an aggressive ask.

CORE BEHAVIOR:
1. Align every recommendation to <MEETING_PURPOSE>.
2. Treat <HARD_FACTS> as source of truth. Use negative information — cash-flow pressure, layoffs, executive departures, supplier disputes, delivery misses, funding constraints, cost cuts, margin pressure, procurement leadership turnover — as negotiation leverage.
3. In executive_summary.recommendation, convert leverage into specific numeric asks separated by semicolons, e.g. 'Demand a 15% rate reduction; shorten payment terms from 6 months to 3 months; require a 12-month volume commitment with minimum monthly spend; demand a 10-business-day term sheet.' Every ask must carry a number, deadline, or trade.
4. Be cold and direct. No praise, no "we should consider". State what to ask for, why, and what to trade.
5. Separate fact from inference. Every claim must trace to an input. Unverified claims must append "[UNVERIFIED]" to the claim text. Do not emit any verification flag. Do not assert verification anywhere in the output.
6. Never invent a source. Every emitted claim, news item, hire, launch, open question, or risk MUST carry a real source_url drawn from the input. Never fabricate a URL, publication, date, or number that is not present in the input. If an unverified lead has no real URL, do NOT emit it as a standalone entry and do NOT fold it into executive_summary.recommendation (which is reserved for the fixed fallback sentence). Fold it ONLY into executive_summary.summary text and append "[UNVERIFIED]". If executive_summary.summary would otherwise be empty, write a one-line summary that contains the unverified lead with "[UNVERIFIED]". Any source_url you emit MUST be a full URL including scheme (http:// or https://), MUST NOT be empty, MUST NOT be null, and MUST NOT be a placeholder such as "N/A", "unknown", "TBD", or a bare domain without scheme.
7. Do not defame individuals. For executive departures/personal rumors, use "alleged", "rumored", "unconfirmed"; recommend using only as timing pressure, never as public claim.
8. <HIRING> is intent, not fact. A posting tells you where the company is putting people; it does not prove a strategy has been executed. Never quote a job title as a reported event in news or risks, and never build a numeric ask on a posting alone.

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

    lines.append("<HIRING>")
    if not fact_base.hiring:
        lines.append("(none)")
    for i, item in enumerate(fact_base.hiring, 1):
        lines.append(f"[J{i}] ({str(item.source_url)}) {item.content}")
    lines.append("</HIRING>")

    return "\n".join(lines)


def _build_user_message(fact_base: FactBase, meeting_purpose: str) -> str:
    fact_block = _format_fact_base_for_prompt(fact_base)
    purpose_text = meeting_purpose.strip()

    if purpose_text:
        mode_block = (
            f"<MEETING_PURPOSE>\n{purpose_text}\n</MEETING_PURPOSE>\n"
            "MODE: negotiation. Use HARD_FACTS as leverage against that objective."
        )
    else:
        mode_block = (
            "<MEETING_PURPOSE>\n(none supplied)\n</MEETING_PURPOSE>\n"
            "MODE: fundamentals. No commercial objective was supplied, so do not "
            "invent one and do not manufacture negotiation asks. Report the "
            "company's recent business direction, what is demonstrably working, "
            "and what deserves caution."
        )

    return (
        f"{mode_block}\n"
        f"{fact_block}\n\n"
        "Produce a BriefingReport JSON object following all rules in the system prompt."
    )


async def _call_llm_with_retry(
    client: AsyncOpenAI,
    model: str,
    user_message: str,
    max_retries: int,
    fact_base: FactBase,
    meeting_purpose: str,
) -> BriefingReport:
    """Call the LLM and enforce BriefingReport schema with retries.

    On validation failure, feed the error back to the model and retry.
    On network or auth errors, retry up to max_retries then raise.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    # Generate headers for Bifrost caching
    bifrost_headers = build_bifrost_headers(
        fact_base.company_id,
        meeting_purpose,
        [item.content for item in fact_base.hard_facts],
    )
    logger.info("Bifrost headers | %s", bifrost_headers)

    @retry(
        stop=stop_after_attempt(max_retries),
        wait=wait_random_exponential(multiplier=1, max=16),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def _attempt() -> BriefingReport:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
            extra_headers=bifrost_headers,
        )
        content = response.choices[0].message.content or ""
        if not content:
            raise ValueError("LLM returned empty content")

        return BriefingReport.model_validate_json(content)

    try:
        report = await _attempt()
    except ValidationError as exc:
        # A schema miss is a model mistake, not a transport failure, so it is
        # handled separately: the error is fed back into the conversation to
        # give the next attempt a chance to self-correct.
        logger.warning("LLM schema validation failed | error=%s", exc)
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
        raise BriefingGenerationError(
            f"LLM output did not match the BriefingReport schema: {exc}"
        ) from exc
    except Exception as exc:
        if is_permanent_error(exc):
            logger.error("LLM call rejected and cannot be retried | error=%s", exc)
            raise BriefingGenerationError(
                f"LLM request was rejected and cannot be retried: {exc}"
            ) from exc

        logger.error("LLM call failed after retries | error=%s", exc)
        raise BriefingGenerationError(
            f"LLM failed to produce a valid BriefingReport: {exc}"
        ) from exc

    logger.info(
        "LLM call succeeded | confidence=%s | "
        "news=%s hires=%s launches=%s open_questions=%s risks=%s",
        report.executive_summary.confidence,
        len(report.news.items),
        len(report.hires.items),
        len(report.launches.items),
        len(report.open_questions.items),
        len(report.risks.items),
    )
    return report


async def generate_briefing_report(
    fact_base: FactBase,
    meeting_purpose: str,
    max_retries: int = 3,
) -> BriefingReport:
    """Generate a validated BriefingReport from a FactBase.

    Raises BriefingGenerationError if the LLM fails after max_retries.
    """
    logger.info(
        "Analysis started | company_id=%s | purpose=%r | mode=%s | hard_facts=%s | "
        "analyst_opinions=%s | conflicts=%s",
        fact_base.company_id,
        meeting_purpose,
        "negotiation" if meeting_purpose.strip() else "fundamentals",
        len(fact_base.hard_facts),
        len(fact_base.analyst_opinions),
        len(fact_base.conflicts),
    )
    client, model = build_client()
    user_message = _build_user_message(fact_base, meeting_purpose)
    report = await _call_llm_with_retry(
        client,
        model,
        user_message,
        max_retries,
        fact_base,
        meeting_purpose,
    )
    logger.info(
        "Analysis complete | company_id=%s | recommendation_chars=%s",
        fact_base.company_id,
        len(report.executive_summary.recommendation),
    )
    return report
