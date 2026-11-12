from __future__ import annotations

import re
from typing import List, Optional

from app.services.hiring import is_careers_url
from app.services.keywords import match_word
from app.services.urls import host_of, url_path
from app.services.source_rules import (
    HARD_FACT_HEAD_PARAGRAPHS,
    MIN_PARAGRAPH_LENGTH,
    NEGATIVE_SIGNAL_KEYWORDS,
    NEGATIVE_SIGNAL_MAX_PER_SOURCE,
    OPINION_MAX_PER_SOURCE,
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


# Abbreviations whose trailing period is not a sentence end. A dateline such as
# "AHMEDABAD, India, Oct. 12, 2026" is one sentence, not three.
SENTENCE_END_EXCEPTIONS = (
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "inc", "ltd", "co", "corp",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "st", "no", "vs", "approx", "dept", "est", "pty", "plc",
)


def _is_abbreviation(before: str) -> bool:
    """True when the word before a period is a known abbreviation."""
    word = before.rsplit(" ", 1)[-1].strip().lower()
    return word in SENTENCE_END_EXCEPTIONS


def first_sentence(paragraph: str) -> str:
    """Return the first sentence of a paragraph without semantic rewriting.

    A period only ends a sentence when the word before it is not a known
    abbreviation, so a dateline ("AHMEDABAD, India, Oct. 12, 2026") stays whole
    instead of being cut to its third word.
    """
    paragraph = paragraph.strip()
    if not paragraph:
        return ""

    for match in re.finditer(r"[.!?]\s+", paragraph):
        if _is_abbreviation(paragraph[: match.start()]):
            continue
        return paragraph[: match.start() + 1].strip()

    return paragraph


def has_negative_signal(text: str) -> bool:
    """Detect negotiation-relevant negative signals in a sentence."""
    return any(match_word(text, keyword) for keyword in NEGATIVE_SIGNAL_KEYWORDS)


def extract_hard_facts(clean_text: str) -> List[str]:
    """Extract hard facts from clean text, ignoring commentator opinion.

    - The opening paragraphs and the closing paragraph are always read.
    - Middle paragraphs are never read as prose; only their first sentence is
      inspected, and only to recover the negative signals a negotiator needs.
    - Middle sentences without a negative signal are dropped from the fact
      base, so commentator framing cannot leak into it. They are still
      recoverable as analyst opinions, which the prompt quarantines.

    Paragraphs shorter than MIN_PARAGRAPH_LENGTH are dropped, because Tavily
    markdown contains headings, list items and quote blocks that are
    structurally not paragraphs.

    A paragraph is then split into sentences and each sentence is stored as its
    own fact. One sentence states one thing; a paragraph states several, and
    storing the paragraph whole meant a claim built from its third sentence could
    not be traced to any single stored item, and a figure buried at character 500
    sat inside one "fact" that no citation could point at. Measured on a live
    brief: 15 of 78 stored facts bundled three or more distinct statements, and
    the audit could not verify the claims built from them.
    """
    paragraphs = _paragraphs(clean_text)
    if not paragraphs:
        return []

    if len(paragraphs) <= HARD_FACT_HEAD_PARAGRAPHS:
        return _split_sentences(paragraphs)

    lead = paragraphs[:HARD_FACT_HEAD_PARAGRAPHS]
    middle = paragraphs[HARD_FACT_HEAD_PARAGRAPHS:-1]
    tail = paragraphs[-1:]

    hard_texts = [text for text in _split_sentences(lead + tail)]

    negative_texts: List[str] = []
    for paragraph in middle:
        sentence = first_sentence(paragraph)
        if not sentence or not has_negative_signal(sentence):
            continue
        if is_structural_noise(sentence):
            continue

        negative_texts.append(sentence)
        if len(negative_texts) >= NEGATIVE_SIGNAL_MAX_PER_SOURCE:
            break

    return hard_texts + negative_texts


# A sentence end followed by what starts the next sentence. A run of whitespace
# after terminal punctuation is what separates sentences in cleaned prose. The
# lookahead requires a capitalised word, an opening bracket, or a digit followed
# by a space — not a currency sign, because "C$274.0m. Gross profit" would split
# inside the figure and "C$274.0m from C$258.8m" would split at the "$".
_SENTENCE_BOUNDARY = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z(*]|[0-9]+[A-Za-z]?\.\s)"
)

