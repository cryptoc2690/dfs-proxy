"""The NBA simulator, the field bar, duplication, ranking and selection — shared
by both formats — plus the showdown builder. Pure Python, no dependencies.

The PROCESS is the NFL tool's, by decision: Stokastic's lineups export is a
model of the opponents, we build our own lineups, and a lineup is ranked on how
often it clears the field's 99th-percentile score in the same simulated world,
divided by how many opponents are expected to hold it. Everything the NFL notes
say about why the ranking is shaped that way carries over; it is not repeated
here.

What does NOT carry over is anything NFL measured about NFL. Stack quotas, the
5-1 split target, the captain cap, the K/DST read and the football correlation
structure were all fitted to football, and NBA starts without them. Where a
number below is borrowed from NFL or WNBA it says so, and says it is waiting on
NBA results.
"""

from __future__ import annotations

import heapq
import math
import random

from dk import CAPTAIN_MULT, SALARY_CAP, SD_ROSTER_SIZE, ShowdownLineup

# --- simulation ----------------------------------------------------------
# No team or game correlation by default. This is the WNBA result carried as a
# prior, not an NBA measurement: on 23 WNBA slates the residual correlation was
# teammates -0.010, opponents -0.006, different games +0.019, on thousands of
# pairs, and realised game totals over projected ran exactly as independent
# players predict. The old WNBA simulator's shared game factor (a forced +0.35)
# handed extra variance to game stacks and made its 85th percentile a
# stack-shaped artefact. NBA has reasons it might differ — usage competition
# pulls teammates negative, pace pulls a whole game together — so the knob is
# here, set to zero, to be fitted once graded slates exist rather than guessed.
GAME_SD = 0.0

# Spread when the projections file carries no Std Dev. Stokastic's does, and
# every row is calibrated to it exactly (the NFL fix: rows landing on the stated
# mean and sigma, not near them). This fallback is WNBA's measured residual —
# actual minus projection had an SD of 9-11 for rostered rotation players —
# and exists only so a file without the column still builds.
SD_FLOOR = 6.0
SD_SHARE = 0.40


def _gamma(k, rng):
    """Marsaglia-Tsang. Right-skewed, which is right for a bench player whose
    night is mostly minutes that may not come; near-normal for a star."""
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
    if mean <= 0:
        return 0.0
    if sd <= 1e-6:
        return mean
    return _gamma((mean / sd) ** 2, rng) * (sd * sd) / mean


def _calibrate(row, mean_t, sd_t):
    """Land a row on the stated mean and sigma exactly. An affine map leaves
    any correlation alone. Ported unchanged from nfl/engine.py."""
    n = len(row)
    m = sum(row) / n
    if m <= 0:
        return row
    k = mean_t / m
    row = [x * k for x in row]
    sd = math.sqrt(sum((x - mean_t) ** 2 for x in row) / n)
    if sd > 1e-9 and sd_t > 0:
        f = sd_t / sd
        row = [max(0.0, mean_t + (x - mean_t) * f) for x in row]
        m2 = sum(row) / n
        if m2 > 0:
            k2 = mean_t / m2
            row = [x * k2 for x in row]
    return row


def player_sd(p):
    return p.sd if p.sd > 0 else max(SD_FLOOR, SD_SHARE * p.proj)


def simulate(players, sims=4000, seed=0):
    """-> {dk_id: [DK points per sim]}. A removed or projected-0 player is a
    row of zeros, so a field lineup still holding him scores what it would."""
    rng = random.Random(seed)
    game_f = {}
    if GAME_SD:
        for g in sorted({p.game for p in players if p.game}):
            game_f[g] = [max(0.25, 1 + GAME_SD * rng.gauss(0, 1)) for _ in range(sims)]
    out = {}
    for p in players:
        if p.proj <= 0:
            out[p.dk_id] = [0.0] * sims
            continue
        sd = player_sd(p)
        row = [_gamma_draw(p.proj, sd, rng) for _ in range(sims)]
        if p.game in game_f:
            row = [x * m for x, m in zip(row, game_f[p.game])]
        out[p.dk_id] = _calibrate(row, p.proj, sd)
    return out


def score_lineup(lu, mat, _cache=None):
    """-> [lineup total per sim]. Captain rows are 1.5x."""
    rows = []
    for p, m in lu.weighted():
        r = mat[p.dk_id]
        if m != 1.0:
            key = (p.dk_id, m)
            if _cache is not None and key in _cache:
                r = _cache[key]
            else:
                r = [x * m for x in r]
                if _cache is not None:
                    _cache[key] = r
        rows.append(r)
    return [sum(t) for t in zip(*rows)]


