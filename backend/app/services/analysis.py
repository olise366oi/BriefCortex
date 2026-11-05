# /backend/app/services/analysis.py
from __future__ import annotations

from typing import Tuple

from typing import Tuple

from pydantic import ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_random_exponential

from app.core.config import get_settings
from app.models.briefing import AuditRemoval, BriefingReport, LooseClaim
from app.services.bifrost_headers import build_bifrost_headers
from app.services.llm_client import (
    LLMUnavailableError,
    build_client,
    is_permanent_error,
    is_retryable_error,
)
from app.services.logger_service import get_logger
from app.services.report_audit import audit_report
from app.services.report_order import order_news

from .ingestion import FactBase

logger = get_logger("analysis")


class BriefingGenerationError(Exception):
    """Raised when the LLM fails to produce a valid BriefingReport after retries."""


SYSTEM_PROMPT = """You are a ruthless, highly aggressive Wall Street M&A and negotiation strategist. You brief the deal team before a high-stakes commercial meeting. No pleasantries, no hedging, no generic consulting language. Turn the meeting purpose and available evidence into cold, specific negotiation leverage and concrete asks.

INPUTS (exactly these five blocks, no others):
<MEETING_PURPOSE> the commercial objective of the meeting. It may be "(none supplied)".
<HARD_FACTS> verified factual evidence. This is the ONLY source of negotiation leverage.
<ANALYST_OPINIONS> unverified analyst commentary and rumors. Treat as leads to verify, never as facts.
<HIRING> current job postings at the company. These signal intent, not outcome: read them to infer where people and budget are moving, never quote them as a reported fact.
<COVERAGE> how much evidence was actually retrieved, and which lanes came back empty. This is a statement about our coverage, not about the company.
The output fields news/hires/analysis must be produced by classifying and summarizing ANALYST_OPINIONS, HARD_FACTS and HIRING. Do not assume any input block named NEWS, HIRES, ANALYSIS or RISKS exists.

COVERAGE HONESTY (applies in every mode):
- "No reporting found" is not the same as "no problems found". Never let a thin or empty evidence set read as reassurance.
- When COVERAGE level is "none" or "thin", executive_summary.confidence must say plainly that coverage was insufficient and name what could not be checked.
- When COVERAGE can_support_numeric_leverage is false, do not emit numeric asks even if a purpose was supplied: state that the evidence cannot price the demand and say what is missing.
- Never describe an unchecked area as clean. Say "not checked" instead of implying it was verified.

TWO SECTIONS ONLY — do not invent a third:
- news: every reported development about the company. Launches, partnerships,
  funding, executive changes, regulatory actions and deterioration all belong
  here. There is no separate "launches", "open questions" or "risks" module: the
  reader decides what a development means from the development itself, and a
  module named "risks" was pre- judging that before the evidence had been weighed.
- hires: job postings, which are intent rather than outcome.
Everything else the brief needs to say belongs in analysis or executive_summary.

DATING AND ORDER — news is read as a timeline:
- Every news claim must carry "date": the day the report was published, as
  YYYY-MM-DD, taken from the evidence. Use "" only when the evidence carries no
  date at all; never guess one.
- When several reports cover the same event over time, merge them into ONE claim
  that states how the event developed, using the latest date. Two outlets
  describing one announcement is one event, not two findings.
- Order news by priority, then by date descending within each priority. Set
  "key_event": true for the developments a reader preparing for this meeting
  actually needs: filed results, guidance, a regulatory action, a funding round,
  a named partnership, a departure at the top. A key event from three weeks ago
  leads; routine commentary from yesterday follows it.
- Report at most 8 news claims. Fewer, well-ordered, beats a longer list.

OUTPUT STYLE:
- One pass, not a catalogue. The brief is a single coherent read of the company, so do not manufacture one observation per input item and do not restate the evidence item by item.
- Keep each module short. Fewer is better than padded.
- executive_summary.summary is one tight paragraph that says what matters and why. Never a list of findings.
- Length discipline: a reader skimming before a meeting must get the whole picture in one screen. If you can cut a sentence without losing meaning, cut it.

READING ORDER — the reader encounters the brief in this order, so write each
part for the place it appears:
1. news: what the company has actually done lately, in priority then date order.
2. hires: what the postings say about where people and budget are going.
3. analysis: what the news and postings amount to, and where the risk sits. This
   is the assessment, and it must be derived from the items above.
4. the recommendation: the executive_summary, which is read last. It must follow
   from the assessment rather than introduce a judgement that assessment does not
   support.
A brief that leads with a conclusion the facts do not carry is a failure, even if
the conclusion turns out to be right.

ATTRIBUTION — never let one company's reporting become another's finding:
- A claim whose subject is a different company must not appear as this company's
  news or hire. A partnership announcement by the counterparty is the
  counterparty's action.
- If a sentence about our company appears inside an article whose subject is
  another company, it is not a confirmed finding. Do not write it as one.
- Do not present a disputed or unresolved attribution as a settled fact. Say the
  attribution is unresolved and name what would settle it.

PLACEHOLDERS ARE FORBIDDEN: no "(none declared)", no "N/A", no empty section
padding. Emit only what you can support, and omit the rest entirely.

TWO MODES — read the MODE line in the user message and follow exactly one:

MODE negotiation (a real <MEETING_PURPOSE> is supplied):
- Follow CORE BEHAVIOR and ANTI-HALLUCINATION BOTTOM LINE below.

MODE fundamentals (the purpose block says "(none supplied)"):
- There is no deal to negotiate. Do NOT invent an objective and do NOT emit numeric asks, price demands, payment-term demands or concessions.
- news = every reported development about the company, dated and ordered. hires = where people and budget are going. analysis = what it amounts to and where the risk sits.
- <HIRING> feeds the hires module and the analysis: a cluster of roles in one function is evidence of where effort is going.
- executive_summary.recommendation becomes one short directional read on the company, not a demand list. Two or three sentences: the business direction, what is demonstrably working, what deserves caution. Every statement must trace to <HARD_FACTS> or <HIRING> (or be marked [UNVERIFIED]).
- executive_summary.confidence must state what the evidence supports and what it does not.
- The ANTI-HALLUCINATION BOTTOM LINE fallback sentence does NOT apply in this mode; it applies only when a real objective exists but the evidence cannot support an aggressive ask.

CORE BEHAVIOR:
1. Align every recommendation to <MEETING_PURPOSE>.
2. Treat <HARD_FACTS> as source of truth. Use negative information — cash-flow pressure, layoffs, executive departures, supplier disputes, delivery misses, funding constraints, cost cuts, margin pressure, procurement leadership turnover — as negotiation leverage.
3. In executive_summary.recommendation, convert leverage into specific numeric asks separated by semicolons, e.g. 'Demand a 15% rate reduction; shorten payment terms from 6 months to 3 months; require a 12-month volume commitment with minimum monthly spend; demand a 10-business-day term sheet.' Every ask must carry a number, deadline, or trade.
4. Be cold and direct. No praise, no "we should consider". State what to ask for, why, and what to trade.
5. Separate fact from inference. Every claim must trace to an input. Unverified claims must append "[UNVERIFIED]" to the claim text. Do not emit any verification flag. Do not assert verification anywhere in the output.
6. Never invent a source. Every emitted claim and hire MUST carry a real source_url drawn from the input. Never fabricate a URL, publication, date, or number that is not present in the input. If an unverified lead has no real URL, do NOT emit it as a standalone entry and do NOT fold it into executive_summary.recommendation (which is reserved for the fixed fallback sentence). Fold it ONLY into executive_summary.summary text and append "[UNVERIFIED]". If executive_summary.summary would otherwise be empty, write a one-line summary that contains the unverified lead with "[UNVERIFIED]". Any source_url you emit MUST be a full URL including scheme (http:// or https://), MUST NOT be empty, MUST NOT be null, and MUST NOT be a placeholder such as "N/A", "unknown", "TBD", or a bare domain without scheme.
7. Do not defame individuals. For executive departures/personal rumors, use "alleged", "rumored", "unconfirmed"; recommend using only as timing pressure, never as public claim.
8. <HIRING> is intent, not fact. A posting tells you where the company is putting people; it does not prove a strategy has been executed. Never quote a job title as a reported event in news, and never build a numeric ask on a posting alone.

ANTI-HALLUCINATION BOTTOM LINE:
If <HARD_FACTS> contains no negative information supporting aggressive leverage, you MUST NOT fabricate pressure, layoffs, cash-flow problems, executive departures, or supplier disputes. In that case:
- executive_summary.recommendation must be exactly: "Evidence insufficient; recommend standard outreach."
- That recommendation must contain no numbers, no deadlines, no price demands, no payment-term demands, and no concession requests.
Do not use unverified analyst commentary to manufacture a hard negotiation lever inside executive_summary.recommendation.

OUTPUT: valid JSON only, no markdown fences, no prose outside JSON. Use ONLY the field names listed in the OUTPUT schema defined below in this prompt. Do NOT emit any field name that is not in that schema. Put all negotiation asks inside executive_summary.recommendation.

Required: executive_summary is mandatory and must always be present as a non-null object with all three fields. news and hires are optional and may be omitted or emitted as {"items": []}. analysis may be omitted when there is nothing to assess, but a brief with facts and no assessment is a brief with no reason for its recommendation.

Schema:
{
  "news": {"items": [{"claim": str, "source_url": str, "date": str, "key_event": bool}]},
  "hires": {"items": [{"claim": str, "source_url": str, "date": str, "key_event": bool}]},
  "analysis": {"assessment": str, "risks": [str]},
  "executive_summary": {"summary": str, "recommendation": str, "confidence": str},
  "changes_since_previous": [{"change": str, "why": str}]
}

REVISION MODE — when the user message carries a <PREVIOUS_FINDINGS> block:

The previous brief's conclusions are NOT evidence and must not be reused as
evidence. Read the evidence afresh and form your own reading first, exactly as a
first brief would. Only after that reading exists, compare it with the previous
one.

Then:
1. Write the brief from the evidence as you now read it. Do not carry a previous
   judgement forward because it was stated; carry it forward only if the evidence
   still supports it.
2. Populate changes_since_previous with every place your reading differs from the
   previous one: a direction that reversed, a judgement that strengthened,
   weakened, or was withdrawn. If nothing material changed, return an empty list
   rather than inventing a change. Do not list additions of new facts as
   "changes"; a change is a difference in the reading, not new material.
3. Each "why" must name the evidence that caused the change — the newer facts,
   the corrected attribution, the conflict that surfaced — so the reader can
   judge whether the shift is justified.
4. Keep the previous conclusion out of the new brief except inside
   changes_since_previous, where it is referenced only to state the difference.

FIRST BRIEF — when the user message carries no <PREVIOUS_FINDINGS> block:

There is nothing to differ from, so changes_since_previous must be omitted
entirely. Do not emit it as an empty list and never write a placeholder into it:
a first brief that announces "no changes" is describing a comparison that never
happened.
"""


