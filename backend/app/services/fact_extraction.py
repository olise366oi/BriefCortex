from __future__ import annotations

import re
from typing import List, Optional

from app.services.keywords import match_word
from app.services.source_rules import (
    HARD_FACT_HEAD_PARAGRAPHS,
    MIN_PARAGRAPH_LENGTH,
    NEGATIVE_SIGNAL_KEYWORDS,
    NEGATIVE_SIGNAL_MAX_PER_SOURCE,
)

# Source credibility ranking, most authoritative first. Results from an earlier
# tier win when the same sentence is reported by several outlets.
SOURCE_PRIORITY = (
    "reuters.com",
    "bloomberg.com",
    "wsj.com",
    "ft.com",
    "nikkei.com",
    "cnbc.com",
    "businessinsider.com",
    "finance.yahoo.com",
    "sec.gov",
    "prnewswire.com",
    "globenewswire.com",
    "businesswire.com",
    "autonews.gasgoo.com",
    "insideevs.com",
    "electrek.co",
    "cnevpost.com",
)

# A twin is one telling that a longer telling extends word for word. The shared
# opening must be substantial before one may replace the other, so two different
# facts that happen to share a short opening both survive.
DEDUP_MIN_SHARED_CHARS = 80


def first_sentence(paragraph: str) -> str:
    """Return the first sentence of a paragraph without semantic rewriting."""
    paragraph = paragraph.strip()
    if not paragraph:
        return ""

    parts = re.split(r"(?<=[.!?])\s+", paragraph, maxsplit=1)
    first = parts[0].strip() if parts else paragraph

    return first or paragraph


def has_negative_signal(text: str) -> bool:
    """Detect negotiation-relevant negative signals in a sentence."""
    return any(match_word(text, keyword) for keyword in NEGATIVE_SIGNAL_KEYWORDS)


def extract_hard_facts(clean_text: str) -> List[str]:
    """Extract hard facts from clean text, ignoring commentator opinion.

    - The opening paragraphs and the closing paragraph are always read.
    - Middle paragraphs are never read as prose; only their first sentence is
      inspected, and only to recover the negative signals a negotiator needs.
    - Middle sentences without a negative signal are dropped, so commentator
      framing cannot leak into the fact base.

    Paragraphs shorter than MIN_PARAGRAPH_LENGTH are dropped, because Tavily
    markdown contains headings, list items and quote blocks that are
    structurally not paragraphs.
    """
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", clean_text)
        if paragraph.strip() and len(paragraph.strip()) >= MIN_PARAGRAPH_LENGTH
    ]

    if not paragraphs:
        return []

    if len(paragraphs) <= HARD_FACT_HEAD_PARAGRAPHS:
        return paragraphs

    lead = paragraphs[:HARD_FACT_HEAD_PARAGRAPHS]
    middle = paragraphs[HARD_FACT_HEAD_PARAGRAPHS:-1]
    tail = paragraphs[-1:]

    hard_texts = [text for text in lead + tail if text]

    negative_texts: List[str] = []
    for paragraph in middle:
        sentence = first_sentence(paragraph)
        if not sentence or not has_negative_signal(sentence):
            continue

        negative_texts.append(sentence)
        if len(negative_texts) >= NEGATIVE_SIGNAL_MAX_PER_SOURCE:
            break

    return hard_texts + negative_texts


def _host_of(url: str) -> str:
    host = url.strip().lower()
    for separator in ("?", "#"):
        if separator in host:
            host = host.split(separator, 1)[0]
    if "//" in host:
        host = host.split("//", 1)[1]
    host = host.split("/", 1)[0]
    if host.startswith("www."):
        host = host[len("www."):]
    return host


def source_tier(url: str) -> int:
    """Rank a source by credibility tier; unknown hosts rank last."""
    host = _host_of(url)
    for index, domain in enumerate(SOURCE_PRIORITY):
        if host.endswith(domain):
            return index
    return len(SOURCE_PRIORITY)


def publisher_of(url: str) -> str:
    """Return the publishing brand for grouping.

    markets.businessinsider.com and businessinsider.com are one brand, so their
    articles are read together and de-duplicated before the next publisher in
    the priority list is opened.
    """
    host = _host_of(url)
    for domain in SOURCE_PRIORITY:
        if host.endswith(domain):
            return domain
    return host


def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace."""
    normalized = re.sub(r"[^\w\s]", "", text.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def dedup_key(text: str) -> str:
    """Normalize a sentence into an exact-duplicate key.

    Keyed on the whole sentence rather than a fixed prefix: two facts that open
    with the same few words are still different facts, and collapsing them would
    silently drop one. Longer tellings are reconciled separately by
    ``find_prefix_twin``.
    """
    return normalize_text(text)


def find_prefix_twin(normalized_texts: List[str], candidate: str) -> Optional[int]:
    """Return the index of a stored text that the candidate strictly extends.

    A syndicated story is often re-published with an extra clause, so the fuller
    telling should win. The match requires the stored text to be a word-for-word
    prefix of the candidate and long enough that the overlap cannot be accidental.
    ``normalized_texts`` must already be run through ``normalize_text``.
    """
    normalized_candidate = normalize_text(candidate)
    for index, normalized_existing in enumerate(normalized_texts):
        if len(normalized_existing) < DEDUP_MIN_SHARED_CHARS:
            continue
        if normalized_candidate.startswith(normalized_existing):
            return index
    return None


def order_by_publisher(results: List[dict]) -> List[dict]:
    """Order results by publisher credibility, keeping one publisher's sites together."""
    usable = [result for result in results if isinstance(result, dict)]
    usable.sort(
        key=lambda result: (
            source_tier(str(result.get("url") or "")),
            publisher_of(str(result.get("url") or "")),
        )
    )
    return usable
