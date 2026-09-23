"""DraftKings NBA Classic — the roster, slot assignment, and the builder.

The one genuinely new problem against NFL: positions overlap. A PG/SG can fill
PG, SG, G or UTIL, so a set of eight players is legal only if SOME assignment of
them to the eight slots works, and which assignment goes in the upload file is a
choice. The old Base44 tool never faced this — it only ever re-used Stokastic's
own slotting — so there is no prior art in this project for it.

Construction therefore fills SLOTS rather than players: base positions first
(the scarce ones), then G and F, then UTIL, each from the players eligible for
that slot. Every roster it produces is legal by construction and carries its
own assignment. The upload writer then re-slots within the same eight so the
latest-tipping players sit in the flexible slots (see `best_slotting`).

There are no shape rules here, and that is a decision. WNBA's slate-size shape
rules are WNBA measurements; the only transferable part is that concentration
inverts with slate size, and the response to that is to LOG every lineup's game
and team blocks against the slate's game count so NBA's version can be
measured, not to guess one. The user's standing instruction is to follow the
data, and the data from three sports is that most construction rules are
already in the projections.
"""

from __future__ import annotations

import random

from dk import CLASSIC_SIZE, CLASSIC_SLOTS, SALARY_CAP, SLOT_TAKES
from engine import CORE_BOOST, FILL_EXP, _pick

# Leftover salary: a junk filter, not a lever. WNBA measured a floor at $800 on
# its own builds across 22 slates and 8 seeds and it cost 4.9 cashes for no
# tail; $2,000 was measured identical to no floor at all. NFL's main slate uses
# 2,000 too. If this ever shows up as binding on anything, it is wrong.
MAX_LEFTOVER = 2000
MIN_PROJ = 2.0           # a roster spot needs some path to a useful score
STUD_SALARY = 10_000     # reporting only — logged, never enforced

BASE_SLOTS = ("PG", "SG", "SF", "PF", "C")
FLEX_SLOTS = ("G", "F")


class Lineup:
    """One classic roster: eight players, with a legal slot assignment."""

    __slots__ = ("players", "slotting", "metrics", "source")

    def __init__(self, players, slotting=None, source="mine"):
        self.players = list(players)
        # slotting[i] is the player in CLASSIC_SLOTS[i]
        self.slotting = list(slotting) if slotting else None
        self.metrics = {}
        self.source = source

    def weighted(self):
        return [(p, 1.0) for p in self.players]

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

    overlap_ids = ids

    def key(self):
        return frozenset(p.dk_id for p in self.players)

    def blocks(self):
        """(biggest game block, its game, biggest team block, its team, games used)."""
        g, t = {}, {}
        for p in self.players:
            g[p.game] = g.get(p.game, 0) + 1
            t[p.team] = t.get(p.team, 0) + 1
        bg = max(g, key=lambda k: g[k])
        bt = max(t, key=lambda k: t[k])
        return g[bg], bg, t[bt], bt, len(g)

    def shape_label(self):
        gb, _g, tb, _t, _n = self.blocks()
        return f"game {gb} / team {tb}"

    def slots(self):
        """The eight players in DK slot order."""
        if self.slotting is None or not _valid(self.slotting):
            self.slotting = best_slotting(self.players)
        return self.slotting


# --- slot assignment -------------------------------------------------------
def _valid(slotting):
    return (slotting is not None and len(slotting) == CLASSIC_SIZE
            and all(p is not None and s in p.eligible
                    for s, p in zip(CLASSIC_SLOTS, slotting))
            and len({p.dk_id for p in slotting}) == CLASSIC_SIZE)


def assignments(players, limit=500):
    """Every legal assignment of these eight players to the eight slots, up to
    `limit`. Most-constrained slots first, so dead ends die early."""
    order = sorted(range(CLASSIC_SIZE),
                   key=lambda i: sum(1 for p in players if CLASSIC_SLOTS[i] in p.eligible))
    out, cur, used = [], [None] * CLASSIC_SIZE, set()

    def go(k):
        if len(out) >= limit:
            return
        if k == CLASSIC_SIZE:
            out.append(list(cur))
            return
        i = order[k]
        for j, p in enumerate(players):
            if j in used or CLASSIC_SLOTS[i] not in p.eligible:
                continue
            used.add(j)
            cur[i] = p
            go(k + 1)
            used.discard(j)
            cur[i] = None

    if len(players) == CLASSIC_SIZE:
        go(0)
    return out


# Which assignment goes in the upload: the one with the LATEST-tipping players in
# the flexible slots. UTIL takes anyone, so a late player there keeps the most
# options open; G and F take two positions each. Same eight players, only the
# slots move — DK allows it and it costs nothing (WNBA: _util_holds_latest). A
# slotting nicety for the upload, not late-swap logic.


