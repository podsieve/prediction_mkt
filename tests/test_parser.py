from __future__ import annotations

import os

import pytest

from src.parser import (
    parse_int,
    parse_leaderboard,
    parse_total_votes,
)

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "sample_leaderboard.html"
)


@pytest.fixture
def sample_html():
    with open(FIXTURE_PATH, "r") as f:
        return f.read()


@pytest.fixture
def scrape_result(sample_html):
    return parse_leaderboard(
        sample_html, "https://arena.ai/leaderboard/text/overall-no-style-control"
    )


class TestParseInt:
    def test_plain_number(self):
        assert parse_int("42") == 42

    def test_with_commas(self):
        assert parse_int("23,616") == 23616

    def test_with_whitespace(self):
        assert parse_int("  1,234  ") == 1234


class TestParseLeaderboard:
    def test_parses_all_models(self, scrape_result):
        assert scrape_result.total_models == 357

    def test_total_votes(self, scrape_result):
        assert scrape_result.total_votes == 6110156

    def test_source_url(self, scrape_result):
        assert scrape_result.source_url == "https://arena.ai/leaderboard/text/overall-no-style-control"

    def test_has_html_hash(self, scrape_result):
        assert len(scrape_result.raw_html_hash) == 64

    def test_first_model_rank(self, scrape_result):
        first = scrape_result.models[0]
        assert first.rank == 1

    def test_first_model_name(self, scrape_result):
        first = scrape_result.models[0]
        assert "claude-opus" in first.model_name.lower()

    def test_first_model_score(self, scrape_result):
        first = scrape_result.models[0]
        assert first.score > 1400

    def test_first_model_ci(self, scrape_result):
        first = scrape_result.models[0]
        assert first.score_ci is not None
        assert first.score_ci > 0

    def test_first_model_votes(self, scrape_result):
        first = scrape_result.models[0]
        assert first.votes > 1000

    def test_rank_spread_parsed(self, scrape_result):
        first = scrape_result.models[0]
        assert first.rank_upper is not None
        assert first.rank_lower is not None
        assert first.rank_upper <= first.rank_lower

    def test_organization_parsed(self, scrape_result):
        first = scrape_result.models[0]
        assert first.organization is not None

    def test_license_type_parsed(self, scrape_result):
        first = scrape_result.models[0]
        assert first.license_type in ("Proprietary", "Open Source", None)

    def test_last_model_has_lowest_score(self, scrape_result):
        last = scrape_result.models[-1]
        first = scrape_result.models[0]
        assert last.score < first.score

    def test_ranks_are_sequential(self, scrape_result):
        ranks = [m.rank for m in scrape_result.models]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_all_scores_positive(self, scrape_result):
        for m in scrape_result.models:
            assert m.score > 0

    def test_all_votes_non_negative(self, scrape_result):
        for m in scrape_result.models:
            assert m.votes >= 0

    def test_scrape_duration_recorded(self, scrape_result):
        assert scrape_result.scrape_duration_ms >= 0


class TestParseTotalVotes:
    def test_from_html(self, sample_html):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(sample_html, "html.parser")
        votes = parse_total_votes(soup)
        assert votes == 6110156

    def test_returns_none_for_empty(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<html></html>", "html.parser")
        votes = parse_total_votes(soup)
        assert votes is None
