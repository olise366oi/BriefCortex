# /backend/app/services/article_subject.py
"""Decide which company each article is about, before any fact is trusted.

Naming the company is not the same as being about the company: a story on
Commerzbank's valuation will mention UniCredit and still be Commerzbank's story.
Routing an article by "did the name appear" therefore files other companies'
reporting as ours. This module asks the model per batch of excerpts and routes
each article by the answer.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import List, Literal, Sequence

from pydantic import BaseModel, Field

from app.services.llm_client import build_client, no_reasoning
from app.services.logger_service import get_logger

logger = get_logger("article_subject")

SUBJECT_PROMPT = """You decide which company each news excerpt is primarily about.

You receive a list of excerpts, the company we care about, and optionally what
the caller told us about that company. For each excerpt classify:
- "primary": the excerpt reports on our company. Its numbers, decisions, results
  or actions are its subject.
- "comparison": our company appears inside a story whose own subject is a
  different company — as a peer, rival, precedent, benchmark, partner,
  counterparty, customer or supplier. The deciding test is whose action the
  sentence reports: "Zocdoc announced a partnership with Instinct" reports
  Zocdoc's action, so it is comparison, even though our company is named twice.
- "unrelated": our company is not a subject of this excerpt. A passing mention,
  a list of many banks, or a similarity in name does not count.

Names collide. Several unrelated businesses can share one name, and each will
report its own numbers, customers and hiring. When the caller described the
company, an excerpt that matches a different same-named business is "unrelated",
not "primary": attribute by what the excerpt describes, never by the name alone.

Judge by what the opening sentences describe, not by whether the name appears.
Reply with JSON only:
{"items": [{"index": 0, "subject": "primary|comparison|unrelated"}]}"""

SubjectKind = Literal["primary", "comparison", "unrelated"]

# Excerpts per classifier call. One call carrying a hundred articles is a single
# request whose latency grows with the batch: measured on a Wipro run, routing
# plus extraction took 60 seconds, and the model call inside it was the largest
# single contributor. Batches also let a malformed reply cost one batch instead
# of every article in the run.
SUBJECT_BATCH_SIZE = 20

# Batches run concurrently. They are independent — each index appears in exactly
# one batch — so serialising them buys nothing.
SUBJECT_CONCURRENCY = 6


class SubjectItem(BaseModel):
    index: int
    subject: SubjectKind


class SubjectBatch(BaseModel):
    items: List[SubjectItem] = Field(default_factory=list)


@dataclass(frozen=True)
class ArticleRoute:
    """Where one article's content should be filed."""

    index: int
    subject: SubjectKind

    @property
    def goes_to_hard_facts(self) -> bool:
        return self.subject == "primary"


async def classify_article_subjects(
    openings: Sequence[str],
    company: str,
    description: str = "",
) -> List[ArticleRoute]:
    """Route each article by what it is about, not by whether it names us.

    ``description`` is the caller's own wording for the company. It is the only
    way to separate two businesses with the same name: a description such as
    "San Francisco AI assistant startup" lets the model reject an excerpt about
    a veterinary software company that also calls itself Instinct, which a bare
    name cannot do.

    Falls back to "comparison" for everything when the model is unreachable: an
    article whose subject is unknown must not be allowed to write our fact base,
    but its sentences about us are still worth harvesting as incidental.
    """
    if not openings:
        return []

    name = company.strip()
    if not name:
        return [ArticleRoute(index=i, subject="unrelated") for i in range(len(openings))]

    has_content = any(text and text.strip() for text in openings)
    if not has_content:
        return [ArticleRoute(index=i, subject="unrelated") for i in range(len(openings))]

    described = " ".join((description or "").split())
    context = f"COMPANY: {name}"
    if described:
        context += f"\nCALLER'S DESCRIPTION OF THAT COMPANY: {described}"

    # Indexed excerpts, split into batches so one call does not carry the whole
    # run. Each batch keeps the original indices, so the routes still line up
    # with the caller's list.
    indexed = [
        (index, text.strip()[:400])
        for index, text in enumerate(openings)
        if text and text.strip()
    ]
    batches = [
        indexed[start : start + SUBJECT_BATCH_SIZE]
        for start in range(0, len(indexed), SUBJECT_BATCH_SIZE)
    ]

    semaphore = asyncio.Semaphore(SUBJECT_CONCURRENCY)

    async def classify_batch(batch: Sequence[tuple[int, str]]) -> dict[int, SubjectKind]:
        numbered = "\n\n".join(f"[{index}] {text}" for index, text in batch)
        user_message = f"{context}\n\nEXCERPTS:\n{numbered}"

        async with semaphore:
            try:
                client, model = build_client()
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SUBJECT_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    **no_reasoning(),
                )
                content = response.choices[0].message.content or ""
                parsed = SubjectBatch.model_validate(json.loads(content))
            except Exception as exc:
                logger.warning(
                    "Article subject classification unavailable for a batch, "
                    "treating those excerpts as comparison | %s",
                    exc,
                )
                return {}

        valid = {index for index, _text in batch}
        return {
            item.index: item.subject
            for item in parsed.items
            if item.index in valid
        }

    per_batch = await asyncio.gather(*[classify_batch(batch) for batch in batches])
    by_index: dict[int, SubjectKind] = {}
    for mapping in per_batch:
        by_index.update(mapping)

    routes = [
        ArticleRoute(index=index, subject=by_index.get(index, "comparison"))
        for index in range(len(openings))
    ]

    primary = sum(1 for route in routes if route.subject == "primary")
    comparison = sum(1 for route in routes if route.subject == "comparison")
    unrelated = sum(1 for route in routes if route.subject == "unrelated")
    logger.info(
        "Article routing | articles=%s | primary=%s | comparison=%s | unrelated=%s",
        len(routes),
        primary,
        comparison,
        unrelated,
    )
    return routes
