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

# Team-correlation control. Four underowned starters on ONE team look
# independently great but ride a single game script — when that team lays an egg
# the whole pool sinks together (the TOR wound). Across the pool, a team may hold
# up to this multiple of its EVEN share of roster slots (even share = 1/#teams).
# Data-driven: it loosens automatically as the slate adds teams and only bites on
# small, lopsided slates where the pile-on actually hurts.
TEAM_SHARE_MULT = 1.6


class Lineup:
    def __init__(self, players: list[Player]):
        self.players = players
        self.metrics: dict[str, float] = {}
        self.alt: "Lineup | None" = None  # pool-legal alternative (P2), if any

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
    # Weight by projection; reliability is handled structurally by the risk-body
    # cap (see _build_one), not by nudging weights here — a weight nudge always
    # loses to a real projection gap, so keep this clean.
    weights = [max(p.proj, 0.1) ** 3 * (0.6 + 0.8 * rng.random()) for p in cands]
    total = sum(weights)
    x = rng.random() * total
    for p, w in zip(cands, weights):
        x -= w
        if x <= 0:
            return p
    return cands[-1]


def _build_one(pool, max_per_team, rng, cores=None, min_cores=0, reserve=MIN_SALARY,
               max_off_pool=None, max_risk=None):
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
    if max_off_pool is not None and sum(1 for p in picked if not p.in_pool) > max_off_pool:
        return None

    def _risk(p):  # a bust-prone body; cores are anchors, never counted
        return p.risk and not p.core

    while len(picked) < ROSTER_SIZE:
        remaining = ROSTER_SIZE - len(picked)
        g = sum(1 for p in picked if p.is_guard)
        f = len(picked) - g
        need_g, need_f = max(0, MIN_GUARDS - g), max(0, MIN_FORWARDS - f)
        must_guard = need_g >= remaining
        must_forward = need_f >= remaining
        budget = SALARY_CAP - salary - reserve * (remaining - 1)
        off_pool_used = (sum(1 for p in picked if not p.in_pool)
                         if max_off_pool is not None else 0)
        risk_used = (sum(1 for p in picked if _risk(p))
                     if max_risk is not None else 0)
        elig = []
        for p in pool:
            if p.dk_id in used or p.salary > budget:
                continue
            if team_count.get(p.team, 0) >= max_per_team:
                continue
            if max_off_pool is not None and not p.in_pool and off_pool_used >= max_off_pool:
                continue
            if max_risk is not None and _risk(p) and risk_used >= max_risk:
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
                     cores=None, min_cores=0, reserve=MIN_SALARY, max_off_pool=None,
                     max_risk=None):
    rng = random.Random(seed)
    out, seen = [], set()
    tries = 0
    while len(out) < count and tries < count * 15:
        tries += 1
        lu = _build_one(pool, max_per_team, rng, cores, min_cores, reserve,
                        max_off_pool, max_risk)
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
    # Leverage on OVER-ownership (ownership per point of ceiling), not raw
    # ownership. Raw ownership can't tell good chalk (high-owned because it's the
    # best play — a big ceiling backs it) from overpriced chalk (high-owned with
    # no ceiling to show for it). Dividing by ceiling fades only the latter, and
    # stops a cheap, low-owned, low-ceiling body from reading as "leverage" just
    # because it's unpopular — it's unpopular because it's worse. Differentiation
    # still comes from the overlap + exposure caps in select_final, not here.
    effs = [c.total_own / (c.metrics["ceiling"] or 1.0) for c in cands] or [0]
    lo, hi = min(effs), max(effs)
    span = (hi - lo) or 1.0
    for c, eff in zip(cands, effs):
        on = (eff - lo) / span
        c.metrics["score"] = c.metrics["ceiling"] * (1 + leverage * (1 - 2 * on))
    cands.sort(key=lambda c: -c.metrics["score"])


