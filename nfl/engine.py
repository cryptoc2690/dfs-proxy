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

import heapq
import math
import random

from dk import CAPTAIN_MULT, ROSTER_SIZE, SALARY_CAP, Lineup

# --- correlation structure ----------------------------------------------
# Relative standard deviations of the shared factors. Every player's outcome
# rides some combination of these; whatever variance is left over is
# idiosyncratic. Sizes are judgement, informed by the brief's pair table, and
# they are the first thing to re-fit once real results exist.
GAME_SD = 0.10      # the whole game runs hot or cold (pace, total)
TEAM_SD = 0.14      # one offense outperforms
PASS_SD = 0.30      # that offense does it through the air -> QB + receivers
RUSH_SD = 0.26      # ...or on the ground -> the backfield
RB_PASS_SHARE = 0.35   # an RB's night is part passing game, part rushing game
DST_OPP = -0.85     # a defense moves opposite the offense it faces
DST_OPP_PASS = -0.60  # ...and especially opposite its passing game
DST_OWN = 0.20      # ...and mildly with its own (blowouts create turnovers)
K_TEAM = 0.45       # kickers ride their offense weakly and partly anti-correlate
                    # with its touchdowns (a stalled drive is what makes a FG)

# Within-team substitution. Everything above is SHARED, so on its own it makes
# every teammate rise together — which is how two running backs splitting one
# backfield ended up positively correlated in this model, the exact opposite of
# the truth. These add a zero-sum shock inside a team's position group: targets
# and carries are a fixed budget, so one player's extra share is another's
# missing share. This is the one place the WNBA reallocation logic does apply.
SUB_SD_RB = 0.65    # carries substitute hard — the strongest negative pair
SUB_SD_REC = 0.30   # targets compete, but a good passing day feeds everyone
#
# What these settle at, measured on the real main-slate file (3k sims, every
# team's top player at each position), AFTER each row is calibrated to the
# stated mean and sigma — the calibration is what makes these hold, because
# before it the rows ran 17-31% wider than stated and that excess was all
# uncorrelated noise, which diluted every pair:
#
#   QB -> own WR1        +0.46    DST -> opposing QB   -0.23
#   QB -> own TE1        +0.40    own RB1 -> own RB2   -0.24
#   QB -> own RB1        +0.26    different game        0.00
#   own WR1 -> own WR2   +0.22
#
# The receiver pair staying positive is deliberate: a good passing day lifts
# every target-earner, and the competition for targets only partly offsets it.
# The backfield is the pair that has to come out clearly negative, because two
# backs really are splitting one budget. The factor SIZES are still judgement
# — there is no NFL results history yet — and are the first thing to re-fit.

# Bodies below this projection do not join a team's backfield or receiving
# group. With every proj>0 player in the group, a backfield of two real backs
# and two special-teamers is a four-way split, the zero-sum shock is spread
# four ways, and the RB-RB anti-correlation the group exists to create
# collapses from about -0.16 to -0.07.
SUB_MIN_PROJ = 2.0


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


def _sub_group(p):
    """Which within-team budget this player competes for, if any."""
    if p.pos == "RB":
        return "RB"
    if p.pos in ("WR", "TE"):
        return "REC"
    return None


def _player_factors(p):
    """How much of each shared factor this player rides. Returns
    (game, own_team, own_pass, own_rush, opp_team, opp_pass) exposures."""
    if p.is_dst:
        # Points allowed IS the opponent's scoring, so a defense is close to the
        # mechanical opposite of the offense it faces — and most of all of that
        # offense's passing game.
        return (0.5, DST_OWN, 0.0, 0.0, DST_OPP, DST_OPP_PASS)
    if p.pos == "K":
        return (0.8, K_TEAM, 0.0, 0.0, 0.0, 0.0)
    if p.is_qb:
        return (1.0, 1.0, 1.0, 0.0, 0.0, 0.0)
    if p.pos == "RB":
        return (1.0, 1.0, RB_PASS_SHARE, 1.0 - RB_PASS_SHARE, 0.0, 0.0)
    return (1.0, 1.0, 1.0, 0.0, 0.0, 0.0)     # WR / TE


