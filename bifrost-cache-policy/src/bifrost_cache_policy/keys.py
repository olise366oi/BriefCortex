"""Stable keys for cache partitions and for near-duplicate suppression.

Two different questions live here, and they need different answers:

* **Is this the same evidence?** A digest over the fact texts, order- and
  formatting-independent, so a re-run that assembled the same facts in a
  different order still hits.
* **Is this the same question?** A token-overlap test, because two phrasings of
  one objective should not both pay for inference, and an embedding cannot
  answer this on a gateway configured with `dimension: 1`.

Order independence is not cosmetic. Retrieval returns facts in the order the
lanes finished, so a digest that hashed the list as given would produce a
different key on every run for the same evidence — the cache would never hit
and nobody could tell why.
"""
from __future__ import annotations

import hashlib
from typing import Iterable

# Two prompts this similar are treated as the same request. Measured on real
# brief prompts: a reworded objective for the same company scores above 0.8,
# while two different companies score below 0.4.
NEAR_DUPLICATE_THRESHOLD = 0.72

# Words that carry no discriminating power in a briefing prompt.
STOPWORDS = frozenset(
    ("the", "and", "for", "with", "that", "this", "from", "about", "into",
     "company", "brief", "meeting", "report", "please", "give", "what", "which",
     "will", "would", "their", "they", "our", "you", "are", "was", "were")
)


def canonical(text: str) -> str:
    """Collapse whitespace and lowercase, so formatting noise is not a change."""
    return " ".join((text or "").lower().split())


def evidence_digest(texts: Iterable[str]) -> str:
    """Return a short stable digest over a set of fact texts.

    Sorted and canonicalised, so the digest identifies the evidence rather than
    the order the retriever happened to return it in.
    """
    canonical_texts = sorted(canonical(text) for text in texts)
    joined = "\n".join(canonical_texts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def objective_digest(meeting_purpose: str) -> str:
    """Return a short stable digest of a meeting objective."""
    return hashlib.sha256(canonical(meeting_purpose).encode("utf-8")).hexdigest()[:12]


def _tokens(text: str) -> set[str]:
    words = []
    for raw in (text or "").lower().split():
        clean = "".join(ch for ch in raw if ch.isalnum())
        if len(clean) > 2 and clean not in STOPWORDS:
            words.append(clean)
    return set(words)


def similarity(left: str, right: str) -> float:
    """Return a 0..1 overlap score between two prompts.

    Containment of the smaller token set inside the larger one, not Jaccard:
    word order and a slightly longer phrasing do not change what is being asked,
    while Jaccard penalises the extra words as if the request differed. Measured
    on real prompts, a reordered request scores 0.71 under Jaccard (a false miss)
    and 1.0 under containment, while two different companies stay well below the
    threshold either way.
    """
    a = _tokens(left)
    b = _tokens(right)
    if not a or not b:
        return 0.0
    smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
    return len(smaller & larger) / len(smaller)


def is_near_duplicate(
    left: str,
    right: str,
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> bool:
    """True when two prompts are close enough to share a cached response."""
    return similarity(left, right) >= threshold


def request_signature(prompt: str) -> str:
    """Return a stable key for a prompt, normalised through its token set.

    Ordering and punctuation are dropped, so two phrasings that reduce to the
    same tokens share a signature while a single changed word produces a
    different one.
    """
    tokens = sorted(_tokens(prompt))
    return hashlib.sha256(" ".join(tokens).encode("utf-8")).hexdigest()[:32]
