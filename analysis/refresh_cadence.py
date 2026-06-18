"""Detect Arena.ai's underlying data-refresh cadence.

We scrape on a fixed cron, but Arena only recomputes the leaderboard every so
often. Between their refreshes our scrapes capture identical data. This script
finds the snapshots where the data genuinely *changed* and measures:

  - the interval between real refreshes (median / distribution)
  - whether refreshes cluster on a day-of-week or hour-of-day (UTC)
  - how many of our scrapes are redundant duplicates

Signal used: board-wide total votes per snapshot (sum of model votes, which is
robust even if the snapshots.total_votes column is null). A "refresh" is a
snapshot whose fingerprint differs from the previous snapshot in that category.

Run:  python -m analysis.refresh_cadence
      python -m analysis.refresh_cadence --category coding
"""
from __future__ import annotations

import argparse
import logging
from typing import List

import numpy as np
import pandas as pd
from dateutil.parser import isoparse

from src.config import settings
from supabase import create_client

logger = logging.getLogger(__name__)

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def get_client():
    return create_client(settings.supabase_url, settings.supabase_key)


def _fetch_all(client, table: str, columns: str, page_size: int = 1000) -> List[dict]:
    rows: List[dict] = []
    start = 0
    while True:
        resp = (client.table(table).select(columns)
                .range(start, start + page_size - 1).execute())
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def build_snapshot_series(client) -> pd.DataFrame:
    """One row per (category, snapshot) with a content fingerprint."""
    snaps = _fetch_all(client, "snapshots",
                       "id, scraped_at, category, status, total_votes")
    snaps = [s for s in snaps if s.get("status") == "success"]
    rankings = _fetch_all(client, "rankings", "snapshot_id, votes, score")

    # Aggregate rankings -> per-snapshot vote sum + a score fingerprint
    agg: dict = {}
    for r in rankings:
        sid = r["snapshot_id"]
        a = agg.setdefault(sid, {"votes_sum": 0, "score_sum": 0.0, "n": 0})
        a["votes_sum"] += int(r["votes"] or 0)
        a["score_sum"] += float(r["score"] or 0)
        a["n"] += 1

    rows = []
    for s in snaps:
        a = agg.get(s["id"], {"votes_sum": 0, "score_sum": 0.0, "n": 0})
        rows.append({
            "category": s.get("category", "overall"),
            "scraped_at": isoparse(s["scraped_at"]),
            "votes_sum": a["votes_sum"],
            # round score fingerprint to avoid float jitter
            "score_fp": round(a["score_sum"], 1),
            "n_models": a["n"],
        })
    df = pd.DataFrame(rows).sort_values(["category", "scraped_at"]).reset_index(drop=True)
    return df


def analyze_category(df: pd.DataFrame, category: str) -> None:
    print(f"\n{'='*68}\nREFRESH CADENCE  [{category}]\n{'='*68}")
    cat = df[df["category"] == category].copy().reset_index(drop=True)
    if len(cat) < 3:
        print("Not enough snapshots.")
        return

    span = cat["scraped_at"].max() - cat["scraped_at"].min()
    # Our scrape interval (gap between consecutive scrapes)
    scrape_gaps = cat["scraped_at"].diff().dt.total_seconds().dropna() / 3600.0
    print(f"Scrapes: {len(cat)} over {span.total_seconds()/86400:.1f} days "
          f"({cat['scraped_at'].min().date()} -> {cat['scraped_at'].max().date()})")
    print(f"Our scrape interval: median {scrape_gaps.median():.1f}h "
          f"(min {scrape_gaps.min():.1f}h, max {scrape_gaps.max():.1f}h)")

    # A refresh = snapshot whose content differs from the previous one.
    # Use vote sum as primary signal, score fingerprint as backup.
    changed = (
        (cat["votes_sum"] != cat["votes_sum"].shift()) |
        (cat["score_fp"] != cat["score_fp"].shift())
    )
    changed.iloc[0] = False  # first snapshot has no predecessor
    refreshes = cat[changed].copy()
    n_ref = len(refreshes)
    dup_frac = (1 - n_ref / (len(cat) - 1)) * 100 if len(cat) > 1 else 0

    print(f"\nData actually changed in {n_ref} of {len(cat)-1} scrape-to-scrape "
          f"transitions")
    print(f"  -> {dup_frac:.1f}% of our scrapes were REDUNDANT duplicates")

    if n_ref < 2:
        print("Too few real refreshes to measure an interval.")
        return

    # Interval between real refreshes
    ref_gaps = refreshes["scraped_at"].diff().dt.total_seconds().dropna() / 3600.0
    print(f"\nInterval between REAL data refreshes (hours):")
    print(f"  median {ref_gaps.median():.1f}h | mean {ref_gaps.mean():.1f}h | "
          f"min {ref_gaps.min():.1f}h | max {ref_gaps.max():.1f}h")
    # Note resolution limit
    print(f"  (resolution limited by our {scrape_gaps.median():.0f}h scrape cadence — "
          f"true refresh could be finer)")

    # Histogram of refresh intervals bucketed by likely cadence
    print(f"\n  Refresh-interval distribution:")
    buckets = [(0, 8, "< 8h"), (8, 20, "8-20h (~daily-ish)"),
               (20, 28, "20-28h (~24h DAILY)"), (28, 56, "28-56h (~2 days)"),
               (56, 1e9, "> 56h")]
    for lo, hi, lbl in buckets:
        c = ((ref_gaps >= lo) & (ref_gaps < hi)).sum()
        bar = "#" * c
        print(f"    {lbl:<22} {c:>3}  {bar}")

    # Day-of-week / hour-of-day clustering of refreshes (UTC)
    refreshes["dow"] = refreshes["scraped_at"].dt.dayofweek
    refreshes["hour"] = refreshes["scraped_at"].dt.hour
    print(f"\n  Refreshes detected by day-of-week (UTC, by when WE caught them):")
    dow_counts = refreshes["dow"].value_counts().reindex(range(7), fill_value=0)
    for d in range(7):
        c = int(dow_counts[d])
        print(f"    {DOW[d]} {c:>3}  {'#'*c}")

    print(f"\n  Refreshes by hour-of-day (UTC):")
    hour_counts = refreshes["hour"].value_counts().reindex(range(24), fill_value=0)
    busy_hours = hour_counts[hour_counts > 0]
    for h in busy_hours.index.sort_values():
        c = int(hour_counts[h])
        print(f"    {h:02d}:00 UTC {c:>3}  {'#'*c}")

    # Verdict
    med = ref_gaps.median()
    if 20 <= med <= 28:
        cad = "~DAILY (every 24h)"
    elif med < 20:
        cad = f"sub-daily (~{med:.0f}h)"
    elif med <= 56:
        cad = "~every 2 days"
    else:
        cad = f"~every {med/24:.1f} days"
    print(f"\n  VERDICT: underlying refresh looks {cad}.")
    dominant_hour = hour_counts.idxmax() if hour_counts.max() > 1 else None
    if dominant_hour is not None and hour_counts.max() >= max(3, 0.3 * n_ref):
        print(f"  Refreshes cluster around {dominant_hour:02d}:00 UTC — "
              f"likely the daily recompute window.")


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Detect Arena data refresh cadence")
    parser.add_argument("--category", default=None)
    args = parser.parse_args()

    client = get_client()
    df = build_snapshot_series(client)
    if df.empty:
        print("No snapshot data.")
        return

    categories = ([args.category] if args.category
                  else sorted(df["category"].unique()))
    for category in categories:
        analyze_category(df, category)


if __name__ == "__main__":
    main()
