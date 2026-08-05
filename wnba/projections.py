"""Projection + ownership for the CSV path.

Projections come from one of two sources:
  - DFF cheatsheet (source of truth) — handled in app.py (apply_dff /
    build_players_from_dff), which sets player.proj/floor/ceil directly.
  - DK CSV alone (fallback, no DFF) — CsvProjector here, off AvgPointsPerGame.

Either way the solver only ever reads player.proj / floor / ceil / ownership.
"""

from __future__ import annotations

from dataclasses import dataclass

from dk import Player


class CsvProjector:
    """Project using only what the DraftKings CSV provides.

    AvgPointsPerGame is DK's own season-average fantasy output — a legitimate
    (if blunt) median. Floor/ceiling come from a fixed variance band.
    """

    name = "csv-only"
    floor_mult = 0.72
    ceil_mult = 1.38

    def project(self, players: list[Player]) -> list[Player]:
        for p in players:
            p.proj = p.avg_points
            p.floor = round(p.avg_points * self.floor_mult, 1)
            p.ceil = round(p.avg_points * self.ceil_mult, 1)
            if p.out:
                p.proj = p.floor = p.ceil = 0.0
                p.notes.append("OUT — excluded")
            elif p.questionable:
                p.proj = round(p.proj * 0.90, 1)
                p.floor = round(p.floor * 0.70, 1)
                p.notes.append(f"{p.status} — risk haircut")
        _estimate_ownership(players)
        return players


def _estimate_ownership(players: list[Player]) -> None:
    """Projected-ownership proxy for GPP leverage.

    Value (points per $1k) is the field's dominant driver. Backtested on real
    %Drafted from two contests: plain value ranks ownership at Spearman ~0.63,
    vs ~0.12 for a salary-tier heuristic. Raise value to a power to concentrate
    ownership on the chalk, then normalize so the pool sums to ~600% (six roster
    spots) — giving realistic magnitudes, not just an order.

    Value is computed on the *pre-redistribution* projection: when we boost a
    teammate because someone's out, that's our edge, and it must NOT loop back
    into their estimated ownership and get them extra-faded. (The field's real
    ownership bump from the news is separate from our mechanical boost.)
    """
    playable = [p for p in players if p.proj > 0]
    if not playable:
        return

    def _own_value(p):
        base = max(p.proj - p.redis_bump, 0.0)
        return base / (p.salary / 1000.0) if p.salary else 0.0

    scores = {p.dk_id: max(_own_value(p), 0.1) ** 1.6 for p in playable}
    total = sum(scores.values()) or 1.0
    for p in players:
        p.ownership = round(scores.get(p.dk_id, 0.0) / total * 600, 1) if p.proj > 0 else 0.0