def _date_of(item) -> str:
    """Return the ISO date of an evidence item, or an empty marker."""
    return item.publish_date.date().isoformat() if item.publish_date else "undated"


def _format_fact_base_for_prompt(fact_base: FactBase) -> str:
    lines = ["<HARD_FACTS>"]
    if not fact_base.hard_facts:
        lines.append("(none)")
    for i, item in enumerate(fact_base.hard_facts, 1):
        # The date is part of the evidence: news is delivered as a timeline, and
        # two reports on one event weeks apart are one story developing.
        lines.append(f"[H{i}] date={_date_of(item)} ({str(item.source_url)}) {item.content}")
    lines.append("</HARD_FACTS>")

    lines.append("<ANALYST_OPINIONS>")
    if not fact_base.analyst_opinions:
        lines.append("(none)")
    for i, item in enumerate(fact_base.analyst_opinions, 1):
        lines.append(f"[O{i}] date={_date_of(item)} ({str(item.source_url)}) {item.content}")
    lines.append("</ANALYST_OPINIONS>")

    lines.append("<HIRING>")
    if not fact_base.hiring:
        lines.append("(none)")
    for i, item in enumerate(fact_base.hiring, 1):
        lines.append(f"[J{i}] date={_date_of(item)} ({str(item.source_url)}) {item.content}")
    lines.append("</HIRING>")

    lines.append("<COVERAGE>")
    coverage = fact_base.coverage
    lines.append(f"level: {coverage.level}")
    lines.append(f"facts collected: {coverage.total_facts}")
    lines.append(f"distinct sources: {coverage.distinct_sources}")
    lines.append(f"can support numeric leverage: {coverage.can_support_leverage}")
    if coverage.lanes_missing:
        lines.append(f"lanes that returned nothing: {', '.join(coverage.lanes_missing)}")
    for note in coverage.notes:
        lines.append(f"note: {note}")
    lines.append("</COVERAGE>")

    return "\n".join(lines)