def simulate(players, sims=4000, seed=0):
    """-> {dk_id: [score per sim]}

    Each player's variance is split between the shared factors above and an
    idiosyncratic remainder, and every row is then calibrated so its mean is
    the projection and its spread the stated standard deviation — exactly.

    Boom% is deliberately NOT used to shape the distribution. A two-piece
    mixture built on it once lived here. Backing the threshold out of the
    vendor's own Proj/SD/Boom gives ~3.9x salary for RBs, 4.1x WRs, 5.2x QBs —
    not a flat 5x — and built on 5x the mixture doubled star players' sigma,
    halved the QB-to-receiver correlation the stacking exists to exploit, and
    still missed its own Boom target. Measured out of sample it made the
    lineups worse. Boom is still read and logged, so a results review can
    test whether it predicts anything.
    """
    rng = random.Random(seed)
    teams = sorted({p.team for p in players if p.team})
    tidx = {t: i for i, t in enumerate(teams)}
    # One factor PER GAME, not one for the slate. On a showdown there is a
    # single game and this is the old behaviour; on a main slate a single shared
    # factor would make all twelve games run hot or cold together, which would
    # both understate lineup variance and invent correlation between players who
    # never share a field.
    games = sorted({p.game for p in players if p.game})
    gidx = {g: i for i, g in enumerate(games)}
    game_f = [[max(0.25, 1 + GAME_SD * rng.gauss(0, 1)) for _ in range(sims)]
              for _ in games]
    team_f, pass_f, rush_f = [], [], []
    for _ in teams:
        team_f.append([max(0.2, 1 + TEAM_SD * rng.gauss(0, 1)) for _ in range(sims)])
        pass_f.append([max(0.2, 1 + PASS_SD * rng.gauss(0, 1)) for _ in range(sims)])
        rush_f.append([max(0.2, 1 + RUSH_SD * rng.gauss(0, 1)) for _ in range(sims)])

    # Zero-sum share shocks inside each team's backfield and receiving corps.
    # Drawing one value per member and subtracting the group mean makes it pure
    # redistribution: the group's total is untouched, but who gets it moves.
    sub, sub_n = {}, {}
    groups = {}
    for p in players:
        g = _sub_group(p)
        if g and p.proj >= SUB_MIN_PROJ and p.team:
            groups.setdefault((p.team, g), []).append(p)
    for (tm, g), members in groups.items():
        if len(members) < 2:
            continue
        width = SUB_SD_RB if g == "RB" else SUB_SD_REC
        draws = [[rng.gauss(0, 1) for _ in members] for _ in range(sims)]
        for s in range(sims):
            row = draws[s]
            mean = sum(row) / len(row)
            for m, z in zip(members, row):
                sub.setdefault(m.dk_id, [1.0] * sims)[s] = max(
                    0.15, 1 + width * (z - mean))
        for m in members:
            sub_n[m.dk_id] = len(members)

    out = {}
    for p in players:
        if p.proj <= 0:
            out[p.dk_id] = [0.0] * sims
            continue
        gx, tx, px, rx, ox, opx = _player_factors(p)
        ti = tidx.get(p.team, 0)
        # An opponent the file does not name gets NO opponent factor, rather
        # than the old 1 - ti fallback, which on a 24-team slate pointed at
        # whichever team happened to sit next to this one in the index.
        oi = tidx.get(p.opponent)
        gi = gidx.get(p.game, 0)
        game = game_f[gi]

        # Systematic relative variance this player is exposed to. A zero-sum
        # draw minus its group mean has variance width^2 * (1 - 1/n), not
        # width^2 — charging the full width over-counted it and left too
        # little for the player's own noise.
        gsub = _sub_group(p)
        n_sub = sub_n.get(p.dk_id, 0)
        sub_w = (SUB_SD_RB if gsub == "RB" else SUB_SD_REC) if n_sub else 0.0
        sub_var = sub_w ** 2 * (1.0 - 1.0 / n_sub) if n_sub else 0.0
        sys_var = ((gx * GAME_SD) ** 2 + (tx * TEAM_SD) ** 2
                   + (px * PASS_SD) ** 2 + (rx * RUSH_SD) ** 2
                   + (ox * TEAM_SD) ** 2 + (opx * PASS_SD) ** 2
                   + sub_var)
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

        sub_row = sub.get(p.dk_id)
        row = []
        for s in range(sims):
            mult = (game[s] ** gx)
            mult *= team_f[ti][s] ** (tx * damp)
            if px:
                mult *= pass_f[ti][s] ** (px * damp)
            if rx:
                mult *= rush_f[ti][s] ** (rx * damp)
            if ox and oi is not None:
                mult *= team_f[oi][s] ** (ox * damp)
            if opx and oi is not None:
                mult *= pass_f[oi][s] ** (opx * damp)
            if sub_row is not None:
                mult *= sub_row[s] ** damp
            row.append(max(0.0, _gamma_draw(p.proj, idio_sd, rng) * mult))
        # Land the row on the stated mean and sigma. Multiplying lognormal-ish
        # factors does not preserve the mean (E[f^a] != 1 for a != 1), which
        # biased every defense high by 6-8% and damped backs low by 4-6%; and
        # the shared-plus-own split only approximates the stated sigma — every
        # position ran 14-31% wide, all of it uncorrelated noise diluting the
        # pairs. Rescale so both hold exactly; an affine map leaves the
        # correlations alone.
        out[p.dk_id] = _calibrate(row, p.proj, total_sd)
    return out


