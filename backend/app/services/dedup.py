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
from app.models.fact_base import FactItem

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


# Coverage only counts when the shorter sentence is substantial enough that a
# partial match is meaningful. Below this, a single shared word can dominate.
MIN_COVERAGE_CHARS = 40


def coverage(shorter: str, longer: str) -> float:
    """Return how much of the shorter sentence appears inside the longer one.

    ``SequenceMatcher.ratio`` compares both directions, so a short sentence and
    the same sentence with an added clause score low even though one is plainly
    a telling of the other (measured: 0.559 for a base sentence versus its own
    extended version). Coverage asks the question the dedup step actually cares
    about: is this the same sentence, told with more detail?

    Very short sentences are excluded, because one shared word would otherwise
    read as high coverage.
    """
    a = normalize(shorter)
    b = normalize(longer)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    if min(len(a), len(b)) < MIN_COVERAGE_CHARS:
        return 0.0

    # Two sentences with different lengths: match the shorter against the longer.
    if len(a) > len(b):
        a, b = b, a

    matcher = SequenceMatcher(None, a, b)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(a)


def similarity(left: str, right: str) -> float:
    """Return a 0..1 measure of whether these two sentences tell the same thing.

    The larger of a two-way edit distance and a one-way coverage score: the edit
    distance catches paraphrases of the same length, and coverage catches a
    fuller telling of the same sentence.
    """
    a = normalize(left)
    b = normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return max(SequenceMatcher(None, a, b).ratio(), coverage(left, right))


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


async def merge_duplicate_facts(
    facts: Sequence[FactItem],
) -> tuple[List[FactItem], dict]:
    """Collapse facts that say the same thing into one, keeping every source.

    A fact is worth keeping once, no matter how many outlets carried it: the
    claim is the value, not the coverage count. When two entries are the same
    fact, the fuller telling wins because it carries more of the detail, and the
    shorter entry's source moves into the winner's ``also_reported_by`` list so
    the reader still has every link.

    Returns the merged list and counters describing what happened.
    """
    stats = {"considered": len(facts), "merged": 0, "kept": 0}
    if len(facts) < 2:
        return list(facts), stats

    merged: List[FactItem] = []
    claimed: set[int] = set()

    for index, fact in enumerate(facts):
        if index in claimed:
            continue

        group = [fact]
        candidates = [
            (other_index, other)
            for other_index, other in enumerate(facts)
            if other_index != index and other_index not in claimed
        ]
        if not candidates:
            merged.append(fact)
            stats["kept"] += 1
            continue

        # Compare against every member already in the group, not just the
        # anchor: a third telling can be worded closer to the second one, and
        # pairing only against the anchor would silently leave it unmerged.
        remaining = list(candidates)
        while remaining:
            existing_texts = [item.content for item in group]
            incoming_texts = [other.content for _i, other in remaining]

            pairs = find_candidates(existing_texts, incoming_texts)
            if not pairs:
                break

            confirmed = await confirm_same_facts(existing_texts, incoming_texts, pairs)
            if not confirmed:
                break

            advanced = False
            for pair in confirmed:
                other_index, other = remaining[pair.incoming_index]
                if other_index in claimed or other in group:
                    continue
                claimed.add(other_index)
                group.append(other)
                advanced = True

            if not advanced:
                break
            remaining = [
                entry for entry in remaining if entry[0] not in claimed
            ]

        if len(group) == 1:
            merged.append(fact)
            stats["kept"] += 1
            continue

        winner = max(group, key=lambda item: len(item.content))
        sources: List[str] = []
        for item in group:
            for url in item.all_sources():
                if url not in sources:
                    sources.append(url)

        primary = str(winner.source_url)
        winner.also_reported_by = [url for url in sources if url != primary]
        merged.append(winner)
        stats["merged"] += len(group) - 1

    logger.info(
        "Fact merge | considered=%s | merged=%s | kept=%s",
        stats["considered"],
        stats["merged"],
        stats["kept"],
    )
    return merged, stats


async def drop_duplicate_facts(
    existing: Sequence[str],
    incoming: Sequence[str],
) -> List[int]:
    """Return the indices of incoming sentences that repeat an existing fact.

    Used to check incidental mentions against the fact base: a one-line mention
    that says the same thing as a stored fact adds nothing, so it is dropped.
    Runs in two stages — a lexical score proposes pairs, the model confirms
    which are actually the same — and nothing is dropped without confirmation.
    """
    pairs = find_candidates(existing, incoming)
    if not pairs:
        logger.info("Incidental dedup | no candidate pairs above threshold")
        return []

    confirmed = await confirm_same_facts(existing, incoming, pairs)
    dropped = sorted({pair.incoming_index for pair in confirmed})

    logger.info(
        "Incidental dedup | candidates=%s | confirmed=%s | dropped=%s",
        len(pairs),
        len(confirmed),
        len(dropped),
    )
    return dropped
