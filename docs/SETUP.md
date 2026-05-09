# Setup & Deployment Guide

## Local Development Setup

### 1. Clone and Install

```bash
git clone https://github.com/shyamvora/prediction_mkt.git
cd prediction_mkt
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Supabase Project

Create a Supabase project:
1. Go to [supabase.com](https://supabase.com)
2. Create new project (note the URL and service role key)
3. Go to SQL Editor
4. Run `sql/001_schema.sql` (creates tables)
5. Run `sql/002_views.sql` (creates views)

### 3. Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your Supabase credentials:
```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-service-role-key
```

**Important:** Keep `.env` out of git (already in .gitignore).

### 4. Test Locally

```bash
# Run tests (uses fixture, no live fetch)
pytest

# Manual scrape (requires .env with valid Supabase credentials)
python -m src.scraper
```

Expected output:
```
INFO: Fetching leaderboard from https://arena.ai/leaderboard/text/overall-no-style-control
INFO: Fetched 652341 bytes, parsing...
INFO: Parsed 357 models (total votes: 6110156) in 234ms
INFO: Created snapshot abc123...
INFO: Loaded 347 models and 12 aliases into cache
INFO: Bulk inserted 2 new models
INFO: Inserted 357 rankings for snapshot abc123
INFO: Done. Stored snapshot with 357 models.
```

## GitHub Actions Deployment

### 1. Set Repository Secrets

In GitHub repo → Settings → Secrets and variables → Actions, add:
- `SUPABASE_URL` — Your Supabase project URL
- `SUPABASE_KEY` — Your Supabase service role key

### 2. Verify Workflow

File: `.github/workflows/scrape.yml`

This workflow automatically:
- Runs every 6 hours (0, 6, 12, 18 UTC)
- Can be triggered manually via "Workflow Dispatch"
- Uses Python 3.12 with pip cache
- Runs `python -m src.scraper`

To test:
1. Push to main branch
2. Go to Actions tab
3. Click "Scrape Arena Leaderboard"
4. Click "Run workflow"

## Database Schema Walkthrough

### models table

Primary table for deduplication:
```sql
SELECT * FROM models LIMIT 5;
```

Columns:
- `id` — UUID primary key
- `canonical_name` — Official model name (UNIQUE)
- `organization` — e.g., "Anthropic", "OpenAI"
- `license_type` — "Open Source", "Proprietary"
- `first_seen_at` — When model first appeared
- `last_seen_at` — Last time model was on leaderboard
- `is_active` — false if model has been removed
- `created_at` — Row insertion time

### snapshots table

One row per scrape run:
```sql
SELECT scraped_at, total_models, status FROM snapshots 
ORDER BY scraped_at DESC LIMIT 10;
```

Columns:
- `scraped_at` — When scrape happened
- `total_models` — Number of models on leaderboard
- `total_votes` — Total votes across all models
- `status` — "success" or "failed"
- `error_message` — If status='failed'
- `raw_html_hash` — SHA256 of HTML (detect layout changes)

### rankings table

One row per model per snapshot:
```sql
SELECT m.canonical_name, r.rank, r.score, r.votes 
FROM rankings r
JOIN models m ON m.id = r.model_id
WHERE r.snapshot_id = 'abc123'
ORDER BY r.rank;
```

Columns:
- `snapshot_id` → snapshots.id
- `model_id` → models.id
- `rank` — Position on leaderboard (1, 2, 3, ...)
- `rank_upper`, `rank_lower` — Confidence interval
- `score` — Numerical score (e.g., 1473.4)
- `score_ci` — Confidence interval ± (e.g., 1.2)
- `votes` — Total votes for this model
- `raw_model_name` — Original text from page
- `raw_organization` — Original text from page

### model_aliases table

For handling model renames:
```sql
SELECT * FROM model_aliases WHERE model_id = 'uuid';
```

Columns:
- `model_id` → models.id
- `alias_name` — Old name (UNIQUE)

## Common Operations

### Query Latest Rankings

```sql
SELECT canonical_name, rank, score, votes
FROM latest_rankings
ORDER BY rank
LIMIT 10;
```

### Score Trajectory for a Model

```sql
SELECT scraped_at, rank, score, votes
FROM model_trajectory
WHERE canonical_name = 'Claude Opus'
ORDER BY scraped_at DESC
LIMIT 20;
```

### Find New Models (Last 7 Days)

```sql
SELECT canonical_name, first_seen_at, organization
FROM new_model_appearances
WHERE first_seen_at > now() - interval '7 days'
ORDER BY first_seen_at DESC;
```

### Manual Model Merge

When Arena.ai renames a model (e.g., "claude-opus-preview" → "Claude Opus"):

```bash
python scripts/merge_models.py "claude-opus-preview" "Claude Opus"
```

This:
1. Moves all rankings from old_id → keep_id
2. Adds "claude-opus-preview" as an alias
3. Deletes the old model row

Verify:
```sql
SELECT * FROM model_aliases WHERE alias_name = 'claude-opus-preview';
-- Shows alias_name → model_id (Claude Opus)
```

## Troubleshooting

### Issue: "No models parsed (<50)"

Check if Arena.ai changed the HTML structure:
1. Download current Arena.ai HTML
2. Compare table structure with `tests/fixtures/sample_leaderboard.html`
3. Update `parse_leaderboard()` if column order changed

Workflow:
```bash
# Look at actual Arena.ai HTML
curl https://arena.ai/leaderboard/text/overall-no-style-control > current.html

