"""The main-slate builder (Stage 3 spec, 2026-11-09).

A line-for-line port of the analysis chat's `hbuild.py` with the final
settings from its README_PORT. Nothing here simulates, ranks on win rate or
models duplication. It:

  1. gives each QB a quota (15 QBs for the 150, 7 for a 20-max; share set by
     projection squared; only QBs whose game total is inside the range),
  2. draws random legal lineups around each QB: QB+1 or QB+2, a bring-back
     about 60% of the time, never the QB's own RB, the DST never facing a team
     in the lineup, budget-aware fill weighted by projection squared,
  3. keeps a lineup only if it sits inside the winners' ranges,
  4. picks by projected points under a 55% cap on every player, the QB quotas
     and a 55% QB+2 quota.

There is no core, no Stokastic half, no add-ons and no tilt: Stages 2-3 tested
each and none helped. Keep this file in step with the harness: the analysis
chat grades against `hbuild.py`, so a "small improvement" here is a build that
was never tested.
"""
from __future__ import annotations

import collections
import random

from dk import SALARY_CAP, normalize_name

SPEC = "stage3-2026-11-09"

# The winners' ranges, refit on all 7 graded main slates (27,946 top-1%
# lineups). The analysis chat refits these as new slates arrive (README_PORT
# section 8); change them here and nowhere else.
RANGES = {
    "pown": (86.4, 145.0),   # projected-ownership sum, 10th-90th percentile
    "lt5p": (0, 3),          # players under 5% projected
    "lt2p": (0, 1),          # players under 2% projected
    "left": (0, 400),        # salary left
    "vt": (41.5, 53.5),      # the QB's game total (LineStar VegasTotals)
}
FLEX_W = {"RB": 0.34, "WR": 0.25, "TE": 0.41}
STACK_PROBS = {1: 0.35, 2: 0.65}   # draw mix; the quota at selection sets the set
P_BB = 0.60                        # bring-back draw probability
SHARE2 = 0.55                      # QB+2 share of the set
CAP = 0.55                         # every player, QB and DST included
ALPHA = 2                          # QB quota weight = proj ** ALPHA
NQB_BIG = 15
NQB_SMALL = 7
SMALL_SET = 20                     # a contest of 20 entries or fewer gets its own set
MIN_PROJ = 2.0


def per_qb(q):
    return max(1400, 160 * q)


# ---------------- the player table ----------------
def player_table(players, linestar=None):
    """Player objects -> ({name: rec}, {name: Player}, notes).

    Keyed by normalised name, as in the harness, because the harness iterates
    in sorted-name order and the same seed must give the same lineups.
    `linestar` is read_linestar's per-name dict; the game total comes from the
    player's own row, else the total most LineStar players on his team carry.
    """
    linestar = linestar or {}
    team_tot = collections.defaultdict(collections.Counter)
    for p in players:
        rec = linestar.get(normalize_name(p.name))
        if rec and rec.get("total"):
            team_tot[p.team][rec["total"]] += 1
    tmap = {t: c.most_common(1)[0][0] for t, c in team_tot.items()}
    P, obj = {}, {}
    for p in players:
        k = normalize_name(p.name)
        if k in P and P[k]["proj"] >= p.proj:
            continue
        rec = linestar.get(k)
        tot = rec.get("total") if rec and rec.get("total") else tmap.get(p.team)
        P[k] = {"pos": p.pos, "team": p.team, "opp": p.opponent, "sal": p.salary,
                "proj": p.proj, "pown": p.ownership, "total": tot}
        obj[k] = p
    return P, obj, tmap


def eligible(P, pool=None, removed=()):
    """Typed names with proj >= 2, plus every DST. No pool: the whole board."""
    removed = set(removed)
    out = set()
    for n, a in P.items():
        if n in removed:
            continue
        if a["pos"] == "DST" or ((not pool or n in pool) and a["proj"] >= MIN_PROJ):
            out.add(n)
    return out


