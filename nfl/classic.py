"""DraftKings NFL Classic (main slate) — roster rules, builder, selection.

Classic is a different game from showdown, not a bigger version of it, and the
research brief is explicit that three things point the OPPOSITE way:

1. **Ownership.** At matched projection, chalk WINS in showdown (+106%) and
   LOSES on a main slate (-32%). The mechanism is pool size: with 68 draftable
   players consensus has nowhere to hide, so fading it means playing worse
   players; with 300+ it becomes a congestion signal instead. A single
   sport-level ownership setting is guaranteed to be wrong for one of the two.
   *This is also the least-trusted finding in the brief* — it was measured from
   the vendor's own simulation, and that simulation contains a leverage penalty
   as an assumption, so it may be reading its own prior back. Hence a weak
   default and a switch, rather than something baked in.

2. **Stacking is the dominant lever**, where showdown's equivalent is the team
   split. Holding projection AND ownership constant, each extra pass-catcher
   stacked with your QB is worth +31% relative win probability — about 2.4
   projected points. Unstacked lineups are 9.6% of the field and give up more
   than half their win probability. (Those shares are the vendor's, whose
   stack column counts running backs; on this module's pass-catcher
   definition the real field builds QB+3 4.6% of the time.)

3. **Bring-backs cut win equity** (-12.7%, standardised beta -0.103 on Win% and
   +0.010 on Cash%). A bring-back hedges game script: it raises the floor and
   cuts the tail. That is right in a cash game and wrong when first place is the
   objective, so it belongs only in the small insurance block.

Duplication also cannot be read straight off the vendor pool here: it models a
capped 10,000 opponents against a contest that holds 416,171, so the Dupes
column understates by up to ~40x and must be scaled.
"""

from __future__ import annotations

import heapq
import random

from dk import SALARY_CAP

# --- the ruleset ---------------------------------------------------------
# QB, RB, RB, WR, WR, WR, TE, FLEX, DST. No kicker on classic — confirmed from
# the real export, where ownership sums to exactly 900% = nine slots.
ROSTER = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST")
ROSTER_SIZE = len(ROSTER)
NEED = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}   # + 1 FLEX
FLEX_POS = ("RB", "WR", "TE")
MAX_POS = {"QB": 1, "RB": 3, "WR": 4, "TE": 2, "DST": 1}  # the flex is the +1

# --- construction targets ------------------------------------------------
# Win rate at matched projection, from the brief: no stack 0.0077, QB+1 0.0156,
# QB+2 0.0194, QB+3 0.0201. The field builds QB+3 about 4.7% of the time by this
# definition (pass-catchers only; the vendor's own column counts backs too and
# says 8.8%), so this is where the room is. No floor on QB+1: a slice of the
# weakest shape was kept for slates where a third partner cannot be afforded,
# and measured out of sample it cost first-place equity (1.21x the vendor arm
# with it, 1.33x without, under the calibrated simulator).
STACK_TARGETS = {3: 0.45, 2: 0.55}
BRING_BACK_SHARE = 0.15   # the insurance block, and nothing more
MIN_STACK = 1             # never build a lineup with no stack at all
# Ownership lean: NEUTRAL by default, and this is deliberate. Ranking on win
# probability already fades chalk on its own — with the lean at zero these
# lineups land well below the field's median ownership. The lever is live in
# both directions (measured: -0.2 moves the ownership sum 4-10 points lower,
# +0.25 moves it 15-17 higher), but the finding that would justify a default
# in either direction is the report's LEAST-trusted one, so it stays at zero
# until graded results say otherwise.
OWN_LEAN = 0.0
QB_CAP = 0.35             # QB exposure IS stack exposure. A rail: the top QB
                          # sits at 17-21 of 75 on real builds, under the cap.
PLAYER_CAP = 0.55
DST_CAP = 0.30

# How hard to tilt the defence toward the offence it is FACING. 0 = off.
#
# A defence's points come from the opponent failing: DraftKings' points-allowed
# tiers are most of the floor, and sacks and turnovers come from a team that is
# behind and pressing. So the question is not how good the defence is, it is how
# likely the other side is to collapse — and on a main slate, unlike a showdown,
# you get to choose the matchup out of a dozen games instead of two.
#
# Measured on the logged main slate, 24 defences scored against their real
# results: the opposing offence's summed projection correlates -0.27 with what a
# defence actually scored, which is a STRONGER read than the defence's own
# projection (+0.23) or its salary (+0.21). Split into thirds by the offence
# faced, the weakest third averaged 7.9 points at 1.08x projection and the
# strongest third 5.2 at 0.97x. The top of the board is where it bites: the three
# defences facing the weakest offences went 13.0, 18.0 and 13.0, and the slate's
# best defence, the Steelers at 18.0, faced the second-weakest offence on the
# board at 5% ownership.
#
# One slate, 24 defences. Not proven — a week effect could produce this. It is
# tilted rather than enforced for that reason: the weight moves, nothing is
# banned, and a defence facing a strong offence can still be built.
DST_MATCHUP = 0.0

