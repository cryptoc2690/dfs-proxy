"""Box-score reads before lock: the Box Score Read, Rotation Read and Sim Chalk
reports (Nov 19, `~/Downloads/NBA Results 1/`). Ported from the prototype
(`Prototype/bs301.py`, `bs307.py`, `rr203.py`, `rr209.py`, `sc40x.py`).

Every read is a number from his games BEFORE tonight (role.py's ESPN logs):

  A  last-3 average      the mean minutes of his last three games
  C  trend               his last three climbed (m1 <= m2 <= m3, m3 - m1 >= 6):
                         the last game's minutes
  D  his starts          Stokastic has him starting and he has started before:
                         the mean minutes of his last five starts
  E  same teammates out  his team has a regular out tonight and played earlier
                         games with the same regulars out (all of tonight's
                         outs, else the highest-minute one): his mean minutes in
                         the last three such games he played

A regular: 3+ games, 20+ minutes a game over his last 10 played, his latest game
for this team, and in one of the team's last 8 games. He is OUT tonight when he
is not an active player in tonight's file (missing, projected 0, or ruled out).

What the build uses (the running build list):
  G20  part-time starter floor: Stokastic has him starting, a regular is out on
       his team, he started under 60% of his last five, he has started before,
       projected own 5-20% -> floored at 2x his own (150 and 20s)
  G21  owned bench player, role unchanged: Stokastic has him on the bench,
       projected 10+, own 10%+, 3+ games, Stokastic's minutes under his last-3
       average + 3, and no read (A, C, E) 3+ over Stokastic's -> left out
Shown only: G23 (players at 5%+ own not in the pool, with their box-score
line) and G24 (owned low-minute chalk). The rest is shadows and logs.
"""

from __future__ import annotations

import collections
import datetime as dt
import statistics

from dk import normalize_name

REG_MINS, REG_GAMES, REG_RECENT = 20.0, 3, 8
OVER = 3.0                       # a read is "over" at 3+ minutes above Stokastic's
G20_LO, G20_HI, G20_K, G20_RATE = 5.0, 20.0, 2.0, 0.6
G21_OWN, G21_PROJ, G21_GAMES = 10.0, 10.0, 3
G23_OWN = 5.0
G24_SAL, G24_MINS, G24_OWN = 5500, 24.0, 15.0
FIRST_START_GAMES, FIRST_START_LATE = 5, 31   # minutes after the build
SHADOW_K = 1.5                   # the Sensabaugh and points-in-starts floors


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def _r(v, k=1):
    return None if v is None else round(v, k)


def _tip(p):
    s = getattr(p, "start", "") or ""
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()      # a bare time is this computer's clock


def team_outs(active, logs, jk):
    """{team: [{key, name, avg10}]} the regulars out tonight, most minutes first."""
    G, TD = logs["games"], logs["team_dates"]
    in_file = {jk(p) for p in active}
    teams = {p.team for p in active if p.team}
    out = {}
    for t in teams:
        recent = set((TD.get(t) or [])[-REG_RECENT:])
        regs = []
        for k, H in G.items():
            if not H or H[-1]["team"] != t:
                continue
            if not any(x["team"] == t and x["date"] in recent for x in H):
                continue
            l10 = H[-10:]
            avg10 = _mean(x["mins"] for x in l10)
            if len(H) >= REG_GAMES and avg10 and avg10 >= REG_MINS and k not in in_file:
                regs.append({"key": k, "name": H[-1]["name"], "avg10": round(avg10, 1)})
        out[t] = sorted(regs, key=lambda r: -r["avg10"])
    return out


