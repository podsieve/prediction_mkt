# API Reference

## Core Scraping

### `src.scraper.fetch_page(url: str) -> str`

Fetch HTML from Arena.ai with exponential backoff retry.

**Parameters:**
- `url` (str) — Full URL to fetch

**Returns:**
- (str) — Raw HTML content

**Raises:**
- `requests.exceptions.HTTPError` — After 3 retries, max 60s wait
- `requests.exceptions.Timeout` — After 3 retries

**Example:**
```python
from src.scraper import fetch_page
html = fetch_page("https://arena.ai/leaderboard/text/overall-no-style-control")
```

**Notes:**
- Automatically sets User-Agent header
- Request timeout: 30 seconds
- Exponential backoff: 5s × (2^attempt), max 60s
- Logs all retry attempts at WARNING level

---

### `src.scraper.scrape() -> ScrapeResult`

Fetch and parse leaderboard in one call.

**Parameters:** None

**Returns:**
- `ScrapeResult` — Snapshot with models, votes, metadata

**Raises:**
- `Exception` — On HTTP failure or parse failure

**Example:**
```python
from src.scraper import scrape
result = scrape()
print(f"Parsed {result.total_models} models")
for model in result.models:
    print(f"{model.rank}. {model.model_name}: {model.score}")
```

**Notes:**
- Warns if <50 models (possible layout change)
- Includes raw_html_hash for detecting structure changes
- scrape_duration_ms only includes parsing time

---

## Parsing

### `src.parser.parse_leaderboard(html: str, source_url: str) -> ScrapeResult`

Parse Arena.ai HTML into structured models.

**Parameters:**
- `html` (str) — Raw HTML from arena.ai
- `source_url` (str) — Original URL (for metadata)

**Returns:**
- `ScrapeResult` — See below

**Raises:**
- Does NOT raise; logs warnings for unparseable rows

**Example:**
```python
from src.parser import parse_leaderboard
result = parse_leaderboard(html, "https://arena.ai/leaderboard/text/overall-no-style-control")
print(f"Total votes: {result.total_votes}")
for model in result.models[:5]:
    print(f"{model.rank}. {model.model_name}: {model.score} ± {model.score_ci}")
```

**Notes:**
- Finds tbody, falls back to all tr if missing
- Each field wrapped in try/except (robust parsing)
- Returns all successfully parsed rows even if some fail

### `src.parser.parse_rank_spread(cell) -> Tuple[Optional[str], Optional[int], Optional[int]]`

Extract confidence interval from rank cell.

**Returns:**
- (raw_string, upper_int, lower_int)
- Example: ("1↔3", 1, 3)

### `src.parser.parse_model_cell(cell) -> Tuple[str, Optional[str], Optional[str]]`

Extract model name, organization, license from model cell.

**Returns:**
- (name, organization, license_type)
- Example: ("Claude Opus", "Anthropic", "Proprietary")

### `src.parser.parse_score_cell(cell) -> Tuple[float, Optional[float]]`

Extract score and confidence interval.

**Returns:**
- (score, ci)
- Example: (1473.4, 1.2) or (1473.4, None)

### `src.parser.parse_votes_cell(cell) -> int`

Extract vote count.

**Returns:**
- (votes)
- Example: 23616

---

## Data Models

### `ScrapedModel`

A single model from one snapshot.

**Fields:**
- `rank` (int) — Position (1-357)
- `rank_spread_raw` (str|None) — "1↔3" format
- `rank_upper` (int|None) — Upper confidence bound
- `rank_lower` (int|None) — Lower confidence bound
- `model_name` (str) — Official name (auto-stripped)
- `organization` (str|None) — e.g., "Anthropic"
- `license_type` (str|None) — "Open Source" or "Proprietary"
- `score` (float) — Numerical score
- `score_ci` (float|None) — Confidence interval ± value
- `votes` (int) — Total votes

