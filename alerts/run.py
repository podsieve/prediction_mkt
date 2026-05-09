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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arena leaderboard alerts")
    parser.add_argument(
        "mode",
        choices=["events", "digest"],
        help="'events' for post-scrape alerts, 'digest' for daily summary",
    )
    args = parser.parse_args()

    try:
        if args.mode == "events":
            run_event_alerts()
        else:
            run_daily_digest()
    except Exception as e:
        logger.error("Alert run failed: %s", e)
        sys.exit(1)
