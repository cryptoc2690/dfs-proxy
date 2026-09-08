"""Showdown simulator, builder and selection. Pure Python, no dependencies.

Three things here are deliberately different from the WNBA engine, because the
research brief found the WNBA versions would actively mislead in NFL:

1. **Correlation is structural, not one scalar.** WNBA multiplies a whole game
   by one number. That cannot express NFL, where a QB and his receivers rise
   together, two RBs on one team trade carries, and a defense moves opposite the
   offense it faces — all inside the same game. A single positive nudge applied
   to every pair in the game would reward combinations that genuinely oppose
   each other.

2. **Ranking is duplication-adjusted win probability, not "half production,
   half upside".** Stokastic's Ceiling column is Proj + 0.675*sd at R-squared
   1.0000 — a normal quantile, exceeded one night in four, carrying no
   information. Upside has to come out of the simulation itself.

3. **Team concentration is a target, not a cap.** In WNBA, piling onto one team
   is a risk to limit. In showdown the 5-1 split wins at nearly double the rate
   of the balanced 3-3 at matched projection, and the field only builds 5-1
   about 15.6% of the time. The cap inverts into a requirement.
"""

from __future__ import annotations

import math
import random

from dk import CAPTAIN_MULT, ROSTER_SIZE, SALARY_CAP, Lineup, Player

# --- correlation structure ----------------------------------------------
# Relative standard deviations of the shared factors. Every player's outcome
# rides some combination of these; whatever variance is left over is
# idiosyncratic. Sizes are judgement, informed by the brief's pair table, and
# they are the first thing to re-fit once real results exist.
GAME_SD = 0.10      # the whole game runs hot or cold (pace, total)
TEAM_SD = 0.14      # one offense outperforms
PASS_SD = 0.18      # that offense does it through the air -> QB + receivers
RUSH_SD = 0.18      # ...or on the ground -> the backfield
RB_PASS_SHARE = 0.35   # an RB's night is part passing game, part rushing game
DST_OPP = -0.85     # a defense moves opposite the offense it faces
DST_OWN = 0.20      # ...and mildly with its own (blowouts create turnovers)
K_TEAM = 0.45       # kickers ride their offense weakly and partly anti-correlate
                    # with its touchdowns (a stalled drive is what makes a FG)

BOOM_VALUE_MULT = 5.0    # Stokastic's Boom is P(score > 5x salary/1000)


def _gamma(k, rng):
    """Marsaglia-Tsang. Right-skewed by construction, which is what a football
    scoring distribution actually looks like — most of the tail is touchdowns
    arriving as near-Bernoulli draws."""
    if k <= 0:
        return 0.0
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


def _gamma_draw(mean, sd, rng):
    """Gamma with the requested mean and standard deviation."""
    if mean <= 0:
        return 0.0
    if sd <= 1e-6:
        return mean
    shape = (mean / sd) ** 2
    scale = (sd ** 2) / mean
    return _gamma(shape, rng) * scale


def _player_factors(p, teams):
    """How much of each shared factor this player rides. Returns
    (game, own_team, own_pass, own_rush, opp_team) exposures."""
    if p.is_dst:
        return (0.5, DST_OWN, 0.0, 0.0, DST_OPP)
    if p.pos == "K":
        return (0.8, K_TEAM, 0.0, 0.0, 0.0)
    if p.is_qb:
        return (1.0, 1.0, 1.0, 0.0, 0.0)
    if p.pos == "RB":
        return (1.0, 1.0, RB_PASS_SHARE, 1.0 - RB_PASS_SHARE, 0.0)
    return (1.0, 1.0, 1.0, 0.0, 0.0)          # WR / TE


