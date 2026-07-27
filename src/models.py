from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

# Longest real Arena model name seen to date is ~45 chars
# (claude-opus-4-1-20250805-thinking-16k and friends); 80 leaves headroom while
# still catching a cell that swallowed the org and license text.
MAX_MODEL_NAME_LENGTH = 80


class ScrapedModel(BaseModel):
    rank: int = Field(gt=0)
    rank_spread_raw: Optional[str] = None
    rank_upper: Optional[int] = Field(default=None, gt=0)
    rank_lower: Optional[int] = Field(default=None, gt=0)
    model_name: str = Field(min_length=1)
    organization: Optional[str] = None
    license_type: Optional[str] = None
    score: float = Field(gt=0)
    score_ci: Optional[float] = Field(default=None, ge=0)
    votes: int = Field(ge=0)

    @field_validator("model_name")
    @classmethod
    def strip_model_name(cls, v: str) -> str:
        v = v.strip()
        # A model name is a slug. Anything carrying the "Org · License" caption
        # separator, a newline, or novel-length text is a parser regression, not
        # a real model — reject it here so it never becomes a row in `models`.
        if "·" in v:
            raise ValueError(f"model name contains an org/license separator: {v!r}")
        if "\n" in v:
            raise ValueError(f"model name spans multiple lines: {v!r}")
        if len(v) > MAX_MODEL_NAME_LENGTH:
            raise ValueError(
                f"model name is {len(v)} chars, over the "
                f"{MAX_MODEL_NAME_LENGTH}-char sanity limit: {v!r}"
            )
        return v


class ScrapeResult(BaseModel):
    scraped_at: datetime
    source_url: str
    category: str = "overall"
    total_models: int = Field(ge=0)
    total_votes: Optional[int] = Field(default=None, ge=0)
    models: List[ScrapedModel]
    raw_html_hash: str
    scrape_duration_ms: int = Field(ge=0)
