"""The NBA classic build — the Nov 7 spec.

From four reviews of every saved night (Oct 20 - Nov 6, 51 contests), in
`~/Downloads/NBA Results 1/`: Contest First Review, Where The Points Come From,
Which Chalk To Go Heavy On, Full System Review And Prototype, and Before The
Build (the final spec). The prototype it is ported from is
`~/Downloads/NBA Results 1/Prototype/` (its README is the hand-over).

Two sources of lineups and almost no rules:

  150-max   2-4 games : all 150 from the typed pool (our own candidates)
            5-7 games : all 150 from Stokastic's contest-lineup file(s)
            8+ games  : 75 from the file, 75 from the pool drawn the old way
  20-max    2-4 games : 20 from the pool candidates, then the top three by
                        projected ownership (15%+) raised to 2x their projected
                        ownership (at most 95%); no file lineups
            5+ games  : 20 of the 150, every 7th lineup, a different start for
                        each contest (G17, Step Back report, Nov 19; app.py)

  Own candidates      : slot-first from the pool, weight projection^2.5, within
                        $300 of the cap, kept only inside the "blueprint" ranges
                        (what the top-1% lineups looked like on earlier nights of
                        the same slate size). On 8+ games the old draw instead:
                        projection^3, up to $2,000 left, 4,000 candidates, no ranges.
  File lineups        : every legal lineup of active players in every lineups file
                        dropped, picked by projected sum on 5-7 games and by
                        Stokastic's own Top 10% figure on 8+ (Nov 13).
  One live piece      : on 5-7 games the 150 takes only file lineups whose
                        fewest-owned player is projected at 2-10% (G14), after
                        the lineups that floor each stud ($9,000+, projected 30+)
                        at 5-20% own to 1.5x his own (G12). Win Not Cash and
                        Juice The Live Piece reports, Nov 15.
  Weak chalk          : on 2-4 games, chalk with a shaky role and no role change
                        is left out of the pick (role.py, app.run_build; Nov 13).
                        On 5+ games it is a shadow build, logged only.
  Selection           : by projected points under one cap on any player (85% of a
                        150, 90% of a 20); a 150 drops anyone in fewer than 4
                        lineups for the set's most-used player at his salary.
  Not here            : the simulation, the bar, the win-rate score, duplicates,
                        neutral rules, tier caps, core floors, automatic pool adds,
                        stud floors (other than G12), fades, any lean in the 150. Each was tested and
                        lost or did nothing (Full System report, section 1).

Typed by hand: the pool, removals (nothing is shifted to teammates), per-player
caps (a cap at 0 is a removal) and minimums.
"""

from __future__ import annotations

import collections
import csv
import glob
import hashlib
import io
import json
import os
import pickle
import random
import re
import statistics
from datetime import datetime

from dk import CLASSIC_SLOTS, SALARY_CAP, normalize_name

# --- the spec's settings (Before The Build report, section 4) ---------------
SMALL_SET = 20            # a contest this size or smaller gets the 20-max build
CAP_BIG = 0.85            # no player in more than 85% of a 150
CAP_SMALL = 0.90          # ... or 90% of a 20
MIN_LINEUPS = 4           # a 150 drops anyone in fewer than 4 lineups
# The tail fix is OFF (Four Lineup Minimum quick ask, Nov 24). It was a count:
# a player could not be in 1, 2 or 3 lineups of the 150. On the 33 saved nights
# it added no top-10 finish (3 with it, 3 without): it made a 6th on Nov 2
# because a pool player in 2 lineups failed (any swap would have done it, and
# the lineup it made breaks the limit of 5 shared players), made a 2nd out of a
# 5th on Nov 4, and turned an 8th into a 13th on Oct 31 by swapping a 4.8%
# owned pool player who hit for 43% chalk. On 8+ game slates it changed 22 to
# 38 lineups a night, left 72 of 1,050 over the limit of 5 shared players, and
# dropped 61 of the 127 players the wider pool had put in the 150. True turns
# it back on exactly as it was.
TAIL_ON = False
# T1 (20s, Spread And Pool report, Nov 20): the top-three raise is OFF (K = 0).
# It was a cash part; the 20s finish level or higher without it. The grader
# passes 2.0 to rebuild a night built with it.
TOP3_K, TOP3_CAP, TOP3_MIN_OWN = 0.0, 0.95, 15.0    # the 20-max's top-3 lean
# The 150's takeover lean (the prototype's final spec, takeover_k / takeover_cap;
# left out of the Nov 7 port by mistake, added Nov 12 after the 11-12 review, T13):
# a takeover player at 15%+ projected own is floored at 1.5x his own, cap 95%.
# T1 (Nov 20): the lean is OFF (K = 0). On the nights after it was written the
# 150 finished higher without it. The grader passes 1.5 to rebuild a night
# built with it.
TAKEOVER_K, TAKEOVER_CAP, TAKEOVER_MIN_OWN = 0.0, 0.95, 15.0
# T1 (20s, Spread And Pool report, Nov 20): the 150 is spread, not near copies.
# SHARE_LIMIT: no two lineups of the 150 share more than this many players
# (every slate). On 5-7 game slates Stokastic's file is first cut to the top-10
# SHAPE and no player is in more than CAP_SPREAD of the 150. The shape reads
# projected ownership only: "a field lineup" is the ownership-weighted sum
# (sum of own^2 / 100) and the expected count of 15%+ players (sum of own / 100
# over the 15%+ players). It is used only when SHAPE_MIN or more of the file's
# lineups pass.
SHARE_LIMIT = 5
SHAPE = dict(left=300, chalk=(-1, 3), under10=2, minOwn=5.0, own=(0.8, 1.5))
SHAPE_MIN = 225
SHAPE_GAMES = (5, 7)
CAP_SPREAD = 0.60
NEW_DRAW = dict(weight=2.5, left=300, n=2000, tries=40000, ranges=True)
OLD_DRAW = dict(weight=3.0, left=2000, n=4000, tries=80000, ranges=False)
STUD = 9000
# One live piece (G14) and the low-owned stud floor (G12), the 150 on 5-7 game
# slates (Win Not Cash and Juice The Live Piece reports, Nov 15). A lineup has a
# live piece when its fewest-owned player is projected at LIVE_LO% to under
# LIVE_HI%; if fewer than LIVE_LEFT_K x the lineups needed have one, the whole
# file is used. A stud ($9,000+, projected STUD_FLOOR_PROJ+) at 5-20% own whom
# the plain pick holds under 1.5x his own gets his lineups picked first, from
# the whole file in its order. RARE_PAIR: the shadow (the two fewest-owned
# players' ownerships multiplied under 0.75% of the field).
# Nov 17 (Contest Review 11-17): G12 is OFF in the live build and back to a
# shadow (the 150 with the floor, saved each night). It lost 1.9 and 1.5 split
# top-1% lineups on its two live nights; G14 alone is ahead over 13 nights.
LIVE_LO, LIVE_HI, LIVE_LEFT_K = 2.0, 10.0, 3
LIVE_GAMES = (5, 7)
STUD_FLOOR_LO, STUD_FLOOR_HI, STUD_FLOOR_K, STUD_FLOOR_PROJ = 5.0, 20.0, 1.5, 30.0
RARE_PAIR = 0.75
G19_SPREAD = 0.05         # G19's shadow: no live piece in more than 5% of the 150
SWAP_BAND = 500           # a re-target or tail swap stays within $500
# G17 (Step Back report, Nov 19): on 5+ game slates each 20-max contest's 20 are
# taken evenly from the 150 (every 7th lineup, a different start for each
# contest). +$1,459 over 28 nights against the pool-built 20s.
FROM150_GAMES, FROM150_STEP = 5, 7
# Sim Chalk report (Nov 19), shadow: the narrowed-spread 20s, picked by each
# lineup's upper end with the Std Dev halved for players projected 24 minutes
# or under.
UPPER_Z, UPPER_MINS, UPPER_HALF = 1.28, 24.0, 0.5

HISTORY_DIR = os.environ.get("NBA_HISTORY") or os.path.expanduser(
    "~/Downloads/NBA Results 1/Main")
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def slate_group(games):
    return "2-4 games" if games <= 4 else ("5-7 games" if games <= 7 else "8+ games")


def plan(n, games, have_file):
    """-> (own lineups, file lineups, which draw, the rule in words)."""
    if n <= SMALL_SET:
        return n, 0, (OLD_DRAW if games >= 8 else NEW_DRAW), \
            f"{n} from your pool"
    if games <= 4:
        return n, 0, NEW_DRAW, f"{games} games: all {n} from your pool"
    if not have_file:
        return n, 0, (OLD_DRAW if games >= 8 else NEW_DRAW), \
            f"{games} games: no Stokastic lineups file, so all {n} from your pool"
    if games <= 7:
        return 0, n, NEW_DRAW, f"{games} games: all {n} from Stokastic's lineups"
    # 8+ games (Mini-MAX Tail And Big Slates report, Nov 23): all from Stokastic's
    # file too, now that it is cut to the pool. The half drawn from the pool was
    # the weaker half: on six 8+ game nights the 150 finished higher without it
    # on five and level on one. The pool draw (old draw) still fills whatever
    # the file cannot give, as on 5-7 games.
    return 0, n, OLD_DRAW, f"{games} games: all {n} from Stokastic's lineups"


# --- injury tags (the tool's reader does not keep them) ---------------------
def injury_tags(proj_text):
    """{normalised name: injury tag} from the Stokastic file's Injury column."""
    out = {}
    try:
        for r in csv.DictReader(io.StringIO((proj_text or "").lstrip("﻿"))):
            nm = normalize_name(r.get("Player") or r.get("Name") or "")
            if nm:
                out[nm] = (r.get("Injury") or "").strip()
    except csv.Error:
        pass
    return out


