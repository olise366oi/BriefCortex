# /backend/app/services/tavily_search.py
"""The single Tavily call both retrieval stages use."""
from __future__ import annotations

from typing import List

from tenacity import retry, stop_after_attempt, wait_random_exponential
from tavily import TavilyClient

from app.core.config import get_settings
from app.services.logger_service import get_logger
from app.services.publisher_filter import filter_results_for_company

logger = get_logger("tavily")

# Tavily returns 10 results by default and accepts up to 20. Above 20 the
# response is unreliable (measured: 25 -> 16, 30 -> 1, 40 -> 10), so 20 is the
# hard ceiling and additional coverage comes from running more queries.
TAVILY_MAX_RESULTS = 20


class TavilySearchError(Exception):
    """Raised when Tavily search fails after retries."""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True,
)
def tavily_search(
    query: str,
    start_date: str,
    end_date: str,
    company_name: str,
    domains: List[str],
) -> List[dict]:
    """Run one Tavily query, retrying transient failures with backoff."""
    try:
        api_key = get_settings().TAVILY_API_KEY
        if not api_key:
            raise RuntimeError("TAVILY_API_KEY is not configured")

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
            include_domains=domains or None,
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