**Example:**
```python
model = ScrapedModel(
    rank=1,
    rank_upper=1,
    rank_lower=3,
    model_name="Claude Opus",
    organization="Anthropic",
    license_type="Proprietary",
    score=1473.4,
    score_ci=1.2,
    votes=23616
)
```

### `ScrapeResult`

Complete snapshot from one scrape run.

**Fields:**
- `scraped_at` (datetime) — UTC timestamp
- `source_url` (str) — URL that was fetched
- `total_models` (int) — Number of models found
- `total_votes` (int|None) — Total votes across all models
- `models` (List[ScrapedModel]) — All models
- `raw_html_hash` (str) — SHA256 of HTML
- `scrape_duration_ms` (int) — Parse time in milliseconds

**Example:**
```python
result = parse_leaderboard(html, url)
print(f"Snapshot at {result.scraped_at}")
print(f"Models: {result.total_models}, Votes: {result.total_votes}")
print(f"Hash: {result.raw_html_hash}")
```

---

## Database Operations

### `src.db.store_results(scrape_result: ScrapeResult) -> None`

Store a snapshot and all rankings in database.

**Parameters:**
- `scrape_result` (ScrapeResult) — From parse_leaderboard()

**Raises:**
- `Exception` — Supabase connection error

**Example:**
```python
from src.scraper import scrape
from src.db import store_results

result = scrape()
store_results(result)
```

**Does:**
1. Creates snapshot row (metadata)
2. Loads model cache and alias cache (pre-optimization)
3. Auto-detects new models, bulk-inserts them
4. Resolves all model_ids from cache/aliases
5. Bulk-inserts 357 ranking rows
6. Updates last_seen_at for all models
7. Marks missing models as inactive

**Notes:**
- Batches inserts in chunks of 100
- Creates snapshot even if insert fails

### `src.db.record_failed_scrape(error_message: str) -> None`

Log a scrape failure to database.

**Parameters:**
- `error_message` (str) — Error details (truncated to 1000 chars)

**Example:**
```python
from src.db import record_failed_scrape
try:
    result = scrape()
except Exception as e:
    record_failed_scrape(str(e))
```

### `src.db.load_caches(client) -> Tuple[Dict, Dict]`

Pre-load all models and aliases into memory.

**Returns:**
- (model_cache, alias_cache)
- `model_cache`: {canonical_name → model_id}
- `alias_cache`: {alias_name → model_id}

**Example:**
```python
client = get_client()
models, aliases = load_caches(client)
print(f"Loaded {len(models)} models and {len(aliases)} aliases")
```

### `src.db.bulk_insert_new_models(client, new_models: List[Dict], model_cache: Dict) -> Dict`

Insert new models and return updated cache.

**Parameters:**
- `client` — Supabase client
- `new_models` — List of dicts with canonical_name, organization, license_type
- `model_cache` — Existing cache dict

**Returns:**
- Updated model_cache with new IDs

### `src.db.mark_inactive_models(client, seen_model_ids: Set[str], now: str) -> None`

Mark models as inactive if not in this snapshot.

**Parameters:**
- `client` — Supabase client
- `seen_model_ids` — Set of model IDs in this snapshot
- `now` — ISO timestamp string

---

## Analysis Queries

### `analysis.queries.score_trajectory(model_name: str, days: int = 30) -> List[Dict]`

Get score, CI, rank, votes over time.

**Parameters:**
- `model_name` (str) — Canonical model name
- `days` (int) — Look back this many days (default 30)

**Returns:**
- List of dicts with:
  - `scraped_at` (datetime)
  - `rank` (int)
  - `score` (float)
  - `score_ci` (float|None)
  - `votes` (int)
  - `score_delta` (float) — Change from previous snapshot
  - `votes_delta` (int) — Change from previous snapshot
  - `vote_share_pct` (float) — % of total site votes

**Example:**
```python
from analysis.queries import score_trajectory
trajectory = score_trajectory("Claude Opus", days=7)
for point in trajectory:
    print(f"{point['scraped_at']}: {point['rank']}. {point['score']} (±{point['score_ci']})")
```

