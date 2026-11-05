# /backend/app/services/entity_search.py
"""Find the companies a caller might mean, so they can confirm one.

The registry (GLEIF) only knows registered legal entities, which a private
startup usually is not. When the caller rejects the registry list they are asked
to describe the company, and that description alone is too thin to brief on:
"Instinct" plus one sentence still leaves several businesses.

So the description is searched against the web and the model reads the results
back as candidate entities with the concrete facts that identify each one —
legal operator, headquarters, founder, website, what the product does. The caller
confirms the right entity, and only then does retrieval start.

This is a confirmation step, not a brief: it must be cheap, it must never
invent an entity, and every field it shows is traceable to a URL.
"""
from __future__ import annotations

import json
from typing import List, Optional, Sequence

from pydantic import BaseModel, Field, ValidationError
from tavily import TavilyClient

from app.core.config import get_settings
from app.services.llm_client import build_client
from app.services.logger_service import get_logger

logger = get_logger("entity_search")

# Enough to discriminate between same-named businesses without turning a
# confirmation prompt into a research task.
MAX_SEARCH_RESULTS = 10
MAX_CANDIDATES = 4

EXTRACT_PROMPT = """You identify which real companies a business user might mean.

You receive the name the caller typed, the description they supplied, and web
search results about that name. Return the distinct companies those results
actually describe.

Rules:
- Every candidate must be supported by the search results. Never invent a
  company, a website, a founder or a headquarters.
- Distinguish businesses that share a name. A veterinary software firm and a
  consumer AI assistant are two candidates, not one.
- "matching_facts" lists the concrete identifiers a reader would check: the legal
  operator, the headquarters city, the founder, the website, and what the product
  does. Leave a field out of the text if the results do not state it.
- "reason" says why this candidate is or is not likely the one described.
- "evidence_url" must be one of the URLs supplied.
- Order candidates by likelihood for the description, best first. If the results
  describe only unrelated businesses, still return them, with a low "confidence".

Reply with JSON only:
{"candidates": [{"name": str, "matching_facts": str, "reason": str,
  "evidence_url": str, "confidence": "high|medium|low"}]}"""


class EntityCandidate(BaseModel):
    """One company the caller might mean, with the facts that identify it."""

    name: str
    matching_facts: str = ""
    reason: str = ""
    evidence_url: str = ""
    confidence: str = "low"


class CandidateBatch(BaseModel):
    candidates: List[EntityCandidate] = Field(default_factory=list)


def _search(company_name: str, description: str) -> List[dict]:
    """Search the web for this name plus the caller's description."""
    api_key = get_settings().TAVILY_API_KEY
    if not api_key:
        logger.info("Entity search skipped | no search key configured")
        return []

    query = f"{company_name} {description}".strip()[:280]
    client = TavilyClient(api_key=api_key)
    try:
        response = client.search(
            query=query,
            search_depth="advanced",
            max_results=MAX_SEARCH_RESULTS,
            topic="general",
        )
    except Exception as exc:
        logger.warning("Entity search failed | company=%r | %s", company_name, exc)
        return []

    results = response.get("results", []) if isinstance(response, dict) else []
    return [item for item in results if isinstance(item, dict)]


async def suggest_candidates(
    company_name: str,
    description: str,
    rejected: Sequence[str] = (),
    round_number: int = 1,
) -> List[EntityCandidate]:
    """Return the companies this name might refer to, best match first.

    A caller who typed a name that several businesses share, and described theirs
    in one sentence, cannot be asked to pick from a registry list that does not
    contain their company. This turns their description into a short list of
    real, checkable candidates.

    ``rejected`` names are dropped from the result: a caller who just said "none
    of these" must not be shown the same list again. ``round_number`` only
    affects logging and the confidence floor, because on a later round the
    description is longer and the search narrower.
    """
    results = _search(company_name, description)
    if not results:
        return []

    digest = "\n\n".join(
        f"[{index}] {item.get('title') or ''}\n"
        f"URL: {item.get('url') or ''}\n"
        f"{(item.get('content') or '')[:400]}"
        for index, item in enumerate(results)
    )

    try:
        client, model = build_client()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": EXTRACT_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"NAME TYPED BY THE CALLER: {company_name}\n"
                        f"CALLER'S DESCRIPTION: {description or '(none supplied)'}\n\n"
                        f"SEARCH RESULTS:\n{digest}"
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        content = response.choices[0].message.content or ""
        batch = CandidateBatch.model_validate(json.loads(content))
    except (ValidationError, ValueError) as exc:
        logger.warning("Entity extraction returned unusable output | %s", exc)
        return []
    except Exception as exc:
        logger.warning("Entity extraction unavailable | %s", exc)
        return []

    known_urls = {str(item.get("url") or "") for item in results}
    rejected_names = {" ".join(name.split()).lower() for name in rejected}
    kept: List[EntityCandidate] = []
    for candidate in batch.candidates:
        # A candidate whose evidence URL was not in the results is a fabrication.
        if candidate.evidence_url and candidate.evidence_url not in known_urls:
            logger.info(
                "Candidate rejected | url not in results | %s", candidate.evidence_url
            )
            continue
        # Someone already said this is not their company.
        if " ".join(candidate.name.split()).lower() in rejected_names:
            logger.info("Candidate skipped | already rejected by the caller | %s", candidate.name)
            continue
        kept.append(candidate)
        if len(kept) >= MAX_CANDIDATES:
            break

    logger.info(
        "Entity candidates | company=%r | round=%s | results=%s | offered=%s | "
        "previously_rejected=%s",
        company_name,
        round_number,
        len(results),
        len(kept),
        len(rejected_names),
    )
    return kept
