"""WNBA DraftKings lineup optimizer — GPP-first.

The engine you'll actually use is GPP mode:

  1. Generate a large, diverse pool of valid lineups (ILP, randomized
     ceiling objectives), optionally forcing a game stack.
  2. Monte-Carlo simulate the slate with game correlation (simulate.py).
  3. Rank lineups by a tournament objective: simulated ceiling, then
     leverage-adjust by projected ownership (being right where the field
     is light pays more than being right where it's heavy).
  4. Select the final N under per-player exposure caps + uniqueness.

Cash mode is kept as a simple max-projection ILP for completeness, but GPP
is the point.

The solver only reads player.proj/floor/ceil/ownership, so the projection
source (CsvProjector today, BalldontlieProjector once the GOAT key is set)
is swappable without touching anything here.

    python optimizer.py --csv DKSalaries.csv --mode gpp --n 20 --out lineups.csv
    python optimizer.py --csv DKSalaries.csv --mode gpp --n 20 \
        --source bdl --stack 2 --max-exposure 0.5 --out lineups.csv
"""

from __future__ import annotations

import argparse
import random

import pulp

from dk import (MIN_FORWARDS, MIN_GUARDS, ROSTER_SIZE, SALARY_CAP, Player,
                load_players)
from projections import CsvProjector, make_projector


class Lineup:
    def __init__(self, players: list[Player]):
        self.players = sorted(players, key=lambda p: (-p.salary, p.name))
        self.metrics: dict[str, float] = {}

    @property
    def salary(self) -> int:
        return sum(p.salary for p in self.players)

    @property
    def proj(self) -> float:
        return round(sum(p.proj for p in self.players), 1)

    @property
    def total_own(self) -> float:
        return round(sum(p.ownership for p in self.players), 1)

    def ids(self) -> list[str]:
        return [p.dk_id for p in self.players]

    def id_set(self) -> frozenset[str]:
        return frozenset(p.dk_id for p in self.players)

    def games(self) -> set[str]:
        return {p.game for p in self.players}

    def dk_slots(self) -> list[Player]:
        """DK upload order: G, G, F, F, UTIL, UTIL."""
        guards = sorted([p for p in self.players if p.is_guard], key=lambda p: -p.proj)
        forwards = sorted([p for p in self.players if not p.is_guard], key=lambda p: -p.proj)
        slotted = [guards[0], guards[1], forwards[0], forwards[1]]
        rest = sorted(guards[2:] + forwards[2:], key=lambda p: -p.proj)
        return slotted + rest[:2]


def _solve(pool: list[Player], objective_key: str,
           exclude: list[frozenset[str]], *,
           max_per_team: int | None, min_diff: int,
           min_stack: int) -> Lineup | None:
    prob = pulp.LpProblem("wnba_dk", pulp.LpMaximize)
    x = {p.dk_id: pulp.LpVariable(f"x_{p.dk_id}", cat="Binary") for p in pool}

    prob += pulp.lpSum(getattr(p, objective_key) * x[p.dk_id] for p in pool)
    prob += pulp.lpSum(x.values()) == ROSTER_SIZE
    prob += pulp.lpSum(p.salary * x[p.dk_id] for p in pool) <= SALARY_CAP
    prob += pulp.lpSum(x[p.dk_id] for p in pool if p.is_guard) >= MIN_GUARDS
    prob += pulp.lpSum(x[p.dk_id] for p in pool if not p.is_guard) >= MIN_FORWARDS

    if max_per_team:
        for t in {p.team for p in pool}:
            prob += pulp.lpSum(x[p.dk_id] for p in pool if p.team == t) <= max_per_team

    # Require at least one game with >= min_stack of our players (game stack).
    if min_stack and min_stack > 1:
        games = sorted({p.game for p in pool})
        y = {g: pulp.LpVariable(f"y_{i}", cat="Binary") for i, g in enumerate(games)}
        for g in games:
            in_game = [x[p.dk_id] for p in pool if p.game == g]
            # y[g] can only be 1 if we actually have min_stack players in g.
            prob += pulp.lpSum(in_game) >= min_stack * y[g]
        prob += pulp.lpSum(y.values()) >= 1

    for prev in exclude:
        prob += pulp.lpSum(x[pid] for pid in prev if pid in x) <= ROSTER_SIZE - min_diff

    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[prob.status] != "Optimal":
        return None
    chosen = [p for p in pool if x[p.dk_id].value() and x[p.dk_id].value() > 0.5]
    return Lineup(chosen) if len(chosen) == ROSTER_SIZE else None