### `analysis.queries.vote_velocity(model_name: str, last_n_snapshots: int = 4) -> Dict|None`

Calculate votes per hour from last N snapshots.

**Parameters:**
- `model_name` (str) — Canonical model name
- `last_n_snapshots` (int) — Compare oldest to newest (default 4)

**Returns:**
- Dict with:
  - `model` (str)
  - `votes_gained` (int)
  - `hours_elapsed` (float)
  - `votes_per_hour` (float)
  - `current_votes` (int)
- Or None if model not found or <2 snapshots

**Example:**
```python
from analysis.queries import vote_velocity
velocity = vote_velocity("Claude Opus")
print(f"{velocity['votes_per_hour']} votes/hour")
print(f"Gained {velocity['votes_gained']} votes in {velocity['hours_elapsed']} hours")
```

### `analysis.queries.gap_to_first(model_name: str) -> Dict|None`

Current score gap to #1 with CI significance test.

**Returns:**
- Dict with:
  - `model` (str)
  - `rank` (int)
  - `score` (float)
  - `score_ci` (float)
  - `first_place_score` (float)
  - `first_place_ci` (float)
  - `gap` (float) — Score difference
  - `combined_ci` (float) — Sum of both CIs
  - `ci_overlap` (bool) — Gap < combined_ci
  - `statistically_significant` (bool) — Gap > combined_ci
- Or None if models not found

**Example:**
```python
from analysis.queries import gap_to_first
gap = gap_to_first("GPT-4")
print(f"Gap to #1: {gap['gap']} points")
if gap['ci_overlap']:
    print("Not statistically significant")
else:
    print("Statistically significant")
```

### `analysis.queries.first_seen_models(days: int = 7) -> List[Dict]`

New models that appeared in last N days.

**Returns:**
- List of dicts with:
  - `canonical_name` (str)
  - `organization` (str|None)
  - `first_seen_at` (datetime)
  - `is_active` (bool)

**Example:**
```python
from analysis.queries import first_seen_models
new_models = first_seen_models(days=7)
for model in new_models:
    print(f"New: {model['canonical_name']} ({model['organization']})")
```

### `analysis.queries.anomaly_detection(model_name: str, threshold_multiplier: float = 2.0) -> List[Dict]`

Flag snapshots where score moved >threshold × CI.

**Parameters:**
- `model_name` (str)
- `threshold_multiplier` (float) — Default 2.0× CI (can adjust to 3.0×)

**Returns:**
- List of dicts with:
  - `timestamp` (datetime)
  - `score_before` (float)
  - `score_after` (float)
  - `delta` (float) — Absolute change
  - `ci` (float) — Confidence interval
  - `ratio` (float) — delta / ci

**Example:**
```python
from analysis.queries import anomaly_detection
anomalies = anomaly_detection("Claude Opus", threshold_multiplier=2.0)
for anomaly in anomalies:
    print(f"{anomaly['timestamp']}: {anomaly['score_before']} → {anomaly['score_after']} ({anomaly['ratio']}× CI)")
```

---

## Trajectory Analysis

### `analysis.trajectory.ci_tightening_rate(model_name: str) -> Dict|None`

Rate at which confidence interval is narrowing.

**Returns:**
- Dict with:
  - `model` (str)
  - `ci_slope_per_day` (float) — Negative = tightening
  - `tightening` (bool) — slope < 0
  - `initial_ci` (float)
  - `latest_ci` (float)
  - `initial_votes` (int)
  - `latest_votes` (int)
  - `data_points` (int) — Number of snapshots
  - `days_tracked` (float)
- Or None if not enough data

**Interpretation:**
- Negative slope = model gaining more battles = more confident ranking
- Faster tightening = model is stabilizing in position

