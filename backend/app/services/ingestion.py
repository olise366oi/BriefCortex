# /backend/app/services/ingestion.py
"""Tavily retrieval orchestration: search, clean, structure, validate."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from pydantic import BaseModel, HttpUrl, ValidationError
from tenacity import retry, stop_after_attempt, wait_random_exponential
from tavily import TavilyClient

from app.services.fact_extraction import (
    dedup_key,
    extract_hard_facts,
    order_by_publisher,
    pick_richer,
    publisher_of,
)
from app.services.freshness import (
    is_stale_period,
    parse_publish_date,
    resolve_publish_dates,
)
from app.services.logger_service import get_logger
from app.services.publisher_filter import (
    extract_clean_text,
    filter_results_for_company,
    host_of,
)
from app.services.search_planner import SearchPlan, plan_searches

logger = get_logger("ingestion")


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


class TavilySearchError(Exception):
    """Raised when Tavily search fails after retries."""


# Tavily returns 10 results by default and accepts up to 20. Above 20 the
# response is unreliable (measured: 25 -> 16, 30 -> 1, 40 -> 10), so 20 is the
# hard ceiling and additional coverage comes from running more queries.
TAVILY_MAX_RESULTS = 20

# The loop exits early once a round stops contributing new usable sources, so a
# generous ceiling costs nothing when coverage is already saturated.
MAX_SEARCH_ROUNDS = 20
MAX_EXTRA_QUERIES = 6

# A round is judged by URLs that carry an article body, not by raw hit count,
# because a round of quote shells adds nothing to the fact base.
MIN_USABLE_PER_ROUND = 1

WINDOW_DAYS = 30


def _calculate_date_window(
    last_refresh_date: Optional[datetime] = None,
) -> Tuple[str, str]:
    """Return the Tavily search window as (start_date, end_date) in ISO format.

    The window is 30 days. A last_refresh_date less than 30 days ago shortens it
    so a re-run only looks at new material; otherwise it looks back the full
    window. Persistence of last_refresh_date belongs to the service layer, so
    this always falls back to the full window today.
    """
    today = datetime.now(timezone.utc).date()

    if last_refresh_date is not None:
        if last_refresh_date.tzinfo is None:
            last_date = last_refresh_date.date()
        else:
            last_date = last_refresh_date.astimezone(timezone.utc).date()

        days_since_last_refresh = (today - last_date).days
        if 0 <= days_since_last_refresh < WINDOW_DAYS:
            return last_date.isoformat(), today.isoformat()

    return (today - timedelta(days=WINDOW_DAYS)).isoformat(), today.isoformat()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True,
)
def _tavily_search(
    query: str,
    start_date: str,
    end_date: str,
    company_name: str,
    domains: List[str],
) -> List[dict]:
    """Run one Tavily query, retrying transient failures with backoff."""
    try:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise RuntimeError("TAVILY_API_KEY environment variable is missing")

        logger.info(
            "Tavily search | query=%r | window=%s..%s | domains=%s | max=%s",
            query,
            start_date,
            end_date,
            domains,
            TAVILY_MAX_RESULTS,
        )
        client = TavilyClient(api_key=api_key)

        response = client.search(
            query=query,
            search_depth="advanced",
            include_raw_content=True,
            start_date=start_date,
            end_date=end_date,
            include_domains=domains,
            topic="news",
            max_results=TAVILY_MAX_RESULTS,
        )

        if not isinstance(response, dict):
            logger.warning("Tavily returned non-dict response")
            return []

        results = response.get("results", [])
        if not isinstance(results, list):
            logger.warning("Tavily results were not a list")
            return []

        kept = filter_results_for_company(results, company_name)
        logger.info("Filter kept %s of %s results", len(kept), len(results))
        return kept

    except Exception as exc:
        logger.exception(
            "Tavily search failed | query=%r | window=%s..%s",
            query,
            start_date,
            end_date,
        )
        raise TavilySearchError(
            f"Tavily search failed (query={query!r}, "
            f"start_date={start_date}, end_date={end_date}): {exc}"
        ) from exc


async def _search_query(
    query: str,
    domains: List[str],
    start_date: str,
    end_date: str,
    company_name: str,
) -> List[dict]:
    return await asyncio.to_thread(
        _tavily_search, query, start_date, end_date, company_name, domains
    )


def _usable_count(batch: List[dict]) -> int:
    """Count results that actually carry an article body."""
    return sum(1 for result in batch if (result.get("raw_content") or "").strip())


def _merge_batch(merged: List[dict], seen_urls: set, batch: List[dict]) -> int:
    """Append new URLs from a batch and return how many were added."""
    added = 0
    for result in batch:
        url = str(result.get("url") or "").strip().rstrip("/")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        merged.append(result)
        added += 1
    return added


async def _run_round(
    queries: List[str],
    domains: List[str],
    start_date: str,
    end_date: str,
    company_name: str,
    merged: List[dict],
    seen_urls: set,
) -> Tuple[int, int]:
    """Run a round of queries concurrently and fold the results in."""
    batches = await asyncio.gather(
        *[
            _search_query(query, domains, start_date, end_date, company_name)
            for query in queries
        ]
    )

    usable = 0
    added = 0
    for batch in batches:
        usable += _usable_count(batch)
        added += _merge_batch(merged, seen_urls, batch)

    return usable, added


async def _refill_missing_bodies(
    merged: List[dict],
    plan: SearchPlan,
    start_date: str,
    end_date: str,
    company_name: str,
) -> List[dict]:
    """Re-run the queries without the publishers that returned no body.

    Some publishers answer with a page shell, so their stories would otherwise
    be lost. The empty-body hosts observed in this run are dropped and the same
    queries re-run, letting a syndicated telling take the slot.
    """
    empty_hosts = {
        host_of(str(result.get("url") or ""))
        for result in merged
        if not (result.get("raw_content") or "").strip()
    }
    if not empty_hosts:
        return merged

    retry_domains = [domain for domain in plan.domains if domain not in empty_hosts]
    if not retry_domains:
        logger.info("Body refill skipped | every planned publisher was empty")
        return merged

    logger.info(
        "Bodies missing | results=%s | with_body=%s | empty=%s | retry_with=%s",
        len(merged),
        _usable_count(merged),
        sorted(empty_hosts),
        retry_domains,
    )

    seen_urls = {str(r.get("url") or "").strip().rstrip("/") for r in merged}
    _, added = await _run_round(
        plan.queries,
        retry_domains,
        start_date,
        end_date,
        company_name,
        merged,
        seen_urls,
    )
    logger.info("Body refill | new_results=%s | total=%s", added, len(merged))
    return merged


async def _search_plan(
    plan: SearchPlan,
    start_date: str,
    end_date: str,
    company_name: str,
) -> List[dict]:
    """Run the planned queries, then keep searching until a round adds nothing."""
    merged: List[dict] = []
    seen_urls: set = set()

    usable, added = await _run_round(
        plan.queries, plan.domains, start_date, end_date, company_name,
        merged, seen_urls,
    )
    logger.info(
        "Search round 1 | queries=%s | usable=%s | new_urls=%s | total=%s",
        len(plan.queries), usable, added, len(merged),
    )

    merged = await _refill_missing_bodies(
        merged, plan, start_date, end_date, company_name
    )

    round_index = 1
    pending = list(plan.fallback_queries)

    while (
        round_index < MAX_SEARCH_ROUNDS
        and usable >= MIN_USABLE_PER_ROUND
        and pending
    ):
        round_index += 1
        take = pending[:MAX_EXTRA_QUERIES]
        pending = pending[MAX_EXTRA_QUERIES:]

        usable, added = await _run_round(
            take, plan.domains, start_date, end_date, company_name,
            merged, seen_urls,
        )
        logger.info(
            "Search round %s | queries=%s | usable=%s | new_urls=%s | total=%s",
            round_index, len(take), usable, added, len(merged),
        )

        merged = await _refill_missing_bodies(
            merged, plan, start_date, end_date, company_name
        )

    logger.info(
        "Search plan executed | rounds=%s | merged=%s | fallback_exhausted=%s",
        round_index, len(merged), not pending,
    )
    return merged


def _build_fact_item(
    content: str,
    source_url: object,
    publish_date: Optional[datetime],
) -> Optional[FactItem]:
    """Build a FactItem, validating source_url through HttpUrl."""
    if not content or not source_url:
        return None

    try:
        return FactItem(
            content=content, source_url=source_url, publish_date=publish_date
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


def _collect_hard_facts(
    results: List[dict],
    window_start: str,
) -> Tuple[List[FactItem], dict]:
    """Read every result and return the deduplicated hard facts plus counters."""
    hard_facts: List[FactItem] = []
    fact_index: dict[str, int] = {}
    stats = {
        "extracted": 0,
        "skipped": 0,
        "duplicate": 0,
        "stale_source": 0,
        "stale_fact": 0,
    }
    seen_publishers: set[str] = set()

    for result in results:
        if not isinstance(result, dict):
            stats["skipped"] += 1
            continue

        source_url = str(result.get("url") or "")
        publish_date = parse_publish_date(result.get("published_date"))

        publisher = publisher_of(source_url)
        if publisher not in seen_publishers:
            seen_publishers.add(publisher)
            logger.info(
                "Reading publisher #%s | %s | accumulated=%s",
                len(seen_publishers), publisher, len(hard_facts),
            )

        clean_text = extract_clean_text(result.get("raw_content"), source_url)
        if not clean_text:
            stats["skipped"] += 1
            logger.info("Skipped source (no clean text) | url=%s", source_url)
            continue

        stats["extracted"] += 1
        texts = extract_hard_facts(clean_text)

        fresh: List[str] = []
        for text in texts:
            if is_stale_period(text, window_start):
                stats["stale_fact"] += 1
                continue
            fresh.append(text)

        if texts and not fresh:
            stats["stale_source"] += 1
            logger.info("Skipped source (only stale periods) | url=%s", source_url)
            continue

        logger.info(
            "Extracted source | url=%s | paragraphs=%s | stale_dropped=%s",
            source_url, len(fresh), len(texts) - len(fresh),
        )

        added = 0
        replaced = 0
        for text in fresh:
            key = dedup_key(text)
            fact_item = _build_fact_item(text, source_url, publish_date)
            if fact_item is None:
                continue

            existing_index = fact_index.get(key)
            if existing_index is None:
                fact_index[key] = len(hard_facts)
                hard_facts.append(fact_item)
                added += 1
                continue

            if pick_richer(hard_facts[existing_index].content, text):
                hard_facts[existing_index] = fact_item
                replaced += 1

        if added == 0 and replaced == 0:
            stats["duplicate"] += 1
            logger.info("Skipped source (duplicate content) | url=%s", source_url)
        elif replaced:
            logger.info(
                "Richer duplicate replaced earlier fact | url=%s | replaced=%s",
                source_url, replaced,
            )

    return hard_facts, stats


async def ingest_company_data(request: BriefingRequest) -> FactBase:
    """Ingest company data and return a validated FactBase.

    analyst_opinions stays empty on purpose: commentator framing must not
    reach the LLM, so the report is generated from hard facts alone.
    """
    logger.info(
        "Ingestion started | company=%r | purpose=%r | meeting_time=%s",
        request.company_name,
        request.meeting_purpose,
        request.meeting_time.isoformat(),
    )

    start_date, end_date = _calculate_date_window()
    logger.info("Ingestion date window | start=%s | end=%s", start_date, end_date)

    company_label = request.company_name.strip()
    plan = await plan_searches(company_label, request.meeting_purpose.strip())
    logger.info(
        "Search plan | industry=%s | categories=%s | fundamentals=%s",
        plan.industry, plan.categories, plan.fundamentals,
    )

    results = await _search_plan(plan, start_date, end_date, company_label)
    results = await resolve_publish_dates(results)
    results = order_by_publisher(results)

    hard_facts, stats = _collect_hard_facts(results, start_date)

    fact_base = FactBase.model_validate(
        {
            "company_id": _build_company_id(request.company_name),
            "last_refresh_date": datetime.now(timezone.utc),
            "hard_facts": hard_facts,
            "analyst_opinions": [],
            "conflicts": [],
        }
    )

    from .conflict_detection import detect_fact_conflicts

    fact_base.conflicts = detect_fact_conflicts(fact_base)

    logger.info(
        "Ingestion complete | company_id=%s | extracted=%s | skipped=%s | "
        "duplicate=%s | stale_source=%s | stale_fact=%s | hard_facts=%s | "
        "conflicts=%s",
        fact_base.company_id,
        stats["extracted"],
        stats["skipped"],
        stats["duplicate"],
        stats["stale_source"],
        stats["stale_fact"],
        len(fact_base.hard_facts),
        len(fact_base.conflicts),
    )
    return fact_base
