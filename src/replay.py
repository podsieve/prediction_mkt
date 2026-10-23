"""Store scrapes that were refused earlier, once the cause has been fixed.

Usage:
    python -m src.replay            # dry run: list what would be replayed
    python -m src.replay --apply    # store them, oldest first

A scrape lands in `raw_scrapes` when a guard refuses it (e.g. Arena renamed
every model and the new-model surge guard tripped). Fix the cause first — for a
rename, run scripts/alias_renamed_models.py --apply — then replay. Each scrape
is stored with the timestamp it was originally fetched at.
"""
from __future__ import annotations

import argparse
import logging
import sys

from src.db import get_client, load_caches, store_results
from src.models import ScrapeResult
from src.raw_scrapes import mark_replayed, pending_raw_scrapes

logger = logging.getLogger(__name__)


def replay_pending(apply: bool) -> int:
    """Replay saved scrapes. Returns how many failed (they stay pending)."""
    client = get_client()
    pending = pending_raw_scrapes(client)
    if not pending:
        logger.info("Nothing to replay")
        return 0

    model_cache, alias_cache = load_caches(client)
    failed = 0
    for row in pending:
        result = ScrapeResult.model_validate(row["payload"])
        label = f"[{result.category}] scrape from {result.scraped_at:%Y-%m-%d %H:%M} UTC"

        if not apply:
            new = sum(
                1 for m in result.models
                if m.model_name not in model_cache and m.model_name not in alias_cache
            )
            logger.info(
                "[dry run] Would replay %s: %d models, %d unrecognised (was refused: %s)",
                label, len(result.models), new, (row.get("error_message") or "")[:100],
            )
            continue

        try:
            stored = store_results(result, replay=True)
        except Exception as e:
            failed += 1
            logger.error("Could not replay %s, leaving it pending: %s", label, e)
            continue
        mark_replayed(client, row["id"])
        logger.info("Replayed %s: stored %d rankings", label, stored)

    if not apply:
        logger.info("Dry run only. Re-run with --apply to execute.")
    return failed


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Replay scrapes that were refused earlier")
    parser.add_argument("--apply", action="store_true", help="store them (default is a dry run)")
    if replay_pending(parser.parse_args().apply):
        sys.exit(1)


if __name__ == "__main__":
    main()