# ---------------- the harness, ported ----------------
def lineup_feats(names, P):
    ps = [P[n] for n in names]
    qb = next((p for p in ps if p["pos"] == "QB"), None)
    dst = next((p for p in ps if p["pos"] == "DST"), None)
    T = qb["team"] if qb else None
    O = qb["opp"] if qb else None
    npc = sum(1 for p in ps if p["team"] == T and p["pos"] in ("WR", "TE"))
    qbrb = int(any(p["team"] == T and p["pos"] == "RB" for p in ps))
    nbb = sum(1 for p in ps if p["team"] == O and p["pos"] in ("RB", "WR", "TE"))
    off_teams = {p["team"] for p in ps if p["pos"] != "DST"}
    dvo = int(bool(dst) and dst["opp"] in off_teams)
    pown = [p["pown"] for p in ps]
    sal = sum(p["sal"] for p in ps)
    return dict(pown=sum(pown), lt5p=sum(1 for x in pown if x < 5),
                lt2p=sum(1 for x in pown if x < 2), sal=sal, left=SALARY_CAP - sal,
                npc=npc, nbb=nbb, qbrb=qbrb, dvo=dvo,
                vt=qb["total"] if qb else None, proj=sum(p["proj"] for p in ps),
                qb=next((n for n in names if P[n]["pos"] == "QB"), None))


def in_ranges(ft, R):
    if not (R["pown"][0] <= ft["pown"] <= R["pown"][1]):
        return False
    if ft["lt5p"] > R["lt5p"][1] or ft["lt2p"] > R["lt2p"][1]:
        return False
    if ft["left"] < 0 or ft["left"] > R["left"][1]:
        return False
    if ft["vt"] is not None and not (R["vt"][0] <= ft["vt"] <= R["vt"][1]):
        return False
    if ft["npc"] not in (1, 2) or ft["qbrb"] or ft["dvo"] or ft["nbb"] > 2:
        return False
    return True


def qb_quotas(P, elig, n, nqb, alpha=ALPHA, vt_range=None):
    ok = lambda q: (vt_range is None or P[q]["total"] is None
                    or vt_range[0] <= P[q]["total"] <= vt_range[1])
    qbs = sorted([q for q in elig if P[q]["pos"] == "QB" and ok(q)],
                 key=lambda q: -P[q]["proj"])[:nqb]
    if not qbs:
        return {}
    w = [P[q]["proj"] ** alpha for q in qbs]
    s = sum(w)
    quota = {q: max(1, round(n * x / s)) for q, x in zip(qbs, w)}
    while sum(quota.values()) > n:
        k = max(quota, key=quota.get)
        quota[k] -= 1
    while sum(quota.values()) < n:
        k = max(quota, key=quota.get)
        quota[k] += 1
    return {q: v for q, v in quota.items() if v > 0}


def generate(P, elig, quota, rng, R, p_bb=P_BB, stack_probs=None, min_proj=MIN_PROJ):
    """-> {sorted name tuple: (score, qb, npc)}, attempts made."""
    stack_probs = stack_probs or STACK_PROBS
    W = lambda n: P[n]["proj"] ** 2
    by_pos = collections.defaultdict(list)
    for n in sorted(elig):
        if n in P and P[n]["proj"] >= min_proj:
            by_pos[P[n]["pos"]].append(n)
    minsal = {pos: min(P[n]["sal"] for n in v) for pos, v in by_pos.items()}
    sizes = sorted(stack_probs)
    sw = [stack_probs[s] for s in sizes]
    last_lo = SALARY_CAP - R["left"][1]

    def wsample(pool, k):
        out, pool = [], list(pool)
        while pool and len(out) < k:
            pick = rng.choices(pool, [W(n) for n in pool])[0]
            out.append(pick)
            pool.remove(pick)
        return out

    cands, tries = {}, 0
    for qb, q in sorted(quota.items()):
        T, O = P[qb]["team"], P[qb]["opp"]
        pcs_pool = [n for n in by_pos["WR"] + by_pos["TE"] if P[n]["team"] == T]
        bb_pool = [n for n in by_pos["RB"] + by_pos["WR"] + by_pos["TE"]
                   if P[n]["team"] == O]
        for _ in range(per_qb(q)):
            tries += 1
            s = rng.choices(sizes, sw)[0]
            s = min(s, len(pcs_pool))
            picked = wsample(pcs_pool, s) + (wsample(bb_pool, 1)
                                             if (bb_pool and rng.random() < p_bb) else [])
            need = {"RB": 2, "WR": 3, "TE": 1}
            flex = rng.choices(list(FLEX_W), list(FLEX_W.values()))[0]
            need[flex] += 1
            for n in picked:
                need[P[n]["pos"]] -= 1
            if min(need.values()) < 0:
                continue
            lineup = [qb] + picked
            used = sum(P[n]["sal"] for n in lineup)
            chosen = set(lineup)
            slots = [pos for pos, k in need.items() for _ in range(k)]
            rng.shuffle(slots)
            slots.append("DST")
            ok = True
            for i, pos in enumerate(slots):
                rem = slots[i + 1:]
                floor_rem = sum(minsal.get(p, 2000) for p in rem)
                hi = SALARY_CAP - used - floor_rem
                lo = (last_lo - used) if not rem else 0
                off_teams = {P[n]["team"] for n in lineup}
                pool = [n for n in by_pos[pos] if n not in chosen
                        and lo <= P[n]["sal"] <= hi
                        and not (pos == "RB" and P[n]["team"] == T)
                        and not (pos == "DST" and P[n]["opp"] in off_teams)]
                if not pool:
                    ok = False
                    break
                pick = rng.choices(pool, [W(n) for n in pool])[0]
                lineup.append(pick)
                chosen.add(pick)
                used += P[pick]["sal"]
            if not ok:
                continue
            key = tuple(sorted(lineup))
            ft = lineup_feats(key, P)
            if not in_ranges(ft, R):
                continue
            if ft["left"] < 0 or ft["left"] > 2000:
                continue
            cands[key] = (ft["proj"], qb, ft["npc"])
    return cands, tries