# Construction weights every seat by projection CUBED, which is right for a
# position whose projection means something. For a defence in week one it may not:
# on the logged main slate the Titans carried the highest DST projection on the
# board at 8.1, took 21 of 150 lineups before any tilt and 31 after one, and
# scored 0.0. Ranked by the offence they faced instead, the top three defences
# went 13.0, 18.0 and 13.0 — a mean of 14.7 against 10.3 for the top three by
# projection. The cube is what stops the matchup competing: 8.1 cubed buries
# every multiplier you can reasonably put on top of it.
#
# Tested against the real 416,171-entry field, three seeds, 150 lineups. Tilt is
# DST_MATCHUP, exp is this:
#
#   tilt/exp    best rank   top 1%   top 10%   median pct   defences used
#   0.0 / 3.0       2,122      1.0      12.0        56.7%   Titans 20, Jets 15
#   0.0 / 1.5       1,080      1.0      11.0        52.7%   Titans 20, Jets 12
#   0.6 / 3.0         968      1.0      13.7        56.3%   Titans 26, Jets 18
#   0.6 / 1.5       3,133      0.7      13.3        54.7%   Titans 23, Jets 16
#   1.0 / 1.5       2,120      1.0      13.0        54.2%   Titans 22, Jets 18
#
# Nothing here is worth shipping. Flattening alone moves the median percentile
# four points the right way but leaves top-1% flat, and — the point of the whole
# exercise — it does NOT reduce the Titans: 20 lineups at the cube and 20 at 1.5,
# because compressing the weights keeps the ordering that put them on top. The
# tilt raises top-10% and lowers top-1%, which is the trade a GPP does not want,
# and it made exposure-weighted DST points WORSE on the same slate, 6.44 to 6.30,
# by moving Titans 21 -> 31 into a 0.0. Both knobs stay at their no-op values.
DST_EXP = 3.0
MAX_OVERLAP = 6           # of 9
# How hard construction concentrates on the top of the board — see the note on
# engine.FILL_EXP, which carries the numbers. Tested across five real contests
# at eight seeds each and every gap sat inside its own seed noise; on this slate
# 1.0 returned $34 +-6 against $31 +-5 for the cube. Flattening buys variance,
# not money. It stays at 3.
FILL_EXP = 3.0
MIN_PROJ = 3.0            # a roster spot needs some path to a useful score
MAX_LEFTOVER = 2000
CORE_BOOST = 3.0          # construction weight on a core, so its floor is reachable

# Share of a real contest's entries the vendor's rosters account for. Left at
# 1.0 here, which preserves the existing behaviour, because the measurement on a
# finished main slate says the classic duplication model has a far larger
# problem than any coverage factor can fix.
# Contest 193028212: 415,601 entries across 383,126 DISTINCT rosters, 96.4% of
# them played exactly once, and every one of the top 100 finishers was unique
# (median 0 duplicates, max 1). The vendor's 9,860 rosters matched 79 of those
# 415,601 entries — 0.0%. Scaling 10,000 modelled entries up to the full field
# gives x41.56, which is why every vendor lineup reports ~41.6 duplicates when
# the true figure at the top of a main slate is zero. Changing that number is a
# behaviour change on its own evidence, so it waits for its own measurement
# rather than riding along with the showdown fix.
FIELD_COVERAGE = 1.0

# Which vendor column the re-ranked arm sorts on; see vendor_arm below. Win%
# is as coarse here as on a showdown file — 34 distinct values across 9,860
# rows, against 212 for Top 10% — so most of its ordering was ties broken by
# whatever the sort happened to do.
VENDOR_SIGNAL = "top10"