def select_final(cands, n, max_exposure, max_overlap=4, player_caps=None,
                 max_team_slots=None, core_floors=None):
    """Pick the final N, best-first, under a per-player exposure cap, a
    pairwise-overlap cap, and a pool-level team-slot cap so the set is genuinely
    differentiated AND not quietly piled onto one team (WNBA's whole game once
    everyone shares the same projections). Backfills if the constraints starve
    the set, so we always return N. player_caps overrides the global cap for
    specific dk_ids (rein in one heavy play without touching the rest — matters
    on short slates where a global cap would hobble the studs). core_floors
    guarantees each core dk_id a minimum number of lineups — a play the sharp
    believes in can't get squeezed out (and their conviction outranks the team
    cap, so a core on a capped team still gets its floor)."""
    cap = max(1, round(max_exposure * n))
    player_caps = player_caps or {}
    max_team_slots = max_team_slots or {}
    counts, team_slots, final, final_sets = {}, {}, [], []

    def add(c):
        final.append(c)
        final_sets.append(set(c.ids()))
        for p in c.players:
            counts[p.dk_id] = counts.get(p.dk_id, 0) + 1
            team_slots[p.team] = team_slots.get(p.team, 0) + 1

    def exposure_ok(c):
        return not any(counts.get(i, 0) >= player_caps.get(i, cap) for i in c.ids())

    def team_ok(c):
        if not max_team_slots:
            return True
        need = {}
        for p in c.players:
            need[p.team] = need.get(p.team, 0) + 1
        return all(team_slots.get(t, 0) + k <= max_team_slots.get(t, ROSTER_SIZE * len(cands))
                   for t, k in need.items())

    for c in cands:  # exposure + overlap + team cap
        if len(final) >= n:
            break
        if not exposure_ok(c) or not team_ok(c):
            continue
        if any(len(set(c.ids()) & s) > max_overlap for s in final_sets):
            continue
        add(c)
    for c in cands:  # relax overlap, keep exposure + team cap
        if len(final) >= n:
            break
        if c not in final and exposure_ok(c) and team_ok(c):
            add(c)
    for c in cands:  # relax team cap, keep exposure
        if len(final) >= n:
            break
        if c not in final and exposure_ok(c):
            add(c)
    for c in cands:  # last resort: fill to N
        if len(final) >= n:
            break
        if c not in final:
            add(c)
    final = final[:n]
    if core_floors:  # guarantee each core its minimum presence (overrides team cap)
        final = _enforce_core_floors(final, cands, core_floors)
    final.sort(key=lambda c: -c.metrics.get("score", 0))
    return final


def _enforce_core_floors(final, cands, core_floors):
    """Top up under-exposed cores to their floor. For each core below its target,
    pull the best-scoring candidate that features it (cands is score-sorted) and
    drop the weakest chosen lineup that lacks it — but never one whose removal
    would knock another core back under its own floor. Best-effort: stops when no
    swap is available rather than looping forever."""
    final = list(final)

    def count(core_id):
        return sum(1 for lu in final if core_id in lu.ids())

    for core_id, need in core_floors.items():
        while count(core_id) < need:
            cand = next((c for c in cands
                         if core_id in c.ids() and c not in final), None)
            if cand is None:
                break
            drop = None
            for lu in reversed(final):  # weakest-last -> reversed hits it first
                if core_id in lu.ids():
                    continue
                safe = True
                for oid, oneed in core_floors.items():
                    if oid != core_id and oid in lu.ids() and count(oid) - 1 < oneed:
                        safe = False
                        break
                if safe:
                    drop = lu
                    break
            if drop is None:
                break
            final.remove(drop)
            final.append(cand)
    return final


# ---------------- pool-legal alternative (P2) ----------------
def _complete_in_pool(kept, pool, max_per_team):
    """Hold the in-pool players of an off-pool lineup and re-fill the vacated
    slots using ONLY in-pool players — a minimal-change, pool-legal version of
    the same lineup. Greedy by projection, keeping the roster legal and under
    cap. Returns 6 players or None if no legal all-in-pool repair exists."""
    picked = list(kept)
    used = {p.dk_id for p in picked}
    inpool = sorted((p for p in pool if p.in_pool and p.dk_id not in used),
                    key=lambda p: -p.proj)
    while len(picked) < ROSTER_SIZE:
        remaining = ROSTER_SIZE - len(picked)
        g = sum(1 for p in picked if p.is_guard)
        f = len(picked) - g
        need_g, need_f = max(0, MIN_GUARDS - g), max(0, MIN_FORWARDS - f)
        must_guard = need_g >= remaining
        must_forward = need_f >= remaining
        salary = sum(p.salary for p in picked)
        budget = SALARY_CAP - salary - MIN_SALARY * (remaining - 1)
        team_count = {}
        for p in picked:
            team_count[p.team] = team_count.get(p.team, 0) + 1
        pick = None
        for p in inpool:
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
            pick = p
            break
        if pick is None:
            return None
        picked.append(pick)
        used.add(pick.dk_id)
    g = sum(1 for p in picked if p.is_guard)
    if g < MIN_GUARDS or (len(picked) - g) < MIN_FORWARDS or sum(p.salary for p in picked) > SALARY_CAP:
        return None
    return picked


def _attach_pool_alternatives(lineups, pool, max_per_team, n_sims, leverage, seed):
    """For every final lineup that spent an off-pool slot, attach the best
    pool-legal alternative (simulated on the same footing so its ceiling is
    comparable) for the UI to reveal/swap in."""
    alts = []
    for lu in lineups:
        if all(p.in_pool for p in lu.players):
            continue
        repaired = _complete_in_pool([p for p in lu.players if p.in_pool],
                                     pool, max_per_team)
        if repaired:
            lu.alt = Lineup(repaired)
            alts.append(lu.alt)
    if alts:
        simulate_and_score(alts, pool, sims=n_sims, leverage=leverage, seed=seed)


