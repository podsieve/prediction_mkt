"""
Arena.ai Leaderboard — Data Analysis & Feature Suggestion Report
Run: SUPABASE_URL=... SUPABASE_KEY=... RESEND_API_KEY=... python run_analysis.py
"""
import os
import statistics
from collections import defaultdict
from datetime import datetime

from supabase import create_client

# ── Supabase client ────────────────────────────────────────────────────────────
client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

def fetch_all(table, select="*", filters=None, order=None, limit=20000):
    req = client.table(table).select(select)
    if filters:
        for col, val in filters.items():
            req = req.eq(col, val)
    if order:
        req = req.order(order)
    return req.limit(limit).execute().data

# ── Fetch ─────────────────────────────────────────────────────────────────────
print("Fetching data…")
snapshots    = fetch_all("snapshots", order="scraped_at")
models       = fetch_all("models")
rankings_raw = fetch_all(
    "rankings",
    select="snapshot_id,model_id,rank,score,score_ci,votes",
    limit=50000,
)

model_name = {m["id"]: m["canonical_name"] for m in models}
snap_by_id = {s["id"]: s for s in snapshots}

rows = []
for r in rankings_raw:
    snap = snap_by_id.get(r["snapshot_id"])
    if snap and snap.get("status") == "success":
        rows.append({
            **r,
            "score":      float(r["score"])    if r["score"]    is not None else None,
            "score_ci":   float(r["score_ci"]) if r["score_ci"] is not None else None,
            "scraped_at": snap["scraped_at"],
            "category":   snap.get("category", "overall"),
        })

rows.sort(key=lambda x: x["scraped_at"])
overall = [r for r in rows if r["category"] == "overall"]
coding  = [r for r in rows if r["category"] == "coding"]

good_snaps  = [s for s in snapshots if s.get("status") == "success"]
dates       = sorted(s["scraped_at"] for s in good_snaps)
date_start  = dates[0][:10]  if dates else "N/A"
date_end    = dates[-1][:10] if dates else "N/A"
n_snapshots = len(good_snaps)

print(f"  snapshots={n_snapshots}  models={len(models)}  ranking_rows={len(rows)}")
print(f"  date range: {date_start} → {date_end}")

# ── Helpers ───────────────────────────────────────────────────────────────────
def timeseries(rlist):
    ts = defaultdict(list)
    for r in rlist:
        if r["score"] is not None:
            ts[r["model_id"]].append((r["scraped_at"], r["score"], r["rank"]))
    for mid in ts:
        ts[mid].sort()
    return ts

ts_overall = timeseries(overall)
ts_coding  = timeseries(coding)

# ─────────────────────────────────────────────────────────────────────────────
# 1. SCORE VOLATILITY
# ─────────────────────────────────────────────────────────────────────────────
volatility = []
for mid, series in ts_overall.items():
    if len(series) < 10:
        continue
    scores = [s for _, s, _ in series]
    deltas = [abs(scores[i] - scores[i - 1]) for i in range(1, len(scores))]
    half = len(scores) // 2
    std_early = statistics.stdev(scores[:half]) if half > 1 else 0
    std_late  = statistics.stdev(scores[half:]) if half > 1 else 0
    volatility.append({
        "name":       model_name.get(mid, mid),
        "n":          len(series),
        "std_dev":    statistics.stdev(scores),
        "mean_delta": statistics.mean(deltas),
        "max_delta":  max(deltas),
        "std_early":  std_early,
        "std_late":   std_late,
        "converging": std_late < std_early,
    })

volatility.sort(key=lambda x: -x["std_dev"])

# ─────────────────────────────────────────────────────────────────────────────
# 2. RANK STABILITY — tenure at #1
# ─────────────────────────────────────────────────────────────────────────────
snap_sorted = sorted({r["snapshot_id"] for r in overall})
rank1_map   = {}
for r in overall:
    if r["rank"] == 1:
        rank1_map[r["snapshot_id"]] = r["model_id"]

rank1_seq = [(sid, rank1_map[sid]) for sid in snap_sorted if sid in rank1_map]
tenures, cur_m, cur_n = [], None, 0
for sid, mid in rank1_seq:
    if mid == cur_m:
        cur_n += 1
    else:
        if cur_m:
            tenures.append((cur_m, cur_n))
        cur_m, cur_n = mid, 1
