# /pre-meeting-agent/services/ingestion.py
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from pydantic import BaseModel, HttpUrl, ValidationError
from tenacity import retry, stop_after_attempt, wait_random_exponential
from trafilatura import extract
from tavily import TavilyClient


# =============================================================================
# 1. Data contracts: input and output models
# =============================================================================

class BriefingRequest(BaseModel):
    """Input model for a pre-meeting briefing request."""

    company_name: str
    meeting_time: datetime
    meeting_purpose: str


class FactItem(BaseModel):
    """A single fact or opinion item.

    The content field must be an excerpt from the source text or a short
    sentence tightly bound to the source text.
    """

    content: str
    source_url: HttpUrl
    publish_date: Optional[datetime] = None


class FactBase(BaseModel):
    """Core output of the ingestion layer."""

    company_id: str
    last_refresh_date: datetime
    hard_facts: List[FactItem]
    analyst_opinions: List[FactItem]
    conflicts: List[dict] = []


# =============================================================================
# 2. Custom exception
# =============================================================================

class TavilySearchError(Exception):
    """Raised when Tavily search fails after retries."""


# =============================================================================
# 3. Constants
# =============================================================================

INCLUDE_DOMAINS = ["reuters.com", "bloomberg.com", "wsj.com", "cnbc.com"]

MIN_CLEAN_TEXT_LENGTH = 100


# =============================================================================
# 4. Date window calculation
# =============================================================================

def _calculate_date_window(
    last_refresh_date: Optional[datetime] = None,
) -> Tuple[str, str]:
    """Calculate the Tavily search window in YYYY-MM-DD format.

    Rules:
    1. The total window is fixed at 30 days.
    2. If last_refresh_date exists and is less than 30 days ago:
       start_date = last_refresh_date, end_date = today.
    3. If last_refresh_date is missing or at least 30 days ago:
       start_date = today - 30 days, end_date = today.

    Persistence of last_refresh_date is handled by the FastAPI/service layer.
    The current BriefingRequest contract does not include last_refresh_date,
    so the main function treats it as missing and always looks back 30 days.
    """
    today = datetime.now(timezone.utc).date()

    if last_refresh_date is not None:
        if last_refresh_date.tzinfo is None:
            last_date = last_refresh_date.date()
        else:
            last_date = last_refresh_date.astimezone(timezone.utc).date()

        days_since_last_refresh = (today - last_date).days
        if 0 <= days_since_last_refresh < 30:
            return last_date.isoformat(), today.isoformat()

    start_date = today - timedelta(days=30)
    return start_date.isoformat(), today.isoformat()


# =============================================================================
# 5. Tavily search with exponential backoff retry
# =============================================================================

@retry(
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True,
)
def _tavily_search_with_retry(
    query: str,
    start_date: str,
    end_date: str,
) -> List[dict]:
    """Call Tavily search with exponential backoff retry.

    Retries at most 3 times. On final failure, TavilySearchError is raised
    instead of silently returning an empty result.
    """
    try:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise RuntimeError("TAVILY_API_KEY environment variable is missing")

        client = TavilyClient(api_key=api_key)

        response = client.search(
            query=query,
            search_depth="advanced",
            include_raw_content=True,
            start_date=start_date,
            end_date=end_date,
            include_domains=INCLUDE_DOMAINS,
        )

        if not isinstance(response, dict):
            return []

        results = response.get("results", [])
        if not isinstance(results, list):
            return []

        return results

    except Exception as exc:
        raise TavilySearchError(
            f"Tavily search failed "
            f"(query={query!r}, start_date={start_date}, end_date={end_date}): {exc}"
        ) from exc


# =============================================================================
# 6. Trafilatura text extraction
# =============================================================================

def _extract_clean_text(raw_content: Optional[str]) -> Optional[str]:
    """Extract clean text from raw_content using Trafilatura.

    Returns None when extraction fails or the result is shorter than
    MIN_CLEAN_TEXT_LENGTH characters.
    """
    if not raw_content or not isinstance(raw_content, str):
        return None

    try:
        clean_text = extract(raw_content, output_format="markdown")
    except Exception:
        return None

    if not clean_text:
        return None

    clean_text = clean_text.strip()
    if len(clean_text) < MIN_CLEAN_TEXT_LENGTH:
        return None

    return clean_text


# =============================================================================
# 7. Inverted-pyramid structural split
# =============================================================================

