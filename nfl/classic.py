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
   than half their win probability.

3. **Bring-backs cut win equity** (-12.7%, standardised beta -0.103 on Win% and
   +0.010 on Cash%). A bring-back hedges game script: it raises the floor and
   cuts the tail. That is right in a cash game and wrong when first place is the
   objective, so it belongs only in the small insurance block.

Duplication also cannot be read straight off the vendor pool here: it models a
capped 10,000 opponents against a contest that holds 416,171, so the Dupes
column understates by up to ~40x and must be scaled.
"""

from __future__ import annotations

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
# QB+2 0.0194, QB+3 0.0201. The field builds QB+3 about 8.7% of the time, so
# this is where the room is. Aimed at depth 2 and 3, with a slice of 1 kept for
# the slates where a third partner cannot be afforded.
STACK_TARGETS = {3: 0.45, 2: 0.40, 1: 0.15}
BRING_BACK_SHARE = 0.15   # the insurance block, and nothing more
MIN_STACK = 1             # never build a lineup with no stack at all
# Ownership lean: NEUTRAL by default, and this is deliberate. Ranking on win
# probability against the field's best score already fades chalk hard on its own
# — measured on the real main slate it lands these lineups at the field's 12th
# ownership percentile with the lean at zero. Adding a negative lean on top
# bought one further point of ownership sum, because the candidate pool is
# already skewed that way and there is nothing left to fade. So the lever fails
# the test every lever here has to pass: it does not change the output enough to
# justify acting on the report's LEAST-trusted finding. It stays exposed, in both
# directions, for a slate where the read says otherwise.
OWN_LEAN = 0.0
QB_CAP = 0.35             # QB exposure IS stack exposure, so it binds tighter
PLAYER_CAP = 0.55
DST_CAP = 0.30
MAX_OVERLAP = 6           # of 9
MIN_PROJ = 3.0            # a roster spot needs some path to a useful score
MAX_LEFTOVER = 2000


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
            by.get(p.pos, by["WR"]).append(p)
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
        return (max_off_pool is None or p.in_pool or p.core
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

        qw = [max(q.proj, 0.1) ** 2 * (0.0 if qb_used.get(q.dk_id, 0) >= qb_room
                                       else 1.0) for q in qbs]
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
        rng.shuffle(mates)
        mates.sort(key=lambda p: -p.proj)
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
            if not (p.in_pool or p.core or licensed):
                off += 1
        if got < min(want, MIN_STACK):
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
                    if not (p.in_pool or p.core):
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
                w.append(max(p.proj, 0.1) ** 3)
            if not elig:
                ok = False
                break
            p = _pick(elig, w, rng)
            picked.append(p)
            used.add(p.dk_id)
            salary += p.salary
            if not (p.in_pool or p.core):
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


def field_bar(entries, mat, sims, sample=1200, seed=0):
    """The score to beat per sim: the best a sample of the field reaches.

    Sampling understates the true field maximum, but it understates it the same
    way for every candidate, so the ordering — which is all we use — holds.
    """
    rng = random.Random(seed + 11)
    usable = [e for e in entries if len(e.get("flex") or []) == ROSTER_SIZE]
    if not usable:
        return None, 0
    picks = usable if len(usable) <= sample else rng.sample(usable, sample)
    bar = [0.0] * sims
    for e in picks:
        rows = [mat.get(p.dk_id) for p in e["flex"]]
        if any(r is None for r in rows):
            continue
        for s in range(sims):
            v = sum(r[s] for r in rows)
            if v > bar[s]:
                bar[s] = v
    return bar, len(picks)


# --- scoring and selection ----------------------------------------------
def score_lineup(lu, mat, sims):
    rows = [mat[p.dk_id] for p in lu.players]
    return [sum(r[s] for r in rows) for s in range(sims)]


def rank(lineups, mat, bar, sims, dupes_idx, own_lean=OWN_LEAN, dupe_scale=1.0,
         field_n=0.0):
    owns = [lu.own_sum for lu in lineups] or [0]
    lo, hi = min(owns), max(owns)
    span = (hi - lo) or 1.0
    for lu in lineups:
        sc = score_lineup(lu, mat, sims)
        w = (sum(1 for s in range(sims) if sc[s] > bar[s]) / sims) if bar else 0.0
        d = estimated_dupes(lu, dupes_idx, scale=dupe_scale, field_n=field_n)
        on = (lu.own_sum - lo) / span
        lu.metrics.update({
            "win": round(w, 5), "dupes": round(d, 2),
            "mean": round(sum(sc) / sims, 2),
            "stack": lu.stack_depth(), "bringBack": lu.bring_back(),
        })
        base = w if bar else (sum(sc) / sims) / 200.0
        lu.metrics["score"] = (base / (1.0 + d)) * (1 + own_lean * (2 * on - 1))
    lineups.sort(key=lambda l: -l.metrics["score"])
    return lineups


def select(lineups, n, *, player_cap=PLAYER_CAP, qb_cap=QB_CAP, dst_cap=DST_CAP,
           max_overlap=MAX_OVERLAP, stack_targets=None, core_floors=None,
           exclude=None):
    """Pick the final N under exposure, overlap and stack-shape quotas.

    Stack depth is a quota for the same reason the showdown team split is: the
    finding is measured at MATCHED projection, and raw ranking does not match
    projection. Forcing a third pass-catcher from one team means reaching deeper
    into that team's roster, so those lineups carry slightly less projection and
    lose a straight ranking contest while winning every like-for-like one. That
    is why the field builds QB+3 under 9% of the time.
    """
    # Duplicate rosters are dropped here, and `exclude` carries what an earlier
    # arm already took. Both arms chase the same shapes out of the same pool, so
    # they collide — a real showdown split produced three identical pairs. A
    # second copy of a roster you already hold buys no coverage: if it hits, the
    # two entries just split the tied places between them.
    seen_keys = set(exclude or ())
    unique = []
    for lu in lineups:
        k = lu.key()
        if k in seen_keys:
            continue
        seen_keys.add(k)
        unique.append(lu)
    lineups = unique
    ply = max(1, round(player_cap * n))
    qbc = max(1, round(qb_cap * n))
    dstc = max(1, round(dst_cap * n))
    quota = ({d: int(round(v * n)) for d, v in stack_targets.items()}
             if stack_targets else {})
    chosen, sets, taken = [], [], set()
    used, qb_ct, dst_ct, depth_ct = {}, {}, {}, {}

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
            if p.is_qb and qb_ct.get(p.dk_id, 0) >= qbc:
                return False
            if p.is_dst and dst_ct.get(p.dk_id, 0) >= dstc:
                return False
            if used.get(p.dk_id, 0) >= ply:
                return False
        s = set(lu.ids())
        return not any(len(s & t) > overlap for t in sets)

    def sweep(overlap, depth=None, limit=None):
        for lu in lineups:
            if len(chosen) >= n:
                return
            if limit is not None and depth_ct.get(depth, 0) >= limit:
                return
            if id(lu) in taken:
                continue
            if depth is not None and lu.stack_depth() != depth:
                continue
            if ok(lu, overlap):
                take(lu)

    for d, want in sorted(quota.items(), key=lambda kv: -kv[1]):
        sweep(max_overlap, depth=d, limit=want)
    sweep(max_overlap)
    for relax in (max_overlap + 1, ROSTER_SIZE):
        sweep(relax)
    if len(chosen) < n:                      # hold the QB cap longest
        for lu in lineups:
            if len(chosen) >= n:
                break
            q = lu.qb()
            if id(lu) in taken or (q and qb_ct.get(q.dk_id, 0) >= qbc):
                continue
            take(lu)
    for lu in lineups:
        if len(chosen) >= n:
            break
        if id(lu) not in taken:
            take(lu)
    chosen = chosen[:n]

    # Cores last, so a conviction pick outranks every preference above it.
    if core_floors:
        picked = {id(c) for c in chosen}

        def held(cid):
            return sum(1 for lu in chosen if cid in lu.ids())

        for cid, need in core_floors.items():
            while held(cid) < need:
                cand = next((c for c in lineups
                             if cid in c.ids() and id(c) not in picked), None)
                if cand is None:
                    break
                drop = next((lu for lu in reversed(chosen)
                             if cid not in lu.ids()
                             and all(oid not in lu.ids() or held(oid) - 1 >= o
                                     for oid, o in core_floors.items()
                                     if oid != cid)), None)
                if drop is None:
                    break
                chosen.remove(drop)
                picked.discard(id(drop))
                chosen.append(cand)
                picked.add(id(cand))
    return chosen[:n]


def vendor_arm(field_entries, n, *, dupe_scale=1.0, **kw):
    """Their pool, re-ranked on Win% / (1 + Dupes) — the control arm."""
    cands = []
    for e in field_entries:
        ps = [p for p in (e.get("flex") or []) if p is not None]
        # Length alone is not legality. A 9-player roster with no TE, or two
        # QBs, makes slots() hand the writer eight players or a None, and the
        # upload file comes out malformed or the build crashes outright.
        if len(ps) != ROSTER_SIZE or not _legal_final(ps) or not dst_ok(ps):
            continue
        lu = Lineup(ps, source="vendor")
        d = (e.get("dupes") or 0.0) * dupe_scale
        lu.metrics = {"win": e.get("win", 0.0), "dupes": round(d, 2),
                      "roi": e.get("roi", 0.0), "cash": e.get("cash", 0.0),
                      "stack": lu.stack_depth(), "bringBack": lu.bring_back(),
                      "score": e.get("win", 0.0) / (1.0 + d)}
        cands.append(lu)
    cands.sort(key=lambda l: -l.metrics["score"])
    return select(cands, n, **kw)