# The score to beat. Ranking on "beat the field's single BEST score" was too
# coarse a target. Measured on the real main slate with 3,000 fixed candidates:
# 764-828 of them cleared it in ZERO simulations, the rest took one of only
# 12-13 distinct values, and the order among those was noise — change the
# simulation seed and only 12-16 of the chosen 75 survived. Against the field's
# 99th percentile: 2-3 zeros, 63-65 distinct values, 45-47 of 75 survive a
# seed change. It is also the tier that actually pays. The sample is drawn
# WEIGHTED by (1 + Dupes), because a row the field holds forty times is forty
# opponents, not one.
BAR_QUANTILE = 0.99
BAR_SAMPLE = 2000
BAR_WEIGHTED = True


class Lineup:
    """One classic roster, stored as a flat list of nine players."""

    __slots__ = ("players", "metrics", "source")

    def __init__(self, players, source="mine"):
        self.players = list(players)
        self.metrics = {}
        self.source = source

    @property
    def salary(self):
        return sum(p.salary for p in self.players)

    @property
    def proj(self):
        return round(sum(p.proj for p in self.players), 2)

    @property
    def own_sum(self):
        return round(sum(p.ownership for p in self.players), 1)

    def ids(self):
        return [p.dk_id for p in self.players]

    def key(self):
        return frozenset(p.dk_id for p in self.players)

    def qb(self):
        return next((p for p in self.players if p.is_qb), None)

    def stack_depth(self):
        """Pass-catchers rostered alongside your own QB. The lever."""
        q = self.qb()
        if not q:
            return 0
        return sum(1 for p in self.players
                   if p is not q and p.team == q.team and p.pos in ("WR", "TE"))

    def bring_back(self):
        """Players from the opponent of your QB's team."""
        q = self.qb()
        if not q:
            return 0
        return sum(1 for p in self.players
                   if p.team == q.opponent and not p.is_dst)

    def stack_label(self):
        d, b = self.stack_depth(), self.bring_back()
        base = f"QB+{d}" if self.qb() else "no QB"
        return base + (f" | {b} OPP" if b else "")

    def slots(self):
        """The nine players in DK's slot order: QB, RB, RB, WR, WR, WR, TE,
        FLEX, DST. The flex is whichever RB/WR/TE is left over."""
        by = {"QB": [], "RB": [], "WR": [], "TE": [], "DST": []}
        for p in self.players:
            if p.pos in by:            # an unknown position is not a receiver
                by[p.pos].append(p)
        for k in by:
            by[k].sort(key=lambda p: -p.proj)
        out, used = [], set()
        for pos, ct in (("QB", 1), ("RB", 2), ("WR", 3), ("TE", 1)):
            for p in by[pos][:ct]:
                out.append(p)
                used.add(id(p))
        flex = next((p for p in self.players
                     if id(p) not in used and p.pos in FLEX_POS), None)
        out.append(flex)
        out.append(by["DST"][0] if by["DST"] else None)
        return out


# --- legality ------------------------------------------------------------
def _counts(players):
    c = {}
    for p in players:
        c[p.pos] = c.get(p.pos, 0) + 1
    return c


def _legal_final(players):
    c = _counts(players)
    if len(players) != ROSTER_SIZE:
        return False
    for pos, need in NEED.items():
        if c.get(pos, 0) < need:
            return False
        if c.get(pos, 0) > MAX_POS[pos]:
            return False
    return sum(c.get(p, 0) for p in FLEX_POS) == 7


def _completable(players):
    """Could this partial roster still become a legal one?"""
    c = _counts(players)
    if any(c.get(pos, 0) > MAX_POS.get(pos, 0) for pos in c):
        return False
    short = sum(max(0, need - c.get(pos, 0)) for pos, need in NEED.items())
    return short <= ROSTER_SIZE - len(players)


def dst_ok(players):
    """Never roster a defense against an offense you are stacking.

    Points allowed IS the opponent's scoring, so the two are close to
    mechanically self-cancelling. The brief calls for a hard constraint rather
    than a soft penalty, and the simulator agrees: a DST loads -0.85 on its
    opponent's team factor and -0.60 on their passing game.
    """
    for d in players:
        if not d.is_dst:
            continue
        against = sum(1 for p in players
                      if not p.is_dst and p.team and p.team == d.opponent)
        if against >= 2:
            return False
    return True


# --- construction --------------------------------------------------------
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


def pool_exempt(p):
    """Seats the sharp's sheet is not expected to cover. -> bool

    DST only, and deliberately. Nobody writes a defence onto a pool sheet: it is
    close to a coin flip, it is picked last out of whatever salary is left, and
    it is the one seat where the sheet carries no opinion. Before this, a sheet
    with no DST could not fill nine seats, so the pool stopped being a hard
    filter for EVERY seat — one missing position quietly turned a 60-name sheet
    into a shortlist across the whole roster. Exempting the seat keeps the other
    eight binding and lets the builder take whatever defence fits.

    This is not a general "fill the gaps" rule. A sheet missing tight ends is a
    sheet with an opinion about tight ends, and that still drops the constraint
    and says so.
    """
    return p.is_dst


