"""Follow up an incidental fact with a targeted search for a better telling."""
from __future__ import annotations

import asyncio
from typing import List, Optional, Sequence

from app.models.fact_base import IncidentalFact
from app.services.tavily_search import tavily_search
from app.services.logger_service import get_logger

logger = get_logger("corroboration")

# Upper bounds so a follow-up never runs away: only the freshest facts get a
# search, and only the first few hits are read.
MAX_FOLLOWUPS = 8
MAX_RESULTS_PER_FOLLOWUP = 5

# Follow-up searches are independent Tavily calls, so the only reason to
# serialise them is to protect the API. Three at a time turned eight
# corroboration checks into 21.6 seconds on a measured Wipro run.
FOLLOWUP_CONCURRENCY = 8


def _query_for(fact: IncidentalFact) -> str:
    """Build a search query that is about this specific sentence.

    The sentence already names the company and the event, so it is the query;
    trimming keeps it inside what the search API accepts.
    """
    text = fact.content.strip()
    return text[:180]


async def corroborate_incidental_facts(
    facts: Sequence[IncidentalFact],
    start_date: str,
    end_date: str,
    company: str,
    *,
    known_urls: Optional[set] = None,
) -> List[IncidentalFact]:
    """Mark each incidental fact as corroborated or unverified.

    A fact stays "unverified" unless the follow-up search finds reporting that
    is not the article it came from. Unverified is not a failure: it is the
    honest answer, and the field exists so a reader knows to check.
    """
    if not facts:
        return []

    known = {url.strip().rstrip("/") for url in (known_urls or set())}
    semaphore = asyncio.Semaphore(FOLLOWUP_CONCURRENCY)
    targets = list(facts[:MAX_FOLLOWUPS])

    async def check(fact: IncidentalFact) -> IncidentalFact:
        async with semaphore:
            try:
                hits = await asyncio.to_thread(
                    tavily_search,
                    _query_for(fact),
                    start_date,
                    end_date,
                    company,
                    [],
                )
            except Exception as exc:
                logger.info("Follow-up search failed | url=%s | %s", fact.source_url, exc)
                return fact

        own = str(fact.source_url).strip().rstrip("/")
        for hit in hits[:MAX_RESULTS_PER_FOLLOWUP]:
            url = str(hit.get("url") or "").strip().rstrip("/")
            if not url or url == own or url in known:
                continue
            if not (hit.get("raw_content") or "").strip():
                continue
            fact.provenance = "corroborated"
            fact.corroborating_url = url
            return fact

        return fact

    updated = await asyncio.gather(*[check(fact) for fact in targets])
    resolved = list(updated) + list(facts[MAX_FOLLOWUPS:])

    corroborated = sum(1 for fact in resolved if fact.provenance == "corroborated")
    logger.info(
        "Corroboration | checked=%s | corroborated=%s | unverified=%s",
        len(targets),
        corroborated,
        len(resolved) - corroborated,
    )
    return resolved