def reads(active, logs, jk, outs):
    """{dk_id: reads} for every active player with a game on record."""
    G, TD = logs["games"], logs["team_dates"]
    same_dates = {}
    for t, regs in outs.items():
        if not regs:
            same_dates[t] = []
            continue
        dates = TD.get(t) or []

        def missed(d, k):
            return not any(x["date"] == d and x["team"] == t for x in G.get(k, []))
        allout = [d for d in dates if all(missed(d, r["key"]) for r in regs)]
        top = [d for d in dates if missed(d, regs[0]["key"])]
        same_dates[t] = allout or top
    R = {}
    for p in active:
        H = G.get(jk(p), [])
        if not H:
            continue
        mins = float(p.minutes or 0)
        l3, l5, l10 = H[-3:], H[-5:], H[-10:]
        m3 = [x["mins"] for x in l3]
        rA = _mean(m3) if len(H) >= 3 else None
        rC = m3[2] if (len(m3) == 3 and m3[0] <= m3[1] <= m3[2] and m3[2] - m3[0] >= 6) else None
        st_ = [x for x in H if x["starter"]]
        be = [x for x in H if not x["starter"]]
        rD = _mean(x["mins"] for x in st_[-5:]) if (p.starting is True and st_) else None
        sd = set(same_dates.get(p.team) or [])
        sg = [x for x in H if x["date"] in sd and x["team"] == p.team][-3:]
        rE = _mean(x["mins"] for x in sg) if sg else None
        tm = sum(x["mins"] for x in l10)
        R[p.dk_id] = {
            "name": p.name, "team": p.team, "salary": p.salary, "own": round(p.ownership, 1),
            "proj": round(p.proj, 1), "mins": round(mins, 1), "starting": p.starting,
            "games": len(H), "starts": len(st_),
            "startRate5": round(sum(1 for x in l5 if x["starter"]) / len(l5), 2) if l5 else None,
            "rA": _r(rA), "rC": _r(rC), "rD": _r(rD), "rE": _r(rE), "nSame": len(sg),
            "last3": m3, "last3dk": [x["dk"] for x in l3],
            "minStarts": _r(_mean(x["mins"] for x in st_[-5:])),
            "dkStarts": _r(_mean(x["dk"] for x in st_[-5:])),
            "minBench": _r(_mean(x["mins"] for x in be[-5:])),
            "dkBench": _r(_mean(x["dk"] for x in be[-5:])),
            "sameDk": _r(_mean(x["dk"] for x in sg)),
            "avg10": _r(_mean(x["mins"] for x in l10)),
            "best10": max((x["dk"] for x in l10), default=None),
            "dkpm10": round(sum(x["dk"] for x in l10) / tm, 2) if tm else None,
            "regularOut": [r["name"] for r in outs.get(p.team) or []]}
    return R


def _over(r, k):
    return r.get(k) is not None and r[k] - r["mins"] >= OVER


def any_over(r):
    """A box-score minutes read (A, C, E) 3+ over Stokastic's minutes."""
    return r["mins"] > 0 and (_over(r, "rA") or _over(r, "rC") or _over(r, "rE"))


def g20(active, R):
    """The part-time starter floor: [player]."""
    return [p for p in active if p.dk_id in R and p.starting is True
            and R[p.dk_id]["regularOut"] and R[p.dk_id]["starts"] >= 1
            and (R[p.dk_id]["startRate5"] or 0) < G20_RATE
            and G20_LO <= p.ownership < G20_HI]


def g21(active, R):
    """The owned bench player whose role has not changed: [player]."""
    out = []
    for p in active:
        r = R.get(p.dk_id)
        if not r or p.starting is not False or p.proj < G21_PROJ or p.ownership < G21_OWN:
            continue
        if r["games"] < G21_GAMES or r["rA"] is None or not r["mins"]:
            continue
        if any_over(r) or r["mins"] - r["rA"] >= OVER:
            continue
        out.append(p)
    return out


def sensabaugh(active, R):
    """Shadow: an owned bench player (10%+) with a minutes read 3+ over."""
    return [p for p in active if p.dk_id in R and p.starting is False
            and p.proj >= G21_PROJ and p.ownership >= G21_OWN
            and R[p.dk_id]["games"] >= G21_GAMES and any_over(R[p.dk_id])]


