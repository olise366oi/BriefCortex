# /backend/app/services/tavily_search.py
"""The single Tavily call both retrieval stages use."""
from __future__ import annotations

from typing import Dict, List, Optional

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_random_exponential
from tavily import TavilyClient
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    MissingAPIKeyError,
    UsageLimitExceededError,
)

from app.core.config import get_settings
from app.services.logger_service import get_logger
from app.services.publisher_filter import filter_results_for_company

logger = get_logger("tavily")

# Tavily returns 10 results by default and accepts up to 20. Above 20 the
# response is unreliable (measured: 25 -> 16, 30 -> 1, 40 -> 10), so 20 is the
# hard ceiling and additional coverage comes from running more queries.
TAVILY_MAX_RESULTS = 20


def _is_transient(exc: BaseException) -> bool:
    """Only failures that can succeed on another attempt are worth retrying.

    A rejected key, a refused request or an exhausted plan quota answers the
    same way every time. Retrying it three times with backoff spends the
    request's whole time budget on a question that was already answered, and
    across a four-lane plan that is minutes spent learning nothing.
    """
    return not isinstance(
        exc,
        (
            UsageLimitExceededError,
            ForbiddenError,
            InvalidAPIKeyError,
            MissingAPIKeyError,
            BadRequestError,
        ),
    )


class TavilySearchError(Exception):
    """Raised when Tavily search fails after retries."""


class TavilyQuotaError(TavilySearchError):
    """The search plan will refuse every further query in this run.

    Called out separately from a generic failure because the difference
    matters to the reader: a quota-exhausted run has *no* coverage, which is a
    statement about the run and not about the company.
    """


@retry(
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    retry=retry_if_exception(_is_transient),
    reraise=True,
)
def tavily_search(
    query: str,
    start_date: str,
    end_date: str,
    company_name: str,
    domains: List[str],
    aliases: Optional[List[str]] = None,
    dated: bool = True,
) -> List[dict]:
    """Run one Tavily query, retrying transient failures with backoff.

    ``aliases`` are the other names this company is known by, so the mention
    filter keeps a headline that uses the registered legal name instead of the
    short form the caller searched for.

    ``dated`` restricts the search to the evidence window. A careers page
    carries no publish date, so a dated search excludes every posting it might
    find (measured: 1 careers URL with the window versus 9 without), and hiring
    queries therefore run undated.
    """
    try:
        api_key = get_settings().TAVILY_API_KEY
        if not api_key:
            raise RuntimeError("TAVILY_API_KEY is not configured")

        logger.info(
            "Tavily search | query=%r | window=%s | domains=%s | max=%s",
            query,
            f"{start_date}..{end_date}" if dated else "undated",
            domains,
            TAVILY_MAX_RESULTS,
        )
        client = TavilyClient(api_key=api_key)

        response = client.search(
            query=query,
            search_depth="advanced",
            include_raw_content=True,
            start_date=start_date if dated else None,
            end_date=end_date if dated else None,
            include_domains=domains or None,
            topic="news" if dated else "general",
            max_results=TAVILY_MAX_RESULTS,
        )

        if not isinstance(response, dict):
            logger.warning("Tavily returned non-dict response")
            return []

        results = response.get("results", [])
        if not isinstance(results, list):
            logger.warning("Tavily results were not a list")
            return []

        kept = filter_results_for_company(results, company_name, aliases)
        logger.info("Filter kept %s of %s results", len(kept), len(results))
        return kept

    except UsageLimitExceededError as exc:
        raise TavilyQuotaError(
            f"Tavily search quota exhausted (query={query!r}): {exc}"
        ) from exc
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


# Extracting a body is a repair, not a search: it never raises, because a page
# Tavily cannot read is the situation this call exists to work around.
EXTRACT_BATCH_SIZE = 20


def extract_bodies(urls: List[str]) -> Dict[str, str]:
    """Fetch the raw content for specific URLs that came back as shells.

    A search result can point at a page whose body the crawler did not return.
    Extract reads that exact URL, so the story behind an already-relevant
    result is recovered instead of being dropped. Returns {url: raw_content}.
    """
    api_key = get_settings().TAVILY_API_KEY
    if not api_key or not urls:
        return {}

    client = TavilyClient(api_key=api_key)
    recovered: Dict[str, str] = {}

    for start in range(0, len(urls), EXTRACT_BATCH_SIZE):
        batch = urls[start : start + EXTRACT_BATCH_SIZE]
        try:
            response = client.extract(urls=batch, include_images=False)
        except Exception as exc:
            logger.info("Body extract failed | urls=%s | %s", len(batch), exc)
            continue

        if not isinstance(response, dict):
            continue

        for document in response.get("results") or []:
            url = str(document.get("url") or "")
            content = document.get("raw_content")
            if url and isinstance(content, str) and content.strip():
                recovered[url] = content

        for failure in response.get("failed_results") or []:
            logger.info("Body extract unreadable | %s", str(failure)[:120])

    return recovered
