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
  20-max    always    : 20 from the pool candidates, then the top three by
                        projected ownership (15%+) raised to 2x their projected
                        ownership (at most 95%); no file lineups

  Own candidates      : slot-first from the pool, weight projection^2.5, within
                        $300 of the cap, kept only inside the "blueprint" ranges
                        (what the top-1% lineups looked like on earlier nights of
                        the same slate size). On 8+ games the old draw instead:
                        projection^3, up to $2,000 left, 4,000 candidates, no ranges.
  File lineups        : every legal lineup of active players in every lineups file
                        dropped, picked by Stokastic's own Top 10% figure.
  Selection           : by projected points under one cap on any player (85% of a
                        150, 90% of a 20); a 150 drops anyone in fewer than 4
                        lineups for the set's most-used player at his salary.
  Not here            : the simulation, the bar, the win-rate score, duplicates,
                        neutral rules, tier caps, core floors, automatic pool adds,
                        stud floors, fades, any lean in the 150. Each was tested and
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
TOP3_K, TOP3_CAP, TOP3_MIN_OWN = 2.0, 0.95, 15.0    # the 20-max's top-3 lean
NEW_DRAW = dict(weight=2.5, left=300, n=2000, tries=40000, ranges=True)
OLD_DRAW = dict(weight=3.0, left=2000, n=4000, tries=80000, ranges=False)
STUD = 9000
SWAP_BAND = 500           # a re-target or tail swap stays within $500

HISTORY_DIR = os.environ.get("NBA_HISTORY") or os.path.expanduser(
    "~/Downloads/NBA Results 1/Main")
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def slate_group(games):
    return "2-4 games" if games <= 4 else ("5-7 games" if games <= 7 else "8+ games")


def plan(n, games, have_file):
    """-> (own lineups, file lineups, which draw, the rule in words)."""
    if n <= SMALL_SET:
        return n, 0, (OLD_DRAW if games >= 8 else NEW_DRAW), \
            f"20-max: {n} from your pool, top three owned to 2x"
    if games <= 4:
        return n, 0, NEW_DRAW, f"{games} games: all {n} from your pool"
    if not have_file:
        return n, 0, (OLD_DRAW if games >= 8 else NEW_DRAW), \
            f"{games} games: no Stokastic lineups file, so all {n} from your pool"
    if games <= 7:
        return 0, n, NEW_DRAW, f"{games} games: all {n} from Stokastic's lineups"
    own = n // 2
    return own, n - own, OLD_DRAW, \
        f"{games} games: {n - own} from Stokastic's lineups, {own} from your pool (old draw)"


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
    for path in glob.glob(os.path.join(folder, "NBA Stokastic Projections Main *.csv")):
        m = _DATE.search(os.path.basename(path))
        if not m:
            continue
        key = _season_key(int(m.group(1)), int(m.group(2)))
        if key >= today:
            continue
        date = f"{int(m.group(1))}-{int(m.group(2))}"
        if date in found and not os.path.basename(path).endswith(f"{date}.csv"):
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


def gen_candidates(allowed, draw, B, V, rng):
    """Slot-first sampling. -> list of candidates (players in DK slot order)."""
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
        for s in order:
            i = CLASSIC_SLOTS.index(s)
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


# --- selection and exposure ------------------------------------------------
def select(cands, n, max_exp, total, cnt, taken, key="proj", caps=None):
    """Greedy by `key` under a per-player cap (a share of `total`) and typed caps
    (counts). -> chosen candidates; `cnt` and `taken` are updated."""
    chosen = []
    for c in sorted(cands, key=lambda c: -c[key]):
        if len(chosen) >= n:
            break
        if c["key"] in taken:
            continue
        if any(cnt[p.dk_id] + 1 > max_exp * total for p in c["ps"]):
            continue
        if caps and any(cnt[p.dk_id] + 1 > caps[p.dk_id] for p in c["ps"] if p.dk_id in caps):
            continue
        chosen.append(c)
        taken.add(c["key"])
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


