# /backend/app/services/ingestion.py
"""Tavily retrieval orchestration: search, clean, structure, validate."""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import date, datetime, timezone
from typing import List, Optional, Tuple

from pydantic import ValidationError

from app.models.briefing import BriefingRequest
from app.models.fact_base import CoverageRecord, FactBase, FactItem
from app.services.fact_extraction import (
    dedup_key,
    extract_analyst_opinions,
    extract_hard_facts,
    find_prefix_twin,
    order_by_publisher,
    publisher_of,
)
from app.services.freshness import (
    is_stale_period,
    parse_publish_date,
    resolve_publish_dates,
)
from app.services.forward_looking import split_forward_looking
from app.services.market_data import fetch_quote
from app.services.article_subject import classify_article_subjects
from app.services.company_identity import (
    disambiguate_with_model,
    jurisdiction_from_description,
    needs_caller_choice,
    rank_candidates,
    resolve_identity,
)
from app.services.coverage import build_coverage_report
from app.services.corroboration import corroborate_incidental_facts
from app.services.dedup import drop_duplicate_facts, merge_duplicate_facts
from app.services.entity_disambiguation import filter_by_entity
from app.services.hiring import (
    LINKEDIN_JOB_DOMAINS,
    hiring_queries,
    is_careers_url,
    is_hiring_line,
    linkedin_hiring_queries,
)
from app.services.incidental import opening_sentence
from app.services.job_postings import JobPosting, postings_from_url
from app.services.keywords import match_word
from app.services.urls import host_of
from app.services.incidental_harvest import harvest_incidental_facts
from app.services.legal_signals import legal_queries
from app.services.logger_service import get_logger
from app.services.publisher_filter import extract_clean_text, host_of
from app.services.search_planner import SearchPlan, plan_searches
from app.services.tavily_search import extract_bodies, tavily_search
from app.services.window import compute_evidence_window

logger = get_logger("ingestion")


# The loop exits once a round contributes no new usable URL, so the round cap is
# only a guard against a runaway plan and rarely reached.
MAX_SEARCH_ROUNDS = 8
MAX_EXTRA_QUERIES = 6

# Wall-clock budget for retrieval. The UI polls with its own timeout, so the
# search layer stops planning new rounds rather than letting a request outlive
# the client.
SEARCH_BUDGET_SECONDS = 45.0

# A round is judged by URLs that carry an article body, not by raw hit count,
# because a round of quote shells adds nothing to the fact base.
MIN_USABLE_PER_ROUND = 1

# Retrieval stops after this many consecutive rounds that produced nothing new
# compared with both the URLs seen this run and the URLs already stored. One
# fully-duplicate round can be a reordering; two in a row means the queries are
# exhausted.
MAX_STALE_ROUNDS = 2

# Once this many results already carry a body, the body refill is skipped: it
# re-runs a full round of queries and mostly returns the same shells again.
REFILL_MIN_USABLE_RESULTS = 8

# A career page line shorter than this is a label ("Careers", "Filter by")
# rather than a role worth reading.
MIN_POSTING_LINE_CHARS = 20

# Cap roles per source so one aggregator page cannot fill the hiring block.
MAX_POSTINGS_PER_SOURCE = 10

async def _search_query(
    query: str,
    domains: List[str],
    start_date: str,
    end_date: str,
    company_name: str,
    aliases: Optional[List[str]] = None,
    dated: bool = True,
) -> List[dict]:
    return await asyncio.to_thread(
        tavily_search, query, start_date, end_date, company_name, domains, aliases, dated
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
    aliases: Optional[List[str]] = None,
    dated: bool = True,
) -> Tuple[List[dict], int, int]:
    """Run a round of queries concurrently and fold the results in.

    ``seen_urls`` already contains every URL stored from earlier runs, so a
    result the company has already been briefed on is not counted as new.
    Returns (results_kept, usable_with_body, newly_added). The kept results are
    returned as well so independent lanes can be merged by the caller after they
    finish, instead of racing over one shared list.
    """
    batches = await asyncio.gather(
        *[
            _search_query(query, domains, start_date, end_date, company_name, aliases, dated)
            for query in queries
        ]
    )

    kept: List[dict] = []
    usable = 0
    added = 0
    for batch in batches:
        usable += _usable_count(batch)
        added += _merge_batch(merged, seen_urls, batch)
        kept.extend(batch)

    return kept, usable, added