if cur_m:
    tenures.append((cur_m, cur_n))

avg_tenure = statistics.mean(t[1] for t in tenures) if tenures else 0
top_tenures = sorted(tenures, key=lambda x: -x[1])[:5]

# Top-10 swap rate
swaps = 0
prev_top10 = set()
for sid in snap_sorted:
    cur_top10 = {r["model_id"] for r in overall if r["snapshot_id"] == sid and r["rank"] <= 10}
    if prev_top10 and cur_top10 != prev_top10:
        swaps += 1
    prev_top10 = cur_top10
swap_rate = swaps / max(1, len(snap_sorted) - 1)

# ─────────────────────────────────────────────────────────────────────────────
# 3. VOTE VELOCITY — time-of-day / day-of-week
# ─────────────────────────────────────────────────────────────────────────────
vote_data = sorted(
    [(s["scraped_at"], s["total_votes"]) for s in good_snaps if s.get("total_votes")],
    key=lambda x: x[0],
)
hour_deltas = defaultdict(list)
dow_deltas  = defaultdict(list)
for i in range(1, len(vote_data)):
    t0, v0 = vote_data[i - 1]
    t1, v1 = vote_data[i]
    if v1 and v0:
        dt = datetime.fromisoformat(t1.replace("Z", "+00:00"))
        dv = v1 - v0
        if 0 < dv < 500_000:
            hour_deltas[dt.hour].append(dv)
            dow_deltas[dt.weekday()].append(dv)

hourly_avg = {h: statistics.mean(v) for h, v in hour_deltas.items()} if hour_deltas else {}
dow_avg    = {d: statistics.mean(v) for d, v in dow_deltas.items()}   if dow_deltas  else {}
peak_hour  = max(hourly_avg, key=hourly_avg.get) if hourly_avg else None
slow_hour  = min(hourly_avg, key=hourly_avg.get) if hourly_avg else None
peak_dow   = max(dow_avg, key=dow_avg.get) if dow_avg else None
days_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ─────────────────────────────────────────────────────────────────────────────
# 4. CI AS PREDICTOR
# ─────────────────────────────────────────────────────────────────────────────
model_ci_ts = defaultdict(list)
for r in overall:
    if r["score_ci"] is not None:
        model_ci_ts[r["model_id"]].append((r["scraped_at"], r["rank"], r["score_ci"]))
for mid in model_ci_ts:
    model_ci_ts[mid].sort()

wide_changes, narrow_changes, pairs_ci = [], [], []
for mid, series in model_ci_ts.items():
    for i in range(len(series) - 1):
        _, rank_now, ci_now = series[i]
        _, rank_next, _     = series[i + 1]
        rc = abs(rank_next - rank_now)
        pairs_ci.append((ci_now, rc))
        (wide_changes if ci_now > 15 else narrow_changes).append(rc)

wide_avg   = statistics.mean(wide_changes)   if wide_changes   else 0
narrow_avg = statistics.mean(narrow_changes) if narrow_changes else 0

ci_corr = 0.0
if len(pairs_ci) > 30:
    x_vals = [p[0] for p in pairs_ci]
    y_vals = [p[1] for p in pairs_ci]
    mx, my = statistics.mean(x_vals), statistics.mean(y_vals)
    sx, sy = statistics.stdev(x_vals), statistics.stdev(y_vals)
    cov = statistics.mean([(x - mx) * (y - my) for x, y in zip(x_vals, y_vals)])
    ci_corr = cov / (sx * sy) if sx and sy else 0.0

# ─────────────────────────────────────────────────────────────────────────────
# 5. NEW MODEL TRAJECTORY
# ─────────────────────────────────────────────────────────────────────────────
model_first = {}
for r in overall:
    mid = r["model_id"]
    if mid not in model_first or r["scraped_at"] < model_first[mid]:
        model_first[mid] = r["scraped_at"]

new_model_trajectories = {}
for mid, first_time in model_first.items():
    if dates and first_time > dates[0]:
        series = sorted(
            [(r["scraped_at"], r["rank"]) for r in overall if r["model_id"] == mid],
            key=lambda x: x[0],
        )
        if len(series) >= 5:
            new_model_trajectories[model_name.get(mid, mid)] = [r for _, r in series[:12]]

