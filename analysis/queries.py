from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from supabase import create_client

from src.config import settings

logger = logging.getLogger(__name__)


def get_client():
    return create_client(settings.supabase_url, settings.supabase_key)


def score_trajectory(model_name: str, category: str = "overall", days: int = 30) -> List[Dict[str, Any]]:
    """Score, CI, rank, and votes over time for a single model in a category."""
    client = get_client()
    result = (
        client.rpc(
            "get_model_trajectory",
            {"p_model_name": model_name, "p_category": category, "p_days": days},
        )
        .execute()
    )
    return result.data or []


def score_trajectory_sql() -> str:
    """SQL to create the get_model_trajectory function in Supabase."""
    return """
    CREATE OR REPLACE FUNCTION get_model_trajectory(
        p_model_name TEXT,
        p_category TEXT DEFAULT 'overall',
        p_days INT DEFAULT 30
    )
    RETURNS TABLE(
        scraped_at TIMESTAMPTZ,
        rank INT,
        score NUMERIC,
        score_ci NUMERIC,
        votes BIGINT,
        score_delta NUMERIC,
        votes_delta BIGINT,
        vote_share_pct NUMERIC
    ) AS $$
    SELECT
        s.scraped_at,
        r.rank,
        r.score,
        r.score_ci,
        r.votes,
        r.score - LAG(r.score) OVER (ORDER BY s.scraped_at) AS score_delta,
        r.votes - LAG(r.votes) OVER (ORDER BY s.scraped_at) AS votes_delta,
        r.votes::NUMERIC / NULLIF(s.total_votes, 0) * 100 AS vote_share_pct
    FROM rankings r
    JOIN models m ON m.id = r.model_id
    JOIN snapshots s ON s.id = r.snapshot_id
    WHERE m.canonical_name = p_model_name
      AND s.category = p_category
      AND s.status = 'success'
      AND s.scraped_at >= NOW() - (p_days || ' days')::INTERVAL
    ORDER BY s.scraped_at;
    $$ LANGUAGE sql STABLE;
    """


def gap_to_first(model_name: str, category: str = "overall") -> Optional[Dict[str, Any]]:
    """Current score gap to #1, with CI overlap analysis."""
    client = get_client()

    # Get the latest snapshot for this category
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
        return None

    snapshot_id = snap.data[0]["id"]

    first_place = (
        client.table("rankings")
        .select("score, score_ci, rank, model_id")
        .eq("snapshot_id", snapshot_id)
        .eq("rank", 1)
        .limit(1)
        .execute()
    )
    if not first_place.data:
        return None

    first = first_place.data[0]

    model_result = (
        client.table("models")
        .select("id")
        .eq("canonical_name", model_name)
        .limit(1)
        .execute()
    )
    if not model_result.data:
        return None

    model_ranking = (
        client.table("rankings")
        .select("score, score_ci, rank, votes")
        .eq("snapshot_id", snapshot_id)
        .eq("model_id", model_result.data[0]["id"])
        .limit(1)
        .execute()
    )
    if not model_ranking.data:
        return None

    target = model_ranking.data[0]
    gap = first["score"] - target["score"]
    combined_ci = (first["score_ci"] or 0) + (target["score_ci"] or 0)

    return {
        "model": model_name,
        "category": category,
        "rank": target["rank"],
        "score": target["score"],
        "score_ci": target["score_ci"],
        "first_place_score": first["score"],
        "first_place_ci": first["score_ci"],
        "gap": gap,
        "combined_ci": combined_ci,
        "ci_overlap": gap < combined_ci,
        "statistically_significant": gap > combined_ci,
    }


def first_seen_models(days: int = 7) -> List[Dict[str, Any]]:
    """Models that appeared on the leaderboard in the last N days."""
    from datetime import datetime, timedelta, timezone
    client = get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    result = (
        client.table("models")
        .select("canonical_name, organization, first_seen_at, is_active")
        .gte("first_seen_at", cutoff)
        .order("first_seen_at", desc=True)
        .execute()
    )
    return result.data or []


def vote_velocity(model_name: str, category: str = "overall", last_n_snapshots: int = 4) -> Optional[Dict[str, Any]]:
    """Votes per hour based on the last N snapshots in a category."""
    client = get_client()

    model_result = (
        client.table("models")
        .select("id")
        .eq("canonical_name", model_name)
        .limit(1)
        .execute()
    )
    if not model_result.data:
        return None

    model_id = model_result.data[0]["id"]

    # Get snapshot IDs for this category
    cat_snaps = (
        client.table("snapshots")
        .select("id")
        .eq("status", "success")
        .eq("category", category)
        .order("scraped_at", desc=True)
        .execute()
    )
    snap_ids = {s["id"] for s in (cat_snaps.data or [])}

    rankings = (
        client.table("rankings")
        .select("votes, created_at, snapshot_id")
        .eq("model_id", model_id)
        .order("created_at", desc=True)
        .execute()
    )

    # Filter to this category's snapshots
    filtered = [r for r in (rankings.data or []) if r["snapshot_id"] in snap_ids]
    filtered = filtered[:last_n_snapshots]

    if len(filtered) < 2:
        return None

    newest = filtered[0]
    oldest = filtered[-1]

    from datetime import datetime
    t1 = datetime.fromisoformat(newest["created_at"].replace("Z", "+00:00"))
    t0 = datetime.fromisoformat(oldest["created_at"].replace("Z", "+00:00"))
    hours = (t1 - t0).total_seconds() / 3600.0

    if hours <= 0:
        return None

    votes_gained = newest["votes"] - oldest["votes"]
    velocity = votes_gained / hours

    return {
        "model": model_name,
        "category": category,
        "votes_gained": votes_gained,
        "hours_elapsed": round(hours, 1),
        "votes_per_hour": round(velocity, 1),
        "current_votes": newest["votes"],
    }


def anomaly_detection(model_name: str, category: str = "overall", threshold_multiplier: float = 2.0) -> List[Dict[str, Any]]:
    """Flag snapshots where score moved more than threshold x CI in a category."""
    client = get_client()

    model_result = (
        client.table("models")
        .select("id")
        .eq("canonical_name", model_name)
        .limit(1)
        .execute()
    )
    if not model_result.data:
        return []

    model_id = model_result.data[0]["id"]

    # Get snapshot IDs for this category
    cat_snaps = (
        client.table("snapshots")
        .select("id")
        .eq("status", "success")
        .eq("category", category)
        .order("scraped_at", desc=True)
        .execute()
    )
    snap_ids = {s["id"] for s in (cat_snaps.data or [])}

    rankings = (
        client.table("rankings")
        .select("score, score_ci, votes, created_at, snapshot_id")
        .eq("model_id", model_id)
        .order("created_at", desc=False)
        .execute()
    )

    # Filter to this category
    filtered = [r for r in (rankings.data or []) if r["snapshot_id"] in snap_ids]

    if len(filtered) < 2:
        return []

    anomalies = []
    for i in range(1, len(filtered)):
        prev = filtered[i - 1]
        curr = filtered[i]
        delta = abs(curr["score"] - prev["score"])
        ci = curr["score_ci"] or 0

        if ci > 0 and delta > threshold_multiplier * ci:
            anomalies.append({
                "timestamp": curr["created_at"],
                "score_before": prev["score"],
                "score_after": curr["score"],
                "delta": delta,
                "ci": ci,
                "ratio": round(delta / ci, 2),
            })

    return anomalies
