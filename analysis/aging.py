"""Rank/score trajectories aligned on each model's debut rather than the calendar.

Calendar-time charts hide the pattern this is built to show: a model's score
barely moves after launch while its rank decays, because the field around it
keeps improving. Aligning every model on day 0 = its first appearance makes
those trajectories comparable.

Only models that debuted *after* scraping began are usable. The 357 models
present in the first snapshot have a `first_seen_at` of 2026-05-09 — the day
the scraper started, not the day they launched — so including them would
measure the collection window instead of the models.
"""
from __future__ import annotations

import logging
from datetime import datetime
from statistics import median
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Models needing at least this many days of history before they count toward a
# median. Below it the curve is dominated by a handful of very fresh models.
MIN_DAYS_FOR_MEDIAN = 2


def parse_ts(value: str) -> datetime:
    """Parse a Postgres timestamptz. Handles the 5-digit microseconds that
    `datetime.fromisoformat` rejects on Python < 3.11."""
    import re

    value = re.sub(
        r"\.(\d{1,6})\d*", lambda m: "." + m.group(1).ljust(6, "0"), value.replace("Z", "+00:00")
    )
    return datetime.fromisoformat(value)


def fetch_category_snapshots(client, category: str) -> Dict[str, datetime]:
    """Successful snapshot id -> scraped_at for one category."""
    rows: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("snapshots")
            .select("id, scraped_at")
            .eq("status", "success")
            .eq("category", category)
            .order("id")
            .range(start, start + 999)
            .execute()
        )
        batch = page.data or []
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000
    return {r["id"]: parse_ts(r["scraped_at"]) for r in rows}


def fetch_debut_cohort(client, category_start: datetime) -> List[Dict[str, Any]]:
    """Models whose first sighting postdates the start of collection.

    A model already on the board when scraping began has an unknowable real
    age, so it can't sit on a since-debut axis.
    """
    rows: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("models")
            .select("id, canonical_name, organization, first_seen_at")
            .gt("first_seen_at", category_start.isoformat())
            .order("id")
            .range(start, start + 999)
            .execute()
        )
        batch = page.data or []
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000
    return rows


def fetch_rankings_for(client, model_ids: Sequence[str]) -> List[Dict[str, Any]]:
    """All ranking rows for the given models, across every category."""
    rows: List[Dict] = []
    for i in range(0, len(model_ids), 50):
        batch_ids = list(model_ids[i:i + 50])
        start = 0
        while True:
            page = (
                client.table("rankings")
                .select("model_id, snapshot_id, rank, score, score_ci, votes")
                .in_("model_id", batch_ids)
                .order("id")
                .range(start, start + 999)
                .execute()
            )
            batch = page.data or []
            rows.extend(batch)
            if len(batch) < 1000:
                break
            start += 1000
    return rows


def build_curves(
    rankings: List[Dict[str, Any]],
    snapshots: Dict[str, datetime],
    models: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Turn raw rankings into one debut-aligned curve per model.

    Each curve carries deltas from the model's own first observation, so a
    model that opened at rank 3 and one that opened at rank 200 are directly
    comparable.
    """
    by_model: Dict[str, List[Dict[str, Any]]] = {}
    for row in rankings:
        when = snapshots.get(row["snapshot_id"])
        if when is None:
            continue  # a different category, or a failed snapshot
        by_model.setdefault(row["model_id"], []).append({**row, "when": when})

    curves = []
    for model_id, observations in by_model.items():
        observations.sort(key=lambda r: r["when"])
        debut = observations[0]
        points = [
            {
                "days": (o["when"] - debut["when"]).total_seconds() / 86400.0,
                "rank": o["rank"],
                "score": o["score"],
                "score_ci": o["score_ci"],
                "votes": o["votes"],
                "rank_delta": o["rank"] - debut["rank"],
                "score_delta": o["score"] - debut["score"],
                "when": o["when"],
            }
            for o in observations
        ]
        info = models.get(model_id, {})
        curves.append({
            "model_id": model_id,
            "model_name": info.get("canonical_name", "?"),
            "organization": info.get("organization") or "",
            "debut_at": debut["when"],
            "debut_rank": debut["rank"],
            "debut_score": debut["score"],
            "days_tracked": points[-1]["days"],
            "points": points,
        })

    curves.sort(key=lambda c: c["debut_at"])
    return curves


def step_interpolate(points: List[Dict[str, Any]], field: str, grid: Sequence[float]) -> List[Optional[float]]:
    """Sample a curve onto a shared day grid, carrying the last value forward.

    Step, not linear: a model's score is whatever Arena last published, and it
    holds that value until the next recompute.
    """
    out: List[Optional[float]] = []
    i = 0
    current: Optional[float] = None
    last_day = points[-1]["days"] if points else -1
    for day in grid:
        while i < len(points) and points[i]["days"] <= day:
            current = points[i][field]
            i += 1
        # Don't extrapolate past a model's last observation.
        out.append(current if day <= last_day else None)
    return out


def median_curve(
    curves: List[Dict[str, Any]], field: str, grid: Sequence[float], min_models: int = 3
) -> Dict[str, List[Optional[float]]]:
    """Median and interquartile band across curves, per grid point.

    Grid points backed by fewer than `min_models` are left empty rather than
    drawn from one or two survivors.
    """
    sampled = [step_interpolate(c["points"], field, grid) for c in curves]
    med: List[Optional[float]] = []
    lo: List[Optional[float]] = []
    hi: List[Optional[float]] = []
    for idx in range(len(grid)):
        values = sorted(v for v in (s[idx] for s in sampled) if v is not None)
        if len(values) < min_models:
            med.append(None); lo.append(None); hi.append(None)
            continue
        med.append(median(values))
        lo.append(values[len(values) // 4])
        hi.append(values[(3 * len(values)) // 4])
    return {"median": med, "q1": lo, "q3": hi, "n": [
        sum(1 for s in sampled if s[i] is not None) for i in range(len(grid))
    ]}


def day_grid(max_days: float, step: float = 0.5) -> List[float]:
    n = int(max_days / step) + 1
    return [i * step for i in range(n)]


def rank_decay(category: str = "overall", client=None) -> Dict[str, Any]:
    """Everything the aging-curve view needs for one category."""
    if client is None:
        from analysis.queries import get_client
        client = get_client()

    snapshots = fetch_category_snapshots(client, category)
    empty = {"category": category, "curves": [], "category_start": None, "censored": 0}
    if not snapshots:
        return empty

    category_start = min(snapshots.values())
    first_snapshot_id = min(snapshots, key=lambda k: snapshots[k])
    # Everything in the very first snapshot predates collection, so its real
    # age is unknowable — count it so the view can say what it left out.
    censored = (
        client.table("rankings")
        .select("id", count="exact")
        .eq("snapshot_id", first_snapshot_id)
        .limit(1)
        .execute()
        .count
    ) or 0

    cohort = fetch_debut_cohort(client, category_start)
    if not cohort:
        return {**empty, "category_start": category_start, "censored": censored}

    models = {m["id"]: m for m in cohort}
    rankings = fetch_rankings_for(client, list(models))
    curves = build_curves(rankings, snapshots, models)

    logger.info(
        "[%s] %d debut-aligned curves from %d cohort models (%d censored)",
        category, len(curves), len(cohort), censored,
    )
    return {
        "category": category,
        "category_start": category_start,
        "curves": curves,
        "censored": censored,
    }