def _format_previous_reading(previous: Optional[BriefingReport]) -> str:
    """Render the previous brief as a block the model must not treat as evidence.

    Only the previous *facts* are carried over — the news and hiring items, so
    the new brief can extend them instead of silently dropping what the first
    run already found. The previous analysis and recommendation are deliberately
    withheld: those are the judgement this revision exists to re-form, and putting
    them in the prompt would anchor the new reading to the old one.
    """
    if previous is None:
        return ""

    lines = ["<PREVIOUS_FINDINGS>"]
    for label, module in (
        ("news", previous.news),
        ("hires", previous.hires),
    ):
        claims = [item.claim for item in module.items]
        if claims:
            lines.append(f"{label}: " + " | ".join(claims))
    lines.append(
        "These are what the EARLIER brief reported. They are not evidence, they "
        "have no source authority, and you must not cite them. They are here so "
        "this brief can include and extend them rather than lose them."
    )
    lines.append("</PREVIOUS_FINDINGS>")

    # The previous judgement is withheld from the reading block and passed only
    # to the comparison step, so a fresh assessment is formed first.
    if previous.analysis.assessment or previous.executive_summary.recommendation:
        lines.append("")
        lines.append("<PREVIOUS_JUDGEMENT_NOT_FOR_THIS_BRIEF>")
        if previous.analysis.assessment:
            lines.append(f"assessment: {previous.analysis.assessment[:1200]}")
        if previous.executive_summary.recommendation:
            lines.append(
                f"recommendation: {previous.executive_summary.recommendation[:1200]}"
            )
        lines.append(
            "Do NOT reuse this judgement. Form your own assessment and "
            "recommendation from the evidence. Consult this only afterwards, to "
            "state in changes_since_previous where your new reading differs."
        )
        lines.append("</PREVIOUS_JUDGEMENT_NOT_FOR_THIS_BRIEF>")

    return "\n".join(lines)


