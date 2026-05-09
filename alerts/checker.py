from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from supabase import create_client

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AlertEvent:
    event_type: str
    model_name: str
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)


def get_client():
    return create_client(settings.supabase_url, settings.supabase_key)


def check_new_models(client) -> List[AlertEvent]:
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    result = (
        client.table("models")
        .select("canonical_name, organization, first_seen_at")
        .gte("first_seen_at", cutoff)
        .order("first_seen_at", desc=True)
        .execute()
    )
    events = []
    for row in result.data or []:
        org = row.get("organization") or "Unknown"
        events.append(AlertEvent(
            event_type="new_model",
            model_name=row["canonical_name"],
            summary=f"New model appeared: {row['canonical_name']} ({org})",
            details=row,
        ))
    return events


def check_rank_changes(client, threshold: int = 3) -> List[AlertEvent]:
    snapshots = (
        client.table("snapshots")
        .select("id, scraped_at")
        .eq("status", "success")
        .order("scraped_at", desc=True)
        .limit(2)
        .execute()
    )
    if not snapshots.data or len(snapshots.data) < 2:
        return []

    current_snap = snapshots.data[0]["id"]
    previous_snap = snapshots.data[1]["id"]

    current = (
        client.table("rankings")
        .select("model_id, rank, score, score_ci")
        .eq("snapshot_id", current_snap)
        .execute()
    )
    previous = (
        client.table("rankings")
        .select("model_id, rank")
        .eq("snapshot_id", previous_snap)
        .execute()
    )

    prev_ranks = {r["model_id"]: r["rank"] for r in (previous.data or [])}

    model_ids = list({r["model_id"] for r in (current.data or [])} | set(prev_ranks.keys()))
    model_names = {}
    for i in range(0, len(model_ids), 100):
        batch = model_ids[i:i + 100]
        result = client.table("models").select("id, canonical_name").in_("id", batch).execute()
        for row in result.data or []:
            model_names[row["id"]] = row["canonical_name"]

    events = []
    for r in current.data or []:
        mid = r["model_id"]
        if mid not in prev_ranks:
            continue
        delta = prev_ranks[mid] - r["rank"]
        if abs(delta) >= threshold:
            name = model_names.get(mid, mid)
            direction = "rose" if delta > 0 else "dropped"
            events.append(AlertEvent(
                event_type="rank_change",
                model_name=name,
                summary=f"{name} {direction} {abs(delta)} ranks (#{prev_ranks[mid]} -> #{r['rank']})",
                details={"rank_before": prev_ranks[mid], "rank_after": r["rank"], "delta": delta},
            ))

    return events


def check_score_anomalies(client, threshold_multiplier: float = 2.0) -> List[AlertEvent]:
    snapshots = (
        client.table("snapshots")
        .select("id")
        .eq("status", "success")
        .order("scraped_at", desc=True)
        .limit(2)
        .execute()
    )
    if not snapshots.data or len(snapshots.data) < 2:
        return []

    current_snap = snapshots.data[0]["id"]
    previous_snap = snapshots.data[1]["id"]

    current = (
        client.table("rankings")
        .select("model_id, score, score_ci")
        .eq("snapshot_id", current_snap)
        .execute()
    )
    previous = (
        client.table("rankings")
        .select("model_id, score")
        .eq("snapshot_id", previous_snap)
        .execute()
    )

    prev_scores = {r["model_id"]: r["score"] for r in (previous.data or [])}

    model_ids = [r["model_id"] for r in (current.data or [])]
    model_names = {}
    for i in range(0, len(model_ids), 100):
        batch = model_ids[i:i + 100]
        result = client.table("models").select("id, canonical_name").in_("id", batch).execute()
        for row in result.data or []:
            model_names[row["id"]] = row["canonical_name"]

    events = []
    for r in current.data or []:
        mid = r["model_id"]
        ci = r.get("score_ci") or 0
        if mid not in prev_scores or ci <= 0:
            continue
        delta = abs(r["score"] - prev_scores[mid])
        if delta > threshold_multiplier * ci:
            name = model_names.get(mid, mid)
            events.append(AlertEvent(
                event_type="score_anomaly",
                model_name=name,
                summary=f"{name} score moved {delta:.1f} (>{threshold_multiplier}x CI of {ci:.1f})",
                details={"score_before": prev_scores[mid], "score_after": r["score"], "delta": delta, "ci": ci},
            ))

    return events


def run_all_checks() -> List[AlertEvent]:
    client = get_client()
    events = []
    events.extend(check_new_models(client))
    events.extend(check_rank_changes(client, threshold=settings.alert_rank_threshold))
    events.extend(check_score_anomalies(client))
    logger.info("Alert check complete: %d events found", len(events))
    return events
