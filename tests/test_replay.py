"""Replaying saved scrapes without disturbing newer data."""
from __future__ import annotations

import pytest

from src import replay
from src.db import touch_seen_models
from tests.test_raw_scrapes import _result


class RecordingClient:
    """Records each update's payload and filters."""

    def __init__(self):
        self.updates: list = []

    def table(self, name):
        self._current = {"table": name, "filters": []}
        return self

    def update(self, values):
        self._current["values"] = values
        return self

    def _filter(self, op):
        def add(*args):
            self._current["filters"].append((op, *args))
            return self
        return add

    def __getattr__(self, name):
        if name in {"in_", "lt", "gt"}:
            return self._filter(name)
        raise AttributeError(name)

    def execute(self):
        self.updates.append(self._current)


class TestTouchSeenModels:
    def test_live_scrape_overwrites_last_seen(self):
        client = RecordingClient()
        touch_seen_models(client, ["a", "b"], "T", replay=False)
        assert [u["values"] for u in client.updates] == [{"last_seen_at": "T"}]
        assert not any(op in {"lt", "gt"} for u in client.updates for op, *_ in u["filters"])

    def test_replay_only_widens_the_seen_window(self):
        client = RecordingClient()
        touch_seen_models(client, ["a", "b"], "T", replay=True)
        by_column = {next(iter(u["values"])): u["filters"] for u in client.updates}
        assert ("lt", "last_seen_at", "T") in by_column["last_seen_at"]
        assert ("gt", "first_seen_at", "T") in by_column["first_seen_at"]


@pytest.fixture
def stubbed(monkeypatch):
    saved = [
        {"id": "1", "category": "overall", "scraped_at": "2026-10-20T12:00:00+00:00",
         "error_message": "e", "payload": _result().model_dump(mode="json")},
        {"id": "2", "category": "overall", "scraped_at": "2026-10-21T12:00:00+00:00",
         "error_message": "e", "payload": _result(1401.0).model_dump(mode="json")},
    ]
    stored, marked = [], []
    monkeypatch.setattr(replay, "get_client", lambda: object())
    monkeypatch.setattr(replay, "pending_raw_scrapes", lambda client: saved)
    monkeypatch.setattr(replay, "load_caches", lambda client: ({}, {}))
    monkeypatch.setattr(replay, "mark_replayed", lambda client, id_: marked.append(id_))
    return stored, marked, monkeypatch


class TestReplayPending:
    def test_dry_run_stores_nothing(self, stubbed):
        stored, marked, monkeypatch = stubbed
        monkeypatch.setattr(replay, "store_results", lambda r, replay=False: stored.append(r))
        assert replay.replay_pending(apply=False) == 0
        assert stored == [] and marked == []

    def test_apply_replays_oldest_first_as_replays(self, stubbed):
        stored, marked, monkeypatch = stubbed
        flags = []

        def fake_store(result, replay=False):
            stored.append(result.scraped_at)
            flags.append(replay)
            return 1

        monkeypatch.setattr(replay, "store_results", fake_store)
        assert replay.replay_pending(apply=True) == 0
        assert stored == sorted(stored) and flags == [True, True]
        assert marked == ["1", "2"]

    def test_failure_stays_pending_and_does_not_stop_the_rest(self, stubbed):
        stored, marked, monkeypatch = stubbed

        def fake_store(result, replay=False):
            if result.models[0].score == 1400.0:
                raise RuntimeError("still too many new models")
            return 1

        monkeypatch.setattr(replay, "store_results", fake_store)
        assert replay.replay_pending(apply=True) == 1
        assert marked == ["2"]