# --- the field bar -------------------------------------------------------
# The score to beat: the field's 99th percentile in each simulated world,
# sampled in proportion to how many entries each vendor roster represents.
# NFL measured "beat the single best field score" as too coarse (most
# candidates cleared it in zero simulations and the order among the rest was
# seed noise) and the 95th and 99.9th percentiles as worse than the 99th.
BAR_QUANTILE = 0.99
BAR_SAMPLE = 2000
BAR_WEIGHTED = True


def field_bar(field, mat, sims, sample=None, seed=0, quantile=None, weighted=None):
    """-> ([bar per sim], lineups sampled). `field` is vendor lineups (either
    format). Scored in chunks and merged per sim, so the sample x sims matrix
    never exists at once."""
    rng = random.Random(seed + 11)
    usable = [lu for lu in field if all(p.dk_id in mat for p in lu.players)]
    if not usable:
        return None, 0
    sample = sample or BAR_SAMPLE
    q = BAR_QUANTILE if quantile is None else quantile
    if BAR_WEIGHTED if weighted is None else weighted:
        picks = rng.choices(usable, weights=[1.0 + lu.metrics.get("vdupes", 0.0)
                                             for lu in usable], k=sample)
    else:
        picks = usable if len(usable) <= sample else rng.sample(usable, sample)
    keep = max(1, int(round((1.0 - q) * len(picks))))
    tops = [[] for _ in range(sims)]
    cache = {}
    for i in range(0, len(picks), 100):
        totals = [score_lineup(lu, mat, cache) for lu in picks[i:i + 100]]
        for s, col in enumerate(zip(*totals)):
            tops[s] = heapq.nlargest(keep, tops[s] + list(col))
    return [t[-1] if t else 0.0 for t in tops], len(picks)


# --- duplication ---------------------------------------------------------
# NBA classic DOES duplicate, unlike NFL's main slate (96.4% of 415,601 NFL
# entries were unique). The pool of real scorers is small, so the right eight is
# more likely to be somebody else's eight too. The old chats' standings files
# show it at the top of real $0.50 fields: contest 188543930 had ten entries,
# ours among them, tied at rank 5 on one roster (334.0 pts, OwnSum 146.4%), and
# 188625961 had an 8-way tie at rank 11 and a 4-way tie at rank 4. So classic is
# priced exactly as NFL showdown is priced, and the numbers below are NFL
# showdown's until NBA results re-fit them.
#
# A vendor roster listed with Dupes = D is really held by about
# scale * (DUPE_A + DUPE_B * (1 + D)) opponents — NFL's leave-one-slate-out fit
# over four finished showdowns.
DUPE_A = 0.45
DUPE_B = 0.67
# score = win / (1 + dupes) ** DUPE_EXP. Not 1.0: a roster 206 entries share
# collects the whole block of ranks 1-206 divided 206 ways, not first prize
# divided 206 ways. NFL priced 1.0 at $168 per 600 entries against $497 at 0.25.
DUPE_EXP = 0.25


def dupe_index(field):
    idx = {}
    for lu in field:
        idx[lu.key()] = idx.get(lu.key(), 0.0) + 1.0 + lu.metrics.get("vdupes", 0.0)
    return idx


def field_size(field):
    """How many opponent entries the vendor pool models."""
    return sum(1.0 + lu.metrics.get("vdupes", 0.0) for lu in field)


def expected_copies(hit, scale):
    return max(0.0, scale * (DUPE_A + DUPE_B * hit))


def estimated_dupes(lu, idx, scale=1.0, field_n=0.0):
    """How many OPPONENTS are expected to hold this exact roster."""
    hit = idx.get(lu.key())
    if hit is not None:
        return expected_copies(hit, scale)
    # Not in the vendor pool: an ownership-driven independence estimate against
    # the field the vendor actually models, scaled to the real contest.
    p = 1.0
    for pl in lu.players:
        own = (pl.cpt_own or pl.ownership / 3.0) if (
            isinstance(lu, ShowdownLineup) and pl is lu.cpt) else pl.ownership
        p *= max(own, 0.1) / 100.0
    return p * (field_n if field_n > 0 else 50_000.0) * scale


