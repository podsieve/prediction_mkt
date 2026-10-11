"""Forecast which of two close models holds the higher rank at a future date.

Motivating case: on 2026-08-13 `claude-opus-5-max` (1506, 9.7k votes) led
`claude-opus-5-high` (1505, 20.0k votes) by a single displayed point. A one-point
gap is well inside both models' confidence intervals, so the question is not
"who is better" but "how much can the gap move before the target date, and which
way does it drift".

The model has three pieces, each calibrated from our own snapshot history rather
than assumed:

1. **Starting gap**, with rounding slack. Arena displays integer scores, so a
   displayed +1 is really anywhere in (0, 2). Each score carries U(-0.5, 0.5).

2. **Gap volatility.** Estimated two independent ways that agree closely:
   - Empirically, the sd of the 18-day change in score gap for top-10 pairs
     where the leader has under 60% of the trailer's votes: 2.78 (n=177).
   - Bottom-up, from CI shrinkage: for nested estimators Var(theta_2 - theta_1)
     ~= Var_1 - Var_2, and Var scales as 1/votes. With max at 9.7k -> ~15.3k and
     high at 20.0k -> ~31k votes, that gives sd 2.64.
   Both land near 2.6-2.8, so the default is 2.7.

3. **Drift.** New models debut overrated and decay as votes arrive, which would
   favour `high`. But that transient is spent by ~3.5x debut votes (forward
   18-day change: mean -0.10, sd 1.68, n=39) and `max` is already at 4.1x. The
   residual drift is therefore small, not the -0.35/day the raw gap history
   (+7, +4, +2, +3, +2, +3, +1) naively implies.

`P_a_ahead` answers "which of the two is higher". `P_a_is_number_one` also
prices the risk that a third model debuts above both, which is how the #1 spot
has actually changed every time in our window - never by a #2 overtaking a #1.

Run:  python -m analysis.head_to_head
"""
from __future__ import annotations

import argparse
import logging
import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# Empirical defaults measured over 2026-05-09..2026-08-13, 29 refresh events.
DEFAULT_GAP_SD_18D = 2.7
DEFAULT_DRIFT_18D = -0.15
DEFAULT_NEW_LEADER_HAZARD_18D = 0.18
SCORE_ROUNDING = 0.5


@dataclass
class Contender:
    name: str
    score: float
    votes: int
    ci: float


@dataclass
class Forecast:
    p_a_ahead: float
    p_tie_displayed: float
    p_a_is_number_one: float
    p_b_is_number_one: float
    p_third_party: float
    gap_quantiles: Dict[str, float] = field(default_factory=dict)


def gap_sd_from_ci(a: Contender, b: Contender, a_future_votes: int, b_future_votes: int) -> float:
    """Bottom-up volatility of the gap, from how much each CI should shrink.

    Treats the displayed +/- as a 95% half-width and the score estimate as
    efficient, so Var scales as 1/votes and the variance of the *change* is
    Var_now - Var_future.
    """

    def var_of_change(c: Contender, future_votes: int) -> float:
        var_now = (c.ci / 1.96) ** 2
        var_future = var_now * (c.votes / max(future_votes, c.votes))
        return max(var_now - var_future, 0.0)

    return (var_of_change(a, a_future_votes) + var_of_change(b, b_future_votes)) ** 0.5


