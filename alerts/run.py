from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_event_alerts():
    from alerts.checker import run_all_checks
    from alerts.emailer import send_alert_email

    events = run_all_checks()
    if events:
        send_alert_email(events)
    else:
        logger.info("No alert-worthy events detected")


def run_daily_digest():
    from alerts.digest import build_digest
    from alerts.emailer import send_digest_email

    html = build_digest()
    send_digest_email(html)
    logger.info("Daily digest sent")


def run_health(force: bool):
    from alerts.health import run_health_check

    run_health_check(force=force)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arena leaderboard alerts")
    parser.add_argument(
        "mode",
        choices=["events", "digest", "health"],
        help="'events' for post-scrape alerts, 'digest' for daily summary, "
        "'health' for scraper failure/staleness alerts",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="health only: email whenever data is stale, not just at the first "
        "detection and daily after",
    )
    args = parser.parse_args()

    try:
        if args.mode == "events":
            run_event_alerts()
        elif args.mode == "health":
            run_health(args.force)
        else:
            run_daily_digest()
    except Exception as e:
        logger.error("Alert run failed: %s", e)
        sys.exit(1)
