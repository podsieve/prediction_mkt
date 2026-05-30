"""Watch the Arena.ai changelog page for new model additions.

Designed to run frequently (e.g. every minute via GitHub Actions).
Read-only against the DB — no snapshots or rankings are created.
Sends an alert email via Resend if any model on the changelog is not
yet tracked in the database.
"""
from __future__ import annotations

import logging
import sys

from src.changelog_parser import CHANGELOG_URL, parse_changelog
from src.config import settings
from src.db import get_client, load_caches
from src.scraper import fetch_page

from alerts.checker import AlertEvent
from alerts.emailer import send_alert_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def watch() -> list[AlertEvent]:
    """Fetch the changelog, diff against known models, return new-model alerts."""
    logger.info("Fetching changelog from %s", CHANGELOG_URL)
    html = fetch_page(CHANGELOG_URL)

    entries = parse_changelog(html, lookback_hours=48)
    if not entries:
        logger.info("No recent changelog entries found")
        return []

    client = get_client()
    model_cache, alias_cache = load_caches(client)
    known_names = set(model_cache.keys()) | set(alias_cache.keys())

    events: list[AlertEvent] = []
    for entry in entries:
        if entry.model_name not in known_names:
            events.append(AlertEvent(
                event_type="new_model",
                model_name=entry.model_name,
                summary=(
                    f"New model on changelog: {entry.model_name} "
                    f"(added to {entry.category}, {entry.date:%b %d})"
                ),
                details={
                    "source": "changelog",
                    "changelog_date": entry.date.isoformat(),
                    "category": entry.category,
                    "link": entry.link,
                },
            ))

    return events


def main():
    try:
        events = watch()
    except Exception as e:
        logger.error("Changelog watch failed: %s", e)
        sys.exit(1)

    if events:
        logger.info("Detected %d new model(s), sending alert email", len(events))
        send_alert_email(events)
    else:
        logger.info("No new models detected on changelog")


if __name__ == "__main__":
    main()
