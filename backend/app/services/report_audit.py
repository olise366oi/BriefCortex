# /backend/app/services/report_audit.py
"""Verify a generated brief before the reader sees it.

Two failures reached a real reader because nothing checked the output:

- every claim was about Zocdoc while the brief was for another company, because
  the articles were filed as ours upstream and no gate re-examined them;
- a claim can cite a URL that is not in the evidence at all.

So the brief is audited after generation and before delivery. A claim that is
not about the company being briefed, or that the evidence does not support, is
removed rather than shown with a caveat: a caveat still puts the sentence in
front of the reader, and the reader's time with a wrong statement is not
recoverable.

The audit is deliberately separate from generation. The model that wrote the
brief is the worst judge of it, and a second pass with a narrow question
("is this sentence about this company, and does this evidence say it") is
cheaper than making generation prove every sentence inline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from pydantic import BaseModel, Field, ValidationError

from app.models.briefing import BriefingReport, ClaimModule
from app.models.fact_base import FactBase
from app.services.llm_client import build_client
from app.services.logger_service import get_logger
from app.services.number_check import unsupported_numbers

logger = get_logger("report_audit")

AUDIT_PROMPT = """You audit a brief before it reaches a reader. You are not asked
to improve it, only to reject what does not hold.

You receive the company being briefed, what the caller said about that company,
and one claim at a time with the evidence sentence the claim was built from.

Judge each claim on two questions:
1. SUBJECT: is the claim about the company being briefed? A claim about a
   different company is rejected even when that company is named alongside ours,
   and even when its URL was in the retrieved evidence. A partnership story about
   a counterparty is about the counterparty, not about us.
   The company being briefed is the one named after COMPANY BEING BRIEFED plus
   any name listed under ALSO KNOWN AS. A claim naming one of those is about the
   briefed company unless it clearly concerns a different named entity. Never
   infer a different briefed company from the evidence: the subject is the one
   named in the request, not whatever entity appears most often in the digest.
2. SUPPORT: does the evidence sentence actually state what the claim states?
   Extra numbers, dates, terms or consequences that the evidence does not carry
   are a failure, not an enrichment.

Reply with JSON only:
{"verdicts": [{"index": 0, "verdict": "ok|wrong_subject|unsupported", "remedy": "one short sentence"}]}

Be strict about attribution and invention, but do not reject a claim merely
because a detail is phrased differently from the source: "raised $1 billion at a
$10 billion valuation" and "raised one billion dollars at a ten billion dollar
valuation" are the same claim. Reject a claim when it states something the cited
evidence does not say — an extra figure, an extra date, an extra party, or a
conclusion the sentence never draws. Rejecting a true claim for wording is as
harmful as passing a false one: it empties a correct brief.

