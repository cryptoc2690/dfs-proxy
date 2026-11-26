"""The lineup maker (After Lock, The Board And The Handover, Nov 25).

The mini-MAX 150 made from the typed pool instead of picked from Stokastic's
file. Each lineup is the best legal lineup of one lightly shaken night: every
player's projection is drawn around Stokastic's number with his own Std Dev x
MAKER_SCALE, and the search returns the highest total under the cap. Four
parts, each behind its own switch in newbuild.py:

  MAKER_ON     the 150 is made here (False gives back today's lineups exactly)
  MAKER_WHOLE  every lineup spends the whole $50,000 (the search itself only
               returns whole-salary lineups; if none exists for a shaken night
               with the players held, it takes the best that leaves $100, then
               $200, then $300, then any)
  MAKER_CORE   the core: a player flagged starting, $5,500 or less, 5.75+
               projected points per $1,000, who is a new starter or whose team
               has a top-two usage regular starter out. What the build does
               with him is MAKER_CORE_FORM (Core Hold report, Nov 26):
                 "hold"  the two best by points per $1,000 are in 9 lineups of
                         10, any others in half (as handed over Nov 25)
                 "pool"  he is in the pool like any other player, nothing more
                 "free"  in the pool and not weighed down for his ownership:
                         the build takes what his projection earns
                 "line"  held as in "hold" only when he projects between
                         MAKER_CORE_FLOOR % and MAKER_CORE_LINE % owned;
                         outside that, as "free"
                 "mult"  held in no more than MAKER_CORE_MULT x his projected
                         ownership of the lineups (and never more than "hold")
                 "lift"  not held: his projection is lifted by MAKER_CORE_LIFT
                         (a share of it) and he is not weighed down
  MAKER_OWN    ownership weighed: a player outside the core gives up this many
               projected points for each 30 points of projected ownership
               (4.0; 0 turns it off)

Typed removals are never used. A typed cap is a number of lineups and is never
passed. A typed minimum is met first: the player is held until he has his
lineups, pool or not. Sheet ticks reach this module as the caps and minimums
app.run_build already makes of them. Nothing is loaded from a file: the core is
read from role.py's ESPN logs (games before tonight) and tonight's projections.

The search is exact (depth first over the eight slots with a bound), in plain
Python: about 5 to 20 seconds for 150 lineups.
"""

from __future__ import annotations

import collections
import random

from dk import CLASSIC_SLOTS, SALARY_CAP

ORDER = ("C", "PG", "SG", "SF", "PF", "G", "F", "UTIL")   # scarce slots first
BASE = ("PG", "SG", "SF", "PF", "C")

CORE_MAX_SALARY = 5500     # the core rule's price line
CORE_MIN_VALUE = 5.75      # projected points per $1,000
CORE_TOP = 2               # this many core players (best by points per $1,000) ...
CORE_COVER = 0.9           # ... are each in 9 lineups of 10
CORE_REST = 0.5            # any further core player: half the lineups
REG_GAMES = 3              # a regular starter: 3+ games on record ...
REG_START_SHARE = 0.6      # ... and started 60%+ of his last five
NEW_START_SHARE = 0.2      # a new starter: started 20% or less of his last five
USAGE_TOP = 2              # "top-two usage": by shots a game on his team
SCALE = 0.35               # the shake: Std Dev x this
KEEP_OTHERS = 26           # with players held: this many best others are searched