def optimize_cash(players: list[Player]) -> Lineup | None:
    pool = [p for p in players if p.proj > 0]
    return _solve(pool, "proj", [], max_per_team=None, min_diff=0, min_stack=0)


def _generate_pool(players: list[Player], size: int, *, min_diff: int,
                   max_per_team: int, min_stack: int, seed: int) -> list[Lineup]:
    """Diverse valid candidate lineups via randomized-ceiling ILP solves."""
    rng = random.Random(seed)
    pool = [p for p in players if p.proj > 0]
    base_ceil = {p.dk_id: p.ceil for p in pool}
    built: list[Lineup] = []
    seen: set[frozenset[str]] = set()
    excludes: list[frozenset[str]] = []
    attempts = 0
    while len(built) < size and attempts < size * 4:
        attempts += 1
        for p in pool:
            p.ceil = base_ceil[p.dk_id] * rng.uniform(0.75, 1.05)
        lu = _solve(pool, "ceil", excludes, max_per_team=max_per_team,
                    min_diff=min_diff, min_stack=min_stack)
        for p in pool:
            p.ceil = base_ceil[p.dk_id]
        if lu is None:
            break
        if lu.id_set() in seen:
            excludes.append(lu.id_set())
            continue
        seen.add(lu.id_set())
        excludes.append(lu.id_set())
        built.append(lu)
    return built


def optimize_gpp(players: list[Player], n: int = 20, *, pool_size: int = 150,
                 min_diff: int = 1, max_per_team: int = 4, min_stack: int = 2,
                 max_exposure: float = 0.6, leverage: float = 0.35,
                 n_sims: int = 10_000, seed: int = 0) -> list[Lineup]:
    """Build, simulate, leverage-rank, and exposure-cap the final N lineups."""
    from simulate import lineup_metrics, simulate_players

    candidates = _generate_pool(players, pool_size, min_diff=min_diff,
                                max_per_team=max_per_team, min_stack=min_stack,
                                seed=seed)
    if not candidates:
        return []

    id_to_row, mat = simulate_players(players, n_sims=n_sims, seed=seed)
    for lu in candidates:
        lu.metrics = lineup_metrics(lu.ids(), id_to_row, mat)

    # Leverage-adjusted ceiling. Normalize ownership across candidates so the
    # penalty is relative to this slate's field, not an absolute %.
    owns = [lu.total_own for lu in candidates]
    lo, hi = min(owns), max(owns)
    span = (hi - lo) or 1.0
    for lu in candidates:
        own_norm = (lu.total_own - lo) / span           # 0 (contrarian)..1 (chalky)
        lu.metrics["score"] = lu.metrics["ceiling"] * (1 + leverage * (1 - 2 * own_norm))

    candidates.sort(key=lambda l: -l.metrics["score"])

    # Greedy selection under per-player exposure cap.
    cap = max(1, int(round(max_exposure * n)))
    counts: dict[str, int] = {}
    final: list[Lineup] = []
    for lu in candidates:
        if len(final) >= n:
            break
        if any(counts.get(pid, 0) >= cap for pid in lu.ids()):
            continue
        final.append(lu)
        for pid in lu.ids():
            counts[pid] = counts.get(pid, 0) + 1
    # If exposure caps starved us, backfill by score.
    for lu in candidates:
        if len(final) >= n:
            break
        if lu not in final:
            final.append(lu)
    return final[:n]


# --- presentation --------------------------------------------------------