def _calibrate(row, mean_t, sd_t):
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


def score_lineup(lu, mat, sims):
    """Captain scores 1.5x. -> [total per sim]"""
    cpt = mat[lu.cpt.dk_id]
    rows = [mat[p.dk_id] for p in lu.flex]
    return [cpt[s] * CAPTAIN_MULT + sum(r[s] for r in rows) for s in range(sims)]


# The score a lineup has to clear to count as a win in that simulated world.
#
# This used to be the MAX of an unweighted 1,200-lineup sample, and a sampled
# maximum is a noisy extreme: on SF @ LAR it left 1,141 of 4,000 candidates at
# exactly zero and gave the whole pool only 45 distinct win values, so the
# winning roster sat in a 740-way tie spanning ranks #305 to #2,859 and the
# duplication divisor — not the simulator — did the actual ranking.
#
# So the same bar the main slate uses: the field's 99th percentile, sampled in
# proportion to how many entries each lineup really represents. Weighting
# matters because the vendor exports DISTINCT lineups with a Dupes count; drawn
# uniformly, a one-off counts as much as a roster 500 people entered.
BAR_QUANTILE = 0.99
BAR_SAMPLE = 2000
BAR_WEIGHTED = True


def field_bar(field_entries, mat, sims, sample=None, seed=0, quantile=None,
              weighted=None):
    """The score to beat per sim — the field's BAR_QUANTILE score.

    Kept as a running top-k heap per sim rather than a sample x sims matrix,
    which at 2,000 x 4,000 would be a quarter of a gigabyte of Python floats.
    """
    rng = random.Random(seed + 11)
    usable = [e for e in field_entries
              if e.get("cpt") is not None and len(e.get("flex") or []) == ROSTER_SIZE - 1]
    if not usable:
        return None, 0
    sample = sample or BAR_SAMPLE
    q = BAR_QUANTILE if quantile is None else quantile
    if BAR_WEIGHTED if weighted is None else weighted:
        picks = rng.choices(usable, weights=[1.0 + (e.get("dupes") or 0.0)
                                             for e in usable], k=sample)
    else:
        picks = usable if len(usable) <= sample else rng.sample(usable, sample)
    rows = []
    for e in picks:
        cpt = mat.get(e["cpt"].dk_id)
        fl = [mat.get(p.dk_id) for p in e["flex"]]
        if cpt is None or any(r is None for r in fl):
            continue
        rows.append((cpt, fl))
    if not rows:
        return None, 0
    keep = max(1, int(round((1.0 - q) * len(rows))))     # k-th largest = bar
    bar = [0.0] * sims
    for s in range(sims):
        h = []
        for cpt, fl in rows:
            v = cpt[s] * CAPTAIN_MULT + sum(r[s] for r in fl)
            if len(h) < keep:
                heapq.heappush(h, v)
            elif v > h[0]:
                heapq.heapreplace(h, v)
        bar[s] = h[0]
    return bar, len(rows)


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


