"""WNBA DraftKings lineup optimizer.

Cash mode  : one lineup that maximizes projected points (high floor) under
             the DK cap + G/G/F/F/UTIL/UTIL rules.
GPP mode   : N diverse lineups, each optimized on a randomized ("ceiling")
             projection, forced to differ from one another, with a
             max-players-per-team cap so no lineup over-stacks.

The solver only reads player.proj / floor / ceil / ownership — it never
knows where those numbers came from, so swapping CsvProjector for the
Phase-2 balldontlie projector changes nothing here.

Usage:
    python optimizer.py --csv DKSalaries.csv --mode cash
    python optimizer.py --csv DKSalaries.csv --mode gpp --n 20 --out lineups.csv
"""

from __future__ import annotations

import argparse
import random

import pulp

from dk import (MIN_FORWARDS, MIN_GUARDS, ROSTER_SIZE, SALARY_CAP, Player,
                load_players)
from projections import CsvProjector


class Lineup:
    def __init__(self, players: list[Player]):
        self.players = sorted(players, key=lambda p: (-p.salary, p.name))

    @property
    def salary(self) -> int:
        return sum(p.salary for p in self.players)

    @property
    def proj(self) -> float:
        return round(sum(p.proj for p in self.players), 1)

    @property
    def floor(self) -> float:
        return round(sum(p.floor for p in self.players), 1)

    @property
    def ceil(self) -> float:
        return round(sum(p.ceil for p in self.players), 1)

    @property
    def total_own(self) -> float:
        return round(sum(p.ownership for p in self.players), 1)

    def ids(self) -> frozenset[str]:
        return frozenset(p.dk_id for p in self.players)

    # DK upload order: G, G, F, F, UTIL, UTIL.
    def dk_slots(self) -> list[Player]:
        guards = [p for p in self.players if p.is_guard]
        forwards = [p for p in self.players if not p.is_guard]
        guards.sort(key=lambda p: -p.proj)
        forwards.sort(key=lambda p: -p.proj)
        slotted = [guards[0], guards[1], forwards[0], forwards[1]]
        rest = guards[2:] + forwards[2:]
        rest.sort(key=lambda p: -p.proj)
        return slotted + rest[:2]


def _solve(players: list[Player], objective_key: str,
           exclude_lineups: list[frozenset[str]],
           max_per_team: int | None,
           min_diff: int) -> Lineup | None:
    """Single ILP solve. objective_key selects proj/ceil/floor as the
    maximization target. exclude_lineups forces >= min_diff player
    difference from each already-built lineup (GPP diversity)."""
    pool = [p for p in players if p.proj > 0]
    prob = pulp.LpProblem("wnba_dk", pulp.LpMaximize)
    x = {p.dk_id: pulp.LpVariable(f"x_{p.dk_id}", cat="Binary") for p in pool}

    prob += pulp.lpSum(getattr(p, objective_key) * x[p.dk_id] for p in pool)

    prob += pulp.lpSum(x.values()) == ROSTER_SIZE
    prob += pulp.lpSum(p.salary * x[p.dk_id] for p in pool) <= SALARY_CAP
    prob += pulp.lpSum(x[p.dk_id] for p in pool if p.is_guard) >= MIN_GUARDS
    prob += pulp.lpSum(x[p.dk_id] for p in pool if not p.is_guard) >= MIN_FORWARDS

    if max_per_team:
        teams = {p.team for p in pool}
        for t in teams:
            prob += pulp.lpSum(x[p.dk_id] for p in pool if p.team == t) <= max_per_team

    # Force diversity vs. previously built lineups: shared players <= size - min_diff.
    for prev in exclude_lineups:
        prob += pulp.lpSum(x[pid] for pid in prev if pid in x) <= ROSTER_SIZE - min_diff

    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[prob.status] != "Optimal":
        return None
    chosen = [p for p in pool if x[p.dk_id].value() and x[p.dk_id].value() > 0.5]
    return Lineup(chosen) if len(chosen) == ROSTER_SIZE else None