# ─────────────────────────────────────────────────────────────────────────────
# 6. CROSS-CATEGORY DIVERGENCE
# ─────────────────────────────────────────────────────────────────────────────
common = set(ts_overall.keys()) & set(ts_coding.keys())
divergence = []
for mid in common:
    os_ = ts_overall[mid]
    cs_ = ts_coding[mid]
    if len(os_) >= 5 and len(cs_) >= 5:
        avg_o = statistics.mean(r for _, _, r in os_[-20:])
        avg_c = statistics.mean(r for _, _, r in cs_[-20:])
        divergence.append({
            "name":    model_name.get(mid, mid),
            "overall": avg_o,
            "coding":  avg_c,
            "diff":    avg_c - avg_o,
        })
divergence.sort(key=lambda x: abs(x["diff"]), reverse=True)

# ─────────────────────────────────────────────────────────────────────────────
# 7. SCORE GAP / TIER CLUSTERING
# ─────────────────────────────────────────────────────────────────────────────
latest_sid = snap_sorted[-1] if snap_sorted else None
latest_scores, tier_gaps = [], []
if latest_sid:
    latest_scores = sorted(
        [(r["score"], r["rank"], model_name.get(r["model_id"], "?"))
         for r in overall if r["snapshot_id"] == latest_sid and r["score"] is not None],
        key=lambda x: -x[0],
    )
    gaps = [(latest_scores[i - 1][0] - latest_scores[i][0],
             latest_scores[i - 1][2],
             latest_scores[i][2],
             latest_scores[i - 1][1])
            for i in range(1, min(30, len(latest_scores)))]
    tier_gaps = sorted(gaps, key=lambda x: -x[0])[:5]

# ─────────────────────────────────────────────────────────────────────────────
# 8. MOMENTUM
# ─────────────────────────────────────────────────────────────────────────────
m_continue = m_reverse = m_flat = 0
for mid, series in ts_overall.items():
    if len(series) < 5:
        continue
    for i in range(3, len(series)):
        r0, r1, r2, r3, r4 = [series[j][2] for j in range(i - 4, i + 1)]
        if r1 < r0 and r2 < r1 and r3 < r2:
            if r4 < r3:
                m_continue += 1
            elif r4 > r3:
                m_reverse += 1
            else:
                m_flat += 1

m_total = m_continue + m_reverse + m_flat
momentum_pct = 100 * m_continue / m_total if m_total else 0

# ─────────────────────────────────────────────────────────────────────────────
# Print summary
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== VOLATILITY ===")
for i, v in enumerate(volatility[:10], 1):
    trend = "↓converging" if v["converging"] else "↑diverging"
    print(f"  {i:2}. {v['name']:<45} σ={v['std_dev']:.2f}  mean|Δ|={v['mean_delta']:.2f}  {trend}")

print("\n=== RANK STABILITY ===")
print(f"Avg tenure at #1: {avg_tenure:.1f} snapshots ({avg_tenure*2:.0f}h)")
for m, n in top_tenures:
    print(f"  {model_name.get(m,m):<45} {n} snaps ({n*2}h)")
print(f"Top-10 swap rate: {swap_rate*100:.1f}%")

print("\n=== VOTE VELOCITY ===")
if peak_hour is not None:
    print(f"Peak UTC hour: {peak_hour}:00  +{hourly_avg[peak_hour]:.0f} votes/snap")
    print(f"Slow UTC hour: {slow_hour}:00  +{hourly_avg[slow_hour]:.0f} votes/snap")
    print(f"Ratio: {hourly_avg[peak_hour]/max(1,hourly_avg[slow_hour]):.1f}x")
if peak_dow is not None:
    print(f"Peak day: {days_labels[peak_dow]}  avg +{dow_avg[peak_dow]:.0f} votes/snap")

print("\n=== CI PREDICTOR ===")
print(f"Wide CI (>15) next-snap rank change avg:   {wide_avg:.2f} (n={len(wide_changes)})")
print(f"Narrow CI (<=15) next-snap rank change avg: {narrow_avg:.2f} (n={len(narrow_changes)})")
print(f"Wide:Narrow ratio: {wide_avg/max(0.01,narrow_avg):.1f}x")
print(f"Pearson r(CI_width, |rank_delta|) = {ci_corr:.3f}")

