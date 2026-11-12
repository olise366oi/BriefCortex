# /backend/app/services/dedup.py
"""Decide when two sentences are telling the same thing.

Character overlap is only a candidate filter: two sentences can share most of
their words and still report different facts, and two sentences can report the
same event using almost none of the same words. So the cheap score proposes
pairs and the model decides them.
"""
from __future__ import annotations

import asyncio
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

# Cap for the two-list pass (incidental mentions against the fact base), where
# one side is new and the other is already stored: pair count grows with the
# product of the two lengths.
MAX_CANDIDATES = 20

# Pairs per confirmation request.
BATCH_SIZE = 12

# Confirmation batches run concurrently. Each batch is an independent model call,
# so waiting for one before starting the next is pure latency: measured on a
# Wipro run, 172 facts took 335 seconds in the merge stage alone — 64% of the
# whole ingestion — because the anchor loop issued one confirmation round after
# another.
CONFIRM_CONCURRENCY = 8

# Partners kept per fact in the global candidate pass. Without a per-fact bound
# a page of near-identical syndicated sentences would post thousands of pairs;
# this keeps the model's workload proportional to the fact count rather than to
# its square.
MAX_PARTNERS_PER_FACT = 8

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


def _coverage_normalized(a: str, b: str) -> float:
    """Coverage for two strings that are already ``normalize``d."""
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


def _similarity_normalized(a: str, b: str) -> float:
    """Similarity for two strings that are already ``normalize``d."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return max(SequenceMatcher(None, a, b).ratio(), _coverage_normalized(a, b))


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
    return _coverage_normalized(normalize(shorter), normalize(longer))


def similarity(left: str, right: str) -> float:
    """Return a 0..1 measure of whether these two sentences tell the same thing.

    The larger of a two-way edit distance and a one-way coverage score: the edit
    distance catches paraphrases of the same length, and coverage catches a
    fuller telling of the same sentence.
    """
    return _similarity_normalized(normalize(left), normalize(right))


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
    normalized_incoming = [normalize(text) for text in incoming]
    normalized_existing = [normalize(text) for text in existing]
    for incoming_index, text in enumerate(normalized_incoming):
        for existing_index, known in enumerate(normalized_existing):
            score = _similarity_normalized(known, text)
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


def find_candidates_global(
    facts: Sequence[str],
    *,
    threshold: float = CANDIDATE_SIMILARITY,
    max_partners: int = MAX_PARTNERS_PER_FACT,
) -> List[CandidatePair]:
    """Return candidate pairs across the whole set, strongest first.

    The previous merge walked one anchor at a time and asked the model about its
    partners, then re-asked about the remaining ones after every accept — one
    model round per accepted fact, which is where 335 seconds went on a 172-fact
    run. The pairs do not depend on the merge order, so they are all proposed
    once and confirmed in parallel; the grouping is then a union-find over the
    confirmed pairs.

    ``max_partners`` bounds each fact's contribution: a page of near-identical
    syndicated sentences otherwise proposes thousands of pairs, none of which
    the model needs to see twice.
    """
    scored: List[CandidatePair] = []
    # Normalize once per fact: the pairwise loop is quadratic, and normalizing
    # inside it re-ran the regexes on every partner (every fact was normalized
    # n-1 times).
    normalized = [normalize(text) for text in facts]
    for left in range(len(facts)):
        for right in range(left + 1, len(facts)):
            score = _similarity_normalized(normalized[left], normalized[right])
            if score >= threshold:
                scored.append(
                    CandidatePair(
                        existing_index=left,
                        incoming_index=right,
                        similarity=score,
                    )
                )

    scored.sort(key=lambda pair: pair.similarity, reverse=True)

    kept: List[CandidatePair] = []
    partners: dict[int, int] = {}
    for pair in scored:
        if (
            partners.get(pair.existing_index, 0) >= max_partners
            or partners.get(pair.incoming_index, 0) >= max_partners
        ):
            continue
        kept.append(pair)
        partners[pair.existing_index] = partners.get(pair.existing_index, 0) + 1
        partners[pair.incoming_index] = partners.get(pair.incoming_index, 0) + 1
    return kept


async def confirm_same_facts(
    existing: Sequence[str],
    incoming: Sequence[str],
    pairs: Sequence[CandidatePair],
) -> List[CandidatePair]:
    """Return only the pairs the model confirms report the same fact.

    When the model is unavailable nothing is confirmed and every incoming
    sentence survives. Keeping a duplicate is recoverable; deleting a distinct
    fact is not.

    Batches are independent calls, so they run concurrently: a run with 172
    facts had enough candidate pairs that confirming them one batch at a time
    was most of the request's wall clock.
    """
    if not pairs:
        return []

    batches = [
        pairs[start : start + BATCH_SIZE]
        for start in range(0, len(pairs), BATCH_SIZE)
    ]

    semaphore = asyncio.Semaphore(CONFIRM_CONCURRENCY)

    async def confirm_batch(batch: Sequence[CandidatePair]) -> List[CandidatePair]:
        async with semaphore:
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
                return []

        confirmed: List[CandidatePair] = []
        for verdict in verdicts.pairs:
            if not verdict.same:
                continue
            if 0 <= verdict.index < len(batch):
                confirmed.append(batch[verdict.index])
        return confirmed

    results = await asyncio.gather(*[confirm_batch(batch) for batch in batches])
    return [pair for batch_confirmed in results for pair in batch_confirmed]


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

    texts = [item.content for item in facts]
    # The candidate pass is O(n²) SequenceMatcher work: run it off the event
    # loop, otherwise the CPU bound blocks every other stage of the ingestion
    # (the same reason body extraction moved to a worker pool).
    pairs = await asyncio.to_thread(find_candidates_global, texts)
    confirmed = await confirm_same_facts(texts, texts, pairs) if pairs else []
    if not pairs:
        logger.info(
            "Fact merge | considered=%s | no lexical candidates, model not asked",
            len(facts),
        )
        return list(facts), stats

    parent = list(range(len(facts)))

    def find_root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find_root(left), find_root(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for pair in confirmed:
        union(pair.existing_index, pair.incoming_index)

    groups: dict[int, List[FactItem]] = {}
    for index, fact in enumerate(facts):
        groups.setdefault(find_root(index), []).append(fact)

    merged: List[FactItem] = []
    for group in groups.values():
        if len(group) == 1:
            merged.append(group[0])
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
        "Fact merge | considered=%s | candidates=%s | confirmed=%s | merged=%s | kept=%s",
        stats["considered"],
        len(pairs),
        len(confirmed),
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
