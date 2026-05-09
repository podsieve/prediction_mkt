from __future__ import annotations

import os
import sys

import streamlit as st

# Allow imports from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load secrets into env vars before importing src.config (which validates on import)
try:
    for key in ("SUPABASE_URL", "SUPABASE_KEY"):
        if key in st.secrets:
            os.environ[key] = st.secrets[key]
except Exception:
    pass

import plotly.graph_objects as go
from supabase import create_client

from src.config import settings

st.set_page_config(page_title="Arena Leaderboard", layout="wide")


@st.cache_data(ttl=300)
def get_client():
    return create_client(settings.supabase_url, settings.supabase_key)


@st.cache_data(ttl=300)
def get_latest_snapshot():
    client = get_client()
    result = (
        client.table("snapshots")
        .select("id, scraped_at, total_models, total_votes")
        .eq("status", "success")
        .order("scraped_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


@st.cache_data(ttl=300)
def get_latest_rankings(snapshot_id: str):
    client = get_client()
    rankings = (
        client.table("rankings")
        .select("rank, score, score_ci, votes, model_id")
        .eq("snapshot_id", snapshot_id)
        .order("rank")
        .execute()
    )
    if not rankings.data:
        return []

    model_ids = [r["model_id"] for r in rankings.data]
    model_names = {}
    for i in range(0, len(model_ids), 100):
        batch = model_ids[i:i + 100]
        result = client.table("models").select("id, canonical_name, organization").in_("id", batch).execute()
        for row in result.data or []:
            model_names[row["id"]] = row

    for r in rankings.data:
        info = model_names.get(r["model_id"], {})
        r["model_name"] = info.get("canonical_name", "?")
        r["organization"] = info.get("organization", "")

    return rankings.data


@st.cache_data(ttl=300)
def get_model_history(model_name: str, days: int = 30):
    client = get_client()
    model = (
        client.table("models")
        .select("id")
        .eq("canonical_name", model_name)
        .limit(1)
        .execute()
    )
    if not model.data:
        return []

    model_id = model.data[0]["id"]
    rankings = (
        client.table("rankings")
        .select("rank, score, score_ci, votes, created_at, snapshot_id")
        .eq("model_id", model_id)
        .order("created_at", desc=False)
        .execute()
    )

    snapshots_result = (
        client.table("snapshots")
        .select("id, scraped_at, total_votes")
        .eq("status", "success")
        .execute()
    )
    snap_map = {s["id"]: s for s in (snapshots_result.data or [])}

    for r in rankings.data or []:
        snap = snap_map.get(r["snapshot_id"], {})
        r["scraped_at"] = snap.get("scraped_at", r["created_at"])
        total = snap.get("total_votes") or 0
        r["vote_share_pct"] = (r["votes"] / total * 100) if total > 0 else 0

    return rankings.data or []


@st.cache_data(ttl=300)
def get_new_models(days: int = 30):
    client = get_client()
    result = (
        client.table("models")
        .select("canonical_name, organization, first_seen_at, is_active")
        .gte("first_seen_at", f"now() - interval '{days} days'")
        .order("first_seen_at", desc=True)
        .execute()
    )
    return result.data or []


@st.cache_data(ttl=300)
def get_all_model_names():
    client = get_client()
    result = (
        client.table("models")
        .select("canonical_name")
        .eq("is_active", True)
        .order("canonical_name")
        .execute()
    )
    return [r["canonical_name"] for r in (result.data or [])]


# --- Sidebar ---
page = st.sidebar.radio("Page", ["Overview", "Model Detail", "New Models"])

# --- Overview ---
if page == "Overview":
    st.title("Arena.ai Leaderboard")

    snap = get_latest_snapshot()
    if not snap:
        st.warning("No snapshot data yet.")
        st.stop()

    col1, col2, col3 = st.columns(3)
    col1.metric("Models", snap["total_models"])
    col2.metric("Total Votes", f"{snap['total_votes']:,}" if snap["total_votes"] else "N/A")
    col3.metric("Last Scraped", snap["scraped_at"][:16].replace("T", " "))

    rankings = get_latest_rankings(snap["id"])
    if rankings:
        st.subheader("Current Rankings")
        display = [
            {
                "Rank": r["rank"],
                "Model": r["model_name"],
                "Org": r["organization"] or "",
                "Score": r["score"],
                "CI": f"±{r['score_ci']}" if r.get("score_ci") else "",
                "Votes": f"{r['votes']:,}",
            }
            for r in rankings
        ]
        st.dataframe(display, use_container_width=True, hide_index=True)

        # Top 10 score trend
        st.subheader("Top 10 — Score Over Time")
        top_names = [r["model_name"] for r in rankings[:10]]
        fig = go.Figure()
        for name in top_names:
            history = get_model_history(name)
            if history:
                fig.add_trace(go.Scatter(
                    x=[h["scraped_at"] for h in history],
                    y=[h["score"] for h in history],
                    name=name,
                    mode="lines",
                ))
        fig.update_layout(xaxis_title="Date", yaxis_title="Score", height=400)
        st.plotly_chart(fig, use_container_width=True)

# --- Model Detail ---
elif page == "Model Detail":
    st.title("Model Detail")

    models = get_all_model_names()
    if not models:
        st.warning("No models found.")
        st.stop()

    selected = st.selectbox("Select a model", models)
    history = get_model_history(selected)

    if not history:
        st.info(f"No data for {selected}")
        st.stop()

    latest = history[-1]
    first = history[0]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Current Rank", f"#{latest['rank']}")
    col2.metric("Score", f"{latest['score']}", delta=f"{latest['score'] - first['score']:.1f}" if len(history) > 1 else None)
    col3.metric("Votes", f"{latest['votes']:,}")
    col4.metric("CI", f"±{latest['score_ci']}" if latest.get("score_ci") else "N/A")

    # Rank over time
    st.subheader("Rank Over Time")
    fig_rank = go.Figure()
    fig_rank.add_trace(go.Scatter(
        x=[h["scraped_at"] for h in history],
        y=[h["rank"] for h in history],
        mode="lines+markers",
        name="Rank",
    ))
    fig_rank.update_layout(yaxis=dict(autorange="reversed", title="Rank"), xaxis_title="Date", height=300)
    st.plotly_chart(fig_rank, use_container_width=True)

    # Score with CI band
    st.subheader("Score Trajectory")
    dates = [h["scraped_at"] for h in history]
    scores = [h["score"] for h in history]
    cis = [h.get("score_ci") or 0 for h in history]
    upper = [s + c for s, c in zip(scores, cis)]
    lower = [s - c for s, c in zip(scores, cis)]

    fig_score = go.Figure()
    fig_score.add_trace(go.Scatter(x=dates, y=upper, mode="lines", line=dict(width=0), showlegend=False))
    fig_score.add_trace(go.Scatter(x=dates, y=lower, mode="lines", line=dict(width=0), fill="tonexty",
                                    fillcolor="rgba(68, 68, 255, 0.15)", showlegend=False))
    fig_score.add_trace(go.Scatter(x=dates, y=scores, mode="lines+markers", name="Score"))
    fig_score.update_layout(yaxis_title="Score", xaxis_title="Date", height=300)
    st.plotly_chart(fig_score, use_container_width=True)

    # Votes over time
    st.subheader("Votes Over Time")
    fig_votes = go.Figure()
    fig_votes.add_trace(go.Scatter(
        x=dates,
        y=[h["votes"] for h in history],
        mode="lines+markers",
        name="Votes",
    ))
    fig_votes.update_layout(yaxis_title="Votes", xaxis_title="Date", height=300)
    st.plotly_chart(fig_votes, use_container_width=True)

    # CI tightening
    if any(h.get("score_ci") for h in history):
        st.subheader("CI Over Time")
        fig_ci = go.Figure()
        fig_ci.add_trace(go.Scatter(x=dates, y=cis, mode="lines+markers", name="CI (±)"))
        fig_ci.update_layout(yaxis_title="CI (±)", xaxis_title="Date", height=300)
        st.plotly_chart(fig_ci, use_container_width=True)

# --- New Models ---
elif page == "New Models":
    st.title("New Models")

    days = st.slider("Days to look back", 1, 90, 30)
    new = get_new_models(days)

    if not new:
        st.info(f"No new models in the last {days} days.")
    else:
        st.write(f"**{len(new)} new model(s)** in the last {days} days")
        display = [
            {
                "Model": m["canonical_name"],
                "Organization": m.get("organization") or "",
                "First Seen": m["first_seen_at"][:16].replace("T", " "),
                "Active": "Yes" if m["is_active"] else "No",
            }
            for m in new
        ]
        st.dataframe(display, use_container_width=True, hide_index=True)
