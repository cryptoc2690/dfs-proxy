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

# DraftKings Classic requires players from at least two different games, so a
# roster can never be more than ROSTER_SIZE-1 from one game. This is a contest
# rule, not a preference — an all-one-game lineup is rejected at upload.
MAX_PER_GAME = ROSTER_SIZE - 1

# Team-correlation control. Four underowned starters on ONE team look
# independently great but ride a single game script — when that team lays an egg
# the whole pool sinks together (the TOR wound). Across the pool, a team may hold
# up to this multiple of its EVEN share of roster slots (even share = 1/#teams).
# Data-driven: it loosens automatically as the slate adds teams and only bites on
# small, lopsided slates where the pile-on actually hurts.
TEAM_SHARE_MULT = 1.6

# --- construction rules from the 24-contest / 68,380-lineup review -------
#
# Sub-10%-owned players. 58% of top-1% lineups carried ZERO of them against 37%
# of the field and 30% of ours, and each extra one lowered top-1%, top-10% and
# cash — still true at MATCHED lineup projection, so it isn't just "those lineups
# were worse". The gradient by count of sub-10% players:
#
#   0 -> top-1% 2.01%, cash 29.2%      2  -> 0.69%, 16.0%
#   1 -> 1.00%, 22.0%                  3+ -> 0.64%,  9.2%
#
# Cores are exempt: a conviction play the sharp set is never the thing we cut.
SUB10_OWN = 10.0
MAX_SUB10 = 1

# At least one stud. Lineups with no $10k+ player are 4.7% of the field and reach
# top-1% at 0.34% against 1.10% for two-stud lineups, and the penalty survives
# controls for projection and ownership (slate-FE logit coef -1.02, p=0.004).
# Relaxed automatically when a slate simply has no player this expensive.
STUD_SALARY = 10_000

# Two-game slates have the most consistent rules in the whole study, so they are
# hard constraints rather than preferences:
#   * the balanced 3-3 game split is the WORST construction on the board —
#     cash 17.2% vs 23.0% (4-2) vs 28.9% (5-1), and 4-2 beat 3-3 in 7 of 7 slates
#   * a 3+ block from one team with NO player from its opponent went 0-for-2,254
#     on top-1% finishes (about 22 expected at the field rate)
#   * putting the majority in the game with the higher projected-ownership sum
#     paid in 7 of 7 slates on cash: 29.8% vs 12.3%


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
        """DK upload order: G, G, F, F, F, UTIL — matching the DK entries file."""
        g = sorted((p for p in self.players if p.is_guard), key=lambda p: -p.proj)
        f = sorted((p for p in self.players if not p.is_guard), key=lambda p: -p.proj)
        slotted = g[:MIN_GUARDS] + f[:MIN_FORWARDS]          # 2 G, 3 F
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


# Correlation seeding. A 7-slate, ~20,600-lineup review found this is where
# top-1% finishes come from, and that our builder was leaving it on the table:
#
#   3 from one team, team implied total >= 84 -> 2.65% top-1% (vs 1.04% at 2)
#   3 from one team, team implied total <  84 -> 0.40% top-1% (WORSE than 2)
#   5 from one game                           -> 7.03% top-1% (vs 0.98% at 4)
#
# So it was never "stacking helps" — it's "concentrate on high-total offences".
# Projection-weighted construction almost never produces these by accident (we
# measured ~3 of 20 lineups reaching even an unconditioned 3-stack), so a share
# of lineups is now SEEDED with a stack instead of hoping one shows up.
# The 22-slate review re-measured these cells on ~4x the sample. The 5-man game
# stack regressed hard (7.03% -> 2.76%) and only holds its edge in a genuinely
# big game (>=178 combined implied -> 3.83%, below that 1.81%), so the game stack
# now has to clear that bar and the mix leans a little more on team stacks.
STACK_SHARE = 0.5          # fraction of lineups built around a deliberate stack
STACK_GAME_FRACTION = 0.4  # of those, how many go for a game stack vs a team stack
STACK_GAME_SIZE = 5        # players from one game
STACK_TEAM_SIZE = 3        # players from one team (the 2.65% cell)
STACK_GAME_MIN_TOTAL = 178.0  # combined implied a game must reach to be worth stacking


