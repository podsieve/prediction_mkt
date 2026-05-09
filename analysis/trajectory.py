from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from supabase import create_client

from src.config import settings

logger = logging.getLogger(__name__)


def get_client():
    return create_client(settings.supabase_url, settings.supabase_key)


def ci_tightening_rate(model_name: str) -> Optional[Dict[str, Any]]:
    """Rate at which CI is narrowing (CI units per day).

    Faster tightening = more battles being fought = more confidence in ranking.
    Returns slope from linear regression on (days_since_first_seen, score_ci).
    """
    client = get_client()

    model_result = (
        client.table("models")
        .select("id, first_seen_at")
        .eq("canonical_name", model_name)
        .maybe_single()
        .execute()
    )
    if not model_result.data:
        return None

    model_id = model_result.data["id"]
    first_seen = datetime.fromisoformat(
        model_result.data["first_seen_at"].replace("Z", "+00:00")
    )

    rankings = (
        client.table("rankings")
        .select("score_ci, votes, created_at")
        .eq("model_id", model_id)
        .not_.is_("score_ci", "null")
        .order("created_at", desc=False)
        .execute()
    )
    if not rankings.data or len(rankings.data) < 3:
        return None

    points = []
    for r in rankings.data:
        t = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        days = (t - first_seen).total_seconds() / 86400.0
        points.append((days, r["score_ci"], r["votes"]))

    n = len(points)
    sum_x = sum(p[0] for p in points)
    sum_y = sum(p[1] for p in points)
    sum_xy = sum(p[0] * p[1] for p in points)
    sum_x2 = sum(p[0] ** 2 for p in points)

    denom = n * sum_x2 - sum_x**2
    if denom == 0:
        return None

    slope = (n * sum_xy - sum_x * sum_y) / denom

    return {
        "model": model_name,
        "ci_slope_per_day": round(slope, 3),
        "tightening": slope < 0,
        "initial_ci": points[0][1],
        "latest_ci": points[-1][1],
        "initial_votes": points[0][2],
        "latest_votes": points[-1][2],
        "data_points": n,
        "days_tracked": round(points[-1][0], 1),
    }


def new_model_report(model_name: str) -> Optional[Dict[str, Any]]:
    """Full launch trajectory report for a newly released model."""
    client = get_client()

    model_result = (
        client.table("models")
        .select("id, canonical_name, organization, first_seen_at")
        .eq("canonical_name", model_name)
        .maybe_single()
        .execute()
    )
    if not model_result.data:
        return None

    model_id = model_result.data["id"]
    first_seen = datetime.fromisoformat(
        model_result.data["first_seen_at"].replace("Z", "+00:00")
    )

    rankings = (
        client.table("rankings")
        .select("rank, score, score_ci, votes, created_at")
        .eq("model_id", model_id)
        .order("created_at", desc=False)
        .execute()
    )
    if not rankings.data:
        return None

    first = rankings.data[0]
    latest = rankings.data[-1]
    now = datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
    days_tracked = (now - first_seen).total_seconds() / 86400.0

    score_change = latest["score"] - first["score"]

    # Get current #1 for gap analysis
    top_result = (
        client.table("rankings")
        .select("score, score_ci")
        .eq("rank", 1)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    gap_to_first = None
    if top_result.data:
        gap_to_first = top_result.data[0]["score"] - latest["score"]

    return {
        "model": model_name,
        "organization": model_result.data["organization"],
        "days_since_launch": round(days_tracked, 1),
        "snapshots_collected": len(rankings.data),
        "initial_rank": first["rank"],
        "current_rank": latest["rank"],
        "rank_change": first["rank"] - latest["rank"],
        "initial_score": first["score"],
        "current_score": latest["score"],
        "score_change": score_change,
        "initial_ci": first["score_ci"],
        "current_ci": latest["score_ci"],
        "initial_votes": first["votes"],
        "current_votes": latest["votes"],
        "votes_gained": latest["votes"] - first["votes"],
        "gap_to_first": gap_to_first,
    }
