# /backend/app/services/dedup.py
"""Decide when two sentences are telling the same thing.

Character overlap is only a candidate filter: two sentences can share most of
their words and still report different facts, and two sentences can report the
same event using almost none of the same words. So the cheap score proposes
pairs and the model decides them.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Sequence

from pydantic import BaseModel, Field

from app.services.llm_client import build_client
from app.services.logger_service import get_logger

logger = get_logger("dedup")

# Below this lexical similarity the pair is not worth a model call; above it the
# pair is a candidate but still not a conclusion.
CANDIDATE_SIMILARITY = 0.6

# How many candidate pairs to submit at once. Pair count grows quadratically, so
# the list is capped and the strongest candidates win.
MAX_CANDIDATES = 20
BATCH_SIZE = 12

SAME_EVENT_PROMPT = """You decide whether two sentences report the same fact.

Two sentences report the same fact when they assert the same thing about the
same subject: the same number, the same event, the same decision, the same
period. Different wording, extra detail, or a different outlet does not make
them different facts.

They are NOT the same fact when:
- they describe different events, even of the same kind (two separate layoffs)
- they report different numbers, periods, or subjects
- one adds a consequence the other does not assert

Reply with JSON only:
{"pairs": [{"index": 0, "same": true|false, "reason": "short"}]}"""


def normalize(text: str) -> str:
    """Lowercase and collapse punctuation so similarity is about words."""
    lowered = text.lower()
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def similarity(left: str, right: str) -> float:
    """Return a 0..1 lexical similarity between two sentences."""
    a = normalize(left)
    b = normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


@dataclass(frozen=True)
class CandidatePair:
    """Two sentence positions that may repeat the same fact."""

    existing_index: int
    incoming_index: int
    similarity: float


class PairVerdict(BaseModel):
    index: int
    same: bool = False
    reason: str = ""


class PairVerdicts(BaseModel):
    pairs: List[PairVerdict] = Field(default_factory=list)


def find_candidates(
    existing: Sequence[str],
    incoming: Sequence[str],
    *,
    threshold: float = CANDIDATE_SIMILARITY,
    max_candidates: int = MAX_CANDIDATES,
) -> List[CandidatePair]:
    """Return candidate pairs above the similarity threshold, strongest first."""
    pairs: List[CandidatePair] = []
    for incoming_index, text in enumerate(incoming):
        for existing_index, known in enumerate(existing):
            score = similarity(known, text)
            if score >= threshold:
                pairs.append(
                    CandidatePair(
                        existing_index=existing_index,
                        incoming_index=incoming_index,
                        similarity=score,
                    )
                )

    pairs.sort(key=lambda pair: pair.similarity, reverse=True)
    return pairs[:max_candidates]


async def confirm_same_facts(
    existing: Sequence[str],
    incoming: Sequence[str],
    pairs: Sequence[CandidatePair],
) -> List[CandidatePair]:
    """Return only the pairs the model confirms report the same fact.

    When the model is unavailable nothing is confirmed and every incoming
    sentence survives. Keeping a duplicate is recoverable; deleting a distinct
    fact is not.
    """
    if not pairs:
        return []

    confirmed: List[CandidatePair] = []

    for start in range(0, len(pairs), BATCH_SIZE):
        batch = pairs[start : start + BATCH_SIZE]
        lines = []
        for offset, pair in enumerate(batch):
            lines.append(
                f"[{offset}]\nA: {existing[pair.existing_index]}\n"
                f"B: {incoming[pair.incoming_index]}"
            )
        prompt = "\n\n".join(lines)

        try:
            client, model = build_client()
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SAME_EVENT_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            content = response.choices[0].message.content or ""
            verdicts = PairVerdicts.model_validate(json.loads(content))
        except Exception as exc:
            logger.warning("Semantic dedup unavailable, keeping all | %s", exc)
            continue

        for verdict in verdicts.pairs:
            if not verdict.same:
                continue
            if 0 <= verdict.index < len(batch):
                confirmed.append(batch[verdict.index])

    return confirmed


async def drop_duplicate_facts(
    existing: Sequence[str],
    incoming: Sequence[str],
) -> List[int]:
    """Return the indices of incoming sentences that repeat an existing fact.

    Runs in two stages: a lexical score proposes the pairs that could plausibly
    be the same, then the model confirms which of them actually are. A sentence
    is dropped only when the model agrees.
    """
    pairs = find_candidates(existing, incoming)
    if not pairs:
        logger.info("Semantic dedup | no candidate pairs above threshold")
        return []

    confirmed = await confirm_same_facts(existing, incoming, pairs)
    dropped = sorted({pair.incoming_index for pair in confirmed})

    logger.info(
        "Semantic dedup | candidates=%s | confirmed=%s | dropped=%s",
        len(pairs),
        len(confirmed),
        len(dropped),
    )
    return dropped
