from __future__ import annotations

import logging
from typing import Any, Dict, List

from supabase import create_client

from src.config import settings

logger = logging.getLogger(__name__)


def get_client():
    return create_client(settings.supabase_url, settings.supabase_key)


def build_digest() -> str:
    client = get_client()

    snapshot = (
        client.table("snapshots")
        .select("id, scraped_at, total_models, total_votes")
        .eq("status", "success")
        .order("scraped_at", desc=True)
        .limit(1)
        .execute()
    )
    if not snapshot.data:
        return "<p>No snapshot data available.</p>"

    snap = snapshot.data[0]
    snap_id = snap["id"]

    top_models = (
        client.table("rankings")
        .select("rank, score, score_ci, votes, model_id")
        .eq("snapshot_id", snap_id)
        .order("rank")
        .limit(10)
        .execute()
    )

    model_ids = [r["model_id"] for r in (top_models.data or [])]
    model_names = {}
    if model_ids:
        result = client.table("models").select("id, canonical_name, organization").in_("id", model_ids).execute()
        for row in result.data or []:
            model_names[row["id"]] = row

    top_movers = _get_top_movers(client, snap_id)
    new_models = _get_recent_new_models(client)

    parts = []
    parts.append(f"<p><strong>Last scraped:</strong> {snap['scraped_at']}<br>")
    parts.append(f"<strong>Models:</strong> {snap['total_models']} &nbsp; ")
    parts.append(f"<strong>Total votes:</strong> {snap['total_votes']:,}</p>")

    parts.append("<h3>Top 10</h3>")
    parts.append('<table style="border-collapse: collapse; width: 100%;">')
    parts.append('<tr style="border-bottom: 2px solid #333;">'
                 '<th style="text-align:left; padding:4px;">Rank</th>'
                 '<th style="text-align:left; padding:4px;">Model</th>'
                 '<th style="text-align:right; padding:4px;">Score</th>'
                 '<th style="text-align:right; padding:4px;">Votes</th></tr>')
    for r in top_models.data or []:
        info = model_names.get(r["model_id"], {})
        name = info.get("canonical_name", "?")
        ci = f" &plusmn;{r['score_ci']}" if r.get("score_ci") else ""
        parts.append(
            f'<tr style="border-bottom: 1px solid #eee;">'
            f'<td style="padding:4px;">#{r["rank"]}</td>'
            f'<td style="padding:4px;">{name}</td>'
            f'<td style="text-align:right; padding:4px;">{r["score"]}{ci}</td>'
            f'<td style="text-align:right; padding:4px;">{r["votes"]:,}</td></tr>'
        )
    parts.append("</table>")

    if top_movers:
        parts.append("<h3>Biggest Movers (24h)</h3><ul>")
        for m in top_movers:
            direction = "up" if m["delta"] > 0 else "down"
            arrow = "&#9650;" if m["delta"] > 0 else "&#9660;"
            parts.append(f'<li>{arrow} {m["name"]} — {direction} {abs(m["delta"])} ranks '
                         f'(#{m["rank_before"]} &rarr; #{m["rank_after"]})</li>')
        parts.append("</ul>")

    if new_models:
        parts.append("<h3>New Models (7 days)</h3><ul>")
        for m in new_models:
            org = m.get("organization") or ""
            parts.append(f'<li>{m["canonical_name"]} {f"({org})" if org else ""}</li>')
        parts.append("</ul>")

    return "\n".join(parts)


def _get_top_movers(client, current_snap_id: str, limit: int = 5) -> List[Dict[str, Any]]:
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

    previous_snap = snapshots.data[1]["id"]

    current = (
        client.table("rankings")
        .select("model_id, rank")
        .eq("snapshot_id", current_snap_id)
        .execute()
    )
    previous = (
        client.table("rankings")
        .select("model_id, rank")
        .eq("snapshot_id", previous_snap)
        .execute()
    )

    prev_ranks = {r["model_id"]: r["rank"] for r in (previous.data or [])}

    movers = []
    for r in current.data or []:
        mid = r["model_id"]
        if mid in prev_ranks:
            delta = prev_ranks[mid] - r["rank"]
            if delta != 0:
                movers.append({"model_id": mid, "delta": delta,
                               "rank_before": prev_ranks[mid], "rank_after": r["rank"]})

    movers.sort(key=lambda x: abs(x["delta"]), reverse=True)
    movers = movers[:limit]

    if movers:
        mids = [m["model_id"] for m in movers]
        result = client.table("models").select("id, canonical_name").in_("id", mids).execute()
        names = {row["id"]: row["canonical_name"] for row in (result.data or [])}
        for m in movers:
            m["name"] = names.get(m["model_id"], m["model_id"])

    return movers


def _get_recent_new_models(client, days: int = 7) -> List[Dict[str, Any]]:
    result = (
        client.table("models")
        .select("canonical_name, organization, first_seen_at")
        .gte("first_seen_at", f"now() - interval '{days} days'")
        .order("first_seen_at", desc=True)
        .execute()
    )
    return result.data or []