async def _refill_missing_bodies(
    merged: List[dict],
    plan: SearchPlan,
    start_date: str,
    end_date: str,
    company_name: str,
    aliases: Optional[List[str]] = None,
) -> List[dict]:
    """Recover the results that arrived without a body.

    Some publishers answer a search with a page shell, so their stories would
    otherwise be lost. Two repairs run in order: the empty-body URLs are
    fetched directly first, because a targeted extract recovers the page that
    the search result already points at (measured: 1 of 1 shell recovered,
    24892 chars). Only if that leaves coverage short are the empty-body hosts
    dropped and the queries re-run, letting a syndicated telling take the slot.

    This is a one-shot repair: it runs only once per request and only when
    bodies are genuinely missing, because every run costs Tavily calls.
    """
    empty = [
        str(result.get("url") or "")
        for result in merged
        if not (result.get("raw_content") or "").strip()
    ]
    empty_urls = [url for url in empty if url]
    if not empty_urls:
        return merged

    recovered = await asyncio.to_thread(extract_bodies, empty_urls)
    if recovered:
        filled = 0
        for result in merged:
            url = str(result.get("url") or "")
            if (result.get("raw_content") or "").strip():
                continue
            body = recovered.get(url)
            if body:
                result["raw_content"] = body
                filled += 1
        logger.info(
            "Body recovery | requested=%s | recovered=%s | filled=%s",
            len(empty_urls),
            len(recovered),
            filled,
        )

    empty_hosts = {
        host_of(str(result.get("url") or ""))
        for result in merged
        if not (result.get("raw_content") or "").strip()
    }
    if not empty_hosts:
        return merged

    with_body = _usable_count(merged)
    if with_body >= REFILL_MIN_USABLE_RESULTS:
        logger.info(
            "Body refill skipped | coverage already sufficient | with_body=%s | empty=%s",
            with_body,
            sorted(empty_hosts),
        )
        return merged

    retry_domains = [domain for domain in plan.domains if domain not in empty_hosts]
    if not retry_domains:
        logger.info("Body refill skipped | every planned publisher was empty")
        return merged

    logger.info(
        "Bodies missing | results=%s | with_body=%s | empty=%s | retry_with=%s",
        len(merged),
        with_body,
        sorted(empty_hosts),
        retry_domains,
    )

    seen_urls = {str(r.get("url") or "").strip().rstrip("/") for r in merged}
    _kept, _usable, added = await _run_round(
        plan.queries,
        retry_domains,
        start_date,
        end_date,
        company_name,
        merged,
        seen_urls,
        aliases,
    )
    logger.info("Body refill | new_results=%s | total=%s", added, len(merged))
    return merged


