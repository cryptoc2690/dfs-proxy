"""Headless CLI for the WNBA optimizer (the GUI is app.py).

Pure-Python: uses engine.build_gpp — no pulp, no numpy. The solver only reads
player.proj/floor/ceil/ownership, so the projection source (CsvProjector or
BalldontlieProjector) is swappable without touching anything here.

    python optimizer.py --csv DKSalaries.csv --mode gpp --n 20 --out lineups.csv
"""

from __future__ import annotations

import argparse

from dk import SALARY_CAP, load_players
from engine import build_cash, build_gpp
from projections import make_projector

# Re-exported for anything importing from optimizer.
optimize_gpp = build_gpp
optimize_cash = build_cash


def _print_lineup(lu, idx=None):
    head = f"Lineup {idx}" if idx is not None else "Lineup"
    m = lu.metrics
    sim = (f"  sim: mean {m['mean']:.0f}  ceil {m['ceiling']:.0f}  p95 {m['p95']:.0f}"
           if m else "")
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
              f"{p.salary:>7,}{p.proj:>7.1f}{p.ownership:>5.0f}%  {', '.join(p.notes)}")


def _write_dk_csv(lineups, path):
    import csv as _csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(["G", "G", "F", "F", "UTIL", "UTIL"])
        for lu in lineups:
            w.writerow([p.label() for p in lu.dk_slots()])
    print(f"\nWrote {len(lineups)} lineup(s) -> {path}")


def main():
    ap = argparse.ArgumentParser(description="WNBA DraftKings optimizer (GPP-first)")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--mode", choices=["gpp", "cash"], default="gpp")
    ap.add_argument("--source", choices=["csv", "bdl", "auto"], default="auto")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--pool", type=int, default=None)
    ap.add_argument("--stack", type=int, default=2)
    ap.add_argument("--max-per-team", type=int, default=4)
    ap.add_argument("--max-exposure", type=float, default=0.6)
    ap.add_argument("--leverage", type=float, default=0.35)
    ap.add_argument("--sims", type=int, default=5000)
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out")
    args = ap.parse_args()

    projector = make_projector(args.source, season=args.season)
    players = projector.project(load_players(args.csv))
    playable = [p for p in players if p.proj > 0]
    out = [p for p in players if p.out]
    print(f"Source: {getattr(projector, 'name', '?')} | {len(players)} players | "
          f"{len(playable)} playable | {len(out)} OUT: "
          f"{', '.join(p.name for p in out) or 'none'}")

    if args.mode == "cash":
        lu = build_cash(players)
        if lu:
            _print_lineup(lu)
            if args.out:
                _write_dk_csv([lu], args.out)
        else:
            print("No feasible lineup.")
        return

    lineups = build_gpp(players, n=args.n, pool_size=args.pool, min_stack=args.stack,
                        max_per_team=args.max_per_team, max_exposure=args.max_exposure,
                        leverage=args.leverage, n_sims=args.sims, seed=args.seed)
    for i, lu in enumerate(lineups, 1):
        _print_lineup(lu, i)
    if args.out:
        _write_dk_csv(lineups, args.out)


if __name__ == "__main__":
    main()
