"""Tell the owner when the scraper stops storing data.

The scrape workflow can fail for days without anyone looking at Actions. Two
things are flagged, per category, and each is emailed rarely enough not to nag:

- newly failing: the latest snapshot failed but the one before it succeeded.
  This fires once, on the first failure.
- stale: no successful snapshot for STALE_AFTER_HOURS. Scrapes run every two
  hours, so that is three missed runs. It is emailed when it first crosses the
  threshold, then once a day. `force` emails whenever stale, for a separate
  daily workflow that catches the scrape workflow itself not running.
"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from pydantic import TypeAdapter

from src.config import settings

logger = logging.getLogger(__name__)

STALE_AFTER_HOURS = 6
_parse_datetime = TypeAdapter(datetime).validate_python


@dataclass
class HealthIssue:
    category: str
    kind: str  # "failing" or "stale"
    message: str


def stale_alert_due(age_hours: float) -> bool:
    """True for the run that first sees staleness, then for one run a day."""
    if age_hours < STALE_AFTER_HOURS:
        return False
    if age_hours < STALE_AFTER_HOURS + 2:
        return True
    return age_hours >= 24 and age_hours % 24 < 2


def _short_error(snapshot: Optional[dict]) -> str:
    error = (snapshot or {}).get("error_message") or "no error recorded"
    return html.escape(error[:300])


def evaluate_health(
    state: Dict[str, dict],
    categories: Iterable[str],
    now: datetime,
    force: bool = False,
) -> List[HealthIssue]:
    """Turn each category's recent snapshots into the issues worth emailing.

    `state[category]` holds `latest` (newest snapshots first) and `last_success`
    (the newest successful one, or None).
    """
    issues: List[HealthIssue] = []
    for category in categories:
        entry = state.get(category) or {}
        latest = entry.get("latest") or []
        last_success = entry.get("last_success")

        newly_failing = (
            len(latest) >= 2
            and latest[0]["status"] != "success"
            and latest[1]["status"] == "success"
        )
        if newly_failing:
            issues.append(HealthIssue(
                category, "failing",
                f"Latest scrape failed: {_short_error(latest[0])}",
            ))
            continue

        if last_success is None:
            issues.append(HealthIssue(category, "stale", "No successful scrape on record."))
            continue

        age_hours = (now - _parse_datetime(last_success["scraped_at"])).total_seconds() / 3600
        if age_hours >= STALE_AFTER_HOURS and (force or stale_alert_due(age_hours)):
            detail = ""
            if latest and latest[0]["status"] != "success":
                detail = f" Latest attempt failed: {_short_error(latest[0])}"
            issues.append(HealthIssue(
                category, "stale",
                f"No successful scrape for {age_hours:.0f}h "
                f"(last: {last_success['scraped_at'][:16].replace('T', ' ')} UTC).{detail}",
            ))
    return issues


def fetch_health_state(client, categories: Iterable[str]) -> Dict[str, dict]:
    state: Dict[str, dict] = {}
    for category in categories:
        latest = (
            client.table("snapshots")
            .select("status, scraped_at, error_message")
            .eq("category", category)
            .order("scraped_at", desc=True)
            .limit(2)
            .execute()
        )
        last_success = (
            client.table("snapshots")
            .select("status, scraped_at")
            .eq("category", category)
            .eq("status", "success")
            .order("scraped_at", desc=True)
            .limit(1)
            .execute()
        )
        state[category] = {
            "latest": latest.data or [],
            "last_success": (last_success.data or [None])[0],
        }
    return state


def run_health_check(force: bool = False) -> List[HealthIssue]:
    from alerts.checker import get_client
    from alerts.emailer import send_health_email

    categories = list(settings.scrape_categories)
    state = fetch_health_state(get_client(), categories)
    issues = evaluate_health(state, categories, datetime.now(timezone.utc), force=force)
    if issues:
        send_health_email(issues)
    else:
        logger.info("Scraper health OK")
    return issues
