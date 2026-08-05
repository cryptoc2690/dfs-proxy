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
        """DK upload order: F, F, F, G, G, UTIL."""
        g = sorted((p for p in self.players if p.is_guard), key=lambda p: -p.proj)
        f = sorted((p for p in self.players if not p.is_guard), key=lambda p: -p.proj)
        slotted = f[:MIN_FORWARDS] + g[:MIN_GUARDS]          # 3 F, 2 G
        rest = sorted(g[MIN_GUARDS:] + f[MIN_FORWARDS:], key=lambda p: -p.proj)
        return slotted + rest[:ROSTER_SIZE - MIN_FORWARDS - MIN_GUARDS]  # + UTIL


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


def _build_one(pool, max_per_team, rng, cores=None, min_cores=0):
    # Seed the lineup with the required number of cores, then fill the rest with
    # a position-aware greedy that always keeps the G/F minimums reachable.
    # (Extra cores can still land in the fill — min_cores is a floor.)
    picked = []
    if cores and min_cores > 0:
        avail = [c for c in cores if c.proj > 0]
        k = min(min_cores, len(avail))
        if k > 0:
            picked = list(rng.sample(avail, k))
    used = {p.dk_id for p in picked}
    salary = sum(p.salary for p in picked)
    team_count = {}
    for p in picked:
        team_count[p.team] = team_count.get(p.team, 0) + 1
    if salary > SALARY_CAP or any(v > max_per_team for v in team_count.values()):
        return None

    while len(picked) < ROSTER_SIZE:
        remaining = ROSTER_SIZE - len(picked)
        g = sum(1 for p in picked if p.is_guard)
        f = len(picked) - g
        need_g, need_f = max(0, MIN_GUARDS - g), max(0, MIN_FORWARDS - f)
        must_guard = need_g >= remaining
        must_forward = need_f >= remaining
        budget = SALARY_CAP - salary - MIN_SALARY * (remaining - 1)
        elig = []
        for p in pool:
            if p.dk_id in used or p.salary > budget:
                continue
            if team_count.get(p.team, 0) >= max_per_team:
                continue
            if (must_guard and not p.is_guard) or (must_forward and p.is_guard):
                continue
            ng = need_g - (1 if p.is_guard and need_g else 0)
            nf = need_f - (1 if not p.is_guard and need_f else 0)
            if max(0, ng) + max(0, nf) > remaining - 1:
                continue
            elig.append(p)
        if not elig:
            return None
        p = _weighted_pick(elig, rng)
        picked.append(p)
        used.add(p.dk_id)
        salary += p.salary
        team_count[p.team] = team_count.get(p.team, 0) + 1

    g = sum(1 for p in picked if p.is_guard)
    if g < MIN_GUARDS or (len(picked) - g) < MIN_FORWARDS or salary > SALARY_CAP:
        return None
    return picked


def _has_stack(players, stack):
    counts = {}
    for p in players:
        counts[p.game] = counts.get(p.game, 0) + 1
    return any(c >= stack for c in counts.values())


def build_candidates(pool, count, *, stack, max_per_team, seed=0,
                     cores=None, min_cores=0):
    rng = random.Random(seed)
    out, seen = [], set()
    tries = 0
    while len(out) < count and tries < count * 15:
        tries += 1
        lu = _build_one(pool, max_per_team, rng, cores, min_cores)
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


def select_final(cands, n, max_exposure, max_overlap=4):
    """Pick the final N, best-first, under a per-player exposure cap AND a
    pairwise-overlap cap so the set is genuinely differentiated (WNBA's whole
    game once everyone shares the same projections). Backfills if the
    constraints starve the set, so we always return N."""
    cap = max(1, round(max_exposure * n))
    counts, final, final_sets = {}, [], []

    def add(c):
        final.append(c)
        final_sets.append(set(c.ids()))
        for i in c.ids():
            counts[i] = counts.get(i, 0) + 1

    def exposure_ok(c):
        return not any(counts.get(i, 0) >= cap for i in c.ids())

    for c in cands:
        if len(final) >= n:
            break
        if not exposure_ok(c):
            continue
        if any(len(set(c.ids()) & s) > max_overlap for s in final_sets):
            continue
        add(c)
    for c in cands:  # relax overlap, keep exposure
        if len(final) >= n:
            break
        if c not in final and exposure_ok(c):
            add(c)
    for c in cands:  # last resort: fill to N
        if len(final) >= n:
            break
        if c not in final:
            add(c)
    return final[:n]


# ---------------- public API ----------------
def build_gpp(players, *, n=20, pool_size=None, min_stack=2, max_per_team=4,
              max_exposure=0.6, leverage=0.35, n_sims=5000, seed=0,
              cores=None, min_cores=0, max_overlap=4):
    pool = [p for p in players if p.proj > 0]
    if len(pool) < ROSTER_SIZE:
        return []
    pool = _viable_pool(pool, n)
    cores = [c for c in (cores or []) if c.proj > 0]
    pool_size = pool_size or max(120, n * 8)
    kw = dict(max_per_team=max_per_team, seed=seed, cores=cores, min_cores=min_cores)
    cands = build_candidates(pool, pool_size, stack=min_stack, **kw)
    if not cands:  # relax the stack requirement rather than return nothing
        cands = build_candidates(pool, pool_size, stack=1, **kw)
    if not cands:
        return []
    simulate_and_score(cands, pool, sims=n_sims, leverage=leverage, seed=seed)
    return select_final(cands, n, max_exposure, max_overlap)


def _viable_pool(pool, n):
    """Dynamic 'no minutes-punts' filter, fully slate-driven.

    Winning WNBA lineups need every slot to have a real path to a useful score,
    so we keep players by UPSIDE (ceiling), not median — a cheap starter with a
    24-ceiling stays; a low-minutes body with a 9-ceiling is cut. The cutoff is
    dynamic: we keep the top slice of the pool by ceiling, and the slice gets
    DEEPER on bigger slates (more games -> more players -> the bar naturally
    rises because the slice is a fraction of a larger pool). On a thin slate the
    slice is small, so a marginal value play survives only if the slate is
    genuinely that shallow. Finally we guarantee a legal, affordable lineup
    still fits — expanding the pool just enough if the top slice can't.
    """
    by_ceil = sorted(pool, key=lambda p: -p.ceil)
    depth = min(len(by_ceil), max(18, round(len(by_ceil) * 0.6)))
    kept = by_ceil[:depth]
    while not _can_field(kept) and depth < len(by_ceil):
        depth += 1
        kept = by_ceil[:depth]
    return kept


def _can_field(pool):
    """Cheapest legal roster (2 G + 3 F + 1 flex) fits under the cap?"""
    g = sorted((p.salary for p in pool if p.is_guard))
    f = sorted((p.salary for p in pool if not p.is_guard))
    if len(g) < MIN_GUARDS or len(f) < MIN_FORWARDS:
        return False
    rest = sorted(g[MIN_GUARDS:] + f[MIN_FORWARDS:])
    need = g[:MIN_GUARDS] + f[:MIN_FORWARDS] + rest[:ROSTER_SIZE - MIN_GUARDS - MIN_FORWARDS]
    return len(need) == ROSTER_SIZE and sum(need) <= SALARY_CAP