def _build_user_message(
    fact_base: FactBase,
    meeting_purpose: str,
    previous: Optional[BriefingReport] = None,
) -> str:
    fact_block = _format_fact_base_for_prompt(fact_base)
    purpose_text = meeting_purpose.strip()
    previous_block = _format_previous_reading(previous)

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

    parts = [mode_block, fact_block]
    if previous_block:
        parts.append(previous_block)
        parts.append(
            "REVISION: this is a later brief for the same company, and the evidence "
            "above already includes what the earlier brief was built on. Keep those "
            "findings, add the new ones, and state in changes_since_previous every "
            "place your own reading differs from <PREVIOUS_JUDGEMENT_NOT_FOR_THIS_BRIEF>."
        )
    parts.append(
        "Produce a BriefingReport JSON object following all rules in the system prompt."
    )
    return "\n\n".join(parts)


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
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=1, max=16),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def _transport_call() -> str:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
            extra_headers=bifrost_headers,
        )
        content = response.choices[0].message.content or ""
        if not content.strip():
            raise ValueError("LLM returned empty content")
        return content

    last_error: Exception = RuntimeError("no attempt executed")

    for attempt in range(1, max_retries + 1):
        try:
            content = await _transport_call()
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

        try:
            report = BriefingReport.model_validate_json(_json_payload(content))
        except (ValidationError, ValueError) as exc:
            # A schema miss is a model mistake, not a transport failure, so the
            # error is fed back into the conversation: a plain retry would send
            # the identical prompt again and repeat the identical mistake.
            last_error = exc
            logger.warning(
                "LLM output rejected on attempt %s/%s | error=%s",
                attempt,
                max_retries,
                exc,
            )
            if attempt == max_retries:
                break

            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "That response could not be parsed: "
                        f"{exc}. Reply with ONLY one JSON object matching the "
                        "schema. No text before or after the object, no markdown "
                        "fences, no commentary."
                    ),
                }
            )
            continue

        logger.info(
            "LLM call succeeded on attempt %s | confidence=%s | "
            "news=%s hires=%s risks=%s",
            attempt,
            report.executive_summary.confidence,
            len(report.news.items),
            len(report.hires.items),
            len(report.analysis.risks),
        )
        return report

    raise BriefingGenerationError(
        f"LLM output did not match the BriefingReport schema after "
        f"{max_retries} attempts: {last_error}"
    ) from last_error


