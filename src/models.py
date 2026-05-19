from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


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
        return v.strip()


class ScrapeResult(BaseModel):
    scraped_at: datetime
    source_url: str
    category: str = "overall"
    total_models: int = Field(ge=0)
    total_votes: Optional[int] = Field(default=None, ge=0)
    models: List[ScrapedModel]
    raw_html_hash: str
    scrape_duration_ms: int = Field(ge=0)
