"""Role marks from real games: the Weak Chalk Rebuild report (Nov 13).

The report found one thing the projections file cannot show: chalk whose role
is shaky. It is a player at 20%+ projected own with NO role change tonight
(no takeover mark, no minutes lift over his real last five games, no new
starting job) who

  * comes off the bench ("the coach doesn't have to go to him"), or
  * has a teammate at his position back tonight ("someone else can take the
    minutes"), or
  * is projected under 22 minutes ("the minutes aren't guaranteed").

On 22 nights those 31 calls reached projection 19% of the time against 49% for
the other chalk, and leaving them out of the pick put more lineups in the top 1%
on every 2-4 game night. Three of the marks need real minutes and starts, so
this module keeps ESPN's public box scores, one small file a day, in
logs/espn/day_YYYYMMDD.json. A finished day never changes, so it is fetched once.

It also names the "Bones type" (logged, never built on): a cheap player in a
small role who has smashed in that role before. 1.1+ DK points a minute and two
or more earlier games of 6x his salary in under 24 minutes.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request

from dk import normalize_name

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
ESPN_DIR = os.path.join(LOG_DIR, "espn")
SEASON_START = dt.date(2026, 10, 20)
FETCH = True                 # smoke.py turns this off: no network in the tests
BUDGET_S = 120               # one sync never runs longer than this

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={}"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={}"
ABBR = {"UTAH": "UTA", "NY": "NYK", "GS": "GSW", "SA": "SAS", "NO": "NOP", "WSH": "WAS"}

# The rule as the report wrote it (section 7, build item 1).
CHALK_OWN = 20.0             # projected own, %
UNDER_MINS = 22.0            # "projected under 22 minutes"
LIFT_MINS = 5.0              # a lift: projected 5+ minutes over his real last five
BACK_PROJ = 10.0             # a teammate who counts as "back" is projected 10+
NEW_START_MAX = 1            # a new starting job: started 1 or fewer of his last 5 ...
NEW_START_MIN_GAMES = 3      # ... with at least 3 games on record
# The Bones type (section 3).
BONES_SAL, BONES_MINS, BONES_PPM, BONES_6X, BONES_GAMES = 5000, 24.0, 1.1, 2, 5

_lock = threading.Lock()


# --- fetching ---------------------------------------------------------------
def _get(url, timeout=20):
    """JSON from ESPN. Python from python.org on a Mac ships without the system's
    certificates, so a certificate failure falls back to curl, which has them."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        if "CERTIFICATE" not in str(exc).upper():
            raise
    out = subprocess.run(["curl", "-s", "-f", "-m", str(timeout), url],
                         capture_output=True, check=True)
    return json.loads(out.stdout.decode("utf-8"))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def dk_points(st):
    """DK classic scoring from one ESPN box-score line {label: value}."""
    three = st.get("3PT", "0-0")
    threes = _num(three.split("-")[0]) if "-" in str(three) else 0.0
    pts, reb, ast = _num(st.get("PTS")), _num(st.get("REB")), _num(st.get("AST"))
    stl, blk, to = _num(st.get("STL")), _num(st.get("BLK")), _num(st.get("TO"))
    tens = sum(1 for v in (pts, reb, ast, stl, blk) if v >= 10)
    bonus = 3.0 if tens >= 3 else (1.5 if tens >= 2 else 0.0)
    return pts + 0.5 * threes + 1.25 * reb + 1.5 * ast + 2 * stl + 2 * blk - 0.5 * to + bonus


def box_rows(summary):
    """-> [{team, key, name, mins, dk, starter, dnp}] from one ESPN game summary."""
    out = []
    for t in (summary.get("boxscore") or {}).get("players") or []:
        ab = t["team"]["abbreviation"]
        ab = ABBR.get(ab, ab)
        for sec in t.get("statistics") or []:
            labels = sec.get("labels") or []
            for a in sec.get("athletes") or []:
                nm = a["athlete"]["displayName"]
                row = {"team": ab, "key": normalize_name(nm), "name": nm,
                       "starter": bool(a.get("starter"))}
                if a.get("didNotPlay") or not a.get("stats"):
                    row.update(mins=0.0, dk=0.0, dnp=True)
                else:
                    st = dict(zip(labels, a["stats"]))
                    row.update(mins=_num(st.get("MIN")), dk=round(dk_points(st), 2),
                               dnp=False)
                out.append(row)
    return out


def _day_path(d):
    return os.path.join(ESPN_DIR, f"day_{d:%Y%m%d}.json")


