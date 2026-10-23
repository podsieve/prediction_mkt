"""Work out which scraped names are existing models under a new naming scheme.

On 2026-10 Arena switched its leaderboard labels from slugs
("claude-opus-4-6-high") to display names ("Claude Opus 4.6 (High)"). Every
name missed the model cache, so the new-model surge guard refused the scrape.
`match_renamed_models` pairs each unrecognised scraped name with the existing
model it really is, so scripts/alias_renamed_models.py can record the new names
as aliases and the history stays on one model ID.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Set

from src.models import ScrapedModel

# A rename is the same model, so its score and vote count must still line up
# with the last stored snapshot (votes only grow, scores drift a little).
MAX_SCORE_DRIFT = 15.0
MIN_VOTE_RATIO = 0.95
MIN_NAME_SIMILARITY = 0.5
MIN_SIMILARITY_MARGIN = 0.1


@dataclass
class PrevRanking:
    """One model's row in a category's last successful snapshot."""

    model_id: str
    name: str
    score: float
    votes: int


@dataclass
class RenameMatch:
    scraped_name: str
    model_id: str
    model_name: str
    method: str  # "name" (names agree once punctuation is ignored) or "stats"


def normalize_name(name: str) -> str:
    """Lowercase and drop everything but letters and digits.

    "Claude Opus 5.5 (High)" and "claude-opus-5.5-high" both become
    "claudeopus55high".
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()


def match_renamed_models(
    live: Iterable[ScrapedModel],
    known: Dict[str, str],
    aliases: Dict[str, str],
    prev: List[PrevRanking],
) -> tuple[List[RenameMatch], List[ScrapedModel]]:
    """Match scraped names that don't resolve to the model they really are.

    `known` maps canonical name -> id, `aliases` alias -> id, and `prev` is the
    category's last successful snapshot. Returns (matches, unmatched). Each
    existing model is claimed by at most one scraped name, and anything
    ambiguous is left in `unmatched` for a human rather than guessed at.
    """
    unresolved = [m for m in live if m.model_name not in known and m.model_name not in aliases]

    ids_by_norm: Dict[str, Set[str]] = {}
    name_by_id: Dict[str, str] = {}
    for name, model_id in known.items():
        ids_by_norm.setdefault(normalize_name(name), set()).add(model_id)
        name_by_id[model_id] = name
    for name, model_id in aliases.items():
        ids_by_norm.setdefault(normalize_name(name), set()).add(model_id)

    # A model already claimed by an exact-name scraped row can't also be the
    # target of a rename.
    claimed: Set[str] = {
        known.get(m.model_name) or aliases[m.model_name]
        for m in live
        if m.model_name in known or m.model_name in aliases
    }

    matches: List[RenameMatch] = []
    pending: List[ScrapedModel] = []

    for model in unresolved:
        ids = ids_by_norm.get(normalize_name(model.model_name), set()) - claimed
        if len(ids) == 1:
            model_id = next(iter(ids))
            claimed.add(model_id)
            matches.append(RenameMatch(model.model_name, model_id, name_by_id[model_id], "name"))
        else:
            pending.append(model)

    # Fallback for names that differ by more than punctuation: same model,
    # near-identical score and vote count in the last snapshot.
    unmatched: List[ScrapedModel] = []
    for model in pending:
        candidates = [
            p for p in prev
            if p.model_id not in claimed
            and abs(p.score - model.score) <= MAX_SCORE_DRIFT
            and model.votes >= p.votes * MIN_VOTE_RATIO
        ]
        ranked = sorted(
            ((_similarity(model.model_name, p.name), p) for p in candidates),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best = _accept(ranked)
        if best is None:
            unmatched.append(model)
            continue
        claimed.add(best.model_id)
        matches.append(
            RenameMatch(model.model_name, best.model_id, name_by_id.get(best.model_id, best.name), "stats")
        )

    return matches, unmatched


def _accept(ranked) -> Optional[PrevRanking]:
    if not ranked:
        return None
    best_sim, best = ranked[0]
    if best_sim < MIN_NAME_SIMILARITY:
        return None
    if len(ranked) > 1 and ranked[1][0] > best_sim - MIN_SIMILARITY_MARGIN:
        return None
    return best
