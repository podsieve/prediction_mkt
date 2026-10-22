from __future__ import annotations

import logging
import sys
import time
from typing import List, Optional

from curl_cffi import requests
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

# Arena sits behind Cloudflare; plain `requests` gets 403 challenges from
# GitHub Actions (and most datacenter IPs). curl_cffi impersonates a real
# Chrome TLS/HTTP2 fingerprint so the challenge never fires.
RECOVERABLE_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.HTTPError,
)

_BLOCKED_PAGE_MARKERS = (
    "Just a moment...",
    "Enable JavaScript and cookies to continue",
    "cf-mitigated",
    "Access denied | arena.ai used Cloudflare",
)

# Reused across category fetches within one run so Cloudflare cookies stick.
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session(impersonate=settings.http_impersonate)
    return _session


def _assert_not_blocked(url: str, status_code: int, body: str) -> None:
    """Raise HTTPError if Cloudflare served a challenge/deny page as 200."""
    if any(marker in body for marker in _BLOCKED_PAGE_MARKERS):
        raise requests.exceptions.HTTPError(
            f"{status_code} Client Error: Cloudflare blocked {url}"
        )


@retry(
    stop=stop_after_attempt(settings.max_retries),
    wait=wait_exponential(multiplier=settings.retry_delay, max=60),
    retry=retry_if_exception_type(RECOVERABLE_ERRORS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def fetch_page(url: str) -> str:
    # Let the impersonation profile supply browser headers (UA, sec-ch-ua,
    # Accept-Encoding, etc.). A custom bot UA on top of Chrome TLS is a
    # fingerprint mismatch Cloudflare has learned to reject.
    headers = {}
    if settings.user_agent:
        headers["User-Agent"] = settings.user_agent

    response = _get_session().get(
        url,
        headers=headers or None,
        timeout=settings.request_timeout,
    )
    response.raise_for_status()
    _assert_not_blocked(url, response.status_code, response.text)
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
    categories = list(settings.scrape_categories.items())
    for i, (category, url) in enumerate(categories):
        if i > 0:
            # Brief pause between categories — rapid back-to-back fetches
            # from one IP can trip Cloudflare's 429 rate limit.
            time.sleep(settings.retry_delay)
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

    from src.db import record_failed_scrape, store_results

    results = scrape_all()
    if not results:
        logger.error("All category scrapes failed")
        sys.exit(1)

    stored = 0
    for result in results:
        # One category failing its sanity checks shouldn't stop the others.
        try:
            # The stored count can be lower than the parsed count — duplicate
            # listings and unresolvable names are dropped on the way in.
            stored_count = store_results(result)
        except Exception as e:
            logger.error("[%s] Refused to store scrape: %s", result.category, e)
            record_failed_scrape(
                str(e), category=result.category, source_url=result.source_url
            )
            continue
        stored += 1
        logger.info(
            "[%s] Stored snapshot with %d models.",
            result.category,
            stored_count,
        )

    logger.info("Done. Stored %d/%d categories successfully.",
                stored, len(settings.scrape_categories))
    if not stored:
        sys.exit(1)


if __name__ == "__main__":
    main()