def _fetch_day(d):
    """-> the day's record, or None when it is not finished yet or a fetch failed."""
    sb = _get(SCOREBOARD.format(f"{d:%Y%m%d}"))
    games = []
    for e in sb.get("events") or []:
        st = (e.get("status") or {}).get("type") or {}
        if st.get("name") in ("STATUS_POSTPONED", "STATUS_CANCELED"):
            continue
        if not st.get("completed"):
            return None
        c = e["competitions"][0]
        teams = sorted(ABBR.get(x["team"]["abbreviation"], x["team"]["abbreviation"])
                       for x in c["competitors"])
        rows = box_rows(_get(SUMMARY.format(e["id"])))
        time.sleep(0.15)
        if not rows:
            return None
        games.append({"id": e["id"], "teams": teams, "tip": e.get("date"), "rows": rows})
    return {"date": f"{d:%Y-%m-%d}", "games": games}


def sync(today=None, budget_s=None):
    """Fetch every finished day from the season's start to the day before `today`
    that is not saved yet. -> {"days", "fetched", "missing", "error"}"""
    today = today or dt.date.today()
    budget_s = BUDGET_S if budget_s is None else budget_s
    t0 = time.time()
    out = {"days": 0, "fetched": 0, "missing": [], "error": None}
    if not _lock.acquire(timeout=budget_s):
        out["error"] = "another fetch was still running"
        return out
    try:
        os.makedirs(ESPN_DIR, exist_ok=True)
        d = SEASON_START
        while d < today:
            if os.path.exists(_day_path(d)):
                out["days"] += 1
            elif not FETCH or time.time() - t0 > budget_s:
                out["missing"].append(f"{d:%Y-%m-%d}")
            else:
                try:
                    rec = _fetch_day(d)
                except Exception as exc:                  # noqa: BLE001
                    rec = None
                    out["error"] = f"{type(exc).__name__}: {exc}"[:200]
                if rec is None:
                    out["missing"].append(f"{d:%Y-%m-%d}")
                else:
                    with open(_day_path(d) + ".tmp", "w", encoding="utf-8") as fh:
                        json.dump(rec, fh)
                    os.replace(_day_path(d) + ".tmp", _day_path(d))
                    out["days"] += 1
                    out["fetched"] += 1
            d += dt.timedelta(days=1)
    finally:
        _lock.release()
    return out


def warm():
    """Fill the box-score folder in the background when the tool starts."""
    threading.Thread(target=lambda: sync(budget_s=600), daemon=True).start()


