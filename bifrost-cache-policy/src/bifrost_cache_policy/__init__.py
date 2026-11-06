"""Evidence-derived cache policy for the Bifrost AI gateway.

Three rules, all deterministic, all computed before the request reaches the
gateway:

1. **A cache key from the evidence** (`headers.build_cache_key`). The key is
   the objective plus a digest of the facts, so a refreshed fact base produces a
   new key instead of a stale hit.
2. **A TTL from the evidence** (`ttl.classify_fact_texts`). Market material
   expires in a day; a filing is still true next week. Sent as
   `x-bf-cache-ttl`, it overrides the gateway's one global default.
3. **Invalidation from a contradiction** (`invalidate.check_reuse`). The stored
   and fresh evidence are each internally consistent and still disagree; a brief
   written during a hiring push is wrong the moment a layoff is reported.

Nothing here needs a model call or an embedding provider, which is what makes
it usable where the gateway decides: before inference.
"""

from .headers import (
    CACHE_KEY_HEADER,
    CACHE_TTL_HEADER,
    CONTENT_CLASS_HEADER,
    CacheDecision,
    build_cache_headers,
    build_cache_key,
    cache_key_to_clear,
    decide,
)
from .invalidate import (
    CONFLICT_RULES,
    Conflict,
    Verdict,
    check_reuse,
    detect_cross_set_conflicts,
    source_of,
    text_of,
)
from .keys import (
    NEAR_DUPLICATE_THRESHOLD,
    STOPWORDS,
    canonical,
    evidence_digest,
    is_near_duplicate,
    objective_digest,
    request_signature,
    similarity,
)
from .matcher import match_word, matched_keyword
from .ttl import (
    CLASS_KEYWORDS,
    DEFAULT_CLASS,
    FACT_CLASSES,
    TTL_BY_CLASS,
    classify_fact_texts,
    classify_ttl,
)

__version__ = "0.1.0"

__all__ = [
    "CACHE_KEY_HEADER",
    "CACHE_TTL_HEADER",
    "CONTENT_CLASS_HEADER",
    "CacheDecision",
    "build_cache_headers",
    "build_cache_key",
    "cache_key_to_clear",
    "decide",
    "CONFLICT_RULES",
    "Conflict",
    "Verdict",
    "check_reuse",
    "detect_cross_set_conflicts",
    "source_of",
    "text_of",
    "NEAR_DUPLICATE_THRESHOLD",
    "STOPWORDS",
    "canonical",
    "evidence_digest",
    "is_near_duplicate",
    "objective_digest",
    "request_signature",
    "similarity",
    "match_word",
    "matched_keyword",
    "CLASS_KEYWORDS",
    "DEFAULT_CLASS",
    "FACT_CLASSES",
    "TTL_BY_CLASS",
    "classify_fact_texts",
    "classify_ttl",
    "__version__",
]
