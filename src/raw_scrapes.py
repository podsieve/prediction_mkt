"""Keep the parsed rows of a scrape that could not be stored, so it can be replayed.

Arena only publishes its current board, so a scrape that is thrown away is gone
for good. When a guard refuses one (see src/db.py check_new_model_surge), the
parsed rows are saved here; src/replay.py stores them later with their original
timestamp once the cause is fixed.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import List

from src.models import ScrapeResult

logger = logging.getLogger(__name__)


def content_hash(result: ScrapeResult) -> str:
    """Identify a scrape by what Arena published, not by when we fetched it."""
    rows = [m.model_dump(mode="json") for m in result.models]
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def save_raw_scrape(client, result: ScrapeResult, error_message: str) -> bool:
    """Save a scrape that failed to store. Never raises: this runs on a failure
    path, and losing the copy must not mask the original error.

    Repeat scrapes of unchanged content keep the first copy, so the replay uses
    the earliest time that state was seen.
    """
    try:
        client.table("raw_scrapes").upsert(
            {
                "category": result.category,
                "scraped_at": result.scraped_at.isoformat(),
                "source_url": result.source_url,
                "content_hash": content_hash(result),
                "error_message": error_message[:1000],
                "payload": result.model_dump(mode="json"),
            },
            on_conflict="category,content_hash",
            ignore_duplicates=True,
        ).execute()
        logger.info("[%s] Saved scrape for replay", result.category)
        return True
    except Exception as e:
        logger.error("[%s] Could not save scrape for replay: %s", result.category, e)
        return False


def pending_raw_scrapes(client) -> List[dict]:
    """Saved scrapes not yet replayed, oldest first."""
    result = (
        client.table("raw_scrapes")
        .select("id, category, scraped_at, error_message, payload")
        .is_("replayed_at", "null")
        .order("scraped_at")
        .execute()
    )
    return result.data or []


def mark_replayed(client, raw_scrape_id: str) -> None:
    client.table("raw_scrapes").update(
        {"replayed_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", raw_scrape_id).execute()