# --- the game logs -----------------------------------------------------------
def load_logs(today=None, folder=None):
    """Every saved game before `today`. -> {"games": {key: [game, ...]},
    "team_dates": {team: [date, ...]}, "days": n}; a game is
    {date, team, mins, dk, starter}, played games only, oldest first."""
    today = today or dt.date.today()
    folder = folder or ESPN_DIR
    games = collections.defaultdict(list)
    team_dates = collections.defaultdict(set)
    days = 0
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        names = []
    for nm in names:
        if not (nm.startswith("day_") and nm.endswith(".json")):
            continue
        try:
            d = dt.datetime.strptime(nm[4:12], "%Y%m%d").date()
        except ValueError:
            continue
        if d >= today:
            continue
        try:
            with open(os.path.join(folder, nm), encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        days += 1
        for g in rec.get("games") or []:
            for t in g.get("teams") or []:
                team_dates[t].add(d)
            for r in g.get("rows") or []:
                if not r.get("dnp") and r.get("mins", 0) > 0:
                    games[r["key"]].append({"date": d, "team": r["team"],
                                            "mins": float(r["mins"]), "dk": float(r["dk"]),
                                            "starter": bool(r.get("starter"))})
    for k in games:
        games[k].sort(key=lambda x: x["date"])
    return {"games": dict(games), "team_dates": {t: sorted(v) for t, v in team_dates.items()},
            "days": days}


def _group(pos):
    p = (pos or "").split("/")[0].strip().upper()
    return "G" if p in ("PG", "SG", "G") else "F" if p in ("SF", "PF", "F") else \
        "C" if p == "C" else None


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


# --- the marks ---------------------------------------------------------------
def _joiner(G):
    """A Stokastic name that is not ESPN's (Alexandre / Alex Sarr, Nicolas / Nic
    Claxton, Gregory / GG Jackson) falls back to the one ESPN player on his team
    with the same last name and first initial."""
    idx = collections.defaultdict(set)
    for k, H in G.items():
        parts = k.split()
        if len(parts) >= 2:
            for t in {x["team"] for x in H}:
                idx[(t, parts[-1], parts[0][0])].add(k)

    def key(p):
        k = normalize_name(p.name)
        if k in G:
            return k
        parts = k.split()
        if len(parts) >= 2:
            hit = idx.get((p.team, parts[-1], parts[0][0])) or set()
            if len(hit) == 1:
                return next(iter(hit))
        return k
    return key


def marks(active, takeover_ids, logs):
    """{normalised name: marks} for every active player. takeover_ids: the dk_ids
    newbuild.takeover() marked tonight."""
    G = logs["games"]
    TD = logs["team_dates"]
    jk = _joiner(G)
    out = {}
    for p in active:
        k = normalize_name(p.name)
        H = G.get(jk(p), [])
        L5 = H[-5:]
        m5 = _mean(x["mins"] for x in L5)
        dk5 = _mean(x["dk"] for x in L5)
        starts5 = sum(1 for x in L5 if x["starter"])
        mins = float(p.minutes or 0)
        tk = p.dk_id in takeover_ids
        lift = bool(tk or (m5 is not None and mins > 0 and mins >= m5 + LIFT_MINS))
        newstart = bool(p.starting is True and len(L5) >= NEW_START_MIN_GAMES
                        and starts5 <= NEW_START_MAX)
        bench = p.starting is False
        under = 0 < mins < UNDER_MINS
        # a teammate at his position, projected 10+, who missed the team's last
        # game after playing in an earlier one
        back = []
        dates = TD.get(p.team) or []
        if dates:
            last = dates[-1]
            grp = _group(p.pos)
            for q in active:
                if q is p or q.team != p.team or q.proj < BACK_PROJ or _group(q.pos) != grp:
                    continue
                qh = [x for x in G.get(jk(q), []) if x["team"] == p.team]
                if qh and qh[-1]["date"] < last:
                    back.append(q.name)
        n = len(H)
        tot_m = sum(x["mins"] for x in H)
        ppm = (sum(x["dk"] for x in H) / tot_m) if tot_m else None
        six = 6.0 * p.salary / 1000.0
        n6x_small = sum(1 for x in H if x["dk"] >= six and x["mins"] < BONES_MINS)
        role_change = lift or newstart
        why = []
        if bench:
            why.append("bench")
        if back:
            why.append("teammate back: " + ", ".join(back))
        if under:
            why.append(f"projected {mins:.0f} minutes")
        if why and n == 0:
            why.append("no game on record this season")
        weak = bool(p.ownership >= CHALK_OWN and not role_change and (bench or back or under))
        bones = bool(p.salary < BONES_SAL and 0 < mins < BONES_MINS and n >= BONES_GAMES
                     and ppm is not None and ppm >= BONES_PPM and n6x_small >= BONES_6X)
        out[k] = {"name": p.name, "team": p.team, "salary": p.salary,
                  "own": round(p.ownership, 1), "proj": round(p.proj, 1),
                  "mins": round(mins, 1), "starting": p.starting,
                  "games": n, "m5": None if m5 is None else round(m5, 1),
                  "dk5": None if dk5 is None else round(dk5, 2),
                  "dkLast": L5[-1]["dk"] if L5 else None,
                  "starts5": starts5, "takeover": tk, "lift": lift, "newStart": newstart,
                  "bench": bench, "under22": under, "back": back,
                  "ppm": None if ppm is None else round(ppm, 2), "n6xSmall": n6x_small,
                  "weak": weak, "why": "; ".join(why) if weak else "", "bones": bones}
    return out


def chalk_without_games(active, logs):
    """Chalk with no ESPN game on record (a debut, a return from a long absence,
    or a name that does not join): no lift or new starting job can be seen."""
    G = logs["games"]
    jk = _joiner(G)
    return [p.name for p in active if p.ownership >= CHALK_OWN and not G.get(jk(p))]


def slate_traits(active):
    """The Shark Edge report's trigger values: the share of teams tipping 30+
    minutes after the first game, the gap between the top two studs'
    projections, the studs projected 40+. Logged only."""
    tips = {}
    for p in active:
        s = getattr(p, "start", "") or ""
        if p.team and s:
            try:
                tips[p.team] = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                pass
    late = None
    if tips:
        first = min(tips.values())
        late = round(sum(1 for t in tips.values()
                         if (t - first).total_seconds() >= 30 * 60) / len(tips), 3)
    studs = sorted((p.proj for p in active if p.salary >= 9000), reverse=True)
    return {"lateTeamShare": late, "teamsWithTip": len(tips),
            "studGap": round(studs[0] - studs[1], 1) if len(studs) >= 2 else None,
            "studs40": sum(1 for x in studs if x >= 40)}
