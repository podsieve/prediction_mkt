#!/usr/bin/env bash
# Run the scrape from this Mac on a launchd timer instead of GitHub's scheduler,
# which skips or delays cron runs for hours at a time.
#
#   scripts/scraper_timer.sh install   # schedule every 2 hours and load it
#   scripts/scraper_timer.sh run       # run one cycle now
#   scripts/scraper_timer.sh status    # is it loaded, and the last log lines
#   scripts/scraper_timer.sh remove    # unload and delete the timer
#   scripts/scraper_timer.sh print     # show the launchd plist without installing
#
# Needs a .env in the repo root (see .env.example). It is a LaunchAgent, so it
# runs while this user is logged in; keep the Mac awake and set to log in
# automatically after a restart.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.voralabs.arena-scraper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/arena-scraper.log"
MINUTE=17  # off the hour, where everyone else's jobs fire
TARGET="gui/$(id -u)/$LABEL"

plist() {
  cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$REPO_DIR/scripts/run_scrape.sh</string></array>
  <key>WorkingDirectory</key><string>$REPO_DIR</string>
  <key>StartCalendarInterval</key>
  <array>
$(for h in 0 2 4 6 8 10 12 14 16 18 20 22; do
    echo "    <dict><key>Hour</key><integer>$h</integer><key>Minute</key><integer>$MINUTE</integer></dict>"
  done)
  </array>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
}

case "${1:-}" in
  install)
    [ -f "$REPO_DIR/.env" ] || { echo "Create $REPO_DIR/.env first (see .env.example)" >&2; exit 1; }
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
    launchctl bootout "$TARGET" 2>/dev/null || true
    plist > "$PLIST"
    plutil -lint "$PLIST" >/dev/null
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "Scheduled every 2 hours at :$MINUTE. Log: $LOG"
    ;;
  run) launchctl kickstart "$TARGET" ;;
  status)
    launchctl print "$TARGET" 2>&1 | grep -E "state =|last exit code|runs =" || echo "Not loaded"
    tail -n 5 "$LOG" 2>/dev/null || true
    ;;
  remove)
    launchctl bootout "$TARGET" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Timer removed."
    ;;
  print) plist ;;
  *) echo "Usage: $0 install|run|status|remove|print" >&2; exit 1 ;;
esac
