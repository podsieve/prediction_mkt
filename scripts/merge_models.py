"""Merge two model entries when a codename is renamed to its real name.

Usage:
    python scripts/merge_models.py "old-codename" "real-model-name"

This merges all rankings from old-codename into real-model-name,
adds old-codename as an alias, and deletes the old model row.
"""
from __future__ import annotations

import argparse
import logging
import sys

from supabase import create_client

from src.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def merge_models(old_name: str, keep_name: str):
    client = create_client(settings.supabase_url, settings.supabase_key)

    old = (
        client.table("models")
        .select("id, canonical_name, first_seen_at")
        .eq("canonical_name", old_name)
        .maybe_single()
        .execute()
    )
    if not old.data:
        logger.error("Model '%s' not found", old_name)
        sys.exit(1)

    keep = (
        client.table("models")
        .select("id, canonical_name, first_seen_at")
        .eq("canonical_name", keep_name)
        .maybe_single()
        .execute()
    )
    if not keep.data:
        logger.error("Model '%s' not found", keep_name)
        sys.exit(1)

    old_id = old.data["id"]
    keep_id = keep.data["id"]

    if old_id == keep_id:
        logger.error("Both names resolve to the same model")
        sys.exit(1)

    # Count rankings to move
    old_rankings = (
        client.table("rankings")
        .select("id", count="exact")
        .eq("model_id", old_id)
        .execute()
    )
    count = old_rankings.count or 0

    logger.info(
        "Merging '%s' (%d rankings) into '%s'",
        old_name,
        count,
        keep_name,
    )

    # Move all rankings
    client.table("rankings").update({"model_id": keep_id}).eq("model_id", old_id).execute()

    # Add old name as alias
    client.table("model_aliases").insert(
        {"model_id": keep_id, "alias_name": old_name}
    ).execute()

    # Update first_seen_at if the old model was seen earlier
    if old.data["first_seen_at"] < keep.data["first_seen_at"]:
        client.table("models").update(
            {"first_seen_at": old.data["first_seen_at"]}
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
    args = parser.parse_args()
    merge_models(args.old_name, args.keep_name)