**Example:**
```python
from analysis.trajectory import ci_tightening_rate
rate = ci_tightening_rate("Claude Opus")
if rate['tightening']:
    print(f"CI tightening at {abs(rate['ci_slope_per_day']):.3f} units/day")
else:
    print(f"CI expanding at {rate['ci_slope_per_day']:.3f} units/day")
```

### `analysis.trajectory.new_model_report(model_name: str) -> Dict|None`

Full launch trajectory report for a new model.

**Returns:**
- Dict with:
  - `model` (str)
  - `organization` (str|None)
  - `days_since_launch` (float)
  - `snapshots_collected` (int)
  - `initial_rank` (int)
  - `current_rank` (int)
  - `rank_change` (int) — Positive = climbed
  - `initial_score` (float)
  - `current_score` (float)
  - `score_change` (float)
  - `initial_ci` (float|None)
  - `current_ci` (float|None)
  - `initial_votes` (int)
  - `current_votes` (int)
  - `votes_gained` (int)
  - `gap_to_first` (float|None)
- Or None if model not found

**Example:**
```python
from analysis.trajectory import new_model_report
report = new_model_report("Claude Opus")
print(f"Launched {report['days_since_launch']} days ago")
print(f"Rank: {report['initial_rank']} → {report['current_rank']} (improved by {report['rank_change']})")
print(f"Score: {report['initial_score']} → {report['current_score']} (+{report['score_change']})")
print(f"Votes: {report['initial_votes']} → {report['current_votes']} (+{report['votes_gained']})")
print(f"Gap to #1: {report['gap_to_first']}")
```

---

## Configuration

### `src.config.settings`

Global settings object loaded from `.env`.

**Attributes:**
- `supabase_url` (str) — Supabase project URL
- `supabase_key` (str) — Supabase service role key
- `scrape_url` (str) — Default: arena.ai text leaderboard
- `request_timeout` (int) — Default: 30 seconds
- `max_retries` (int) — Default: 3
- `retry_delay` (float) — Default: 5.0 seconds
- `user_agent` (str) — Default: "ArenaLeaderboardTracker/1.0"

**Example:**
```python
from src.config import settings
print(f"Scraping from: {settings.scrape_url}")
print(f"Timeout: {settings.request_timeout}s")
```

---

## CLI Tools

### `scripts/merge_models.py`

Merge two model entries (for codename→real name transitions).

**Usage:**
```bash
python scripts/merge_models.py "old-codename" "real-name"
```

**Does:**
1. Validates both models exist
2. Moves all rankings from old → real
3. Adds old as alias to real
4. Updates first_seen_at if old was earlier
5. Deletes old model row

**Example:**
```bash
python scripts/merge_models.py "claude-opus-preview" "Claude Opus"
```

**Output:**
```
INFO: Merging 'claude-opus-preview' (156 rankings) into 'Claude Opus'
INFO: Done. Merged 156 rankings, added alias 'claude-opus-preview' -> 'Claude Opus'
```

---

## Error Codes

| Code | Meaning | Resolution |
|------|---------|-----------|
| 1 (scraper.py main) | Scrape failed, recorded in DB | Check DB for error_message |
| — | Parse error, row skipped | Check logs for "Failed to parse row" |
| — | <50 models parsed | Check if Arena.ai layout changed |
| — | Model not resolved | Check aliases, manually merge if needed |

---

## Performance

Typical values (measured on ubuntu-latest GitHub Actions):
- fetch_page(): 2-5s (network dependent)
- parse_leaderboard(): 200-300ms
- load_caches(): 100-200ms
- bulk inserts: 200-400ms
- Total: ~1.5s wall time

Optimizations used:
- In-memory model cache (avoids 357 HTTP calls)
- Batch inserts (100 rows per request)
- Indexed lookups on (model_id, snapshot_id)

---

## See Also

- README.md — Overview and conventions
- docs/SETUP.md — Local dev and GitHub Actions setup
- docs/CODEMAP.md — Architecture and module breakdown
- sql/001_schema.sql — Table schemas
- sql/002_views.sql — Pre-built views