# ---------------- public API ----------------
def build_gpp(players, *, n=20, pool_size=None, min_stack=2, max_per_team=3,
              max_exposure=0.6, leverage=0.35, n_sims=5000, seed=0,
              cores=None, min_cores=0, max_overlap=4, max_off_pool=None,
              stars_and_scrubs=None, max_risk=None, max_leftover=1000, player_caps=None):
    playable = [p for p in players if p.proj > 0]
    if len(playable) < ROSTER_SIZE:
        return []
    pool = _viable_pool(playable, n)
    if max_off_pool is not None:
        # The pool constraint is only meaningful if every pool member is
        # available to build with, so never let the ceiling filter trim one.
        seen = {id(p) for p in pool}
        pool += [p for p in playable if p.in_pool and id(p) not in seen]
    cores = [c for c in (cores or []) if c.proj > 0]
    pool_size = pool_size or max(120, n * 8)
    # Salary-aware construction: stars-and-scrubs is only right when a CHEAP
    # player actually projects. If cheap value exists, reserve less per slot so
    # the build can pay up + use it; if not, reserve more so it spreads into
    # mid-range instead of punting two slots into 9-point dead weight. The caller
    # passes the slate read so the engine's reserve and the UI's badge are the
    # SAME determination; None -> decide it here (direct/standalone calls).
    if stars_and_scrubs is None:
        cheap_best = max((p.proj for p in pool if p.salary <= 5500), default=0.0)
        stars_and_scrubs = cheap_best >= 16
    reserve = 4200 if stars_and_scrubs else 6000
    kw = dict(max_per_team=max_per_team, seed=seed, cores=cores,
              min_cores=min_cores, reserve=reserve, max_off_pool=max_off_pool,
              max_risk=max_risk)
    cands = build_candidates(pool, pool_size, stack=min_stack, **kw)
    if not cands:  # relax the stack requirement rather than return nothing
        cands = build_candidates(pool, pool_size, stack=1, **kw)
    if not cands and max_risk is not None:
        # Not enough reliable bodies to fill under this cap (thin slate) — loosen
        # the risk allowance one at a time before giving up.
        for r in range(max_risk + 1, ROSTER_SIZE + 1):
            cands = build_candidates(pool, pool_size, stack=1, **dict(kw, max_risk=r))
            if cands:
                break
    if not cands and max_off_pool is not None:
        # Pool too thin to field legal lineups at this cap — loosen it one at a
        # time (0 -> 1 -> ... -> unconstrained) rather than return nothing.
        for relaxed in range(max_off_pool + 1, ROSTER_SIZE + 1):
            kw2 = dict(kw, max_off_pool=(None if relaxed >= ROSTER_SIZE else relaxed),
                       max_risk=None)
            cands = build_candidates(pool, pool_size, stack=1, **kw2)
            if cands:
                break
    if not cands:
        return []
    # Salary floor: leaving money on the table usually means points left on it
    # too. Keep only lineups that spend within max_leftover of the cap — but only
    # if enough survive to still build a differentiated set (else the slate can't
    # support it and we take what we've got).
    if max_leftover is not None:
        floor = SALARY_CAP - max_leftover
        spent = [c for c in cands if c.salary >= floor]
        if len(spent) >= n:
            cands = spent
    simulate_and_score(cands, pool, sims=n_sims, leverage=leverage, seed=seed)
    # Team-correlation cap: a team may hold up to TEAM_SHARE_MULT x its even share
    # of roster slots across the whole pool (even share = 1/#teams). Scales with
    # the slate — loose on big boards, firm on small lopsided ones.
    teams = {p.team for p in pool if p.team}
    max_team_slots = None
    if len(teams) >= 2:
        even = n * ROSTER_SIZE / len(teams)
        cap_slots = int(math.ceil(even * TEAM_SHARE_MULT))
        max_team_slots = {t: cap_slots for t in teams}
    # Core-exposure floor: every core the sharp set is guaranteed at least this
    # many lineups so a conviction play can't get squeezed to 1 of N. Data-driven
    # from slate shape — more cores spread the floor thinner, more lineups raise
    # it — never a hardcoded number.
    core_floors = None
    if cores and min_cores > 0:
        floor_ct = int(math.ceil(n / (len(cores) + 1)))
        if floor_ct >= 1:
            core_floors = {c.dk_id: floor_ct for c in cores}
    final = select_final(cands, n, max_exposure, max_overlap, player_caps,
                         max_team_slots=max_team_slots, core_floors=core_floors)
    if max_off_pool:  # 0 or None -> every lineup is already all-in-pool
        _attach_pool_alternatives(final, pool, max_per_team, n_sims, leverage, seed)
    return final


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