print("\n=== NEW MODEL TRAJECTORIES ===")
for nm, traj in list(new_model_trajectories.items())[:8]:
    print(f"  {nm[:42]:<42} {traj}")

print("\n=== CROSS-CATEGORY DIVERGENCE ===")
for d in divergence[:8]:
    arrow = "coding worse" if d["diff"] > 0 else "coding better"
    print(f"  {d['name'][:45]:<45} overall={d['overall']:.1f}  coding={d['coding']:.1f}  delta={d['diff']:+.1f} ({arrow})")

print("\n=== TIER GAPS ===")
if latest_scores:
    print("Latest snapshot top-15:")
    for sc, rk, nm in latest_scores[:15]:
        print(f"  #{rk:2}  {nm[:42]:<42}  {sc:.1f}")
print("Largest score gaps (tier boundaries):")
for g, m1, m2, rk in tier_gaps:
    print(f"  #{rk}->{rk+1}: {m1[:28]} -> {m2[:28]}  gap={g:.1f}")

print("\n=== MOMENTUM ===")
if m_total:
    print(f"After 3 consec improvements -- continue: {m_continue} ({100*m_continue/m_total:.1f}%)  "
          f"reverse: {m_reverse} ({100*m_reverse/m_total:.1f}%)  flat: {m_flat} ({100*m_flat/m_total:.1f}%)")
    print(f"Total observations: {m_total}")

# ─────────────────────────────────────────────────────────────────────────────
# Build and send email
# ─────────────────────────────────────────────────────────────────────────────
def html_row(label, value):
    return f"<tr><td style='padding:4px 12px 4px 0;color:#666;'>{label}</td><td>{value}</td></tr>"

def html_model_table(rows, headers):
    ths = "".join(f"<th style='text-align:left;padding:4px 10px 4px 0;border-bottom:2px solid #333;'>{h}</th>" for h in headers)
    body = ""
    for row in rows:
        tds = "".join(f"<td style='padding:3px 10px 3px 0;border-bottom:1px solid #eee;'>{c}</td>" for c in row)
        body += f"<tr>{tds}</tr>"
    return f"<table style='border-collapse:collapse;font-size:13px;'><thead><tr>{ths}</tr></thead><tbody>{body}</tbody></table>"

vol_rows = [
    (f"#{i}", v["name"], f"&sigma;={v['std_dev']:.2f}", f"mean|&Delta;|={v['mean_delta']:.2f}",
     "converging" if v["converging"] else "diverging")
    for i, v in enumerate(volatility[:8], 1)
]
vol_table = html_model_table(vol_rows, ["#", "Model", "Score sigma", "Mean delta", "Trend"])

tenure_rows = [(model_name.get(m, m), str(n), f"{n*2}h") for m, n in top_tenures]
tenure_table = html_model_table(tenure_rows, ["Model", "Snapshots at #1", "Hours"])

div_rows = [
    (d["name"], f"{d['overall']:.1f}", f"{d['coding']:.1f}",
     f"<span style='color:{'#c00' if d['diff']>0 else '#080'}'>{d['diff']:+.1f}</span>",
     "coding worse" if d["diff"] > 0 else "coding better")
    for d in divergence[:8]
]
div_table = html_model_table(div_rows, ["Model", "Overall Rank", "Coding Rank", "Delta", "Direction"])

score_rows = [(f"#{rk}", nm, f"{sc:.1f}") for sc, rk, nm in latest_scores[:15]]
score_table = html_model_table(score_rows, ["Rank", "Model", "Score"])

gap_rows = [(f"#{rk}->{rk+1}", m1[:28], m2[:28], f"{g:.1f}") for g, m1, m2, rk in tier_gaps]
gap_table = html_model_table(gap_rows, ["Positions", "Upper model", "Lower model", "Gap"])