def simulate(players, sims=4000, seed=0):
    """-> {dk_id: [score per sim]}

    Each player's variance is split between the shared factors above and an
    idiosyncratic remainder, so the total still lands on Stokastic's stated
    standard deviation instead of inflating it. Where Boom% is present the
    idiosyncratic part becomes a two-piece mixture calibrated to reproduce it —
    that is the one distribution column with content a normal cannot express
    (Josh Allen and Justin Herbert carry near-identical projections and sigma
    but Boom of 5.3 against 13.2).
    """
    rng = random.Random(seed)
    teams = sorted({p.team for p in players if p.team})
    tidx = {t: i for i, t in enumerate(teams)}

    # Shared factors, drawn once per sim and reused by every player.
    game = [max(0.25, 1 + GAME_SD * rng.gauss(0, 1)) for _ in range(sims)]
    team_f, pass_f, rush_f = [], [], []
    for _ in teams:
        team_f.append([max(0.2, 1 + TEAM_SD * rng.gauss(0, 1)) for _ in range(sims)])
        pass_f.append([max(0.2, 1 + PASS_SD * rng.gauss(0, 1)) for _ in range(sims)])
        rush_f.append([max(0.2, 1 + RUSH_SD * rng.gauss(0, 1)) for _ in range(sims)])

    out = {}
    for p in players:
        if p.proj <= 0:
            out[p.dk_id] = [0.0] * sims
            continue
        gx, tx, px, rx, ox = _player_factors(p, teams)
        ti = tidx.get(p.team, 0)
        oi = tidx.get(p.opponent, 1 - ti if len(teams) > 1 else 0)

        # Systematic relative variance this player is exposed to.
        sys_var = ((gx * GAME_SD) ** 2 + (tx * TEAM_SD) ** 2
                   + (px * PASS_SD) ** 2 + (rx * RUSH_SD) ** 2
                   + (ox * TEAM_SD) ** 2)
        sys_sd = math.sqrt(sys_var) * p.proj
        total_sd = p.sd if p.sd > 0 else 0.9 * p.proj
        # Whatever variance the shared factors do not account for is the
        # player's own. If the factors already exceed the stated sigma, damp
        # them rather than pretend the player is more volatile than stated.
        damp = 1.0
        if sys_sd >= total_sd * 0.95:
            damp = (total_sd * 0.95) / max(sys_sd, 1e-9)
            sys_sd = total_sd * 0.95
        idio_sd = math.sqrt(max(total_sd ** 2 - sys_sd ** 2, (0.05 * p.proj) ** 2))

        # Boom-calibrated two-piece idiosyncratic mean.
        boom_p = min(max(p.boom / 100.0, 0.0), 0.60)
        thresh = BOOM_VALUE_MULT * p.salary / 1000.0
        hi_mean = lo_mean = p.proj
        use_mix = False
        if boom_p > 0.01 and thresh > p.proj:
            hi_mean = thresh * 1.25
            lo_mean = (p.proj - boom_p * hi_mean) / (1 - boom_p)
            use_mix = lo_mean > 0.15 * p.proj
        row = []
        for s in range(sims):
            mult = (game[s] ** gx)
            if tx:
                mult *= team_f[ti][s] ** (tx * damp)
            if px:
                mult *= pass_f[ti][s] ** (px * damp)
            if rx:
                mult *= rush_f[ti][s] ** (rx * damp)
            if ox and len(teams) > 1:
                mult *= team_f[oi][s] ** (ox * damp)
            if use_mix and rng.random() < boom_p:
                base = _gamma_draw(hi_mean, idio_sd, rng)
            elif use_mix:
                base = _gamma_draw(lo_mean, idio_sd, rng)
            else:
                base = _gamma_draw(p.proj, idio_sd, rng)
            row.append(max(0.0, base * mult))
        out[p.dk_id] = row
    return out


def score_lineup(lu, mat, sims):
    """Captain scores 1.5x. -> [total per sim]"""
    cpt = mat[lu.cpt.dk_id]
    rows = [mat[p.dk_id] for p in lu.flex]
    return [cpt[s] * CAPTAIN_MULT + sum(r[s] for r in rows) for s in range(sims)]


def field_bar(field_entries, mat, sims, sample=1200, seed=0):
    """The score to beat, per sim.

    We only ever need to RANK our own lineups, so an honest monotone proxy for
    win probability is enough: the best score a sample of the field reaches in
    that same simulated world. Sampling understates the true field maximum, but
    it understates it identically for every candidate, so the ordering holds.
    """
    rng = random.Random(seed + 11)
    usable = [e for e in field_entries
              if e.get("cpt") is not None and len(e.get("flex") or []) == ROSTER_SIZE - 1]
    if not usable:
        return None, 0
    picks = usable if len(usable) <= sample else rng.sample(usable, sample)
    bar = [0.0] * sims
    for e in picks:
        cpt = mat.get(e["cpt"].dk_id)
        rows = [mat.get(p.dk_id) for p in e["flex"]]
        if cpt is None or any(r is None for r in rows):
            continue
        for s in range(sims):
            v = cpt[s] * CAPTAIN_MULT + sum(r[s] for r in rows)
            if v > bar[s]:
                bar[s] = v
    return bar, len(picks)


