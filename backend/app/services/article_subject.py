# /backend/app/services/article_subject.py
"""Decide which company each article is about, before any fact is trusted.

Naming the company is not the same as being about the company: a story on
Commerzbank's valuation will mention UniCredit and still be Commerzbank's story.
Routing an article by "did the name appear" therefore files other companies'
reporting as ours. This module asks the model once, for the whole batch, and
routes each article by the answer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Literal, Sequence

from pydantic import BaseModel, Field

from app.services.llm_client import build_client
from app.services.logger_service import get_logger

logger = get_logger("article_subject")

SUBJECT_PROMPT = """You decide which company each news excerpt is primarily about.

You receive a list of excerpts and the company we care about. For each excerpt
classify:
- "primary": the excerpt reports on our company. Its numbers, decisions, results
  or actions are its subject.
- "comparison": our company is a peer, rival, precedent or benchmark inside a
  story about someone else.
- "unrelated": our company is not a subject of this excerpt. A passing mention,
  a list of many banks, or a similarity in name does not count.

Judge by what the opening sentences describe, not by whether the name appears.
Reply with JSON only:
{"items": [{"index": 0, "subject": "primary|comparison|unrelated"}]}"""

SubjectKind = Literal["primary", "comparison", "unrelated"]


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
) -> List[ArticleRoute]:
    """Route each article by what it is about, not by whether it names us.

    Falls back to "comparison" for everything when the model is unreachable: an
    article whose subject is unknown must not be allowed to write our fact base,
    but its sentences about us are still worth harvesting as incidental.
    """
    if not openings:
        return []

    name = company.strip()
    if not name:
        return [ArticleRoute(index=i, subject="unrelated") for i in range(len(openings))]

    numbered = "\n\n".join(
        f"[{index}] {text.strip()[:400]}"
        for index, text in enumerate(openings)
        if text and text.strip()
    )
    if not numbered:
        return [ArticleRoute(index=i, subject="unrelated") for i in range(len(openings))]

    try:
        client, model = build_client()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SUBJECT_PROMPT},
                {"role": "user", "content": f"COMPANY: {name}\n\nEXCERPTS:\n{numbered}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        content = response.choices[0].message.content or ""
        batch = SubjectBatch.model_validate(json.loads(content))
    except Exception as exc:
        logger.warning(
            "Article subject classification unavailable, treating all as comparison | %s",
            exc,
        )
        return [
            ArticleRoute(index=i, subject="comparison") for i in range(len(openings))
        ]

    by_index = {item.index: item.subject for item in batch.items}
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
