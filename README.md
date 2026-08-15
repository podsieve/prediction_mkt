# Arena.ai Leaderboard Tracker

A Python-based system that scrapes the [Arena.ai text leaderboard](https://arena.ai/leaderboard/text/overall-no-style-control) every 2 hours, stores historical snapshots in Supabase Postgres, and provides analysis tools for tracking model performance trajectories.

## What It Does

- **Scrapes** Arena.ai leaderboard HTML with automatic retries
- **Parses** table rows into structured data (rank, score, votes, CI)
- **Stores** snapshots in Postgres with model deduplication and aliasing
- **Analyzes** model trajectories: score velocity, CI tightening, vote velocity, anomaly detection
- **Tracks** new model launches with full performance reports
- **Auto-detects** new models and handles codename→real-name transitions via merge tool

## Quick Start

### Prerequisites

- Python 3.12+
- Supabase project with Postgres database
- GitHub Actions (for automated scheduling)

### Setup

1. Clone the repo and install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Set up environment variables:
   ```bash
   cp .env.example .env
   ```
   Then add your Supabase credentials:
   ```
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_KEY=your-service-role-key
   ```

3. Initialize the database schema:
   ```bash
   # Run sql/001_schema.sql in Supabase SQL editor to create tables
   # Run sql/002_views.sql for analysis views
   ```

4. Test the parser:
   ```bash
   pytest
   ```

5. Run a manual scrape:
   ```bash
   python -m src.scraper
   ```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ GitHub Actions (cron: 0 */2 * * *)                           │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
         ┌─────────────────┐
         │  scraper.py     │  Fetch with retries (Tenacity)
         │  - fetch_page() │  User-Agent, 30s timeout, 3 attempts
         │  - scrape()     │
         └────────┬────────┘
                  │
                  ▼
         ┌──────────────────┐
         │   parser.py      │  BeautifulSoup HTML parser
         │ - parse_rank()   │  Resilient to layout changes
         │ - parse_score()  │  Field-by-field error handling
         │ - parse_votes()  │
         └────────┬─────────┘
                  │
                  ▼
         ┌──────────────────────────────────┐
         │  db.py - Data Normalization      │
         │ - load_caches()                  │  In-memory model/alias cache
         │ - bulk_insert_new_models()       │  Dedup, batch insert
         │ - resolve model IDs              │  Check canonical→ID→alias
         │ - mark_inactive_models()         │
         └────────┬───────────────────────┘
                  │
                  ▼
         ┌────────────────────┐
         │  Supabase Postgres │
         │  ├─ snapshots      │
         │  ├─ rankings       │
         │  ├─ models         │
         │  └─ model_aliases  │
         └────────────────────┘
```

## Core Modules

### `src/parser.py`
Parses HTML table rows into `ScrapedModel` objects. Finds table columns by header text (resilient to CSS changes). Each field wrapped in try/except—one broken field never kills the row.

Key functions:
- `parse_rank_spread()` — Extracts rank confidence interval (↔ notation)
- `parse_model_cell()` — Extracts model name, organization, license
- `parse_score_cell()` — Extracts score and confidence interval (±)
- `parse_total_votes()` — Finds total vote count in page HTML

### `src/scraper.py`
HTTP fetch with exponential backoff retry logic. Calls parser, logs metrics, and coordinates DB insert.

Key functions:
- `fetch_page()` — Decorated with Tenacity retry logic
- `scrape()` — Main entry point; logs model count and warnings
- `main()` — Handles errors, records failed scrapes

### `src/db.py`
Manages Supabase inserts, model deduplication, and alias resolution.

Key functions:
- `load_caches()` — Preload all models and aliases (avoids per-model HTTP calls)
- `bulk_insert_new_models()` — Deduplicate, batch-insert new models
- `store_results()` — Create snapshot, insert rankings, mark inactive models
- `record_failed_scrape()` — Log scrape failures

### `src/models.py`
Pydantic data models with validation:
- `ScrapedModel` — Single leaderboard entry (rank, score, votes, CI)
- `ScrapeResult` — Full snapshot with metadata (hash, duration, total votes)

### `src/config.py`
Settings from environment variables via Pydantic:
- `SUPABASE_URL`, `SUPABASE_KEY` (required)
- `scrape_url` (default: Arena.ai text leaderboard)
- `request_timeout`, `max_retries`, `retry_delay`

### `analysis/queries.py`
Query helpers for score trajectories, vote velocity, anomaly detection, and CI overlap analysis.

Key functions:
- `score_trajectory()` — Score, CI, rank, votes over N days (calls DB RPC)
- `vote_velocity()` — Votes per hour from last N snapshots
- `gap_to_first()` — Score gap to #1 with CI significance test
- `first_seen_models()` — New arrivals in last N days
- `anomaly_detection()` — Flags snapshots where score moved >2× CI

### `analysis/studies.py`
Ad-hoc research studies for deeper analysis (manual-only, not scheduled).

### `analysis/refresh_cadence.py`
Detects Arena.ai's underlying data-refresh cadence by finding snapshots where data genuinely changed and measuring the interval distribution.

### `analysis/aging.py`
Debut-aligned rank/score curves — the data behind the dashboard's **Rank Decay** page.

Aligns every model on day 0 = its own first appearance instead of the calendar, which is
what makes trajectories comparable across models that launched months apart. It exists to
answer one question: after a model launches, does it hold its position?

Key functions:
- `rank_decay(category)` — Full dataset for one category: curves, cohort start, censored count
- `build_curves()` — Raw rankings → one debut-aligned curve per model, with deltas from debut
- `step_interpolate()` — Samples a curve onto a shared day grid, last value carried forward
  (step, not linear — a score holds until Arena's next recompute)
- `median_curve()` — Median + interquartile band across curves, blank where fewer than 3 models

**Only models that debuted after scraping began are usable.** Everything present in a
category's first snapshot has a `first_seen_at` of the day the scraper started, not the day
it launched, so including it would measure the collection window instead of the model. For
`overall` that leaves 34 of 391 models; the page reports how many it excluded.

### `analysis/trajectory.py`
Launch trajectory analysis for new models.

Key functions:
- `ci_tightening_rate()` — Linear regression on CI decay rate (units/day)
- `new_model_report()` — Full report: rank/score change, votes gained, gap to #1

### `scripts/merge_models.py`
CLI tool for handling model renames. Dry run by default:
```bash
python scripts/merge_models.py "old-name" "new-name"           # show the plan
python scripts/merge_models.py "old-name" "new-name" --apply   # execute
```
Moves all rankings from old→new, records old as an alias so future scrapes still
resolve, carries `first_seen_at` back if the old row was seen earlier, and deletes
the old row. Rankings keep their `raw_model_name`, so the merge stays traceable.

**Refuses to merge if both models appear in the same snapshot** — `rankings` is
unique on `(snapshot_id, model_id)`, so two models that were live at once are not a
rename, and merging would fail partway and leave the data half-moved. Confirm a
rename by checking that rank/score/votes carry over unchanged at the handoff.

### `scripts/cleanup_garbage_models.py`
One-off repair for the July 2026 parser regression (see *Guarding against parser
regressions*). Reconstructs each phantom model's real name from its
`organization`/`license_type` columns, merges the rankings back onto the real
model, and deletes the phantom row. Dry run by default:
```bash
python scripts/cleanup_garbage_models.py           # show the plan
python scripts/cleanup_garbage_models.py --apply   # execute
```
Already applied on 2026-07-27 (377 phantoms merged, 25,348 rankings moved). It is
idempotent — a re-run on a clean database reports nothing to do.

## Database Schema

### `models`
Master list of models with deduplication:
- `id` (UUID) — Primary key
- `canonical_name` (TEXT, UNIQUE) — Official model name
- `organization`, `license_type` — Metadata
- `first_seen_at`, `last_seen_at` — Tracking dates
- `is_active` — Set to false when model leaves leaderboard

### `model_aliases`
For codename transitions:
- `model_id` → `models.id`
- `alias_name` (UNIQUE) — Old name (e.g., "claude-opus-preview")

### `snapshots`
Metadata for each scrape run:
- `id` (UUID) — Primary key
- `scraped_at` (TIMESTAMPTZ) — When scrape ran
- `total_models`, `total_votes` — Count from page
- `status` — "success" or "failed"
- `error_message` — If status="failed"
- `raw_html_hash` — SHA256 of HTML (detect layout changes)

### `rankings`
One row per model per snapshot:
- `snapshot_id` → `snapshots.id`
- `model_id` → `models.id`
- `rank` — Current position
- `rank_upper`, `rank_lower` — Confidence interval bounds
- `score`, `score_ci` — Numerical score ± CI
- `votes` — Total votes for this model
- `raw_model_name`, `raw_organization` — Preserve original text for traceability

## Views

### `latest_rankings`
Latest ranking for each model:
```sql
SELECT * FROM latest_rankings WHERE canonical_name = 'Claude Opus'
```

### `model_trajectory`
Full time-series with deltas:
```sql
SELECT * FROM model_trajectory 
WHERE canonical_name = 'Claude Opus' 
ORDER BY scraped_at DESC
```

### `new_model_appearances`
First-ever ranking for each model:
```sql
SELECT * FROM new_model_appearances 
WHERE first_seen_at > now() - interval '7 days'
```

## GitHub Actions Workflow

File: `.github/workflows/scrape.yml`

- **Schedule:** Every 2 hours (`0 */2 * * *`). Arena refreshes roughly weekly, so most runs are duplicates; the uniform grid pins each refresh to a ≤2h window without relying on a day-of-week pattern
- **Manual trigger:** Workflow dispatch with optional reason
- **Environment:** Ubuntu latest, Python 3.12
- **Dependencies:** pip cache
- **Secrets required:**
  - `SUPABASE_URL`
  - `SUPABASE_KEY`

## Error Handling

### Parser
- Each field wrapped in try/except — missing/malformed data logged as warning, row still inserted if rank/name/score parse
- If parse errors >0, logged but doesn't halt execution
- If <50 models parsed, warning logged (possible layout change)

### Scraper
- Exponential backoff retry with 3 attempts, max 60s wait
- Recoverable errors: ConnectionError, Timeout, HTTPError
- Non-recoverable errors trigger `record_failed_scrape()` and exit with code 1
- Failed scrape still recorded in DB with error_message

### Database
- New models auto-detected and inserted on first appearance
- Model aliasing prevents duplicates after renames
- Missing models marked inactive when not found in snapshot
- Bulk inserts in batches of 100 (avoids timeouts)
- `check_new_model_surge()` aborts the whole snapshot if a scrape would create
  more than `max_new_models_per_scrape` (default 25) models at once

### Guarding against parser regressions
Between 2026-07-09 and 2026-07-20 Arena rendered the model cell without its
`<a title="...">` wrapper. The parser fell back to the whole cell's text, which
concatenates the org logo's `<svg><title>`, the model name, and the
`Org · License` caption with no separator:

```
claude-fable-5  ->  Anthropicclaude-fable-5Anthropic · Proprietary
tulu-2-dpo-70b  ->  tulu-2-dpo-70bAI2 ImpACT Low-risk
```

Every mangled name looked like a brand-new model, so 377 phantom rows were
created and each real model's history was split in two. Four layers now prevent
a repeat:

1. **Parse precisely** — `first_text()` skips `<svg>` subtrees so the org logo
   can never prefix the name.
2. **Repair and verify** — `strip_org_suffix()` peels a concatenated caption
   back off using the org/license text, which is parsed independently and was
   correct for 347/347 affected rows. If the caption still survives in the name,
   `parse_model_cell()` raises and the row is dropped rather than stored.
3. **Reject at the model boundary** — `ScrapedModel` refuses any name containing
   `·`, a newline, or over 80 characters.
4. **Circuit-break the write** — `check_new_model_surge()` refuses to store a
   snapshot that wants to mint more than 25 new models. Arena's busiest real day
   added 8; the regression tried to add 369. A blocked category records a failed
   snapshot and the other categories still store normally.

Layer 4 is the general net: it catches any future layout change regardless of
what shape the bad names take.

> **Note:** paginated Supabase reads must call `.order("id")` before `.range()`.
> Without a stable sort, Postgres may return a different row order per page, so
> paging silently duplicates some rows and drops others.

## Data Flow Example

1. **GitHub Actions** triggers every 2 hours
2. **scraper.py** fetches HTML from arena.ai (with retries)
3. **parser.py** extracts 357 models, 6.1M votes, 6 parse errors logged
4. **db.py**:
   - Creates snapshot row (success, duration=234ms)
   - Loads cache: 347 known models, 12 aliases
   - Finds 2 new models (e.g., "GPT-5", "LLaMA3"), inserts them
   - Resolves all 357 model IDs (via cache and aliases)
   - Inserts 357 ranking rows in 4 batches
   - Updates last_seen_at for all 357 models
   - Marks any previously-active models not in this snapshot as inactive
5. **Database** now has one more snapshot with 357 rankings

## Development

### Tests
```bash
pytest                    # Run all tests
pytest -v                 # Verbose output
pytest tests/test_parser.py  # Single file
```

Tests use a saved HTML fixture (`tests/fixtures/sample_leaderboard.html`), not live fetches.

### Local Scrape
```bash
python -m src.scraper
```
Requires `.env` with valid Supabase credentials.

### Manual Model Merge
When "Claude Opus Preview" is renamed to "Claude Opus":
```bash
python scripts/merge_models.py "Claude Opus Preview" "Claude Opus"
```
All rankings from preview→opus, adds preview as alias, deletes preview row.

### Dashboard
```bash
streamlit run dashboard/app.py
```
Four pages, each honouring the sidebar category selector:

| Page | Shows |
|---|---|
| Overview | Current full leaderboard + top-10 score lines over calendar time |
| Model Detail | One model's rank, score (with CI band), votes, and CI tightening |
| Rank Decay | Every model aligned on its own debut — see below |
| New Models | Recent arrivals, with a lookback slider |

**Rank Decay** is the one to reach for when asking *"do models hold their position after
launch?"*. Two panels share a since-debut x-axis: rank change on top (axis inverted, so
falling reads as falling), score change below. One step line per model, with a median and
interquartile band over the top. Lines are stepped rather than smoothed because a score
holds whatever Arena last published until the next recompute.

The pattern it surfaces: **score stays roughly flat while rank decays** — models mostly
don't get worse, the field improves around them. A second chart medians each debut-month
cohort separately, which answers whether that decay is accelerating as the board fills up.

Note the page charts only models that debuted after scraping began (34 of 391 for
`overall`) and states how many it excluded — see `analysis/aging.py` for why.

## Conventions

- **Parser resilience:** Finds columns by header text, not CSS classes
- **Error handling:** All field parses wrapped in try/except
- **Traceability:** `raw_model_name` always preserved in rankings
- **Logging:** Use `logging` module, not `print`
- **Bulk operations:** Batch in 100-row chunks (Supabase best practice)
- **Caching:** Pre-load models/aliases in-memory to avoid per-model HTTP calls

## Troubleshooting

### No models parsed (<50)
Check Arena.ai HTML structure hasn't changed. Compare with fixture in tests/.

### Model resolution fails
Ensure model exists in `models` table or has entry in `model_aliases`.

### CI values missing
Arena.ai sometimes omits CI for new/low-vote models. Script handles null gracefully.

### Supabase rate limits
Use bulk inserts (batches of 100) and .limit(1) instead of .maybe_single() quirk.

## Architecture Decisions

1. **Supabase (not PostgreSQL):** Managed service, no ops overhead
2. **In-memory caches:** Avoid per-model HTTP round-trips to DB
3. **Bulk inserts:** 100-row batches prevent timeouts
4. **Column detection by text:** Resilient to CSS class renames
5. **raw_model_name preservation:** Traceability if display name changes
6. **Alias table:** Handle codename→real name transitions without data loss
7. **Snapshot-based:** Every run creates a snapshot row (even failures) for audit trail

## Next Steps

- **Alerts:** Slack notifications for anomalies (score jump >3× CI)
- **API endpoint:** FastAPI to serve latest rankings and trajectories
- **Forecast:** ARIMA on vote velocity to predict model saturation
- **Clustering:** Group similar models by score, org, license

## License

Proprietary (Shyam Vora)

## Contact

shyamvora91@gmail.com