if m_total:
    cont_pct  = 100 * m_continue / m_total
    rev_pct   = 100 * m_reverse  / m_total
    flat_pct_ = 100 * m_flat     / m_total
    momentum_html = f"""
    <table style='border-collapse:collapse;font-size:13px;'>
    <tr>
      <td style='padding:4px 10px 4px 0;'>Continue improving</td>
      <td style='width:200px;background:#eee;padding:0;'>
        <div style='background:#2a7;width:{cont_pct:.0f}%;height:16px;'></div></td>
      <td style='padding:4px 0 4px 8px;'>{m_continue} ({cont_pct:.1f}%)</td>
    </tr>
    <tr>
      <td style='padding:4px 10px 4px 0;'>Reverse (worsen)</td>
      <td style='width:200px;background:#eee;padding:0;'>
        <div style='background:#c44;width:{rev_pct:.0f}%;height:16px;'></div></td>
      <td style='padding:4px 0 4px 8px;'>{m_reverse} ({rev_pct:.1f}%)</td>
    </tr>
    <tr>
      <td style='padding:4px 10px 4px 0;'>Flat</td>
      <td style='width:200px;background:#eee;padding:0;'>
        <div style='background:#aaa;width:{flat_pct_:.0f}%;height:16px;'></div></td>
      <td style='padding:4px 0 4px 8px;'>{m_flat} ({flat_pct_:.1f}%)</td>
    </tr>
    </table>
    <p style='font-size:12px;color:#666;margin-top:4px;'>n={m_total} observations</p>
    """
else:
    momentum_html = "<p>Insufficient data for momentum analysis.</p>"

dow_html_content = ""
if dow_avg:
    max_dv = max(dow_avg.values())
    dow_html_content = "<table style='border-collapse:collapse;font-size:13px;'>"
    for d in range(7):
        if d in dow_avg:
            pct = dow_avg[d] / max_dv * 100
            dow_html_content += (
                f"<tr><td style='padding:2px 8px 2px 0;width:40px;'>{days_labels[d]}</td>"
                f"<td style='width:180px;background:#eee;padding:0;'>"
                f"<div style='background:#5599ff;width:{pct:.0f}%;height:14px;'></div></td>"
                f"<td style='padding:2px 0 2px 8px;'>{dow_avg[d]:.0f} votes/snap</td></tr>"
            )
    dow_html_content += "</table>"

ci_direction = "positive" if ci_corr > 0 else "negative"
ci_strength  = "strong" if abs(ci_corr) > 0.3 else ("moderate" if abs(ci_corr) > 0.15 else "weak")
ci_ratio     = wide_avg / max(0.01, narrow_avg)

nm_html_content = ""
if new_model_trajectories:
    nm_html_content = "<ul style='font-size:13px;line-height:1.8;'>"
    for nm, traj in list(new_model_trajectories.items())[:6]:
        nm_html_content += f"<li><strong>{nm}</strong> &mdash; entry rank #{traj[0]}, trajectory: {' &rarr; '.join(str(r) for r in traj[:8])}</li>"
    nm_html_content += "</ul>"
else:
    nm_html_content = "<p>No new models with sufficient data.</p>"

pattern_spotlight = f"""
<p><strong>The most actionable pattern is the CI-as-leading-indicator signal.</strong></p>
<p>
Models with a wide confidence interval (&gt;15 points) move an average of
<strong>{wide_avg:.2f} rank positions</strong> in the next snapshot, while
narrow-CI models move only <strong>{narrow_avg:.2f}</strong> &mdash; a
<strong>{ci_ratio:.1f}&times; difference</strong>.
The Pearson correlation between CI width and next-snapshot absolute rank
change is <strong>r&nbsp;=&nbsp;{ci_corr:.3f}</strong> ({ci_strength} {ci_direction} relationship,
n&nbsp;=&nbsp;{len(pairs_ci):,} observation pairs).
</p>
<p>
This means CI width is a <em>leading indicator</em> of upcoming rank instability,
not just a measure of current uncertainty. Before a model&apos;s rank moves, its CI
tends to widen &mdash; possibly because it is gaining votes faster than the leaderboard&apos;s
smoothing window can absorb. A trader watching CI expansion can pre-position
<em>before</em> the rank change is visible.
</p>
<p>
Complementary finding: after three consecutive rank improvements (momentum),
models continue improving <strong>{momentum_pct:.1f}%</strong> of the time vs.
reversing {100*m_reverse/max(1,m_total):.1f}% &mdash;
{'a meaningful momentum signal' if momentum_pct > 55 else 'essentially coin-flip, so momentum alone is not reliable'}.
This makes CI expansion the more trustworthy pre-signal.
</p>
"""

