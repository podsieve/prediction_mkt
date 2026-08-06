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
from plotly.subplots import make_subplots
from supabase import create_client

from analysis.aging import day_grid, median_curve, rank_decay
from src.config import settings

st.set_page_config(page_title="Arena Leaderboard", layout="wide")


@st.cache_resource
def get_client():
    return create_client(settings.supabase_url, settings.supabase_key)


@st.cache_data(ttl=300)
def get_categories():
    """Fetch distinct categories that have at least one successful snapshot."""
    client = get_client()
    result = (
        client.table("snapshots")
        .select("category")
        .eq("status", "success")
        .execute()
    )
    cats = sorted({r["category"] for r in (result.data or [])})
    return cats if cats else ["overall"]


@st.cache_data(ttl=300)
def get_latest_snapshot(category: str):
    client = get_client()
    result = (
        client.table("snapshots")
        .select("id, scraped_at, total_models, total_votes")
        .eq("status", "success")
        .eq("category", category)
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
def get_model_history(model_name: str, category: str, days: int = 30):
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

    # Get snapshot IDs for this category
    snapshots_result = (
        client.table("snapshots")
        .select("id, scraped_at, total_votes")
        .eq("status", "success")
        .eq("category", category)
        .execute()
    )
    snap_map = {s["id"]: s for s in (snapshots_result.data or [])}

    if not snap_map:
        return []

    # Fetch rankings for this model, then filter to category snapshots
    rankings = (
        client.table("rankings")
        .select("rank, score, score_ci, votes, created_at, snapshot_id")
        .eq("model_id", model_id)
        .order("created_at", desc=False)
        .execute()
    )

    filtered = []
    for r in rankings.data or []:
        if r["snapshot_id"] in snap_map:
            snap = snap_map[r["snapshot_id"]]
            r["scraped_at"] = snap.get("scraped_at", r["created_at"])
            total = snap.get("total_votes") or 0
            r["vote_share_pct"] = (r["votes"] / total * 100) if total > 0 else 0
            filtered.append(r)

    return filtered


@st.cache_data(ttl=300)
def get_new_models(days: int = 30):
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


@st.cache_data(ttl=900)
def get_rank_decay(category: str):
    """Debut-aligned curves. Cached longer than the rest — it scans a lot of rows."""
    return rank_decay(category, client=get_client())


@st.cache_data(ttl=300)
def get_all_model_names(category: str):
    """Get model names that appear in the latest snapshot for a category."""
    client = get_client()

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
        return []

    rankings = (
        client.table("rankings")
        .select("model_id")
        .eq("snapshot_id", snap.data[0]["id"])
        .execute()
    )
    model_ids = [r["model_id"] for r in (rankings.data or [])]
    if not model_ids:
        return []

    names = []
    for i in range(0, len(model_ids), 100):
        batch = model_ids[i:i + 100]
        result = (
            client.table("models")
            .select("canonical_name")
            .in_("id", batch)
            .order("canonical_name")
            .execute()
        )
        names.extend(r["canonical_name"] for r in (result.data or []))

    return sorted(names)


# --- Sidebar ---
CATEGORY_LABELS = {
    "overall": "Overall",
    "coding": "Coding",
    "math": "Math",
}

categories = get_categories()
category_display = [CATEGORY_LABELS.get(c, c.title()) for c in categories]

selected_display = st.sidebar.selectbox("Category", category_display)
category = categories[category_display.index(selected_display)]

page = st.sidebar.radio("Page", ["Overview", "Model Detail", "Rank Decay", "New Models"])

# --- Overview ---
if page == "Overview":
    st.title(f"Arena.ai Leaderboard — {CATEGORY_LABELS.get(category, category.title())}")

    snap = get_latest_snapshot(category)
    if not snap:
        st.warning(f"No snapshot data yet for {category}.")
        st.stop()

    col1, col2, col3 = st.columns(3)
    col1.metric("Models", snap["total_models"])
    col2.metric("Total Votes", f"{snap['total_votes']:,}" if snap["total_votes"] else "N/A")
    col3.metric("Last Scraped", snap["scraped_at"][:16].replace("T", " "))

    rankings = get_latest_rankings(snap["id"])
    if rankings:
        st.subheader("Current Rankings")
        st.caption("Full leaderboard from the most recent scrape. Higher score = better model. Smaller CI = more certain of the ranking.")
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
        st.caption("How the top 10 models' Elo scores have changed over time. Lines going up = model improving. Look for crossovers — that's one model overtaking another.")
        top_names = [r["model_name"] for r in rankings[:10]]
        fig = go.Figure()
        for name in top_names:
            history = get_model_history(name, category)
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
    st.title(f"Model Detail — {CATEGORY_LABELS.get(category, category.title())}")

    models = get_all_model_names(category)
    if not models:
        st.warning(f"No models found for {category}.")
        st.stop()

    selected = st.selectbox("Select a model", models)
    history = get_model_history(selected, category)

    if not history:
        st.info(f"No data for {selected} in {category}")
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
    st.caption("Position on the leaderboard over time. Line going down = climbing the leaderboard. Flat = stable position.")
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
    st.caption("Elo score over time. Shaded band = confidence interval. Line going up = model getting stronger. Band shrinking = score becoming trustworthy.")
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
    st.caption("Total head-to-head battles this model has participated in. Steeper slope = faster engagement. Flat = model is no longer being actively compared.")
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
        st.caption("Confidence interval over time. Line going down = ranking stabilizing. Once flat near zero, the position is locked in.")
        fig_ci = go.Figure()
        fig_ci.add_trace(go.Scatter(x=dates, y=cis, mode="lines+markers", name="CI (±)"))
        fig_ci.update_layout(yaxis_title="CI (±)", xaxis_title="Date", height=300)
        st.plotly_chart(fig_ci, use_container_width=True)

# --- Rank Decay ---
elif page == "Rank Decay":
    st.title(f"Rank Decay — {CATEGORY_LABELS.get(category, category.title())}")
    st.caption(
        "Every model aligned on **day 0 = its first appearance**, not the calendar. "
        "The question this answers: after a model launches, does it hold its position?"
    )

    data = get_rank_decay(category)
    curves = data["curves"]
    if not curves:
        st.warning(f"No models have debuted since {category} scraping began.")
        st.stop()

    full_horizon = max(c["days_tracked"] for c in curves)
    c1, c2, c3 = st.columns([1, 1, 2])
    min_days = c1.slider("Min days tracked", 0, 30, 5,
                         help="Drop models too fresh to show a trend.")
    days_to_show = c2.slider(
        "Days to show", 7, max(int(full_horizon) + 1, 8), min(30, int(full_horizon) + 1),
        help="Zoom the time axis. Most of the movement happens in the first few weeks, "
             "while vote counts are still thin.",
    )
    eligible = [c for c in curves if c["days_tracked"] >= min_days]
    highlight = c3.multiselect(
        "Highlight models",
        sorted(c["model_name"] for c in eligible),
        help="Everything else stays as a light background line.",
    )

    if not eligible:
        st.warning(f"No models with at least {min_days} days of history.")
        st.stop()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Models charted", len(eligible))
    m2.metric("Excluded (pre-dated scraping)", data["censored"])
    net_ranks = [c["points"][-1]["rank_delta"] for c in eligible]
    m3.metric("Median rank change", f"{sorted(net_ranks)[len(net_ranks)//2]:+.0f}")
    net_scores = [c["points"][-1]["score_delta"] for c in eligible]
    m4.metric("Median score change", f"{sorted(net_scores)[len(net_scores)//2]:+.0f}")

    # --- The main two-panel aging curve ---
    horizon = max(c["days_tracked"] for c in eligible)
    grid = day_grid(horizon, step=0.5)
    med_rank = median_curve(eligible, "rank_delta", grid)
    med_score = median_curve(eligible, "score_delta", grid)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        subplot_titles=("Rank change since debut", "Score change since debut"),
    )

    # Red is reserved for the median, so highlights draw from a palette without it.
    HIGHLIGHT_COLORS = ["#1f77b4", "#2ca02c", "#9467bd", "#17becf", "#8c564b", "#7f7f00"]
    hl_color = {name: HIGHLIGHT_COLORS[i % len(HIGHLIGHT_COLORS)]
                for i, name in enumerate(highlight)}

    for curve in eligible:
        is_hl = curve["model_name"] in highlight
        xs = [p["days"] for p in curve["points"]]
        style = (dict(width=2.5, color=hl_color[curve["model_name"]]) if is_hl
                 else dict(width=1, color="rgba(140,140,150,0.28)"))
        for row, field in ((1, "rank_delta"), (2, "score_delta")):
            fig.add_trace(
                go.Scatter(
                    x=xs, y=[p[field] for p in curve["points"]],
                    mode="lines", line_shape="hv", line=style,
                    name=curve["model_name"],
                    legendgroup=curve["model_name"],
                    showlegend=is_hl and row == 1,
                    hoverinfo="skip" if not is_hl else "x+y+name",
                ),
                row=row, col=1,
            )

    # Median with an interquartile band, drawn on top.
    for row, med in ((1, med_rank), (2, med_score)):
        xs = [g for g, v in zip(grid, med["median"]) if v is not None]
        if not xs:
            continue
        keep = [i for i, v in enumerate(med["median"]) if v is not None]
        fig.add_trace(go.Scatter(
            x=xs + xs[::-1],
            y=[med["q3"][i] for i in keep] + [med["q1"][i] for i in keep][::-1],
            fill="toself", fillcolor="rgba(230,90,60,0.13)",
            line=dict(width=0), hoverinfo="skip", showlegend=False,
        ), row=row, col=1)
        fig.add_trace(go.Scatter(
            x=xs, y=[med["median"][i] for i in keep],
            mode="lines", line=dict(color="rgb(230,90,60)", width=3),
            name="Median (IQR band)", legendgroup="median",
            showlegend=(row == 1), hovertemplate="day %{x:.1f}: %{y:+.1f}<extra>median</extra>",
        ), row=row, col=1)
        fig.add_hline(y=0, line=dict(color="rgba(120,120,120,0.55)", width=1, dash="dot"),
                      row=row, col=1)

    # Positive rank delta means the model fell, so flip the axis: down = worse.
    fig.update_yaxes(title_text="Ranks lost (down = fell)", autorange="reversed", row=1, col=1)
    fig.update_yaxes(title_text="Score change", row=2, col=1)
    fig.update_xaxes(title_text="Days since debut", row=2, col=1)
    # Linear, not log: day 0 is the debut and log10(0) would silently drop it.
    fig.update_xaxes(range=[0, days_to_show])
    fig.update_layout(height=680, hovermode="closest",
                      legend=dict(orientation="h", yanchor="bottom", y=1.06))
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "Grey line = one model. Red = median across all charted models, shaded to the "
        "interquartile range. The median is only drawn where at least 3 models still "
        "have data, so it stops short of the longest single line."
    )

    # --- Cohort view: is decay getting faster as the board fills up? ---
    st.subheader("By debut cohort")
    st.caption(
        "Median rank change per debut month. If later cohorts fall faster, the board is "
        "getting harder to hold position on."
    )
    cohorts: dict = {}
    for curve in eligible:
        cohorts.setdefault(curve["debut_at"].strftime("%Y-%m"), []).append(curve)

    fig_c = go.Figure()
    for label in sorted(cohorts):
        group = cohorts[label]
        if len(group) < 3:
            continue
        c_horizon = max(c["days_tracked"] for c in group)
        c_grid = day_grid(c_horizon, step=1.0)
        med = median_curve(group, "rank_delta", c_grid, min_models=3)
        xs = [g for g, v in zip(c_grid, med["median"]) if v is not None]
        ys = [v for v in med["median"] if v is not None]
        if xs:
            fig_c.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line_shape="hv",
                                       line=dict(width=2.5), name=f"{label} (n={len(group)})"))
    if fig_c.data:
        fig_c.add_hline(y=0, line=dict(color="rgba(120,120,120,0.55)", width=1, dash="dot"))
        fig_c.update_yaxes(title_text="Median ranks lost (down = fell)", autorange="reversed")
        fig_c.update_xaxes(title_text="Days since debut")
        fig_c.update_layout(height=380, legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig_c, use_container_width=True)
    else:
        st.info("Not enough models per debut month yet — needs 3+ in a cohort.")

    # --- Movers ---
    st.subheader("Biggest movers since debut")
    st.caption("Sorted by ranks lost. A model can lose rank while its score rises — that's the field improving around it.")
    rows_out = []
    for c in sorted(eligible, key=lambda c: -c["points"][-1]["rank_delta"]):
        last = c["points"][-1]
        rows_out.append({
            "Model": c["model_name"],
            "Org": c["organization"],
            "Debut": c["debut_at"].strftime("%Y-%m-%d"),
            "Days": f"{c['days_tracked']:.0f}",
            "Rank": f"{c['debut_rank']} → {last['rank']}",
            "Ranks lost": f"{last['rank_delta']:+d}",
            "Score": f"{c['debut_score']:.0f} → {last['score']:.0f}",
            "Score change": f"{last['score_delta']:+.0f}",
            "Votes": f"{last['votes']:,}",
        })
    st.dataframe(rows_out, use_container_width=True, hide_index=True)

    st.info(
        f"**{data['censored']} models are excluded.** They were already on the board in the "
        f"first {category} snapshot ({str(data['category_start'])[:10]}), so their "
        f"`first_seen_at` records when this scraper started — not when they launched. "
        f"Putting them on a since-debut axis would measure the collection window."
    )

# --- New Models ---
elif page == "New Models":
    st.title("New Models")

    st.caption("Models that recently appeared on the leaderboard. Select one and go to Model Detail to watch its launch trajectory.")
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
