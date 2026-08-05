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


def make_projector(source: str = "auto", *, season: int | None = None):
    """Pick a projection source.

    - "csv" : DK CSV only (offline, always works)
    - "bdl" : live balldontlie enrichment (needs BALLDONTLIE_API_KEY)
    - "auto": bdl when a key is present, else csv
    """
    import os
    want_bdl = source == "bdl" or (source == "auto" and os.environ.get("BALLDONTLIE_API_KEY"))
    if want_bdl:
        from bdl import BalldontlieProjector
        return BalldontlieProjector(season=season)
    return CsvProjector()


@dataclass
class CsvProjector:
    name: str = "csv-only"

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
    # Value (points per $1k) is the field's dominant driver. Backtested on real
    # %Drafted from two contests: plain value ranks ownership at Spearman ~0.63,
    # vs ~0.12 for the old salary-tier heuristic. Raise value to a power to
    # concentrate ownership on the chalk, then normalize so the pool sums to
    # ~600% (six roster spots) — giving realistic magnitudes, not just an order.
    playable = [p for p in players if p.proj > 0]
    if not playable:
        return
    scores = {p.dk_id: max(p.value, 0.1) ** 1.6 for p in playable}
    total = sum(scores.values()) or 1.0
    for p in players:
        p.ownership = round(scores.get(p.dk_id, 0.0) / total * 600, 1) if p.proj > 0 else 0.0


# The live projector lives in bdl.py (BalldontlieProjector) and exposes the
# same .project(players) contract, so the solver never changes. Select it via
# make_projector("bdl") / make_projector("auto").