def dst_matchup(players):
    """-> {dst dk_id: weight multiplier}, from the offence each defence faces.

    Empty when DST_MATCHUP is 0. The multiplier runs linearly from 1+DST_MATCHUP
    for the defence facing the weakest projected offence on the slate to
    1-DST_MATCHUP for the one facing the strongest, so it re-orders preference
    without excluding anything.
    """
    if not DST_MATCHUP:
        return {}
    off = {}
    for p in players:
        if not p.is_dst and p.proj > 0 and p.team:
            off[p.team] = off.get(p.team, 0.0) + p.proj
    faced = {d.dk_id: off.get(d.opponent, 0.0)
             for d in players if d.is_dst and d.proj > 0 and d.opponent in off}
    if len(faced) < 3:
        return {}
    lo, hi = min(faced.values()), max(faced.values())
    span = (hi - lo) or 1.0
    # 1 at the weakest offence faced, 0 at the strongest
    return {k: 1.0 + DST_MATCHUP * (1.0 - 2.0 * (v - lo) / span)
            for k, v in faced.items()}


def build_candidates(players, n, *, rng=None, stack_targets=None,
                     bring_back_share=BRING_BACK_SHARE, max_off_pool=None,
                     min_proj=MIN_PROJ, max_leftover=MAX_LEFTOVER):
    """Randomised construction built AROUND the QB stack.

    The QB is chosen first because he decides the lineup's whole correlation
    structure — he is the lowest-variance seat on the roster (sigma/projection
    0.42 against 1.34 for receivers) and is not where the ceiling comes from.
    He is the transmission mechanism that turns a receiver's ceiling into a
    lineup-wide one, which is why the stack is built through him.
    """
    matchup = dst_matchup(players)
    rng = rng or random.Random(0)
    stack_targets = stack_targets or STACK_TARGETS
    pool = [p for p in players if p.proj >= min_proj and p.salary > 0
            and p.pos in ("QB", "RB", "WR", "TE", "DST")]
    if len(pool) < ROSTER_SIZE:
        pool = [p for p in players if p.proj > 0 and p.salary > 0]

    # The sharp's pool has to bind DURING construction, not as a filter at the
    # end. Showdown got away with the post-filter because a 40-player board and
    # a 25-name sheet still let roughly one build in sixteen through; on a
    # 745-player board the odds of nine random picks all landing inside a
    # 55-name sheet are effectively zero, so every candidate was rejected and
    # the build returned nothing at all.
    def allowed(p, off):
        return (max_off_pool is None or p.in_pool or p.core or pool_exempt(p)
                or off < max_off_pool)

    qbs = [p for p in pool if p.is_qb and allowed(p, 0)]
    if not qbs:
        return []
    depths = list(stack_targets.items())
    out, seen = [], set()
    # No single QB may take more than this share of the CANDIDATE pool, so the
    # selection stage always has real alternatives. The share has to widen when
    # the board is narrow: a sharp's pool holding five QBs against a flat 10%
    # cap can only ever produce half the candidates asked for.
    qb_room = max(2, int(n * max(0.10, 1.5 / max(1, len(qbs)))))
    qb_used = {}
    tries = 0
    # Give up early on a slate or a pool that cannot produce a legal roster at
    # all, instead of spending the full try budget rejecting rosters that can
    # never complete. On a 700-player board a full budget of failing tries is
    # minutes of silence, which reads as a hang.
    give_up = min(3000, max(500, n // 2))
    while len(out) < n and tries < n * 30:
        tries += 1
        if tries > give_up and len(out) * 100 < tries:
            break        # under 1% accepted: the budget will never fill
        want = _pick([d for d, _ in depths], [w for _, w in depths], rng)
        want_bb = rng.random() < bring_back_share

        qw = [max(q.proj, 0.1) ** 2 * (CORE_BOOST if q.core else 1.0)
              * (0.0 if qb_used.get(q.dk_id, 0) >= qb_room else 1.0) for q in qbs]
        if not any(qw):
            qb_used.clear()
            continue
        qb = _pick(qbs, qw, rng)
        picked, used = [qb], {qb.dk_id}
        salary = qb.salary
        off = 0 if (qb.in_pool or qb.core) else 1

        # Stack partners: pass-catchers on the QB's own team.
        # A CORE quarterback licences his own pass-catchers past the pool
        # filter. Without this a core QB whose team has no receiver on the
        # sharp's sheet can never be built at all — MIN_STACK rejects every
        # attempt — and he lands in zero lineups while the tool reports a
        # guaranteed floor. That silent failure is the exact one this tool has
        # been bitten by before, so the conviction pick wins and the licensed
        # partners do not count against the off-pool allowance.
        licensed = qb.core
        mates = [p for p in pool if p.team == qb.team and p.pos in ("WR", "TE")
                 and p.dk_id not in used and (licensed or allowed(p, off))]
        # Best partners first, ties broken at random so the same QB does not
        # get the identical stack every time. (A shuffle before a stable sort
        # only ever reordered exact-projection ties.)
        mates.sort(key=lambda p: (-p.proj * (CORE_BOOST if p.core else 1.0),
                                  rng.random()))
        got = 0
        for p in mates:
            if got >= want:
                break
            if not (licensed or allowed(p, off)):   # off moves as mates land
                continue
            trial = picked + [p]
            if not _completable(trial):
                continue
            if salary + p.salary > SALARY_CAP - 3000 * (ROSTER_SIZE - len(trial)):
                continue
            picked, got = trial, got + 1
            used.add(p.dk_id)
            salary += p.salary
            if not (p.in_pool or p.core or licensed or pool_exempt(p)):
                off += 1
        # A quarterback the pool allows but cannot stack is built UNSTACKED
        # rather than not at all. The sheet is the instruction: naming a QB and
        # none of his receivers is a decision, and MIN_STACK was overriding it
        # silently — Josh Allen reached 0 of 4,000 candidates as the only
        # Buffalo name on a 61-player sheet while the build reported no legal
        # lineup and gave no reason. This does not loosen the stack rule for
        # anyone else: the floor still applies to every QB who HAS a legal
        # partner, so a stack is skipped only where one was never possible.
        need_stack = MIN_STACK if mates else 0
        if got < min(want, need_stack):
            continue

        # Optional bring-back from the QB's opponent.
        if want_bb:
            opps = [p for p in pool if p.team == qb.opponent
                    and p.pos in ("WR", "TE", "RB") and p.dk_id not in used
                    and allowed(p, off)]
            if opps:
                p = _pick(opps, [max(q.proj, 0.1) ** 2 for q in opps], rng)
                trial = picked + [p]
                if (_completable(trial) and salary + p.salary
                        <= SALARY_CAP - 3000 * (ROSTER_SIZE - len(trial))):
                    picked, salary = trial, salary + p.salary
                    used.add(p.dk_id)
                    if not (p.in_pool or p.core or pool_exempt(p)):
                        off += 1

        # Fill the rest, position-aware, keeping a legal roster reachable.
        #
        # The QB's opponent is closed off here whatever happened above. A
        # bring-back cuts win equity by 12.7%, so it belongs in the insurance
        # block by DELIBERATE choice and nowhere else — and without this, a
        # random fill on a 700-player board walks into one about a third of the
        # time, which quietly turns a 15% policy into a 34% one.
        ok = True
        while len(picked) < ROSTER_SIZE:
            left = ROSTER_SIZE - len(picked)
            c = _counts(picked)
            short = {pos: max(0, need - c.get(pos, 0)) for pos, need in NEED.items()}
            forced = sum(short.values()) >= left
            budget = SALARY_CAP - salary - 3000 * (left - 1)
            elig, w = [], []
            for p in pool:
                if p.dk_id in used or p.salary > budget:
                    continue
                if p.team == qb.opponent and not p.is_dst:
                    continue
                if not allowed(p, off):
                    continue
                if c.get(p.pos, 0) >= MAX_POS.get(p.pos, 0):
                    continue
                if forced and short.get(p.pos, 0) <= 0:
                    continue
                if not _completable(picked + [p]):
                    continue
                elig.append(p)
                # A core gets extra weight here so its floor is reachable at
                # all: with none, a core QB projected 13.6 reached 32 of 4,000
                # candidates and his "guaranteed" floor was a fiction.
                e = DST_EXP if p.is_dst else FILL_EXP
                wt = max(p.proj, 0.1) ** e * (CORE_BOOST if p.core else 1.0)
                if p.is_dst and matchup:
                    wt *= matchup.get(p.dk_id, 1.0)
                w.append(wt)
            if not elig:
                ok = False
                break
            p = _pick(elig, w, rng)
            picked.append(p)
            used.add(p.dk_id)
            salary += p.salary
            if not (p.in_pool or p.core or pool_exempt(p)):
                off += 1
        if not ok or not _legal_final(picked):
            continue
        if salary > SALARY_CAP or SALARY_CAP - salary > max_leftover:
            continue
        if not dst_ok(picked):
            continue
        if max_off_pool is not None and off > max_off_pool:
            continue
        lu = Lineup(picked)
        if lu.key() in seen:
            continue
        seen.add(lu.key())
        qb_used[qb.dk_id] = qb_used.get(qb.dk_id, 0) + 1
        out.append(lu)
    return out


# --- the opponent field ---------------------------------------------------
# Same three readings as showdown, on nine flat slots instead of a captain plus
# five. They live here rather than in engine.py because every one of those
# functions asserts a captain exists, and a classic entry has none.
def field_size(entries):
    """How many opponent entries the vendor pool models.

    On the main slate this matters far more than it did in showdown. The pool
    caps at 10,000 modelled opponents while the contest holds 416,171, so
    duplication read straight off the Dupes column understates by up to ~40x.
    """
    return sum(1.0 + (e.get("dupes") or 0.0) for e in entries
               if len(e.get("flex") or []) == ROSTER_SIZE)


def dupe_index(entries):
    idx = {}
    for e in entries:
        ps = e.get("flex") or []
        if len(ps) != ROSTER_SIZE:
            continue
        key = frozenset(p.dk_id for p in ps)
        idx[key] = idx.get(key, 0.0) + 1.0 + (e.get("dupes") or 0.0)
    return idx


def estimated_dupes(lu, idx, scale=1.0, field_n=0.0):
    """How many opponents we expect to hold this exact nine.

    Classic duplication is a far smaller number than showdown's: nine slots out
    of 700+ players, and only 0.3% of the vendor's modelled pool carries a dupe
    at all, against 70% in showdown. It is kept because it costs nothing and
    because the chalkiest constructions genuinely do get duplicated in a
    400,000-entry field — but it will almost never be the deciding term, and a
    build reporting ~0 average dupes on a main slate is right, not broken.
    """
    hit = idx.get(lu.key())
    if hit is not None:
        # Every field entry holding this roster is an OPPONENT we share with;
        # ours is an extra entry on top. Subtracting one treated a field entry
        # as if it were ours.
        return max(0.0, hit * scale)
    # Not in the vendor pool. Fall back to the independence estimate against the
    # real field size rather than a hard-coded one — the pool models 10,000
    # opponents for a contest that holds 400,000+, so the two differ by ~40x.
    p = 1.0
    for pl in lu.players:
        p *= max(pl.ownership, 0.1) / 100.0
    return p * (field_n if field_n > 0 else 100_000.0) * scale


def field_bar(entries, mat, sims, sample=None, seed=0, quantile=None,
              weighted=None):
    """The score to beat per sim — the field's BAR_QUANTILE score.

    Kept as a running top-k per sim rather than the full sample x sims matrix,
    which at 2,000 x 4,000 would be a quarter of a gigabyte of Python floats.
    """
    rng = random.Random(seed + 11)
    usable = [e for e in entries if len(e.get("flex") or []) == ROSTER_SIZE]
    if not usable:
        return None, 0
    sample = sample or BAR_SAMPLE
    q = BAR_QUANTILE if quantile is None else quantile
    if BAR_WEIGHTED if weighted is None else weighted:
        picks = rng.choices(usable, weights=[1.0 + (e.get("dupes") or 0.0)
                                             for e in usable], k=sample)
    else:
        picks = usable if len(usable) <= sample else rng.sample(usable, sample)
    keep = max(1, int(round((1.0 - q) * len(picks))))   # k-th largest = bar
    heaps = [[] for _ in range(sims)]
    for e in picks:
        rows = [mat.get(p.dk_id) for p in e["flex"]]
        if any(r is None for r in rows):
            continue
        for s in range(sims):
            v = sum(r[s] for r in rows)
            h = heaps[s]
            if len(h) < keep:
                heapq.heappush(h, v)
            elif v > h[0]:
                heapq.heapreplace(h, v)
    return [h[0] if h else 0.0 for h in heaps], len(picks)


# --- scoring and selection ----------------------------------------------
def score_lineup(lu, mat, sims):
    rows = [mat[p.dk_id] for p in lu.players]
    return [sum(r[s] for r in rows) for s in range(sims)]


def rank(lineups, mat, bar, sims, dupes_idx, own_lean=None, dupe_scale=1.0,
         field_n=0.0):
    # Read at call time, not bound as a default — see the note in engine.rank.
    own_lean = OWN_LEAN if own_lean is None else own_lean
    owns = [lu.own_sum for lu in lineups] or [0]
    lo, hi = min(owns), max(owns)
    span = (hi - lo) or 1.0
    for lu in lineups:
        sc = score_lineup(lu, mat, sims)
        w = sum(1 for s in range(sims) if sc[s] > bar[s]) / sims
        d = estimated_dupes(lu, dupes_idx, scale=dupe_scale, field_n=field_n)
        on = (lu.own_sum - lo) / span
        lu.metrics.update({"win": round(w, 5), "dupes": round(d, 2),
                           "mean": round(sum(sc) / sims, 2)})
        lu.metrics["score"] = (w / (1.0 + d)) * (1 + own_lean * (2 * on - 1))
    lineups.sort(key=lambda l: -l.metrics["score"])
    return lineups


def select(lineups, n, *, player_cap=None, player_caps=None, qb_cap=None,
           dst_cap=None, max_overlap=None, stack_targets=None, core_floors=None,
           prior=None):
    """Pick the final N under exposure, overlap and stack-shape quotas.

    Stack depth is a quota for the same reason the showdown team split is: the
    finding is measured at MATCHED projection, and raw ranking does not match
    projection. Forcing a third pass-catcher from one team means reaching deeper
    into that team's roster, so those lineups carry slightly less projection and
    lose a straight ranking contest while winning every like-for-like one. That
    is why the field builds QB+3 under 5% of the time.

    `prior` is what an earlier arm already took: its rosters are excluded (a
    second copy of a roster you hold buys no coverage — if it hits, the two
    entries split the tied places), its exposure counts are inherited so the
    caps hold across ALL entries rather than per arm, and its rosters count for
    overlap at the same six-of-nine bar.
    """
    prior = list(prior or [])
    # Read at call time, not bound as defaults — see the note in engine.rank.
    player_cap = PLAYER_CAP if player_cap is None else player_cap
    qb_cap = QB_CAP if qb_cap is None else qb_cap
    dst_cap = DST_CAP if dst_cap is None else dst_cap
    max_overlap = MAX_OVERLAP if max_overlap is None else max_overlap
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
    ply = max(1, round(player_cap * total))
    # Per-player caps in lineup counts. Named player beats the board-wide number
    # in both directions — see the note in engine.select.
    player_caps = {i: max(0, int(round(v * total)))
                   for i, v in (player_caps or {}).items()}
    qbc = max(1, round(qb_cap * total))
    dstc = max(1, round(dst_cap * total))
    quota = ({d: int(round(v * n)) for d, v in stack_targets.items()}
             if stack_targets else {})
    chosen, taken = [], set()
    sets = [set(lu.ids()) for lu in prior]
    used, qb_ct, dst_ct, depth_ct = {}, {}, {}, {}
    for lu in prior:
        for p in lu.players:
            used[p.dk_id] = used.get(p.dk_id, 0) + 1
            if p.is_qb:
                qb_ct[p.dk_id] = qb_ct.get(p.dk_id, 0) + 1
            if p.is_dst:
                dst_ct[p.dk_id] = dst_ct.get(p.dk_id, 0) + 1

    def take(lu):
        chosen.append(lu)
        sets.append(set(lu.ids()))
        taken.add(id(lu))
        depth_ct[lu.stack_depth()] = depth_ct.get(lu.stack_depth(), 0) + 1
        for p in lu.players:
            used[p.dk_id] = used.get(p.dk_id, 0) + 1
            if p.is_qb:
                qb_ct[p.dk_id] = qb_ct.get(p.dk_id, 0) + 1
            if p.is_dst:
                dst_ct[p.dk_id] = dst_ct.get(p.dk_id, 0) + 1

    def ok(lu, overlap):
        for p in lu.players:
            # A number you typed for this player replaces every board-wide cap
            # that would otherwise bind him, including the positional ones. You
            # asked for him at 10%; a 30% defence cap should not quietly make
            # that 30%, and a 35% quarterback cap should not make it 35%.
            lim = player_caps.get(p.dk_id)
            if lim is not None:
                if used.get(p.dk_id, 0) >= lim:
                    return False
                continue
            if p.is_qb and qb_ct.get(p.dk_id, 0) >= qbc:
                return False
            if p.is_dst and dst_ct.get(p.dk_id, 0) >= dstc:
                return False
            if used.get(p.dk_id, 0) >= ply:
                return False
        s = set(lu.ids())
        return not any(len(s & t) > overlap for t in sets)

    def sweep(overlap, depth=None, limit=None, need=None, floor=None):
        for lu in lineups:
            if len(chosen) >= n:
                return
            if limit is not None and depth_ct.get(depth, 0) >= limit:
                return
            if floor is not None and used.get(need, 0) >= floor:
                return
            if id(lu) in taken:
                continue
            if depth is not None and lu.stack_depth() != depth:
                continue
            if need is not None and need not in lu.ids():
                continue
            if ok(lu, overlap):
                take(lu)

    # Cores FIRST, under the same caps and overlap as everything else. They
    # used to be swapped in at the end past every cap — one showdown core
    # reached 62 of 75 against a cap of 49 and dragged identical-six pairs in
    # with it. A conviction pick outranks the tool's preferences; it does not
    # outrank the exposure rules the user set.
    for cid, floor in (core_floors or {}).items():
        sweep(max_overlap, need=cid, floor=floor)
    for d, want in sorted(quota.items(), key=lambda kv: -kv[1]):
        sweep(max_overlap, depth=d, limit=want)
    sweep(max_overlap)
    for relax in (max_overlap + 1, ROSTER_SIZE):
        sweep(relax)
    # A core still short gets one more look with overlap relaxed — caps held.
    for cid, floor in (core_floors or {}).items():
        if used.get(cid, 0) < floor:
            sweep(ROSTER_SIZE, need=cid, floor=floor)
    # Hold the QB cap longest, and a cap you TYPED longer still — it gives one
    # lineup at a time. This fill used to ignore those caps completely, so a thin
    # board could quietly hand back the exposure you had just capped away.
    def fill(slack, honour_qb):
        for lu in lineups:
            if len(chosen) >= n:
                return
            if id(lu) in taken:
                continue
            q = lu.qb()
            if honour_qb and q and qb_ct.get(q.dk_id, 0) >= qbc:
                continue
            if slack is not None and any(
                    used.get(p.dk_id, 0) >= player_caps[p.dk_id] + slack
                    for p in lu.players if p.dk_id in player_caps):
                continue
            take(lu)

    if len(chosen) < n:
        fill(0, True)
    if player_caps:
        for slack in range(1, n + 1):
            if len(chosen) >= n:
                break
            fill(slack, True)
    fill(None, False)
    return chosen[:n]


def vendor_arm(field_entries, n, *, dupe_scale=1.0, **kw):
    """Their pool, re-ranked on Top 10% — the control arm.

    Two things here are deliberately NOT what the showdown arm does.

    The SIGNAL is Top 10% rather than Win%, for the reason it is on showdown
    and more so: on a real main-slate file Win% takes about 34 distinct values
    across 9,860 rows, so ranking ten thousand lineups by it is really the
    file's ROI order breaking a handful of enormous ties. Top 10% takes 212
    values on the same file and is never zero.

    DUPLICATION IS NOT IN THE RANKING AT ALL, where showdown divides by it.
    That is not an oversight, it is the measurement: on contest 193028212,
    415,601 entries played 383,126 DISTINCT rosters, 96.4% of them exactly
    once, and every one of the top 100 finishers was unique. The vendor's
    9,860 rosters matched 79 of those 415,601 entries — 0.0%. Scaling 10,000
    modelled entries to the full field gave every row an identical d of about
    41.6, so the divisor was a constant: it reordered nothing and reported a
    number that was fiction. A main slate is a 300-player board where nobody
    collides; there is no duplication to price. The figure is still computed
    and shown, because it costs nothing and a future slate may disagree.
    """
    cands = []
    for e in field_entries:
        ps = [p for p in (e.get("flex") or []) if p is not None]
        # Length alone is not legality. A 9-player roster with no TE, or two
        # QBs, makes slots() hand the writer eight players or a None, and the
        # upload file comes out malformed or the build crashes outright.
        if len(ps) != ROSTER_SIZE or not _legal_final(ps) or not dst_ok(ps):
            continue
        lu = Lineup(ps, source="vendor")
        d = (1.0 + (e.get("dupes") or 0.0)) * dupe_scale   # every copy is an opponent
        signal = e.get(VENDOR_SIGNAL, 0.0) or 0.0
        lu.metrics = {"win": e.get("win", 0.0), "top10": e.get("top10", 0.0),
                      "dupes": round(d, 2), "score": signal}
        cands.append(lu)
    cands.sort(key=lambda l: -l.metrics["score"])
    return select(cands, n, **kw)