# --- ranking -------------------------------------------------------------
# Neutral. WNBA: 0 was picked on all 23 held-out folds, and fading cost 11-14
# cashes. NFL: +0.35 / 0 / -0.35 returned the same money inside seed noise on
# showdown, and ranking on win probability already fades chalk on a main slate
# without help. The old NBA chats' own evidence points the same way — the
# leverage number faded chalk that won (Jenkins, Dosunmu, Gobert; Grimes on a
# two-game night). The lever stays live in both directions.
OWN_LEAN = 0.0


def rank(lineups, mat, bar, sims, dupes_idx, own_lean=None, dupe_scale=1.0,
         field_n=0.0):
    own_lean = OWN_LEAN if own_lean is None else own_lean
    owns = [lu.own_sum for lu in lineups] or [0]
    lo, hi = min(owns), max(owns)
    span = (hi - lo) or 1.0
    cache = {}
    for lu in lineups:
        sc = score_lineup(lu, mat, cache)
        d = estimated_dupes(lu, dupes_idx, scale=dupe_scale, field_n=field_n)
        lean = 1 + own_lean * (2 * (lu.own_sum - lo) / span - 1)
        lu.metrics.update({"dupes": round(d, 2), "mean": round(sum(sc) / sims, 2),
                           "ownLean": round(lean, 3)})
        if bar:
            w = sum(1 for a, b in zip(sc, bar) if a > b) / sims
            lu.metrics["win"] = round(w, 5)
            lu.metrics["score"] = w / (1.0 + d) ** DUPE_EXP * lean
        else:
            # No field file: no bar to beat, so rank on the simulated mean. NFL's
            # fallback, and a much weaker build — the page says so.
            lu.metrics["score"] = lu.metrics["mean"] / (1.0 + d) ** DUPE_EXP * lean
    lineups.sort(key=lambda l: -l.metrics["score"])
    return lineups


# --- selection -----------------------------------------------------------
# No board-wide exposure cap and no near-duplicate rule by default: 1.0 and
# "distinct rosters only". Both are measurement, not omission.
#   WNBA: the exposure cap, the pairwise-overlap cap and the team cap changed
#   no outcome held out over 23 slates, and the fill pass beneath them broke
#   the cap anyway (realised 76% against a 60% setting). Pairwise overlap was
#   2.62 of 6 without them against 2.65 with.
#   NFL showdown: tightening the player cap to 60% cost $39 and 26 cashes over
#   four contests — heavy exposure was the builder following its projections.
# Heavy exposure is REPORTED instead, and the per-player caps you type are
# honoured — those are your instruction, not a data preference.
PLAYER_CAP = 1.0
CAPTAIN_CAP = 1.0          # showdown; NFL's 0.28 rail never bound there either