def is_active(p, inj):
    tag = inj.get(normalize_name(p.name), "")
    return (p.proj > 0 and p.salary > 0 and bool(p.eligible) and not p.removed
            and tag in ("", "None") and (p.minutes > 0 or not p.minutes))


# --- earlier nights: normal roles and the blueprint ranges ------------------
_DATE = re.compile(r"(\d{1,2})-(\d{1,2})(?: \d)?\.csv$")


def _season_key(month, day):
    return (month if month >= 7 else month + 12, day)


def _nights(folder=None):
    """[(key, 'M-D', projections path, mini-MAX standings path or None)], oldest first,
    only nights before today."""
    folder = folder or HISTORY_DIR
    now = datetime.now()
    today = _season_key(now.month, now.day)
    found = {}
    # The plain projections export first; a night saved only as the Data Hub
    # export (Nov 19 on) is read from that (Chalk Sheet handover, Nov 20).
    for pat, hub in (("NBA Stokastic Projections Main *.csv", False),
                     ("NBA Stokastic Data Hub Projections Main *.csv", True)):
        for path in glob.glob(os.path.join(folder, pat)):
            m = _DATE.search(os.path.basename(path))
            if not m:
                continue
            key = _season_key(int(m.group(1)), int(m.group(2)))
            if key >= today:
                continue
            date = f"{int(m.group(1))}-{int(m.group(2))}"
            if date in found and (hub or not os.path.basename(path).endswith(f"{date}.csv")):
                continue
            st = os.path.join(folder, f"NBA Contest Standing Main mini-MAX {date}.csv")
            found[date] = (key, date, path, st if os.path.exists(st) else None)
    return sorted(found.values())