def points_in_starts(active, R):
    """Shadow: starting tonight, his DK in his last five starts 5+ over tonight's projection."""
    return [p for p in active if p.dk_id in R and p.starting is True
            and R[p.dk_id]["dkStarts"] is not None and R[p.dk_id]["dkStarts"] >= p.proj + 5]


def first_starts(active, R, now=None):
    """Stokastic has him starting, he has never started (5+ games), and his game
    tips 31+ minutes from now (the lineup is not out yet). -> [(player, minutes to tip)]"""
    now = now or dt.datetime.now().astimezone()
    out = []
    for p in active:
        r = R.get(p.dk_id)
        if not r or p.starting is not True or r["starts"] or r["games"] < FIRST_START_GAMES:
            continue
        t = _tip(p)
        mins = None if t is None else (t - now).total_seconds() / 60
        if mins is None or mins >= FIRST_START_LATE:
            out.append((p, None if mins is None else round(mins)))
    return out


def low_minute_chalk(active):
    """G24: under $5,500, projected 24 minutes or under, owned (15%+, or one of the
    three most-owned under $5,000). -> [player], most owned first"""
    cheap3 = {p.dk_id for p in sorted((q for q in active if q.salary < 5000),
                                       key=lambda q: -q.ownership)[:3]}
    return sorted((p for p in active if p.salary < G24_SAL and 0 < (p.minutes or 0) <= G24_MINS
                   and (p.ownership >= G24_OWN or p.dk_id in cheap3)),
                  key=lambda p: -p.ownership)


def is_center(p):
    return "C" in (p.eligible or set())


def line(p, r):
    """His box-score line in words."""
    if not r:
        return "no game on record"
    bits = [f"{'starting' if p.starting else 'bench' if p.starting is False else '?'}, "
            f"Stokastic {r['mins']:.0f} min"]
    if r["starts"]:
        bits.append(f"{r['starts']} start{'s' if r['starts'] != 1 else ''} this season "
                    f"({r['minStarts']:.0f} min, {r['dkStarts']:.1f} DK)")
    else:
        bits.append("no starts this season")
    bits.append("last 3: " + ", ".join(f"{m:.0f}" for m in r["last3"]) + " min, "
                + ", ".join(f"{d:.1f}" for d in r["last3dk"]) + " DK")
    if r["rE"] is not None:
        bits.append(f"with the same teammates out: {r['rE']:.0f} min, {r['sameDk']:.1f} DK "
                    f"({r['nSame']} game{'s' if r['nSame'] != 1 else ''})")
    return "; ".join(bits)


def night(players, active, logs, jk):
    """Every read, list and mark for tonight. -> dict"""
    outs = team_outs(active, logs, jk)
    R = reads(active, logs, jk, outs)
    return {"outs": outs, "reads": R, "g20": g20(active, R), "g21": g21(active, R),
            "sensabaugh": sensabaugh(active, R), "pointsInStarts": points_in_starts(active, R),
            "firstStarts": first_starts(active, R), "lowMinuteChalk": low_minute_chalk(active)}


def tip_waves(active, now=None):
    """The later tip waves and when to pull a fresh Stokastic file for each (G22):
    [{"tip", "pullBy", "teams"}], tips 30+ minutes from now, 20 minutes before each."""
    now = now or dt.datetime.now().astimezone()
    tips = collections.defaultdict(set)
    for p in active:
        t = _tip(p)
        if t is not None and p.team:
            tips[t].add(p.team)
    out = []
    for t in sorted(tips):
        if (t - now).total_seconds() >= 30 * 60:
            pb = t - dt.timedelta(minutes=20)
            out.append({"tip": t.astimezone().isoformat(timespec="minutes"),
                        "pullBy": pb.astimezone().isoformat(timespec="minutes"),
                        "teams": sorted(tips[t])})
    return out


def clock(iso):
    try:
        t = dt.datetime.fromisoformat(iso).astimezone()
    except (TypeError, ValueError):
        return "?"
    return t.strftime("%-I:%M%p").lower()


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None
