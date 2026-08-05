"""Pure-Python GPP engine — no third-party dependencies.

Replaces the pulp ILP + numpy simulation with a randomized-construction
lineup builder and a hand-rolled Monte-Carlo simulation, so the whole app
runs on a stock Python install with nothing to pip-install. This is the
standard GPP approach anyway: you want a diverse pool of strong, stacked
lineups, then rank them on simulated ceiling — not the single LP optimum.
"""

from __future__ import annotations

import math
import random

from dk import MIN_FORWARDS, MIN_GUARDS, ROSTER_SIZE, SALARY_CAP, Player

MIN_SALARY = 3000  # DK WNBA min; used so partial lineups stay completable


class Lineup:
    def __init__(self, players: list[Player]):
        self.players = players
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

    def games(self) -> set[str]:
        return {p.game for p in self.players}

    def dk_slots(self) -> list[Player]:
        """DK upload order: G, G, F, F, UTIL, UTIL."""
        g = sorted((p for p in self.players if p.is_guard), key=lambda p: -p.proj)
        f = sorted((p for p in self.players if not p.is_guard), key=lambda p: -p.proj)
        slotted = [g[0], g[1], f[0], f[1]]
        rest = sorted(g[2:] + f[2:], key=lambda p: -p.proj)
        return slotted + rest[:2]


# ---------------- lineup construction ----------------
def _weighted_pick(cands, rng):
    weights = [max(p.proj, 0.1) ** 3 * (0.6 + 0.8 * rng.random()) for p in cands]
    total = sum(weights)
    x = rng.random() * total
    for p, w in zip(cands, weights):
        x -= w
        if x <= 0:
            return p
    return cands[-1]


def _build_one(pool, max_per_team, rng):
    template = ["G", "G", "F", "F", "U", "U"]
    picked, used, team_count, salary = [], set(), {}, 0
    for i, slot in enumerate(template):
        slots_after = len(template) - i - 1
        budget = SALARY_CAP - salary - MIN_SALARY * slots_after
        elig = [p for p in pool
                if p.dk_id not in used and p.salary <= budget
                and (slot == "U" or p.pos == slot)
                and team_count.get(p.team, 0) < max_per_team]
        # keep enough G/F headroom to satisfy the minimums
        feasible = [p for p in elig if _can_complete(picked, p, slots_after)]
        choices = feasible or elig
        if not choices:
            return None
        p = _weighted_pick(choices, rng)
        picked.append(p)
        used.add(p.dk_id)
        salary += p.salary
        team_count[p.team] = team_count.get(p.team, 0) + 1
    g = sum(1 for p in picked if p.is_guard)
    if g < MIN_GUARDS or (len(picked) - g) < MIN_FORWARDS or salary > SALARY_CAP:
        return None
    return picked


def _can_complete(picked, cand, slots_after):
    g = sum(1 for p in picked if p.is_guard) + (1 if cand.is_guard else 0)
    f = sum(1 for p in picked if not p.is_guard) + (0 if cand.is_guard else 1)
    return max(0, MIN_GUARDS - g) + max(0, MIN_FORWARDS - f) <= slots_after


def _has_stack(players, stack):
    counts = {}
    for p in players:
        counts[p.game] = counts.get(p.game, 0) + 1
    return any(c >= stack for c in counts.values())


def build_candidates(pool, count, *, stack, max_per_team, seed=0):
    rng = random.Random(seed)
    out, seen = [], set()
    tries = 0
    while len(out) < count and tries < count * 15:
        tries += 1
        lu = _build_one(pool, max_per_team, rng)
        if not lu:
            continue
        if stack > 1 and not _has_stack(lu, stack):
            continue
        key = frozenset(p.dk_id for p in lu)
        if key in seen:
            continue
        seen.add(key)
        out.append(Lineup(lu))
    return out


