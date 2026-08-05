"""Monte-Carlo simulation for GPP lineup evaluation.

Why simulate at all: in a tournament you are not paid for your lineup's
*average* — you are paid for its *ceiling* landing in the top fraction of a
huge field. Average (projection) and ceiling are different objectives, and
they rank lineups differently. So we sample thousands of "tonight"s and score
lineups on the tail, not the mean.

Two ideas do the work:

1. Per-player skewed outcomes. A player's fantasy night is right-skewed
   (capped downside ~0, long upside via a 40-burger). We draw from a
   Beta-PERT distribution parameterized by (floor, median, ceiling) so the
   shape matches reality instead of a symmetric bell.

2. Game correlation. Teammates and opponents in the same game rise and fall
   together — a fast, high-scoring game inflates everyone's counting stats.
   We draw one "game environment" multiplier per game per simulation and
   apply it to every player in that game. This is *why* stacking a game
   works, and the sim rewards it automatically instead of us bolting on a
   rule.
"""

from __future__ import annotations

import numpy as np

from dk import Player


def _pert_samples(floor: float, median: float, ceil: float, n: int,
                  rng: np.random.Generator, lam: float = 4.0) -> np.ndarray:
    """Beta-PERT draws in [floor, ceil] with mode ~ median.

    lam controls how tightly outcomes concentrate on the median (classic
    PERT uses 4). Degenerate spans fall back to a point mass.
    """
    lo, mo, hi = float(floor), float(median), float(ceil)
    if hi - lo < 1e-6:
        return np.full(n, mo)
    mo = min(max(mo, lo + 1e-6), hi - 1e-6)
    alpha = 1.0 + lam * (mo - lo) / (hi - lo)
    beta = 1.0 + lam * (hi - mo) / (hi - lo)
    return lo + rng.beta(alpha, beta, size=n) * (hi - lo)


def simulate_players(players: list[Player], n_sims: int = 10_000, *,
                     game_sigma: float = 0.10, seed: int = 0
                     ) -> tuple[dict[str, int], np.ndarray]:
    """Simulate every playable player's fantasy output.

    Returns (id_to_row, matrix) where matrix[row] is an n_sims vector of that
    player's simulated DK points, already carrying game correlation.
    """
    rng = np.random.default_rng(seed)
    pool = [p for p in players if p.proj > 0]

    # One shared environment multiplier per game per sim (positive game corr).
    games = sorted({p.game for p in pool})
    game_idx = {g: i for i, g in enumerate(games)}
    game_mult = rng.normal(1.0, game_sigma, size=(len(games), n_sims)).clip(0.6, 1.5)

    id_to_row: dict[str, int] = {}
    mat = np.zeros((len(pool), n_sims), dtype=np.float32)
    for row, p in enumerate(pool):
        base = _pert_samples(p.floor, p.proj, p.ceil, n_sims, rng)
        mat[row] = base * game_mult[game_idx[p.game]]
        id_to_row[p.dk_id] = row
    return id_to_row, mat


def lineup_metrics(lineup_ids: list[str], id_to_row: dict[str, int],
                   mat: np.ndarray) -> dict[str, float]:
    """Score one lineup across all simulations."""
    rows = [id_to_row[i] for i in lineup_ids if i in id_to_row]
    totals = mat[rows].sum(axis=0)
    return {
        "mean": float(totals.mean()),
        "floor": float(np.percentile(totals, 15)),
        "ceiling": float(np.percentile(totals, 85)),
        "p95": float(np.percentile(totals, 95)),
        "std": float(totals.std()),
    }