def win_rate(scores, bar, sims):
    """Share of simulated worlds where this lineup clears the field bar."""
    return sum(1 for s in range(sims) if scores[s] > bar[s]) / sims


# --- duplication ---------------------------------------------------------
def field_size(field_entries):
    """How many opponent entries the vendor pool actually models.

    Their generator is capped — 50,000 for showdown on the Max package — while
    this contest holds up to 237,812. If it fills past what the pool models,
    every duplication figure is understated by the ratio, so the caller can
    scale. At the fill levels seen so far the two happen to be close, which is
    luck rather than design.
    """
    return sum(1.0 + (e.get("dupes") or 0.0) for e in field_entries
               if e.get("cpt") is not None)


def dupe_index(field_entries):
    """{lineup key: how many field entries hold exactly this roster}.

    Built from the vendor pool, which the brief established is an
    ownership-matched model of your opponents rather than a set of picks. Its
    Dupes column plus one row per distinct lineup reconstructs the whole field.
    """
    idx = {}
    for e in field_entries:
        cpt, flex = e.get("cpt"), e.get("flex") or []
        if cpt is None or len(flex) != ROSTER_SIZE - 1:
            continue
        key = (cpt.dk_id, frozenset(p.dk_id for p in flex))
        idx[key] = idx.get(key, 0.0) + 1.0 + (e.get("dupes") or 0.0)
    return idx


def estimated_dupes(lu, idx, own_fallback=True, scale=1.0):
    """How many opponents we expect to be holding this exact roster."""
    hit = idx.get(lu.key())
    if hit is not None:
        return max(0.0, (hit - 1.0) * scale)
    if not own_fallback:
        return 0.0
    # Not in the vendor pool at all -> the field is unlikely to build it. Use a
    # small ownership-driven estimate rather than claiming zero.
    p = 1.0
    for pl in lu.flex:
        p *= max(pl.ownership, 0.1) / 100.0
    p *= max(lu.cpt.cpt_own or lu.cpt.ownership / 3.0, 0.1) / 100.0
    return p * 50_000.0 * scale


# --- construction --------------------------------------------------------
SPLIT_TARGETS = {"5-1": 0.45, "4-2": 0.40, "3-3": 0.15}
# Unspent salary. The brief found leftover is a null on showdown win rate
# (beta +0.001) but NOT on duplication (r = -0.30) — cheaper lineups are less
# duplicated, which is worth something when 70% of showdown lineups carry a
# dupe. So this is a junk filter, not a lever: it exists to stop the builder
# handing back a lineup with five figures unspent, and nothing more.
MAX_LEFTOVER = 5000

# A roster spot projected under this is dead weight, not a punt. The real slate
# runs Efton Chism III at 0.20 projected points and Tanner Arkin at 0.15 — a
# $200 body exists only so the cap can be reached, and since leftover salary is
# a null on showdown win rate there is no reason to reach it through one. This
# is the showdown equivalent of the WNBA minutes gate: a floor on whether a slot
# has any path to a useful score, not a grade on how good the player is.
MIN_PROJ = 2.0
OWN_LEAN = 0.35          # POSITIVE = lean toward the field. See below.
CAPTAIN_CAP = 0.28       # share of entries any one captain may hold
MIN_CAPTAINS = 10
PLAYER_CAP = 0.65        # showdown must run high: 6 of ~68 players fill a lineup
MAX_OVERLAP = 4          # of 6, before two entries are near-duplicates


def _weighted_pick(cands, weights, rng):
    total = sum(weights)
    if total <= 0:
        return rng.choice(cands)
    x = rng.random() * total
    for c, w in zip(cands, weights):
        x -= w
        if x <= 0:
            return c
    return cands[-1]


def _dst_ok(players):
    """Never roster a defense against an offense you are stacking — close to
    mechanically self-cancelling, since points allowed IS the opponent's
    scoring. Enforced as a hard rule, not a soft penalty."""
    for d in players:
        if not d.is_dst:
            continue
        against = sum(1 for p in players
                      if not p.is_dst and p.team and p.team == d.opponent)
        if against >= 3:
            return False
    return True