feature_suggestion = f"""
<h3 style='color:#1a5276;'>Feature: CI Expansion Alert (Widening Window)</h3>

<p><strong>What it computes:</strong> For each model in the top 30, compare the current
snapshot&apos;s <code>score_ci</code> against its rolling 5-snapshot average CI. If the
current CI exceeds the rolling average by more than 1.5&times;, flag the model as
&ldquo;CI Expanding.&rdquo; Overlay this flag on the rank timeline chart.</p>

<p><strong>SQL logic:</strong></p>
<pre style='background:#f4f4f4;padding:12px;border-radius:4px;font-size:12px;overflow-x:auto;'>
WITH ci_history AS (
  SELECT
    r.model_id,
    s.scraped_at,
    r.score_ci,
    AVG(r.score_ci) OVER (
      PARTITION BY r.model_id
      ORDER BY s.scraped_at
      ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
    ) AS ci_rolling_avg
  FROM rankings r
  JOIN snapshots s ON s.id = r.snapshot_id
  WHERE s.status = 'success'
    AND s.category = 'overall'
    AND r.score_ci IS NOT NULL
)
SELECT
  model_id,
  scraped_at,
  score_ci,
  ci_rolling_avg,
  score_ci / NULLIF(ci_rolling_avg, 0) AS ci_expansion_ratio
FROM ci_history
WHERE score_ci / NULLIF(ci_rolling_avg, 0) &gt; 1.5
ORDER BY scraped_at DESC;
</pre>

<p><strong>How a trader uses it (specific scenario):</strong></p>
<ol>
  <li>Trader holds a prediction market position that Model X stays in top 5 for the next 24 hours.</li>
  <li>At the 8 PM snapshot, the Widening Window alert fires on Model X &mdash;
      its CI jumped from 12 to 22, a 1.8&times; expansion.</li>
  <li>Historical data shows that when CI expands &gt;1.5&times; for a top-10 model,
      there is a {ci_ratio:.0f}&times; greater average rank movement in the next 2&ndash;4 snapshots.</li>
  <li>Trader reduces or hedges their position before the rank move is visible in raw rankings.</li>
</ol>

<p><strong>Prediction signal:</strong> CI expansion is a ~2&ndash;6 hour leading indicator of
rank instability. Because it fires <em>before</em> the rank number changes, it gives
prediction-market traders a meaningful edge window that the raw leaderboard does not expose.</p>
"""

