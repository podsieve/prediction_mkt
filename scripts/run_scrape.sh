#!/usr/bin/env bash
# One scrape cycle, run on a schedule by launchd (see scripts/scraper_timer.sh).
# Mirrors .github/workflows/scrape.yml: scrape, alert checks if it worked,
# health check either way.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

if [ ! -f .env ]; then
  log "Missing $REPO_DIR/.env (copy .env.example and fill it in)"
  exit 1
fi

# Pick up merged fixes. A failed pull (offline, local edits) must not stop the scrape.
git pull --ff-only --quiet || log "git pull failed, running the current checkout"

PY="$(command -v python3.13 || command -v python3.12 || command -v python3.11 || true)"
[ -n "$PY" ] || { log "Python 3.11+ not found (brew install python@3.13)"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv

wanted="$(shasum requirements.txt | cut -d' ' -f1)"
if [ "$(cat .venv/.requirements.sha 2>/dev/null)" != "$wanted" ]; then
  log "Installing requirements"
  .venv/bin/pip install --quiet -r requirements.txt && echo "$wanted" > .venv/.requirements.sha
fi

status=0
.venv/bin/python -m src.scraper || status=$?
if [ "$status" -eq 0 ]; then
  .venv/bin/python -m alerts.run events || log "alert check failed"
fi
.venv/bin/python -m alerts.run health || log "health check failed"

log "cycle finished, scrape exit status $status"
exit "$status"
