# Quick Reference Card

## Installation

```bash
# Clone
git clone https://github.com/shyamvora/prediction_mkt.git
cd prediction_mkt

# Virtual environment
python -m venv venv
source venv/bin/activate

# Install
pip install -r requirements.txt

# Setup
cp .env.example .env
# Edit .env with SUPABASE_URL and SUPABASE_KEY
```

## Key Commands

```bash
# Run scraper locally
python -m src.scraper

# Run tests
pytest
pytest -v

# Merge models (for renames)
python scripts/merge_models.py "old-name" "real-name"

# Check git status
git status
```

## Database Setup

1. Create Supabase project (https://supabase.com)
2. Go to SQL Editor
3. Paste and run `sql/001_schema.sql`
4. Paste and run `sql/002_views.sql`

## Environment Variables

```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-service-role-key
```

## Core Python Functions

```python
# Scrape
from src.scraper import scrape
result = scrape()  # Returns ScrapeResult

# Parse
from src.parser import parse_leaderboard
result = parse_leaderboard(html, url)

# Store
from src.db import store_results
store_results(result)

# Query trajectory
from analysis.queries import score_trajectory
trajectory = score_trajectory("Claude Opus", days=30)

# Velocity
from analysis.queries import vote_velocity
velocity = vote_velocity("Claude Opus")

# New model report
from analysis.trajectory import new_model_report
report = new_model_report("Claude Opus")

# CI tightening
from analysis.trajectory import ci_tightening_rate
rate = ci_tightening_rate("Claude Opus")
```

## Common SQL Queries

```sql
-- Latest rankings
SELECT * FROM latest_rankings ORDER BY rank LIMIT 10;

-- Model history
SELECT * FROM model_trajectory 
WHERE canonical_name = 'Claude Opus' 
ORDER BY scraped_at DESC LIMIT 20;

-- New models (7 days)
SELECT * FROM new_model_appearances 
WHERE first_seen_at > now() - interval '7 days' 
ORDER BY first_seen_at DESC;

-- Check scrape status
SELECT scraped_at, status, total_models FROM snapshots 
ORDER BY scraped_at DESC LIMIT 5;

-- Find failed scrapes
SELECT * FROM snapshots 
WHERE status = 'failed' 
ORDER BY scraped_at DESC;

-- Model activation status
SELECT canonical_name, is_active, last_seen_at FROM models 
ORDER BY last_seen_at DESC LIMIT 20;

-- Check aliases
SELECT * FROM model_aliases WHERE alias_name = 'old-name';
```

## GitHub Actions Setup

1. Go to repo → Settings → Secrets and variables → Actions
2. Add two secrets:
   - `SUPABASE_URL` = your Supabase URL
   - `SUPABASE_KEY` = your service role key
3. Workflow runs automatically every 6 hours
4. Or trigger manually: Actions tab → Scrape Arena Leaderboard → Run workflow

## Data Models

```python
# ScrapedModel (one model in snapshot)
model = ScrapedModel(
    rank=1,
    model_name="Claude Opus",
    organization="Anthropic",
    license_type="Proprietary",
    score=1473.4,
    score_ci=1.2,
    votes=23616,
    rank_upper=1,
    rank_lower=3
)

# ScrapeResult (full snapshot)
result = ScrapeResult(
    scraped_at=datetime.now(timezone.utc),
    source_url="https://arena.ai/...",
    total_models=357,
    total_votes=6110156,
    models=[model1, model2, ...],
    raw_html_hash="abc123...",
    scrape_duration_ms=234
)
```

## Database Tables (Quick Overview)

| Table | Purpose | Key Column |
|-------|---------|-----------|
| `models` | Master list | `canonical_name` (UNIQUE) |
| `snapshots` | Scrape metadata | `scraped_at`, `status` |
| `rankings` | Scores per snapshot | `(snapshot_id, model_id)` UNIQUE |
| `model_aliases` | Handle renames | `alias_name` (UNIQUE) |

## Troubleshooting

| Problem | Solution |
|---------|----------|
| No .env file | `cp .env.example .env` and add credentials |
| Tests fail | Run `pytest -v` to see errors |
| <50 models parsed | Check if Arena.ai layout changed |
| Model not found | Check `model_aliases` or manually insert |
| Supabase timeout | Reduce batch size (currently 100) |
| CI values NULL | Normal for new/low-vote models |

## Performance Targets

- fetch_page(): 2-5s
- parse_leaderboard(): 200-300ms
- store_results(): 500-800ms
- Total: ~1.5 seconds

## File Locations

```
README.md           ← Start here
docs/INDEX.md       ← Doc index
docs/CODEMAP.md     ← Architecture
docs/SETUP.md       ← Operations
docs/API.md         ← Function reference
docs/QUICK_REF.md   ← This file

src/scraper.py      ← Main entry
src/parser.py       ← HTML parsing
src/db.py           ← Database
src/models.py       ← Pydantic schemas
src/config.py       ← Settings

sql/001_schema.sql  ← Create tables
sql/002_views.sql   ← Create views

.github/workflows/scrape.yml ← GitHub Actions
```

## Useful Links

- Arena.ai: https://arena.ai/leaderboard/text/overall-no-style-control
- Supabase: https://app.supabase.com
- GitHub: https://github.com/shyamvora/prediction_mkt
- Tests: `pytest`

## Contact

Shyam Vora - shyamvora91@gmail.com

---

For detailed info, see:
- **Setup**: docs/SETUP.md
- **Architecture**: docs/CODEMAP.md
- **API**: docs/API.md