class Solver:
    """players: [(key, salary, eligible slots)]. best() -> (total, [8 keys in
    CLASSIC_SLOTS order]) or (None, None)."""

    def __init__(self, players, cap=SALARY_CAP):
        self.P = [p for p in players if p[1] > 0 and p[2]]
        self.cap = cap
        self.idx = {p[0]: i for i, p in enumerate(self.P)}
        self.sal = [p[1] for p in self.P]
        self.el = [[i for i, p in enumerate(self.P) if s in p[2]] for s in ORDER]
        self.minsal = min(self.sal) if self.sal else 0
        self.L = 0.005

    def tune(self, values):
        """The price of a salary dollar: the L at which the best lineup with no
        cap just fits the cap (found by halving)."""
        lo, hi = 0.0, 0.02
        for _ in range(18):
            mid = (lo + hi) / 2
            self.L = mid
            if self._relaxed(values) > self.cap:
                lo = mid
            else:
                hi = mid
        self.L = hi
        return hi

    def _relaxed(self, values):
        used, tot = set(), 0
        for si in range(8):
            b = max((i for i in self.el[si] if i not in used),
                    key=lambda i: values[i] - self.L * self.sal[i], default=None)
            if b is None:
                continue
            used.add(b)
            tot += self.sal[b]
        return tot

    def best(self, values, lock=(), ban=(), min_salary=0):
        """The highest-valued legal lineup under the cap that spends at least
        min_salary and holds every player in `lock`; `ban` players are in none."""
        L, sal, cap, minsal = self.L, self.sal, self.cap, self.minsal
        banned = {self.idx[k] for k in ban if k in self.idx}
        lk = [self.idx[k] for k in lock if k in self.idx]
        lkset = set(lk)
        adj = [values[i] - L * sal[i] for i in range(len(sal))]
        cand, esl = [], {i: [] for i in lk}
        for si in range(8):
            el = [i for i in self.el[si] if i not in banned]
            fr = [i for i in el if i in lkset]
            ot = sorted((i for i in el if i not in lkset), key=lambda i: -adj[i])
            cand.append((fr, ot))
            for i in fr:
                esl[i].append(si)
        topo = [max((adj[i] for i in self.el[si] if i not in banned and i not in lkset),
                    default=-1e9) for si in range(8)]
        msal = [max((sal[i] for i in self.el[si] if i not in banned), default=0)
                for si in range(8)]
        msuf = [0] * 9                      # the most salary slots si..7 can add
        for si in range(7, -1, -1):
            msuf[si] = msuf[si + 1] + msal[si]
        bk = [[0.0] for _ in range(9)]      # bk[si][k]: the k largest open tops among slots si..7
        for si in range(8):
            acc = 0.0
            for x in sorted(topo[si:], reverse=True):
                acc += x
                bk[si].append(acc)
        best = [-1e9, None]
        roster = [0] * 8
        used = set()
        last = {i: (max(v) if v else -1) for i, v in esl.items()}

        def dfs(si, S, V, miss):
            if si == 8:
                if not miss and S >= min_salary and V > best[0]:
                    best[0] = V
                    best[1] = roster[:]
                return
            left = 7 - si
            if len(miss) > left + 1:
                return
            for i in miss:
                if last[i] < si:
                    return
            fr, ot = cand[si]
            free = left - len(miss)         # later slots an open player can still take
            base = (V + L * (cap - S) + sum(adj[j] for j in miss)
                    + (bk[si + 1][free] if free >= 0 else -1e9))
            for i in fr:
                if i in used:
                    continue
                s2 = S + sal[i]
                if s2 + left * minsal > cap or s2 + msuf[si + 1] < min_salary:
                    continue
                used.add(i)
                roster[si] = i
                miss.discard(i)
                dfs(si + 1, s2, V + values[i], miss)
                miss.add(i)
                used.discard(i)
            if len(miss) > left:            # every later slot is needed for a held player
                return
            for i in ot:
                if base + adj[i] <= best[0] + 1e-9:
                    break
                if i in used:
                    continue
                s2 = S + sal[i]
                if s2 + left * minsal > cap or s2 + msuf[si + 1] < min_salary:
                    continue
                used.add(i)
                roster[si] = i
                dfs(si + 1, s2, V + values[i], miss)
                used.discard(i)

        dfs(0, 0, 0.0, set(lk))
        if best[1] is None:
            return None, None
        by = {ORDER[si]: self.P[best[1][si]][0] for si in range(8)}
        return best[0], [by[s] for s in CLASSIC_SLOTS]


