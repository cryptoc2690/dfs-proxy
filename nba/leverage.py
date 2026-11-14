"""G11, the single-stud leverage block (NBA Leverage Report 11-14, build list).

The low-owned plays that win can be named before lock for studs only. A named
stud is under 10% projected own, $8,500+, projected 15+, with at least one of:
  - regulars out: teammates averaging 15+ minutes (3+ games) who are out
    tonight, worth 30+ minutes, 4+ assists or 12+ shots a game between them;
  - scoring over the projection: his season average at or over tonight's
    projection, or his last five 3+ over it (4+ games), or he beat his
    projection on 60%+ of his earlier saved slates (4+ slates).
Named studs smashed (6x or over Stokastic's ceiling) 41% of the time against
19% for the other low-owned studs.

The pick: the named stud with the most reasons, of seven: regulars out; the top
ball-handler or scorer of his active teammates; season average at or over the
projection; last five 3+ over; the record; reached tonight's ceiling in 30%+ of
his games; owned 3+ under what his value and price usually draw. Ties go to the
largest gap between his scoring (the higher of season average and last five)
and tonight's projection.

The block (5+ game slates, the 150): 30 lineups built around him from the
typed pool (he is added if he is off it), picked by projection with every other
player capped at 85% of the block. They replace the 30 lineups of the built set
holding the most players the set has in 3 lineups or fewer ("the scattered 2%
positions"); ties come off the end of the pick order. Chalk is not touched.

Review numbers (12 nights): +1.07 top-1% lineups a night (2.65 -> 3.72),
top-0.1% 0.125 -> 0.236, best rank better on 5 nights and worse on 2, mean
lineup -0.9. On the nights the stud missed, the top was unchanged. The same
block around a low-owned stud with no reason did nothing. Ledger row G11.

This is the review's `Prototype/lv141.py`, `lv142.py`, `lv147.py` and the
harness's `rebuild` (shape 'anchor', pay 'dust') on the tool's own data.
"""

from __future__ import annotations

import collections
import csv
import io

from dk import normalize_name

MIN_MINS = 8.0
STUD = 8500
LOW_OWN = 10.0
MIN_PROJ = 15.0
REG_MPG = 15.0           # a regular: 15+ minutes a game over 3+ games
REG_GAMES = 3
OUT_MINS, OUT_AST, OUT_FGA = 30.0, 4.0, 12.0
FORM_GAMES = 4
HOT_OVER = 3.0
BEAT_SLATES, BEAT_SHARE = 4, 0.6
CEIL_SHARE = 0.3
UNDER_OWN = 3.0
EXP_MIN = 5              # the usual-ownership cell needs 5+ players
MIN_GAMES = 5            # 5+ game slates only
BLOCK = 30               # lineups in the 150
SHADOW_20 = 6            # the 20s: shadow only, 6 of the 20
# the block itself (the draw, the 85% cap, the scattered lineups it replaces) is
# newbuild.leverage_block / newbuild._dust_drop

LABELS = {"out": "regulars out", "carry": "top ball-handler or scorer",
          "avg": "season average at or over the projection",
          "hot": "last five 3+ over the projection",
          "beat": "beat his projection on 60%+ of his slates",
          "ceil": "reached tonight's ceiling in 30%+ of his games",
          "under": "owned 3+ under what his value and price usually draw"}


def ceilings(proj_text):
    """{normalised name: Stokastic's ceiling} from the projections file."""
    out = {}
    try:
        for r in csv.DictReader(io.StringIO((proj_text or "").lstrip("﻿"))):
            nm = r.get("Player") or r.get("Name") or ""
            try:
                c = float(r.get("Ceiling") or "")
            except ValueError:
                continue
            if nm:
                out[normalize_name(nm)] = c
    except csv.Error:
        pass
    return out


def _grp(g):
    return "2-4" if g <= 4 else "5-7" if g <= 7 else "8+"


def _cell(games, vrank, sal, proj):
    vb = 0 if vrank <= 3 else 1 if vrank <= 8 else 2 if vrank <= 15 else \
        3 if vrank <= 30 else 4 if vrank <= 60 else 5
    tier = 0 if sal < 4000 else 1 if sal < 5500 else 2 if sal < 7000 else 3 if sal < 8500 else 4
    pb = 0 if proj < 15 else 1 if proj < 25 else 2 if proj < 35 else 3
    return (_grp(games), vb, tier, pb)


def usual_own(history, before):
    """{cell: (sum of own, players)} over the saved nights before `before`: what a
    player's value rank, price and projection usually draw."""
    from matchup import night_date
    acc = collections.defaultdict(lambda: [0.0, 0])
    for h in history:
        try:
            if night_date(h["date"]) >= before:
                continue
        except (KeyError, ValueError):
            continue
        A = {k: v for k, v in h["P"].items() if v["proj"] > 0 and v["mins"] > 0
             and v["sal"] > 0 and v.get("injury", "") in ("None", "")}
        rank = {k: i + 1 for i, k in enumerate(sorted(A, key=lambda k: -A[k]["proj"] / A[k]["sal"]))}
        for k, v in A.items():
            c = acc[_cell(h["games"], rank[k], v["sal"], v["proj"])]
            c[0] += v["own"]
            c[1] += 1
    return acc