def _print_lineup(lu: Lineup, idx: int | None = None) -> None:
    head = f"Lineup {idx}" if idx is not None else "Lineup"
    m = lu.metrics
    sim = (f"  sim: mean {m['mean']:.0f}  ceil {m['ceiling']:.0f}  "
           f"p95 {m['p95']:.0f}" if m else "")
    print(f"\n{head}  |  proj {lu.proj}  ${lu.salary:,}/{SALARY_CAP:,}  "
          f"own~{lu.total_own}%{sim}")
    stacks = {g: sum(1 for p in lu.players if p.game == g) for g in lu.games()}
    stack_txt = ", ".join(f"{g}:{c}" for g, c in stacks.items() if c >= 2)
    if stack_txt:
        print(f"  stacks: {stack_txt}")
    print(f"  {'SLOT':<5}{'POS':<4}{'PLAYER':<24}{'TEAM':<5}{'GAME':<9}"
          f"{'SAL':>7}{'PROJ':>7}{'OWN':>6}  NOTES")
    for slot, p in zip(["G", "G", "F", "F", "UTIL", "UTIL"], lu.dk_slots()):
        print(f"  {slot:<5}{p.pos:<4}{p.name:<24}{p.team:<5}{p.game:<9}"
              f"{p.salary:>7,}{p.proj:>7.1f}{p.ownership:>5.0f}%  "
              f"{', '.join(p.notes)}")


def _write_dk_csv(lineups: list[Lineup], path: str) -> None:
    import csv as _csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(["G", "G", "F", "F", "UTIL", "UTIL"])
        for lu in lineups:
            w.writerow([p.label() for p in lu.dk_slots()])
    print(f"\nWrote {len(lineups)} lineup(s) -> {path}")


def _exposure_report(lineups: list[Lineup]) -> None:
    counts: dict[str, tuple[str, int]] = {}
    for lu in lineups:
        for p in lu.players:
            name, c = counts.get(p.dk_id, (p.name, 0))
            counts[p.dk_id] = (name, c + 1)
    n = len(lineups)
    print("\nExposure (top 12):")
    for name, c in sorted(counts.values(), key=lambda t: -t[1])[:12]:
        print(f"  {c/n*100:4.0f}%  {name}")


def main() -> None:
    ap = argparse.ArgumentParser(description="WNBA DraftKings optimizer (GPP-first)")
    ap.add_argument("--csv", required=True, help="DraftKings salary export")
    ap.add_argument("--mode", choices=["gpp", "cash"], default="gpp")
    ap.add_argument("--source", choices=["csv", "bdl", "auto"], default="auto",
                    help="projection source: csv-only, balldontlie, or auto "
                         "(bdl if BALLDONTLIE_API_KEY set, else csv)")
    ap.add_argument("--n", type=int, default=20, help="GPP: lineups to build")
    ap.add_argument("--pool", type=int, default=150, help="GPP: candidate pool size")
    ap.add_argument("--stack", type=int, default=2, help="GPP: min game-stack size")
    ap.add_argument("--max-per-team", type=int, default=4)
    ap.add_argument("--max-exposure", type=float, default=0.6,
                    help="GPP: max share of lineups any one player appears in")
    ap.add_argument("--leverage", type=float, default=0.35,
                    help="GPP: how hard to reward low ownership (0 = pure ceiling)")
    ap.add_argument("--sims", type=int, default=10_000)
    ap.add_argument("--season", type=int, default=None, help="bdl: season year")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", help="write DK-uploadable CSV to this path")
    args = ap.parse_args()

    projector = make_projector(args.source, season=args.season)
    players = projector.project(load_players(args.csv))
    playable = [p for p in players if p.proj > 0]
    out = [p for p in players if p.out]
    print(f"Source: {projector.name} | {len(players)} players | "
          f"{len(playable)} playable | {len(out)} OUT: "
          f"{', '.join(p.name for p in out) or 'none'}")

    if args.mode == "cash":
        lu = optimize_cash(players)
        if lu:
            _print_lineup(lu)
            if args.out:
                _write_dk_csv([lu], args.out)
        else:
            print("No feasible lineup.")
        return

    lineups = optimize_gpp(
        players, n=args.n, pool_size=args.pool, max_per_team=args.max_per_team,
        min_stack=args.stack, max_exposure=args.max_exposure,
        leverage=args.leverage, n_sims=args.sims, seed=args.seed)
    for i, lu in enumerate(lineups, 1):
        _print_lineup(lu, i)
    if lineups:
        _exposure_report(lineups)
    if args.out:
        _write_dk_csv(lineups, args.out)


if __name__ == "__main__":
    main()
