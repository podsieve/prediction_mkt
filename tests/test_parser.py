from __future__ import annotations

import os

import pytest

from src.parser import (
    parse_int,
    parse_leaderboard,
    parse_total_votes,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
OVERALL_FIXTURE = os.path.join(FIXTURE_DIR, "sample_leaderboard.html")
CODING_FIXTURE = os.path.join(FIXTURE_DIR, "sample_coding_leaderboard.html")
MATH_FIXTURE = os.path.join(FIXTURE_DIR, "sample_math_leaderboard.html")


# --- Fixtures ---

@pytest.fixture
def sample_html():
    with open(OVERALL_FIXTURE, "r") as f:
        return f.read()


@pytest.fixture
def coding_html():
    with open(CODING_FIXTURE, "r") as f:
        return f.read()


@pytest.fixture
def scrape_result(sample_html):
    return parse_leaderboard(
        sample_html, "https://arena.ai/leaderboard/text/overall-no-style-control"
    )


@pytest.fixture
def coding_result(coding_html):
    return parse_leaderboard(
        coding_html,
        "https://arena.ai/leaderboard/text/coding-no-style-control",
        category="coding",
    )


@pytest.fixture
def math_html():
    with open(MATH_FIXTURE, "r") as f:
        return f.read()


@pytest.fixture
def math_result(math_html):
    return parse_leaderboard(
        math_html,
        "https://arena.ai/leaderboard/text/math-no-style-control",
        category="math",
    )


# --- parse_int ---

class TestParseInt:
    def test_plain_number(self):
        assert parse_int("42") == 42

    def test_with_commas(self):
        assert parse_int("23,616") == 23616

    def test_with_whitespace(self):
        assert parse_int("  1,234  ") == 1234


# --- Overall leaderboard ---

class TestParseLeaderboard:
    def test_parses_all_models(self, scrape_result):
        assert scrape_result.total_models == 357

    def test_total_votes(self, scrape_result):
        assert scrape_result.total_votes == 6110156

    def test_source_url(self, scrape_result):
        assert scrape_result.source_url == "https://arena.ai/leaderboard/text/overall-no-style-control"

    def test_default_category(self, scrape_result):
        assert scrape_result.category == "overall"

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


# --- Coding leaderboard ---

class TestParseCodingLeaderboard:
    def test_category_is_coding(self, coding_result):
        assert coding_result.category == "coding"

    def test_source_url(self, coding_result):
        assert "coding" in coding_result.source_url

    def test_parses_models(self, coding_result):
        assert coding_result.total_models > 100

    def test_total_votes(self, coding_result):
        assert coding_result.total_votes is not None
        assert coding_result.total_votes > 0

    def test_first_model_rank(self, coding_result):
        assert coding_result.models[0].rank == 1

    def test_first_model_has_score(self, coding_result):
        assert coding_result.models[0].score > 1000

    def test_first_model_has_votes(self, coding_result):
        assert coding_result.models[0].votes > 0

    def test_ranks_are_sequential(self, coding_result):
        ranks = [m.rank for m in coding_result.models]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_all_scores_positive(self, coding_result):
        for m in coding_result.models:
            assert m.score > 0

    def test_last_model_lower_score(self, coding_result):
        assert coding_result.models[-1].score < coding_result.models[0].score

    def test_has_html_hash(self, coding_result):
        assert len(coding_result.raw_html_hash) == 64

    def test_organization_parsed_for_top(self, coding_result):
        """At least some top models should have an organization."""
        top_10 = coding_result.models[:10]
        orgs = [m.organization for m in top_10 if m.organization]
        assert len(orgs) > 0


# --- Math leaderboard ---

class TestParseMathLeaderboard:
    def test_category_is_math(self, math_result):
        assert math_result.category == "math"

    def test_source_url(self, math_result):
        assert "math" in math_result.source_url

    def test_parses_models(self, math_result):
        assert math_result.total_models > 100

    def test_total_votes(self, math_result):
        assert math_result.total_votes is not None
        assert math_result.total_votes > 0

    def test_first_model_rank(self, math_result):
        assert math_result.models[0].rank == 1

    def test_first_model_has_score(self, math_result):
        assert math_result.models[0].score > 1000

    def test_first_model_has_votes(self, math_result):
        assert math_result.models[0].votes > 0

    def test_ranks_are_sequential(self, math_result):
        ranks = [m.rank for m in math_result.models]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_all_scores_positive(self, math_result):
        for m in math_result.models:
            assert m.score > 0

    def test_last_model_lower_score(self, math_result):
        assert math_result.models[-1].score < math_result.models[0].score

    def test_has_html_hash(self, math_result):
        assert len(math_result.raw_html_hash) == 64

    def test_organization_parsed_for_top(self, math_result):
        """At least some top models should have an organization."""
        top_10 = math_result.models[:10]
        orgs = [m.organization for m in top_10 if m.organization]
        assert len(orgs) > 0


# --- Shared model identity ---

class TestCrossCategory:
    def test_shared_models_exist(self, scrape_result, coding_result):
        """Some models should appear in both overall and coding leaderboards."""
        overall_names = {m.model_name for m in scrape_result.models}
        coding_names = {m.model_name for m in coding_result.models}
        overlap = overall_names & coding_names
        assert len(overlap) > 10, "Expected significant overlap between overall and coding models"

    def test_math_overlaps_overall(self, scrape_result, math_result):
        """Some models should appear in both overall and math leaderboards."""
        overall_names = {m.model_name for m in scrape_result.models}
        math_names = {m.model_name for m in math_result.models}
        overlap = overall_names & math_names
        assert len(overlap) > 10, "Expected significant overlap between overall and math models"

    def test_categories_differ(self, scrape_result, coding_result):
        assert scrape_result.category != coding_result.category

    def test_math_category_differs(self, scrape_result, math_result):
        assert scrape_result.category != math_result.category

    def test_different_vote_counts(self, scrape_result, coding_result):
        """Coding and overall should have different total vote counts."""
        assert scrape_result.total_votes != coding_result.total_votes


# --- parse_total_votes ---

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

    def test_coding_total_votes(self, coding_html):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(coding_html, "html.parser")
        votes = parse_total_votes(soup)
        assert votes is not None
        assert votes > 0
