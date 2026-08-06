"""Guards that stop a bad parse from being written to the database."""
from __future__ import annotations

import pytest

from src.config import settings
from src.db import SuspiciousScrapeError, check_new_model_surge, dedupe_scraped_models
from src.models import ScrapedModel


def _models(n: int):
    return [{"canonical_name": f"model-{i}"} for i in range(n)]


def _scraped(name: str, rank: int, score: float = 1400.0):
    return ScrapedModel(rank=rank, model_name=name, score=score, votes=1000)


class TestNewModelSurge:
    def test_allows_a_normal_launch_day(self):
        """Arena's busiest real day added 8 new models."""
        check_new_model_surge(_models(8), known_model_count=380, category="overall")

    def test_allows_exactly_the_limit(self):
        check_new_model_surge(
            _models(settings.max_new_models_per_scrape),
            known_model_count=380,
            category="overall",
        )

    def test_blocks_a_parser_regression(self):
        """The July 2026 regression tried to create 369 models in one run."""
        with pytest.raises(SuspiciousScrapeError, match="369 new models"):
            check_new_model_surge(_models(369), known_model_count=380, category="overall")

    def test_bootstrap_is_exempt(self):
        """First ever scrape legitimately creates the whole board."""
        check_new_model_surge(_models(357), known_model_count=0, category="overall")

    def test_no_new_models_is_fine(self):
        check_new_model_surge([], known_model_count=380, category="overall")

    def test_error_names_the_category_and_a_sample(self):
        with pytest.raises(SuspiciousScrapeError) as exc:
            check_new_model_surge(_models(100), known_model_count=380, category="math")
        message = str(exc.value)
        assert "[math]" in message
        assert "model-0" in message
        assert "src/parser.py" in message


class TestDedupeScrapedModels:
    def test_leaves_a_clean_board_alone(self):
        models = [_scraped(f"model-{i}", i + 1) for i in range(5)]
        assert dedupe_scraped_models(models, "overall") == models

    def test_keeps_the_best_ranked_of_a_repeat_listing(self):
        """Arena listed gemini-3.5-flash-high at both rank 9 and rank 11."""
        models = [
            _scraped("a", 8),
            _scraped("gemini-3.5-flash-high", 9, score=1480),
            _scraped("b", 10),
            _scraped("gemini-3.5-flash-high", 11, score=1479),
        ]
        result = dedupe_scraped_models(models, "overall")
        assert [m.model_name for m in result] == ["a", "gemini-3.5-flash-high", "b"]
        assert result[1].rank == 9
        assert result[1].score == 1480

    def test_collapses_more_than_two_copies(self):
        models = [_scraped("dup", i) for i in range(1, 5)]
        result = dedupe_scraped_models(models, "coding")
        assert len(result) == 1
        assert result[0].rank == 1

    def test_warns_naming_the_dropped_rows(self, caplog):
        models = [_scraped("dup", 1), _scraped("dup", 7)]
        with caplog.at_level("WARNING"):
            dedupe_scraped_models(models, "math")
        assert "[math]" in caplog.text
        assert "dup (rank 7)" in caplog.text

    def test_empty_board(self):
        assert dedupe_scraped_models([], "overall") == []