# A fragment shorter than this is a table cell, a ticker, or a stray label, not a
# statement that can be cited on its own.
MIN_FACT_SENTENCE_CHARS = 40


def _split_sentences(paragraphs: List[str]) -> List[str]:
    """Return one fact per sentence, keeping only sentences that state something.

    A paragraph whose sentences cannot be recovered (a table, a list of tickers)
    is kept whole rather than shredded into meaningless fragments: structure that
    has no sentence boundary has no information boundary either.
    """
    out: List[str] = []
    for paragraph in paragraphs:
        sentences = [
            " ".join(sentence.split())
            for sentence in _SENTENCE_BOUNDARY.split(paragraph)
        ]
        sentences = [s for s in sentences if s]

        if len(sentences) <= 1:
            if not _is_document_fragment(paragraph):
                out.append(paragraph.strip())
            continue

        # A trailing fragment that never became a sentence ("14.") joins the one
        # before it instead of standing alone. A complete sentence that happens to
        # be short is kept: "Gross profit rose to C$182.9 million." is a fact and
        # joining it upward would bury a figure inside another sentence.
        merged: List[str] = []
        for sentence in sentences:
            if (
                merged
                and len(sentence) < MIN_FACT_SENTENCE_CHARS
                and not sentence.endswith((".", "!", "?"))
            ):
                merged[-1] = f"{merged[-1]} {sentence}"
                continue
            merged.append(sentence)

        # A court filing splits into caption lines — "Defendants.", "No.", "564.",
        # a comma run of corporate names — and a measured YKK run filed six of
        # them as facts. They carry no statement, so they are not stored.
        out.extend(item for item in merged if not _is_document_fragment(item))
    return out


# Case-docket fragments. A court PDF splits into lines like "Defendants.",
# "No.", "564.", and a party list that is only corporation names. None states
# anything about the business: they are the caption and numbering of a filing.
_DOCKET_FRAGMENT_RE = re.compile(
    r"^(?:"
    r"defendants?\.?|plaintiffs?\.?|"
    r"no\.?|nos\.?|dkt\.?|"
    r"(?:v\.|vs\.)\s|"
    r"order(ed)?\s*[:.]|"
    r"so\s+ordered\.?|"
    r"\d{1,4}[.\-–]?|"
    r"(?:ltd|llc|inc|corp|gmbh|s\.?a\.?r\.?l|pte|co)\.?"
    r")$",
    re.IGNORECASE,
)

# A party list is many company names joined by commas, with no verb anywhere.
_PARTY_LIST_MIN_NAMES = 3


def _is_document_fragment(text: str) -> bool:
    """True when a stored item is a filing's numbering rather than a statement."""
    stripped = text.strip()
    if not stripped:
        return True

    if _DOCKET_FRAGMENT_RE.match(stripped):
        return True

    # "YKK ZIPPER INDONESIA, YKK BANGLADESH PTE. LTD., YKK KOREA CO., LTD., …"
    # A block with no verb and at least three legal-entity suffixes is a caption.
    if not _has_sentence(stripped):
        suffixes = len(
            re.findall(r"\b(?:ltd|llc|inc|corp|gmbh|sarl|pte|co)\b\.?", stripped, re.I)
        )
        if suffixes >= _PARTY_LIST_MIN_NAMES:
            return True

    return False


def extract_analyst_opinions(clean_text: str, hard_facts: List[str]) -> List[str]:
    """Recover the commentary that the hard-fact split deliberately dropped.

    Only the middle paragraphs are read, and only their first sentence. A
    sentence that already entered the fact base is not repeated here, and one
    carrying a negative signal is a fact, not an opinion. These lines are
    rendered in their own prompt block and may not be used as leverage.
    """
    paragraphs = _paragraphs(clean_text)
    if len(paragraphs) <= HARD_FACT_HEAD_PARAGRAPHS:
        return []

    middle = paragraphs[HARD_FACT_HEAD_PARAGRAPHS:-1]
    dropped = {dedup_key(text) for text in hard_facts}

    opinions: List[str] = []
    for paragraph in middle:
        sentence = first_sentence(paragraph)
        if not sentence or has_negative_signal(sentence):
            continue
        if dedup_key(sentence) in dropped:
            continue
        # A table row survives paragraph splitting when the row is long enough to
        # look like prose; the sentence that comes out of it is still a row.
        if is_structural_noise(sentence):
            continue

        opinions.append(sentence)
        if len(opinions) >= OPINION_MAX_PER_SOURCE:
            break

    return opinions