def simulate(
    a: Contender,
    b: Contender,
    gap_sd: float = DEFAULT_GAP_SD_18D,
    drift: float = DEFAULT_DRIFT_18D,
    new_leader_hazard: float = DEFAULT_NEW_LEADER_HAZARD_18D,
    trials: int = 200_000,
    seed: int = 20260815,
) -> Forecast:
    """Monte Carlo the gap forward one horizon. Positive gap means `a` leads."""
    rng = random.Random(seed)
    base_gap = a.score - b.score

    a_ahead = 0
    displayed_tie = 0
    a_first = 0
    b_first = 0
    third = 0
    gaps: List[float] = []

    for _ in range(trials):
        # Rounding slack on each displayed integer score.
        gap0 = base_gap + rng.uniform(-SCORE_ROUNDING, SCORE_ROUNDING) - rng.uniform(
            -SCORE_ROUNDING, SCORE_ROUNDING
        )
        gap = gap0 + drift + rng.gauss(0.0, gap_sd)
        gaps.append(gap)

        if gap > 0:
            a_ahead += 1
        if abs(round(gap)) < 0.5:
            displayed_tie += 1

        if rng.random() < new_leader_hazard:
            third += 1
        elif gap > 0:
            a_first += 1
        else:
            b_first += 1

    gaps.sort()

    def q(p: float) -> float:
        return gaps[min(int(p * len(gaps)), len(gaps) - 1)]

    return Forecast(
        p_a_ahead=a_ahead / trials,
        p_tie_displayed=displayed_tie / trials,
        p_a_is_number_one=a_first / trials,
        p_b_is_number_one=b_first / trials,
        p_third_party=third / trials,
        gap_quantiles={"p05": q(0.05), "p25": q(0.25), "p50": q(0.50), "p75": q(0.75), "p95": q(0.95)},
    )


def sensitivity(
    a: Contender, b: Contender, grid: List[Tuple[float, float]], **kw
) -> List[Tuple[float, float, float]]:
    """P(a ahead) across a grid of (gap_sd, drift) assumptions."""
    out = []
    for sd, drift in grid:
        f = simulate(a, b, gap_sd=sd, drift=drift, **kw)
        out.append((sd, drift, f.p_a_ahead))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trials", type=int, default=200_000)
    args = p.parse_args()

    a = Contender("claude-opus-5-max", 1506.0, 9679, 7.0)
    b = Contender("claude-opus-5-high", 1505.0, 20030, 5.0)
    a_future, b_future = 15_300, 31_000

    logger.info("Horizon: 2026-08-13 -> 2026-08-31 (18 days)")
    logger.info("%s  %.0f +/- %.0f  %d votes -> ~%d", a.name, a.score, a.ci, a.votes, a_future)
    logger.info("%s  %.0f +/- %.0f  %d votes -> ~%d", b.name, b.score, b.ci, b.votes, b_future)

    bottom_up = gap_sd_from_ci(a, b, a_future, b_future)
    logger.info("\ngap sd: %.2f bottom-up (CI shrinkage) vs 2.78 empirical -> using %.2f",
                bottom_up, DEFAULT_GAP_SD_18D)

    f = simulate(a, b, trials=args.trials)
    logger.info("\n--- Head to head ---")
    logger.info("P(%s ahead of %s)  = %.3f", a.name, b.name, f.p_a_ahead)
    logger.info("P(%s ahead of %s)  = %.3f", b.name, a.name, 1 - f.p_a_ahead)
    logger.info("P(displayed scores tied)   = %.3f", f.p_tie_displayed)
    logger.info("gap quantiles (max - high): %s",
                ", ".join(f"{k} {v:+.1f}" for k, v in f.gap_quantiles.items()))

    logger.info("\n--- Outright #1 on the overall board ---")
    logger.info("P(%s is #1)   = %.3f", a.name, f.p_a_is_number_one)
    logger.info("P(%s is #1)   = %.3f", b.name, f.p_b_is_number_one)
    logger.info("P(some third model is #1) = %.3f", f.p_third_party)

    logger.info("\n--- Sensitivity: P(%s ahead) ---", a.name)
    grid = [(sd, d) for sd in (2.2, 2.7, 3.2) for d in (-0.75, -0.15, 0.0)]
    for sd, drift, prob in sensitivity(a, b, grid, trials=50_000):
        logger.info("  gap_sd %.1f  drift %+.2f  ->  %.3f", sd, drift, prob)


if __name__ == "__main__":
    main()