def build_candidates(players, n, *, teams, split_targets=None, rng=None,
                     max_off_pool=None, cpt_pool=None, max_leftover=MAX_LEFTOVER,
                     min_proj=MIN_PROJ):
    """Randomised construction aimed at the shapes the field under-builds."""
    rng = rng or random.Random(0)
    split_targets = split_targets or SPLIT_TARGETS
    pool = [p for p in players
            if p.proj >= (min_proj if min_proj is not None else 0) and p.salary > 0]
    if len(pool) < ROSTER_SIZE:      # gate too tight for this slate — ungate
        pool = [p for p in players if p.proj > 0 and p.salary > 0]
    if len(pool) < ROSTER_SIZE or len(teams) < 2:
        return []
    cpt_pool = cpt_pool or pool
    splits = list(split_targets.items())
    out, seen = [], set()
    # No single captain may take more than this share of the CANDIDATE pool, so
    # the selection stage always has real alternatives to pick between.
    cpt_used, cpt_room = {}, max(3, int(n * 0.12))
    tries = 0
    while len(out) < n and tries < n * 40:
        tries += 1
        want = _weighted_pick([s for s, _ in splits], [w for _, w in splits], rng)
        big, small = (int(x) for x in want.split("-"))
        major = teams[rng.randrange(len(teams))]
        minor = [t for t in teams if t != major][0]
        need = {major: big, minor: small}

        # Captain first: it is the highest-dispersion decision in the format and
        # it moves the salary budget most (1.5x), so choosing it last would let
        # the flex fill strand it.
        #
        # The exponent is deliberately gentler than the flex weighting. Weighted
        # hard on projection, virtually every candidate captains one of two QBs,
        # and the selection stage then has nothing diverse to choose from — the
        # captain cap starves and the split quotas go unfilled. Stokastic's own
        # field shows the same failure from the other side: 20.6% of its
        # captaincies sit on one player and it uses only 24 distinct captains
        # across 9,061 lineups.
        cw = []
        for p in cpt_pool:
            base = max(p.proj, 0.1) ** 1.4
            if p.cpt_optimal > 0:
                base *= (1.0 + p.cpt_optimal / 20.0)
            if cpt_used.get(p.dk_id, 0) >= cpt_room:
                base = 0.0            # this captain has enough candidates already
            cw.append(base)
        if not any(cw):
            cpt_used.clear()
            continue
        cpt = _weighted_pick(cpt_pool, cw, rng)
        if need.get(cpt.team, 0) <= 0:
            continue
        picked = [cpt]
        used = {cpt.dk_id}
        left = {cpt.team: need[cpt.team] - 1,
                minor if cpt.team == major else major:
                    need[minor if cpt.team == major else major]}
        salary = cpt.cpt_salary()
        ok = True
        for _ in range(ROSTER_SIZE - 1):
            slots_left = ROSTER_SIZE - len(picked)
            # What an average remaining slot can afford. Weighting on projection
            # alone builds cheap lineups that leave five figures unspent, because
            # a random draw from a projection-weighted pool has no reason to use
            # the budget. Biasing toward this figure is what makes the cap
            # reachable without hard-coding a salary target.
            per_slot = (SALARY_CAP - salary) / max(slots_left, 1)
            # The two cheapest still-available salaries, worked out ONCE per
            # slot. Recomputing this inside the per-player loop made the whole
            # build quadratic in pool size for no benefit.
            avail = sorted(q.salary for q in pool if q.dk_id not in used)
            cheap1 = avail[0] if avail else 0
            cheap2 = avail[1] if len(avail) > 1 else cheap1
            elig, w = [], []
            for p in pool:
                if p.dk_id in used:
                    continue
                if left.get(p.team, 0) <= 0:
                    continue
                if salary + p.salary > SALARY_CAP:
                    continue
                # leave enough room for the remaining slots
                if slots_left > 1:
                    floor_rest = (cheap2 if p.salary == cheap1 else cheap1)
                    if salary + p.salary + floor_rest * (slots_left - 1) > SALARY_CAP:
                        continue
                elig.append(p)
                spend = min(p.salary / per_slot, 1.6) if per_slot > 0 else 1.0
                w.append((max(p.proj, 0.1) ** 3) * (0.35 + spend))
            if not elig:
                ok = False
                break
            p = _weighted_pick(elig, w, rng)
            picked.append(p)
            used.add(p.dk_id)
            salary += p.salary
            left[p.team] -= 1
        if not ok or len(picked) != ROSTER_SIZE:
            continue
        if not _dst_ok(picked):
            continue
        if max_off_pool is not None:
            if sum(1 for p in picked if not p.in_pool and not p.core) > max_off_pool:
                continue
        # Spend-up repair. Rejecting every lineup that leaves money on the table
        # throws away most of the work — the random fill lands near the cap far
        # more often than on it. Upgrading the weakest slot to a better player on
        # the same team, within budget, converts those near-misses into usable
        # lineups instead and keeps the candidate pool wide enough for the
        # captain and split quotas to have real choices.
        if max_leftover is not None:
            for _ in range(4):
                spare = SALARY_CAP - (picked[0].cpt_salary()
                                      + sum(q.salary for q in picked[1:]))
                if spare <= max_leftover:
                    break
                worst = min(picked[1:], key=lambda q: q.proj)
                budget = worst.salary + spare
                better = [q for q in pool
                          if q.dk_id not in used and q.team == worst.team
                          and q.salary <= budget and q.proj > worst.proj]
                if not better:
                    break
                up = max(better, key=lambda q: q.proj)
                used.discard(worst.dk_id)
                used.add(up.dk_id)
                picked[picked.index(worst)] = up
            if not _dst_ok(picked):
                continue

        lu = Lineup(picked[0], picked[1:], source="mine")
        if lu.salary > SALARY_CAP:
            continue
        if max_leftover is not None and SALARY_CAP - lu.salary > max_leftover:
            continue
        if lu.key() in seen:
            continue
        seen.add(lu.key())
        cpt_used[cpt.dk_id] = cpt_used.get(cpt.dk_id, 0) + 1
        out.append(lu)
    if not out and max_leftover is not None:   # slate can't spend the cap
        return build_candidates(players, n, teams=teams,
                                split_targets=split_targets, rng=rng,
                                max_off_pool=max_off_pool, cpt_pool=cpt_pool,
                                max_leftover=None, min_proj=min_proj)
    return out