def _first_sentence(paragraph: str) -> str:
    """Return the first sentence of a paragraph without semantic rewriting."""
    paragraph = paragraph.strip()
    if not paragraph:
        return ""

    parts = re.split(r"(?<=[\u3002\uFF01\uFF1F.!?])\s*", paragraph, maxsplit=1)
    first = parts[0].strip() if parts else paragraph

    return first or paragraph


def _split_facts_and_opinions(clean_text: str) -> Tuple[List[str], List[str]]:
    """Split clean text by inverted-pyramid structure.

    - The first 3 paragraphs plus the last paragraph go to hard facts.
    - The first sentence of each middle paragraph goes to analyst opinions.

    This is a pure structural split. No semantic judgment, summarization,
    rewriting, or conflict detection is performed here.
    """
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n+", clean_text)
        if paragraph.strip()
    ]

    if not paragraphs:
        return [], []

    paragraph_count = len(paragraphs)

    hard_indices = list(range(min(3, paragraph_count)))

    last_index = paragraph_count - 1
    if last_index not in hard_indices:
        hard_indices.append(last_index)

    hard_indices_set = set(hard_indices)

    hard_texts = [paragraphs[index] for index in hard_indices if paragraphs[index]]

    opinion_texts: List[str] = []
    for index, paragraph in enumerate(paragraphs):
        if index in hard_indices_set:
            continue

        first_sentence = _first_sentence(paragraph)
        if first_sentence:
            opinion_texts.append(first_sentence)

    return hard_texts, opinion_texts


# =============================================================================
# 8. Helpers: date parsing, FactItem construction, company_id generation
# =============================================================================

def _parse_publish_date(value: object) -> Optional[datetime]:
    """Parse Tavily published_date as best effort.

    Returns None when parsing fails. A bad date does not discard the whole
    result.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    if not isinstance(value, str):
        return None

    value = value.strip()
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_fact_item(
    content: str,
    source_url: object,
    publish_date: Optional[datetime],
) -> Optional[FactItem]:
    """Build a FactItem and validate source_url via HttpUrl.

    Invalid URLs or validation failures cause the item to be dropped.
    """
    if not content or not source_url:
        return None

    try:
        return FactItem(
            content=content,
            source_url=source_url,
            publish_date=publish_date,
        )
    except ValidationError:
        return None


def _build_company_id(company_name: str) -> str:
    """Build a stable company_id with a readable slug and a hash suffix."""
    normalized = company_name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]

    if slug:
        return f"{slug}-{digest}"

    return f"company-{digest}"


# =============================================================================
# 9. Main entry: ingest, clean, structure, validate
# =============================================================================

async def ingest_company_data(request: BriefingRequest) -> FactBase:
    """Ingest company data and return a validated FactBase.

    Steps:
    1. Calculate start_date and end_date.
    2. Build the Tavily query and call the retry-enabled search function.
    3. Extract clean text from each raw_content with Trafilatura.
    4. Split clean text by inverted-pyramid structure.
    5. Store results separately in hard_facts and analyst_opinions.
    6. Validate and return the result with FactBase.model_validate().
    """
    last_refresh_date: Optional[datetime] = None
    start_date, end_date = _calculate_date_window(last_refresh_date)

    query = f"{request.company_name} news OR hires OR launches OR legal risk"

    results = await asyncio.to_thread(
        _tavily_search_with_retry,
        query,
        start_date,
        end_date,
    )

    hard_facts: List[FactItem] = []
    analyst_opinions: List[FactItem] = []

    for result in results:
        if not isinstance(result, dict):
            continue

        raw_content = result.get("raw_content")
        source_url = result.get("url")
        publish_date = _parse_publish_date(result.get("published_date"))

        clean_text = _extract_clean_text(raw_content)
        if not clean_text:
            continue

        hard_texts, opinion_texts = _split_facts_and_opinions(clean_text)

        for text in hard_texts:
            fact_item = _build_fact_item(text, source_url, publish_date)
            if fact_item is not None:
                hard_facts.append(fact_item)

        for text in opinion_texts:
            opinion_item = _build_fact_item(text, source_url, publish_date)
            if opinion_item is not None:
                analyst_opinions.append(opinion_item)

    payload = {
        "company_id": _build_company_id(request.company_name),
        "last_refresh_date": datetime.now(timezone.utc),
        "hard_facts": hard_facts,
        "analyst_opinions": analyst_opinions,
        "conflicts": [],
    }

    return FactBase.model_validate(payload)