async def _search_plan(
    plan: SearchPlan,
    start_date: str,
    end_date: str,
    company_name: str,
    known_urls: Optional[set] = None,
    open_queries: Optional[List[str]] = None,
    aliases: Optional[List[str]] = None,
    linkedin_queries: Optional[List[str]] = None,
) -> List[dict]:
    """Run the planned queries until two consecutive rounds bring nothing new.

    Freshness is measured against everything already known: the URLs collected
    during this run and the URLs stored by earlier runs for the same company.
    A single repeat round can just be result reordering, so retrieval continues
    through one of them and stops only when the next round repeats as well.

    ``open_queries`` run without the publisher registry, because the source
    registry lists news outlets and some answers only exist outside them: a
    regulator's own case page is not a publisher. Their results enter the same
    dedup pool, so an outlet telling is still preferred by publisher priority.

    Two budgets also apply: MAX_SEARCH_ROUNDS guards a runaway plan, and
    SEARCH_BUDGET_SECONDS keeps the request inside the client's poll window.
    """
    merged: List[dict] = []
    seen_urls: set = set(known_urls or set())
    known_before = set(seen_urls)
    started_at = time.monotonic()

    # Each lane folds into its own list; the lists are merged once afterwards.
    lanes = [
        _run_round(
            plan.queries, plan.domains, start_date, end_date, company_name,
            [], set(seen_urls), aliases,
        )
    ]
    lane_labels = ["news"]

    if open_queries:
        # Open-domain answers are structural, not news: a regulator's case page
        # and a careers listing carry no publish date, so a dated search drops
        # all of them. Both lanes therefore run undated.
        lanes.append(
            _run_round(
                open_queries, [], start_date, end_date, company_name,
                [], set(seen_urls), aliases, dated=False,
            )
        )
        lane_labels.append("open")

    if linkedin_queries:
        # A job board lists roles that no press release covers, so the hiring
        # lane reads LinkedIn directly as well. It runs against a fixed domain
        # list and undated, because a posting has no publish date.
        lanes.append(
            _run_round(
                linkedin_queries, list(LINKEDIN_JOB_DOMAINS), start_date, end_date,
                company_name, [], set(seen_urls), aliases, dated=False,
            )
        )
        lane_labels.append("linkedin")

    lane_results = await asyncio.gather(*lanes)

    usable = 0
    added = 0
    for label, (lane_kept, lane_usable, _lane_added) in zip(lane_labels, lane_results):
        usable += lane_usable
        merged_added = _merge_batch(merged, seen_urls, lane_kept)
        added += merged_added
        logger.info(
            "Lane complete | lane=%s | usable=%s | unique_added=%s | total=%s",
            label,
            lane_usable,
            merged_added,
            len(merged),
        )

    logger.info(
        "Opening lanes ran in parallel | lanes=%s | usable=%s | new_urls=%s "
        "| known_before=%s | total=%s | elapsed=%.1fs",
        lane_labels, usable, added, len(known_before), len(merged),
        time.monotonic() - started_at,
    )

    merged = await _refill_missing_bodies(
        merged, plan, start_date, end_date, company_name, aliases
    )

    round_index = 1
    stale_rounds = 0 if added else 1
    pending = list(plan.fallback_queries)

    while (
        round_index < MAX_SEARCH_ROUNDS
        and usable >= MIN_USABLE_PER_ROUND
        and pending
        and stale_rounds < MAX_STALE_ROUNDS
        and time.monotonic() - started_at < SEARCH_BUDGET_SECONDS
    ):
        round_index += 1
        take = pending[:MAX_EXTRA_QUERIES]
        pending = pending[MAX_EXTRA_QUERIES:]

        _kept, usable, added = await _run_round(
            take, plan.domains, start_date, end_date, company_name,
            merged, seen_urls, aliases,
        )
        stale_rounds = 0 if added else stale_rounds + 1

        logger.info(
            "Search round %s | queries=%s | usable=%s | new_urls=%s | stale_streak=%s "
            "| total=%s | elapsed=%.1fs",
            round_index, len(take), usable, added, stale_rounds, len(merged),
            time.monotonic() - started_at,
        )

    logger.info(
        "Search plan executed | rounds=%s | merged=%s | pending_queries=%s "
        "| stale_streak=%s | elapsed=%.1fs",
        round_index, len(merged), len(pending), stale_rounds,
        time.monotonic() - started_at,
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


def title_names_company(result: dict, company: str, aliases: Optional[List[str]] = None) -> bool:
    """True when the publisher's own headline names the company being briefed.

    The headline is the publisher's statement of what the article is about, so it
    settles the subject when the body is ambiguous. Word boundaries are used so
    "NVIDIA" does not match inside a longer token, and the registry legal name is
    accepted as well, because a headline often uses the formal name.
    """
    title = " ".join(str(result.get("title") or "").split())
    if not title:
        return False

    names = [company.strip()]
    for alias in aliases or []:
        alias_text = (alias or "").strip()
        if alias_text and alias_text.lower() != company.strip().lower():
            names.append(alias_text)

    lowered = title.lower()
    for name in names:
        clean = name.lower()
        if not clean:
            continue
        if clean.isascii():
            pattern = rf"(?<![a-z0-9]){re.escape(clean)}(?![a-z0-9])"
            if re.search(pattern, lowered):
                return True
        elif clean in lowered:
            return True
    return False


async def _route_and_collect(
    results: List[dict],
    window_start: str,
    company: str,
    company_description: str = "",
    aliases: Optional[List[str]] = None,
) -> Tuple[List[FactItem], List[FactItem], List[FactItem], List[dict], dict]:
    """Route each article by its subject, then read it accordingly.

    An article whose subject is us writes our fact base. An article about someone
    else is not thrown away: it is returned so the incidental stage can mine the
    sentences in it that are about us. Routing on subject rather than on "did the
    name appear" is what stops a competitor's results being filed as ours.

    Careers pages skip routing entirely: a posting has no article subject, so
    asking the subject classifier about it wastes a call and always answers
    wrong.

    ``company_description`` is passed to the classifier so a shared name can be
    split: two businesses both called Instinct publish their own numbers, and only
    the caller's description says which one the brief is about.
    """
    prepared: List[dict] = []
    postings_entries: List[dict] = []

    # Careers pages need the page's structured markup, which the searched body
    # does not contain: it is a JavaScript shell. They are fetched directly and
    # in parallel, so a board listing cannot stall the run.
    careers_entries: List[dict] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "")
        if not url:
            continue
        if is_careers_url(url):
            careers_entries.append(
                {
                    "result": result,
                    "url": url,
                    "clean_text": extract_clean_text(result.get("raw_content"), url) or "",
                }
            )

    if careers_entries:
        fetched = await asyncio.gather(
            *[asyncio.to_thread(postings_from_url, entry["url"]) for entry in careers_entries]
        )
        for entry, postings_found in zip(careers_entries, fetched):
            entry["postings"] = postings_found
            if postings_found:
                postings_entries.append(entry)
            elif entry["clean_text"]:
                # No structured data, but the page does carry readable text.
                postings_entries.append(entry)

    for result in results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "")
        if is_careers_url(url):
            continue
        clean_text = extract_clean_text(result.get("raw_content"), url)
        if not clean_text:
            continue
        prepared.append({"result": result, "url": url, "clean_text": clean_text})

    routes = await classify_article_subjects(
        [opening_sentence(item["clean_text"]) for item in prepared],
        company,
        company_description,
    )
    by_index = {route.index: route for route in routes}

    own_articles: List[dict] = []
    other_articles: List[dict] = []
    promoted = 0
    for index, item in enumerate(prepared):
        route = by_index.get(index)
        entry = {
            "result": item["result"],
            "url": item["url"],
            "clean_text": item["clean_text"],
        }
        title_hit = title_names_company(item["result"], company, aliases=aliases)
        if title_hit:
            # A headline that names the company is about the company. The opening
            # sentence decides routing everywhere else, but on a large company the
            # classifier errs the other way: an article whose headline says
            # "NVIDIA reports record revenue" and whose body also discusses peers
            # came back as "comparison", and its facts were then offered as
            # unconfirmed leads instead of as news. The headline is the publisher's
            # own statement of the subject, so it outranks a body reading.
            if route is not None and route.subject != "primary":
                promoted += 1
            own_articles.append(entry)
            continue

        if route is not None and route.subject == "primary":
            own_articles.append(entry)
        else:
            # Either a comparison or a passing mention: the article is not ours,
            # but it may contain a sentence that is. Both go to the incidental
            # stage so the mention is kept instead of discarded with the article.
            other_articles.append(entry)

    if promoted:
        logger.info(
            "Headline named the company | promoted_to_news=%s | company=%r",
            promoted,
            company,
        )

    hard_facts, analyst_opinions, hiring_items, stats = _collect_hard_facts(
        own_articles + postings_entries, window_start, company
    )
    return hard_facts, analyst_opinions, hiring_items, other_articles, prepared, stats