def estimated_dupes(lu, idx, scale=1.0, field_n=0.0):
    """How many OPPONENTS we expect to be holding this exact roster.

    The index counts field entries holding the roster, and ours is an extra
    entry on top of those — so every one of them is an opponent we share with.
    Subtracting one used to treat a field entry as if it were ours, which made a
    roster the field builds exactly once score ZERO duplication while a roster
    the field never builds scored about 4.7. That is backwards, and it is not a
    rounding error: it made the ranking actively prefer rosters the vendor
    generates. On a real showdown slate it put 145 of 150 entries at a modelled
    zero when each was genuinely shared with about five opponents.
    """
    hit = idx.get(lu.key())
    if hit is not None:
        return max(0.0, hit * scale)
    # Not in the vendor pool at all -> the field is unlikely to build it. Use a
    # small ownership-driven estimate rather than claiming zero, against the
    # size of the field actually modelled rather than a hard-coded number.
    p = 1.0
    for pl in lu.flex:
        p *= max(pl.ownership, 0.1) / 100.0
    p *= max(lu.cpt.cpt_own or lu.cpt.ownership / 3.0, 0.1) / 100.0
    return p * (field_n if field_n > 0 else 50_000.0) * scale


# --- construction --------------------------------------------------------
SPLIT_TARGETS = {"5-1": 0.45, "4-2": 0.40, "3-3": 0.15}
# Unspent salary. The brief found leftover is a null on showdown win rate
# (beta +0.001) but NOT on duplication (r = -0.30) — cheaper lineups are less
# duplicated, which matters when 70% of showdown lineups carry a dupe. Measured
# on the real slate this IS a lever, not a junk filter: removing it cost 0.8
# projected points per lineup, and tightening it to 500 raised expected
# duplicates by 7.9. It stays where it is.
MAX_LEFTOVER = 5000

# A roster spot projected under this is dead weight, not a punt. The real slate
# runs Efton Chism III at 0.20 projected points and Tanner Arkin at 0.15 — a
# $200 body exists only so the cap can be reached, and since leftover salary is
# a null on showdown win rate there is no reason to reach it through one. This
# is the showdown equivalent of the WNBA minutes gate: a floor on whether a slot
# has any path to a useful score, not a grade on how good the player is.
MIN_PROJ = 2.0
OWN_LEAN = 0.35          # POSITIVE = lean toward the field. See below.
CAPTAIN_CAP = 0.28       # share of entries any one captain may hold. A rail:
                         # on the slates built so far the top captain sat at
                         # 12-13 of 75, so it has never bound.
PLAYER_CAP = 0.65        # showdown must run high: 6 of ~68 players fill a lineup.
                         # This one DOES bind (49 of 75 on the real slate).
MAX_OVERLAP = 4          # of 6, before two entries are near-duplicates

# The most of your SIDE-TAKING entries that may sit on one team. The split
# quotas above say how lopsided a lineup is; they say nothing about WHICH side,
# and that was an unmanaged output. Construction is already even — it picks the
# major team on a coin flip — but the vendor arm is a sample of the field, so it
# inherits whichever way the crowd leaned. On SF @ LAR that came out 19 SF-major
# to 43 LAR-major in the vendor arm against 32/32 in our own, and the crowd's
# side lost: 82 of the contest's top 100 were the shape the vendor arm had three
# of.
#
# Measured as a share of SIDE-TAKING entries, not of all entries. A fifth of a
# showdown set is usually even (3-3), which is a bet on neither team, so a cap
# written against the total leaves room for a 62/38 lean and calls it neutral.
# The running form below compares each side against the sides taken so far, so
# it converges on a genuine balance instead of a nominal one.
#
# 0.50 is neutral, NOT contrarian. It does not bet against the field, it only
# stops an arm quietly betting with it. Raise toward 1.0 to let a lean through;
# there is no setting here that deliberately fades the field, because the data
# to justify one does not exist yet — two showdown slates is not a finding.
SIDE_CAP = 0.50