def select(lineups, n, *, player_cap=None, player_caps=None, max_overlap=None,
           captain_cap=None, core_floors=None, prior=None, roster_size=None):
    """Pick the final N, score-first, under your caps and the core floors.

    Ported from NFL's selector with the football quotas taken out. `prior` is
    what an earlier arm took: its rosters are excluded, its exposure counts
    inherited, so caps and floors hold across ALL entries. Cores go first,
    under the same caps as everything else (NFL: swapping them in afterwards
    past every cap once put a core in 62 of 75 against a cap of 49).
    """
    prior = list(prior or [])
    player_cap = PLAYER_CAP if player_cap is None else player_cap
    captain_cap = CAPTAIN_CAP if captain_cap is None else captain_cap
    size = roster_size or (len(lineups[0].players) if lineups else 8)
    max_overlap = (size - 1) if max_overlap is None else max_overlap
    seen = {lu.key() for lu in prior}
    uniq = []
    for lu in lineups:
        if lu.key() in seen:
            continue
        seen.add(lu.key())
        uniq.append(lu)
    lineups = uniq
    total = n + len(prior)
    ply = max(1, round(player_cap * total))
    cptc = max(1, round(captain_cap * total))
    caps = {i: max(0, int(round(v * total))) for i, v in (player_caps or {}).items()}
    chosen, taken, sets = [], set(), [set(lu.overlap_ids()) for lu in prior]
    used, cpt_ct = {}, {}

    def bump(lu):
        for p in lu.players:
            used[p.dk_id] = used.get(p.dk_id, 0) + 1
        if isinstance(lu, ShowdownLineup):
            cpt_ct[lu.cpt.dk_id] = cpt_ct.get(lu.cpt.dk_id, 0) + 1

    for lu in prior:
        bump(lu)

    def take(lu):
        chosen.append(lu)
        taken.add(id(lu))
        sets.append(set(lu.overlap_ids()))
        bump(lu)

    def ok(lu, overlap):
        for p in lu.players:
            lim = caps.get(p.dk_id)      # your number replaces the board-wide one
            if used.get(p.dk_id, 0) >= (lim if lim is not None else ply):
                return False
        if isinstance(lu, ShowdownLineup) and cpt_ct.get(lu.cpt.dk_id, 0) >= cptc:
            return False
        s = set(lu.overlap_ids())
        return not any(len(s & t) > overlap for t in sets)

    def sweep(overlap, need=None, floor=None):
        for lu in lineups:
            if len(chosen) >= n:
                return
            if floor is not None and used.get(need, 0) >= floor:
                return
            if id(lu) in taken:
                continue
            if need is not None and need not in lu.ids():
                continue
            if ok(lu, overlap):
                take(lu)

    for cid, floor in (core_floors or {}).items():
        sweep(max_overlap, need=cid, floor=floor)
    sweep(max_overlap)
    for relax in (max_overlap + 1, size):
        sweep(relax)
    for cid, floor in (core_floors or {}).items():
        if used.get(cid, 0) < floor:
            sweep(size, need=cid, floor=floor)

    # Still short: relax the caps one lineup at a time, a cap you TYPED last,
    # and never silently — the caller checks the finished set against them.
    def fill(slack):
        for lu in lineups:
            if len(chosen) >= n:
                return
            if id(lu) in taken:
                continue
            if slack is not None and any(
                    used.get(p.dk_id, 0) >= caps[p.dk_id] + slack
                    for p in lu.players if p.dk_id in caps):
                continue
            take(lu)

    if len(chosen) < n and caps:
        for slack in range(0, n + 1):
            if len(chosen) >= n:
                break
            fill(slack)
    fill(None)
    return chosen[:n]


def vendor_arm(field, n, **kw):
    """Their pool, re-ranked on Top 10% / (1 + dupes) ** DUPE_EXP — the control
    arm for the A/B split. Top 10% rather than Win%, for NFL's reason: Win% takes
    a few dozen distinct values across ten thousand rows and nearly half of them
    are zero, so ranking on it is ranking on the sort's tie-breaking."""
    dupe_scale = kw.pop("dupe_scale", 1.0)
    cands = []
    for lu in field:
        d = expected_copies(1.0 + lu.metrics.get("vdupes", 0.0), dupe_scale)
        lu.source = "vendor"
        lu.metrics.update({"dupes": round(d, 2),
                           "score": (lu.metrics.get("top10", 0.0) or 0.0)
                                    / (1.0 + d) ** DUPE_EXP})
        cands.append(lu)
    cands.sort(key=lambda l: -l.metrics["score"])
    return select(cands, n, **kw)


# --- showdown construction ----------------------------------------------
# Construction weight goes as projection cubed. NFL tested 1.0 / 1.5 / 3.0 on
# five real contests at eight seeds and every gap sat inside its own seed noise;
# flattening bought variance, not money. Kept at 3 and shared with classic.
FILL_EXP = 3.0
CORE_BOOST = 3.0     # construction weight on a core, so its floor is reachable
# Showdown junk filter. NFL measured unspent salary as a null on showdown win
# rate but not on duplication (cheaper lineups are less duplicated), and 5000 as
# a working level there. Unmeasured on NBA.
SD_MAX_LEFTOVER = 5000
MIN_PROJ = 2.0       # a roster spot needs some path to a useful score


