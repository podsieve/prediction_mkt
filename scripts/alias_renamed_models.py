"""Alias scraped names to the existing models they renamed.

Usage:
    python scripts/alias_renamed_models.py           # dry run: show the plan
    python scripts/alias_renamed_models.py --apply   # write the aliases

Use this when Arena relabels models wholesale (e.g. slugs -> display names) and
the new-model surge guard refuses every scrape. For each category it fetches the
live leaderboard, matches every unrecognised name to an existing model (see
src/renames.py), and inserts the new name into `model_aliases`. Rankings and
canonical names are untouched, so history stays on one model ID. Names it can't
match confidently are listed and left alone — those may be genuinely new models.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

# Allow `python scripts/alias_renamed_models.py` from the repo root without PYTHONPATH.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.config import settings
from src.db import get_client, load_caches
from src.renames import PrevRanking, RenameMatch, match_renamed_models
from src.scraper import scrape_category

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def previous_rankings(client, category: str, name_by_id: dict) -> list[PrevRanking]:
    """Rankings from the category's last successful snapshot."""
    snap = (
        client.table("snapshots")
        .select("id")
        .eq("status", "success")
        .eq("category", category)
        .order("scraped_at", desc=True)
        .limit(1)
        .execute()
    )
    if not snap.data:
        return []

    rows, start = [], 0
    while True:
        page = (
            client.table("rankings")
            .select("model_id, score, votes")
            .eq("snapshot_id", snap.data[0]["id"])
            .order("id")
            .range(start, start + 999)
            .execute()
        )
        batch = page.data or []
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000

    return [
        PrevRanking(r["model_id"], name_by_id.get(r["model_id"], "?"), r["score"], r["votes"])
        for r in rows
    ]


def run(apply: bool) -> None:
    client = get_client()
    known, aliases = load_caches(client)
    name_by_id = {model_id: name for name, model_id in known.items()}

    plan: dict[str, RenameMatch] = {}
    for category, url in settings.scrape_categories.items():
        live = scrape_category(category, url).models
        prev = previous_rankings(client, category, name_by_id)
        # Aliases planned for earlier categories count as already known.
        known_aliases = {**aliases, **{n: m.model_id for n, m in plan.items()}}
        matches, unmatched = match_renamed_models(live, known, known_aliases, prev)

        by_stats = [m for m in matches if m.method == "stats"]
        logger.info(
            "[%s] %d scraped, %d renamed (%d by name, %d by score/votes), %d unmatched",
            category, len(live), len(matches), len(matches) - len(by_stats), len(by_stats),
            len(unmatched),
        )
        for m in by_stats:
            logger.info("   by stats: %r -> %r", m.scraped_name, m.model_name)
        for m in unmatched:
            logger.info("   unmatched (new model?): %r score=%s votes=%s", m.model_name, m.score, m.votes)
        for m in matches:
            plan[m.scraped_name] = m

    logger.info(
        "%s %d aliases", "Inserting" if apply else "[dry run] Would insert", len(plan)
    )
    if not apply:
        logger.info("Dry run only. Re-run with --apply to execute.")
        return

    rows = [{"model_id": m.model_id, "alias_name": name} for name, m in plan.items()]
    for i in range(0, len(rows), 100):
        client.table("model_aliases").insert(rows[i:i + 100]).execute()
    logger.info("Done. Inserted %d aliases.", len(rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Alias renamed scraped names to existing models")
    parser.add_argument("--apply", action="store_true", help="write aliases (default is a dry run)")
    run(parser.parse_args().apply)
