"""Matching scraped names to existing models after Arena relabels them."""
from __future__ import annotations

from src.models import ScrapedModel
from src.renames import PrevRanking, match_renamed_models, normalize_name


def _live(name: str, score: float = 1400.0, votes: int = 10_000, rank: int = 1):
    return ScrapedModel(rank=rank, model_name=name, score=score, votes=votes)


def _match(live, known, prev=(), aliases=None):
    return match_renamed_models(live, known, aliases or {}, list(prev))


class TestNormalizeName:
    def test_slug_and_display_name_agree(self):
        assert normalize_name("claude-opus-5.5-high") == normalize_name("Claude Opus 5.5 (High)")


class TestMatchByName:
    def test_display_name_matches_slug(self):
        matches, unmatched = _match(
            [_live("Claude Opus 5.5 (High)")], {"claude-opus-5.5-high": "id-1"}
        )
        assert [(m.scraped_name, m.model_id, m.method) for m in matches] == [
            ("Claude Opus 5.5 (High)", "id-1", "name")
        ]
        assert unmatched == []

    def test_exact_names_are_left_alone(self):
        matches, unmatched = _match([_live("gpt-5")], {"gpt-5": "id-1"})
        assert matches == [] and unmatched == []

    def test_known_alias_is_left_alone(self):
        matches, unmatched = _match(
            [_live("GPT 5")], {"gpt-5": "id-1"}, aliases={"GPT 5": "id-1"}
        )
        assert matches == [] and unmatched == []

    def test_ambiguous_name_falls_back_to_stats(self):
        """'glm-5.2-max' and 'glm-5.2 (max)' both normalise to the same key."""
        known = {"glm-5.2-max": "id-a", "glm-5.2 (max)": "id-b"}
        prev = [
            PrevRanking("id-a", "glm-5.2-max", 1440.0, 20_000),
            PrevRanking("id-b", "glm-5.2 (max)", 1300.0, 500),
        ]
        matches, _ = _match([_live("GLM-5.2 (Max)", score=1442.0, votes=20_500)], known, prev)
        assert [(m.model_id, m.method) for m in matches] == [("id-a", "stats")]

    def test_one_model_is_not_claimed_twice(self):
        known = {"command-r": "id-1"}
        matches, unmatched = _match(
            [_live("Command R", rank=1), _live("Command-R", rank=2)], known
        )
        assert len(matches) == 1
        assert [m.model_name for m in unmatched] == ["Command-R"]


class TestMatchByStats:
    def test_matches_when_names_differ_beyond_punctuation(self):
        prev = [PrevRanking("id-1", "mistral-medium-2508", 1424.0, 95_000)]
        matches, _ = _match(
            [_live("Mistral Medium 3.1 (2508)", score=1424.0, votes=95_285)],
            {"mistral-medium-2508": "id-1"},
            prev,
        )
        assert [(m.model_id, m.method) for m in matches] == [("id-1", "stats")]

    def test_brand_new_model_stays_unmatched(self):
        prev = [PrevRanking("id-1", "claude-opus-5-high", 1503.0, 62_000)]
        matches, unmatched = _match(
            [_live("Muse Wonder (Max)", score=1501.0, votes=6_001)],
            {"claude-opus-5-high": "id-1"},
            prev,
        )
        assert matches == []
        assert [m.model_name for m in unmatched] == ["Muse Wonder (Max)"]

    def test_votes_that_shrank_rule_out_a_candidate(self):
        prev = [PrevRanking("id-1", "mistral-medium-2508", 1424.0, 95_000)]
        _, unmatched = _match(
            [_live("Mistral Medium 3.1 (2508)", score=1424.0, votes=1_000)],
            {"mistral-medium-2508": "id-1"},
            prev,
        )
        assert len(unmatched) == 1

    def test_two_equally_plausible_candidates_are_not_guessed(self):
        prev = [
            PrevRanking("id-1", "model-alpha-x", 1400.0, 10_000),
            PrevRanking("id-2", "model-alpha-y", 1401.0, 10_000),
        ]
        matches, unmatched = _match(
            [_live("Model Alpha Z", score=1400.0, votes=10_100)],
            {"model-alpha-x": "id-1", "model-alpha-y": "id-2"},
            prev,
        )
        assert matches == []
        assert len(unmatched) == 1
