"""Merge away the phantom models created by the July 2026 parser regression.

Between 2026-07-09 and 2026-07-20 Arena rendered the model cell without its
<a title="..."> wrapper. The old parser fell back to the whole cell's text, so
every model got re-registered under a name with the org logo title and the
"Org · License" caption glued on:

    claude-fable-5              ->  Anthropicclaude-fable-5Anthropic · Proprietary
    tulu-2-dpo-70b              ->  tulu-2-dpo-70bAI2 ImpACT Low-risk

That split each model's history across two rows in `models`. The parser is fixed
(see src/parser.py strip_org_suffix), but the phantom rows are still in the
database. This script repairs each phantom name using the organization and
license_type columns that *were* parsed correctly, matches it to the real model,
and merges the rankings over.

Dry run (default) prints the plan and touches nothing:

    python scripts/cleanup_garbage_models.py

Apply it:

    python scripts/cleanup_garbage_models.py --apply
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Dict, List, Optional, Tuple

from src.db import get_client
from src.parser import strip_org_suffix

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def fetch_all(client, table: str, columns: str, page_size: int = 1000) -> List[Dict]:
    rows: List[Dict] = []
    start = 0
    while True:
        # .order("id") keeps paging stable — without it Postgres is free to
        # return a different row order per page, duplicating and dropping rows.
        page = (
            client.table(table)
            .select(columns)
            .order("id")
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = page.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        start += page_size


def repair_name(row: Dict) -> Optional[str]:
    """Reconstruct the real model name from a phantom row, or None if it's clean."""
    name = row["canonical_name"]
    org = row.get("organization")
    lic = row.get("license_type")
    org_text = f"{org} · {lic}" if org and lic else (org or lic)

    repaired = strip_org_suffix(name, org_text, org, lic)
    return repaired if repaired and repaired != name else None


def plan_merges(models: List[Dict]) -> Tuple[List[Tuple[Dict, Dict]], List[Dict]]:
    """Pair each phantom row with the real model row it belongs to."""
    by_name = {m["canonical_name"]: m for m in models}

    # Names that were never mangled — the pool of real models to match against.
    clean = sorted(
        (m for m in models if repair_name(m) is None),
        key=lambda m: len(m["canonical_name"]),
        reverse=True,
    )

    pairs: List[Tuple[Dict, Dict]] = []
    unmatched: List[Dict] = []
    for row in models:
        repaired = repair_name(row)
        if repaired is None:
            continue

        target = by_name.get(repaired)
        if target is None:
            # The org logo's <title> sometimes differs from the caption's org
            # name ("Thinking Machines" vs "Thinky"), so the prefix strip can't
            # finish the job. Fall back to the longest real model name that ends
            # the repaired string — longest first, so "step-3.5-flash" wins over
            # a shorter name that is also a suffix.
            target = next(
                (
                    m
                    for m in clean
                    if repaired.endswith(m["canonical_name"]) and m["id"] != row["id"]
                ),
                None,
            )

        if target and target["id"] != row["id"]:
            pairs.append((row, target))
        else:
            unmatched.append({**row, "repaired": repaired})
    return pairs, unmatched


def merge(client, phantom: Dict, real: Dict, ranking_counts: Dict[str, int]):
    moved = ranking_counts.get(phantom["id"], 0)
    client.table("rankings").update({"model_id": real["id"]}).eq(
        "model_id", phantom["id"]
    ).execute()
    client.table("model_aliases").update({"model_id": real["id"]}).eq(
        "model_id", phantom["id"]
    ).execute()
    if phantom["first_seen_at"] < real["first_seen_at"]:
        client.table("models").update(
            {"first_seen_at": phantom["first_seen_at"]}
        ).eq("id", real["id"]).execute()
    client.table("models").delete().eq("id", phantom["id"]).execute()
    return moved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually perform the merges (default is a dry run)",
    )
    args = parser.parse_args()

    client = get_client()
    models = fetch_all(
        client, "models", "id, canonical_name, organization, license_type, first_seen_at"
    )
    logger.info("Loaded %d rows from `models`", len(models))

    pairs, unmatched = plan_merges(models)

    if unmatched:
        logger.warning(
            "%d phantom row(s) have no matching real model — skipping them:", len(unmatched)
        )
        for row in unmatched:
            logger.warning("   %r -> %r (no such model)", row["canonical_name"], row["repaired"])

    if not pairs:
        logger.info("Nothing to merge. Database is clean.")
        return

    ranking_rows = fetch_all(client, "rankings", "model_id")
    ranking_counts: Dict[str, int] = {}
    for r in ranking_rows:
        ranking_counts[r["model_id"]] = ranking_counts.get(r["model_id"], 0) + 1

    total_rankings = sum(ranking_counts.get(p["id"], 0) for p, _ in pairs)
    logger.info(
        "%s %d phantom model(s) holding %d ranking rows",
        "Merging" if args.apply else "[dry run] Would merge",
        len(pairs),
        total_rankings,
    )
    for phantom, real in pairs[:20]:
        logger.info(
            "   %r -> %r (%d rankings)",
            phantom["canonical_name"],
            real["canonical_name"],
            ranking_counts.get(phantom["id"], 0),
        )
    if len(pairs) > 20:
        logger.info("   ... and %d more", len(pairs) - 20)

    if not args.apply:
        logger.info("Dry run only. Re-run with --apply to execute.")
        return

    moved = 0
    for i, (phantom, real) in enumerate(pairs, 1):
        try:
            moved += merge(client, phantom, real, ranking_counts)
        except Exception as e:
            logger.error("Failed merging %r: %s", phantom["canonical_name"], e)
            sys.exit(1)
        if i % 50 == 0:
            logger.info("   ... %d/%d merged", i, len(pairs))

    logger.info("Done. Merged %d phantom models, moved %d ranking rows.", len(pairs), moved)


if __name__ == "__main__":
    main()