def _collect_hard_facts(
    articles: List[dict],
    window_start: str,
    company: str = "",
) -> Tuple[List[FactItem], List[FactItem], List[FactItem], dict]:
    """Read articles about our company and return (facts, opinions, hiring, stats).

    Opinions are collected in the same pass so the two lists stay two views of
    one reading of each article, not two separate extractions. Careers pages are
    diverted to the hiring list: a posting states intent, so it informs the
    model without being asserted as a reported fact.
    """
    hard_facts: List[FactItem] = []
    analyst_opinions: List[FactItem] = []
    hiring_items: List[FactItem] = []
    # Normalized text per stored fact, positionally aligned with hard_facts.
    normalized_facts: List[str] = []
    opinion_keys: set[str] = set()
    fact_index: dict[str, int] = {}
    stats = {
        "extracted": 0,
        "skipped": 0,
        "duplicate": 0,
        "stale_source": 0,
        "stale_fact": 0,
        "hiring_sources": 0,
    }
    seen_publishers: set[str] = set()

    for article in articles:
        source_url = article["url"]
        publish_date = parse_publish_date(
            article["result"].get("published_date")
        )
        clean_text = article["clean_text"]

        # A careers page is not a news article. Its openings are read from the
        # page's own schema.org markup, because the HTML a crawler receives is a
        # JavaScript shell with no roles in it. Text extraction is the fallback
        # for pages that do publish their listings as prose.
        if is_careers_url(source_url):
            postings = article.get("postings") or []
            if postings:
                posting_facts = _posting_items(postings, source_url, publish_date)
                stats["hiring_sources"] += 1
            else:
                posting_facts, posting_stats = _collect_posting(
                    clean_text, source_url, publish_date, window_start, company
                )
                stats["hiring_sources"] += posting_stats["hiring_sources"]
                stats["stale_fact"] += posting_stats["stale_fact"]

            hiring_items.extend(posting_facts)
            logger.info(
                "Posting source | url=%s | roles_listed=%s | via=%s",
                source_url,
                len(posting_facts),
                "structured data" if postings else "page text",
            )
            continue

        publisher = publisher_of(source_url)
        if publisher not in seen_publishers:
            seen_publishers.add(publisher)
            logger.info(
                "Reading publisher #%s | %s | accumulated=%s",
                len(seen_publishers), publisher, len(hard_facts),
            )

        stats["extracted"] += 1
        texts = extract_hard_facts(clean_text)

        for sentence in extract_analyst_opinions(clean_text, texts):
            if is_stale_period(sentence, window_start):
                stats["stale_fact"] += 1
                continue
            key = dedup_key(sentence)
            if key in opinion_keys or key in fact_index:
                continue
            opinion_item = _build_fact_item(sentence, source_url, publish_date)
            if opinion_item is None:
                continue
            opinion_keys.add(key)
            analyst_opinions.append(opinion_item)

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
            fact_item = _build_fact_item(text, source_url, publish_date)
            if fact_item is None:
                continue

            key = dedup_key(text)
            if key in fact_index:
                stats["duplicate"] += 1
                continue

            # A syndicated telling reappears with an extra clause. The fuller
            # telling replaces the shorter one only when the overlap is long
            # enough to be the same sentence, so two distinct facts that share a
            # short opening both stay.
            twin_index = find_prefix_twin(normalized_facts, text)
            if twin_index is not None:
                del fact_index[normalized_facts[twin_index]]
                hard_facts[twin_index] = fact_item
                normalized_facts[twin_index] = key
                fact_index[key] = twin_index
                replaced += 1
                continue

            fact_index[key] = len(hard_facts)
            normalized_facts.append(key)
            hard_facts.append(fact_item)
            added += 1

        if added == 0 and replaced == 0:
            stats["duplicate"] += 1
            logger.info("Skipped source (duplicate content) | url=%s", source_url)
        elif replaced:
            logger.info(
                "Richer duplicate replaced earlier fact | url=%s | replaced=%s",
                source_url, replaced,
            )

    return hard_facts, analyst_opinions, hiring_items, stats


