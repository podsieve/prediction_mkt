# Arena.ai Leaderboard Tracker — Codemap

**Last Updated:** 2026-05-09
**Stack:** Python 3.12, Supabase Postgres, BeautifulSoup, Pydantic, Tenacity, GitHub Actions

## Entry Points

- `python -m src.scraper` — Main scraper (run by GitHub Actions every 6h)
- `pytest` — Test suite
- `python scripts/merge_models.py <old> <keep>` — Model merge CLI

## Architecture Diagram

```
┌─ GitHub Actions (cron: 0 */6 * * *) ─────────────┐
│                                                    │
├─ scraper.py ◀─ fetch_page() with Tenacity retries│
│  ├─ parser.py ◀─ parse_leaderboard()             │
│  │  ├─ parse_rank_spread()                       │
│  │  ├─ parse_model_cell()                        │
│  │  ├─ parse_score_cell()                        │
│  │  └─ parse_votes_cell()                        │
│  │                                                │
│  └─ db.py ◀─ store_results()                     │
│     ├─ load_caches() [models, aliases]          │
│     ├─ bulk_insert_new_models()                 │
│     ├─ resolve model_id from cache/aliases      │
│     ├─ bulk_insert rankings                     │
│     └─ mark_inactive_models()                   │
│                                                  │
├─ Supabase Postgres                              │
│  ├─ snapshots (metadata for each run)           │
│  ├─ models (canonical names)                    │
│  ├─ model_aliases (codename→real)               │
│  └─ rankings (model scores per snapshot)        │
│                                                  │
└─ Analysis Layer (queries.py, trajectory.py) ────┘
   ├─ score_trajectory()
   ├─ vote_velocity()
   ├─ gap_to_first()
   ├─ ci_tightening_rate()
   └─ new_model_report()
```

## Module Breakdown

### Source Code

| Module | Purpose | Key Classes/Functions | Dependencies |
|--------|---------|----------------------|--------------|
| `src/models.py` | Pydantic data models | `ScrapedModel`, `ScrapeResult` | pydantic |
| `src/parser.py` | HTML parsing to structured data | `parse_leaderboard()`, `parse_score_cell()`, `parse_rank_spread()` | beautifulsoup4 |
| `src/scraper.py` | HTTP fetch + orchestration | `fetch_page()`, `scrape()`, `main()` | requests, tenacity |
| `src/db.py` | Supabase operations | `store_results()`, `load_caches()`, `bulk_insert_new_models()` | supabase |
| `src/config.py` | Settings from env | `Settings`, `settings` | pydantic-settings |
| `src/__main__.py` | Entry point runner | — | — |

### Analysis

| Module | Purpose | Key Functions |
|--------|---------|---|
| `analysis/queries.py` | Time-series queries | `score_trajectory()`, `vote_velocity()`, `gap_to_first()`, `anomaly_detection()` |
| `analysis/trajectory.py` | New model analysis | `ci_tightening_rate()`, `new_model_report()` |

### Scripts

| Script | Purpose | Usage |
|--------|---------|-------|
| `scripts/merge_models.py` | Merge model entries | `python scripts/merge_models.py "old" "keep"` |

### Database

| File | Purpose |
|------|---------|
| `sql/001_schema.sql` | Core tables (models, snapshots, rankings, aliases) |
| `sql/002_views.sql` | Analysis views (latest_rankings, model_trajectory, new_model_appearances) |

### CI/CD

| File | Purpose |
|------|---------|
| `.github/workflows/scrape.yml` | GitHub Actions workflow (6h cron, manual dispatch) |

### Tests

| File | Purpose | Fixtures |
|------|---------|----------|
| `tests/test_parser.py` | Parser validation (357 models, 6.1M votes) | `tests/fixtures/sample_leaderboard.html` |

## Data Flow

### Scrape Cycle (every 6h)

```
1. GitHub Actions triggers
   ↓
2. scraper.py:fetch_page()
   → Tenacity retry (3 attempts, exp backoff, max 60s)
   → Returns HTML
   ↓
3. parser.py:parse_leaderboard()
   → BeautifulSoup find tbody or tr
   → For each row: parse_rank_spread(), parse_model_cell(), parse_score_cell(), parse_votes_cell()
   → Each field wrapped in try/except
   → Returns ScrapeResult (total_models, total_votes, [ScrapedModel, ...], raw_html_hash)
   ↓
4. db.py:store_results()
   → Create snapshots row (scraped_at, total_models, status='success', raw_html_hash)
   → load_caches() → {canonical_name → id}, {alias_name → id}
   → For each scraped_model: check if model_name in cache → if not, add to new_models
   → bulk_insert_new_models() → insert new models in batches of 100
   → For each scraped_model: resolve model_id from cache/aliases
   → bulk_insert rankings in batches of 100
   → Update last_seen_at for all models in this snapshot
   → mark_inactive_models() → set is_active=false for models not in snapshot
   ↓
5. Postgres stores snapshot + 357 ranking rows
```

### Model Deduplication

```
Scrape finds "claude-opus" (first time)
→ Check model_cache: not found
→ Check alias_cache: not found
→ Add to new_models: {canonical_name: "claude-opus", ...}
→ bulk_insert_new_models() inserts and returns id
→ Insert ranking with that model_id

Later, Arena.ai renames to "Claude Opus" (different spelling)
→ Scraper finds "Claude Opus"
→ Check model_cache: not found
→ Check alias_cache: not found
→ Insert as new model (now have 2 rows)

Manual fix:
python scripts/merge_models.py "claude-opus" "Claude Opus"
→ Move all rankings from old_id → keep_id
→ Insert alias: alias_name="claude-opus" → model_id=keep_id
→ Delete old model row
→ Now only "Claude Opus" exists, old name is alias
```

