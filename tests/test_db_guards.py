"""Guards that stop a bad parse from being written to the database."""
from __future__ import annotations

import pytest

from src.config import settings
from src.db import SuspiciousScrapeError, check_new_model_surge


def _models(n: int):
    return [{"canonical_name": f"model-{i}"} for i in range(n)]


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