def _posting_items(
    postings: List["JobPosting"],
    source_url: str,
    publish_date: Optional[datetime],
) -> List[FactItem]:
    """Turn structured postings into hiring items, one line each."""
    items: List[FactItem] = []
    for posting in postings:
        item = _build_fact_item(posting.to_line(), source_url, publish_date)
        if item is not None:
            items.append(item)
    return items


def _drop_out_of_window(
    results: List[dict],
    window_start: date,
    window_end: date,
) -> List[dict]:
    """Remove articles published outside the evidence window.

    Two rounds deliberately search without date bounds, because the pages they
    need carry no publish date at all: a regulator's case page and a job posting.
    That also lets those rounds return genuine news articles from months earlier
    (measured: a TechCrunch piece from 2026-08-24 inside a 30-day window), and
    nothing downstream caught it — the staleness check reads dates named in the
    sentence, not the article's own publication date.

    An article with no resolvable date is kept: dropping it would discard the
    structural pages this exists for. Its content is still checked against the
    window sentence by sentence.
    """
    kept: List[dict] = []
    dropped: List[str] = []

    for result in results:
        if not isinstance(result, dict):
            continue
        published = parse_publish_date(result.get("published_date"))
        if published is None:
            kept.append(result)
            continue

        published_date = published.date()
        if window_start <= published_date <= window_end:
            kept.append(result)
        else:
            dropped.append(f"{published_date} {str(result.get('url') or '')[:70]}")

    if dropped:
        logger.info(
            "Dropped %s article(s) published outside %s..%s | %s",
            len(dropped),
            window_start,
            window_end,
            dropped[:5],
        )
    return kept


def _company_tokens(company: str) -> List[str]:
    """Return the searchable name tokens of a company, used to attribute a page."""
    import re as _re

    parts = [part for part in _re.split(r"[^a-z0-9]+", company.lower()) if len(part) > 2]
    return parts