def optimize_cash(players: list[Player]) -> Lineup | None:
    """Highest projected-points lineup. In cash you want the median monster;
    proj is the objective and the DK floor is what actually matters."""
    return _solve(players, "proj", [], max_per_team=None, min_diff=0)


def optimize_gpp(players: list[Player], n: int = 20, *, min_diff: int = 2,
                 max_per_team: int = 3, seed: int = 0) -> list[Lineup]:
    """N ceiling-oriented lineups. Each solve randomizes projections around
    the ceiling so the pool explores the right tail, and each new lineup must
    differ by >= min_diff players from every prior one."""
    rng = random.Random(seed)
    built: list[Lineup] = []
    excludes: list[frozenset[str]] = []
    # Snapshot true ceilings so we can restore after perturbing.
    base_ceil = {p.dk_id: p.ceil for p in players}
    for _ in range(n):
        for p in players:
            if p.proj > 0:
                # Randomize toward ceiling: sample a "tonight" outcome.
                jitter = rng.uniform(0.80, 1.0)
                p.ceil = round(base_ceil[p.dk_id] * jitter, 2)
        lu = _solve(players, "ceil", excludes, max_per_team, min_diff)
        # Restore true ceilings before scoring/printing.
        for p in players:
            p.ceil = base_ceil[p.dk_id]
        if lu is None:
            break
        built.append(lu)
        excludes.append(lu.ids())
    return built


# --- presentation --------------------------------------------------------

def _print_lineup(lu: Lineup, idx: int | None = None) -> None:
    head = f"Lineup {idx}" if idx is not None else "Lineup"
    print(f"\n{head}  |  proj {lu.proj}  floor {lu.floor}  ceil {lu.ceil}"
          f"  |  ${lu.salary:,} / ${SALARY_CAP:,}  |  own~{lu.total_own}%")
    print(f"  {'SLOT':<5}{'POS':<4}{'PLAYER':<24}{'TEAM':<5}{'SAL':>7}"
          f"{'PROJ':>7}{'VAL':>6}  NOTES")
    slot_names = ["G", "G", "F", "F", "UTIL", "UTIL"]
    for slot, p in zip(slot_names, lu.dk_slots()):
        note = ", ".join(p.notes)
        print(f"  {slot:<5}{p.pos:<4}{p.name:<24}{p.team:<5}"
              f"{p.salary:>7,}{p.proj:>7.1f}{p.value:>6.1f}  {note}")


def _write_dk_csv(lineups: list[Lineup], path: str) -> None:
    import csv as _csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(["G", "G", "F", "F", "UTIL", "UTIL"])
        for lu in lineups:
            w.writerow([p.label() for p in lu.dk_slots()])
    print(f"\nWrote {len(lineups)} lineup(s) -> {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="WNBA DraftKings optimizer")
    ap.add_argument("--csv", required=True, help="DraftKings salary export")
    ap.add_argument("--mode", choices=["cash", "gpp"], default="cash")
    ap.add_argument("--n", type=int, default=20, help="GPP: number of lineups")
    ap.add_argument("--min-diff", type=int, default=2,
                    help="GPP: min differing players between lineups")
    ap.add_argument("--max-per-team", type=int, default=3,
                    help="GPP: max players from one team")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", help="write DK-uploadable CSV to this path")
    args = ap.parse_args()

    players = CsvProjector().project(load_players(args.csv))
    playable = [p for p in players if p.proj > 0]
    excluded = [p for p in players if p.out]
    print(f"Loaded {len(players)} players | {len(playable)} playable | "
          f"{len(excluded)} excluded (OUT): "
          f"{', '.join(p.name for p in excluded) or 'none'}")

    if args.mode == "cash":
        lu = optimize_cash(players)
        if not lu:
            print("No feasible lineup.")
            return
        _print_lineup(lu)
        if args.out:
            _write_dk_csv([lu], args.out)
    else:
        lineups = optimize_gpp(players, n=args.n, min_diff=args.min_diff,
                               max_per_team=args.max_per_team, seed=args.seed)
        for i, lu in enumerate(lineups, 1):
            _print_lineup(lu, i)
        if args.out:
            _write_dk_csv(lineups, args.out)


if __name__ == "__main__":
    main()
