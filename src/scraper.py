from __future__ import annotations

import logging
import sys

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


def scrape() -> ScrapeResult:
    url = settings.scrape_url
    logger.info("Fetching leaderboard from %s", url)
    html = fetch_page(url)
    logger.info("Fetched %d bytes, parsing...", len(html))

    result = parse_leaderboard(html, url)
    logger.info(
        "Parsed %d models (total votes: %s) in %dms",
        result.total_models,
        result.total_votes,
        result.scrape_duration_ms,
    )

    if result.total_models < 50:
        logger.warning(
            "Only parsed %d models — possible layout change or partial page",
            result.total_models,
        )

    return result


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        result = scrape()
    except Exception as e:
        logger.error("Scrape failed: %s", e)
        from src.db import record_failed_scrape
        record_failed_scrape(str(e))
        sys.exit(1)

    from src.db import store_results
    store_results(result)
    logger.info("Done. Stored snapshot with %d models.", result.total_models)


if __name__ == "__main__":
    main()
