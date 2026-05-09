# Documentation Index

Welcome to the Arena.ai Leaderboard Tracker documentation. This project scrapes LLM rankings every 6 hours and provides analysis tools for tracking model performance.

## Quick Navigation

### Getting Started
- **[README.md](../README.md)** — Overview, quick start, and architecture overview
- **[SETUP.md](SETUP.md)** — Local development setup, GitHub Actions deployment, troubleshooting

### For Developers
- **[CODEMAP.md](CODEMAP.md)** — Module breakdown, data flow, design patterns, conventions
- **[API.md](API.md)** — Function signatures, parameters, return types, examples

### For Database
- **[sql/001_schema.sql](../sql/001_schema.sql)** — Core tables (models, snapshots, rankings, aliases)
- **[sql/002_views.sql](../sql/002_views.sql)** — Analysis views (latest_rankings, model_trajectory, new_model_appearances)

### For Operators
- **[SETUP.md → GitHub Actions](SETUP.md#github-actions-deployment)** — Automated scraping every 6h
- **[SETUP.md → Monitoring](SETUP.md#monitoring)** — Database queries to check status
- **[SETUP.md → Troubleshooting](SETUP.md#troubleshooting)** — Common issues and fixes

---

## Document Purposes

### README.md
**Audience:** Anyone new to the project  
**Contains:**
- What it does (scrapes Arena.ai, stores snapshots, analyzes trajectories)
- Quick start (install, setup, test)
- Architecture diagram
- Core modules overview
- Database schema
- GitHub Actions workflow
- Error handling strategy
- Development commands
- Conventions and best practices

**Read this first** if you want a high-level understanding.

---

### CODEMAP.md
**Audience:** Developers, maintainers  
**Contains:**
- Entry points (scraper, pytest, merge script)
- Architecture diagram (detailed)
- Module table (purpose, classes, dependencies)
- Data flow walkthrough (scrape cycle, model deduplication)
- Design patterns (caching, batch inserts, resilient parsing)
- External dependencies (versions, purposes)
- Database relationships
- Configuration details
- Error handling strategy
- Performance characteristics
- Testing strategy
- Conventions and warnings

**Read this** to understand the internal architecture and how to modify the code.

---

### SETUP.md
**Audience:** Operators, DevOps, new developers  
**Contains:**
- Local development setup (venv, pip install)
- Supabase project creation and schema setup
- Environment variables (.env)
- GitHub Actions secrets configuration
- Testing locally
- Database walkthroughs with SQL examples
- Common operations (queries, merges)
- Troubleshooting (layout changes, model resolution, rate limits)
- Monitoring queries
- Analysis code examples
- Performance tips
- Security notes

**Read this** to get the system running locally or in production.

---

### API.md
**Audience:** Developers using the library  
**Contains:**
- Function signatures (parameters, returns, raises)
- Pydantic model definitions
- Database operations (store, load, cache)
- Analysis queries with examples
- Trajectory analysis functions
- Configuration object
- CLI tool usage
- Error codes
- Performance benchmarks

**Read this** when you need to call a function or integrate the library.

---

## Architecture Overview

```
GitHub Actions (every 6h)
    ↓
scraper.py ← fetch_page() with retries
    ↓
parser.py ← parse_leaderboard() with BeautifulSoup
    ↓
db.py ← store_results() with deduplication
    ↓
Supabase Postgres (snapshots, models, rankings)
    ↓
analysis/ ← queries.py, trajectory.py for insights
```

See [CODEMAP.md](CODEMAP.md#architecture-diagram) for detailed architecture.

---

## Database Structure

**Primary Tables:**
- `models` — Master list (id, canonical_name, organization, is_active)
- `snapshots` — Scrape metadata (scraped_at, total_models, status)
- `rankings` — Model scores per snapshot (rank, score, votes, CI)
- `model_aliases` — Handle renames (alias_name → model_id)

**Views (Pre-built Queries):**
- `latest_rankings` — Most recent ranking for each model
- `model_trajectory` — Time-series with deltas (score_delta, votes_delta)
- `new_model_appearances` — First-ever ranking for each model

See [README.md → Database Schema](../README.md#database-schema) or [SETUP.md → Database Walkthroughs](SETUP.md#database-schema-walkthrough).

---

## Common Tasks

### I want to...

**...run a scrape locally**
```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with Supabase credentials
python -m src.scraper
```
→ See [SETUP.md → Local Development](SETUP.md#local-development-setup)

**...deploy to GitHub Actions**
1. Create Supabase project
2. Run SQL schema and views
3. Set repo secrets (SUPABASE_URL, SUPABASE_KEY)
4. Workflow runs automatically every 6 hours

→ See [SETUP.md → GitHub Actions](SETUP.md#github-actions-deployment)

**...query the database**
```sql
SELECT * FROM latest_rankings ORDER BY rank LIMIT 10;
SELECT canonical_name, score, score_ci FROM latest_rankings WHERE canonical_name = 'Claude Opus';
```
→ See [SETUP.md → Common Operations](SETUP.md#common-operations)

**...handle a model rename**
```bash
python scripts/merge_models.py "old-name" "real-name"
```
→ See [SETUP.md → Manual Model Merge](SETUP.md#manual-model-merge)

**...analyze a model's trajectory**
```python
from analysis.queries import score_trajectory, vote_velocity
from analysis.trajectory import new_model_report

trajectory = score_trajectory("Claude Opus", days=30)
velocity = vote_velocity("Claude Opus")
report = new_model_report("Claude Opus")
```
→ See [API.md → Analysis Queries](API.md#analysis-queries)

**...understand why <50 models parsed**
1. Check if Arena.ai changed HTML structure
2. Compare current HTML with fixture
3. Update parser if column order changed

→ See [SETUP.md → Troubleshooting](SETUP.md#troubleshooting)

**...modify the scraper**
1. Check current behavior in [CODEMAP.md → Data Flow](CODEMAP.md#data-flow)
2. Review parsing logic in [API.md → Parsing](API.md#parsing)
3. Update parser.py
4. Run tests: `pytest`
5. Test locally with .env

→ See [CODEMAP.md → Design Patterns](CODEMAP.md#key-design-patterns)

---

## Key Concepts

### Model Deduplication
Arena.ai uses different names for models over time (e.g., "claude-opus-preview" → "Claude Opus").

Solution: `model_aliases` table maps old names to canonical model IDs. Merge script:
```bash
python scripts/merge_models.py "claude-opus-preview" "Claude Opus"
```

See [README.md → Conventions](../README.md#conventions) and [CODEMAP.md → Model Deduplication](CODEMAP.md#model-deduplication).

### Confidence Intervals (CI)
Arena.ai shows ± values (e.g., 1473.4 ± 1.2) indicating statistical confidence.

- Smaller CI = more confident ranking (more battles fought)
- Larger CI = less confident (newer/less votes)
- Used for anomaly detection: flag if delta > 2× CI

See [API.md → gap_to_first](API.md#analysissqueriesgap_to_firstmodel_name-str---dictNone) for significance testing.

### Batch Inserts
All database inserts done in chunks of 100 (Supabase best practice, avoids timeouts).

See [CODEMAP.md → Batch Inserts](CODEMAP.md#2-batch-inserts).

### In-Memory Caching
All models and aliases pre-loaded at start of scrape to avoid 357 individual HTTP calls.

See [CODEMAP.md → In-Memory Caching](CODEMAP.md#1-in-memory-caching).

---

## Troubleshooting Index

| Issue | See |
|-------|-----|
| No models parsed (<50) | [SETUP.md → Issue: "No models parsed"](SETUP.md#issue-no-models-parsed-50) |
| "Could not resolve model_id" | [SETUP.md → Issue: "Could not resolve"](SETUP.md#issue-could-not-resolve-model_id) |
| Model stuck as inactive | [SETUP.md → Issue: "Model stuck"](SETUP.md#issue-model-stuck-as-inactive) |
| CI values all NULL | [SETUP.md → Issue: "CI values all NULL"](SETUP.md#issue-ci-values-all-null) |
| Rate limits (429) | [SETUP.md → Issue: "Rate limits"](SETUP.md#issue-supabase-rate-limits) |
| Tests failing | [CODEMAP.md → Testing](CODEMAP.md#testing) |
| Layout changed | [README.md → Watch out for](../README.md#watch-out-for) |

---

## File Structure

```
prediction_mkt/
├── README.md                          ← Start here
├── requirements.txt                   ← Dependencies
├── .env.example                       ← Template
├── .github/workflows/scrape.yml       ← GitHub Actions
│
├── src/
│   ├── __main__.py                   ← Entry point
│   ├── scraper.py                    ← HTTP fetch + orchestration
│   ├── parser.py                     ← HTML → structured data
│   ├── db.py                         ← Supabase operations
│   ├── config.py                     ← Settings from env
│   └── models.py                     ← Pydantic schemas
│
├── analysis/
│   ├── queries.py                    ← Time-series queries
│   └── trajectory.py                 ← Launch trajectory analysis
│
├── scripts/
│   └── merge_models.py               ← CLI for model merges
│
├── sql/
│   ├── 001_schema.sql                ← Tables
│   └── 002_views.sql                 ← Analysis views
│
├── tests/
│   ├── test_parser.py                ← Parser tests
│   └── fixtures/
│       └── sample_leaderboard.html   ← Real HTML fixture
│
└── docs/
    ├── INDEX.md                      ← This file
    ├── README.md                     ← (redundant link)
    ├── CODEMAP.md                    ← Architecture
    ├── SETUP.md                      ← Operations
    └── API.md                        ← Function reference
```

---

## External Resources

- **Arena.ai**: https://arena.ai/leaderboard/text/overall-no-style-control
- **Supabase Docs**: https://supabase.com/docs
- **BeautifulSoup**: https://www.crummy.com/software/BeautifulSoup/
- **Pydantic**: https://docs.pydantic.dev/
- **Tenacity**: https://tenacity.readthedocs.io/
- **GitHub Actions**: https://docs.github.com/en/actions

---

## Contact & License

**Author:** Shyam Vora (shyamvora91@gmail.com)  
**License:** Proprietary  
**Last Updated:** 2026-05-09

For questions, check the troubleshooting sections or review the relevant document above.
