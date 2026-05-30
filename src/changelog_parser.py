from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from bs4 import BeautifulSoup
from pydantic import BaseModel

logger = logging.getLogger(__name__)

CHANGELOG_URL = "https://arena.ai/blog/leaderboard-changelog/"

# Matches "model-name has been added to the X leaderboard"
_ADDED_PATTERN = re.compile(
    r"(.+?)\s+(?:has|have) been added to (?:the )?(.+?) leaderboards?",
    re.IGNORECASE,
)

# Date format used on the changelog page: "May 26, 2026"
_DATE_FORMAT = "%B %d, %Y"


class ChangelogEntry(BaseModel):
    date: datetime
    model_name: str
    category: str
    link: Optional[str] = None


def parse_changelog(html: str, lookback_hours: int = 48) -> List[ChangelogEntry]:
    """Parse the Arena changelog HTML and return recent model additions.

    Only returns entries from the last `lookback_hours` hours to keep
    the comparison set small and avoid false positives on old entries.
    """
    soup = BeautifulSoup(html, "html.parser")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    entries: List[ChangelogEntry] = []
    current_date: Optional[datetime] = None

    # The page is structured as a series of elements: bold date headers
    # followed by paragraphs describing model additions.  We walk the
    # top-level elements looking for date patterns then capture model
    # mentions that follow.
    for element in soup.find_all(["h2", "h3", "p", "strong", "b"]):
        text = element.get_text(strip=True)

        # Try to parse as a date header
        parsed_date = _try_parse_date(text)
        if parsed_date is not None:
            current_date = parsed_date
            continue

        if current_date is None:
            continue

        # Skip entries older than the lookback window
        if current_date < cutoff:
            continue

        # Look for "has been added" / "have been added" sentences
        matches = _ADDED_PATTERN.findall(text)
        if not matches:
            continue

        # Extract linked model names from this element
        links = {a.get_text(strip=True): a.get("href") for a in element.find_all("a")}

        for model_part, category in matches:
            # Handle "X and Y have been added" by splitting on " and "
            model_names = [m.strip() for m in model_part.split(" and ") if m.strip()]
            for name in model_names:
                entries.append(ChangelogEntry(
                    date=current_date,
                    model_name=name,
                    category=category.strip(),
                    link=links.get(name),
                ))

    logger.info(
        "Parsed %d changelog entries within the last %dh",
        len(entries),
        lookback_hours,
    )
    return entries


def _try_parse_date(text: str) -> Optional[datetime]:
    """Attempt to parse a date string like 'May 26, 2026'."""
    # Strip surrounding whitespace and common wrappers
    cleaned = text.strip().strip("*").strip()
    try:
        dt = datetime.strptime(cleaned, _DATE_FORMAT)
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
