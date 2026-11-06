# /backend/app/services/report_stages.py
"""Generate a brief in stages, each reading only what it is allowed to read.

A single call that invents the findings and the judgement together has no way to
keep them honest against each other: an unsupported finding quietly becomes the
basis of a recommendation. The work is therefore split into three stages with an
explicit contract between them.

    1. findings      read: evidence                      write: news, hires
       -- the audit runs here, on the findings alone --
    2. analysis      read: audited news, hires, quote    write: analysis
    3. recommendation read: analysis (and the caller's own company profile)
                     write: executive_summary

Two properties follow from this, and both are the point:

* **The analysis never sees evidence the audit removed.** A claim that was about
  the wrong company cannot reach the reasoning that produces the advice.
* **The recommendation never sees raw evidence or the caller's profile before an
  assessment exists.** It reasons over the assessment, so the advice is a
  conclusion drawn from a stated reading rather than a parallel guess. The
  caller's own company profile is readable here and nowhere earlier: what the
  caller's position is can shape what to ask for, but it must never shape what
  the evidence is said to contain.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from pydantic import BaseModel, Field

from app.models.briefing import Analysis, Claim, ClaimModule, ExecutiveSummary
from app.models.fact_base import FactBase
from app.services.logger_service import get_logger

logger = get_logger("report_stages")


# =============================================================================
# Prompts
# =============================================================================

FINDINGS_PROMPT = """You extract what a company has actually done. You are not
asked for judgement: another stage forms that, from what you produce.

You receive evidence about one company and output two lists.

news — every reported development about the company. Launches, partnerships,
funding, executive changes, regulatory actions and deterioration all belong here.
Rules:
- Every claim carries "date": the day the report was published, YYYY-MM-DD, taken
  from the evidence. Use "" only when the evidence carries no date; never guess.
- When several reports cover the same event over time, merge them into ONE claim
  that states how the event developed, using the latest date, and list the
  earliest date in "first_seen". Two outlets describing one announcement is one
  event, not two findings.
- "key_event": true for developments a reader preparing for a meeting needs:
  filed results, guidance, a regulatory action, a funding round, a named
  partnership, a departure at the top.
- At most 8 claims. Fewer, well-ordered, beats a longer list.

hires — job postings. They are intent, not outcome. Never restate a posting as a
reported event, and never build a number on one.

ATTRIBUTION — never let one company's reporting become another's finding:
- A claim whose subject is a different company does not belong here. A partnership
  announcement made by the counterparty is the counterparty's action.
- A sentence about our company inside an article about another company is not a
  confirmed finding.
- If the attribution is unresolved, say so in the claim and name what would settle
  it.

Never invent a source. Every claim carries a real source_url taken from the input,
including scheme. Never fabricate a URL, date or number.

Reply with JSON only:
{"news": {"items": [{"claim": str, "source_url": str, "date": str,
"first_seen": str, "key_event": bool}]},
 "hires": {"items": [{"claim": str, "source_url": str, "date": str}]}}"""


ANALYSIS_PROMPT = """You assess a company from what has already been established.

You receive the findings that survived verification, the job postings, and the
market quote if the company is listed. You may also receive a block describing
OUR OWN COMPANY's current market position. You do NOT receive raw articles, and
you must not introduce a fact that is not in the blocks you were given.

Produce an analysis:
- "assessment": what the findings add up to. The direction of the business, what
  is demonstrably working, what deserves caution. Derived from the items shown,
  not from general knowledge about the company.
- "risks": each a specific exposure with the finding behind it. Name the evidence
  in the sentence. Do not list a risk the findings do not support.

OUR OWN COMPANY, when supplied, is context for this deal, not a subject of the
brief:
- Use it to say how OUR position bears on this counterparty: a weak share price
  or negative market coverage on our side reduces our room to walk away, extend
  terms or hold out for volume; a strong one gives us room.
- It is our market position, not the counterparty's opinion of us. Do not write
  "they think we are..." — nobody in the evidence said that. Write what our
  position means for the negotiation.
- Never restate our company's facts as if they were the counterparty's facts.

Rules:
- Every statement must trace to a finding, posting, quote or our-company block.
  Mark anything else "[UNVERIFIED]".
- "No reporting found" is not "no problems found". When coverage is thin, say the
  coverage was insufficient and name what could not be checked.
- Never describe an unchecked area as clean.
- A posting is intent, not outcome. A quote is a market number, not a
  fundamental.
- Do not give advice. This stage states what the evidence amounts to; the next
  stage decides what to do about it.

Reply with JSON only:
{"analysis": {"assessment": str, "risks": [str]}}"""


RECOMMENDATION_PROMPT = """You write the recommendation from an assessment that
has already been made.

You receive the assessment and its risks, and optionally the caller's own company
profile. You do NOT receive the underlying articles: the recommendation must
follow from the assessment, not from a separate reading of the evidence.

Produce executive_summary:
- "summary": one tight paragraph on what matters and why, drawn from the
  assessment.
- "recommendation": what to do. In negotiation mode, specific asks with numbers,
  deadlines or trades. In fundamentals mode, a directional read: the business
  direction, what is working, what deserves caution. Never invent an objective
  when none was supplied.
- "confidence": what the assessment supports and what it does not, including
  which lanes returned nothing.

The caller's company profile, when supplied, describes the side we are briefing
for: their position, scale and constraints. Use it to shape what is worth asking
for and what is unrealistic. Never use it to restate the other company's facts,
and never let it override the assessment.

If the assessment contains no negative signal supporting leverage, do not
manufacture pressure. State that the evidence cannot support an ask and say what
is missing.