# --- the build ---------------------------------------------------------------
def build_set(players, n, games, field_lineups, inj, caps=None, floors=None, seed=0,
              history=None, slotting=None):
    """One set (a 150 or a 20). -> dict(rosters, sources, notes, meta)

    players: every player with DK ids attached, pool flags set and removals
    marked. caps / floors: {dk_id: lineups}. slotting: players -> slot order."""
    notes = []
    caps = dict(caps or {})
    floors = dict(floors or {})
    history = history if history is not None else load_history()
    active = [p for p in players if is_active(p, inj) and caps.get(p.dk_id, 1) > 0]
    by_id = {p.dk_id: p for p in players}
    active_ids = {p.dk_id for p in active}
    big = n > SMALL_SET
    fc = file_candidates(field_lineups or [], active_ids, slotting) if big else []
    n_own, n_file, draw, rule = plan(n, games, bool(fc))
    notes.append(rule)
    B = blueprint(history, games) if draw["ranges"] else None
    if draw["ranges"] and not B:
        notes.append("no earlier nights of this slate size saved, so no winners' ranges")
    V = _top_sets([(p.dk_id, p.salary, p.proj, p.boom) for p in active])
    rng = random.Random(seed)

    pool = [p for p in active if p.in_pool or p.dk_id in floors]
    sheet = any(p.in_pool for p in players)
    allowed = pool if sheet else active
    cands = []
    need_own = n_own > 0 or (big and len(fc) < n_file)
    if need_own or not big:
        cands = gen_candidates(allowed, draw, B, V, rng)
        if B and len(cands) < max(n, 100):
            notes.append(f"only {len(cands)} pool lineups fit the winners' ranges, "
                         f"so the ranges were dropped for tonight")
            cands = gen_candidates(allowed, dict(draw, ranges=False), None, V, rng)
        if sheet and len(cands) < 100:
            notes.append(f"your pool made only {len(cands)} legal lineups, so the "
                         f"whole slate was used")
            cands = gen_candidates(active, dict(draw, ranges=False), None, V, rng)

    cap = CAP_BIG if big else CAP_SMALL
    cnt, taken = collections.Counter(), set()
    own = select(cands, n_own, cap, n, cnt, taken, "proj", caps)
    half = select(fc, n_file, cap, n, cnt, taken, "top10", caps) if n_file else []
    chosen = own + half
    sources = ["mine"] * len(own) + ["vendor"] * len(half)
    if n_file and len(half) < n_file:
        more = select(cands, n - len(chosen), cap, n, cnt, taken, "proj", caps)
        if more:
            notes.append(f"Stokastic's file gave {len(half)} usable lineups, so "
                         f"{len(more)} more came from your pool")
        chosen += more
        sources += ["mine"] * len(more)
    if len(chosen) < n:
        more = select(cands, n - len(chosen), 1.0, n, cnt, taken, "proj", caps)
        if more:
            notes.append(f"{len(more)} lineup(s) only fit by going past the "
                         f"{cap:.0%} cap")
        chosen += more
        sources += ["mine"] * len(more)
    rosters = [list(c["ps"]) for c in chosen]

    targets = {}
    for k, v in floors.items():
        targets[k] = (min(v, len(rosters)), None)
    for k, v in caps.items():
        lo_, _ = targets.get(k, (None, None))
        targets[k] = (lo_, v)
    lean = []
    if not big and rosters:
        top3 = sorted(active, key=lambda p: -p.ownership)[:3]
        for p in top3:
            if p.ownership >= TOP3_MIN_OWN:
                want = int(round(min(TOP3_K * p.ownership / 100.0, TOP3_CAP) * len(rosters)))
                if p.dk_id in caps:
                    want = min(want, caps[p.dk_id])
                lo_, hi_ = targets.get(p.dk_id, (None, None))
                targets[p.dk_id] = (max(lo_ or 0, want), hi_)
                lean.append(f"{p.name} to {want} of {len(rosters)} "
                            f"({p.ownership:.0f}% projected owned)")
    cut = raised = 0
    if targets and rosters:
        cut, raised = retarget(rosters, targets, by_id)
    if lean:
        notes.append("Top three owned raised: " + "; ".join(lean))
    tail = 0
    if big and rosters:
        tail = tail_fix(rosters, by_id, MIN_LINEUPS, keep=set(floors), caps=caps)
    return {"rosters": rosters, "sources": sources, "notes": notes,
            "meta": {"rule": rule, "own": len(own), "file": len(half),
                     "fileUsable": len(fc), "candidates": len(cands),
                     "draw": "old" if draw is OLD_DRAW else "new",
                     "blueprint": {k: v for k, v in (B or {}).items()},
                     "cap": cap, "retarget": {"cut": cut, "raised": raised},
                     "tail": tail, "seed": seed}}


# --- what the screen says ---------------------------------------------------
def before_build(players, active, NM, hits, removed_names, inj):
    """Warnings before the build. None of them changes it."""
    W = []
    sheet = any(p.in_pool for p in players)
    if sheet:
        top5 = sorted(active, key=lambda p: -p.ownership)[:5]
        miss = [f"{p.name} ({p.ownership:.0f}%)" for p in top5 if not p.in_pool]
        if miss:
            W.append("Top-5 projected owned not in your pool: " + ", ".join(miss)
                     + ". The 150 can still get them from Stokastic's lineups on 5+ "
                       "games; your pool lineups and the 20-max cannot.")
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