# Below this many entries on a side, the cap does not apply. Without it the
# first few picks would have to alternate teams strictly, which hands the
# ordering to whichever side happens to rank first rather than to merit.
SIDE_SLACK = 6


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


# How many of YOUR OWN players a defense may face before the roster is refused.
# This was 3, and at 3 it does something nobody intended: a six-player showdown
# roster split 3-3 holding one defense ALWAYS has exactly three opposing
# non-DST players, so the rule banned every 3-3 construction with a defense.
# Measured against the real NE @ SEA field, it ruled out 733 of the 788 such
# lineups the field built, and seven of the top eight scoring lineups on the
# slate — a whole shape removed by a side effect.
#
# The structural argument for relaxing it does not depend on that result. The
# simulator ALREADY prices this: a defense loads -0.85 on its opponent's team
# factor and -0.60 on their passing game, so pairing one with the offense it
# faces is penalised in the ranking on its own merits. A hard ban on top of that
# is the same belief counted twice, and unlike the simulator it cannot tell a
# cheap defense in a shootout from an expensive one in a blowout.
#
# At 4 it still refuses the constructions that are close to self-cancelling —
# a defense against four or five of your own — and lets the simulation price
# the rest.
DST_MAX_AGAINST = 4


def _dst_ok(players):
    """Refuse a defense facing too many of your own players.

    Points allowed IS the opponent's scoring, so the two partly cancel. This is
    the floor under that idea; the pricing is the simulator's job.
    """
    cap = DST_MAX_AGAINST
    for d in players:
        if not d.is_dst:
            continue
        against = sum(1 for p in players
                      if not p.is_dst and p.team and p.team == d.opponent)
        if against >= cap:
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
    # The sharp's pool binds DURING construction, not as a filter afterwards.
    # As a post-filter it merely wastes tries here (a 25-name sheet against a
    # 40-player board passes about one build in sixteen) and fails outright on a
    # main slate, so both builders enforce it the same way.
    def allowed(p, off):
        return (max_off_pool is None or p.in_pool or p.core
                or off < max_off_pool)

    cpt_pool = [p for p in (cpt_pool or pool) if allowed(p, 0)] or pool
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
        off = 0 if (cpt.in_pool or cpt.core) else 1
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
                if not allowed(p, off):
                    continue
                if salary + p.salary > SALARY_CAP:
                    continue
                # leave enough room for the remaining slots
                if slots_left > 1:
                    floor_rest = (cheap2 if p.salary == cheap1 else cheap1)
                    if salary + p.salary + floor_rest * (slots_left - 1) > SALARY_CAP:
                        continue
                elig.append(p)
                spend = min(p.salary / per_slot, 1.6)
                w.append((max(p.proj, 0.1) ** 3) * (0.35 + spend))
            if not elig:
                ok = False
                break
            p = _weighted_pick(elig, w, rng)
            picked.append(p)
            used.add(p.dk_id)
            salary += p.salary
            left[p.team] -= 1
            if not (p.in_pool or p.core):
                off += 1
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
                          and q.salary <= budget and q.proj > worst.proj
                          and allowed(q, off - (0 if (worst.in_pool or worst.core)
                                                else 1))]
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


def rank(lineups, mat, bar, sims, dupes_idx, own_lean=OWN_LEAN, dupe_scale=1.0,
         field_n=0.0):
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
        w = win_rate(sc, bar, sims)
        d = estimated_dupes(lu, dupes_idx, scale=dupe_scale, field_n=field_n)
        on = (lu.own_sum - lo) / span
        lu.metrics.update({
            "win": round(w, 5),
            "dupes": round(d, 2),
            "mean": round(sum(sc) / sims, 2),
            "ownLean": round(1 + own_lean * (2 * on - 1), 3),
        })
        lu.metrics["score"] = (w / (1.0 + d)) * lu.metrics["ownLean"]
    lineups.sort(key=lambda l: -l.metrics["score"])
    return lineups