# ---------------- simulation ----------------
def _gamma(k, rng):  # Marsaglia-Tsang
    if k < 1:
        return _gamma(k + 1, rng) * (rng.random() or 1e-9) ** (1.0 / k)
    d = k - 1.0 / 3.0
    c = 1.0 / math.sqrt(9 * d)
    while True:
        x = rng.gauss(0, 1)
        v = 1 + c * x
        if v <= 0:
            continue
        v = v ** 3
        u = rng.random()
        if u < 1 - 0.0331 * x ** 4 or math.log(u) < 0.5 * x * x + d * (1 - v + math.log(v)):
            return d * v


def _pert(lo, mode, hi, rng):
    if hi - lo < 1e-6:
        return mode
    mode = min(max(mode, lo + 1e-6), hi - 1e-6)
    a = 1 + 4 * (mode - lo) / (hi - lo)
    b = 1 + 4 * (hi - mode) / (hi - lo)
    x = _gamma(a, rng)
    y = _gamma(b, rng)
    return lo + (x / (x + y)) * (hi - lo)


def simulate_and_score(cands, pool, *, sims, leverage, seed=0):
    rng = random.Random(seed + 7)
    games = sorted({p.game for p in pool})
    gidx = {g: i for i, g in enumerate(games)}
    game_mult = [[min(max(1 + 0.10 * rng.gauss(0, 1), 0.6), 1.5) for _ in range(sims)]
                 for _ in games]
    id_row, mat = {}, []
    for row, p in enumerate(pool):
        gm = game_mult[gidx[p.game]]
        mat.append([_pert(p.floor, p.proj, p.ceil, rng) * gm[s] for s in range(sims)])
        id_row[p.dk_id] = row
    for c in cands:
        rows = [id_row[i] for i in c.ids() if i in id_row]
        totals = [sum(mat[r][s] for r in rows) for s in range(sims)]
        totals.sort()
        c.metrics = {
            "mean": sum(totals) / sims,
            "ceiling": totals[int(sims * 0.85)],
            "p95": totals[int(sims * 0.95)],
        }
    owns = [c.total_own for c in cands] or [0]
    lo, hi = min(owns), max(owns)
    span = (hi - lo) or 1.0
    for c in cands:
        on = (c.total_own - lo) / span
        c.metrics["score"] = c.metrics["ceiling"] * (1 + leverage * (1 - 2 * on))
    cands.sort(key=lambda c: -c.metrics["score"])


def select_final(cands, n, max_exposure):
    cap = max(1, round(max_exposure * n))
    counts, final = {}, []
    for c in cands:
        if len(final) >= n:
            break
        if any(counts.get(i, 0) >= cap for i in c.ids()):
            continue
        final.append(c)
        for i in c.ids():
            counts[i] = counts.get(i, 0) + 1
    for c in cands:  # backfill if exposure caps starved us
        if len(final) >= n:
            break
        if c not in final:
            final.append(c)
    return final[:n]


# ---------------- public API ----------------
def build_gpp(players, *, n=20, pool_size=None, min_stack=2, max_per_team=4,
              max_exposure=0.6, leverage=0.35, n_sims=5000, seed=0):
    pool = [p for p in players if p.proj > 0]
    if len(pool) < ROSTER_SIZE:
        return []
    pool_size = pool_size or max(120, n * 8)
    cands = build_candidates(pool, pool_size, stack=min_stack,
                             max_per_team=max_per_team, seed=seed)
    if not cands:
        # relax the stack requirement rather than return nothing
        cands = build_candidates(pool, pool_size, stack=1,
                                 max_per_team=max_per_team, seed=seed)
    if not cands:
        return []
    simulate_and_score(cands, pool, sims=n_sims, leverage=leverage, seed=seed)
    return select_final(cands, n, max_exposure)


def build_cash(players):
    """Simple max-projection valid lineup (kept for completeness)."""
    pool = [p for p in players if p.proj > 0]
    best = build_candidates(pool, 300, stack=1, max_per_team=6, seed=1)
    if not best:
        return None
    return max(best, key=lambda l: l.proj)