def rank(lineups, mat, bar, sims, dupes_idx, own_lean=OWN_LEAN, dupe_scale=1.0):
    """Duplication-adjusted win probability, with a modest ownership lean.

    The lean is POSITIVE in showdown, which is the opposite of the classic
    default. At matched projection the vendor's own simulation has chalk winning
    by 106% here and losing by 32% on a main slate, and the showdown direction
    is the one that also matches real WNBA contest results on small pools. The
    mechanism is pool size: with under 70 draftable players, consensus has
    nowhere to hide and fading it means deliberately playing worse players.
    """
    owns = [lu.own_sum for lu in lineups] or [0]
    lo, hi = min(owns), max(owns)
    span = (hi - lo) or 1.0
    for lu in lineups:
        sc = score_lineup(lu, mat, sims)
        sc_sorted = sorted(sc)
        w = win_rate(sc, bar, sims) if bar else 0.0
        d = estimated_dupes(lu, dupes_idx, scale=dupe_scale)
        on = (lu.own_sum - lo) / span
        lu.metrics.update({
            "win": round(w, 5),
            "dupes": round(d, 2),
            "mean": round(sum(sc) / sims, 2),
            "p90": round(sc_sorted[int(sims * 0.90)], 2),
            "ownLean": round(1 + own_lean * (2 * on - 1), 3),
        })
        lu.metrics["score"] = (w / (1.0 + d)) * lu.metrics["ownLean"]
    lineups.sort(key=lambda l: -l.metrics["score"])
    return lineups


