from __future__ import annotations

import logging
from typing import List

import resend

from src.config import settings
from alerts.checker import AlertEvent

logger = logging.getLogger(__name__)

ICON = {
    "new_model": "&#127381;",
    "rank_change": "&#128200;",
    "score_anomaly": "&#9888;&#65039;",
}


def _build_event_html(events: List[AlertEvent]) -> str:
    grouped = {}
    for e in events:
        grouped.setdefault(e.event_type, []).append(e)

    section_titles = {
        "new_model": "New Models",
        "rank_change": "Rank Changes",
        "score_anomaly": "Score Anomalies",
    }

    sections = []
    for etype, title in section_titles.items():
        items = grouped.get(etype, [])
        if not items:
            continue
        icon = ICON.get(etype, "")
        rows = "".join(f"<li>{e.summary}</li>" for e in items)
        sections.append(f"<h3>{icon} {title} ({len(items)})</h3><ul>{rows}</ul>")

    return "\n".join(sections)


def _build_html(body_content: str, subject: str, dashboard_url: str = "") -> str:
    dashboard_link = ""
    if dashboard_url:
        dashboard_link = f'<p><a href="{dashboard_url}">Open Dashboard</a></p>'

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
<h2>{subject}</h2>
{body_content}
{dashboard_link}
<hr style="margin-top: 30px; border: none; border-top: 1px solid #ddd;">
<p style="color: #999; font-size: 12px;">Arena.ai Leaderboard Tracker</p>
</body>
</html>"""


def send_alert_email(events: List[AlertEvent]):
    if not events:
        logger.info("No alert events, skipping email")
        return

    resend.api_key = settings.resend_api_key
    body = _build_event_html(events)
    subject = f"Arena Leaderboard: {len(events)} alert{'s' if len(events) != 1 else ''}"
    html = _build_html(body, subject, dashboard_url=settings.dashboard_url)

    resend.Emails.send({
        "from": settings.alert_from_email,
        "to": [settings.alert_recipient],
        "subject": subject,
        "html": html,
    })
    logger.info("Sent alert email with %d events to %s", len(events), settings.alert_recipient)


def send_digest_email(digest_html: str):
    resend.api_key = settings.resend_api_key
    subject = "Arena Leaderboard — Daily Digest"
    html = _build_html(digest_html, subject, dashboard_url=settings.dashboard_url)

    resend.Emails.send({
        "from": settings.alert_from_email,
        "to": [settings.alert_recipient],
        "subject": subject,
        "html": html,
    })
    logger.info("Sent daily digest to %s", settings.alert_recipient)