def _json_payload(content: str) -> str:
    """Return the JSON object inside a model reply.

    The prompt asks for JSON only, but a reply sometimes carries a sentence
    after the object (measured: "... include what was not checked are clean.").
    Trimming to the outermost braces recovers the payload instead of discarding
    a response that is otherwise complete.
    """
    text = content.strip()
    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text

    trimmed = text[start : end + 1]
    logger.info(
        "Trimmed trailing text from model reply | removed=%s chars",
        len(text) - len(trimmed),
    )
    return trimmed


async def generate_briefing_report(
    fact_base: FactBase,
    meeting_purpose: str,
    max_retries: int = 3,
    previous: Optional[BriefingReport] = None,
    *,
    company: str = "",
    description: str = "",
) -> BriefingReport:
    """Generate a validated BriefingReport from a FactBase.

    ``previous`` is the earlier brief for the same company. It is passed as a
    prior reading, never as evidence: the model reads the fact base fresh and
    only afterwards states where it now disagrees, so a second brief cannot be
    anchored to the first one's conclusion.

    ``company`` and ``description`` are what the audit checks claims against.
    Without them the audit can only check that a cited URL exists in the evidence,
    which is necessary but not sufficient: every claim in a real failure case
    cited a URL that did exist and was still about the wrong company.

    Raises BriefingGenerationError if the LLM fails after max_retries.
    """
    logger.info(
        "Analysis started | company_id=%s | purpose=%r | mode=%s | hard_facts=%s | "
        "analyst_opinions=%s | conflicts=%s | revision=%s",
        fact_base.company_id,
        meeting_purpose,
        "negotiation" if meeting_purpose.strip() else "fundamentals",
        len(fact_base.hard_facts),
        len(fact_base.analyst_opinions),
        len(fact_base.conflicts),
        previous is not None,
    )
    client, model = build_client()
    user_message = _build_user_message(fact_base, meeting_purpose, previous)
    report = await _call_llm_with_retry(
        client,
        model,
        user_message,
        max_retries,
        fact_base,
        meeting_purpose,
    )

    # Audit before delivery. A claim that is about another company, or that the
    # evidence does not carry, is removed here: the reader never sees it, and
    # removed_claims records that it happened.
    audited, outcome = await audit_report(
        report,
        fact_base,
        company=company or fact_base.company_id,
        description=description,
    )

    # A first brief has nothing to differ from. The prompt says so, but the
    # comparison is structurally absent, so enforce it here rather than trusting
    # a reply that might announce a change against a brief that never existed.
    if previous is None and audited.changes_since_previous:
        logger.info(
            "Discarded changes on a first brief | claimed=%s",
            len(audited.changes_since_previous),
        )
        audited = audited.model_copy(update={"changes_since_previous": []})

    # Ordering is a rule, not a preference: key events lead, then newest first.
    # Applied after the audit so a removed claim cannot shift the reading order
    # around a gap.
    audited = order_news(audited)
    if outcome.removed:
        audited = audited.model_copy(
            update={
                "removed_claims": [
                    AuditRemoval(
                        section=entry["section"],
                        claim=entry["claim"],
                        verdict=entry["verdict"],
                        remedy=entry["remedy"],
                    )
                    for entry in outcome.removed
                ]
            }
        )

    # Facts mined from articles about other companies. They are leads, not
    # findings, so they travel beside the brief rather than inside it.
    if fact_base.incidental_facts:
        audited = audited.model_copy(
            update={
                "possible_facts": [
                    LooseClaim(
                        claim=item.content,
                        source_url=item.source_url,
                        article_subject=item.article_subject,
                    )
                    for item in fact_base.incidental_facts
                ]
            }
        )

    logger.info(
        "Analysis complete | company_id=%s | recommendation_chars=%s | changes=%s "
        "| claims_removed=%s | possible_facts=%s",
        fact_base.company_id,
        len(audited.executive_summary.recommendation),
        len(audited.changes_since_previous),
        outcome.total_removed,
        len(audited.possible_facts),
    )
    return audited
