"""Merge two model entries when a model is renamed.

Usage:
    python scripts/merge_models.py "old-name" "new-name"           # dry run
    python scripts/merge_models.py "old-name" "new-name" --apply

Moves all rankings from old-name onto new-name, records old-name as an alias so
future scrapes still resolve, and deletes the old model row. The rankings keep
their `raw_model_name`, so the merge stays traceable afterwards.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

# Allow `python scripts/merge_models.py` from the repo root without PYTHONPATH.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from supabase import create_client

from src.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def fetch_model(client, name: str):
    """Look up one model. Uses .limit(1) — .maybe_single() misbehaves on this client."""
    result = (
        client.table("models")
        .select("id, canonical_name, first_seen_at")
        .eq("canonical_name", name)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def snapshot_ids_for(client, model_id: str):
    """Every snapshot the model has a ranking in."""
    ids = set()
    start = 0
    while True:
        page = (
            client.table("rankings")
            .select("snapshot_id")
            .eq("model_id", model_id)
            .order("id")
            .range(start, start + 999)
            .execute()
        )
        batch = page.data or []
        ids.update(r["snapshot_id"] for r in batch)
        if len(batch) < 1000:
            return ids
        start += 1000


def merge_models(old_name: str, keep_name: str, apply: bool = False):
    client = create_client(settings.supabase_url, settings.supabase_key)

    old = fetch_model(client, old_name)
    if not old:
        logger.error("Model '%s' not found", old_name)
        sys.exit(1)

    keep = fetch_model(client, keep_name)
    if not keep:
        logger.error("Model '%s' not found", keep_name)
        sys.exit(1)

    old_id = old["id"]
    keep_id = keep["id"]

    if old_id == keep_id:
        logger.error("Both names resolve to the same model")
        sys.exit(1)

    old_snaps = snapshot_ids_for(client, old_id)
    keep_snaps = snapshot_ids_for(client, keep_id)
    count = len(old_snaps)

    # `rankings` is unique on (snapshot_id, model_id). If both models appear in
    # the same snapshot they were live at once — which means this is not a
    # rename, and merging would fail partway and leave the data half-moved.
    overlap = old_snaps & keep_snaps
    if overlap:
        logger.error(
            "'%s' and '%s' both appear in %d snapshot(s) — they co-existed on the "
            "board, so this is not a rename. Refusing to merge.",
            old_name, keep_name, len(overlap),
        )
        sys.exit(1)

    logger.info(
        "%s '%s' (%d rankings) into '%s'",
        "Merging" if apply else "[dry run] Would merge",
        old_name, count, keep_name,
    )
    if old["first_seen_at"] < keep["first_seen_at"]:
        logger.info(
            "   first_seen_at moves back to %s (from '%s')",
            old["first_seen_at"][:19], old_name,
        )
    if not apply:
        logger.info("Dry run only. Re-run with --apply to execute.")
        return

    # Move all rankings
    client.table("rankings").update({"model_id": keep_id}).eq("model_id", old_id).execute()

    # Add old name as alias
    client.table("model_aliases").insert(
        {"model_id": keep_id, "alias_name": old_name}
    ).execute()

    # Update first_seen_at if the old model was seen earlier
    if old["first_seen_at"] < keep["first_seen_at"]:
        client.table("models").update(
            {"first_seen_at": old["first_seen_at"]}
        ).eq("id", keep_id).execute()

    # Move any existing aliases from old model to keep model
    client.table("model_aliases").update({"model_id": keep_id}).eq("model_id", old_id).execute()

    # Delete old model
    client.table("models").delete().eq("id", old_id).execute()

    logger.info("Done. Merged %d rankings, added alias '%s' -> '%s'", count, old_name, keep_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge two model entries")
    parser.add_argument("old_name", help="Model name to merge away (becomes alias)")
    parser.add_argument("keep_name", help="Model name to keep (receives rankings)")
    parser.add_argument(
        "--apply", action="store_true", help="perform the merge (default is a dry run)"
    )
    args = parser.parse_args()
    merge_models(args.old_name, args.keep_name, apply=args.apply)
