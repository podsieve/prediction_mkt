from __future__ import annotations

import os

import pytest

from src.parser import (
    parse_int,
    parse_leaderboard,
    parse_model_cell,
    parse_total_votes,
    strip_org_suffix,
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


# --- Regression: July 2026 concatenated model names ---

def _cell(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser").find("td")


# How Arena normally renders the cell: org logo <svg><title>, an <a title=...>
# holding the name, then the "Org · License" caption.
WELL_FORMED_CELL = """
<td><div>
  <svg><title>Anthropic</title><path></path></svg>
  <a title="claude-fable-5" href="#"><span>claude-fable-5</span></a>
  <span class="text-xs">Anthropic · Proprietary</span>
</div></td>
"""

# The July 2026 shape: the <a> wrapper disappeared, so the logo title, name and
# caption ran together into one blob of cell text.
REGRESSION_CELL = """
<td><div>
  <svg><title>Anthropic</title><path></path></svg>
  <span>claude-fable-5</span>
  <span class="text-xs">Anthropic · Proprietary</span>
</div></td>
"""

# Worst case: name, logo title and caption all collapsed into one text node.
# The string here is copied verbatim from a row the old parser wrote to the
# `models` table on 2026-07-09.
FLATTENED_CELL = """
<td><div>
  <svg><title>Anthropic</title></svg>
  Anthropicclaude-fable-5Anthropic · Proprietary
  <span class="text-xs">Anthropic · Proprietary</span>
</div></td>
"""

# Only the logo title leaked in; the caption stayed in its own span, so there is
# no suffix to strip and the <svg><title> is the only thing that identifies it.
LOGO_PREFIX_ONLY_CELL = """
<td><div>
  <svg><title>Anthropic</title></svg>
  Anthropicclaude-fable-5
  <span class="text-xs">Anthropic · Proprietary</span>
</div></td>
"""

# A model with no organization — the caption is just the license.
LICENSE_ONLY_CELL = """
<td><div>yi-lightning<span class="text-xs">Proprietary</span></div></td>
"""

# A real name that legitimately starts with its own org name.
ORG_PREFIXED_NAME_CELL = """
<td><div>
  <svg><title>RWKV</title></svg>
  <a title="RWKV-4-Raven-14B" href="#"><span>RWKV-4-Raven-14B</span></a>
  <span class="text-xs">RWKV · Apache 2.0</span>
</div></td>
"""


class TestModelCellNameExtraction:
    def test_well_formed_cell(self):
        name, org, lic = parse_model_cell(_cell(WELL_FORMED_CELL))
        assert name == "claude-fable-5"
        assert org == "Anthropic"
        assert lic == "Proprietary"

    def test_missing_anchor_does_not_absorb_org_logo(self):
        """The <svg><title>Anthropic</title> must not prefix the model name."""
        name, org, lic = parse_model_cell(_cell(REGRESSION_CELL))
        assert name == "claude-fable-5"
        assert org == "Anthropic"

    def test_flattened_cell_is_repaired(self):
        name, org, lic = parse_model_cell(_cell(FLATTENED_CELL))
        assert name == "claude-fable-5"

    def test_logo_prefix_alone_is_stripped(self):
        name, org, lic = parse_model_cell(_cell(LOGO_PREFIX_ONLY_CELL))
        assert name == "claude-fable-5"

    def test_license_only_caption_is_stripped(self):
        name, org, lic = parse_model_cell(_cell(LICENSE_ONLY_CELL))
        assert name == "yi-lightning"

    def test_org_prefixed_name_is_preserved(self):
        """RWKV-4-Raven-14B starts with its org but is not concatenated."""
        name, org, lic = parse_model_cell(_cell(ORG_PREFIXED_NAME_CELL))
        assert name == "RWKV-4-Raven-14B"

    def test_no_parsed_name_keeps_the_org_caption(self):
        """If the caption survives in the name, the row must fail loudly."""
        cell = _cell(
            '<td><div>Anthropic · Proprietary claude-fable-5'
            '<span class="text-xs">Anthropic · Proprietary</span></div></td>'
        )
        with pytest.raises(ValueError, match="org caption"):
            parse_model_cell(cell)


class TestStripOrgSuffix:
    @pytest.mark.parametrize("name,org_text,org,lic,expected", [
        ("Anthropicclaude-fable-5Anthropic · Proprietary",
         "Anthropic · Proprietary", "Anthropic", "Proprietary", "claude-fable-5"),
        ("tulu-2-dpo-70bAI2 ImpACT Low-risk",
         "AI2 ImpACT Low-risk", "AI2 ImpACT Low-risk", None, "tulu-2-dpo-70b"),
        ("yi-lightningProprietary", "Proprietary", "Proprietary", None, "yi-lightning"),
        # Logo <title> casing can differ from the caption's org name.
        ("Stepfunstep-3StepFun · Apache 2.0",
         "StepFun · Apache 2.0", "StepFun", "Apache 2.0", "step-3"),
        # Untouched when nothing was concatenated.
        ("RWKV-4-Raven-14B", "RWKV · Apache 2.0", "RWKV", "Apache 2.0", "RWKV-4-Raven-14B"),
        ("claude-opus-4-6-thinking",
         "Anthropic · Proprietary", "Anthropic", "Proprietary", "claude-opus-4-6-thinking"),
    ])
    def test_repairs(self, name, org_text, org, lic, expected):
        assert strip_org_suffix(name, org_text, org, lic) == expected


class TestModelNameValidation:
    def test_rejects_org_separator(self):
        from pydantic import ValidationError
        from src.models import ScrapedModel
        with pytest.raises(ValidationError):
            ScrapedModel(
                rank=1, model_name="claude-fable-5Anthropic · Proprietary",
                score=1500.0, votes=100,
            )

    def test_rejects_overlong_name(self):
        from pydantic import ValidationError
        from src.models import ScrapedModel
        with pytest.raises(ValidationError):
            ScrapedModel(rank=1, model_name="x" * 200, score=1500.0, votes=100)

    def test_accepts_real_name(self):
        from src.models import ScrapedModel
        m = ScrapedModel(
            rank=1, model_name="claude-opus-4-1-20250805-thinking-16k",
            score=1500.0, votes=100,
        )
        assert m.model_name == "claude-opus-4-1-20250805-thinking-16k"


class TestNoConcatenatedNamesInFixtures:
    """Whole-fixture guard: no parsed name may carry its org or license."""

    @pytest.mark.parametrize("fixture", ["scrape_result", "coding_result", "math_result"])
    def test_names_are_clean(self, fixture, request):
        result = request.getfixturevalue(fixture)
        for m in result.models:
            assert "·" not in m.model_name
            if m.organization:
                assert not m.model_name.endswith(m.organization)
            if m.license_type:
                assert not m.model_name.endswith(m.license_type)


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