def _slate_rules(pool):
    """The slate-shape facts the construction rules key off, worked out once.

    major_game is the game with the higher summed projected ownership — on all
    seven two-game slates in the study that was also the game the field ended up
    more heavily on, and the side the winners loaded."""
    games = {}
    for p in pool:
        if p.game:
            games[p.game] = games.get(p.game, 0.0) + p.ownership
    two_game = len(games) == 2
    major = max(games, key=lambda g: games[g]) if two_game else None
    return {
        "two_game": two_game,
        "major_game": major,
        "has_stud": any(p.salary >= STUD_SALARY for p in pool),
    }


def _rules_ok(picked, rules, max_sub10):
    """Final check against the review's construction rules. Runs on a complete
    roster, so it can see the whole shape."""
    if rules["has_stud"] and not any(p.salary >= STUD_SALARY for p in picked):
        return False
    if max_sub10 is not None:
        thin = sum(1 for p in picked if p.ownership < SUB10_OWN and not p.core)
        if thin > max_sub10:
            return False
    if rules["two_game"]:
        games = {}
        for p in picked:
            games[p.game] = games.get(p.game, 0) + 1
        big_game, big_ct = max(games.items(), key=lambda kv: kv[1])
        if big_ct < 4:                          # kills the 3-3 split
            return False
        if rules["major_game"] and big_game != rules["major_game"]:
            return False
        teams = {}
        for p in picked:
            teams[p.team] = teams.get(p.team, 0) + 1
        for t, ct in teams.items():             # 3+ from a team needs a bring-back
            if ct >= 3:
                opp = next((q.opponent for q in picked if q.team == t), "")
                if not any(q.team == opp for q in picked):
                    return False
    return True


def _stack_targets(pool):
    """Rank teams by implied total and games by combined total, so seeding aims
    at real offences rather than any old cluster."""
    team_total, game_total = {}, {}
    for p in pool:
        if p.implied > 0:
            team_total[p.team] = p.implied
    for p in pool:
        if p.game and p.team in team_total:
            game_total.setdefault(p.game, set()).add(p.team)
    games = {g: sum(team_total.get(t, 0) for t in ts) for g, ts in game_total.items()}
    teams = sorted(team_total.items(), key=lambda kv: -kv[1])
    return teams, sorted(games.items(), key=lambda kv: -kv[1])


def _seed_stack(pool, plan, rng, used, team_count, game_count, max_per_team, salary_left):
    """Pick the stack members up front. Position legality is left to the main
    fill — we only take players that still leave a legal roster reachable."""
    kind, key, size = plan
    group = [p for p in pool
             if (p.team == key if kind == "team" else p.game == key)
             and p.dk_id not in used and p.proj > 0]
    if len(group) < size:
        return []
    # A game stack splits across two teams, and which side gets the bigger half
    # decides whether we end up with a good 3-stack or the field's worst one (a
    # 3-stack of a low-total team). Hold the weaker offence to the small half.
    caps = {}
    if kind == "game":
        sides = {}
        for p in group:
            sides[p.team] = max(sides.get(p.team, 0), p.implied)
        if len(sides) == 2:
            weak = min(sides, key=sides.get)
            caps[weak] = max(0, size - max_per_team)
    picked = []
    for _ in range(size):
        elig = [p for p in group
                if p.dk_id not in used
                and team_count.get(p.team, 0) < max_per_team
                and sum(1 for q in picked if q.team == p.team) < caps.get(p.team, size)
                and game_count.get(p.game, 0) < MAX_PER_GAME
                and p.salary <= salary_left - MIN_SALARY * (ROSTER_SIZE - len(used) - 1)]
        # never take so many of one position that the roster can't be completed
        g = sum(1 for p in picked if p.is_guard)
        f = len(picked) - g
        if g >= MIN_GUARDS + 1:
            elig = [p for p in elig if not p.is_guard]
        if f >= MIN_FORWARDS + 1:
            elig = [p for p in elig if p.is_guard]
        if not elig:
            break
        p = _weighted_pick(elig, rng)
        picked.append(p)
        used.add(p.dk_id)
        team_count[p.team] = team_count.get(p.team, 0) + 1
        game_count[p.game] = game_count.get(p.game, 0) + 1
        salary_left -= p.salary
    return picked