def stack_quotas(n, share2=SHARE2):
    n2 = round(n * share2)
    return {2: n2, 1: n - n2}


def select(cands, n, cap, quota, stack_quota=None, caps=None, floors=None):
    """Greedy by score under the player cap, QB quotas and the stack quota.

    `caps` {name: most lineups} are the user's typed caps: they hold in every
    pass. `floors` {name: fewest lineups} are the optional minimums, taken
    first, inside the normal caps and quotas (the harness's core loop).
    """
    caps = caps or {}
    items = sorted(cands.items(), key=lambda kv: (-kv[1][0], kv[0]))
    chosen, taken = [], set()
    cnt, qbc, stc = collections.Counter(), collections.Counter(), collections.Counter()

    def ok(names, qb, npc, capx, use_quota, use_stack):
        if names in taken:
            return False
        if use_quota and quota is not None and qbc[qb] + 1 > quota.get(qb, 0):
            return False
        if use_stack and stack_quota is not None and stc[npc] + 1 > stack_quota.get(npc, 0):
            return False
        return all(cnt[p] + 1 <= capx * n and cnt[p] + 1 <= caps.get(p, n)
                   for p in names)

    def take(names, qb, npc):
        chosen.append(names)
        taken.add(names)
        qbc[qb] += 1
        stc[npc] += 1
        for p in names:
            cnt[p] += 1

    for k, want in (floors or {}).items():
        for names, (sc, qb, npc) in items:
            if len(chosen) >= n or cnt[k] >= want:
                break
            if k in names and ok(names, qb, npc, cap, True, True):
                take(names, qb, npc)
    for capx, use_quota, use_stack in ((cap, True, True), (cap + 0.10, True, True),
                                       (cap + 0.10, False, True), (cap, True, False),
                                       (cap, False, False), (cap + 0.15, False, False),
                                       (1.0, False, False)):
        for names, (sc, qb, npc) in items:
            if len(chosen) >= n:
                break
            if ok(names, qb, npc, capx, use_quota, use_stack):
                take(names, qb, npc)
        if len(chosen) >= n:
            break
    return chosen


# ---------------- one set ----------------
def build_set(P, elig, n, seed, nqb, caps=None, floors=None, ranges=None):
    """-> {"lineups": [name tuples], "quota", "candidates", "tries", "notes"}"""
    R = ranges or RANGES
    rng = random.Random(seed)
    quota = qb_quotas(P, elig, n, nqb, ALPHA, vt_range=R["vt"])
    notes = []
    if not quota:
        return {"lineups": [], "quota": {}, "candidates": 0, "tries": 0,
                "notes": ["no QB is eligible: check the pool has QBs and their "
                          "game totals sit inside the range"]}
    cands, tries = generate(P, elig, quota, rng, R)
    chosen = select(cands, n, CAP, quota, stack_quotas(n), caps=caps, floors=floors)
    return {"lineups": chosen, "quota": quota, "candidates": len(cands),
            "tries": tries, "notes": notes}


def would_be_value_adds(P, pool, k=5):
    """Shadow log only: the five the old tool would have added (not used)."""
    c = [n for n in P if P[n]["sal"] <= 4000 and n not in (pool or ())
         and P[n]["pos"] in ("RB", "WR", "TE") and P[n]["proj"] >= 3]
    return sorted(c, key=lambda n: -(P[n]["proj"] / P[n]["sal"]))[:k]
