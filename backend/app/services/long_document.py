# /backend/app/services/long_document.py
"""Read a very long document without paying for the whole of it.

A 10-K is 300,000 characters. Sent whole, it either overruns the context or
crowds out every other source in the brief, and most of it is boilerplate: risk
factors repeated verbatim for years, legal exhibits, accountant letters.

Two operations, both deterministic:

* **segment** — split a long body into paragraph blocks that keep their heading,
  so the pieces still mean something on their own.
* **select** — score each block against the meeting objective and the company
  name, then keep the highest-scoring ones under a character budget. This is
  retrieval inside one document, done with the tokens the user already wrote.

No new dependency: the split is on blank lines, which both markdown and plain
text already provide. The scoring is the same word-boundary matcher used
everywhere else in this project.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from app.services.keywords import match_word
from app.services.logger_service import get_logger

logger = get_logger("long_document")

# Characters kept per document after selection. A 10-K is about 300k; this is
# roughly four pages of dense text, which is enough for the parts that matter.
MAX_SELECTED_CHARS = 12000

# A block shorter than this is a heading fragment or a table row.
MIN_BLOCK_CHARS = 80

# Words that appear in nearly every filing and carry no discriminating power.
BOILERPLATE = (
    "forward-looking statements",
    "table of contents",
    "part i",
    "part ii",
    "part iii",
    "part iv",
    "signature",
    "exhibit index",
    "exhibit 31",
    "exhibit 32",
    "power of attorney",
    "integral part of these statements",
    "pursuant to section 302",
    "certification pursuant",
)

# Sections that almost always hold the answerable content of a filing.
PRIORITY_HEADINGS = (
    "results of operations",
    "liquidity",
    "capital resources",
    "revenue",
    "segment",
    "risk factor",
    "legal proceeding",
    "commitment",
    "contingenc",
    "outlook",
    "guidance",
)


@dataclass(frozen=True)
class DocumentBlock:
    """One paragraph block of a long document, with its nearest heading."""

    heading: str
    text: str

    @property
    def length(self) -> int:
        return len(self.text)


def is_long_document(text: str, threshold: int = MAX_SELECTED_CHARS) -> bool:
    """True when the body is large enough that sending it whole is wasteful."""
    return len(text or "") > threshold


def segment(text: str) -> List[DocumentBlock]:
    """Split a long body into blocks that keep their nearest heading.

    Paragraphs are the primary unit, because blank lines are what both markdown
    and plain text already use to separate prose. A heading is recognised on top
    of that: a short line with no terminal period. Splitting on headings alone
    would put a whole 30,000-character section in one block, which no scoring can
    then narrow.
    """
    blocks: List[DocumentBlock] = []
    heading = ""

    for paragraph in re.split(r"\n\s*\n", text or ""):
        chunk = " ".join(paragraph.split())
        if not chunk:
            continue

        if _looks_like_heading(chunk) and len(chunk) <= 90:
            heading = chunk[:120]
            continue

        # A paragraph that happens to begin with a heading line keeps the heading
        # but is still split from it, so scoring sees prose.
        if len(chunk) < MIN_BLOCK_CHARS:
            continue

        blocks.append(DocumentBlock(heading=heading, text=chunk))

    return blocks


def _looks_like_heading(line: str) -> bool:
    """True for a short line that reads as a section title."""
    if len(line) > 90:
        return False
    if line.endswith((".", "!", "?", ":")) and len(line) > 50:
        return False
    words = line.split()
    if len(words) > 12:
        return False
    # A title is not a sentence: it has no terminal period and few lowercase
    # function words at the start.
    return not line.endswith(".")


def _score(
    block: DocumentBlock,
    company: str,
    objective: str,
) -> float:
    """Score one block for relevance, with heading matches weighted higher."""
    score = 0.0

    heading = block.heading.lower()
    for marker in PRIORITY_HEADINGS:
        if marker in heading:
            score += 3.0
            break

    text = block.text.lower()

    if company and match_word(text, company.strip()):
        score += 2.0

    for word in _objective_words(objective):
        hits = text.count(word)
        if hits:
            # Cap per-word contribution so one repeated term cannot dominate.
            score += min(hits, 5) * 0.6

    for marker in BOILERPLATE:
        if marker in text:
            score -= 2.0

    # A block carrying numbers is more likely to be the substantive one.
    if re.search(r"\d", block.text):
        score += 0.5

    return score


def _objective_words(objective: str) -> List[str]:
    """Return distinctive words from the meeting objective."""
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "about", "into",
        "our", "their", "meeting", "brief", "company", "what", "which", "will",
        "would", "have", "has", "are", "was", "were", "you", "can",
    }
    words: List[str] = []
    for raw in (objective or "").lower().split():
        clean = "".join(ch for ch in raw if ch.isalnum())
        if len(clean) > 3 and clean not in stop:
            words.append(clean)
    return words


def select_relevant(
    text: str,
    company: str,
    objective: str,
    budget: int = MAX_SELECTED_CHARS,
) -> str:
    """Return the most relevant part of a long document, within the budget.

    Blocks are taken in score order until the budget is spent, then the kept
    blocks are re-ordered by their original position so the excerpt still reads
    as a document rather than as a ranked list.
    """
    if not is_long_document(text, budget):
        return text

    blocks = segment(text)
    if not blocks:
        return text[:budget]

    scored = sorted(
        ((_score(block, company, objective), index, block)
         for index, block in enumerate(blocks)),
        key=lambda row: row[0],
        reverse=True,
    )

    kept: List[tuple[int, DocumentBlock]] = []
    used = 0
    for score, index, block in scored:
        if score <= 0:
            break
        if used + block.length > budget:
            continue
        kept.append((index, block))
        used += block.length
        if used >= budget * 0.95:
            break

    if not kept:
        # Nothing scored: a document that does not mention the company at all.
        logger.info(
            "Long document had no relevant block | chars=%s | blocks=%s",
            len(text),
            len(blocks),
        )
        return text[:budget]

    kept.sort(key=lambda row: row[0])

    parts: List[str] = []
    for _index, block in kept:
        if block.heading and (not parts or block.heading not in parts[-1]):
            parts.append(f"## {block.heading}")
        parts.append(block.text)

    selected = "\n\n".join(parts)
    logger.info(
        "Long document reduced | original=%s | kept=%s | blocks_kept=%s/%s",
        len(text),
        len(selected),
        len(kept),
        len(blocks),
    )
    return selected
