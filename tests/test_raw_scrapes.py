"""Saving refused scrapes so they can be replayed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.models import ScrapedModel, ScrapeResult
from src.raw_scrapes import content_hash, save_raw_scrape


def _result(score: float = 1400.0, at: datetime | None = None) -> ScrapeResult:
    return ScrapeResult(
        scraped_at=at or datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc),
        source_url="https://arena.example/overall",
        category="overall",
        total_models=1,
        models=[ScrapedModel(rank=1, model_name="Model A", score=score, votes=100)],
        raw_html_hash="abc",
        scrape_duration_ms=5,
    )


class FakeClient:
    """Records the last upsert; set `fail` to make it raise."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list = []

    def table(self, name):
        self.calls.append(("table", name))
        return self

    def upsert(self, row, **kwargs):
        self.calls.append(("upsert", row, kwargs))
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError('relation "raw_scrapes" does not exist')


class TestContentHash:
    def test_same_content_hashes_the_same_whenever_fetched(self):
        later = _result(at=datetime(2026, 10, 21, tzinfo=timezone.utc))
        assert content_hash(_result()) == content_hash(later)

    def test_changed_scores_hash_differently(self):
        assert content_hash(_result(1400.0)) != content_hash(_result(1401.0))


class TestSaveRawScrape:
    def test_saves_payload_that_round_trips(self):
        client = FakeClient()
        assert save_raw_scrape(client, _result(), "refused") is True
        _, row, kwargs = next(c for c in client.calls if c[0] == "upsert")
        assert row["category"] == "overall" and row["error_message"] == "refused"
        assert ScrapeResult.model_validate(row["payload"]) == _result()
        assert kwargs == {"on_conflict": "category,content_hash", "ignore_duplicates": True}

    def test_failure_to_save_never_raises(self):
        """It runs on a failure path; it must not mask the original error."""
        assert save_raw_scrape(FakeClient(fail=True), _result(), "refused") is False
