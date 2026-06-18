"""One-time empirical studies on the Arena leaderboard data.

These answer four research questions that decide whether the scraped data
carries predictive signal (and therefore which recurring alerts are worth
building):

  Q1  When a new model launches, does it impact existing models' scores?
  Q2  What is the typical launch trajectory (cold-start climb vs hot-start fade)?
  Q3  Do vote changes relate to CI / score / rank (and do they *lead* rank moves)?
  Q4  Within the top 5, is there a signal that the #1 rank could change?

Run:  python -m analysis.studies                  # all studies, both categories
      python -m analysis.studies --category coding
      python -m analysis.studies --study q3       # one study only

This is read-only. It fetches the full rankings table once (paginated) and
builds an in-memory pandas panel, then runs the studies offline.
"""
from __future__ import annotations

import argparse
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from dateutil.parser import isoparse

from src.config import settings
from supabase import create_client

logger = logging.getLogger(__name__)

# Tier boundary used by Q4 / context
TOP_N_VULNERABILITY = 5


def get_client():
    return create_client(settings.supabase_url, settings.supabase_key)


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def _fetch_all(client, table: str, columns: str, page_size: int = 1000) -> List[dict]:
    """Fetch every row from a table via range pagination."""
    rows: List[dict] = []
    start = 0
    while True:
        resp = (
            client.table(table)
            .select(columns)
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def load_panel(client) -> pd.DataFrame:
    """Build a tidy panel: one row per (model, category, snapshot).

    Columns: model, organization, category, scraped_at, rank, score,
    score_ci, votes, first_seen_at, total_votes — plus per-model-per-category
    deltas (score_delta, votes_delta, ci_delta, rank_delta) and the previous
    snapshot's votes_delta for lag analysis.
    """
    logger.info("Fetching models...")
    models = _fetch_all(
        client, "models", "id, canonical_name, organization, first_seen_at"
    )
    model_map = {m["id"]: m for m in models}

    logger.info("Fetching snapshots...")
    snaps = _fetch_all(
        client,
        "snapshots",
        "id, scraped_at, category, status, total_votes, total_models",
    )
    snap_map = {
        s["id"]: s for s in snaps if s.get("status") == "success"
    }

    logger.info("Fetching rankings (this is the big one)...")
    rankings = _fetch_all(
        client,
        "rankings",
        "snapshot_id, model_id, rank, score, score_ci, votes, raw_model_name",
    )
    logger.info("Fetched %d ranking rows", len(rankings))

    records = []
    for r in rankings:
        snap = snap_map.get(r["snapshot_id"])
        if snap is None:
            continue  # skip rankings from failed snapshots
        model = model_map.get(r["model_id"], {})
        records.append(
            {
                "model_id": r["model_id"],
                "model": model.get("canonical_name", r.get("raw_model_name", "?")),
                "organization": model.get("organization"),
                "first_seen_at": model.get("first_seen_at"),
                "category": snap.get("category", "overall"),
                "scraped_at": snap["scraped_at"],
                "snapshot_total_votes": snap.get("total_votes"),
                "rank": r["rank"],
                "score": float(r["score"]) if r["score"] is not None else np.nan,
                "score_ci": float(r["score_ci"]) if r.get("score_ci") is not None else np.nan,
                "votes": int(r["votes"]) if r["votes"] is not None else 0,
            }
        )

    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df

    df["scraped_at"] = df["scraped_at"].apply(isoparse)
    df["first_seen_at"] = df["first_seen_at"].apply(
        lambda x: isoparse(x) if x else pd.NaT
    )
    df = df.sort_values(["category", "model_id", "scraped_at"]).reset_index(drop=True)

    # Per (model, category) deltas vs the previous snapshot for that model
    grp = df.groupby(["category", "model_id"], sort=False)
    df["score_delta"] = grp["score"].diff()
    df["votes_delta"] = grp["votes"].diff()
    df["ci_delta"] = grp["score_ci"].diff()
    df["rank_delta"] = grp["rank"].diff()  # +ve = rank number went up = got WORSE
    # Lead of rank: next snapshot's rank change (for lag/lead correlation in Q3)
    df["rank_delta_next"] = grp["rank"].diff().shift(-1)
    df["hours_since_prev"] = grp["scraped_at"].diff().dt.total_seconds() / 3600.0

    return df


# --------------------------------------------------------------------------- #
# Q1: Launch impact on incumbents
# --------------------------------------------------------------------------- #
def study_launch_impact(df: pd.DataFrame, category: str, n_neighbors: int = 5,
                        window_days: int = 14) -> None:
    print(f"\n{'='*70}\nQ1  LAUNCH IMPACT ON INCUMBENTS  [{category}]\n{'='*70}")
    cat = df[df["category"] == category]
    if cat.empty:
        print("No data.")
        return

    first_snap = cat["scraped_at"].min()
    # A "launch" within our window = model whose first_seen_at is after we
    # started collecting (so we have a before/after for neighbours).
    launches = (
        cat.dropna(subset=["first_seen_at"])
        .groupby("model_id")
        .agg(model=("model", "first"),
             first_seen=("first_seen_at", "first"),
             debut_scrape=("scraped_at", "min"))
        .reset_index()
    )
    # Keep launches that happened at least `window_days` after collection start
    # AND have at least a little post-launch history.
    launches = launches[launches["debut_scrape"] > first_snap]
    if launches.empty:
        print("No new-model launches observed during the collection window.")
        print("(Every model currently on the board was already present at the")
        print(" first snapshot, so there is no before/after to measure.)")
        return

    print(f"Observed {len(launches)} launch(es) after collection start "
          f"({first_snap.date()}):\n")

    any_result = False
    for _, L in launches.iterrows():
        debut = L["debut_scrape"]
        # Newcomer's debut score
        debut_row = cat[(cat["model_id"] == L["model_id"]) &
                        (cat["scraped_at"] == debut)]
        if debut_row.empty:
            continue
        debut_score = debut_row["score"].iloc[0]
        debut_rank = int(debut_row["rank"].iloc[0])

        # Neighbours = models ranked just below the newcomer at debut
        debut_board = cat[cat["scraped_at"] == debut]
        neighbors = debut_board[
            (debut_board["rank"] > debut_rank) &
            (debut_board["rank"] <= debut_rank + n_neighbors)
        ]["model_id"].tolist()
        if not neighbors:
            continue

        # For each neighbour: score slope before vs after the launch
        rows = []
        for nb in neighbors:
            nb_hist = cat[cat["model_id"] == nb].sort_values("scraped_at")
            before = nb_hist[(nb_hist["scraped_at"] < debut) &
                             (nb_hist["scraped_at"] >= debut - pd.Timedelta(days=window_days))]
            after = nb_hist[(nb_hist["scraped_at"] >= debut) &
                            (nb_hist["scraped_at"] <= debut + pd.Timedelta(days=window_days))]
            if len(before) < 2 or len(after) < 2:
                continue
            score_before = before["score"].mean()
            score_after = after["score"].mean()
            rows.append({
                "model": nb_hist["model"].iloc[0],
                "score_before": round(score_before, 1),
                "score_after": round(score_after, 1),
                "shift": round(score_after - score_before, 1),
            })
        if rows:
            any_result = True
            res = pd.DataFrame(rows)
            mean_shift = res["shift"].mean()
            print(f"--- {L['model']} (debuted #{debut_rank}, score {debut_score:.0f}) ---")
            print(res.to_string(index=False))
            print(f"  Mean neighbour score shift after launch: {mean_shift:+.1f} pts")
            verdict = ("incumbents LOST score (displacement effect)" if mean_shift < -1
                       else "incumbents GAINED score" if mean_shift > 1
                       else "no meaningful effect")
            print(f"  -> {verdict}\n")

    if not any_result:
        print("Launches found, but not enough before/after history around them")
        print("to measure neighbour score shifts yet. Re-run after more data.")


# --------------------------------------------------------------------------- #
# Q2: Launch trajectory cohort
# --------------------------------------------------------------------------- #
def study_launch_trajectory(df: pd.DataFrame, category: str) -> None:
    print(f"\n{'='*70}\nQ2  TYPICAL LAUNCH TRAJECTORY  [{category}]\n{'='*70}")
    cat = df[df["category"] == category].dropna(subset=["first_seen_at"])
    if cat.empty:
        print("No data.")
        return

    first_snap = df[df["category"] == category]["scraped_at"].min()
    # Only models we actually saw debut (first_seen after collection start),
    # otherwise "days since launch" is wrong.
    cat = cat.copy()
    cat["days_since_launch"] = (
        (cat["scraped_at"] - cat["first_seen_at"]).dt.total_seconds() / 86400.0
    )
    debuted = cat[cat["first_seen_at"] > first_snap]
    cohort_ids = debuted["model_id"].unique()
    if len(cohort_ids) == 0:
        print("No models debuted during the collection window — every current")
        print("model predates our first snapshot, so launch trajectories can't")
        print("be reconstructed. (We only know their state since we started.)")
        print("\nFalling back: relative trajectory since FIRST OBSERVED snapshot")
        print("for all models (treats first sighting as t=0):\n")
        cat["days_since_launch"] = (
            (cat["scraped_at"] - cat.groupby("model_id")["scraped_at"].transform("min"))
            .dt.total_seconds() / 86400.0
        )
        cohort_ids = cat["model_id"].unique()
        debuted = cat

    # Per-model: initial vs current rank/score/ci
    summary = []
    for mid in cohort_ids:
        h = debuted[debuted["model_id"] == mid].sort_values("days_since_launch")
        if len(h) < 3:
            continue
        first, last = h.iloc[0], h.iloc[-1]
        summary.append({
            "model": first["model"],
            "days": round(last["days_since_launch"], 1),
            "rank_0": int(first["rank"]),
            "rank_now": int(last["rank"]),
            "rank_chg": int(first["rank"] - last["rank"]),  # +ve = climbed
            "score_0": round(first["score"], 0),
            "score_now": round(last["score"], 0),
            "score_chg": round(last["score"] - first["score"], 1),
            "ci_0": round(first["score_ci"], 1) if pd.notna(first["score_ci"]) else None,
            "ci_now": round(last["score_ci"], 1) if pd.notna(last["score_ci"]) else None,
        })
    if not summary:
        print("Not enough per-model history (need >=3 snapshots each).")
        return

    s = pd.DataFrame(summary).sort_values("days", ascending=False)
    print(s.to_string(index=False))

    climbed = (s["rank_chg"] > 0).sum()
    fell = (s["rank_chg"] < 0).sum()
    flat = (s["rank_chg"] == 0).sum()
    print(f"\nCohort size: {len(s)} models")
    print(f"  Climbed after debut: {climbed}   Fell: {fell}   Flat: {flat}")
    print(f"  Mean rank change:  {s['rank_chg'].mean():+.1f} positions")
    print(f"  Mean score change: {s['score_chg'].mean():+.1f} pts")
    if s["ci_0"].notna().any():
        print(f"  Mean initial CI:   {s['ci_0'].mean():.1f}  ->  "
              f"latest CI: {s['ci_now'].mean():.1f}  "
              f"(tightening = converging to true rank)")
    pattern = ("COLD START -> CLIMB (debut low, rise as votes arrive)"
               if s["rank_chg"].mean() > 0.5
               else "HOT START -> FADE (debut high, settle down)"
               if s["rank_chg"].mean() < -0.5
               else "FLAT (lands at rank immediately)")
    print(f"  Dominant pattern: {pattern}")


# --------------------------------------------------------------------------- #
# Q3: Votes vs CI / score / rank  (+ lead/lag)
# --------------------------------------------------------------------------- #
def study_vote_correlation(df: pd.DataFrame, category: str) -> None:
    print(f"\n{'='*70}\nQ3  DO VOTE CHANGES RELATE TO CI / SCORE / RANK?  [{category}]\n{'='*70}")
    cat = df[df["category"] == category].copy()
    # Only consecutive snapshots (~2h apart); drop the first row per model (NaN deltas)
    d = cat.dropna(subset=["votes_delta", "score_delta", "ci_delta", "rank_delta"])
    # Filter to sane consecutive intervals
    d = d[(d["votes_delta"] >= 0)]  # votes are cumulative; negative = data glitch
    if len(d) < 30:
        print(f"Only {len(d)} delta rows — too few for stable correlations.")
        return

    print(f"Sample: {len(d)} consecutive (model, snapshot) deltas\n")

    def corr(a, b):
        sub = d[[a, b]].dropna()
        if len(sub) < 10 or sub[a].std() == 0 or sub[b].std() == 0:
            return np.nan, len(sub)
        return sub[a].corr(sub[b]), len(sub)

    pairs = [
        ("votes_delta", "ci_delta",   "more votes -> CI narrows? (expect NEG, mechanical)"),
        ("votes_delta", "score_delta","more votes -> score moves? (expect ~0)"),
        ("votes_delta", "rank_delta", "more votes -> rank moves same snapshot? (expect ~0)"),
        ("votes_delta", "rank_delta_next", "VOTE SPIKE -> rank moves NEXT snapshot? (the money test)"),
    ]
    print(f"{'relationship':<55}{'corr':>8}{'n':>8}")
    print("-" * 71)
    for a, b, desc in pairs:
        c, n = corr(a, b)
        cstr = f"{c:+.3f}" if not np.isnan(c) else "  n/a"
        print(f"{desc:<55}{cstr:>8}{n:>8}")

    # Static fraction
    static = (d["score_delta"].abs() < 0.05).mean() * 100
    print(f"\nFraction of intervals with ~zero score change: {static:.1f}%")

    # Lead test detail: does a big vote spike predict a rank move next time?
    # Most 2h intervals add zero new votes, so split on a threshold taken from
    # the *positive* vote deltas (a real spike), comparing against everything
    # below it (which includes all the zero-vote intervals).
    d2 = d.dropna(subset=["rank_delta_next"]).copy()
    positive = d2[d2["votes_delta"] > 0]["votes_delta"]
    zero_frac = (d2["votes_delta"] == 0).mean() * 100
    print(f"\nIntervals with zero new votes for a model: {zero_frac:.1f}%")
    if len(d2) > 50 and len(positive) > 20:
        thresh = positive.quantile(0.90)  # top-decile among real vote gains
        spike = d2[d2["votes_delta"] >= thresh]
        rest = d2[d2["votes_delta"] < thresh]
        spike_move = spike["rank_delta_next"].abs().mean()
        rest_move = rest["rank_delta_next"].abs().mean()
        print(f"Lead test — vote spikes (>= {thresh:.0f} votes/interval, "
              f"n={len(spike)}):")
        print(f"  |rank change next snapshot| after a spike: {spike_move:.3f}")
        print(f"  |rank change next snapshot| otherwise:     {rest_move:.3f}")
        ratio = spike_move / max(rest_move, 1e-9)
        verdict = ("votes LEAD rank moves -> vote-spike alert is WORTH building"
                   if ratio > 1.5 else
                   "votes do NOT lead rank moves -> drop the vote-spike alert")
        print(f"  ratio: {ratio:.2f}x  ->  {verdict}")
    else:
        print("Too few non-zero vote deltas for a lead test.")


# --------------------------------------------------------------------------- #
# Q4: Is the #1 rank vulnerable?
# --------------------------------------------------------------------------- #
def study_top_vulnerability(df: pd.DataFrame, category: str) -> None:
    print(f"\n{'='*70}\nQ4  IS THE #1 RANK VULNERABLE?  [{category}]\n{'='*70}")
    cat = df[df["category"] == category]
    if cat.empty:
        print("No data.")
        return

    latest_t = cat["scraped_at"].max()
    board = cat[cat["scraped_at"] == latest_t].sort_values("rank")
    top = board[board["rank"] <= TOP_N_VULNERABILITY]
    if len(top) < 2:
        print("Not enough models in the top tier.")
        return

    print(f"Latest snapshot: {latest_t}\n")
    print(f"{'rank':<5}{'model':<28}{'score':>8}{'±CI':>7}{'gap↑':>7}{'overlap?':>10}")
    print("-" * 65)
    rows = top.to_dict("records")
    for i, r in enumerate(rows):
        gap_up = "" if i == 0 else f"{rows[i-1]['score'] - r['score']:.1f}"
        if i == 0:
            overlap = "—"
        else:
            combined_ci = (r["score_ci"] or 0) + (rows[i-1]["score_ci"] or 0)
            gap = rows[i-1]["score"] - r["score"]
            overlap = "YES" if gap < combined_ci else "no"
        ci = r["score_ci"] if pd.notna(r["score_ci"]) else 0
        print(f"#{r['rank']:<4}{r['model'][:27]:<28}{r['score']:>8.0f}"
              f"{ci:>7.1f}{gap_up:>7}{overlap:>10}")

    # #1 vs #2 detail
    first, second = rows[0], rows[1]
    gap = first["score"] - second["score"]
    combined_ci = (first["score_ci"] or 0) + (second["score_ci"] or 0)
    overlap = gap < combined_ci

    # Gap trend over recent snapshots
    fid, sid = first["model_id"], second["model_id"]
    f_hist = cat[cat["model_id"] == fid][["scraped_at", "score"]].rename(
        columns={"score": "s1"})
    s_hist = cat[cat["model_id"] == sid][["scraped_at", "score"]].rename(
        columns={"score": "s2"})
    merged = pd.merge(f_hist, s_hist, on="scraped_at").sort_values("scraped_at")
    merged["gap"] = merged["s1"] - merged["s2"]
    trend_str = "n/a"
    if len(merged) >= 4:
        x = (merged["scraped_at"] - merged["scraped_at"].min()).dt.total_seconds() / 86400.0
        slope = np.polyfit(x, merged["gap"], 1)[0]  # gap pts per day
        per_week = slope * 7
        merged_recent = merged.tail(10)
        direction = "CLOSING" if per_week < -0.1 else "WIDENING" if per_week > 0.1 else "STABLE"
        trend_str = f"{per_week:+.2f} pts/week ({direction})"

    print(f"\n#1 {first['model']} vs #2 {second['model']}:")
    print(f"  score gap        : {gap:.1f} pts")
    print(f"  combined CI       : {combined_ci:.1f} pts")
    print(f"  CI overlap        : {'YES — statistically a tie' if overlap else 'no — #1 separated'}")
    print(f"  gap trend         : {trend_str}")

    # Has #1 ever changed?
    leaders = (cat[cat["rank"] == 1]
               .sort_values("scraped_at")[["scraped_at", "model"]])
    n_leaders = leaders["model"].nunique()
    changes = (leaders["model"] != leaders["model"].shift()).sum() - 1
    span_days = (leaders["scraped_at"].max() - leaders["scraped_at"].min()).total_seconds() / 86400.0
    print(f"  #1 history        : {n_leaders} distinct leader(s), "
          f"{max(changes,0)} change(s) over {span_days:.0f} days")

    if overlap:
        verdict = "VULNERABLE — #1 and #2 are statistically tied right now."
    elif trend_str != "n/a" and "CLOSING" in trend_str:
        verdict = "LOCKED for now, but the gap is closing — watch this."
    else:
        verdict = "LOCKED — #1 is clearly separated and the gap is not closing."
    print(f"  VERDICT           : {verdict}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
STUDIES = {
    "q1": study_launch_impact,
    "q2": study_launch_trajectory,
    "q3": study_vote_correlation,
    "q4": study_top_vulnerability,
}


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Arena leaderboard empirical studies")
    parser.add_argument("--category", default=None,
                        help="Category slug (default: all configured)")
    parser.add_argument("--study", choices=list(STUDIES), default=None,
                        help="Run only one study (default: all)")
    args = parser.parse_args()

    client = get_client()
    df = load_panel(client)
    if df.empty:
        print("No data returned from Supabase.")
        return

    categories = ([args.category] if args.category
                  else list(settings.scrape_categories))
    studies = ([args.study] if args.study else list(STUDIES))

    print(f"\nLoaded panel: {len(df):,} rows, "
          f"{df['model_id'].nunique()} models, "
          f"{df['scraped_at'].nunique()} snapshots, "
          f"categories={sorted(df['category'].unique())}")

    for category in categories:
        for key in studies:
            STUDIES[key](df, category)


if __name__ == "__main__":
    main()