def core_players(active, logs, jk):
    """The core rule on tonight's file and the ESPN logs of games before tonight.
    -> [(player, why)] best points per $1,000 first."""
    G = logs.get("games") or {}
    in_file = {jk(p) for p in active}
    shots, starters = collections.defaultdict(dict), collections.defaultdict(list)
    for k, H in G.items():
        if not H or len(H) < REG_GAMES:
            continue
        t = H[-1]["team"]
        shots[t][k] = sum((x.get("fga") or 0.0) for x in H) / len(H)
        L5 = H[-5:]
        if sum(1 for x in L5 if x["starter"]) / len(L5) >= REG_START_SHARE:
            starters[t].append(k)
    rank = {t: {k: i + 1 for i, (k, _) in enumerate(sorted(v.items(), key=lambda kv: (-kv[1], kv[0])))}
            for t, v in shots.items()}
    top_out = {}
    for t, ks in starters.items():
        outs = [k for k in ks if k not in in_file and rank[t].get(k, 99) <= USAGE_TOP]
        if outs:
            top_out[t] = sorted(outs, key=lambda k: rank[t][k])
    out = []
    for p in active:
        if p.starting is not True or p.salary > CORE_MAX_SALARY or p.salary <= 0:
            continue
        value = p.proj / p.salary * 1000.0
        if value < CORE_MIN_VALUE:
            continue
        H = G.get(jk(p), [])
        L5 = H[-5:]
        new = len(H) >= REG_GAMES and sum(1 for x in L5 if x["starter"]) / len(L5) <= NEW_START_SHARE
        mates = [k for k in top_out.get(p.team, []) if k != jk(p)]
        if not (new or mates):
            continue
        why = []
        if new:
            why.append("a new starter")
        if mates:
            why.append("out: " + ", ".join(G[k][-1]["name"] for k in mates))
        out.append((p, "; ".join(why), value))
    out.sort(key=lambda x: (-x[2], x[0].name))
    return [(p, why) for p, why, _ in out]