def _collect_posting(
    clean_text: str,
    source_url: str,
    publish_date: Optional[datetime],
    window_start: str,
    company: str = "",
) -> Tuple[List[FactItem], dict]:
    """Read a careers page and keep only the lines that describe a role.

    The decision is per line, not per page: a careers URL can legitimately hold
    a walk-in notice posted as an ordinary social update, and a markets column
    that says "jobs data" holds nothing. ``is_hiring_line`` makes that call, so
    this function only reads and deduplicates.

    ``company`` guards against a job board's generic pages. LinkedIn returns an
    index for "jobs hiring jobs" alongside the company's own, and those pages list
    other employers' roles; a line is kept only when the page itself names the
    company being briefed.
    """
    stats = {"hiring_sources": 0, "stale_fact": 0}
    items: List[FactItem] = []
    seen: set[str] = set()

    tokens = _company_tokens(company)
    page_is_ours = tokens and any(token in clean_text.lower() for token in tokens)

    if tokens and not page_is_ours:
        logger.info(
            "Posting page skipped | does not name the company | url=%s", source_url
        )
        return items, stats

    for line in clean_text.splitlines():
        text = " ".join(line.split())
        if len(text) < MIN_POSTING_LINE_CHARS:
            continue
        if not is_hiring_line(text):
            continue
        if is_stale_period(text, window_start):
            stats["stale_fact"] += 1
            continue

        key = text.lower()
        if key in seen:
            continue
        seen.add(key)

        item = _build_fact_item(text, source_url, publish_date)
        if item is not None:
            items.append(item)

        if len(items) >= MAX_POSTINGS_PER_SOURCE:
            break

    if items:
        stats["hiring_sources"] = 1

    return items, stats