# Check table structure
grep -A 5 "<tbody>" current.html | head -20

# Compare with fixture
diff current.html tests/fixtures/sample_leaderboard.html | head -50
```

### Issue: "Could not resolve model_id"

Model name doesn't exist in DB:
1. Check if it's an alias issue: `SELECT * FROM model_aliases WHERE alias_name = 'ModelName';`
2. If not found, the model should have been auto-inserted as new
3. Manual insert if needed:
   ```sql
   INSERT INTO models (canonical_name, organization, is_active)
   VALUES ('NewModel', 'Org', true);
   ```

### Issue: Model stuck as inactive

Model reappeared after being marked inactive:
1. Scraper should auto-mark active again
2. Manual fix:
   ```sql
   UPDATE models SET is_active = true, last_seen_at = now()
   WHERE canonical_name = 'ModelName';
   ```

### Issue: CI values all NULL

Arena.ai sometimes doesn't show CI for new/low-vote models—normal behavior. Script handles null:
```sql
SELECT canonical_name, score, score_ci FROM latest_rankings
WHERE score_ci IS NULL
LIMIT 10;
```

### Issue: Supabase rate limits

Error: "429 Too Many Requests"

Fix:
1. Reduce frequency (currently 6h intervals is safe)
2. Use smaller batch sizes (currently 100, adjust down if needed)
3. Stagger multiple projects' scrapes (not applicable here)

## Monitoring

### Check Latest Scrape Status

```sql
SELECT id, scraped_at, status, total_models, total_votes
FROM snapshots
ORDER BY scraped_at DESC
LIMIT 1;
```

### Count Models Over Time

```sql
SELECT DATE(scraped_at), total_models
FROM snapshots
WHERE status = 'success'
ORDER BY scraped_at DESC
LIMIT 20;
```

### Failed Scrapes

```sql
SELECT scraped_at, error_message
FROM snapshots
WHERE status = 'failed'
ORDER BY scraped_at DESC
LIMIT 10;
```

### Model Activity

```sql
SELECT canonical_name, first_seen_at, last_seen_at, is_active
FROM models
ORDER BY last_seen_at DESC
LIMIT 20;
```

## Analysis Examples

### Vote Velocity (Votes/Hour)

```python
from analysis.queries import vote_velocity

# Over last 4 snapshots
velocity = vote_velocity("Claude Opus")
print(f"{velocity['votes_per_hour']} votes/hour")
```

### Score Trajectory (Last 30 Days)

```python
from analysis.queries import score_trajectory

trajectory = score_trajectory("Claude Opus", days=30)
for point in trajectory:
    print(f"{point['scraped_at']}: rank={point['rank']}, score={point['score']}")
```

### New Model Report

```python
from analysis.trajectory import new_model_report

report = new_model_report("Claude Opus")
print(f"Launched {report['days_since_launch']} days ago")
print(f"Rank change: {report['rank_change']} positions")
print(f"Score change: +{report['score_change']}")
```

### CI Tightening (Learning Rate)

```python
from analysis.trajectory import ci_tightening_rate

rate = ci_tightening_rate("Claude Opus")
print(f"CI tightening: {abs(rate['ci_slope_per_day'])} units/day")
```

## Performance Tips

1. **Use cached queries** — `load_caches()` avoids per-model HTTP calls
2. **Batch inserts** — All inserts done in 100-row chunks
3. **Indexed lookups** — Rankings indexed on (model_id, created_at DESC)
4. **Connection pooling** — Supabase handles internally

Typical scrape time: ~1.5 seconds (fetch + parse + insert)

## Security Notes

- `.env` contains Supabase service role key (sensitive!)
- Keep out of git (already in .gitignore)
- GitHub Actions secrets are never logged
- Raw HTML hash stored for audit trail (doesn't expose PII)

## Next Steps

1. Deploy to GitHub Actions (set secrets)
2. Monitor first few runs in Actions tab
3. Query database to verify data
4. Set up alerts for failed scrapes
5. Build dashboard on top of views/queries

See README.md for more context.
