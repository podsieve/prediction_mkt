from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup

from src.models import ScrapedModel, ScrapeResult

logger = logging.getLogger(__name__)


def parse_int(text: str) -> int:
    return int(text.strip().replace(",", ""))


def parse_rank_spread(cell) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    spans = cell.find_all("span")
    if len(spans) >= 2:
        try:
            upper = int(spans[0].get_text(strip=True))
            lower = int(spans[-1].get_text(strip=True))
            raw = f"{upper}↔{lower}"
            return raw, upper, lower
        except (ValueError, IndexError):
            pass
    text = cell.get_text(strip=True)
    match = re.search(r"(\d+)\s*[↔<>\-]+\s*(\d+)", text)
    if match:
        upper, lower = int(match.group(1)), int(match.group(2))
        return text, upper, lower
    return None, None, None


def parse_model_cell(cell) -> Tuple[str, Optional[str], Optional[str]]:
    link = cell.find("a")
    if link:
        name = link.get("title") or link.get_text(strip=True)
    else:
        name = cell.get_text(strip=True).split("\n")[0].strip()

    org_span = cell.find("span", class_=lambda c: c and "text-xs" in c)
    org_text = org_span.get_text(strip=True) if org_span else None

    organization = None
    license_type = None
    if org_text and "·" in org_text:
        parts = org_text.split("·", 1)
        organization = parts[0].strip()
        license_type = parts[1].strip()
    elif org_text:
        organization = org_text

    return name, organization, license_type


def parse_score_cell(cell) -> Tuple[float, Optional[float]]:
    spans = cell.find_all("span")
    score = None
    ci = None
    for span in spans:
        text = span.get_text(strip=True)
        if text.startswith("±"):
            try:
                ci = float(text[1:])
            except ValueError:
                pass
        elif re.match(r"^\d+\.?\d*$", text):
            try:
                score = float(text)
            except ValueError:
                pass

    if score is None:
        text = cell.get_text(strip=True)
        match = re.search(r"([\d.]+)\s*[±]\s*([\d.]+)", text)
        if match:
            score = float(match.group(1))
            ci = float(match.group(2))
        else:
            match = re.search(r"([\d.]+)", text)
            if match:
                score = float(match.group(1))

    if score is None:
        raise ValueError(f"Could not parse score from: {cell.get_text()}")

    return score, ci


def parse_votes_cell(cell) -> int:
    text = cell.get_text(strip=True)
    return parse_int(text)


def parse_total_votes(soup: BeautifulSoup) -> Optional[int]:
    html_str = str(soup)
    match = re.search(r">([\d,]{5,})\s*(?:<!--.*?-->)?\s*votes<", html_str)
    if match:
        return parse_int(match.group(1))
    text = soup.get_text()
    match = re.search(r"([\d,]{5,})\s+votes", text)
    if match:
        return parse_int(match.group(1))
    return None


def parse_leaderboard(html: str, source_url: str, category: str = "overall") -> ScrapeResult:
    start_time = time.monotonic()
    soup = BeautifulSoup(html, "html.parser")
    raw_html_hash = hashlib.sha256(html.encode()).hexdigest()
    total_votes = parse_total_votes(soup)

    tbody = soup.find("tbody")
    if not tbody:
        rows = soup.find_all("tr")
    else:
        rows = tbody.find_all("tr")

    models: List[ScrapedModel] = []
    parse_errors = 0

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        try:
            rank = parse_int(cells[0].get_text(strip=True))
            rank_spread_raw, rank_upper, rank_lower = parse_rank_spread(cells[1])
            model_name, organization, license_type = parse_model_cell(cells[2])
            score, score_ci = parse_score_cell(cells[3])
            votes = parse_votes_cell(cells[4])

            model = ScrapedModel(
                rank=rank,
                rank_spread_raw=rank_spread_raw,
                rank_upper=rank_upper,
                rank_lower=rank_lower,
                model_name=model_name,
                organization=organization,
                license_type=license_type,
                score=score,
                score_ci=score_ci,
                votes=votes,
            )
            models.append(model)
        except Exception as e:
            parse_errors += 1
            row_text = row.get_text(strip=True)[:100]
            logger.warning("Failed to parse row: %s | Error: %s", row_text, e)

    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    if parse_errors > 0:
        logger.warning("Failed to parse %d rows out of %d", parse_errors, len(rows))

    return ScrapeResult(
        scraped_at=datetime.now(timezone.utc),
        source_url=source_url,
        category=category,
        total_models=len(models),
        total_votes=total_votes,
        models=models,
        raw_html_hash=raw_html_hash,
        scrape_duration_ms=elapsed_ms,
    )
