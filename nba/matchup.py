"""G10, the easy-matchup floor (NBA Matchup Report 11-14, build list).

What an opponent allows: every opposing player's game against the team before
tonight (8+ minutes), his DK points over what his minutes would give at his own
usual rate (DK points a minute in his OTHER games before tonight, 60+ minutes on
record), minus 1, pulled toward zero by n / (n + 30). It needs 12 player-games.
Stokastic's projection already moves with the opponent; what it leaves shows
only at the ends, and the review's floor acts only there.

The calls: typed-pool players at 5-20% projected own whose opponent's figure is
at or over the line, the five with the highest figure (ties to the higher
projected own). The line is the 75th percentile of the figure among 5-20%
players on the EARLIER saved nights (40+ players on record). Each call is
floored at 2x his projected own (95% at most) in the 150 and in each 20, after
the pick, the way the takeover lean is applied.

Review numbers (strict pre-lock version, 2x): the 150 +2.58 a lineup and
+0.71 top-1% lineups on 13 nights; the 20s +2.27 on 14. The same floor on
players in the toughest matchups loses (the placebo). Ledger row G10.

This is the review's `Prototype/mu139.py` on the tool's own box scores
(role.py, logs/espn/).
"""

from __future__ import annotations

import collections
import datetime as dt

MIN_MINS = 8.0           # a game counts at 8+ minutes
USUAL_MIN_MINS = 60.0    # his usual rate needs 60+ minutes in his other games
SHRINK = 30.0            # n / (n + 30)
MIN_N = 12               # player-games against the team
LINE_Q = 0.75            # the line: 75th percentile ...
LINE_MIN = 40            # ... of 40+ players at 5-20% own on the earlier nights
OWN_LO, OWN_HI = 5.0, 20.0
MAX_CALLS = 5
FLOOR_K = 2.0            # floored at 2x his projected own
FLOOR_CAP = 0.95


def night_date(md):
    """'11-13' -> date(2026, 11, 13): the 2026-27 season."""
    m, d = (int(x) for x in md.split("-"))
    return dt.date(2026 if m >= 7 else 2027, m, d)


def _games(logs):
    """[(player key, date, opp, mins, dk)] for every game of 8+ minutes."""
    out = []
    for k, L in (logs.get("games") or {}).items():
        for g in L:
            if g["mins"] >= MIN_MINS and g.get("opp"):
                out.append((k, g["date"], g["opp"], g["mins"], g["dk"]))
    return out


def figures(games, before):
    """{team: figure or None} from the player-games before `before` (a date)."""
    tot = collections.defaultdict(lambda: [0.0, 0.0])
    for k, d, _o, m, dk in games:
        if d < before:
            tot[k][0] += dk
            tot[k][1] += m
    acc = collections.defaultdict(lambda: [0.0, 0.0, 0])
    for k, d, o, m, dk in games:
        if d >= before:
            continue
        sd, sm = tot[k]
        om = sm - m
        if om < USUAL_MIN_MINS:
            continue
        a = acc[o]
        a[0] += dk
        a[1] += m * (sd - dk) / om
        a[2] += 1
    out = {}
    for o, (num, den, n) in acc.items():
        out[o] = ((num / den - 1.0) * n / (n + SHRINK)) if n >= MIN_N and den else None
    return out


def _pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))] if v else None


def line(history, games, tonight):
    """The 75th percentile of the figure among 5-20% players on the saved nights
    before `tonight`, each night's figure from the games before it. -> (line or
    None, players on record)"""
    vals = []
    for h in history:
        try:
            d = night_date(h["date"])
        except (KeyError, ValueError):
            continue
        if d >= tonight:
            continue
        fig = None
        for v in h["P"].values():
            if not (OWN_LO <= v["own"] < OWN_HI) or not v.get("opp"):
                continue
            if not (v["proj"] > 0 and v["mins"] > 0 and v["sal"] > 0
                    and v.get("injury", "") in ("None", "")):
                continue
            if fig is None:
                fig = figures(games, d)
            f = fig.get(v["opp"])
            if f is not None:
                vals.append(f)
    return (_pct(vals, LINE_Q) if len(vals) >= LINE_MIN else None), len(vals)


def tonight(active, logs, history, today):
    """active: tonight's buildable players (in_pool set). -> {"line", "linePlayers",
    "figures": {team: figure}, "calls": [player dicts], "ready": bool, "why"}"""
    games = _games(logs)
    fig = figures(games, today)
    ln, n = line(history, games, today)
    sheet = any(p.in_pool for p in active)
    out = {"line": ln, "linePlayers": n, "ready": ln is not None,
           "figures": {t: (None if f is None else round(f, 4)) for t, f in sorted(fig.items())},
           "calls": [], "why": ""}
    if ln is None:
        out["why"] = (f"only {n} players at 5-20% own on the earlier saved nights have a "
                      f"matchup figure (40 needed)")
        return out
    cand = [p for p in active if (p.in_pool or not sheet)
            and OWN_LO <= p.ownership < OWN_HI
            and fig.get(p.opponent) is not None and fig[p.opponent] >= ln]
    cand.sort(key=lambda p: (-fig[p.opponent], -p.ownership))
    for p in cand[:MAX_CALLS]:
        out["calls"].append({"name": p.name, "id": p.dk_id, "team": p.team,
                             "opp": p.opponent, "salary": p.salary,
                             "own": round(p.ownership, 1), "proj": round(p.proj, 1),
                             "figure": round(fig[p.opponent], 4)})
    return out


def floors(calls, n, k=None):
    """{dk_id: lineups}: each call floored at k x his projected own (95% at most)."""
    k = FLOOR_K if k is None else k
    return {c["id"]: int(round(min(k * c["own"] / 100.0, FLOOR_CAP) * n)) for c in calls}
