from __future__ import annotations

import logging
import sys
from typing import List

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
    retry_if_exception_type,
)

from src.config import settings
from src.models import ScrapeResult
from src.parser import parse_leaderboard

logger = logging.getLogger(__name__)

RECOVERABLE_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.HTTPError,
)


@retry(
    stop=stop_after_attempt(settings.max_retries),
    wait=wait_exponential(multiplier=settings.retry_delay, max=60),
    retry=retry_if_exception_type(RECOVERABLE_ERRORS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def fetch_page(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": settings.user_agent},
        timeout=settings.request_timeout,
    )
    response.raise_for_status()
    return response.text


def scrape_category(category: str, url: str) -> ScrapeResult:
    """Scrape a single leaderboard category and return the parsed result."""
    logger.info("Fetching %s leaderboard from %s", category, url)
    html = fetch_page(url)
    logger.info("Fetched %d bytes for %s, parsing...", len(html), category)

    result = parse_leaderboard(html, url, category=category)
    logger.info(
        "[%s] Parsed %d models (total votes: %s) in %dms",
        category,
        result.total_models,
        result.total_votes,
        result.scrape_duration_ms,
    )

    if result.total_models < 20:
        logger.warning(
            "[%s] Only parsed %d models — possible layout change or partial page",
            category,
            result.total_models,
        )

    return result


def scrape() -> ScrapeResult:
    """Scrape the default (overall) category. Kept for backward compat."""
    return scrape_category("overall", settings.scrape_url)


def scrape_all() -> List[ScrapeResult]:
    """Scrape all configured categories and return a list of results."""
    results: List[ScrapeResult] = []
    for category, url in settings.scrape_categories.items():
        try:
            result = scrape_category(category, url)
            results.append(result)
        except Exception as e:
            logger.error("[%s] Scrape failed: %s", category, e)
            from src.db import record_failed_scrape
            record_failed_scrape(str(e), category=category, source_url=url)
    return results


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from src.db import store_results

    results = scrape_all()
    if not results:
        logger.error("All category scrapes failed")
        sys.exit(1)

    for result in results:
        store_results(result)
        logger.info(
            "[%s] Stored snapshot with %d models.",
            result.category,
            result.total_models,
        )

    logger.info("Done. Scraped %d/%d categories successfully.",
                len(results), len(settings.scrape_categories))


if __name__ == "__main__":
    main()
