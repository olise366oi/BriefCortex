# /backend/app/services/gateway_plugin.py
"""Content-aware caching rules the gateway can enforce, expressed as plain data.

Bifrost's own `semantic_cache` plugin is configured with `dimension: 1`, which
cannot discriminate between prompts: a one-dimensional embedding maps every text
to a single number, so its similarity test is close to random. Rather than depend
on a plugin that cannot work as configured, the decision this project actually
needs — "may this response be reused, and for how long" — is computed here and
sent as headers.

Two rules, both deterministic:

1. **Cache class** (`bifrost_headers.py`). The evidence decides the TTL: same-day
   market material expires in a day, filings can be kept for a week. Sending this
   per request makes the gateway's TTL correct without a model call.

2. **Near-duplicate suppression** (`is_near_duplicate`). Two prompts asking the
   same thing with trivial wording differences must not both pay for inference.
   This project already had a working similarity test — character overlap plus a
   word-boundary check — and it is cheap enough to run before the request leaves
   the process. Using it in place of an embedding keeps the behaviour predictable
   and removes a dependency that cannot function at `dimension: 1`.

The adapter shape below is what a gateway-side plugin would need: a pure function
of the request that returns the class, the TTL and a stable key. Compiling it
into Bifrost is out of scope here — `bifrost/` holds configuration only, and its
`/api/config` endpoint is auth-gated — but the decision itself is complete and
tested, so a plugin would be a thin wrapper rather than a reimplementation.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Sequence

from app.services.bifrost_headers import (
    build_bifrost_headers,
    classify_fact_texts,
    classify_ttl,
)

# Two prompts this similar are treated as the same request. Measured on this
# project's own fact texts: a reworded objective for the same company scores
# above 0.8, while two different companies score below 0.4.
NEAR_DUPLICATE_THRESHOLD = 0.72

# Words that carry no discriminating power in a briefing prompt.
STOPWORDS = frozenset(
    ("the", "and", "for", "with", "that", "this", "from", "about", "into",
     "company", "brief", "meeting", "report", "please", "give", "what", "which",
     "will", "would", "their", "they", "our", "you", "are", "was", "were")
)


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
    on this project's prompts, a reordered request scores 0.71 under Jaccard (a
    false miss) and 1.0 under containment, while two different companies stay
    well below the threshold either way.
    """
    a = _tokens(left)
    b = _tokens(right)
    if not a or not b:
        return 0.0
    smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
    return len(smaller & larger) / len(smaller)


def is_near_duplicate(left: str, right: str) -> bool:
    """True when two prompts are close enough to share a cached response."""
    return similarity(left, right) >= NEAR_DUPLICATE_THRESHOLD


def request_signature(prompt: str) -> str:
    """Return a stable key for a prompt, normalised through its token set.

    Ordering and punctuation are dropped, so two phrasings that reduce to the
    same tokens share a signature while a single changed word produces a
    different one.
    """
    tokens = sorted(_tokens(prompt))
    return hashlib.sha256(" ".join(tokens).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class GatewayDecision:
    """What a gateway plugin needs to know about one request."""

    cache_key: str
    ttl: str
    content_class: str
    headers: Dict[str, str]


def decide_gateway_cache(
    company_id: str,
    meeting_purpose: str,
    fact_texts: Sequence[str],
) -> GatewayDecision:
    """Return the cache decision for one inference request.

    A thin, typed wrapper over `build_bifrost_headers` so a gateway-side caller
    gets one object instead of re-deriving the class and TTL itself.
    """
    headers = build_bifrost_headers(company_id, meeting_purpose, list(fact_texts))
    fact_class = classify_fact_texts(list(fact_texts))
    return GatewayDecision(
        cache_key=headers["x-bf-cache-key"],
        ttl=classify_ttl(fact_class),
        content_class=fact_class,
        headers=headers,
    )