def _paragraphs(clean_text: str) -> List[str]:
    """Split clean text into paragraph-level blocks worth reading.

    A block has to be prose that states something. Three shapes look like
    paragraphs and are not: a markdown table row, a subscription prompt, and a
    filing's structural boilerplate. All three were measured reaching the fact
    base of a live run and being read as evidence — a row of a geographic
    revenue table became a "fact", and "Gain a 360-degree view … Learn more"
    became another.
    """
    return [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", clean_text)
        if paragraph.strip()
        and len(paragraph.strip()) >= MIN_PARAGRAPH_LENGTH
        and not is_structural_noise(paragraph.strip())
    ]


# A markdown table row: cells separated by pipes. Tavily returns XBRL and
# financial statements this way, and a row of numbers with no sentence around
# them is not a fact anyone can cite in a meeting.
_TABLE_ROW_RE = re.compile(r"^\s*\|?[^|\n]*\|[^|\n]*\|")

# A row of dashes under a header ("| --- | --- | --- |") is pure formatting.
_TABLE_RULE_RE = re.compile(r"^[\s|:\-]+$")

# A navigation rail: a chain of links separated by a bullet or a colon. Tavily
# returns the sidebar as prose, and a measured YKK brief filed a Japanese
# comparison-site's card and brokerage rails as three hard facts — 200
# characters of "「年会費無料＆高還元」で選ぶ！" carrying no statement about the
# company. Detection is the ratio, not the presence of any marker: real Japanese
# reporting uses "・" for lists too, so only a block that is mostly separators
# qualifies.
#
# Pipes are deliberately **not** separators here. A markdown table is a chain of
# short fragments too, and it has its own rules; reading "|" as a rail marker
# turned every small table back into noise and deleted the prose cell inside it.
_NAV_SEPARATORS = ("・", "·", ":")


def _is_navigation_rail(text: str) -> bool:
    """True when a block is a link rail rather than a sentence."""
    if not text:
        return False
    separators = sum(text.count(marker) for marker in _NAV_SEPARATORS)
    if separators < 4:
        return False
    # A rail is many short fragments; prose is a few long ones.
    fragments = [
        fragment for fragment in re.split(r"[・·:]", text) if fragment.strip()
    ]
    if len(fragments) < 5:
        return False
    average = sum(len(fragment.strip()) for fragment in fragments) / len(fragments)
    return average < 30


# Marketing copy that a vendor page pads its description with. It names the
# reader's benefit, never the company's situation.
_MARKETING_RE = re.compile(
    r"(gain a 360|gain access|learn more|request a demo|book a demo|"
    r"sign up for|subscribe to|start your free trial|contact sales|"
    r"premium signals|all rights reserved|cookie policy|"
    r"why choose us|trusted by \d|"
    # A vendor's own signup and privacy boilerplate, which a supplier-marketing
    # page pads its "why we use this supplier" copy with. Measured on YKK: two of
    # eleven facts from a motorcycle-apparel page were its newsletter consent and
    # its unsubscribe note.
    r"by subscribing, you agree|marketing communications|"
    r"we respect your privacy|you can unsubscribe|opt out at any time|"
    r"we use cookies|this site uses cookies)",
    re.IGNORECASE,
)

# Filing furniture: statements about the document rather than the business.
_FILING_BOILERPLATE_RE = re.compile(
    r"(is a non-ifrs (financial )?measure|are non-ifrs|"
    r"constant currency (revenue )?(is|was) a non-ifrs|"
    r"exhibit no\.|^description$|press release of .*, dated|"
    r"forward-looking statements? (in this|contained)|"
    r"this (annual|quarterly) report (contains|includes) forward)",
    re.IGNORECASE,
)

# Page footers and copyright rails. A site's own legal furniture states nothing
# about the company it covered: a measured Law360 block reached the fact base as
# "© 2026, Portfolio Media, Inc. | About | Contact Us | Careers at Law360 …".
_PAGE_FOOTER_RE = re.compile(
    r"(©\s*\d{4}|copyright\s+©|all rights reserved|terms (of use|& conditions)|"
    r"privacy policy|cookie (settings|policy)|ad choices|site map|"
    r"accessibility statement|do not sell my|subscribe to our newsletter)",
    re.IGNORECASE,
)