def best_slotting(players, start_of=None):
    """A legal assignment, preferring the latest-tipping players in the flexible
    slots. -> list in CLASSIC_SLOTS order, or None if no assignment exists."""
    opts = assignments(players, limit=5000)
    if not opts:
        return None
    if not start_of:
        return opts[0]
    # Lexicographic, not a weighted sum: the latest player in UTIL first, then
    # the latest possible pair in G and F. A sum can trade the one late player
    # out of UTIL for two middling ones in G and F, which is the wrong trade.
    def worth(a):
        at = dict(zip(CLASSIC_SLOTS, a))
        gf = sorted((start_of(at["G"]), start_of(at["F"])), reverse=True)
        return (start_of(at["UTIL"]), gf[0], gf[1])
    return max(opts, key=worth)


def legal(players):
    """-> "" if DK would accept these eight, else the reason."""
    if len(players) != CLASSIC_SIZE:
        return f"{len(players)} players, a classic roster has {CLASSIC_SIZE}"
    if len({p.dk_id for p in players}) != CLASSIC_SIZE:
        return "the same player twice"
    sal = sum(p.salary for p in players)
    if sal > SALARY_CAP:
        return f"${sal:,} of salary, over the ${SALARY_CAP:,} cap"
    if len({p.game for p in players}) < 2:
        return "only one game — DK requires two"
    if not assignments(players, limit=1):
        return "no way to fill all eight slots from these positions"
    return ""


# --- construction ----------------------------------------------------------
def build_candidates(players, n, *, rng=None, max_off_pool=None,
                     min_proj=MIN_PROJ, max_leftover=MAX_LEFTOVER):
    """Randomised slot-first construction, weighted by projection cubed."""
    rng = rng or random.Random(0)
    pool = [p for p in players if p.proj >= min_proj and p.salary > 0 and p.eligible]
    if len({p.game for p in pool}) < 2:
        pool = [p for p in players if p.proj > 0 and p.salary > 0 and p.eligible]
    by_slot = {s: [p for p in pool if s in p.eligible] for s in CLASSIC_SLOTS}
    if any(not v for v in by_slot.values()):
        return []
    weight = {p.dk_id: max(p.proj, 0.1) ** FILL_EXP * (CORE_BOOST if p.core else 1.0)
              for p in pool}
    min_sal = min(p.salary for p in pool)

    def allowed(p, off):
        return max_off_pool is None or p.in_pool or p.core or off < max_off_pool

    def counts_off(p):
        return 0 if (p.in_pool or p.core) else 1

    out, seen, tries = [], set(), 0
    give_up = min(3000, max(500, n // 2))
    while len(out) < n and tries < n * 30:
        tries += 1
        if tries > give_up and len(out) * 100 < tries:
            break              # under 1% accepted: this budget will never fill
        base = list(BASE_SLOTS)
        rng.shuffle(base)
        flex = list(FLEX_SLOTS)
        rng.shuffle(flex)
        order = base + flex + ["UTIL"]
        slotted = {}
        used, salary, off, ok = set(), 0, 0, True
        for k, slot in enumerate(order):
            left = CLASSIC_SIZE - k - 1
            budget = SALARY_CAP - salary - min_sal * left
            elig = [p for p in by_slot[slot]
                    if p.dk_id not in used and p.salary <= budget and allowed(p, off)]
            if not elig:
                ok = False
                break
            p = _pick(elig, [weight[q.dk_id] for q in elig], rng)
            slotted[slot] = p
            used.add(p.dk_id)
            salary += p.salary
            off += counts_off(p)
        if not ok:
            continue
        # Spend-up repair: upgrade the weakest slot within the spare salary,
        # from players eligible for THAT slot, rather than throw the roster away.
        if max_leftover is not None:
            for _ in range(4):
                spare = SALARY_CAP - salary
                if spare <= max_leftover:
                    break
                slot = min(slotted, key=lambda s: slotted[s].proj)
                worst = slotted[slot]
                w_off = counts_off(worst)
                better = [q for q in by_slot[slot] if q.dk_id not in used
                          and q.salary <= worst.salary + spare and q.proj > worst.proj
                          and allowed(q, off - w_off)]
                if not better:
                    break
                up = max(better, key=lambda q: q.proj)
                used.discard(worst.dk_id)
                used.add(up.dk_id)
                salary += up.salary - worst.salary
                off += counts_off(up) - w_off
                slotted[slot] = up
        picked = [slotted[s] for s in CLASSIC_SLOTS]
        if salary > SALARY_CAP or len({p.game for p in picked}) < 2:
            continue
        if max_leftover is not None and SALARY_CAP - salary > max_leftover:
            continue
        if max_off_pool is not None and off > max_off_pool:
            continue
        lu = Lineup(picked, slotting=picked)
        if lu.key() in seen:
            continue
        seen.add(lu.key())
        out.append(lu)
    return out