def select(lineups, n, *, captain_cap=CAPTAIN_CAP, min_captains=MIN_CAPTAINS,
           player_cap=PLAYER_CAP, max_overlap=MAX_OVERLAP,
           split_targets=None):
    """Pick the final N under coverage rules rather than diversification ones.

    150 showdown entries are worth roughly two independent bets — mean pairwise
    correlation across a random 150 is 0.463 and you cannot get below it, since
    every lineup draws six players from one game. So entry count buys COVERAGE,
    not independence, and the axes with real outcome dispersion are the captain
    and the team split. The captain cap is therefore much tighter than the flex
    cap, and there is a floor on distinct captains.

    Split quotas exist because the lopsided-split finding is measured AT MATCHED
    PROJECTION, and raw ranking does not match projection. Forcing 5 players
    from one team means reaching deeper into that team's pool, so 5-1 lineups
    carry a couple of points less projection than 3-3 ones and lose a
    straight-ranking contest even while winning every like-for-like comparison.
    Our own simulation shows exactly that: 5-1 beats 3-3 inside five of six
    projection bands, and loses pooled. A quota is how you act on a
    matched-projection result; ranking alone would quietly discard it, which is
    the whole reason the field only builds 5-1 about 15.6% of the time.
    """
    cap_ct = max(1, round(captain_cap * n))
    ply_ct = max(1, round(player_cap * n))
    quota = {}
    if split_targets:
        quota = {k: int(round(v * n)) for k, v in split_targets.items()}
    chosen, sets = [], []
    cpt_ct, ply_used, split_ct = {}, {}, {}

    def take(lu):
        chosen.append(lu)
        sets.append(set(lu.ids()))
        cpt_ct[lu.cpt.dk_id] = cpt_ct.get(lu.cpt.dk_id, 0) + 1
        split_ct[lu.split_label()] = split_ct.get(lu.split_label(), 0) + 1
        for p in lu.players:
            ply_used[p.dk_id] = ply_used.get(p.dk_id, 0) + 1

    def ok(lu, overlap):
        if cpt_ct.get(lu.cpt.dk_id, 0) >= cap_ct:
            return False
        if any(ply_used.get(i, 0) >= ply_ct for i in lu.ids()):
            return False
        s = set(lu.ids())
        return not any(len(s & t) > overlap for t in sets)

    taken = set()

    def pass_over(overlap, want_split=None, limit=None):
        for lu in lineups:
            if len(chosen) >= n or (limit is not None and split_ct.get(want_split, 0) >= limit):
                return
            if id(lu) in taken:
                continue
            if want_split and lu.split_label() != want_split:
                continue
            if ok(lu, overlap):
                take(lu)
                taken.add(id(lu))

    # Quotas first, best-first inside each shape, then fill on merit.
    for shape, want in sorted(quota.items(), key=lambda kv: -kv[1]):
        pass_over(max_overlap, want_split=shape, limit=want)
    pass_over(max_overlap)
    for relax in (max_overlap + 1, ROSTER_SIZE):      # loosen rather than under-fill
        pass_over(relax)

    # Still short. Relax the player cap but HOLD the captain cap: the captain is
    # the highest-dispersion decision in the format, so it is the last thing to
    # give up. Only if that also starves do we fill unconditionally.
    if len(chosen) < n:
        for lu in lineups:
            if len(chosen) >= n:
                break
            if id(lu) in taken or cpt_ct.get(lu.cpt.dk_id, 0) >= cap_ct:
                continue
            take(lu)
            taken.add(id(lu))
    for lu in lineups:
        if len(chosen) >= n:
            break
        if id(lu) not in taken:
            take(lu)
            taken.add(id(lu))

    # Captain coverage floor: swap the weakest entries onto unused captains
    # until enough distinct captains are represented.
    if min_captains and len(cpt_ct) < min_captains:
        have = set(cpt_ct)
        for lu in lineups:
            if len(have) >= min_captains:
                break
            if lu.cpt.dk_id in have or id(lu) in taken:
                continue
            victim = next((c for c in reversed(chosen)
                           if cpt_ct.get(c.cpt.dk_id, 0) > 1), None)
            if victim is None:
                break
            chosen.remove(victim)
            cpt_ct[victim.cpt.dk_id] -= 1
            chosen.append(lu)
            cpt_ct[lu.cpt.dk_id] = 1
            have.add(lu.cpt.dk_id)
    return chosen[:n]


def vendor_arm(field_entries, n, *, players_by_id, captain_cap=CAPTAIN_CAP,
               min_captains=MIN_CAPTAINS, player_cap=PLAYER_CAP,
               max_overlap=MAX_OVERLAP, dupe_scale=1.0):
    """Their pool, re-ranked on Win% / (1 + Dupes) and put through the same caps.

    This is the control arm for the A/B comparison, and on its own it is a
    measurable improvement on their default ordering: on the brief's showdown
    file this rule returned $412 of expected first-place equity against $384 for
    Simulated ROI, $91 for raw Win% and $33 for projection. Ranking on raw Win%
    is the obvious naive move and duplication destroys it.
    """
    cands = []
    for e in field_entries:
        cpt, flex = e.get("cpt"), e.get("flex") or []
        if cpt is None or len(flex) != ROSTER_SIZE - 1:
            continue
        lu = Lineup(cpt, flex, source="vendor")
        d = (e.get("dupes") or 0.0) * dupe_scale
        lu.metrics = {"win": e.get("win", 0.0), "dupes": d,
                      "roi": e.get("roi", 0.0), "cash": e.get("cash", 0.0),
                      "score": (e.get("win", 0.0)) / (1.0 + d)}
        cands.append(lu)
    cands.sort(key=lambda l: -l.metrics["score"])
    return select(cands, n, captain_cap=captain_cap, min_captains=min_captains,
                  player_cap=player_cap, max_overlap=max_overlap)
