# /backend/app/services/analysis.py
from __future__ import annotations

from typing import Optional

from openai import AsyncOpenAI
from pydantic import ValidationError

from app.models.briefing import (
    AuditRemoval,
    BriefingReport,
    ExecutiveSummary,
    LooseClaim,
)
from app.services.bifrost_headers import build_bifrost_headers
from app.services.llm_client import build_client, is_permanent_error
from app.services.logger_service import get_logger
from app.services.report_audit import audit_report
from app.services.report_order import order_news
from app.services.report_stages import (
    ANALYSIS_PROMPT,
    FINDINGS_PROMPT,
    RECOMMENDATION_PROMPT,
    AnalysisStage,
    FindingsStage,
    RecommendationStage,
    render_analysis_input,
    render_findings_input,
    render_previous_reading,
    render_recommendation_input,
)

from .ingestion import FactBase

logger = get_logger("analysis")


class BriefingGenerationError(Exception):
    """Raised when the LLM fails to produce a valid BriefingReport after retries."""


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


async def _call_stage_with_retry(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    user_message: str,
    stage_model,
    max_retries: int,
    *,
    stage: str,
    extra_headers: dict | None = None,
):
    """Call one stage and validate its shape, feeding failures back on retry.

    Each stage has its own schema, so a malformed reply is corrected against the
    schema it was supposed to satisfy rather than against the whole report. The
    error is fed back into the conversation: a plain retry would resend the same
    prompt and repeat the same mistake.

    ``extra_headers`` carries the gateway's cache key and TTL. They were computed
    for every request but only ever attached on the dead single-stage path, so a
    deployment behind Bifrost never cached anything: the gateway saw no
    `x-bf-cache-key` and bypassed its cache by design.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    last_error: Exception = RuntimeError("no attempt executed")

    for attempt in range(1, max_retries + 1):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.2,
                extra_headers=extra_headers or {},
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                raise ValueError("model returned empty content")
        except Exception as exc:
            if is_permanent_error(exc):
                raise BriefingGenerationError(
                    f"[{stage}] request rejected and cannot be retried: {exc}"
                ) from exc
            last_error = exc
            logger.warning("[%s] transport failed on attempt %s/%s | %s",
                           stage, attempt, max_retries, exc)
            if attempt == max_retries:
                break
            continue

        try:
            parsed = stage_model.model_validate_json(_json_payload(content))
        except (ValidationError, ValueError) as exc:
            last_error = exc
            logger.warning("[%s] output rejected on attempt %s/%s | %s",
                           stage, attempt, max_retries, exc)
            if attempt == max_retries:
                break
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"That response could not be parsed: {exc}. Reply with ONLY "
                        "one JSON object matching the schema. No text before or "
                        "after the object, no markdown fences, no commentary."
                    ),
                }
            )
            continue

        logger.info("[%s] stage succeeded on attempt %s", stage, attempt)
        return parsed

    raise BriefingGenerationError(
        f"[{stage}] output did not match its schema after {max_retries} attempts: "
        f"{last_error}"
    ) from last_error


async def generate_briefing_report(
    fact_base: FactBase,
    meeting_purpose: str,
    max_retries: int = 3,
    previous: Optional[BriefingReport] = None,
    *,
    company: str = "",
    description: str = "",
    own_profile: Optional[str] = None,
    own_company_block: str = "",
) -> BriefingReport:
    """Generate a validated BriefingReport, in three stages.

    Stage 1 extracts findings and the audit runs on them. Stage 2 assesses the
    surviving findings. Stage 3 writes the recommendation from the assessment.

    ``previous`` is the earlier brief for the same company, passed as a prior
    reading rather than evidence, so a second brief cannot be anchored to the
    first one's conclusion.

    ``company`` and ``description`` are what the audit checks claims against.

    ``own_profile`` describes the caller's own side. It reaches the recommendation
    stage and nothing earlier: what we are is legitimate input to what to ask for,
    and illegitimate input to what the evidence says.
    """
    logger.info(
        "Analysis started | company_id=%s | purpose=%r | mode=%s | hard_facts=%s | "
        "analyst_opinions=%s | conflicts=%s | revision=%s | own_profile=%s | "
        "own_market=%s",
        fact_base.company_id,
        meeting_purpose,
        "negotiation" if meeting_purpose.strip() else "fundamentals",
        len(fact_base.hard_facts),
        len(fact_base.analyst_opinions),
        len(fact_base.conflicts),
        previous is not None,
        bool(own_profile and own_profile.strip()),
        bool(own_company_block and own_company_block.strip()),
    )
    client, model = build_client()

    # The gateway's cache partition and TTL for this evidence set. Computed per
    # stage call so a request carries them wherever Bifrost sits in front of the
    # model; without this the gateway bypasses its own cache.
    gateway_headers = build_bifrost_headers(
        fact_base.company_id,
        meeting_purpose,
        [item.content for item in fact_base.hard_facts],
    )
    logger.info("Gateway cache headers | %s", gateway_headers)

    # ---- stage 1: findings -------------------------------------------------
    # A later brief for the same company extends the earlier one instead of
    # starting from nothing. The earlier findings reached this stage and no
    # other: stages 2 and 3 write a fresh judgement, so passing the old one
    # further would anchor the new reading to it.
    previous_block = render_previous_reading(previous)
    findings_input = render_findings_input(
        fact_base, meeting_purpose, company, previous_block=previous_block
    )
    findings = await _call_stage_with_retry(
        client, model, FINDINGS_PROMPT, findings_input, FindingsStage, max_retries,
        stage="findings", extra_headers=gateway_headers,
    )

    report = BriefingReport(
        news=findings.news,
        hires=findings.hires,
        changes_since_previous=findings.changes_since_previous,
        executive_summary=ExecutiveSummary(
            summary="", recommendation="", confidence=""
        ),
    )

    # ---- audit, on the findings alone -------------------------------------
    # The assessment is not written yet, so a claim that fails verification
    # cannot have influenced it.
    audited, outcome = await audit_report(
        report,
        fact_base,
        company=company or fact_base.company_id,
        description=description,
    )
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
                        source_url=entry.get("source_url", ""),
                    )
                    for entry in outcome.removed
                ]
            }
        )

    # ---- stage 2: analysis, reading only the surviving findings ----------
    quote_text = fact_base.quote.content if fact_base.quote is not None else None
    analysis_input = render_analysis_input(
        audited.news.items,
        audited.hires.items,
        quote_text,
        company,
        fact_base.coverage.level,
        fact_base.coverage.lanes_missing,
        own_company_block,
    )
    analysis_out = await _call_stage_with_retry(
        client, model, ANALYSIS_PROMPT, analysis_input, AnalysisStage, max_retries,
        stage="analysis", extra_headers=gateway_headers,
    )
    audited = audited.model_copy(update={"analysis": analysis_out.analysis})

    # ---- stage 3: recommendation, reasoning over the assessment ----------
    recommendation_input = render_recommendation_input(
        analysis_out.analysis,
        meeting_purpose,
        company,
        own_profile or "",
        fact_base.coverage.level,
    )
    summary_out = await _call_stage_with_retry(
        client, model, RECOMMENDATION_PROMPT, recommendation_input,
        RecommendationStage, max_retries, stage="recommendation",
        extra_headers=gateway_headers,
    )
    audited = audited.model_copy(update={"executive_summary": summary_out.executive_summary})

    # A first brief has nothing to differ from. The comparison is structurally
    # absent, so enforce it here rather than trusting a reply that might announce
    # a change against a brief that never existed.
    if previous is None and audited.changes_since_previous:
        logger.info(
            "Discarded changes on a first brief | claimed=%s",
            len(audited.changes_since_previous),
        )
        audited = audited.model_copy(update={"changes_since_previous": []})
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

    # Promises and the quote travel beside the brief, never inside the reasoning
    # that produced it. The model's evidence excluded them at ingestion, so this
    # only assembles what the reader should see.
    forward = [
        LooseClaim(claim=item.content, source_url=item.source_url)
        for item in fact_base.forward_looking
    ]
    quote = (
        LooseClaim(
            claim=fact_base.quote.content,
            source_url=fact_base.quote.source_url,
        )
        if fact_base.quote is not None
        else None
    )
    if forward or quote is not None:
        audited = audited.model_copy(
            update={"forward_looking": forward, "quote": quote}
        )

    logger.info(
        "Analysis complete | company_id=%s | recommendation_chars=%s | changes=%s "
        "| claims_removed=%s | possible_facts=%s | forward_looking=%s | quote=%s",
        fact_base.company_id,
        len(audited.executive_summary.recommendation),
        len(audited.changes_since_previous),
        outcome.total_removed,
        len(audited.possible_facts),
        len(audited.forward_looking),
        audited.quote is not None,
    )
    return audited
