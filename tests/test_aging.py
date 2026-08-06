"""Debut-aligned curve maths — the pure parts, no database."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from analysis.aging import build_curves, day_grid, median_curve, parse_ts, step_interpolate

T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _snapshots(n: int, hours: int = 24):
    return {f"s{i}": T0 + timedelta(hours=i * hours) for i in range(n)}


def _ranking(model_id: str, snap: str, rank: int, score: float, votes: int = 100):
    return {"model_id": model_id, "snapshot_id": snap, "rank": rank,
            "score": score, "score_ci": 5, "votes": votes}


class TestParseTs:
    def test_handles_five_digit_microseconds(self):
        """Postgres emits these; datetime.fromisoformat rejects them on py<3.11."""
        assert parse_ts("2026-07-11T13:06:55.37163+00:00").year == 2026

    def test_handles_z_suffix(self):
        assert parse_ts("2026-07-11T13:06:55Z").tzinfo is not None


class TestBuildCurves:
    def test_aligns_on_each_models_own_debut(self):
        snaps = _snapshots(4)
        # "late" first appears at snapshot 2, so its day 0 is two days after "early"'s.
        rankings = [
            _ranking("early", "s0", 10, 1400), _ranking("early", "s1", 12, 1402),
            _ranking("early", "s2", 14, 1401), _ranking("early", "s3", 15, 1401),
            _ranking("late", "s2", 50, 1300), _ranking("late", "s3", 55, 1290),
        ]
        models = {"early": {"canonical_name": "early"}, "late": {"canonical_name": "late"}}
        curves = {c["model_name"]: c for c in build_curves(rankings, snaps, models)}

        assert [p["days"] for p in curves["early"]["points"]] == [0, 1, 2, 3]
        assert [p["days"] for p in curves["late"]["points"]] == [0, 1]
        assert curves["late"]["debut_rank"] == 50

    def test_deltas_are_relative_to_debut(self):
        snaps = _snapshots(3)
        rankings = [_ranking("m", "s0", 10, 1400), _ranking("m", "s1", 14, 1395),
                    _ranking("m", "s2", 9, 1410)]
        curve = build_curves(rankings, snaps, {"m": {"canonical_name": "m"}})[0]
        assert [p["rank_delta"] for p in curve["points"]] == [0, 4, -1]
        assert [p["score_delta"] for p in curve["points"]] == [0, -5, 10]

    def test_ignores_rankings_from_other_categories(self):
        """fetch_rankings_for returns every category; the snapshot map is the filter."""
        snaps = _snapshots(2)
        rankings = [_ranking("m", "s0", 10, 1400), _ranking("m", "other-cat", 3, 1500)]
        curve = build_curves(rankings, snaps, {"m": {"canonical_name": "m"}})[0]
        assert len(curve["points"]) == 1

    def test_orders_observations_by_time_not_arrival(self):
        snaps = _snapshots(3)
        rankings = [_ranking("m", "s2", 9, 1410), _ranking("m", "s0", 10, 1400),
                    _ranking("m", "s1", 14, 1395)]
        curve = build_curves(rankings, snaps, {"m": {"canonical_name": "m"}})[0]
        assert curve["debut_rank"] == 10
        assert [p["days"] for p in curve["points"]] == [0, 1, 2]

    def test_no_rankings_yields_no_curves(self):
        assert build_curves([], _snapshots(3), {}) == []


class TestStepInterpolate:
    def test_carries_the_last_value_forward(self):
        """Scores hold whatever Arena last published until the next recompute."""
        points = [{"days": 0, "v": 10}, {"days": 3, "v": 20}]
        assert step_interpolate(points, "v", [0, 1, 2, 3]) == [10, 10, 10, 20]

    def test_does_not_extrapolate_past_the_last_observation(self):
        points = [{"days": 0, "v": 10}, {"days": 2, "v": 20}]
        assert step_interpolate(points, "v", [0, 1, 2, 3, 4]) == [10, 10, 20, None, None]

    def test_grid_point_before_debut_is_empty(self):
        points = [{"days": 1, "v": 10}]
        assert step_interpolate(points, "v", [0, 1]) == [None, 10]


class TestMedianCurve:
    def _curve(self, values):
        return {"points": [{"days": d, "rank_delta": v} for d, v in enumerate(values)]}

    def test_median_and_quartiles(self):
        curves = [self._curve([0, 10]), self._curve([0, 20]), self._curve([0, 30])]
        out = median_curve(curves, "rank_delta", [0, 1])
        assert out["median"] == [0, 20]
        assert out["q1"][1] == 10 and out["q3"][1] == 30

    def test_thin_grid_points_are_left_empty(self):
        """Two survivors shouldn't be drawn as if they were the whole cohort."""
        curves = [self._curve([0, 1, 2]), self._curve([0, 1]), self._curve([0, 1])]
        out = median_curve(curves, "rank_delta", [0, 1, 2], min_models=3)
        assert out["median"][2] is None
        assert out["n"][2] == 1

    def test_counts_contributing_models(self):
        curves = [self._curve([0, 1, 2]), self._curve([0, 1])]
        out = median_curve(curves, "rank_delta", [0, 1, 2], min_models=1)
        assert out["n"] == [2, 2, 1]


class TestDayGrid:
    @pytest.mark.parametrize("horizon,step,expected_last", [(10, 0.5, 10.0), (3, 1.0, 3.0)])
    def test_covers_the_horizon(self, horizon, step, expected_last):
        grid = day_grid(horizon, step)
        assert grid[0] == 0
        assert grid[-1] == pytest.approx(expected_last)