def build_showdown(players, n, *, rng=None, max_off_pool=None, cpt_pool=None,
                   max_leftover=SD_MAX_LEFTOVER, min_proj=MIN_PROJ):
    """Randomised construction: captain first, then five UTIL.

    NFL's showdown builder with the split quotas removed. DK requires both
    teams in a showdown roster, and that is the only shape rule.
    """
    rng = rng or random.Random(0)
    pool = [p for p in players if p.proj >= min_proj and p.salary > 0]
    if len(pool) < SD_ROSTER_SIZE:
        pool = [p for p in players if p.proj > 0 and p.salary > 0]
    if len(pool) < SD_ROSTER_SIZE or len({p.team for p in pool}) < 2:
        return []

    def allowed(p, off):
        return max_off_pool is None or p.in_pool or p.core or off < max_off_pool

    cpt_pool = [p for p in (cpt_pool or pool) if allowed(p, 0)] or pool
    out, seen = [], set()
    # No captain may take more than this share of the CANDIDATE pool, so the
    # selection stage always has real alternatives (NFL: weighted hard, every
    # candidate captained one of two players).
    cpt_used, cpt_room = {}, max(3, int(n * 0.12))
    tries = 0
    while len(out) < n and tries < n * 40:
        tries += 1
        cw = []
        for p in cpt_pool:
            w = max(p.proj, 0.1) ** 1.4 * (CORE_BOOST if p.core else 1.0)
            if p.cpt_optimal > 0:
                w *= 1.0 + p.cpt_optimal / 20.0
            if cpt_used.get(p.dk_id, 0) >= cpt_room:
                w = 0.0
            cw.append(w)
        if not any(cw):
            cpt_used.clear()
            continue
        cpt = _pick(cpt_pool, cw, rng)
        picked, used = [cpt], {cpt.dk_id}
        off = 0 if (cpt.in_pool or cpt.core) else 1
        salary = cpt.cpt_salary()
        ok = True
        for _ in range(SD_ROSTER_SIZE - 1):
            slots_left = SD_ROSTER_SIZE - len(picked)
            per_slot = (SALARY_CAP - salary) / max(slots_left, 1)
            avail = sorted(q.salary for q in pool if q.dk_id not in used)
            cheap1 = avail[0] if avail else 0
            cheap2 = avail[1] if len(avail) > 1 else cheap1
            elig, w = [], []
            for p in pool:
                if p.dk_id in used or not allowed(p, off):
                    continue
                if salary + p.salary > SALARY_CAP:
                    continue
                if slots_left > 1:
                    rest = cheap2 if p.salary == cheap1 else cheap1
                    if salary + p.salary + rest * (slots_left - 1) > SALARY_CAP:
                        continue
                elig.append(p)
                spend = min(p.salary / per_slot, 1.6) if per_slot > 0 else 1.0
                w.append(max(p.proj, 0.1) ** FILL_EXP * (0.35 + spend)
                         * (CORE_BOOST if p.core else 1.0))
            if not elig:
                ok = False
                break
            p = _pick(elig, w, rng)
            picked.append(p)
            used.add(p.dk_id)
            salary += p.salary
            if not (p.in_pool or p.core):
                off += 1
        if not ok:
            continue
        # Spend-up repair (NFL): upgrade the weakest UTIL within the spare
        # salary rather than throw away a near-miss.
        if max_leftover is not None:
            for _ in range(4):
                spare = SALARY_CAP - (picked[0].cpt_salary()
                                      + sum(q.salary for q in picked[1:]))
                if spare <= max_leftover:
                    break
                worst = min(picked[1:], key=lambda q: q.proj)
                w_off = 0 if (worst.in_pool or worst.core) else 1
                better = [q for q in pool if q.dk_id not in used
                          and q.salary <= worst.salary + spare and q.proj > worst.proj
                          and allowed(q, off - w_off)]
                if not better:
                    break
                up = max(better, key=lambda q: q.proj)
                used.discard(worst.dk_id)
                used.add(up.dk_id)
                off += (0 if (up.in_pool or up.core) else 1) - w_off
                picked[picked.index(worst)] = up
        lu = ShowdownLineup(picked[0], picked[1:])
        if lu.salary > SALARY_CAP or len({p.team for p in picked}) < 2:
            continue
        if max_leftover is not None and SALARY_CAP - lu.salary > max_leftover:
            continue
        if max_off_pool is not None and sum(
                1 for p in picked if not p.in_pool and not p.core) > max_off_pool:
            continue
        if lu.key() in seen:
            continue
        seen.add(lu.key())
        cpt_used[cpt.dk_id] = cpt_used.get(cpt.dk_id, 0) + 1
        out.append(lu)
    if not out and max_leftover is not None:        # slate cannot spend the cap
        return build_showdown(players, n, rng=rng, max_off_pool=max_off_pool,
                              cpt_pool=cpt_pool, max_leftover=None, min_proj=min_proj)
    return out


def _pick(cands, weights, rng):
    tot = sum(weights)
    if tot <= 0:
        return rng.choice(cands)
    x = rng.random() * tot
    for c, w in zip(cands, weights):
        x -= w
        if x <= 0:
            return c
    return cands[-1]