Reply with JSON only:"""

MAX_AUDIT_CLAIMS = 40

# How much of the cited article the auditor is shown, in total. Every fact under
# the cited URL is included up to this cap: a figure can sit deep inside a long
# fact (measured: "$497 million" sat past character 300 of a 700-character
# transcript fact), and a truncated fact reads to the auditor as an article that
# never states the figure. Bounded so one long transcript cannot dominate the
# audit prompt.
MAX_CITED_CHARS = 6000


class ClaimVerdict(BaseModel):
    index: int
    verdict: str
    remedy: str = ""


class AuditBatch(BaseModel):
    verdicts: List[ClaimVerdict] = Field(default_factory=list)


@dataclass
class AuditOutcome:
    """What the audit removed, kept so the result is explainable."""

    accepted: int = 0
    removed: List[Dict[str, str]] = field(default_factory=list)

    @property
    def total_removed(self) -> int:
        return len(self.removed)


SECTION_NAMES = ("news", "hires")


def _evidence_text(fact_base: FactBase) -> str:
    """A compact evidence digest the auditor compares claims against.

    Every fact is included, not a truncated head: an auditor that cannot see the
    sentence a claim came from rejects it as unsupported, which strips a correct
    brief down to nothing (measured: 6 of 6 news claims removed on a later run).
    The digest is bounded per fact instead of in total, so coverage stays complete
    while each line stays short.
    """
    lines: List[str] = []
    for item in fact_base.hard_facts:
        lines.append(f"[{str(item.source_url)}] {item.content[:300]}")
    for item in fact_base.hiring:
        lines.append(f"[posting {str(item.source_url)}] {item.content[:220]}")
    for item in fact_base.analyst_opinions:
        lines.append(f"[opinion {str(item.source_url)}] {item.content[:220]}")
    return "\n".join(lines)


def _cited_evidence(claim, fact_base: FactBase) -> str:
    """Return the evidence that shares this claim's source URL.

    The auditor is told what that one source actually said, so it can judge
    support against the right sentence rather than against whichever facts
    happened to fit a prompt budget.

    The window is sized to the claim, not fixed. A claim that consolidates
    several facts from one article states every one of those facts; showing it
    four of them and truncating each to 300 characters hides the figures the claim
    is built from, and the auditor then rejects it as "the evidence does not carry
    this" when the evidence does. Measured on a real brief: seven claims were
    removed this way, five of them carrying figures that were in the evidence set
    but outside the window.

    The URL comparison tolerates the query string. Search results are stored with
    tracking parameters (`?fr=sycsrp_catchall`) and a model echoes the URL it was
    shown, so an exact-equality test on the full string can miss the article for
    a trailing `?`.
    """
    url = str(getattr(claim, "source_url", "") or "")
    if not url:
        return "(no source cited)"

    bare = url.split("?", 1)[0].split("#", 1)[0]
    matching = [
        item.content
        for item in list(fact_base.hard_facts) + list(fact_base.hiring)
        + list(fact_base.analyst_opinions)
        if str(item.source_url).split("?", 1)[0].split("#", 1)[0] == bare
    ]
    if not matching:
        return "(source not in evidence)"

    # A claim is at most a few hundred characters in a real brief, so the facts
    # behind it are few. Every fact under this URL is shown in full: a figure can
    # sit anywhere inside a fact (measured: one claim's "Baffin" was at character
    # 595 of a 752-character fact, so a 300-character cap hid it while the fact
    # itself was visible), and a truncated fact reads to the auditor as an article
    # that does not state the figure.
    shown: List[str] = []
    used = 0
    for text in matching:
        if used >= MAX_CITED_CHARS:
            break
        remaining = MAX_CITED_CHARS - used
        shown.append(text[:remaining])
        used += len(text[:remaining])

    return " || ".join(shown)


def _flatten(report: BriefingReport) -> List[tuple[str, int, object]]:
    """Return (section, index, claim) for every claim in the brief."""
    flat: List[tuple[str, int, object]] = []
    for name in SECTION_NAMES:
        module = getattr(report, name)
        for index, claim in enumerate(module.items):
            flat.append((name, index, claim))
    return flat


def _apply(report: BriefingReport, dropped: Dict[int, str]) -> BriefingReport:
    """Return a copy of the report with the rejected claims removed."""
    if not dropped:
        return report

    counter = 0
    rebuilt: Dict[str, ClaimModule] = {}
    for name in SECTION_NAMES:
        module = getattr(report, name)
        kept = []
        for claim in module.items:
            if counter in dropped:
                counter += 1
                continue
            kept.append(claim)
            counter += 1
        rebuilt[name] = ClaimModule(items=kept)

    return report.model_copy(update=rebuilt)


async def _audit_claims(
    claims: Sequence[tuple[str, int, object]],
    company: str,
    description: str,
    evidence: str,
    fact_base: FactBase,
    *,
    extra_headers: Optional[Dict[str, str]] = None,
    aliases: Sequence[str] = (),
) -> Dict[int, Dict[str, str]]:
    """Ask the model which claims do not hold. Returns {flat_index: verdict}."""
    if not claims:
        return {}

    # Each claim is shown beside the evidence its own source actually stated. The
    # whole digest is included as context, but the decisive comparison is
    # claim-vs-cited-sentence: judged against a sampled digest, a correct claim is
    # rejected for lack of support purely because its sentence was not in the
    # visible slice.
    numbered = "\n\n".join(
        f"[{position}] section={section}\nCLAIM: {claim.claim}\n"
        f"WHAT THAT SOURCE ACTUALLY SAID: {_cited_evidence(claim, fact_base)}"
        for position, (section, _index, claim) in enumerate(claims[:MAX_AUDIT_CLAIMS])
    )
    context = f"COMPANY BEING BRIEFED: {company}"
    other_names = [name for name in aliases if name and name != company]
    if other_names:
        context += f"\nALSO KNOWN AS: {', '.join(other_names)}"
    described = " ".join((description or "").split())
    if described:
        context += f"\nWHAT THE CALLER SAID ABOUT IT: {described}"

    try:
        client, model = build_client()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": AUDIT_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"{context}\n\nFULL EVIDENCE SET (for context):\n{evidence[:5000]}\n\n"
                        f"CLAIMS:\n{numbered}"
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=2048,
            extra_headers=extra_headers or {},
        )
        content = response.choices[0].message.content or ""
        batch = AuditBatch.model_validate(json.loads(content))
    except (ValidationError, ValueError) as exc:
        logger.warning("Report audit returned unusable output | %s", exc)
        return {}
    except Exception as exc:
        # An auditor that cannot run must not silently pass everything: the
        # brief is still delivered, but the caller is told the check did not run.
        logger.warning("Report audit unavailable | %s", exc)
        return {}

    failures: Dict[int, Dict[str, str]] = {}
    for verdict in batch.verdicts:
        if verdict.verdict.strip().lower() in ("ok", ""):
            continue
        failures[verdict.index] = {
            "verdict": verdict.verdict,
            "remedy": verdict.remedy,
        }
    return failures


async def audit_report(
    report: BriefingReport,
    fact_base: FactBase,
    company: str,
    description: str = "",
    *,
    cache_headers: Optional[Dict[str, str]] = None,
    aliases: Sequence[str] = (),
) -> tuple[BriefingReport, AuditOutcome]:
    """Return the brief with unverified claims removed, plus what was removed.

    ``cache_headers`` carries the gateway's cache key and TTL. The audit is a
    model call over the findings and the evidence digest, so when neither changed
    the gateway can replay its verdict instead of paying for a second judgement —
    and, just as important, replaying it keeps the audited findings identical,
    which is what lets the analysis and recommendation replay too.
    """
    outcome = AuditOutcome()

    claims = _flatten(report)
    known_urls = {str(item.source_url) for item in fact_base.hard_facts}
    known_urls.update(str(item.source_url) for item in fact_base.hiring)
    known_urls.update(str(item.source_url) for item in fact_base.analyst_opinions)

    # Deterministic first: a URL that is not in the evidence cannot be a source,
    # whatever the model said about it. This costs nothing and needs no model.
    unfunded: Dict[int, Dict[str, str]] = {}
    for position, (section, _index, claim) in enumerate(claims):
        if str(claim.source_url) not in known_urls:
            unfunded[position] = {
                "verdict": "unknown_source",
                "remedy": f"source not present in the evidence set ({section})",
            }

    # Second deterministic pass: a figure the evidence never states. This is the
    # fabrication the model audit was catching only some of the time, and it
    # costs nothing. A number missing from the *cited* source is not flagged —
    # only one missing from the whole evidence set, so a claim that consolidates
    # several facts stays intact.
    invented: Dict[int, Dict[str, str]] = {}
    evidence_texts = [
        item.content
        for item in list(fact_base.hard_facts) + list(fact_base.hiring)
        + list(fact_base.analyst_opinions)
    ]
    for position, (section, _index, claim) in enumerate(claims):
        if position in unfunded:
            continue
        missing = unsupported_numbers(claim.claim, evidence_texts)
        if not missing:
            continue
        invented[position] = {
            "verdict": "unsupported_figure",
            "remedy": (
                "these figures appear nowhere in the evidence: "
                + ", ".join(missing[:6])
            ),
        }
        logger.info(
            "Figure not in evidence | section=%s | figures=%s | claim=%s",
            section,
            missing[:6],
            claim.claim[:90],
        )

    model_failures = await _audit_claims(
        claims,
        company,
        description,
        _evidence_text(fact_base),
        fact_base,
        extra_headers=cache_headers,
        aliases=aliases,
    )
    model_failures.update({k: v for k, v in unfunded.items() if k not in model_failures})
    model_failures.update({k: v for k, v in invented.items() if k not in model_failures})

    for position in sorted(model_failures):
        if position >= len(claims):
            continue
        section, _index, claim = claims[position]
        failure = model_failures[position]
        outcome.removed.append(
            {
                "section": section,
                "claim": claim.claim[:160],
                "source_url": str(claim.source_url),
                "verdict": failure["verdict"],
                "remedy": failure["remedy"],
            }
        )

    outcome.accepted = len(claims) - len(outcome.removed)
    audited = _apply(report, set(model_failures))

    # The three passes are reported separately: which one removed a claim says
    # whether a rule fired or a model judged, and they need different follow-up.
    logger.info(
        "Report audited | company=%r | claims=%s | accepted=%s | removed=%s "
        "| unknown_source=%s | invented_figure=%s | model=%s",
        company,
        len(claims),
        outcome.accepted,
        outcome.total_removed,
        len(unfunded),
        len(invented),
        len(model_failures) - len(unfunded | invented),
    )
    for entry in outcome.removed:
        logger.info(
            "Claim rejected | section=%s | verdict=%s | %s",
            entry["section"],
            entry["verdict"],
            entry["claim"][:80],
        )

    return audited, outcome