async def ingest_company_data(
    request: BriefingRequest,
    *,
    coverage_end: Optional[date] = None,
    stored_facts: Optional[List[FactItem]] = None,
    stored_opinions: Optional[List[FactItem]] = None,
    known_urls: Optional[set] = None,
    preferred_jurisdiction: Optional[str] = None,
) -> FactBase:
    """Ingest company data and return a validated FactBase.

    The evidence window ends at the meeting, not at today, so a meeting six days
    out only looks at the 24 days that have already happened. Facts already
    stored for this company are folded back into the result, so a second run
    searches the increment instead of the whole window.

    analyst_opinions stays empty on purpose: commentator framing must not reach
    the LLM, so the report is generated from hard facts alone.
    """
    logger.info(
        "Ingestion started | company=%r | purpose=%r | meeting_time=%s",
        request.company_name,
        request.meeting_purpose,
        request.meeting_time.isoformat(),
    )

    window = compute_evidence_window(
        request.meeting_time, coverage_end=coverage_end
    )
    logger.info(
        "Evidence window | window=%s..%s | observable_to=%s | fetch=%s..%s | "
        "pending_days=%s | complete=%s | needs_fetch=%s | preliminary=%s",
        window.window_start, window.window_end, window.available_end,
        window.fetch_start, window.fetch_end, window.pending_days,
        window.is_complete, window.needs_fetch, window.preliminary,
    )

    company_label = request.company_name.strip()

    # Identify the company before searching for it. The registry result decides
    # which regulator the legal queries name; an unidentified company gets
    # generic queries instead of a guessed jurisdiction.
    #
    # A ticker collides with unrelated registered companies, so when the name is
    # too short for an exact match to mean anything the model is asked which
    # entity is meant. Until that is settled the jurisdiction is left unused:
    # aiming legal searches at the wrong country is worse than not aiming them.
    #
    # A caller who rejected every registry candidate ("other") supplies a
    # description instead, and that description becomes the only source of
    # jurisdiction. The registry is then never used as a fallback, because its
    # closest match is an unrelated business that shares the name.
    description_hint = jurisdiction_from_description(request.company_description)
    confirmed_other = bool(request.company_description.strip()) and (
        preferred_jurisdiction is None
    )

    identity = await asyncio.to_thread(
        resolve_identity,
        company_label,
        preferred_jurisdiction=preferred_jurisdiction or description_hint,
        allow_fallback=not confirmed_other,
    )
    if not confirmed_other and preferred_jurisdiction is None and identity is not None:
        candidates = await asyncio.to_thread(rank_candidates, company_label)
        if needs_caller_choice(company_label, candidates):
            decided = await disambiguate_with_model(company_label, candidates)
            if decided and decided != "none":
                identity = await asyncio.to_thread(
                    resolve_identity,
                    company_label,
                    preferred_jurisdiction=decided,
                )
            elif decided == "none":
                logger.info(
                    "Identity withheld | company=%r | registry matches are unrelated",
                    company_label,
                )
                identity = None

    if confirmed_other:
        logger.info(
            "Caller described the company | company=%r | description=%r | "
            "jurisdiction=%s | registry_identity=%s",
            company_label,
            request.company_description.strip()[:120],
            description_hint,
            identity.name if identity is not None else None,
        )

    if request.company_description.strip():
        logger.info(
            "Description available for entity separation | company=%r", company_label
        )

    # Kept in scope outside the fetch branch: the routing stage uses the registry
    # name to read headlines, whether or not retrieval ran this time.
    aliases = [identity.name] if identity is not None else None

    if window.needs_fetch:
        plan = await plan_searches(company_label, request.meeting_purpose.strip())
        logger.info(
            "Search plan | industry=%s | categories=%s | fundamentals=%s",
            plan.industry, plan.categories, plan.fundamentals,
        )
        # Legal exposure is searched by jurisdiction, not by the model's plan:
        # the planner knows the industry, not where the company is registered.
        # Hiring runs here too: a careers page is usually on the company's own
        # site, which the publisher registry does not contain by design.
        open_queries = legal_queries(company_label, identity) + hiring_queries(company_label)
        linkedin_queries = linkedin_hiring_queries(company_label)
        # The registry name is the company's other name, and headlines use it:
        # a search for "IBM" otherwise discards "International Business Machines".
        fetched = await _search_plan(
            plan,
            window.fetch_start.isoformat(),
            window.fetch_end.isoformat(),
            company_label,
            known_urls=known_urls,
            open_queries=open_queries,
            aliases=aliases,
            linkedin_queries=linkedin_queries,
        )
    else:
        # Everything up to today is already covered, so reuse what is stored
        # instead of paying for an identical search.
        logger.info("Retrieval skipped | coverage already reaches %s", window.available_end)
        fetched = []

    fetched = await resolve_publish_dates(fetched)
    fetched = _drop_out_of_window(fetched, window.window_start, window.available_end)
    fetched = order_by_publisher(fetched)

    hard_facts, analyst_opinions, hiring_items, other_articles, prepared, stats = (
        await _route_and_collect(
            fetched, window.window_start.isoformat(), company_label,
            request.company_description, aliases,
        )
    )

    # Stored evidence was retrieved on an earlier run; it belongs to the same
    # company and the same window, so it is merged rather than re-fetched.
    reused = [item for item in (stored_facts or []) if item.publish_date is not None]
    hard_facts = _merge_reused_facts(reused, hard_facts)

    reused_opinions = [
        item for item in (stored_opinions or []) if item.publish_date is not None
    ]
    analyst_opinions = _merge_reused_facts(reused_opinions, analyst_opinions)

    # A promise is not an event. Splitting them here keeps stated intent out of
    # the evidence the model reasons over, so a recommendation can never rest on
    # something the company merely said it would do. The promises are still
    # delivered, in their own lane, for the reader to weigh.
    hard_facts, forward_looking = split_forward_looking(hard_facts)
    if forward_looking:
        logger.info(
            "Forward-looking statements separated | company=%r | promises=%s | events=%s",
            company_label,
            len(forward_looking),
            len(hard_facts),
        )

    # A price is a fact with a clock, and no article is obliged to state today's
    # number. Fetched only when the company has a ticker, so a private company
    # simply has no quote rather than an invented one.
    quote_item = None
    ticker = request.ticker or ""
    if ticker and window.needs_fetch:
        quote = await asyncio.to_thread(fetch_quote, ticker)
        if quote is not None:
            quote_item = FactItem(
                content=quote.to_line(),
                source_url=quote.source_url,
                publish_date=quote.as_of,
            )

    # Facts about us that appear inside other companies' articles. They are kept
    # apart from the evidence the model reasons over: they exist so a reader can
    # follow up, not so the brief can lean on them.
    incidental, incidental_stats = await harvest_incidental_facts(
        other_articles,
        company_label,
        window.window_start.isoformat(),
        already_collected=[item.content for item in hard_facts],
    )
    if incidental and window.needs_fetch:
        incidental = await corroborate_incidental_facts(
            incidental,
            window.fetch_start.isoformat(),
            window.fetch_end.isoformat(),
            company_label,
            known_urls=known_urls,
        )

    # Same-name businesses share these words, so a sentence that names our
    # company may be about another one entirely. The caller's description is the
    # only thing that distinguishes them, so it filters the trailing reference
    # block. Nothing here can reach the brief itself: these are leads beside it.
    if incidental and request.company_description.strip():
        incidental = filter_by_entity(
            incidental, request.company_description, company_label
        )

    # Two stages, in the order the reader benefits from them.
    #
    # First the fact base itself is merged: the same fact reported by five
    # outlets becomes one entry carrying all five links, and the fuller telling
    # wins because it holds more of the detail.
    if hard_facts:
        hard_facts, merge_stats = await merge_duplicate_facts(hard_facts)
        logger.info(
            "Hard fact merge | before=%s | after=%s",
            merge_stats["considered"],
            len(hard_facts),
        )

    # Then incidental mentions are checked against the merged base: a one-line
    # mention adds nothing once the fact itself is already stored with its
    # sources, so it is dropped. A mention that says something the base does not
    # is kept, because it is its own fact.
    if incidental and hard_facts:
        duplicate_indices = await drop_duplicate_facts(
            [item.content for item in hard_facts],
            [item.content for item in incidental],
        )
        if duplicate_indices:
            dropped_set = set(duplicate_indices)
            incidental = [
                item
                for index, item in enumerate(incidental)
                if index not in dropped_set
            ]
            logger.info(
                "Incidental dedup | dropped=%s | remaining=%s",
                len(dropped_set),
                len(incidental),
            )

    coverage_report = build_coverage_report(
        total_facts=len(hard_facts),
        distinct_sources=_count_sources(hard_facts),
        identity=identity,
        has_hiring=bool(hiring_items),
        has_legal=_mentions_legal(hard_facts),
        has_opinions=bool(analyst_opinions),
        preliminary=window.preliminary,
        window_start=window.window_start,
        window_end=window.available_end,
    )
    logger.info(
        "Coverage | level=%s | facts=%s | sources=%s | missing=%s | checks=%s",
        coverage_report.level,
        coverage_report.total_facts,
        coverage_report.distinct_sources,
        coverage_report.lanes_missing,
        len(coverage_report.checks_to_run),
    )

    fact_base = FactBase(
        company_id=_build_company_id(request.company_name),
        last_refresh_date=datetime.now(timezone.utc),
        hard_facts=hard_facts,
        analyst_opinions=analyst_opinions,
        hiring=hiring_items,
        quote=quote_item,
        conflicts=[],
        incidental_facts=incidental,
        forward_looking=forward_looking,
        coverage=CoverageRecord(
            level=coverage_report.level,
            total_facts=coverage_report.total_facts,
            distinct_sources=coverage_report.distinct_sources,
            can_support_leverage=coverage_report.can_support_leverage,
            lanes_missing=coverage_report.lanes_missing,
            notes=coverage_report.notes,
            checks_to_run=coverage_report.checks_to_run,
        ),
    )

    from .conflict_detection import detect_fact_conflicts

    fact_base.conflicts = detect_fact_conflicts(fact_base)

    logger.info(
        "Ingestion complete | company_id=%s | extracted=%s | skipped=%s | "
        "duplicate=%s | stale_source=%s | stale_fact=%s | reused=%s | "
        "hard_facts=%s | analyst_opinions=%s | hiring=%s | incidental=%s | conflicts=%s",
        fact_base.company_id,
        stats["extracted"],
        stats["skipped"],
        stats["duplicate"],
        stats["stale_source"],
        stats["stale_fact"],
        len(reused),
        len(fact_base.hard_facts),
        len(fact_base.analyst_opinions),
        len(fact_base.hiring),
        len(fact_base.incidental_facts),
        len(fact_base.conflicts),
    )
    return fact_base


