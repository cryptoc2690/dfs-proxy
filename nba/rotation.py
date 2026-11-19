"""The rotation record (Rotation Read report, Nov 19, section 9): every game's
substitutions from ESPN's play-by-play, who was on the floor every second, the
closers and the centers' overlap. Ported from the prototype (`Prototype/rr201.py`
to `rr203.py`), which matched the box-score minutes within one minute on 98% of
player-games.

Saved the morning after, one file a day: logs/espn/rot_YYYYMMDD.json, each game
with its starters, every substitution (period, seconds left, team, in, out), the
stints, each player's seconds (all, regulation, overtime, the last 5:00 of the
4th), the margin with 5:00 left, and for each team the centers' seconds, the
seconds two centers shared and the seconds with no center. ESPN's team rosters
(position, height) are saved weekly: logs/espn/rosters_YYYYMMDD.json.

Data only. Nothing in the build reads it; the center split is logged (shadow).
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import os
import threading
import time

import role as RL

ESPN_DIR = RL.ESPN_DIR
ROT_VERSION = 1
TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"
ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{}/roster"
ROSTER_EVERY_DAYS = 7
SPLIT_SHARED = 0.10            # two centers who shared under 10% of the smaller's minutes
SKIP = ("Technical", "Ejection", "Coach", "Delay", "Timeout", "Challenge",
        "Instant Replay", "Review")
_lock = threading.Lock()


def _ab(a):
    return RL.ABBR.get(a, a)


def _clock(s):
    s = str(s or "0")
    if ":" in s:
        m, _, x = s.partition(":")
        return int(m) * 60 + float(x)
    try:
        return float(s)
    except ValueError:
        return 0.0


def _plen(p):
    return 720 if p <= 4 else 300


def _t0(p):
    return 720 * (p - 1) if p <= 4 else 2880 + 300 * (p - 5)


def compact(summary):
    """The parts of an ESPN game summary the rotation needs."""
    comp = summary["header"]["competitions"][0]
    tid, home = {}, None
    for c in comp["competitors"]:
        tid[c["team"]["id"]] = _ab(c["team"]["abbreviation"])
        if c.get("homeAway") == "home":
            home = _ab(c["team"]["abbreviation"])
    players = {}
    for t in (summary.get("boxscore") or {}).get("players") or []:
        team = _ab(t["team"]["abbreviation"])
        for sec in t.get("statistics") or []:
            labels = sec.get("names") or sec.get("labels") or []
            for a in sec.get("athletes") or []:
                A = a["athlete"]
                st = dict(zip(labels, a.get("stats") or []))
                players[A["id"]] = {"name": A["displayName"],
                                    "key": RL.normalize_name(A["displayName"]), "team": team,
                                    "pos": (A.get("position") or {}).get("abbreviation"),
                                    "starter": bool(a.get("starter")),
                                    "min": RL._num(st.get("MIN")) if a.get("stats") else 0.0}
    subs, plays, score = [], [], []
    for x in summary.get("plays") or []:
        per = (x.get("period") or {}).get("number") or 0
        cl = _clock((x.get("clock") or {}).get("displayValue"))
        team = tid.get((x.get("team") or {}).get("id"))
        parts = [p["athlete"]["id"] for p in (x.get("participants") or []) if p.get("athlete")]
        typ = (x.get("type") or {}).get("text") or ""
        seq = int(x.get("sequenceNumber") or 0)
        if typ == "Substitution" and len(parts) == 2:
            subs.append({"p": per, "c": cl, "team": team, "i": parts[0], "o": parts[1], "seq": seq})
        elif parts:
            plays.append({"p": per, "c": cl, "a": parts, "t": typ, "seq": seq})
        if x.get("scoringPlay"):
            score.append((per, cl, x.get("homeScore"), x.get("awayScore")))
    return {"teams": sorted(tid.values()), "home": home, "players": players,
            "subs": subs, "plays": plays, "score": score,
            "periods": max([4] + [s["p"] for s in subs] + [x["p"] for x in plays])}


def build(g):
    """Who was on the floor every second (the prototype's rr202.build)."""
    P = g["players"]
    nper = g["periods"]
    out = {}
    for T in g["teams"]:
        ids = {i for i, v in P.items() if v["team"] == T}
        played = {i for i in ids if P[i]["min"] > 0}
        subs = sorted((s for s in g["subs"] if s["team"] == T), key=lambda s: s["seq"])
        sec = collections.Counter()
        stints = []
        lineup = {i for i in ids if P[i]["starter"]}
        for p in range(1, nper + 1):
            ps = [s for s in subs if s["p"] == p]
            pl = [x for x in g["plays"] if x["p"] == p and not any(k in x["t"] for k in SKIP)]
            first_in, first_out, first_play = {}, {}, {}
            for s in ps:
                first_in.setdefault(s["i"], (s["c"], s["seq"]))
                first_out.setdefault(s["o"], (s["c"], s["seq"]))
            for x in pl:
                for a in x["a"]:
                    if a in ids and (a not in first_play or x["c"] > first_play[a][0]):
                        first_play[a] = (x["c"], x["seq"])

            def started(a):
                fi, fo, fp = first_in.get(a), first_out.get(a), first_play.get(a)
                if fo and (fi is None or fo[0] > fi[0] or (fo[0] == fi[0] and fo[1] < fi[1])):
                    return 2
                if fp and (fi is None or fp[0] > fi[0]):
                    return 1
                return 0
            if p == 1 and len(lineup) == 5:
                start = set(lineup)
            else:
                ev = {a: started(a) for a in ids}
                start = {a for a, v in ev.items() if v}
                if len(start) > 5:
                    start = set(sorted(start, key=lambda a: (-ev[a], -(P[a]["min"] * 60 - sec[a])))[:5])
                if len(start) < 5:
                    cand = ([a for a in lineup if a not in start and a not in first_in]
                            or [a for a in played if a not in start and a not in first_in])
                    cand = sorted(cand, key=lambda a: -(P[a]["min"] * 60 - sec[a]))
                    start |= set(cand[:5 - len(start)])
            lineup = set(start)
            t = _t0(p)
            cur = _plen(p)
            for s in ps:
                if s["c"] < cur:
                    for a in lineup:
                        sec[a] += cur - s["c"]
                    stints.append([t + _plen(p) - cur, t + _plen(p) - s["c"], sorted(lineup)])
                    cur = s["c"]
                lineup.discard(s["o"])
                lineup.add(s["i"])
                if len(lineup) > 5:
                    drop = sorted(lineup - {s["i"]}, key=lambda a: P[a]["min"] * 60 - sec[a])[0]
                    lineup.discard(drop)
                if len(lineup) < 5:
                    cand = sorted((x for x in played if x not in lineup and x != s["o"]),
                                  key=lambda x: -(P[x]["min"] * 60 - sec[x]))
                    if cand:
                        lineup.add(cand[0])
            if cur > 0:
                for a in lineup:
                    sec[a] += cur
                stints.append([t + _plen(p) - cur, t + _plen(p), sorted(lineup)])
        out[T] = {"stints": stints, "sec": dict(sec)}
    return out


def game_record(gid, summary):
    """-> the saved record of one game, keyed by player name keys."""
    g = compact(summary)
    F = build(g)
    P = g["players"]
    key = {i: v["key"] for i, v in P.items()}
    m5, mreg = None, None
    for per, cl, h, a in g["score"]:
        if per < 4 or (per == 4 and cl >= 300):
            m5 = (h, a)
        if per <= 4:
            mreg = (h, a)
    home = g["home"]
    away = next((t for t in g["teams"] if t != home), None)
    rec = {"id": gid, "teams": g["teams"], "home": home, "periods": g["periods"],
           "close": bool(m5 and abs((m5[0] or 0) - (m5[1] or 0)) <= 10),
           "m5": {home: ((m5[0] or 0) - (m5[1] or 0)) if m5 else 0,
                  away: ((m5[1] or 0) - (m5[0] or 0)) if m5 else 0},
           "subs": [[s["p"], s["c"], s["team"], key.get(s["i"]), key.get(s["o"])] for s in g["subs"]],
           "team": {}}
    for T in g["teams"]:
        stints = F[T]["stints"]
        ids = [i for i, v in P.items() if v["team"] == T]
        pl = {}
        for i in ids:
            v = P[i]
            q = collections.Counter()
            l5 = 0.0
            for s0, s1, L in stints:
                if i in L:
                    for p in range(1, g["periods"] + 1):
                        lo, hi = max(s0, _t0(p)), min(s1, _t0(p) + _plen(p))
                        if hi > lo:
                            q[p] += hi - lo
                    lo, hi = max(s0, 2880 - 300), min(s1, 2880)
                    if hi > lo:
                        l5 += hi - lo
            pl[v["key"]] = {"name": v["name"], "pos": v["pos"], "starter": v["starter"],
                            "min": v["min"], "sec": round(F[T]["sec"].get(i, 0.0), 1),
                            "secReg": round(sum(x for p, x in q.items() if p <= 4), 1),
                            "secOT": round(sum(x for p, x in q.items() if p > 4), 1),
                            "last5": round(l5, 1)}
        cs = [i for i in ids if P[i]["pos"] == "C" and P[i]["min"] > 0]
        csec, pair = collections.Counter(), collections.Counter()
        none = two = 0.0
        for s0, s1, L in stints:
            on = [i for i in L if i in cs]
            d = s1 - s0
            if not on:
                none += d
            if len(on) >= 2:
                two += d
            for i in on:
                csec[key[i]] += d
            for a in on:
                for b in on:
                    if key[a] < key[b]:
                        pair[f"{key[a]}|{key[b]}"] += d
        rec["team"][T] = {"starters": sorted(key[i] for i in ids if P[i]["starter"]),
                          "players": pl,
                          "stints": [[round(a, 1), round(b, 1), [key[i] for i in L]]
                                     for a, b, L in stints],
                          "centers": {"sec": {k: round(v, 1) for k, v in csec.items()},
                                      "pair": {k: round(v, 1) for k, v in pair.items()},
                                      "none": round(none, 1), "two": round(two, 1)}}
    return rec


# --- saving -------------------------------------------------------------------
def _rot_path(d):
    return os.path.join(ESPN_DIR, f"rot_{d:%Y%m%d}.json")


def sync(today=None, budget_s=600):
    """For every saved box-score day before `today` without a rotation file, fetch
    its games' summaries and save the rotation record. -> {"days", "fetched", "error"}"""
    today = today or dt.date.today()
    t0 = time.time()
    out = {"days": 0, "fetched": 0, "error": None}
    if not RL.FETCH or not _lock.acquire(timeout=5):
        return out
    try:
        names = sorted(os.listdir(ESPN_DIR)) if os.path.isdir(ESPN_DIR) else []
        for nm in names:
            if not (nm.startswith("day_") and nm.endswith(".json")):
                continue
            try:
                d = dt.datetime.strptime(nm[4:12], "%Y%m%d").date()
            except ValueError:
                continue
            if d >= today:
                continue
            if os.path.exists(_rot_path(d)):
                out["days"] += 1
                continue
            if time.time() - t0 > budget_s:
                break
            try:
                with open(os.path.join(ESPN_DIR, nm), encoding="utf-8") as fh:
                    day = json.load(fh)
                games = []
                for g in day.get("games") or []:
                    games.append(game_record(g["id"], RL._get(RL.SUMMARY.format(g["id"]))))
                    time.sleep(0.15)
                with open(_rot_path(d) + ".tmp", "w", encoding="utf-8") as fh:
                    json.dump({"date": f"{d:%Y-%m-%d}", "v": ROT_VERSION, "games": games}, fh)
                os.replace(_rot_path(d) + ".tmp", _rot_path(d))
                out["days"] += 1
                out["fetched"] += 1
            except Exception as exc:                      # noqa: BLE001
                out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        _lock.release()
    return out


def sync_rosters(today=None):
    """ESPN's team rosters (position, height, weight), at most once a week."""
    today = today or dt.date.today()
    if not RL.FETCH:
        return None
    try:
        have = sorted(n for n in os.listdir(ESPN_DIR) if n.startswith("rosters_"))
    except OSError:
        have = []
    if have:
        try:
            last = dt.datetime.strptime(have[-1][8:16], "%Y%m%d").date()
            if (today - last).days < ROSTER_EVERY_DAYS:
                return None
        except ValueError:
            pass
    try:
        J = RL._get(TEAMS_URL)
        teams = [t["team"] for t in J["sports"][0]["leagues"][0]["teams"]]
        out = {}
        for t in teams:
            R = RL._get(ROSTER_URL.format(t["id"]))
            for a in R.get("athletes") or []:
                out[RL.normalize_name(a.get("displayName") or "")] = {
                    "name": a.get("displayName"), "team": _ab(t["abbreviation"]),
                    "pos": (a.get("position") or {}).get("abbreviation"),
                    "height": a.get("height"), "weight": a.get("weight")}
            time.sleep(0.15)
        path = os.path.join(ESPN_DIR, f"rosters_{today:%Y%m%d}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"date": today.isoformat(), "players": out}, fh)
        return path
    except Exception:                                     # noqa: BLE001
        return None


def warm():
    """Fill the rotation files and the weekly rosters in the background."""
    def go():
        RL.sync(budget_s=600)
        sync()
        sync_rosters()
    threading.Thread(target=go, daemon=True).start()


# --- reading ------------------------------------------------------------------
def load(today=None):
    """[(date, game record)] for every saved rotation day before `today`, oldest first."""
    today = today or dt.date.today()
    out = []
    try:
        names = sorted(os.listdir(ESPN_DIR))
    except OSError:
        return out
    for nm in names:
        if not (nm.startswith("rot_") and nm.endswith(".json")):
            continue
        try:
            d = dt.datetime.strptime(nm[4:12], "%Y%m%d").date()
            if d >= today:
                continue
            with open(os.path.join(ESPN_DIR, nm), encoding="utf-8") as fh:
                for g in json.load(fh).get("games") or []:
                    out.append((d, g))
        except (OSError, ValueError):
            continue
    return out


def center_splits(outs, active, jk, today=None):
    """The center split (shadow log): a regular center out tonight, and his team's
    other center (the one with the most minutes in the team's last 8 games who is
    active tonight). -> [{team, out, backup, shared, split, games}]; split is True
    when they shared under 10% of the smaller man's minutes."""
    games = load(today)
    if not games:
        return []
    act = {jk(p): p for p in active}
    res = []
    for T, regs in outs.items():
        tg = [g["team"][T] for _d, g in games if T in g.get("team", {})]
        if not tg:
            continue
        for r in regs:
            pos = next((g["players"][r["key"]]["pos"] for g in reversed(tg)
                        if r["key"] in g["players"]), None)
            if pos != "C":
                continue
            mins = collections.Counter()
            for g in tg[-8:]:
                for k, v in g["players"].items():
                    if k != r["key"] and v.get("pos") == "C" and k in act:
                        mins[k] += v.get("sec") or 0
            if not mins:
                continue
            X = mins.most_common(1)[0][0]
            both = sx = sr = 0.0
            n = 0
            pk = "|".join(sorted((X, r["key"])))
            for g in tg:
                c = g["centers"]["sec"]
                if X in c and r["key"] in c:
                    n += 1
                    both += g["centers"]["pair"].get(pk, 0.0)
                    sx += c[X]
                    sr += c[r["key"]]
            shared = both / min(sx, sr) if n and min(sx, sr) > 0 else None
            p = act[X]
            res.append({"team": T, "out": r["name"], "backup": p.name, "salary": p.salary,
                        "own": round(p.ownership, 1), "proj": round(p.proj, 1),
                        "mins": round(float(p.minutes or 0), 1), "gamesTogether": n,
                        "shared": None if shared is None else round(shared, 3),
                        "split": bool(shared is not None and shared < SPLIT_SHARED)})
    return res