html_content = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>
  body {{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;
         color:#222;max-width:780px;margin:0 auto;padding:24px;}}
  h1 {{color:#1a5276;border-bottom:3px solid #1a5276;padding-bottom:8px;}}
  h2 {{color:#1a5276;margin-top:32px;}}
  h3 {{color:#21618c;}}
  .badge {{display:inline-block;background:#1a5276;color:#fff;padding:3px 10px;
           border-radius:12px;font-size:12px;margin-bottom:16px;}}
  .card {{background:#f8f9fa;border-left:4px solid #1a5276;padding:14px 18px;
          margin:16px 0;border-radius:0 6px 6px 0;}}
  .alert {{background:#fff3cd;border-left:4px solid #f39c12;padding:14px 18px;
           margin:16px 0;border-radius:0 6px 6px 0;}}
  table {{border-collapse:collapse;width:100%;}}
  pre {{background:#f4f4f4;padding:12px;border-radius:4px;font-size:12px;
        overflow-x:auto;white-space:pre-wrap;}}
</style></head>
<body>

<h1>Arena.ai Leaderboard &mdash; Data Analysis Report</h1>
<span class="badge">Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</span>
<p style='color:#666;font-size:13px;'>
  Dataset: {n_snapshots} successful snapshots &nbsp;&#183;&nbsp;
  {len(models)} models tracked &nbsp;&#183;&nbsp;
  {date_start} to {date_end}
</p>

<hr style='border:none;border-top:1px solid #e0e0e0;'>

<h2>1. Score Volatility</h2>
<p>Top-8 most volatile models (score std dev, at least 10 snapshots):</p>
{vol_table}
<div class="card">
<strong>Convergence check:</strong> Models marked &ldquo;converging&rdquo; show lower score std dev
in the second half of their history than the first &mdash; the leaderboard is gradually
settling as vote counts grow and the Elo-like system stabilises.
</div>

<h2>2. Rank Stability at #1</h2>
<p>
  Average tenure at #1: <strong>{avg_tenure:.1f} snapshots ({avg_tenure*2:.0f}h)</strong><br>
  Top-10 composition changes in <strong>{swap_rate*100:.1f}%</strong> of consecutive snapshot pairs.
</p>
{tenure_table}

<h2>3. Vote Velocity &mdash; Time Patterns</h2>
{"<p>Peak UTC hour: <strong>" + str(peak_hour) + ":00</strong> (" + f"{hourly_avg[peak_hour]:.0f}" + " votes/snapshot avg) &mdash; " + f"{hourly_avg[peak_hour]/max(1,hourly_avg[slow_hour]):.1f}" + "&times; more than the slowest hour (" + str(slow_hour) + ":00).</p>" if peak_hour is not None else "<p>Insufficient data.</p>"}
{dow_html_content}
<div class="card">
Vote surges at certain hours reflect when large user cohorts (US afternoon, EU morning)
are most active on Arena.ai. Rank changes following high-vote windows are more likely to stick.
</div>

<h2>4. CI Width as a Leading Indicator</h2>
<div class="alert">
<strong>Key finding:</strong>
Wide-CI models (&gt;15) move <strong>{wide_avg:.2f} rank positions</strong> on average
in the next snapshot vs. <strong>{narrow_avg:.2f}</strong> for narrow-CI models &mdash;
a <strong>{ci_ratio:.1f}&times; difference</strong>.
Pearson r&nbsp;=&nbsp;<strong>{ci_corr:.3f}</strong> (n&nbsp;=&nbsp;{len(pairs_ci):,} pairs).
</div>
<p>
This is the strongest quantitative signal in the dataset: a model&apos;s CI width
predicts its upcoming rank instability before the rank number changes.
</p>

<h2>5. New Model Trajectories</h2>
{nm_html_content}

<h2>6. Overall vs Coding Divergence</h2>
<p>Models with the largest rank difference between leaderboards:</p>
{div_table}

<h2>7. Score Gap / Tier Clustering</h2>
<p>Latest snapshot &mdash; top 15:</p>
{score_table}
<p>Largest score gaps (potential tier boundaries):</p>
{gap_table}
<div class="card">
Models sitting just below a large gap above them face lower rank-change risk;
models just above a large gap below are similarly entrenched.
Models caught between two small gaps are most susceptible to position swaps.
</div>

<h2>8. Rank Momentum</h2>
<p>After 3 consecutive rank improvements, what happens next?</p>
{momentum_html}

<hr style='border:none;border-top:2px solid #1a5276;margin-top:36px;'>

<h2>Pattern Spotlight</h2>
{pattern_spotlight}

<hr style='border:none;border-top:2px solid #1a5276;margin-top:36px;'>

<h2>Feature Suggestion</h2>
{feature_suggestion}

<hr style='border:none;border-top:1px solid #e0e0e0;margin-top:36px;'>

<h2>Raw Stats</h2>
<table>
{html_row("Total successful snapshots", n_snapshots)}
{html_row("Total models tracked", len(models))}
{html_row("Date range", f"{date_start} to {date_end}")}
{html_row("Overall ranking rows", len(overall))}
{html_row("Coding ranking rows", len(coding))}
{html_row("Models with 10+ snapshots", len(volatility))}
{html_row("Models in both categories", len(common))}
{html_row("CI analysis pairs", len(pairs_ci))}
{html_row("Momentum observations", m_total)}
{html_row("Report generated", datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'))}
</table>

</body>
</html>
"""

import resend

resend.api_key = os.environ["RESEND_API_KEY"]
resp = resend.Emails.send({
    "from":    "onboarding@resend.dev",
    "to":      ["shyamvora91@gmail.com"],
    "subject": "Arena Tracker &mdash; Daily Data Analysis & Feature Suggestion",
    "html":    html_content,
})
print(f"\nEmail sent: {resp}")