def build(active, n, seed, pool_ids, core=(), own_rate=0.0, whole=True, caps=None,
          floors=None, slotting=None, cover=CORE_COVER, rest=CORE_REST, top=CORE_TOP,
          scale=SCALE, max_tries=10, core_form="hold", core_line=50.0, core_mult=1.5,
          core_lift=0.15, core_floor=0.0):
    """n different lineups. active: tonight's players who can be entered (typed
    removals and players ruled out already gone). pool_ids: the typed pool.
    core: dk_ids, best first. caps / floors: {dk_id: lineups}. core_form: what a
    core player gets (see the top of this file).
    -> {"rosters": [[Player x 8 in DK slot order]], "meta": {...}}"""
    caps, floors = dict(caps or {}), dict(floors or {})
    by_id = {p.dk_id: p for p in active}
    core = [c for c in core if c in by_id and caps.get(c, 1) > 0]
    keep = set(pool_ids) | set(core) | set(floors)
    uni = [p for p in active if p.dk_id in keep and p.proj > 0 and p.salary > 0 and p.eligible
           and caps.get(p.dk_id, 1) > 0]
    S = Solver([(p.dk_id, p.salary, set(p.eligible)) for p in uni])
    keys = [p[0] for p in S.P]
    cs = set(core)
    free = set() if core_form == "pool" else cs        # not weighed down for ownership
    up = core_lift if core_form == "lift" else 0.0
    val = {k: by_id[k].proj * (1.0 + (up if k in cs else 0.0))
           - (0.0 if k in free else own_rate / 30.0 * (by_id[k].ownership or 0.0))
           for k in keys}
    sd = {k: (by_id[k].sd or 0.0) for k in keys}
    if len(keys) < 8:
        return {"rosters": [], "meta": {"players": len(keys)}}
    S.tune([val[k] for k in keys])
    rng = random.Random(seed)
    head = set(core[:top])

    def hold_share(c):                  # the share of lineups a core player is held in
        full = cover if c in head else rest
        own = by_id[c].ownership or 0.0
        if core_form == "hold":
            return full
        if core_form == "line":
            return full if core_floor <= own <= core_line else 0.0
        if core_form == "mult":
            return min(full, core_mult * own / 100.0)
        return 0.0                      # "pool", "free", "lift": never held

    share = {c: hold_share(c) for c in core}
    cheap2 = set()                      # the two cheapest at each position stay in every trimmed list
    for pos in BASE:
        cheap2 |= set(sorted((i for i, p_ in enumerate(S.P) if pos in p_[2] and val[p_[0]] > 0),
                             key=lambda i: S.sal[i])[:2])
    held = collections.Counter()
    out, seen = [], set()

    def can_seat(lock):
        el = [[si for si, s in enumerate(CLASSIC_SLOTS) if s in by_id[c].eligible] for c in lock]

        def go(i, used):
            if i == len(el):
                return True
            return any(go(i + 1, used | {si}) for si in el[i] if si not in used)
        return go(0, frozenset())

    def one(v, lock, bn, min_salary):
        if not lock:
            return S.best(v, (), list(bn), min_salary)[1]
        ls = {S.idx[c] for c in lock}
        oth = sorted((i for i in range(len(keys)) if i not in ls and keys[i] not in bn and v[i] > 0),
                     key=lambda i: -(v[i] - S.L * S.sal[i]))[:KEEP_OTHERS]
        ids = list(ls) + oth + [i for i in cheap2 if i not in ls and i not in oth and keys[i] not in bn]
        S2 = Solver([S.P[i] for i in ids])
        S2.L = S.L
        return S2.best([v[i] for i in ids], list(lock), (), min_salary)[1]

    def solve(lock, sc):
        v = [max(0.0, rng.gauss(val[k], sd[k] * sc)) if val[k] > 0 else 0.0 for k in keys]
        lk = [c for c in lock if c in S.idx]
        bn = {c for c, cp in caps.items() if c in S.idx and held[c] + 1 > cp and c not in lk}
        if lk and not can_seat(lk):
            return None
        lu = None
        for floor in ((SALARY_CAP, SALARY_CAP - 100, SALARY_CAP - 200, SALARY_CAP - 300, 0) if whole else (0,)):
            lu = one(v, lk, bn, floor)      # no whole-salary lineup for this shaken night: the nearest to it
            if lu is not None:
                break
        if lu is not None and len({by_id[k].game for k in lu}) < 2:
            return None
        return lu

    def fits(lock):
        return len(lock) <= 6 and sum(by_id[c].salary for c in lock) <= SALARY_CAP - 3000 * (8 - len(lock))

    for _ in range(n):
        lock0 = [c for c in core if rng.random() < share[c]]
        lock0 = [c for c in lock0 if not (c in caps and held[c] + 1 > caps[c])]     # a typed cap comes before the core
        for c, need in floors.items():      # a typed minimum: held until he has his lineups
            if held[c] < need and c in S.idx and c not in lock0:
                lock0 = [c] + lock0
        while lock0 and not fits(lock0):
            lock0 = lock0[:-1]
        lu = None
        for t in range(max_tries):
            if t >= 3 and len(lock0) > len(head & set(lock0)):
                lock0 = lock0[:-1]
            cand = solve(lock0, scale * (1.0 + 0.3 * t))
            if cand is not None and frozenset(cand) not in seen:
                lu = cand
                break
        t = 0
        while lu is None and t < 60:        # always n: the head of the core alone, then nobody, shaken harder
            t += 1
            lock = [c for c in lock0 if c in head] if t <= 30 else []
            cand = solve(lock, scale * (1.0 + 0.2 * t))
            if cand is not None and frozenset(cand) not in seen:
                lu = cand
        if lu is None:
            continue
        seen.add(frozenset(lu))
        held.update(lu)
        ps = [by_id[k] for k in lu]
        slots = slotting(ps) if slotting else ps
        out.append(list(slots) if slots else ps)
    left = [SALARY_CAP - sum(p.salary for p in r) for r in out]
    meta = {"n": len(out), "players": len(keys), "whole": sum(1 for x in left if x <= 0),
            "core": [by_id[c].name for c in core], "head": [by_id[c].name for c in core[:top]],
            "ownRate": own_rate, "coreForm": core_form,
            "coreShare": {by_id[c].name: round(share[c], 3) for c in core},
            "coreIn": {by_id[c].name: held[c] for c in core},
            "minimums": {by_id[c].name: [held[c], need] for c, need in floors.items() if c in by_id},
            "caps": {by_id[c].name: [held[c], cp] for c, cp in caps.items() if c in by_id and cp > 0 and held[c]}}
    return {"rosters": out, "meta": meta}
