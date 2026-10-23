"""When the scraper health check emails, and when it stays quiet."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alerts.health import STALE_AFTER_HOURS, evaluate_health, stale_alert_due

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def _snap(status: str, hours_ago: float, error: str | None = None) -> dict:
    return {
        "status": status,
        "scraped_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "error_message": error,
    }


def _state(*latest: dict, last_success: dict | None = "auto") -> dict:
    if last_success == "auto":
        last_success = next((s for s in latest if s["status"] == "success"), None)
    return {"overall": {"latest": list(latest), "last_success": last_success}}


def _issues(state, force=False):
    return evaluate_health(state, ["overall"], NOW, force=force)


class TestStaleAlertDue:
    def test_quiet_until_the_threshold(self):
        assert not stale_alert_due(STALE_AFTER_HOURS - 0.1)

    def test_fires_when_first_stale(self):
        assert stale_alert_due(STALE_AFTER_HOURS)
        assert stale_alert_due(STALE_AFTER_HOURS + 1.9)

    def test_quiet_between_reminders(self):
        assert not stale_alert_due(10)
        assert not stale_alert_due(20)
        assert not stale_alert_due(30)

    def test_reminds_once_a_day(self):
        assert stale_alert_due(24.5)
        assert stale_alert_due(48.0)
        assert not stale_alert_due(26.5)


class TestEvaluateHealth:
    def test_healthy_scraper_is_silent(self):
        assert _issues(_state(_snap("success", 1), _snap("success", 3))) == []

    def test_first_failure_alerts_once(self):
        issues = _issues(_state(_snap("failed", 0.1, "refusing to store"), _snap("success", 2)))
        assert [(i.category, i.kind) for i in issues] == [("overall", "failing")]
        assert "refusing to store" in issues[0].message

    def test_repeat_failure_is_silent_while_not_yet_stale(self):
        state = _state(_snap("failed", 0.1), _snap("failed", 2), last_success=_snap("success", 4))
        assert _issues(state) == []

    def test_goes_stale_after_the_threshold(self):
        state = _state(_snap("failed", 0.1, "boom"), _snap("failed", 2), last_success=_snap("success", 7))
        issues = _issues(state)
        assert [i.kind for i in issues] == ["stale"]
        assert "7h" in issues[0].message and "boom" in issues[0].message

    def test_stale_reminder_is_throttled(self):
        state = _state(_snap("failed", 0.1), _snap("failed", 2), last_success=_snap("success", 30))
        assert _issues(state) == []

    def test_force_alerts_whenever_stale(self):
        state = _state(_snap("failed", 0.1), _snap("failed", 2), last_success=_snap("success", 30))
        assert [i.kind for i in _issues(state, force=True)] == ["stale"]

    def test_force_stays_quiet_when_fresh(self):
        assert _issues(_state(_snap("success", 1)), force=True) == []

    def test_never_succeeded(self):
        issues = _issues(_state(_snap("failed", 1), _snap("failed", 3), last_success=None))
        assert [i.kind for i in issues] == ["stale"]

    def test_no_snapshots_at_all(self):
        assert [i.kind for i in _issues({})] == ["stale"]

    def test_error_text_is_html_escaped(self):
        issues = _issues(_state(_snap("failed", 0.1, "<script>"), _snap("success", 2)))
        assert "<script>" not in issues[0].message

    def test_each_category_is_judged_alone(self):
        state = {
            "overall": {"latest": [_snap("success", 1)], "last_success": _snap("success", 1)},
            "coding": {
                "latest": [_snap("failed", 0.1, "x"), _snap("success", 2)],
                "last_success": _snap("success", 2),
            },
        }
        issues = evaluate_health(state, ["overall", "coding"], NOW)
        assert [(i.category, i.kind) for i in issues] == [("coding", "failing")]