def _build_one(pool, max_per_team, rng, cores=None, min_cores=0, reserve=MIN_SALARY,
               max_off_pool=None, plan=None, rules=None, max_sub10=MAX_SUB10):
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
    team_count, game_count = {}, {}
    for p in picked:
        team_count[p.team] = team_count.get(p.team, 0) + 1
        game_count[p.game] = game_count.get(p.game, 0) + 1
    if salary > SALARY_CAP or any(v > max_per_team for v in team_count.values()):
        return None
    if any(v > MAX_PER_GAME for v in game_count.values()):
        return None
    if max_off_pool is not None and sum(1 for p in picked if not p.in_pool) > max_off_pool:
        return None

    # Seed the correlation stack before the generic fill, so the lineup is built
    # AROUND it rather than hoping one emerges from projection weighting.
    if plan and len(picked) < ROSTER_SIZE:
        seeded = _seed_stack(pool, plan, rng, used, team_count, game_count,
                             max_per_team, SALARY_CAP - salary)
        picked += seeded
        salary += sum(p.salary for p in seeded)
        if salary > SALARY_CAP:
            return None
        if max_off_pool is not None and sum(1 for p in picked if not p.in_pool) > max_off_pool:
            return None

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
        thin_used = (sum(1 for p in picked if p.ownership < SUB10_OWN and not p.core)
                     if max_sub10 is not None else 0)
        elig = []
        for p in pool:
            if p.dk_id in used or p.salary > budget:
                continue
            if team_count.get(p.team, 0) >= max_per_team:
                continue
            if game_count.get(p.game, 0) >= MAX_PER_GAME:
                continue
            if max_off_pool is not None and not p.in_pool and off_pool_used >= max_off_pool:
                continue
            if (max_sub10 is not None and thin_used >= max_sub10
                    and p.ownership < SUB10_OWN and not p.core):
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
        game_count[p.game] = game_count.get(p.game, 0) + 1

    g = sum(1 for p in picked if p.is_guard)
    if g < MIN_GUARDS or (len(picked) - g) < MIN_FORWARDS or salary > SALARY_CAP:
        return None
    if len({p.game for p in picked}) < 2:   # DK contest rule, never relaxed
        return None
    if rules and not _rules_ok(picked, rules, max_sub10):
        return None
    return picked


def _has_stack(players, stack):
    counts = {}
    for p in players:
        counts[p.game] = counts.get(p.game, 0) + 1
    return any(c >= stack for c in counts.values())


