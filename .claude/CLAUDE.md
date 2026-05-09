# Project: Arena.ai Leaderboard analysis (scrape leaderboard data for AI mdoels, then analyze it)

## Commands
python -m src.scraper                              # Run scraper (requires .env)
pytest                                             # Run tests
python scripts/merge_models.py "old" "keep"        # Merge two model entries (moves rankings, adds alias)
python -m alerts.run events                        # Run post-scrape alert checks
python -m alerts.run digest                        # Send daily digest email
streamlit run dashboard/app.py                     # Run dashboard locally

## Architecture
- Python, Supabase Postgres, GitHub Actions (every 6h)
- src/parser.py parses Arena.ai HTML into Pydantic models
- src/scraper.py fetches page with retries, calls parser, stores to DB
- src/db.py handles model resolution (aliases → canonical names) and bulk inserts
- analysis/ has query helpers for score trajectory, vote velocity, CI tightening
- alerts/ handles post-scrape event alerts and daily digest emails via Resend
- dashboard/app.py is a Streamlit app for visualizing rankings and model trajectories
- sql/ contains the Supabase schema and views — run these manually in the Supabase SQL editor

## Data flow
Cron → fetch HTML → parse table rows → resolve model IDs (check aliases first) → bulk insert rankings → mark missing models inactive. Every run creates a snapshots row, even failures.

## Conventions
- Parser finds table columns by header text, not CSS classes (resilient to restyling)
- Each field parse is wrapped in try/except — one broken field never kills the row
- New models are auto-detected on insert; name changes are handled manually via merge script
- raw_model_name is always preserved in rankings for traceability
- Use logging module, not print

## Watch out for
- Tests use a saved HTML fixture (tests/fixtures/), not live fetches
- .env must have SUPABASE_URL, SUPABASE_KEY, and RESEND_API_KEY (see .env.example)
- GitHub Actions secrets: SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY
- Streamlit Cloud needs SUPABASE_URL and SUPABASE_KEY in its secrets UI
- Resend free tier: 100 emails/day. alert_from_email defaults to onboarding@resend.dev (verify your own domain later)
- Arena.ai is SSR but the HTML structure can change — if model count drops >50%, check parser.py
- Supabase python client quirks: use .limit(1) instead of .maybe_single()