def _f(v, d=0.0):
    try:
        return float(str(v).replace("%", "").replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return d


def _read_night_proj(path):
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            k = normalize_name(r.get("Player") or r.get("Name") or "")
            if not k:
                continue
            pc = "Projected FP" if "Projected FP" in r else "Projection"
            out[k] = {"sal": _f(r.get("Salary")), "team": (r.get("Team") or "").strip(),
                      "opp": (r.get("Opponent") or "").strip(), "mins": _f(r.get("Mins")),
                      "proj": _f(r.get(pc)), "own": _f(r.get("Ownership %")),
                      "boom": _f(r.get("Boom")),
                      "injury": (r.get("Injury") or "").strip(),
                      "starting": (r.get("Starting") or "").strip().lower() == "true"}
    return out


_LU = re.compile(r"(?:^|\s)(?:PG|SG|SF|PF|UTIL|C|G|F)\s")


def _read_top1(path):
    """The top-1% rosters of a standings file, as lists of normalised names."""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.reader(fh):
            if len(r) > 5 and r[1].strip() and r[0].strip().isdigit() and r[5].strip():
                rows.append((int(r[0]), r[5]))
    n = len(rows)
    out = []
    for rank, lineup in rows:
        if rank <= n * 0.01:
            names = [normalize_name(x) for x in
                     (p.strip() for p in _LU.split(" " + lineup.strip() + " ")) if x]
            if len(names) == 8:
                out.append(names)
    return out


def _cache_path():
    return os.path.join(LOG_DIR, "history_cache.pkl")


def load_history(folder=None):
    """Every earlier night: its projections, game count and top-1% rosters (cached)."""
    nights = _nights(folder)
    try:
        with open(_cache_path(), "rb") as fh:
            cache = pickle.load(fh)
    except (OSError, pickle.PickleError, EOFError, AttributeError):
        cache = {}
    out, dirty = [], False
    for key, date, ppath, spath in nights:
        stamp = (ppath, os.path.getmtime(ppath), spath,
                 os.path.getmtime(spath) if spath else None)
        rec = cache.get(date)
        if not rec or rec.get("stamp") != stamp:
            try:
                P = _read_night_proj(ppath)
                top = _read_top1(spath) if spath else []
            except (OSError, csv.Error):
                continue
            games = len({frozenset((v["team"], v["opp"])) for v in P.values()
                         if v["team"] and v["opp"]})
            rec = {"stamp": stamp, "date": date, "P": P, "games": games, "top1": top}
            cache[date] = rec
            dirty = True
        out.append(rec)
    if dirty:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(_cache_path(), "wb") as fh:
                pickle.dump(cache, fh)
        except OSError:
            pass
    return out


def _top_sets(sal_proj_boom):
    """{'val10', 'boom10'}: tonight's top ten by points per $1,000 and by Boom %."""
    S = [(k, s, p, b) for k, s, p, b in sal_proj_boom if p > 0 and s > 0]
    val = {k for k, *_ in sorted(S, key=lambda t: -t[2] / t[1])[:10]}
    boom = {k for k, *_ in sorted(S, key=lambda t: -t[3])[:10]}
    return {"val10": val, "boom10": boom}


def _feats(sals, owns, keys, V):
    return {"studs": sum(1 for s in sals if s >= STUD),
            "cheap": sum(1 for s in sals if 0 < s < 5000),
            "punts": sum(1 for s in sals if 0 < s <= 3500),
            "left": SALARY_CAP - sum(sals), "pown": sum(owns),
            "plt2": sum(1 for o in owns if o < 2),
            "val10": sum(1 for k in keys if k in V["val10"]),
            "boom10": sum(1 for k in keys if k in V["boom10"])}


def _pctl(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def blueprint(history, games):
    """The winners' ranges on earlier nights of the same slate size, or None."""
    group = slate_group(games)
    rows = []
    for rec in history:
        if slate_group(rec["games"]) != group or not rec["top1"]:
            continue
        P = rec["P"]
        V = _top_sets([(k, v["sal"], v["proj"], v["boom"]) for k, v in P.items()])
        for names in rec["top1"]:
            rows.append(_feats([P[x]["sal"] if x in P else 0 for x in names],
                               [P[x]["own"] if x in P else 0.0 for x in names], names, V))
    if not rows:
        return None
    B = {"nights": sum(1 for r in history if slate_group(r["games"]) == group and r["top1"])}
    for k in ("studs", "cheap", "pown"):
        B[k] = (_pctl([r[k] for r in rows], 0.10), _pctl([r[k] for r in rows], 0.90))
    for k in ("punts", "left", "plt2"):
        B[k] = (None, _pctl([r[k] for r in rows], 0.90))
    for k in ("val10", "boom10"):
        B[k] = (_pctl([r[k] for r in rows], 0.10), None)
    return B


def _fits(fe, B):
    if not B:
        return True
    for k, rng in B.items():
        if k == "nights":
            continue
        lo, hi = rng
        if lo is not None and fe[k] < lo:
            return False
        if hi is not None and fe[k] > hi:
            return False
    return True


def normals(history, players):
    """{normalised name: {n, recent, proj, mins, start_rate}} from the last five
    appearances on earlier nights."""
    hist = collections.defaultdict(list)
    for i, rec in enumerate(history):
        for k, v in rec["P"].items():
            if v["proj"] > 0 and v["mins"] > 0:
                hist[k].append((i, v["proj"], v["mins"], v["starting"]))
    n = len(history)
    out = {}
    for p in players:
        k = normalize_name(p.name)
        h = hist.get(k, [])
        if not h:
            continue
        last = h[-5:]
        out[k] = {"n": len(h), "recent": h[-1][0] >= n - 3,
                  "proj": statistics.median(x[1] for x in last),
                  "mins": statistics.median(x[2] for x in last),
                  "start_rate": sum(1 for x in last if x[3]) / len(last)}
    return out


def takeover(players, active, NM, inj):
    """Players whose projection is 1.3x their normal (and +5) because a regular is
    out on their team and leaves 10%+ of its normal projection open. Which Chalk
    report: as chalk they reached projection 63% against 41%."""
    teams = collections.defaultdict(list)
    for p in players:
        if p.team:
            teams[p.team].append(p)
    free = {}
    for t, ps in teams.items():
        reg = [p for p in ps if NM.get(normalize_name(p.name), {}).get("n", 0) >= 2
               and (NM[normalize_name(p.name)]["mins"] >= 20
                    or NM[normalize_name(p.name)]["start_rate"] >= 0.5)]
        out = [p for p in reg if NM[normalize_name(p.name)]["recent"]
               and (p.proj <= 0 or p.removed
                    or inj.get(normalize_name(p.name), "") not in ("", "None"))]
        tot = sum(NM[normalize_name(p.name)]["proj"] for p in reg) or 1.0
        free[t] = sum(NM[normalize_name(p.name)]["proj"] for p in out) / tot
    hits = {}
    for p in active:
        nm = NM.get(normalize_name(p.name))
        if nm and p.proj >= 1.3 * nm["proj"] and p.proj - nm["proj"] >= 5 \
                and free.get(p.team, 0) >= 0.10:
            hits[p.dk_id] = nm["proj"]
    return hits


# --- candidates ---------------------------------------------------------------
def _cand(ps, top10=0.0):
    return {"ps": ps, "proj": sum(p.proj for p in ps), "top10": top10,
            "key": frozenset(p.dk_id for p in ps)}


def gen_candidates(allowed, draw, B, V, rng, anchor=None):
    """Slot-first sampling. -> list of candidates (players in DK slot order).
    anchor: a player placed first, in a random slot he can fill, in every
    candidate (the leverage block, G11); he is never swapped out by the spend-up."""
    keys = sorted(allowed, key=lambda p: p.dk_id)       # the draw must repeat
    byslot = {s: [p for p in keys if s in p.eligible] for s in CLASSIC_SLOTS}
    if not keys or any(not byslot[s] for s in CLASSIC_SLOTS):
        return []
    w = {p.dk_id: max(p.proj, 1.0) ** draw["weight"] for p in keys}
    minsal = min(p.salary for p in keys)
    out, tries = {}, 0
    use_ranges = draw["ranges"] and B
    while len(out) < draw["n"] and tries < draw["tries"]:
        tries += 1
        order = list(CLASSIC_SLOTS)
        rng.shuffle(order)
        names = [None] * 8
        used, sal, ok = set(), 0, True
        if anchor is not None:
            aslots = [s for s in CLASSIC_SLOTS if s in anchor.eligible]
            if not aslots:
                return []
            names[CLASSIC_SLOTS.index(rng.choice(aslots))] = anchor
            used.add(anchor.dk_id)
            sal += anchor.salary
        for s in order:
            i = CLASSIC_SLOTS.index(s)
            if names[i] is not None:
                continue
            left = sum(1 for x in names if x is None) - 1
            c = [p for p in byslot[s] if p.dk_id not in used
                 and sal + p.salary + left * minsal <= SALARY_CAP]
            if not c:
                ok = False
                break
            p = rng.choices(c, weights=[w[x.dk_id] for x in c])[0]
            names[i] = p
            used.add(p.dk_id)
            sal += p.salary
        if not ok:
            continue
        for _ in range(6):                                   # spend up
            if SALARY_CAP - sal <= draw["left"]:
                break
            i = rng.randrange(8)
            cur = names[i]
            if anchor is not None and cur is anchor:
                continue
            ups = [p for p in byslot[CLASSIC_SLOTS[i]] if p.dk_id not in used
                   and cur.salary < p.salary <= cur.salary + SALARY_CAP - sal
                   and w[p.dk_id] >= 0.6 * w[cur.dk_id]]
            if ups:
                p = rng.choice(ups)
                used.discard(cur.dk_id)
                used.add(p.dk_id)
                sal += p.salary - cur.salary
                names[i] = p
        key = frozenset(used)
        if key in out or len({p.game for p in names}) < 2:
            continue
        if use_ranges and not _fits(_feats([p.salary for p in names],
                                           [p.ownership for p in names],
                                           [p.dk_id for p in names], V), B):
            continue
        out[key] = _cand(names)
    return list(out.values())


LEV_DRAW = dict(weight=2.5, left=300, n=1500, tries=30000, ranges=False)
LEV_OTHER_CAP = 0.85     # in the block, every other player at 85% of it at most
LEV_DUST = 0.02          # "scattered": in 2% of the set or less (3 of a 150)


def leverage_block(rosters, anchor, k, allowed, V, seed, caps=None):
    """k lineups built around `anchor` from `allowed` (the typed pool and him),
    none already in the set, picked by projection with every other player at
    85% of the block at most (and under his typed cap). The Leverage review's
    harness: shape 'anchor', the draw with no winners' ranges."""
    if k <= 0:
        return []
    c = gen_candidates(allowed, LEV_DRAW, None, V, random.Random(1000 + seed), anchor=anchor)
    have = {frozenset(p.dk_id for p in r) for r in rosters}
    other = max(1, int(LEV_OTHER_CAP * k + 1e-9))
    lcaps = {p.dk_id: other for p in allowed if p.dk_id != anchor.dk_id}
    for pid, v in (caps or {}).items():
        if pid in lcaps:
            lcaps[pid] = min(lcaps[pid], v)
    return select([x for x in c if x["key"] not in have], k, 1.0, k,
                  collections.Counter(), set(), "proj", lcaps)


def _dust_drop(rosters, k, keep_ids=()):
    """The k lineups holding the most players the set has in 2% of it or less,
    not counting `keep_ids`; ties off the end of the pick order. -> indexes"""
    n = len(rosters)
    if k <= 0 or not n:
        return set()
    cnt = collections.Counter(p.dk_id for r in rosters for p in r)
    thr = max(1, int(round(LEV_DUST * n)))
    sc = lambda r: sum(1 for p in r if cnt[p.dk_id] <= thr and p.dk_id not in keep_ids)
    return set(sorted(range(n), key=lambda i: (-sc(rosters[i]), -i))[:k])


def spread_spec(games, limit=None, shape=True, cap=True, pool=True):
    """T1: how tonight's 150 is spread. -> {"limit", "shape", "cap", "pool"}
    pool (Pool On 5 To 7 Games review, Nov 21): Stokastic's file is first cut to
    lineups made only of players in your pool. On wherever the 150 takes
    lineups from the file (5+ games, see plan())."""
    f = SHAPE_GAMES[0] <= games <= SHAPE_GAMES[1]
    return {"limit": SHARE_LIMIT if limit is None else limit,
            "shape": dict(SHAPE) if (f and shape) else None,
            "cap": CAP_SPREAD if (f and cap) else None,
            "pool": bool(pool) and games >= 5}


def field_lineup(players):
    """What a field lineup carries, from projected ownership only: its summed
    ownership and its count of 15%+ players. -> (own sum, chalk count)"""
    live = [p for p in players if (p.proj or 0) > 0]
    return (sum(p.ownership ** 2 for p in live) / 100.0,
            sum(p.ownership / 100.0 for p in live if p.ownership >= 15))


def shape_ok(c, S, own_sum, chalk):
    """Is lineup c in the top-10 shape S?"""
    o = sorted(p.ownership for p in c["ps"])
    return (SALARY_CAP - sum(p.salary for p in c["ps"]) <= S["left"]
            and S["chalk"][0] <= sum(1 for v in o if v >= 15) - chalk <= S["chalk"][1]
            and sum(1 for v in o if v < 10) <= S["under10"]
            and o[0] >= S["minOwn"]
            and own_sum > 0 and S["own"][0] <= sum(o) / own_sum <= S["own"][1])


def file_candidates(field_lineups, active_ids, slotting):
    """Stokastic's lineups that can be entered tonight, deduplicated across files."""
    out, seen = [], set()
    for lu in field_lineups:
        ps = lu.players
        key = frozenset(p.dk_id for p in ps)
        if key in seen or len(key) != 8 or any(p.dk_id not in active_ids for p in ps):
            continue
        if sum(p.salary for p in ps) > SALARY_CAP or len({p.game for p in ps}) < 2:
            continue
        slots = slotting(ps)
        if not slots:
            continue
        seen.add(key)
        out.append(_cand(slots, float(lu.metrics.get("top10") or 0.0)))
    return out


def pool_only(fc, players, floors=None, let_in=None):
    """The pool respected: Stokastic's lineups made only of players in your pool.

    fc: file_candidates(...). A player counts as in the pool when his name was
    typed in the pool or cores box (p.in_pool), when he has a typed minimum
    (floors, as the pool draw already reads it), or when he is let in tonight
    (let_in: dk_ids, the tick). No count is set for anyone: a lineup is kept or
    not, and the pick decides the rest. With no pool typed the file is left as
    it is. -> the lineups kept, in the file's order."""
    ok = {p.dk_id for p in players if p.in_pool}
    if not ok:
        return fc
    ok |= set(floors or ()) | set(let_in or ())
    return [c for c in fc if all(p.dk_id in ok for p in c["ps"])]


# --- one live piece (G14), the stud floor (G12) ------------------------------
def win_spec(games, pair=None, stud_floor=False, spread=None):
    """The G14 spec for a slate, or None when it does not fire. stud_floor: add
    G12's floor (a shadow since Nov 17; True only for the shadow set and for
    rebuilding Nov 15-16, which were built with it). spread: G19's shadow, no
    live piece in more than this share of the set (Step Back, Nov 19)."""
    if not (LIVE_GAMES[0] <= games <= LIVE_GAMES[1]):
        return None
    return {"band": (LIVE_LO, LIVE_HI), "pair": pair, "studFloor": bool(stud_floor),
            "spread": spread}


def stud_load(rosters):
    """Studs ($9,000+) a lineup in a set of rosters (logged, Nov 17)."""
    rosters = list(rosters)
    if not rosters:
        return None
    return round(sum(1 for r in rosters for p in r if p.salary >= STUD) / len(rosters), 2)


def live_ok(c, band, pair=None):
    """Does a lineup have one live piece (and, for the shadow, a rare pair)?"""
    o = sorted(p.ownership for p in c["ps"])
    if not (band[0] <= o[0] < band[1]):
        return False
    return pair is None or o[0] * o[1] / 100.0 < pair


def live_candidates(active):
    """Players at 2-10% projected own and projected 20+ (logged each night)."""
    return [p for p in active if LIVE_LO <= p.ownership < LIVE_HI and p.proj >= 20]


def top5_own(active):
    """The five highest projected ownerships, summed (logged each night)."""
    return round(sum(sorted((p.ownership for p in active), reverse=True)[:5]), 1)


def stud_floors(active, base, n, caps=None):
    """G12: {dk_id: lineups} for each stud at 5-20% own the plain pick holds
    under 1.5x his own."""
    held = collections.Counter(p.dk_id for c in base for p in c["ps"])
    out = {}
    for p in active:
        if p.salary < STUD or p.proj < STUD_FLOOR_PROJ:
            continue
        if not (STUD_FLOOR_LO <= p.ownership <= STUD_FLOOR_HI):
            continue
        up = int(round(STUD_FLOOR_K * p.ownership / 100.0 * n))
        if caps and p.dk_id in caps:
            up = min(up, caps[p.dk_id])
        if up > held[p.dk_id]:
            out[p.dk_id] = up
    return out


def win_pick(fc, need, cap, n, cnt, taken, key, caps, active, win):
    """G14 + G12: the file lineups for the 150. -> (chosen, info)"""
    info = {"band": list(win["band"]), "pair": win.get("pair"), "studFloors": {},
            "studLineups": 0, "fileLive": 0, "fellBack": False}
    pre = []
    if win.get("studFloor"):
        base = select(fc, need, cap, n, collections.Counter(), set(), key, caps)
        sfl = stud_floors(active, base, n, caps)
        info["studFloors"] = sfl
        for k, up in sorted(sfl.items(), key=lambda t: -t[1]):
            want = min(up - cnt[k], need - len(pre))
            if want <= 0:
                continue
            sub = [c for c in fc if c["key"] not in taken and any(p.dk_id == k for p in c["ps"])]
            pre += select(sub, want, cap, n, cnt, taken, key, caps)
        info["studLineups"] = len(pre)
    left = need - len(pre)
    view = [c for c in fc if c["key"] not in taken]
    live = [c for c in view if live_ok(c, win["band"], win.get("pair"))]
    info["fileLive"] = len(live)
    picked = []
    g = gc = None
    gcnt = collections.Counter()
    if win.get("spread"):                    # G19's shadow: each live piece in 5% at most
        band = win["band"]
        gc = max(1, int(win["spread"] * n + 1e-9))

        def g(c):
            lo = min(c["ps"], key=lambda p: p.ownership)
            return lo.dk_id if band[0] <= lo.ownership < band[1] else None
    if live and len(live) >= LIVE_LEFT_K * max(left, 1):
        if win.get("pair") is not None:      # the shadow: rare pair first, then any live piece
            picked = select(live, left, cap, n, cnt, taken, key, caps)
            more = [c for c in view if c["key"] not in taken and live_ok(c, win["band"])]
            picked += select(more, left - len(picked), cap, n, cnt, taken, key, caps)
        else:
            picked = select(live, left, cap, n, cnt, taken, key, caps, g, gc, gcnt)
    else:
        info["fellBack"] = True
    rest = select(view, left - len(picked), cap, n, cnt, taken, key, caps, g, gc, gcnt)
    return pre + picked + rest, info


# --- the 20s from the 150 (G17) and its shadows -------------------------------
def twenties_from(rosters, sources, copies, k=SMALL_SET, step=FROM150_STEP, skip=None):
    """G17: each copy's k lineups taken evenly from the 150, every `step`th
    lineup from a different start (copy i starts at lineup i, wrapping after
    `step` copies). skip: a dk_id; lineups holding him are passed over (G18's
    shadow), and the step is then the remaining count over k. -> chunks"""
    idx = [i for i, r in enumerate(rosters) if skip is None or all(p.dk_id != skip for p in r)]
    st = step if skip is None else max(1, len(idx) // k)
    chunks = []
    for ci in range(max(1, copies)):
        pick = idx[ci % st::st][:k]
        if len(pick) < k:                       # a short 150: fill from the rest in order
            pick += [i for i in idx if i not in pick][:k - len(pick)]
        chunks.append(([list(rosters[i]) for i in pick],
                       [(sources[i] if i < len(sources) else "vendor") for i in pick]))
    return chunks


def upper_sd(active):
    """{dk_id: Std Dev} with the Std Dev halved for players projected 24 minutes
    or under (the Sim Chalk report's narrowed spread)."""
    return {p.dk_id: (p.sd or 0.0) * (UPPER_HALF if 0 < (p.minutes or 0) <= UPPER_MINS else 1.0)
            for p in active}


# --- selection and exposure ------------------------------------------------
def select(cands, n, max_exp, total, cnt, taken, key="proj", caps=None, group=None,
           gcap=None, gcnt=None, max_shared=None):
    """Greedy by `key` under a per-player cap (a share of `total`) and typed caps
    (counts). group / gcap: at most gcap lineups per group(c) (None = no group;
    G19's shadow, one live piece in at most 5% of the set). max_shared (T1): a
    lineup sharing more than this many players with one already taken is
    passed over; if that leaves the set short, a second pass fills it without
    the limit. -> chosen candidates; `cnt` and `taken` are updated."""
    chosen = []
    gcnt = gcnt if gcnt is not None else collections.Counter()
    srt = sorted(cands, key=lambda c: -c[key])
    sets = [set(t) for t in taken] if max_shared is not None else None
    for strict in ((True, False) if max_shared is not None else (False,)):
        for c in srt:
            if len(chosen) >= n:
                break
            if c["key"] in taken:
                continue
            if any(cnt[p.dk_id] + 1 > max_exp * total for p in c["ps"]):
                continue
            if caps and any(cnt[p.dk_id] + 1 > caps[p.dk_id] for p in c["ps"] if p.dk_id in caps):
                continue
            ks = set(c["key"]) if sets is not None else None
            if strict and any(len(ks & s_) > max_shared for s_ in sets):
                continue
            g = group(c) if group else None
            if g is not None and gcnt[g] + 1 > gcap:
                continue
            if g is not None:
                gcnt[g] += 1
            chosen.append(c)
            taken.add(c["key"])
            if sets is not None:
                sets.append(ks)
            for p in c["ps"]:
                cnt[p.dk_id] += 1
    return chosen


def _swap_ok(r, i, y, keys):
    """Can player y take slot i of roster r? (eligible, cap, two games, distinct)"""
    x = r[i]
    if y in r or CLASSIC_SLOTS[i] not in y.eligible:
        return False
    if sum(p.salary for p in r) - x.salary + y.salary > SALARY_CAP:
        return False
    new = r[:i] + [y] + r[i + 1:]
    if len({p.game for p in new}) < 2:
        return False
    nk = frozenset(p.dk_id for p in new)
    return nk not in keys


def _apply(rosters, keys, j, i, y):
    old = frozenset(p.dk_id for p in rosters[j])
    rosters[j][i] = y
    keys.discard(old)
    keys.add(frozenset(p.dk_id for p in rosters[j]))


def retarget(rosters, targets, by_id):
    """targets {dk_id: (floor count or None, ceiling count or None)}. Ceilings
    first: the last lineups holding a player give him up for the set's most-used
    eligible player within the band who is under his own ceiling. Then floors:
    lineups without the player give up, at a slot he can take, the player of
    similar salary with the lowest projection. -> (cut, raised)"""
    keys = {frozenset(p.dk_id for p in r) for r in rosters}
    cnt = collections.Counter(p.dk_id for r in rosters for p in r)
    lo = lambda k: targets.get(k, (None, None))[0]
    hi = lambda k: targets.get(k, (None, None))[1]
    cuts = raises = 0
    order = [by_id[k] for k, _ in cnt.most_common()]
    for k in [k for k, _ in cnt.most_common() if hi(k) is not None and cnt[k] > hi(k)]:
        x = by_id[k]
        excess = cnt[k] - hi(k)
        for j in [j for j, r in enumerate(rosters) if x in r][::-1]:
            if excess <= 0:
                break
            i = rosters[j].index(x)
            done = False
            for band in (SWAP_BAND, 2 * SWAP_BAND, 3 * SWAP_BAND):
                for y in order:
                    if (hi(y.dk_id) is not None and cnt[y.dk_id] >= hi(y.dk_id)) \
                            or abs(y.salary - x.salary) > band:
                        continue
                    if _swap_ok(rosters[j], i, y, keys):
                        _apply(rosters, keys, j, i, y)
                        cnt[k] -= 1
                        cnt[y.dk_id] += 1
                        cuts += 1
                        excess -= 1
                        done = True
                        break
                if done:
                    break
    for k in sorted((k for k in targets if lo(k) is not None and cnt[k] < lo(k)),
                    key=lambda k: -by_id[k].proj):
        x = by_id[k]
        need = lo(k) - cnt[k]
        opts = []
        for j, r in enumerate(rosters):
            if x in r:
                continue
            best = None
            for i, y in enumerate(r):
                if abs(y.salary - x.salary) > SWAP_BAND:
                    continue
                if lo(y.dk_id) is not None and cnt[y.dk_id] - 1 < lo(y.dk_id):
                    continue
                if not _swap_ok(r, i, x, keys):
                    continue
                if best is None or y.proj < best[1].proj:
                    best = (i, y)
            if best:
                opts.append((best[1].proj, j, best[0], best[1]))
        for _, j, i, y in sorted(opts, key=lambda t: t[0])[:need]:
            if x in rosters[j] or rosters[j][i] is not y or not _swap_ok(rosters[j], i, x, keys):
                continue
            _apply(rosters, keys, j, i, x)
            cnt[y.dk_id] -= 1
            cnt[k] += 1
            raises += 1
    return cuts, raises


def tail_fix(rosters, by_id, min_count, keep=(), caps=None):
    """A player in fewer than `min_count` lineups is replaced by the set's
    most-used eligible player within $500 (typed minimums are left alone)."""
    keys = {frozenset(p.dk_id for p in r) for r in rosters}
    cnt = collections.Counter(p.dk_id for r in rosters for p in r)
    order = [by_id[k] for k, _ in cnt.most_common()]
    moved = 0
    for j, r in enumerate(rosters):
        for i, x in enumerate(list(r)):
            if cnt[x.dk_id] >= min_count or x.dk_id in keep:
                continue
            for y in order:
                if cnt[y.dk_id] < min_count or abs(y.salary - x.salary) > SWAP_BAND:
                    continue
                if caps and y.dk_id in caps and cnt[y.dk_id] + 1 > caps[y.dk_id]:
                    continue
                if _swap_ok(rosters[j], i, y, keys):
                    _apply(rosters, keys, j, i, y)
                    cnt[x.dk_id] -= 1
                    cnt[y.dk_id] += 1
                    moved += 1
                    break
    return moved


# --- a typed minimum that is met (Minimum Box quick ask, Nov 22) -------------
# A minimum is met from the pick's own lineups, at any price. The player's best
# lineups, in the pick's own order, take the place of the last lineups in the
# pick order that do not hold him. A lineup that comes in keeps every limit of
# the set: not already in it, the cap on any one player, typed and ticked caps,
# the shared-player limit, another player's minimum. The swap is no longer
# used for a minimum (it checked none of those). MIN_DRAW: when the set's own
# lineups run out, this many more are drawn around him, inside the winners'
# ranges; if that still leaves him short the ranges give way for his lineups
# only (MIN_RANGES_GIVE_WAY, the way the tool already drops them when they
# leave a set short); only then does the build fall short, and it says so.
MIN_DRAW = dict(n=1500, tries=30000)
MIN_RANGES_GIVE_WAY = True


def meet_minimum(rosters, sources, pid, want, tiers, cnt, taken, max_exp, total,
                 caps=None, keep=None, max_shared=None):
    """One set (`rosters` in pick order, `sources` beside it), one player.
    tiers: [(source label, name, function -> his candidates, best first)],
    each tried only if the one before left him short. cnt / taken: the whole
    pick's counts and lineup keys (updated). keep: {dk_id: lineups}, the other
    players' minimums in this set, never broken.
    -> {"held": lineups that hold him now, "added": {tier name: lineups}}"""
    caps = caps or {}
    keep = keep or {}
    sets = [frozenset(p.dk_id for p in r) for r in rosters]
    here = collections.Counter(k for s in sets for k in s)       # this set's own counts
    held = here[pid]
    added = collections.OrderedDict()
    for label, name, get in tiers:
        if held >= want:
            break
        for c in get():
            if held >= want:
                break
            if c["key"] in taken:
                continue
            # the lineup that gives way: the last in the pick order that does not
            # hold him and that no other player's minimum needs (a player the
            # new lineup also holds is not lost)
            j = next((j for j in range(len(rosters) - 1, -1, -1) if pid not in sets[j]
                      and all(here[q] - 1 + (q in c["key"]) >= keep[q]
                              for q in sets[j] if q in keep)), None)
            if j is None:
                continue
            out = sets[j]
            if any(cnt[p.dk_id] - (p.dk_id in out) + 1 > max_exp * total for p in c["ps"]):
                continue
            if any(cnt[p.dk_id] - (p.dk_id in out) + 1 > caps[p.dk_id]
                   for p in c["ps"] if p.dk_id in caps):
                continue
            if max_shared is not None and any(len(c["key"] & s) > max_shared
                                              for i, s in enumerate(sets) if i != j):
                continue
            for p in rosters[j]:
                cnt[p.dk_id] -= 1
                here[p.dk_id] -= 1
            taken.discard(out)
            rosters[j] = list(c["ps"])
            sources[j] = label
            sets[j] = c["key"]
            taken.add(c["key"])
            for p in c["ps"]:
                cnt[p.dk_id] += 1
                here[p.dk_id] += 1
            held += 1
            added[name] = added.get(name, 0) + 1
    return {"held": held, "added": added}


def twenties_min(chunks, rosters, sources, mins, by_id):
    """G17's 20s (every 7th lineup of the 150) with a typed minimum: a 20 that
    holds the player in fewer than mins[dk_id] lineups takes the 150's next
    lineups that hold him (in the 150's order, not in any 20 yet) in place of
    its own last lineups without him. -> [note lines] for a 20 still short."""
    keys = [frozenset(p.dk_id for p in r) for r in rosters]
    used = {frozenset(p.dk_id for p in r) for ros, _ in chunks for r in ros}
    notes = []
    for ci, (ros, src) in enumerate(chunks):
        for pid in sorted(mins, key=lambda k: -by_id[k].proj):
            want = min(mins[pid], len(ros))
            has = lambda r: any(p.dk_id == pid for p in r)
            held = sum(1 for r in ros if has(r))
            spare = [i for i, k in enumerate(keys) if pid in k and k not in used]
            while held < want and spare:
                i = spare.pop(0)
                here = collections.Counter(p.dk_id for r in ros for p in r)
                j = next((j for j in range(len(ros) - 1, -1, -1) if not has(ros[j])
                          and all(here[p.dk_id] - 1 + (p.dk_id in keys[i])
                                  >= min(mins[p.dk_id], len(ros))
                                  for p in ros[j] if p.dk_id in mins)), None)
                if j is None:
                    continue
                used.discard(frozenset(p.dk_id for p in ros[j]))
                ros[j] = list(rosters[i])
                src[j] = sources[i] if i < len(sources) else "vendor"
                used.add(keys[i])
                held += 1
            if held < want:
                notes.append(f"Minimum not met: {by_id[pid].name} is in {held} of 20-max set "
                             f"{ci + 1}'s {len(ros)} (asked {want}): the 150 holds him in too "
                             f"few lineups")
    return notes


# --- one marked player in every lineup, 8+ games (Big Slates quick ask, Nov 23) ---
# On a slate of MARK_GAMES or more games every lineup the 150 takes from
# Stokastic's file holds at least one marked player: in the pool, projected
# under MARK_OWN % owned, projected MARK_PROJ or more points, and with a ceiling
# (projection + 0.6745 x Std Dev, which is the file's own Ceiling column) within
# MARK_GAP points of the big-game score for his price (the chalk sheet's
# table). There is no count for any player: the pick takes the file's lineups
# in its own order, as before, from the ones that hold one. If those cannot
# fill the set, the rest of the file fills it, then the pool draw.
# MARK_MIN_SHARE: the list has to be broad. When fewer than this share of the
# file's lineups hold a marked player the rule is off for the night and the
# build says so (a narrow list shuts out the night's best lineups: with the
# ownership line at 7.5% the 150 lost its best lineup on two nights of six).
# MARK_ON = False turns the rule off.
MARK_ON = True
MARK_MIN_SHARE = 0.70
MARK_GAMES = 8
MARK_OWN = 10.0
MARK_PROJ = 15.0
MARK_GAP = 3.0
CEIL_Z = 0.6745
MARK_FILL = "file"        # what fills when too few lineups hold one: the rest of the file first
# The ceiling line floats (Contest Review And Big Slates report, Nov 24). Prices
# have tightened since October, so fewer low-owned players have a ceiling within
# MARK_GAP points of a big game: 16 to 23 pool players were marked on the six
# nights the rule was tested on and 3 on Nov 23, where the guard turned it off.
# With MARK_FLOAT the line starts at MARK_GAP and widens MARK_STEP points at a
# time until MARK_MIN_SHARE of the file's lineups hold a marked player, so the
# rule runs every 8+ game night. On a night the line at MARK_GAP is already
# broad enough nothing changes. If the list is still too narrow at MARK_GAP_MAX
# the rule is off for the night, as before. MARK_FLOAT = False is the fixed line.
MARK_FLOAT = True
MARK_STEP = 0.5
MARK_GAP_MAX = 30.0

# --- a wider pool on 8+ games (Contest Review And Big Slates report, Nov 24) ---
# On a slate of WIDE_GAMES or more games, off-pool players projected at WIDE_OWN %
# owned or less whose projection is within WIDE_NEAR points of what they need
# for their price (the chalk sheet's break-even) are let in for the 150:
# Stokastic's lineups that hold them count as pool lineups, exactly as the "let
# him in" tick does. No count is set for anyone: a lineup is kept or not and the
# pick decides the rest. They are not marked players and the pool draw does not
# use them. WIDE_ON = False turns it off.
WIDE_ON = True
WIDE_GAMES = 8
WIDE_OWN = 5.0
WIDE_NEAR = 2.0


def wide_pool(players, inj=None, _t=[]):
    """dk_ids of the off-pool players let in on a big slate (see the note above)."""
    if not _t:
        import chalk_sheet as CS       # here, not at the top: chalk_sheet imports this module
        _t.append((CS, CS.load_needs()))
    CS, T = _t[0]
    out = set()
    for p in players:
        if p.in_pool or p.removed or p.salary <= 0 or p.proj <= 0:
            continue
        if not (p.ownership or 0.0) <= WIDE_OWN:
            continue
        if inj is not None and not is_active(p, inj):
            continue
        if p.proj >= CS.need(T, p.salary, p.ownership or 0.0)[0] - WIDE_NEAR:
            out.add(p.dk_id)
    return out


def big_game_score(p, _t=[]):
    """The big-game score for his price, from the chalk sheet's fitted table."""
    if not _t:
        import chalk_sheet as CS       # here, not at the top: chalk_sheet imports this module
        _t.append((CS, CS.load_needs()))
    CS, T = _t[0]
    return CS.need(T, p.salary, p.ownership)[1]


# --- one of tonight's top projections in every lineup (Raw Points report, Nov 24) ---
# On a slate of RAW_GAMES or more games every lineup the 150 takes from
# Stokastic's file holds at least one of tonight's top RAW_TOP projections (raw
# projected points, any price, in the pool or not). The file lineups that hold
# one are taken first, in the file's own order; the rest of the file fills only
# if they run short under the limit and the cap. No count is set for anyone.
# On the seven 8+ game nights the lineups that placed held more of the top
# projections than the field (1.12 of the top five a lineup against 0.80) and
# the 150 held 0.94. With the rule the 150's best lineup finished higher on
# three nights and lower on none, and a random 20 of the 150 higher on four of
# seven; the same holds for the top 4 to 8, not for the top 3 (too narrow), not
# for the five highest prices and not for two of the five. RAW_ON = False turns
# it off. On 5 to 7 games it is level so far, so it is off there.
RAW_ON = True
RAW_GAMES = 8
RAW_TOP = 5


def top_projections(players, inj=None, n=None):
    """dk_ids of tonight's top projections (see the note above): the n highest
    projections among the players who are playing and not removed."""
    n = RAW_TOP if n is None else n
    live = [p for p in players if not p.removed and p.salary > 0 and p.proj > 0
            and (inj is None or is_active(p, inj))]
    live.sort(key=lambda p: (-p.proj, p.name))
    return {p.dk_id for p in live[:n]}


def marked_players(players, inj=None, gap=None):
    """dk_ids of tonight's marked players (see the note above). gap: the ceiling
    line in points (MARK_GAP when not given)."""
    gap = MARK_GAP if gap is None else gap
    out = set()
    for p in players:
        if not p.in_pool or p.removed or p.proj < MARK_PROJ or not p.ownership < MARK_OWN:
            continue
        if inj is not None and not is_active(p, inj):
            continue
        if p.proj + CEIL_Z * (p.sd or 0.0) >= big_game_score(p) - gap:
            out.add(p.dk_id)
    return out


# --- the build ---------------------------------------------------------------
def build_set(players, n, games, field_lineups, inj, caps=None, floors=None, seed=0,
              history=None, slotting=None, copies=1, file_pick=None, raises=None,
              anchor=None, win=None, upper=None, spread=None, takeover_k=None,
              top3_k=None, let_in=None):
    """One set (a 150 or a 20). -> dict(rosters, sources, chunks, notes, meta)

    players: every player with DK ids attached, pool flags set and removals
    marked. caps / floors: {dk_id: lineups}. slotting: players -> slot order.
    copies (20-max only): how many different 20s. The second takes the next 20
    in the pick order (Slate By Slate Calls report, 5C); each gets its own
    top-3 raise. file_pick: "proj" or "top10" to override the file pick (the
    grader, rebuilding a night built before Nov 13). raises: {dk_id: share of
    the set}, floors applied after the pick the way the takeover lean is (the
    easy-matchup floor, G10). anchor: {"id": dk_id, "k": lineups}, a block of k
    lineups built around that player replacing the k lineups that hold the
    most scattered positions (the leverage block, G11). win: win_spec(games),
    the 150's file pick on 5-7 games takes only lineups with one live piece,
    after the low-owned studs' floors when win["studFloor"] (G12, a shadow
    since Nov 17). chunks = [(rosters,
    sources)] one per copy. upper: {dk_id: Std Dev} picks our own lineups by
    their upper end (summed projection + 1.28 x the lineup's Std Dev) instead of
    the projection (the Sim Chalk report's narrowed-spread 20s, a shadow).
    spread: spread_spec(games), the 150 only (T1): the shared-player limit, and
    on 5-7 games the top-10 shape on the file and the lower cap on any one
    player. takeover_k / top3_k: the lean and the top-three raise (None = the
    module's settings, both 0 since Nov 20). let_in: dk_ids of off-pool players
    let in tonight (the tick): the file's lineups that hold one count as pool
    lineups when spread["pool"] is on."""
    tk_k = TAKEOVER_K if takeover_k is None else takeover_k
    t3_k = TOP3_K if top3_k is None else top3_k
    notes = []
    caps = dict(caps or {})
    floors = dict(floors or {})
    history = history if history is not None else load_history()
    active = [p for p in players if is_active(p, inj) and caps.get(p.dk_id, 1) > 0]
    by_id = {p.dk_id: p for p in players}
    active_ids = {p.dk_id for p in active}
    big = n > SMALL_SET
    copies = 1 if big else max(1, int(copies))
    fc = file_candidates(field_lineups or [], active_ids, slotting) if big else []
    spread = spread if big else None
    ms = spread["limit"] if spread else None
    pool_info = None
    wide_info = None
    if spread and spread.get("pool") and fc and any(p.in_pool for p in players):
        n_all = len(fc)
        wide = set()
        if WIDE_ON and games >= WIDE_GAMES:
            try:
                wide = wide_pool(players, inj) & active_ids
            except Exception as e:              # never worth losing the build over
                notes.append(f"The wider pool could not be read ({type(e).__name__}: {e}), "
                             f"so it is off tonight")
            n_typed = len(pool_only(fc, players, floors, let_in))
            wide_info = {"added": sorted(by_id[k].name for k in wide if k in by_id),
                         "poolOnlyBefore": n_typed}
        fc = pool_only(fc, players, floors, set(let_in or ()) | wide)
        pool_info = {"file": n_all, "poolOnly": len(fc),
                     "letIn": sorted(by_id[k].name for k in (let_in or ()) if k in by_id)}
    shape_info = None
    fc_all = fc
    if spread and spread.get("shape") and fc:
        eso, ech = field_lineup(players)
        kept = [c for c in fc if shape_ok(c, spread["shape"], eso, ech)]
        shape_min = 1 if pool_info else SHAPE_MIN
        shape_info = {"file": len(fc), "pass": len(kept), "used": len(kept) >= shape_min,
                      "ownSum": round(eso, 1), "chalk": round(ech, 2)}
        if shape_info["used"]:
            fc = kept
    # One marked player in every lineup (8+ games): the file is cut to the
    # lineups that hold one. fc_nomark is the file before the cut, for the fill.
    mark_info = None
    raw_info = None
    fc_nomark = fc
    if big and MARK_ON and games >= MARK_GAMES and fc:
        gap = MARK_GAP
        marked, kept = set(), []
        try:
            marked = marked_players(players, inj, gap)
            kept = [c for c in fc if any(p.dk_id in marked for p in c["ps"])]
            while MARK_FLOAT and len(kept) < MARK_MIN_SHARE * len(fc) and gap < MARK_GAP_MAX:
                gap += MARK_STEP                # the line floats until the list is broad enough
                marked = marked_players(players, inj, gap)
                kept = [c for c in fc if any(p.dk_id in marked for p in c["ps"])]
        except Exception as e:                  # never worth losing the build over
            marked, kept = set(), []
            notes.append(f"The marked players could not be read ({type(e).__name__}: {e}), "
                         f"so that rule is off tonight")
        mark_info = {"file": len(fc), "withOne": len(kept), "filled": 0,
                     "share": round(len(kept) / float(len(fc)), 3),
                     "used": len(kept) >= MARK_MIN_SHARE * len(fc),
                     "gap": gap, "floated": gap > MARK_GAP,
                     "marked": sorted(by_id[k].name for k in marked if k in by_id)}
        if mark_info["used"]:
            fc = kept
    n_own, n_file, draw, rule = plan(n, games, bool(fc))
    if not any(p.in_pool for p in players):
        rule = rule.replace("your pool", "the whole slate (no pool typed)")
    notes.append(rule)
    if pool_info:
        notes.append(f"Pool respected: {pool_info['poolOnly']} of Stokastic's {pool_info['file']} "
                     f"lineups are made only of players in your pool"
                     + (f" (let in: {', '.join(pool_info['letIn'])})" if pool_info["letIn"] else ""))
    if wide_info and wide_info["added"]:
        notes.append(f"Wider pool ({games} games): {len(wide_info['added'])} off-pool players at "
                     f"{WIDE_OWN:g}% owned or less with a projection within {WIDE_NEAR:g} of what "
                     f"they need were let in, with no count for any of them "
                     f"({wide_info['poolOnlyBefore']} lineups before, {pool_info['poolOnly']} now): "
                     + ", ".join(wide_info["added"]))
    if shape_info:
        notes.append(f"Top-10 shape: {shape_info['pass']} of Stokastic's {shape_info['file']} "
                     f"lineups pass" + ("" if shape_info["used"] else
                                        f", under {shape_min}, so the shape was SKIPPED tonight"))
    if spread:
        notes.append(f"Spread: no two lineups share more than {ms} players"
                     + (f"; no player in more than {spread['cap']:.0%} of the set"
                        if spread.get("cap") else ""))
    B = blueprint(history, games) if draw["ranges"] else None
    if draw["ranges"] and not B:
        notes.append("no earlier nights of this slate size saved, so no winners' ranges")
    V = _top_sets([(p.dk_id, p.salary, p.proj, p.boom) for p in active])
    rng = random.Random(seed)

    pool = [p for p in active if p.in_pool or p.dk_id in floors]
    sheet = any(p.in_pool for p in players)
    allowed = pool if sheet else active
    cands = []
    B_used = B                # the ranges in force for this set's draw (None once dropped)
    need_own = n_own > 0 or (big and len(fc) < n_file)
    if need_own or not big:
        cands = gen_candidates(allowed, draw, B, V, rng)
        if B and len(cands) < max(n, 100):
            notes.append(f"only {len(cands)} pool lineups fit the winners' ranges, "
                         f"so the ranges were dropped for tonight")
            cands = gen_candidates(allowed, dict(draw, ranges=False), None, V, rng)
            B_used = None
        if sheet and len(cands) < 100:
            notes.append(f"your pool made only {len(cands)} legal lineups, so the "
                         f"whole slate was used")
            cands = gen_candidates(active, dict(draw, ranges=False), None, V, rng)
            B_used = None

    cap = ((spread or {}).get("cap") or CAP_BIG) if big else CAP_SMALL
    cnt, taken = collections.Counter(), set()
    own_key = "proj"
    if upper is not None:
        own_key = "upper"
        for c in cands:
            c["upper"] = c["proj"] + UPPER_Z * sum(upper.get(p.dk_id, 0.0) ** 2
                                                   for p in c["ps"]) ** 0.5
    own = select(cands, n_own * copies, cap, n * copies, cnt, taken, own_key, caps,
                 max_shared=ms)
    # Which of Stokastic's lineups: on 5-7 games by projected sum, on 8+ by the
    # file's own Top 10% figure (Shark Edge And Slate Triggers report, Nov 13:
    # projected sum ahead on 8 of 9 5-7 game nights, Top 10% still ahead on 8+).
    file_key = file_pick or ("proj" if 5 <= games <= 7 else "top10")
    win_info = None
    if n_file and big and win:
        half, win_info = win_pick(fc, n_file, cap, n, cnt, taken, file_key, caps, active, win)
        if win_info["fellBack"]:
            notes.append(f"One live piece: only {win_info['fileLive']} of Stokastic's lineups "
                         f"have one, so the whole file was used")
        else:
            notes.append(f"One live piece: the 150 is picked from the {win_info['fileLive']} "
                         f"of Stokastic's lineups whose fewest-owned player is at "
                         f"{win['band'][0]:.0f}-{win['band'][1]:.0f}% own")
        if win_info["studFloors"]:
            notes.append("Low-owned studs floored at 1.5x own: " + "; ".join(
                f"{by_id[k].name} to {v} of {n} ({by_id[k].ownership:.0f}% projected owned)"
                for k, v in win_info["studFloors"].items()))
    else:
        half = []
        if big and RAW_ON and games >= RAW_GAMES and n_file and fc:
            # One of tonight's top projections in every lineup: the file lineups
            # that hold one are taken first; the rest fill if they run short.
            try:
                raw_top = top_projections(players, inj)
            except Exception as e:              # never worth losing the build over
                raw_top = set()
                notes.append(f"Tonight's top projections could not be read "
                             f"({type(e).__name__}: {e}), so that rule is off tonight")
            if raw_top:
                kept = [c for c in fc if any(p.dk_id in raw_top for p in c["ps"])]
                half = select(kept, n_file, cap, n, cnt, taken, file_key, caps,
                              max_shared=ms)
                raw_info = {"file": len(fc), "withOne": len(kept), "taken": len(half),
                            "top": sorted((by_id[k].name for k in raw_top if k in by_id),
                                          key=lambda nm: nm)}
        if n_file and len(half) < n_file:
            half += select(fc, n_file - len(half), cap, n, cnt, taken, file_key, caps,
                           max_shared=ms)
        if raw_info:
            raw_info["filled"] = len(half) - raw_info["taken"]
            notes.append(f"One of tonight's top {RAW_TOP} projections in every lineup: "
                         f"{raw_info['withOne']} of the {raw_info['file']} file lineups hold "
                         f"one ({', '.join(raw_info['top'])})"
                         + (f"; only {raw_info['taken']} of them fit under the limit and the "
                            f"cap, so {raw_info['filled']} more came from the rest of the file"
                            if raw_info["filled"] else ""))
    if n_file and len(half) < n_file and shape_info and shape_info["used"]:
        # The shape, the limit and the cap together left the set short: the rest
        # comes from the whole file in its order, under the same limit and cap.
        rest = select(fc_all, n_file - len(half), cap, n, cnt, taken, file_key, caps,
                      max_shared=ms)
        if rest:
            notes.append(f"Only {len(half)} of the shape's lineups fit under the limit and "
                         f"the cap, so {len(rest)} more came from the rest of Stokastic's "
                         f"file (same limit and cap)")
            shape_info["short"] = len(half)
            half += rest
    if mark_info and not mark_info["used"]:
        notes.append(f"One marked player in every lineup is OFF tonight: only "
                     f"{mark_info['withOne']} of the {mark_info['file']} file lineups "
                     f"({mark_info['share']:.0%}) hold one of the {len(mark_info['marked'])} "
                     f"marked players, under {MARK_MIN_SHARE:.0%}"
                     + (", even with the ceiling line at its widest" if mark_info["floated"] else ""))
    if mark_info and mark_info["used"]:
        notes.append(f"One marked player in every lineup: {mark_info['withOne']} of the "
                     f"{mark_info['file']} file lineups hold one of the {len(mark_info['marked'])} "
                     f"marked players"
                     + (f" (the ceiling line was widened from {MARK_GAP:g} to {mark_info['gap']:g} "
                        f"points to make the list broad enough)" if mark_info["floated"] else ""))
        if n_file and len(half) < n_file and MARK_FILL == "file":
            # Too few of them fit: the rest of the file fills, same limit and cap.
            rest = select(fc_nomark, n_file - len(half), cap, n, cnt, taken, file_key, caps,
                          max_shared=ms)
            if rest:
                notes.append(f"Only {len(half)} of them fit under the limit and the cap, so "
                             f"{len(rest)} more came from the rest of Stokastic's file")
                mark_info["filled"] = len(rest)
                half += rest
    chosen = own + half
    sources = ["mine"] * len(own) + ["vendor"] * len(half)
    if n_file and len(half) < n_file:
        if not cands and not need_own:
            cands = gen_candidates(allowed, dict(draw, ranges=False), None, V, rng)
        more = select(cands, n - len(chosen), cap, n, cnt, taken, "proj", caps,
                      max_shared=ms)
        if more:
            notes.append(f"Stokastic's file gave {len(half)} usable lineups, so "
                         f"{len(more)} more came from your pool")
        chosen += more
        sources += ["mine"] * len(more)
    if len(chosen) < n * copies:
        more = select(cands, n * copies - len(chosen), 1.0, n * copies, cnt, taken,
                      "proj", caps, max_shared=ms)
        if more:
            notes.append(f"{len(more)} lineup(s) only fit by going past the "
                         f"{cap:.0%} cap")
        chosen += more
        sources += ["mine"] * len(more)
    allr = [list(c["ps"]) for c in chosen]
    chunks = []
    cut = raised = 0
    min_info = {}
    lean, tk_lean = [], []
    hits = takeover(players, active, normals(history, players), inj) if big else {}
    for ci in range(copies):
        rosters = allr[ci * n:(ci + 1) * n]
        csrc = sources[ci * n:(ci + 1) * n]
        if not rosters:
            break
        # A typed minimum (Minimum Box quick ask, Nov 22): met from the pick's own
        # lineups before the caps' re-target and the tail fix. A cap on the same
        # player wins, and so does the cap on any one player.
        top = int(cap * len(rosters) + 1e-9)
        mins = {k: min(v, len(rosters), top, caps.get(k, 10 ** 6))
                for k, v in floors.items() if k in active_ids and v > 0}
        for k in sorted(mins, key=lambda k: -by_id[k].proj):
            x = by_id[k]

            def drawn(ranges, salt, x=x):
                def get():
                    cs = gen_candidates(allowed if x in allowed else allowed + [x],
                                        dict(draw, ranges=ranges, **MIN_DRAW),
                                        B_used if ranges else None, V,
                                        random.Random(7919 * (salt + 1) + 31 * ci + seed), anchor=x)
                    if upper is not None:
                        for c in cs:
                            c["upper"] = c["proj"] + UPPER_Z * sum(
                                upper.get(p.dk_id, 0.0) ** 2 for p in c["ps"]) ** 0.5
                    return sorted(cs, key=lambda c: -c[own_key])
                return get
            tiers = [("mine", "draw", lambda k=k: sorted((c for c in cands if k in c["key"]),
                                                         key=lambda c: -c[own_key]))]
            if big and n_file:
                tiers.append(("vendor", "file", lambda k=k: sorted(
                    (c for c in fc if k in c["key"]), key=lambda c: -c[file_key])))
                if fc_all is not fc:
                    tiers.append(("vendor", "file2", lambda k=k: sorted(
                        (c for c in fc_all if k in c["key"]), key=lambda c: -c[file_key])))
            if B_used:
                tiers.append(("mine", "more", drawn(True, 0)))
            if not B_used or MIN_RANGES_GIVE_WAY:
                tiers.append(("mine", "noranges" if B_used else "more", drawn(False, 1)))
            got = meet_minimum(rosters, csrc, k, mins[k], tiers, cnt, taken, cap, n * copies,
                               caps, {q: mins[q] for q in mins if q != k}, ms)
            asked = min(floors[k], len(rosters))
            where = "" if copies == 1 else f" (20-max set {ci + 1})"
            if got["added"].get("noranges"):
                notes.append(f"Minimum for {x.name}{where}: {got['added']['noranges']} of his "
                             f"lineups were drawn outside the winners' ranges (too few fit them)")
            if mins[k] < asked:
                notes.append(f"Minimum for {x.name}{where} held to {mins[k]} of {len(rosters)} "
                             f"(you asked {asked}): his cap wins")
            if got["held"] < mins[k]:
                notes.append(f"Minimum not met: {x.name} is in {got['held']} of {len(rosters)}"
                             f"{where} (asked {mins[k]}): no more of his lineups fit the set's "
                             f"limits")
            min_info.setdefault(x.name, []).append(
                {"asked": asked, "want": mins[k], "held": got["held"], "added": dict(got["added"])})
        targets = {}
        for k, v in caps.items():
            lo_, _ = targets.get(k, (None, None))
            targets[k] = (lo_, v)
        if big:
            for k in hits:
                p = by_id[k]
                if p.ownership >= TAKEOVER_MIN_OWN:
                    want = int(round(min(tk_k * p.ownership / 100.0, TAKEOVER_CAP)
                                     * len(rosters)))
                    if k in caps:
                        want = min(want, caps[k])
                    lo_, hi_ = targets.get(k, (None, None))
                    targets[k] = (max(lo_ or 0, want), hi_)
                    if ci == 0 and tk_k > 0:
                        tk_lean.append(f"{p.name} to {want} of {len(rosters)} "
                                       f"({p.ownership:.0f}% projected owned)")
        sfl = (win_info or {}).get("studFloors") or {}
        for k, share in (raises or {}).items():
            if k not in active_ids or k in sfl:
                continue
            want = int(round(min(share, 0.95) * len(rosters)))
            if k in caps:
                want = min(want, caps[k])
            lo_, hi_ = targets.get(k, (None, None))
            targets[k] = (max(lo_ or 0, want), hi_)
        for k, up in sfl.items():            # G12: the floor stands in for any raise
            targets[k] = (min(up, len(rosters)), targets.get(k, (None, None))[1])
        if not big:
            top3 = sorted(active, key=lambda p: -p.ownership)[:3]
            for p in top3:
                if p.ownership >= TOP3_MIN_OWN:
                    want = int(round(min(t3_k * p.ownership / 100.0, TOP3_CAP)
                                     * len(rosters)))
                    if p.dk_id in caps:
                        want = min(want, caps[p.dk_id])
                    lo_, hi_ = targets.get(p.dk_id, (None, None))
                    targets[p.dk_id] = (max(lo_ or 0, want), hi_)
                    if ci == 0 and t3_k > 0:
                        lean.append(f"{p.name} to {want} of {len(rosters)} "
                                    f"({p.ownership:.0f}% projected owned)")
        if targets:
            c_, r_ = retarget(rosters, targets, by_id)
            cut += c_
            raised += r_
        chunks.append((rosters, csrc))
    if lean:
        notes.append("Top three owned raised: " + "; ".join(lean))
    if tk_lean:
        notes.append(f"Takeover chalk raised to {tk_k:g}x own: " + "; ".join(tk_lean))
    if copies > 1:
        notes.append(f"{len(chunks)} different sets of {n}: the second takes the next "
                     f"{n} in the pick order")
    rosters, sources = chunks[0] if chunks else ([], [])
    tail = 0
    if big and rosters and TAIL_ON:
        tail = tail_fix(rosters, by_id, MIN_LINEUPS, keep=set(floors), caps=caps)
    lev = None
    if anchor and anchor.get("id") in active_ids and chunks:
        a = by_id[anchor["id"]]
        base_allowed = allowed if a in allowed else allowed + [a]
        lev = {"anchor": a.name, "asked": int(anchor.get("k") or 0), "built": 0, "dropped": []}
        for ci, (ros, src) in enumerate(chunks):
            k_ = min(int(anchor.get("k") or 0), len(ros), caps.get(a.dk_id, 10 ** 6))
            block = leverage_block(ros, a, k_, base_allowed, V, seed + ci, caps)
            drop = _dust_drop(ros, len(block), {a.dk_id})
            if ci == 0:
                lev["built"] = len(block)
                lev["dropped"] = [[p.name for p in ros[i]] for i in sorted(drop)]
            keep = [i for i in range(len(ros)) if i not in drop]
            ros[:] = [ros[i] for i in keep] + [list(c["ps"]) for c in block]
            src[:] = [src[i] for i in keep] + ["leverage"] * len(block)
        rosters, sources = chunks[0]
        notes.append(f"Leverage block: {lev['built']} lineups built around {a.name} "
                     f"({a.ownership:.0f}% projected owned), replacing the "
                     f"{len(lev['dropped'])} holding the most scattered positions")
    return {"rosters": rosters, "sources": sources, "chunks": chunks, "notes": notes,
            "meta": {"rule": rule, "own": len(own), "file": len(half),
                     "filePick": file_key if n_file else None,
                     "poolFile": pool_info, "marked": mark_info, "widePool": wide_info, "rawTop": raw_info,
                     "fileUsable": len(fc), "candidates": len(cands),
                     "draw": "old" if draw is OLD_DRAW else "new",
                     "blueprint": {k: v for k, v in (B or {}).items()},
                     "cap": cap, "retarget": {"cut": cut, "raised": raised},
                     "takeoverLean": [by_id[k].name for k in hits
                                      if by_id[k].ownership >= TAKEOVER_MIN_OWN] if tk_k > 0 else [],
                     "takeoverK": tk_k, "top3K": t3_k,
                     "spread": None if not spread else dict(
                         spread, shapeInfo=shape_info,
                         maxShared=max((len(set(a) & set(b)) for i, a in enumerate(
                             [frozenset(p.dk_id for p in r) for r in rosters])
                             for b in [frozenset(p.dk_id for p in r) for r in rosters[:i]]),
                             default=0)),
                     "tail": tail, "seed": seed, "minimums": min_info,
                     "raises": {by_id[k].name: round(v, 3) for k, v in (raises or {}).items()
                                if k in by_id},
                     "leverage": lev,
                     "win": None if win_info is None else dict(
                         win_info,
                         studFloors={by_id[k].name: v for k, v in win_info["studFloors"].items()},
                         liveShare=round(sum(1 for r in rosters if live_ok(
                             {"ps": r}, win["band"])) / max(len(rosters), 1), 3))}}


# --- what the screen says ---------------------------------------------------
def before_build(players, active, NM, hits, removed_names, inj):
    """Warnings before the build. None of them changes it."""
    W = []
    sheet = any(p.in_pool for p in players)
    if sheet:
        # The top-5-owned line is replaced by the pool warning (G23, app.py, Nov 19).
        tk = [f"{p.name} ({p.ownership:.0f}%, {p.proj:.0f} against a normal "
              f"{hits[p.dk_id]:.0f})" for p in active if p.dk_id in hits
              and not p.in_pool and p.ownership >= 15]
        if tk:
            W.append("Taken-over-a-role chalk not in your pool: " + ", ".join(tk))
    for p in players:
        if normalize_name(p.name) in removed_names and p.starting:
            W.append(f"You removed {p.name} and the projections list him STARTING — "
                     f"check the scratch.")
    if sheet:
        pk = [p for p in active if p.in_pool]
        for s in CLASSIC_SLOTS:
            if not any(s in p.eligible for p in pk):
                W.append(f"Your pool has nobody for the {s} slot.")
    return W


def off_pool_studs(players, active):
    """Studs off the pool under 12% projected owned — the kind of player behind
    the sharks' biggest calls (Full System report, section 5). Shown, not used."""
    return [p for p in active if not p.in_pool and p.salary >= STUD and p.ownership < 12]


def after_build(rosters, players):
    W = []
    n = len(rosters) or 1
    cnt = collections.Counter(p.dk_id for r in rosters for p in r)
    by_id = {p.dk_id: p for p in players}
    for k, v in cnt.most_common(5):
        if v / n >= 0.7:
            p = by_id[k]
            W.append(f"{p.name} in {v} of {n} ({100 * v / n:.0f}%, projected own "
                     f"{p.ownership:.0f}%)")
    return W


# --- what the tool saves for the after-the-fact check -------------------------
def _stamp():
    now = datetime.now().astimezone()
    return now, now.strftime("%Y-%m-%d"), now.strftime("%H%M%S")


def save_pull(proj_text, source="drop"):
    """Every Stokastic projections file the tool sees, saved whole with its time,
    and a line for each player whose projection, starting flag or injury tag
    moved since the last saved pull of the day (pulls/<date>/moves.jsonl). The
    data for late news and the sharks' calls (Before The Build, section 4)."""
    text = proj_text or ""
    if not text.strip():
        return None
    try:
        now, day, hms = _stamp()
        folder = os.path.join(LOG_DIR, "pulls", day)
        os.makedirs(folder, exist_ok=True)
        sha = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
        index = os.path.join(folder, "index.jsonl")
        prev = None
        if os.path.exists(index):
            with open(index, encoding="utf-8") as fh:
                rows = [json.loads(x) for x in fh if x.strip()]
            if any(r.get("sha1") == sha for r in rows):
                return None
            prev = rows[-1] if rows else None
        name = f"{hms}_projections.csv"
        with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(index, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now.isoformat(timespec="seconds"), "file": name,
                                 "sha1": sha, "source": source}) + "\n")
        if prev:
            old = _read_night_proj(os.path.join(folder, prev["file"]))
            new = _read_night_proj(os.path.join(folder, name))
            with open(os.path.join(folder, "moves.jsonl"), "a", encoding="utf-8") as fh:
                for k, v in new.items():
                    o = old.get(k)
                    if not o:
                        continue
                    dp = v["proj"] - o["proj"]
                    moved = abs(dp) >= 3 or (o["proj"] and abs(dp) / o["proj"] >= 0.10)
                    if moved or v["starting"] != o["starting"] or v["injury"] != o["injury"]:
                        fh.write(json.dumps({
                            "player": k, "team": v["team"], "from": prev["ts"],
                            "to": now.isoformat(timespec="seconds"),
                            "proj": [o["proj"], v["proj"]], "own": [o["own"], v["own"]],
                            "starting": [o["starting"], v["starting"]],
                            "injury": [o["injury"], v["injury"]],
                            "mins": [o["mins"], v["mins"]]}) + "\n")
        return name
    except (OSError, ValueError, csv.Error):
        return None


def pull_of(proj_text):
    """Which saved pull the build read: {"file", "ts", "pulls", "first", "last"}
    from today's pulls/index.jsonl, matched by the file's hash. The predictor is
    only as good as its pull (Predicting The Field report: Stokastic's own moved
    6.7 points a player in the last 90 minutes on Nov 7). -> dict or None"""
    text = proj_text or ""
    if not text.strip():
        return None
    try:
        _now, day, _hms = _stamp()
        index = os.path.join(LOG_DIR, "pulls", day, "index.jsonl")
        if not os.path.exists(index):
            return None
        with open(index, encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh if x.strip()]
    except (OSError, ValueError):
        return None
    sha = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
    hit = next((r for r in rows if r.get("sha1") == sha), None)
    return {"file": hit and hit.get("file"), "ts": hit and hit.get("ts"),
            "pulls": len(rows), "first": rows[0].get("ts") if rows else None,
            "last": rows[-1].get("ts") if rows else None}


def save_shadow(name, data):
    """A shadow build (logged, never entered) for the review: logs/shadow/<date>/
    <time>_<name>.json. -> path or None"""
    try:
        _now, day, hms = _stamp()
        folder = os.path.join(LOG_DIR, "shadow", day)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{hms}_{name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        return path
    except OSError:
        return None


def save_inputs(files, typed):
    """The build's input files and typed settings, so the old tool's set can be
    rebuilt from the same night after the fact. -> folder or None"""
    try:
        _now, day, hms = _stamp()
        folder = os.path.join(LOG_DIR, "inputs", day, hms)
        os.makedirs(folder, exist_ok=True)
        for name, text in files.items():
            if (text or "").strip():
                with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
                    fh.write(text)
        with open(os.path.join(folder, "typed.json"), "w", encoding="utf-8") as fh:
            json.dump(typed, fh, indent=1)
        return folder
    except OSError:
        return None