Reply with JSON only:
{"executive_summary": {"summary": str, "recommendation": str,
"confidence": str}}"""


# =============================================================================
# Shapes
# =============================================================================

class FindingsStage(BaseModel):
    news: ClaimModule = Field(default_factory=ClaimModule)
    hires: ClaimModule = Field(default_factory=ClaimModule)


class AnalysisStage(BaseModel):
    analysis: Analysis = Field(default_factory=Analysis)


class RecommendationStage(BaseModel):
    executive_summary: ExecutiveSummary


# =============================================================================
# Input rendering
# =============================================================================

def render_findings_input(
    fact_base: FactBase,
    meeting_purpose: str,
    company: str,
) -> str:
    """The evidence block for the findings stage."""
    lines: List[str] = []
    if company:
        lines.append(f"COMPANY BEING BRIEFED: {company}")

    purpose = meeting_purpose.strip()
    lines.append(
        f"MEETING PURPOSE: {purpose}" if purpose else "MEETING PURPOSE: (none supplied)"
    )
    lines.append("")

    lines.append("<HARD_FACTS>")
    if not fact_base.hard_facts:
        lines.append("(none)")
    for index, item in enumerate(fact_base.hard_facts, 1):
        date = item.publish_date.date().isoformat() if item.publish_date else "undated"
        lines.append(f"[H{index}] date={date} ({item.source_url}) {item.content}")
    lines.append("</HARD_FACTS>")

    lines.append("")
    lines.append("<HIRING>")
    if not fact_base.hiring:
        lines.append("(none)")
    for index, item in enumerate(fact_base.hiring, 1):
        lines.append(f"[J{index}] ({item.source_url}) {item.content}")
    lines.append("</HIRING>")

    lines.append("")
    lines.append("<COVERAGE>")
    coverage = fact_base.coverage
    lines.append(f"level: {coverage.level}")
    lines.append(f"facts collected: {coverage.total_facts}")
    lines.append(f"distinct sources: {coverage.distinct_sources}")
    if coverage.lanes_missing:
        lines.append(f"lanes that returned nothing: {', '.join(coverage.lanes_missing)}")
    lines.append("</COVERAGE>")

    return "\n".join(lines)


def render_analysis_input(
    news: Sequence[Claim],
    hires: Sequence[Claim],
    quote: Optional[str],
    company: str,
    coverage_level: str,
    lanes_missing: Sequence[str],
    own_company_block: str = "",
) -> str:
    """The verified findings the analysis stage is allowed to read.

    ``own_company_block`` describes the caller's own market position. It reaches
    this stage because "how much room do we have" is part of what the evidence
    amounts to, while the caller's internal documents stay out of it entirely.
    """
    lines: List[str] = []
    if company:
        lines.append(f"COMPANY BEING BRIEFED: {company}")

    lines.append("")
    lines.append("<VERIFIED_NEWS>")
    if not news:
        lines.append("(none)")
    for index, item in enumerate(news, 1):
        marker = " KEY" if item.key_event else ""
        span = f"{item.date}"
        if getattr(item, "first_seen", ""):
            span = f"{item.first_seen} .. {item.date}"
        lines.append(f"[N{index}]{marker} {span} ({item.source_url}) {item.claim}")
    lines.append("</VERIFIED_NEWS>")

    lines.append("")
    lines.append("<HIRING>")
    if not hires:
        lines.append("(none)")
    for index, item in enumerate(hires, 1):
        lines.append(f"[J{index}] ({item.source_url}) {item.claim}")
    lines.append("</HIRING>")

    if quote:
        lines.append("")
        lines.append("<MARKET>")
        lines.append(quote)
        lines.append("</MARKET>")

    if own_company_block.strip():
        lines.append("")
        lines.append("<OUR_COMPANY_MARKET_POSITION>")
        lines.append(own_company_block.strip())
        lines.append("</OUR_COMPANY_MARKET_POSITION>")

    lines.append("")
    lines.append("<COVERAGE>")
    lines.append(f"level: {coverage_level}")
    if lanes_missing:
        lines.append(f"lanes that returned nothing: {', '.join(lanes_missing)}")
    lines.append("</COVERAGE>")

    return "\n".join(lines)


def render_recommendation_input(
    analysis: Analysis,
    meeting_purpose: str,
    company: str,
    own_profile: str,
    coverage_level: str,
) -> str:
    """The assessment the recommendation stage reasons over."""
    purpose = meeting_purpose.strip()
    lines = [
        f"COMPANY BEING BRIEFED: {company}" if company else "",
        f"MEETING PURPOSE: {purpose}" if purpose else "MEETING PURPOSE: (none supplied)",
        "MODE: negotiation" if purpose else "MODE: fundamentals",
        "",
        "<ASSESSMENT>",
        analysis.assessment or "(none produced)",
        "</ASSESSMENT>",
        "",
        "<RISKS_FROM_ASSESSMENT>",
    ]
    if analysis.risks:
        for risk in analysis.risks:
            lines.append(f"- {risk}")
    else:
        lines.append("(none)")
    lines.append("</RISKS_FROM_ASSESSMENT>")

    if own_profile.strip():
        lines.append("")
        lines.append("<OUR_COMPANY_PROFILE>")
        lines.append(own_profile.strip())
        lines.append("</OUR_COMPANY_PROFILE>")

    lines.append("")
    lines.append("<COVERAGE>")
    lines.append(f"level: {coverage_level}")
    lines.append("</COVERAGE>")

    return "\n".join(line for line in lines if line is not None)
