"""Projection layer — the swappable seam between raw CSV and the solver.

v1 (this file) projects from the DK CSV alone: median = AvgPointsPerGame,
with a simple variance model for floor/ceiling and a heuristic projected
ownership. Phase 2 will add a `BalldontlieProjector` that overrides
`project()` using recent form, minutes/usage trends, defense-vs-position,
pace, implied team totals, and confirmed inactives from the wnba-* proxy
endpoints. The solver never changes — it only ever reads player.proj /
floor / ceil / ownership.
"""

from __future__ import annotations

from dataclasses import dataclass

from dk import Player


@dataclass
class CsvProjector:
    """Project using only what the DraftKings CSV provides.

    AvgPointsPerGame is DK's own season-average fantasy output, so it is a
    legitimate (if blunt) median. We derive floor/ceiling from a fixed
    variance band and a rough ownership proxy from value + salary tier.
    """

    floor_mult: float = 0.72
    ceil_mult: float = 1.38

    def project(self, players: list[Player]) -> list[Player]:
        for p in players:
            p.proj = p.avg_points
            p.floor = round(p.avg_points * self.floor_mult, 1)
            p.ceil = round(p.avg_points * self.ceil_mult, 1)
            if p.out:
                p.proj = p.floor = p.ceil = 0.0
                p.notes.append("OUT — excluded")
            elif p.questionable:
                # Haircut the median for injury risk; widen the floor down.
                p.proj = round(p.proj * 0.90, 1)
                p.floor = round(p.floor * 0.70, 1)
                p.notes.append(f"{p.status} — risk haircut")
        _estimate_ownership(players)
        return players


def _estimate_ownership(players: list[Player]) -> None:
    """Very rough projected-ownership proxy for GPP leverage.

    Real ownership needs a data feed; until then we approximate it: the
    field piles onto high-value plays (points per $), especially cheap ones
    with a clear salary-relief role. This is only good enough to *rank*
    leverage, not to trust as a number.
    """
    playable = [p for p in players if p.proj > 0]
    if not playable:
        return
    max_val = max(p.value for p in playable) or 1.0
    for p in players:
        if p.proj <= 0:
            p.ownership = 0.0
            continue
        val_score = p.value / max_val               # 0..1, value magnet
        cheap_bonus = 0.25 if p.salary <= 5000 else 0.0
        stud_bonus = 0.15 if p.salary >= 10000 else 0.0
        raw = 0.55 * val_score + cheap_bonus + stud_bonus
        p.ownership = round(min(raw, 1.0) * 45, 1)  # scale to ~0..45%


# Phase 2 placeholder — documents the intended interface.
class BalldontlieProjector:
    """TODO(phase-2): blend market props + recent form + matchup + pace,
    then redistribute minutes/usage for confirmed inactives. Consumes the
    wnba-* proxy endpoints. Must expose the same .project(players) contract
    as CsvProjector so the solver is untouched.
    """

    def __init__(self, proxy_base_url: str):
        self.proxy_base_url = proxy_base_url

    def project(self, players: list[Player]) -> list[Player]:  # pragma: no cover
        raise NotImplementedError("Phase 2: wire wnba-* proxy endpoints")