def _slates(history, before):
    """{normalised name: [(date, projection)]} on the saved nights before `before`."""
    from matchup import night_date
    out = collections.defaultdict(list)
    for h in history:
        try:
            d = night_date(h["date"])
        except (KeyError, ValueError):
            continue
        if d >= before:
            continue
        for k, v in h["P"].items():
            if v["proj"] > 0:
                out[k].append((d, v["proj"]))
    return out


def named_studs(players, active, logs, history, today, ceil, n_games, takeover_ids=()):
    """-> {"named": [dicts, best pick first], "pick": dict or None, "studs": n}
    players: everyone in tonight's file; active: tonight's buildable players."""
    import role as RL
    G = logs.get("games") or {}
    jk = RL._joiner(G)
    prior = {}

    def pr(p):
        k = p.dk_id
        if k not in prior:
            prior[k] = [g for g in G.get(jk(p), []) if g["mins"] >= MIN_MINS and g["date"] < today]
        return prior[k]

    act_ids = {p.dk_id for p in active}
    # who is out, by team: in tonight's file, not active, 15+ minutes over 3+ games
    outs = collections.defaultdict(list)
    for p in players:
        if p.dk_id in act_ids or not p.team:
            continue
        L = pr(p)
        if len(L) < REG_GAMES:
            continue
        g = len(L)
        mpg = sum(x["mins"] for x in L) / g
        if mpg < REG_MPG:
            continue
        outs[p.team].append({"name": p.name, "mpg": mpg,
                             "ast": sum(x.get("ast") or 0 for x in L) / g,
                             "fga": sum(x.get("fga") or 0 for x in L) / g})
    # the active teammates ranked by assists and points a game
    ar, sr = {}, {}
    byteam = collections.defaultdict(list)
    for p in active:
        L = pr(p)
        if L:
            byteam[p.team].append((p.dk_id, sum(x.get("ast") or 0 for x in L) / len(L),
                                   sum(x.get("pts") or 0 for x in L) / len(L)))
    for t, L in byteam.items():
        for i, (k, _a, _s) in enumerate(sorted(L, key=lambda x: -x[1])):
            ar[k] = i + 1
        for i, (k, _a, _s) in enumerate(sorted(L, key=lambda x: -x[2])):
            sr[k] = i + 1
    exp = usual_own(history, today)
    rank = {p.dk_id: i + 1 for i, p in enumerate(
        sorted([p for p in active if p.salary > 0 and p.proj > 0], key=lambda p: -p.proj / p.salary))}
    sl = _slates(history, today)
    out, studs = [], 0
    for p in active:
        if not (p.ownership < LOW_OWN and p.salary >= STUD and p.proj >= MIN_PROJ):
            continue
        studs += 1
        L = pr(p)
        g = len(L)
        dkg = sum(x["dk"] for x in L) / g if g else None
        dk5 = sum(x["dk"] for x in L[-5:]) / len(L[-5:]) if g else None
        c = ceil.get(normalize_name(p.name))
        played = {x["date"]: x["dk"] for x in L}
        slates = [(d, pj) for d, pj in sl.get(normalize_name(p.name), []) if d in played]
        beat = sum(1 for d, pj in slates if played[d] >= pj)
        O = outs.get(p.team, [])
        mo, ao, fo = sum(o["mpg"] for o in O), sum(o["ast"] for o in O), sum(o["fga"] for o in O)
        cell = exp.get(_cell(n_games, rank.get(p.dk_id, 99), p.salary, p.proj))
        e_own = (cell[0] / cell[1]) if cell and cell[1] >= EXP_MIN else None
        R = {"out": mo >= OUT_MINS or ao >= OUT_AST or fo >= OUT_FGA,
             "carry": ar.get(p.dk_id) == 1 or sr.get(p.dk_id) == 1,
             "avg": bool(g >= FORM_GAMES and dkg >= p.proj),
             "hot": bool(g >= FORM_GAMES and dk5 >= p.proj + HOT_OVER),
             "beat": bool(len(slates) >= BEAT_SLATES and beat / len(slates) >= BEAT_SHARE),
             "ceil": bool(g >= FORM_GAMES and c and sum(1 for x in L if x["dk"] >= c) / g >= CEIL_SHARE),
             "under": bool(e_own is not None and p.ownership <= e_own - UNDER_OWN)}
        form = R["avg"] or R["hot"] or R["beat"]
        if not (R["out"] or form):
            continue
        hotm = (max(dk5, dkg) - p.proj) if g >= FORM_GAMES else -99.0
        out.append({"name": p.name, "id": p.dk_id, "team": p.team, "salary": p.salary,
                    "own": round(p.ownership, 1), "proj": round(p.proj, 1),
                    "inPool": bool(p.in_pool), "reasons": [k for k in LABELS if R[k]],
                    "nReasons": sum(1 for k in LABELS if R[k]), "hot": round(hotm, 1),
                    "games": g, "season": None if dkg is None else round(dkg, 1),
                    "last5": None if dk5 is None else round(dk5, 1),
                    "record": f"{beat} of {len(slates)}", "usualOwn": None if e_own is None else round(e_own, 1),
                    "outs": [f"{o['name']} ({o['mpg']:.0f} min, {o['ast']:.1f} ast, {o['fga']:.1f} shots)"
                             for o in sorted(O, key=lambda o: -o["mpg"])],
                    "takeover": p.dk_id in set(takeover_ids)})
    out.sort(key=lambda r: (-r["nReasons"], -r["hot"]))
    return {"named": out, "pick": out[0] if out else None, "studs": studs}


def why(r):
    return ", ".join(LABELS[k] for k in r["reasons"])