# A publisher's own recruitment copy. It sits inside a press release feed and
# reads like prose, so a measured YKK run filed "2027年卒の学生の皆様を対象に
# ビジネス職の1Dayインターンシップを開催します" as a hard fact about the
# company — it is the wire's own hiring advert. The URL is the cheap test, but
# the wire served it under an article path, so the text is checked as well.
_RECRUITMENT_AD_RE = re.compile(
    r"(インターンシップ|インターン|新卒採用|中途採用|"
    r"career\s+opportunit|join\s+our\s+team|we\s+are\s+hiring|apply\s+now|"
    r"open\s+positions?|job\s+openings?|recruit(?:ing|ment)\s+(?:site|page|event))",
    re.IGNORECASE,
)


def is_structural_noise(text: str) -> bool:
    """True when a block is document structure rather than a statement.

    Narrow on purpose. It rejects a block only when the whole block is
    formatting or vendor copy, so a sentence that happens to sit inside a table
    cell still survives: an earnings sentence in a table is the earnings
    sentence, and dropping it would lose the fact rather than the noise.
    """
    stripped = text.strip()
    if not stripped:
        return True

    # A block that is entirely a table rule ("| --- | --- |").
    if _TABLE_RULE_RE.match(stripped):
        return True

    # A block delimited by pipes at both ends is a table row, whatever its
    # length: "| North America | 70% DTC, 20% Wholesale, 10% Other |" has only
    # three cells and is still furniture, not a sentence.
    if stripped.startswith("|") and stripped.endswith("|") and not _has_sentence(stripped):
        return True

    # A block that is mostly pipes, with too little prose to state anything.
    pipe_count = stripped.count("|")
    if pipe_count >= 4 and len(stripped) < 240 and not _has_sentence(stripped):
        return True

    # A block made mostly of table rows. The per-line forms above only catch a
    # table that arrives as one row or as a short one; a comparison table page
    # arrives as thirty pipe rows joined into a single block, and its length
    # defeated the length test. Measured on YKK: a Japanese brokerage-comparison
    # page was filed as one hard fact running to thousands of characters.
    if _is_table_block(stripped):
        return True

    if _MARKETING_RE.search(stripped):
        return True

    if _FILING_BOILERPLATE_RE.search(stripped):
        return True

    if _is_navigation_rail(stripped):
        return True

    if _PAGE_FOOTER_RE.search(stripped):
        return True

    if _RECRUITMENT_AD_RE.search(stripped):
        return True

    return False


def _has_sentence(text: str) -> bool:
    """True when the block carries prose a reader could quote as a fact."""
    words = re.findall(r"[A-Za-z]{3,}", text)
    if len(words) < 8:
        return False
    # A sentence needs a verb-like word; this is a coarse test, deliberately
    # permissive so it never removes real reporting.
    return any(
        word.lower().endswith(("ed", "ing", "s"))
        for word in words
    ) or " is " in text.lower() or " was " in text.lower()


# Prose line inside a table block. A table can carry a sentence in one cell, and
# that sentence is still reporting — what makes the block furniture is that most
# of its lines are rows.
_TABLE_PROSE_LINE_CHARS = 200

# Only a page-scale table is furniture. A five-row table with a sentence in it is
# an article that happens to be tabulated, and rejecting it would delete the
# statement rather than the formatting.
_TABLE_BLOCK_MIN_ROWS = 8
_TABLE_BLOCK_SHORT_ROW_RATIO = 2


def _is_table_block(text: str) -> bool:
    """True when a page-scale block is mostly short table rows.

    Counted per line rather than by separator density so a prose cell survives:
    that cell is a statement, and dropping it would lose the fact rather than the
    noise. A comparison page is mostly commodity rows — fees, counts, tick marks —
    with a handful of descriptive cells, so the short rows dominate.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    rows = [line for line in lines if line.startswith("|")]
    if len(rows) < _TABLE_BLOCK_MIN_ROWS:
        return False
    prose = [
        line
        for line in rows
        if len(line) >= _TABLE_PROSE_LINE_CHARS or _has_sentence(line)
    ]
    return len(rows) > _TABLE_BLOCK_SHORT_ROW_RATIO * max(1, len(prose))


def source_tier(url: str) -> int:
    """Rank a source by credibility tier; unknown hosts rank last."""
    host = host_of(url)
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
    host = host_of(url)
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