def build_candidates(pool, count, *, stack, max_per_team, seed=0,
                     cores=None, min_cores=0, reserve=MIN_SALARY, max_off_pool=None,
                     stack_share=STACK_SHARE, rules=None, max_sub10=MAX_SUB10):
    rng = random.Random(seed)
    if rules is None:
        rules = _slate_rules(pool)
    teams, games = _stack_targets(pool)
    # Only high-total offences are worth concentrating on — the review found a
    # 3-stack of a LOW-total team finishes worse than not stacking at all.
    med = sorted(v for _, v in teams)[len(teams) // 2] if teams else 0
    hi_teams = [t for t, v in teams if v >= med]
    out, seen = [], set()
    tries = 0
    while len(out) < count and tries < count * 15:
        tries += 1
        plan = None
        if stack_share and rng.random() < stack_share:
            top_game = games[0] if games else None
            # On a two-game slate the majority has to sit in the higher-owned
            # game anyway, so point the stack there rather than fighting the rule.
            if rules["two_game"] and rules["major_game"]:
                top_game = next((g for g in games if g[0] == rules["major_game"]),
                                top_game)
            if (top_game and top_game[1] >= STACK_GAME_MIN_TOTAL
                    and rng.random() < STACK_GAME_FRACTION):
                plan = ("game", top_game[0], STACK_GAME_SIZE)
            elif hi_teams:
                plan = ("team", hi_teams[rng.randrange(min(2, len(hi_teams)))],
                        STACK_TEAM_SIZE)
        lu = _build_one(pool, max_per_team, rng, cores, min_cores, reserve,
                        max_off_pool, plan, rules, max_sub10)
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


# How much of the ranking is upside vs production. We used to rank on the 85th
# percentile alone. The review found the projection-ceiling column is close to a
# rescaled projection (r 0.93 with it) that ranks actual scores slightly WORSE
# than the projection does, and that a lineup's summed ceiling is the weakest of
# the six pre-lock signals tested. So upside keeps half the weight — this is
# still a GPP tool and the payout curve is top-heavy — and production takes the
# other half instead of riding along for free.
CEILING_WEIGHT = 0.5

# Lineups in the bottom ownership quintile of their own slate are the one group
# that is reliably bad: top-1% 0.39% and cash 9.8%, against 0.82% / 21.9% at the
# middle quintile, and "beats the bottom quintile" held in 19-22 of 23 contests
# at every tier — the most consistent step on the whole ownership curve. We drop
# that slice outright rather than trusting a soft tilt to avoid it.
OWN_FLOOR_PCTILE = 0.20


def simulate_and_score(cands, pool, *, sims, own_lean=0.0, seed=0):
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
    # Ownership lean on RAW lineup ownership. POSITIVE now means lean toward the
    # consensus, which is the direction three successive reviews have pointed.
    #
    # The slider used to only fade. It first divided ownership by ceiling to flag
    # "overpriced chalk", a metric that separated the exact opposite of what it
    # was built to separate (own/ceiling correlated +0.23 with realised value).
    # That went, and the tilt was set neutral. The 24-contest study then showed
    # neutral is still on the wrong side of the line: sorted by within-slate
    # ownership, the chalkiest quintile hit top-1% at 3.04% and cashed 36.3%
    # against 0.39% and 9.8% for the least-owned, holding in 19 of 23 contests,
    # while OUR lineups averaged the 42nd ownership percentile against the
    # top-1% tier's 70th.
    #
    # The lean is deliberately modest, because the same study found ownership
    # adds nothing ONCE consensus projection is controlled for — it is a proxy
    # for consensus quality, not an independent edge. The blended projection is
    # what does the real work; this only stops us drifting to the wrong side of
    # the field, and OWN_FLOOR_PCTILE does the part that is actually reliable.
    owns = [c.total_own for c in cands] or [0]
    lo, hi = min(owns), max(owns)
    span = (hi - lo) or 1.0
    for c in cands:
        on = (c.total_own - lo) / span
        base = ((1 - CEILING_WEIGHT) * c.metrics["mean"]
                + CEILING_WEIGHT * c.metrics["ceiling"])
        c.metrics["base"] = round(base, 2)
        c.metrics["score"] = base * (1 + own_lean * (2 * on - 1))
    cands.sort(key=lambda c: -c.metrics["score"])


def select_final(cands, n, max_exposure, max_overlap=4, player_caps=None,
                 max_team_slots=None, core_floors=None, backfill=None):
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
    if core_floors:
        # Guarantee each core its minimum presence. This draws from `backfill` —
        # every candidate we built, not the filtered shortlist — because the
        # filters upstream are data preferences and a core is the user's own
        # conviction. A low-owned core would otherwise be cut by the ownership
        # floor before it ever reached the floor logic, which is precisely the
        # bug that once buried a cored player at 1-of-N.
        final = _enforce_core_floors(final, backfill or cands, core_floors)
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


def _attach_pool_alternatives(lineups, pool, max_per_team, n_sims, own_lean, seed):
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
        simulate_and_score(alts, pool, sims=n_sims, own_lean=own_lean, seed=seed)


# ---------------- public API ----------------
def build_gpp(players, *, n=20, pool_size=None, min_stack=2, max_per_team=3,
              max_exposure=0.6, own_lean=0.0, n_sims=5000, seed=0,
              cores=None, min_cores=0, max_overlap=4, max_off_pool=None,
              stars_and_scrubs=None, max_leftover=700, player_caps=None,
              stack_share=STACK_SHARE, max_sub10=MAX_SUB10, slate_rules=True):
    # Reliability gate (not a grade). Back-testing 5 slates showed minutes and
    # stat-stuffer had ZERO correlation with bust rate — grading/rationing them
    # bought nothing. All they cleanly flag is genuine non-rotation risk, so we
    # GATE those out (p.risk == projected minutes under the floor) rather than
    # ration them. Cores are exempt — the sharp can still force a deep-bench dart.
    # Falls back to the ungated pool if the gate would make the slate unfieldable.
    full = [p for p in players if p.proj > 0]
    gated = [p for p in full if not (p.risk and not p.core)]
    playable = gated if _can_field(gated) else full
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
    rules = _slate_rules(pool) if slate_rules else None
    kw = dict(max_per_team=max_per_team, seed=seed, cores=cores,
              min_cores=min_cores, reserve=reserve, max_off_pool=max_off_pool,
              stack_share=stack_share, rules=rules, max_sub10=max_sub10)
    cands = build_candidates(pool, pool_size, stack=min_stack, **kw)
    if not cands:  # relax the stack requirement rather than return nothing
        cands = build_candidates(pool, pool_size, stack=1, **kw)
    if len(cands) < n:
        # The construction rules are strong enough to starve a thin slate. Peel
        # them back in order of how well the data supports them — the sub-10%
        # cap is the softest, the two-game shape rules the firmest — instead of
        # returning fewer lineups than asked for.
        for relaxed in (dict(kw, max_sub10=None),
                        dict(kw, max_sub10=None, rules=None)):
            more = build_candidates(pool, pool_size, stack=1, **relaxed)
            if len(more) > len(cands):
                cands = more
            if len(cands) >= n:
                break
    if not cands and max_off_pool is not None:
        # Pool too thin to field legal lineups at this cap — loosen it one at a
        # time (0 -> 1 -> ... -> unconstrained) rather than return nothing.
        for relaxed in range(max_off_pool + 1, ROSTER_SIZE + 1):
            kw2 = dict(kw, max_off_pool=(None if relaxed >= ROSTER_SIZE else relaxed),
                       max_sub10=None, rules=None)
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
    simulate_and_score(cands, pool, sims=n_sims, own_lean=own_lean, seed=seed)
    # Drop the bottom ownership slice of our OWN candidate pool — the one part of
    # the ownership curve that is reliable slate after slate. Only when enough
    # lineups survive to still build a differentiated set.
    scored_all = list(cands)
    if OWN_FLOOR_PCTILE and len(cands) > n:
        by_own = sorted(cands, key=lambda c: c.total_own)
        cut = by_own[int(len(by_own) * OWN_FLOOR_PCTILE)].total_own
        kept = [c for c in cands if c.total_own >= cut]
        if len(kept) >= n:
            cands = kept
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
                         max_team_slots=max_team_slots, core_floors=core_floors,
                         backfill=scored_all)
    if max_off_pool:  # 0 or None -> every lineup is already all-in-pool
        _attach_pool_alternatives(final, pool, max_per_team, n_sims, own_lean, seed)
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