## Key Design Patterns

### 1. In-Memory Caching
```python
# Load all models + aliases once at start of store_results()
model_cache, alias_cache = load_caches(client)
# Then for each scraped_model:
model_id = model_cache.get(name) or alias_cache.get(name)
# Avoids 357 individual HTTP calls to Supabase
```

### 2. Batch Inserts
```python
# Insert in chunks of 100
for i in range(0, len(unique), 100):
    batch = unique[i:i + 100]
    client.table("models").insert(batch).execute()
# Prevents timeout on large snapshots
```

### 3. Resilient Parsing
```python
try:
    rank = parse_int(cells[0].get_text())
    # ... parse other fields
    model = ScrapedModel(...)
    models.append(model)
except Exception as e:
    parse_errors += 1
    logger.warning("Failed to parse row: %s | Error: %s", row_text, e)
    # Skip this row, continue with next
```

### 4. Retry with Exponential Backoff
```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=5, max=60),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def fetch_page(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.text
```

### 5. Column Detection by Header Text
```python
# Instead of finding <td class="score-column">, find by text:
tbody = soup.find("tbody")
rows = tbody.find_all("tr")
for row in rows:
    cells = row.find_all("td")
    rank = parse_int(cells[0])  # Assumes order: rank, spread, name, score, votes
    # Resilient to CSS class changes
```

## External Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| requests | >=2.31.0 | HTTP client for fetch_page() |
| beautifulsoup4 | >=4.12.0 | HTML parsing (BeautifulSoup) |
| supabase | >=2.0.0 | Postgres client (create_client, table ops) |
| pydantic | >=2.0.0 | Data validation (BaseModel, field_validator) |
| pydantic-settings | >=2.0.0 | Env var loading (BaseSettings) |
| tenacity | >=8.2.0 | Retry logic (@retry decorator) |
| pytest | (dev) | Test runner |

## Database Relationships

```
models (id, canonical_name, organization, ...)
  ↓ (1:N)
model_aliases (model_id, alias_name)
  
models (id, ...)
  ↓ (1:N)
rankings (model_id, snapshot_id, rank, score, votes, ...)
  
snapshots (id, scraped_at, status, ...)
  ↓ (1:N)
rankings (snapshot_id, ...)
```

## Configuration

Loaded from `.env` via Pydantic settings:
- `SUPABASE_URL` (required) — Supabase endpoint
- `SUPABASE_KEY` (required) — Supabase service role key
- `scrape_url` (default) — Arena.ai text leaderboard
- `request_timeout` (default=30) — HTTP timeout in seconds
- `max_retries` (default=3) — Retry attempts
- `retry_delay` (default=5.0) — Initial retry delay in seconds
- `user_agent` (default=ArenaLeaderboardTracker/1.0)

## Error Handling Strategy

| Error | Where | Action |
|-------|-------|--------|
| Parse error (malformed cell) | parser.py | Log warning, skip field, try next field |
| Parse error (missing field) | parser.py | Log warning, skip row if rank/name/score can't parse |
| <50 models parsed | scraper.py | Log warning (possible layout change) |
| HTTP error (timeout, connection) | scraper.py | Retry with exponential backoff (3×) |
| Other exception | scraper.py main() | Log error, call record_failed_scrape(), exit(1) |
| Failed scrape | db.py | Create snapshot row with status='failed', error_message |

## Performance Characteristics

| Operation | Time | Notes |
|-----------|------|-------|
| fetch_page() | 2-5s | Network dependent |
| parse_leaderboard() | 200-300ms | BeautifulSoup + 357 rows |
| load_caches() | 100-200ms | Single query × 2 (models + aliases) |
| bulk_insert_new_models() | 50-100ms | Typically 0-10 new models |
| bulk_insert rankings | 200-400ms | 4 batches of 100 |
| Total scrape_duration_ms | 234ms (recorded) | Parsing + Supabase inserts |

## Testing

**Test file:** `tests/test_parser.py`
**Fixture:** `tests/fixtures/sample_leaderboard.html` (real Arena.ai snapshot)

Test coverage:
- Parse integer with/without commas
- Leaderboard: total models (357), total votes (6.1M), model count
- First model: rank, name, score, CI, votes, organization, license
- Rank spread: upper/lower bounds
- All ranks sequential (1-357)
- All scores positive
- All votes non-negative

No integration tests (uses fixture, not live scrape).

## Conventions & Warnings

1. **Never use `.maybe_single()`** — Use `.limit(1)` instead (Supabase quirk)
2. **All fields wrapped in try/except** — One broken field won't kill the row
3. **raw_model_name preserved** — For traceability if display name changes
4. **Logging not print()** — Use `logging.getLogger(__name__)`
5. **Batch inserts in 100s** — Avoid Supabase timeout
6. **Column detection by text** — Not CSS classes (resilient to restyling)
7. **Mark models inactive** — When they disappear from leaderboard
8. **Snapshot created even on failure** — For audit trail (status='failed')

## Related Areas

See also:
- `.env.example` — Configuration template
- `.github/workflows/scrape.yml` — GitHub Actions workflow
- `sql/001_schema.sql` — Database schema
- `sql/002_views.sql` — Analysis views