def _count_sources(facts: List[FactItem]) -> int:
    """Count distinct outlets across a fact list, including merged sources."""
    hosts: set[str] = set()
    for item in facts:
        for url in item.all_sources():
            hosts.add(host_of(url))
    return len(hosts)


# Words that mark a fact as legal or regulatory rather than merely financial.
LEGAL_MARKERS = (
    "lawsuit",
    "litigation",
    "court",
    "settlement",
    "fine",
    "penalty",
    "investigation",
    "probe",
    "antitrust",
    "regulator",
    "recall",
    "warning letter",
    "subpoena",
)


def _mentions_legal(facts: List[FactItem]) -> bool:
    """True when at least one fact reports legal or regulatory exposure."""
    return any(
        match_word(item.content, marker)
        for item in facts
        for marker in LEGAL_MARKERS
    )


def _merge_reused_facts(
    stored: List[FactItem],
    fresh: List[FactItem],
) -> List[FactItem]:
    """Fold previously stored facts into a freshly fetched set.

    Stored facts come first so a run that retrieves nothing at all still
    produces a usable fact base. Exact duplicates keep the stored entry, and a
    fresh telling that extends a stored one replaces it.
    """
    merged: List[FactItem] = list(stored)
    normalized = [dedup_key(item.content) for item in merged]

    for item in fresh:
        key = dedup_key(item.content)
        if key in normalized:
            continue

        twin_index = find_prefix_twin(normalized, item.content)
        if twin_index is not None:
            merged[twin_index] = item
            normalized[twin_index] = key
            continue

        merged.append(item)
        normalized.append(key)

    return merged

