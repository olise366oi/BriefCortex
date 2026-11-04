# /backend/app/services/sentence_classifier.py
"""Ask the model whether a sentence states a fact.

Rules cannot decide this reliably: "beat expectations" is opinion while "raised
guidance to 26.3bn" is fact, and both look like business writing. The model
reads the sentence and answers, and our code only stores the answer.
"""
from __future__ import annotations

import json
from typing import List, Literal

from pydantic import BaseModel, Field

from app.services.llm_client import build_client
from app.services.logger_service import get_logger

logger = get_logger("sentence_classifier")

CLASSIFY_PROMPT = """You separate reported fact from commentary in financial news.

You receive sentences and an article context. For each sentence decide:
- "fact": it reports something that happened, was filed, was measured, or was
  stated on the record. Numbers, dates, filings, appointments, deliveries,
  guidance, rulings and settlements are facts.
- "opinion": it interprets, evaluates, predicts, or characterises. Superlatives,
  expectations, analyst views, strategy judgements and adjectives of quality are
  opinions.
- "unclear": it cannot be told apart without more context.

Rules:
- Judge only what the sentence asserts, not whether the claim is true.
- A sentence reporting what someone said is a fact about the statement, not a
  fact about the claim. If the claim itself is what matters, mark it "unclear".
- Do not rewrite the sentences. Do not add or drop any.

Reply with JSON only:
{"labels": [{"index": 0, "kind": "fact|opinion|unclear"}]}"""


class SentenceLabel(BaseModel):
    index: int
    kind: Literal["fact", "opinion", "unclear"]


class SentenceLabels(BaseModel):
    labels: List[SentenceLabel] = Field(default_factory=list)


async def classify_sentences(sentences: List[str], context: str) -> List[str]:
    """Return a fact/opinion/unclear label per sentence, in input order.

    Falls back to "unclear" for everything when the model cannot be reached: an
    unlabelled sentence must never be promoted to a fact by accident.
    """
    if not sentences:
        return []

    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(sentences))
    user_message = f"ARTICLE CONTEXT:\n{context}\n\nSENTENCES:\n{numbered}"

    try:
        client, model = build_client()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CLASSIFY_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        content = response.choices[0].message.content or ""
        verdicts = SentenceLabels.model_validate_json(content)
    except Exception as exc:
        # A sentence the model could not label must never be promoted to a fact.
        logger.warning("Sentence classification unavailable | error=%s", exc)
        return ["unclear"] * len(sentences)

    by_index = {label.index: label.kind for label in verdicts.labels}
    return [by_index.get(index, "unclear") for index in range(len(sentences))]