def select(lineups, n, *, captain_cap=CAPTAIN_CAP,
           player_cap=PLAYER_CAP, max_overlap=MAX_OVERLAP, side_cap=SIDE_CAP,
           split_targets=None, core_floors=None, prior=None):
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

    Duplicate rosters are dropped here, and `exclude` carries the rosters an
    earlier arm already took. Both arms chase the same high-win-rate shapes out
    of the same player pool, so they collide: on the real showdown slate a 75/75
    split returned three identical pairs, one copy in each arm. A second copy of
    a roster you already hold buys no coverage — if it hits, the two entries
    just split the tied places between them — so against a first-place objective
    it is an entry spent on an outcome you already own.
    """
    # `prior` is what an earlier arm already took. Its rosters are excluded,
    # its exposure counts are inherited so the caps hold across ALL entries
    # rather than per arm, and its rosters are checked for overlap — at the
    # twin threshold (five of six): on a 68-player board two 5-1 lineups on
    # the same side share four players almost by definition, and holding the
    # other arm to the within-arm cap pushed it off that shape entirely.
    prior = list(prior or [])
    seen_keys = {lu.key() for lu in prior}
    unique = []
    for lu in lineups:
        k = lu.key()
        if k in seen_keys:
            continue
        seen_keys.add(k)
        unique.append(lu)
    lineups = unique
    total = n + len(prior)
    cap_ct = max(1, round(captain_cap * total))
    ply_ct = max(1, round(player_cap * total))
    quota = {}
    if split_targets:
        quota = {k: int(round(v * n)) for k, v in split_targets.items()}
    chosen, sets = [], []
    cross = max_overlap + 1
    cpt_ct, ply_used, split_ct, side_used, split_side_ct = {}, {}, {}, {}, {}
    prior_sets = [set(lu.ids()) for lu in prior]
    for lu in prior:
        cpt_ct[lu.cpt.dk_id] = cpt_ct.get(lu.cpt.dk_id, 0) + 1
        s = lu.major_side()
        if s:
            side_used[s] = side_used.get(s, 0) + 1
        for p in lu.players:
            ply_used[p.dk_id] = ply_used.get(p.dk_id, 0) + 1

    def take(lu):
        chosen.append(lu)
        sets.append(set(lu.ids()))
        cpt_ct[lu.cpt.dk_id] = cpt_ct.get(lu.cpt.dk_id, 0) + 1
        split_ct[lu.split_label()] = split_ct.get(lu.split_label(), 0) + 1
        s = lu.major_side()
        if s:
            side_used[s] = side_used.get(s, 0) + 1
            k = (lu.split_label(), s)
            split_side_ct[k] = split_side_ct.get(k, 0) + 1
        for p in lu.players:
            ply_used[p.dk_id] = ply_used.get(p.dk_id, 0) + 1

    def ok(lu, overlap, honour_side=True):
        if cpt_ct.get(lu.cpt.dk_id, 0) >= cap_ct:
            return False
        side = lu.major_side()
        if honour_side and side_cap and side_cap < 1.0 and side:
            taken_sides = sum(side_used.values()) + 1
            if side_used.get(side, 0) >= max(SIDE_SLACK, side_cap * taken_sides):
                return False
        if any(ply_used.get(i, 0) >= ply_ct for i in lu.ids()):
            return False
        s = set(lu.ids())
        if any(len(s & t) > cross for t in prior_sets):
            return False
        return not any(len(s & t) > overlap for t in sets)

    taken = set()

    def pass_over(overlap, want_split=None, limit=None, need=None, floor=None,
                  honour_side=True, want_side=None):
        counter = split_side_ct if want_side else split_ct
        ckey = (want_split, want_side) if want_side else want_split
        for lu in lineups:
            if len(chosen) >= n or (limit is not None and counter.get(ckey, 0) >= limit):
                return
            if floor is not None and ply_used.get(need, 0) >= floor:
                return
            if id(lu) in taken:
                continue
            if want_split and lu.split_label() != want_split:
                continue
            if want_side and lu.major_side() != want_side:
                continue
            if need is not None and need not in lu.ids():
                continue
            if ok(lu, overlap, honour_side):
                take(lu)
                taken.add(id(lu))

    # Cores first, under the same caps and overlap as everything else — they
    # used to be swapped in afterwards past every cap. Then quotas, best-first
    # inside each shape, then fill on merit.
    # A core is the sharp's explicit instruction, so it outranks the side cap:
    # two cores on the same team would otherwise have their floors silently
    # starved by a rail the user never asked for. The cap still shapes every
    # other pass, so the lean it allows is only ever the lean the cores force.
    for cid, floor in (core_floors or {}).items():
        pass_over(max_overlap, need=cid, floor=floor, honour_side=False)
    # Shape quotas, each lopsided shape split evenly between the two teams.
    #
    # Doing this INSIDE the shape quota rather than as a blanket cap is what
    # keeps the two rules from fighting. A running side cap applied to every
    # pass does balance the sides, but it balances them by rejecting the
    # blocked side's 5-1 lineups and letting the merit fill replace them with
    # 3-3s — measured, it pushed even lineups from 29 to 49 of 150 and blew
    # past the 15% target for the one shape the field already over-builds.
    # Pinning the side within each shape holds both at once.
    teams_seen = [t for t, _ in sorted(
        ((t, c) for t, c in side_used.items()), key=lambda kv: -kv[1])]
    for lu in lineups:
        s = lu.major_side()
        if s and s not in teams_seen:
            teams_seen.append(s)
    # The vendor arm has no shape quota of its own — it is THEIR pool, and
    # imposing our shape mix on it would stop it being an independent control.
    # But with no quota to hang the side balance on, the running cap does the
    # balancing by falling through to 3-3s: measured across both slates it cost
    # six 5-1 lineups per 150 and bought twelve even ones, drifting the arm
    # toward the one shape the field already over-builds. So when the cap is on
    # and no quota was given, take the arm's OWN natural shape mix — what its
    # top n would have been — and balance the sides inside that. It keeps the
    # shape mix the arm chose while removing the side bet it did not choose.
    if side_cap and side_cap < 1.0 and not quota and len(teams_seen) >= 2:
        for lu in lineups[:n]:
            quota[lu.split_label()] = quota.get(lu.split_label(), 0) + 1
    for shape, want in sorted(quota.items(), key=lambda kv: -kv[1]):
        a, b = (int(x) for x in shape.split("-"))
        if a == b or not side_cap or side_cap >= 1.0 or len(teams_seen) < 2:
            pass_over(max_overlap, want_split=shape, limit=want)
            continue
        share = max(1, int(round(want * side_cap)))
        for t in teams_seen:
            pass_over(max_overlap, want_split=shape, want_side=t, limit=share,
                      honour_side=False)
        pass_over(max_overlap, want_split=shape, limit=want)   # any shortfall
    pass_over(max_overlap)
    for relax in (max_overlap + 1, ROSTER_SIZE):      # loosen rather than under-fill
        pass_over(relax)
    for cid, floor in (core_floors or {}).items():    # still short: overlap relaxed
        if ply_used.get(cid, 0) < floor:
            pass_over(ROSTER_SIZE, need=cid, floor=floor, honour_side=False)

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

    return chosen[:n]


def vendor_arm(field_entries, n, *, captain_cap=CAPTAIN_CAP,
               player_cap=PLAYER_CAP, max_overlap=MAX_OVERLAP, side_cap=SIDE_CAP,
               dupe_scale=1.0, core_floors=None, prior=None):
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
        # Every field copy is an opponent once we enter it — the same count
        # our own arm is charged (see estimated_dupes), not one fewer.
        d = (1.0 + (e.get("dupes") or 0.0)) * dupe_scale
        lu.metrics = {"win": e.get("win", 0.0), "dupes": d,
                      "score": (e.get("win", 0.0)) / (1.0 + d)}
        cands.append(lu)
    cands.sort(key=lambda l: -l.metrics["score"])
    # The side cap matters MORE here than in our own arm. These lineups are a
    # sample of the field, so whichever way the crowd leaned is baked into the
    # supply: on SF @ LAR their in-pool lineups ran 42% LAR-major to 24%
    # SF-major and this arm came out 43-19 the same way, while our own arm —
    # which ranks on beating the field rather than resembling it — came out
    # 32-32 without being told to.
    return select(cands, n, captain_cap=captain_cap, player_cap=player_cap,
                  max_overlap=max_overlap, side_cap=side_cap,
                  core_floors=core_floors, prior=prior)
