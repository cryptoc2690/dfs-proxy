"""Local WNBA DFS optimizer — a self-contained web app, powered by LineStar.

Run it, open the browser, drop in a LineStar projections CSV, get lineups.
LineStar carries everything we need in one file — projection, floor, ceiling,
real projected ownership, starter status and Vegas implied totals — so there's
no cheatsheet to reconcile and no external API. Pure standard library.

    python app.py                 # opens http://localhost:8000
"""

from __future__ import annotations

import bisect
import json
import os
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dk import (MIN_FORWARDS, MIN_GUARDS, ROSTER_SIZE, SALARY_CAP, Player,
                normalize_name)
from engine import CEILING_WEIGHT, STUD_SALARY, SUB10_OWN
from engine import build_gpp as optimize_gpp


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# Ceiling sanity caps, as a multiple of the projection. A 7-slate review found
# the old flat 2.5x rarely bound (6% of rows) but clipped real smashes when it
# did — 9 of 22 capped players beat 2.5x, including a 25-minute starter who
# scored 49 against an 11-point projection. The artifact it exists to kill is a
# low-MINUTE body with an inflated ceiling, so the tight cap now applies only
# below the rotation floor and rotation players get room to run.
CEIL_CAP_MULT = 2.5        # applied at parse time, before minutes are known
CEIL_CAP_MULT_ROTATION = 3.0   # relaxed once minutes confirm a real role


# ---------------- LineStar parsing ----------------
def _parse_versus(vs, team):
    """LineStar VersusStr -> (opponent, DK-style game key 'AWAY@HOME').

    '@IND' -> the player's team is away: opp IND, game '{team}@IND'.
    'vs LVA' -> the player's team is home: opp LVA, game 'LVA@{team}'.
    Keying both sides of a matchup to the same 'AWAY@HOME' string is what lets
    the engine detect and reward game stacks.
    """
    vs = (vs or "").strip()
    if vs.startswith("@"):
        opp = vs[1:].strip()
        return opp, f"{team}@{opp}"
    if vs[:2].lower() == "vs":
        opp = vs[2:].strip()
        return opp, f"{opp}@{team}"
    return "", team


def parse_linestar(text):
    """Parse a LineStar projections export into Player records.

    Projection, season PPG, floor, ceiling and projected ownership all come
    straight from the file. Floor/ceiling are sanity-checked against the
    projection (LineStar occasionally ships a 0 floor or a ceiling barely above
    the projection); when they look wrong we fall back to a projection-anchored
    band, because those two numbers are the simulator's outcome band and a
    broken one distorts every lineup the player appears in.
    """
    import csv as _csv
    import io
    rows = list(_csv.DictReader(io.StringIO((text or "").lstrip("﻿"))))
    players = []
    for i, d in enumerate(rows):
        name = (d.get("Name") or "").strip()
        if not name:
            continue
        pos = (d.get("Position") or "").strip().upper()
        is_guard = pos.split("/")[0] in ("PG", "SG", "G")
        team = (d.get("Team") or "").strip()
        opp, game = _parse_versus(d.get("VersusStr"), team)
        proj = _f(d.get("Projected"))
        p = Player(
            name=name, dk_id=f"ls-{i}", salary=int(_f(d.get("Salary"))),
            team=team, opponent=opp, game=game, is_guard=is_guard,
            avg_points=_f(d.get("PPG")), status="",
        )
        p.starter = str(d.get("StartingStatus") or "").strip() == "1"
        p.implied = _f(d.get("VegasImplied"))
        p.spread = _f(d.get("Vegas"))
        if proj <= 0:  # StartingStatus 4 / deep bench -> not playing
            p.proj = p.floor = p.ceil = 0.0
            p.status = "OUT"
            players.append(p)
            continue
        ceil, floor = _f(d.get("Ceiling")), _f(d.get("Floor"))
        # Trust LineStar's own variance model; override ONLY genuinely broken
        # values. A ceiling at/below the projection is impossible for the sim; a
        # floor that's <=0, absurdly low (< 0.35x proj), or >= projection is a
        # bad row (LineStar ships the odd 0 or token floor). Otherwise keep them
        # — a legitimately modest ceiling should fade a low-upside play, not get
        # inflated into false upside (which was over-rewarding Boston-type
        # plays and under-fading chalk like Sabally).
        if ceil <= proj:
            ceil = round(proj * 1.3, 1)
        elif ceil > proj * CEIL_CAP_MULT:
            # Broken high ceiling: LineStar inflates the ceiling on some low-minute
            # bench players (Okot: 5.48 proj / 5 min, but a 22 ceiling). A ceiling
            # that's a wild multiple of the projection isn't real upside, it's a
            # data artifact — and it would make a 5-minute punt look like the best
            # cheap play on the board. Cap it to a sane band around the projection.
            # Cap the RAW value too, so the minutes pass can relax it for players
            # who clear the rotation floor without losing the original number.
            p.raw_ceil = ceil
            ceil = round(proj * CEIL_CAP_MULT, 1)
        if floor <= 0 or floor < proj * 0.35 or floor >= proj:
            floor = round(proj * 0.6, 1)
        p.proj = round(proj, 1)
        p.ls_proj = p.proj          # kept raw; blend_projections moves p.proj
        p.ceil = round(ceil, 1)
        p.floor = round(floor, 1)
        # Floor a playable player's ownership at 1%: a blank/0 ProjOwn would
        # otherwise read as the most contrarian play on the board purely from
        # missing data, not from being genuinely under-owned.
        own = _f(d.get("ProjOwn"))
        p.ownership = own if own > 0 else 1.0
        p.notes.append("LineStar" if p.starter else "LineStar · bench")
        players.append(p)
    return players


# A player LineStar projects at 0 is dead three times over: parse_linestar marks
# him OUT and zeroes him, apply_daily_projections skips him before it attaches
# anything, and blend_projections skips him again. So when LineStar does not know
# whether someone is starting and prints a 0, the daily file's minutes and stat
# line for that same player are never even read.
#
# That is the wrong file to ignore. The 24-contest review ranked how well each
# signal's lineup sum predicts finish, and the daily file BEAT LineStar:
#
#   realised ownership  -0.32     daily-file DK sum   -0.29
#   projected ownership -0.23     season PPG sum      -0.22
#   LineStar projection -0.19     LineStar ceiling    -0.17
#
# It is already a third of the blend for everyone else. This uses it for the one
# group where LineStar has admitted it has no opinion.
#
# Two guards, because a 0 usually does mean OUT and resurrecting a scratch would
# be far worse than missing a starter:
#
#   YOU have to have named him. Pool or core only — the sharp asserting he plays
#   is the whole evidence here, and it is the same "your instruction outranks the
#   vendor" rule cores already run on. Nothing is revived off a bare slate.
#   REAL MINUTES. A stale daily row on a player ruled out after that file was
#   built still carries his old line, so a token four minutes is not enough. The
#   bar is ROTATION_MINUTES, read at call time because it is defined below.
#
# CHECKED AGAINST 16 SLATES, and the honest summary is "harmless and nearly
# inert". LineStar zeroes about 29 players a slate and roughly 6.8 of them play,
# but they score a mean of 6.3 — so most of what the zero hides is not worth
# having. With the 14-minute guard the rule fires on FOUR players across 16
# slates, all four played, mean 7.4 points, and only one was in a pool. The
# failure mode it was built to avoid did not occur once: 0 of 4 resurrected a
# player who was genuinely out.
#
# The guard is at the right place. Play rate by daily minutes among zeroed
# players: 8 min 65% (n=23), 10 min 86% (n=14), 12 min 100% (n=5), 14 min 100%
# (n=4). Lowering it to 10 would roughly triple how often this fires and buy a
# 14% chance each time of starting someone who never plays.
#
# Two things to keep in view. The daily number is a MEDIOCRE projection for these
# players — it over-projects the ones clearing 14 minutes by 6.6. And 93 of the
# 97 zeroed players who actually played had under 14 daily minutes, so the rule
# cannot reach them by design. Expect this to matter about once a month.


def revive_pooled_zeros(players, daily_text, report=None):
    """Give a pooled player LineStar zeroed a projection from the daily file.

    Runs AFTER cores and pool are assigned, because being named is the licence.
    -> [(player, minutes, dk)] for everyone revived, so the caller can say so.
    """
    dmap = parse_daily_projections(daily_text or "")
    if not dmap:
        return []
    out = []
    for p in players:
        if p.proj > 0 or not p.in_pool:
            continue
        d = dmap.get(normalize_name(p.name))
        if not d or d["minutes"] < ROTATION_MINUTES or d["compdk"] <= 0:
            continue
        p.proj = round(d["compdk"], 1)
        # Same shape parse_linestar falls back to when LineStar's own ceiling and
        # floor are unusable — which they are here, since both were zeroed.
        p.ceil = round(p.proj * 1.3, 1)
        p.floor = round(p.proj * 0.6, 1)
        p.minutes, p.stuffer, p.daily_dk = d["minutes"], d["stuffer"], d["compdk"]
        p.risk = False
        p.status = ""                      # no longer "OUT" — he is playable
        # ls_proj stays 0. LineStar really did say nothing, and late swap's
        # projection-cut test compares raw to raw; a fabricated baseline there
        # would invent news later.
        p.ownership = p.ownership or 1.0
        p.notes.append(f"revived: LineStar 0, daily file {d['minutes']:.0f} min "
                       f"/ {d['compdk']:.1f} DK, and you pooled him")
        out.append((p, d["minutes"], d["compdk"]))
    if out and report is not None:
        report.append(
            "Using the daily file for " + ", ".join(
                f"{p.name} ({m:.0f} min, {dk:.1f} DK)" for p, m, dk in out)
            + " — LineStar has them at 0, which usually means out. They are in "
              "your pool, so that is being read as you knowing better. Drop them "
              "from the pool if that is not what you meant.")
    return out


def _log_swap(result, players, baseline, options, news):
    """Append one late-swap record. Never let logging break a swap run.

    Two things are captured that nothing else records.

    BOTH POLICIES. Every entry carries what news-only would have done and what
    free re-optimisation would have done, whichever was acted on — so a single
    night grades the policy that ran AND the one that did not.

    EVERY PROJECTION THAT MOVED, with its value at lock, its value now, and the
    actual score where the game has finished. That is the evidence for the
    question underneath all of this: LineStar hedges when it does not know a
    starting five, so is the later number actually BETTER, or just different? If
    it is not better, re-optimising on it is a more expensive way to churn.
    """
    try:
        from datetime import datetime
        moved = []
        for p in players:
            was = (baseline.get(normalize_name(p.name)) or {}).get("ls")
            now = p.ls_proj if p.ls_proj > 0 else p.proj
            if was is None or (abs(now - was) < 1.5 and not p.status):
                continue
            moved.append({
                "name": p.name, "team": p.team, "salary": p.salary,
                "lockLs": round(was, 1), "nowLs": round(now, 1),
                "nowBlend": round(p.proj, 1), "starter": p.starter,
                "status": p.status,
                "scored": result.get("_scored", {}).get(normalize_name(p.name)),
            })
        record = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "games": sorted({p.game for p in players if p.game}),
            "mode": "newsOnly" if result.get("newsOnly") else "free",
            "discBar": SWAP_DISCRETIONARY_GAIN, "newsBar": SWAP_MIN_GAIN,
            "hadBaseline": result.get("hadBaseline"),
            "hadContest": result.get("field") is not None,
            "field": result.get("field"), "winScore": result.get("winScore"),
            "lockedPlayers": result.get("lockedPlayers"),
            "entries": result.get("entries"), "changed": result.get("changed"),
            "news": {k: v for k, v in (news or {}).items()},
            "projectionsMoved": moved,
            "swaps": [{k: v for k, v in s.items()
                       if k in ("entryId", "keep", "hold", "gain", "projGain",
                                "rank", "projFinal", "pct", "banked", "proj",
                                "salary", "open", "news", "shadow")}
                      for s in result.get("swaps", [])],
        }
        os.makedirs(os.path.dirname(SWAP_LOG_PATH), exist_ok=True)
        with open(SWAP_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:  # noqa: BLE001
        # Same rule as the build log: never break the run, never disappear.
        result.setdefault("warnings", []).append(
            f"Swap log not written ({exc.__class__.__name__}: {exc}). The swap "
            f"itself is unaffected, but this night will not be gradable later.")


def parse_linestar_scored(text):
    """{normalized name: actual DK points so far} from LineStar's `Scored` column.

    Empty pre-lock; mid-slate it carries live/final scores for players whose games
    have started. This is what lets late swap know how a lineup already stands.
    """
    import csv as _csv
    import io
    out = {}
    for d in _csv.DictReader(io.StringIO((text or "").lstrip("﻿"))):
        name = (d.get("Name") or "").strip()
        got = _f(d.get("Scored"))
        if name and got > 0:
            out[normalize_name(name)] = got
    return out


# ---------------- daily projections (minutes + stat-stuffer floor) ----------------
# Minutes are now INFORMATION ONLY. Back-testing first killed the graded version
# (minutes and stat-stuffer had ~zero correlation with bust rate), leaving a gate
# that removed anyone projected under 14 minutes. Held out across 23 slates that
# gate is the cleanest negative in the whole grid: turning it OFF helped on 10
# slates and hurt on 0, and gained 6 lineups in the money. The daily file's
# minutes run optimistic and it gates real players — on 8-10 it removed Carrington,
# who was in that night's winning lineup, because the file said zero minutes. One
# day's file (8-23) was for a different slate entirely and the gate silently ran
# on half the board with nobody the wiser.
#
# Set this above 0 to gate again; at 0 nothing is gated and minutes only display.
# The daily file's projection VOTE is unaffected and stays — that half is neutral.
GATE_MINUTES = 0.0

# Minutes that confirm a real rotation role, used only to decide whether a
# player's ceiling was a low-minute artifact (see CEIL_CAP_MULT_ROTATION). This
# is deliberately separate from GATE_MINUTES: "is the ceiling believable" and
# "should we build with them at all" are different questions, and tying them to
# one number is what let the gate quietly loosen ceilings when it was turned off.
ROTATION_MINUTES = 14.0


def parse_daily_projections(text):
    """Parse a daily category-projections CSV -> {norm_name: {minutes, stuffer,
    compdk}}. The file has a two-row header (group labels, then real header), so
    we skip the first line. stuffer = DK points from the non-scoring categories
    (the reliable floor); compdk = a full DK projection from the stat line, used
    only to flag where this source and LineStar strongly disagree."""
    import csv as _csv
    import io
    lines = (text or "").lstrip("﻿").splitlines()
    if len(lines) < 3:
        return {}
    out = {}
    for r in _csv.DictReader(io.StringIO("\n".join(lines[1:]))):
        name = (r.get("Player") or "").strip()
        if not name:
            continue
        pts, reb, ast = _f(r.get("PTS")), _f(r.get("REB")), _f(r.get("AST"))
        stl, blk, tpm, to = _f(r.get("STL")), _f(r.get("BLK")), _f(r.get("3PM")), _f(r.get("TO"))
        stuffer = 1.25 * reb + 1.5 * ast + 2 * stl + 2 * blk
        compdk = pts + 0.5 * tpm + stuffer - 0.5 * to
        out[normalize_name(name)] = {
            "minutes": _f(r.get("Min")), "stuffer": round(stuffer, 1),
            "compdk": round(compdk, 1),
        }
    return out


def apply_daily_projections(players, text):
    """Overlay minutes + stat-stuffer floor from the daily-projections file and
    gate out genuine non-rotation bodies (projected minutes under GATE_MINUTES).
    This is a gate, not a grade — we no longer classify the rest as reliable vs
    risky, because the data says that split doesn't predict busts. Unmatched
    players are left in (we can't gate what we can't measure). LineStar still owns
    the projection; a note flags where the two sources disagree hard."""
    dmap = parse_daily_projections(text)
    if not dmap:
        return False  # no minutes read without the file; gate stays off
    for p in players:
        if p.proj <= 0:
            continue
        d = dmap.get(normalize_name(p.name))
        if not d:  # unmatched -> can't measure minutes, so don't gate it
            continue
        p.minutes = d["minutes"]
        p.stuffer = d["stuffer"]
        p.daily_dk = d["compdk"]
        p.risk = GATE_MINUTES > 0 and p.minutes < GATE_MINUTES
        # Real rotation minutes -> the ceiling wasn't a low-minute artifact, so
        # give back what the parse-time cap took (up to the looser multiple).
        # Keyed on the minutes themselves, not on the gate flag: the gate is off
        # by default now and this is a separate question from whether to build
        # with the player at all.
        if p.minutes >= ROTATION_MINUTES and getattr(p, "raw_ceil", 0):
            p.ceil = round(min(p.raw_ceil, p.proj * CEIL_CAP_MULT_ROTATION), 1)
        p.notes.append(f"gated: {p.minutes:.0f} proj min (non-rotation)" if p.risk
                       else f"{p.minutes:.0f} min, stuffer {p.stuffer:.0f}")
        # Cross-source disagreement flag (informational; LineStar's proj is used).
        if abs(d["compdk"] - p.proj) >= 6 and abs(d["compdk"] - p.proj) >= 0.3 * p.proj:
            p.notes.append(f"src split: proj-file {d['compdk']:.0f} vs LS {p.proj:.0f}")
    return True


# ---------------- projection blend ----------------
# The 24-contest review's single biggest finding about OUR tool: we were
# maximising the weakest signal available. Ranked by how well a lineup-level sum
# predicts finish (Spearman vs finish percentile, negative = better):
#
#   realised ownership   -0.32     daily-file DK sum    -0.29
#   projected ownership  -0.23     season PPG sum       -0.22
#   LineStar projection  -0.19     LineStar ceiling     -0.17
#
# We sat at the 70th percentile of LineStar projection sum but the 49th of season
# PPG and the 47th of projected ownership — i.e. we were near the top of the one
# column that predicts least, and average on the ones that predict more. The
# three add INDEPENDENTLY in a joint model, so this isn't a choice between them.
#
# At player level LineStar is still the best single point forecast (MAE 6.78 vs
# 7.57 for season average), but it is optimistic by +0.9 on the players the field
# actually rosters, and it over-reacts relative to the season baseline:
#
#   LineStar >= PPG + 3  ->  misses by 2.2 (negative in 17 of 23 slates), and
#                            this is the MOST-owned bucket in the field
#   LineStar <= PPG - 3  ->  beats by 3.1 (positive in 23 of 23 slates)
#
# Averaging the two removes that bias without hurting correlation (r 0.753 both
# ways). It also fixes, for free, the exposure mistake the review pinned on us:
# we ran +8.9 points of ownership ABOVE the field on "role bump" chalk (LineStar
# well over season average) while sitting 3.1 BELOW the field on everything else.
# Blending is the whole correction — we deliberately do NOT also add a separate
# role-bump penalty, because that would double-count one belief twice, which is
# the exact error that got the core projection edge deleted.
PPG_WEIGHT = 0.5        # season average's share of the blend
DAILY_DISAGREE = 2.0    # daily-vs-LineStar gap that earns the daily file a vote


def blend_projections(players):
    """Move p.proj from LineStar's raw number to a blend of the signals that
    actually predict finish. Floor and ceiling ride along on the same ratio, so
    LineStar's outcome SHAPE is preserved and only its level moves.

    Run after apply_daily_projections, which is what fills in daily_dk."""
    moved = 0
    for p in players:
        if p.proj <= 0 or p.ls_proj <= 0:
            continue
        ls, ppg, daily = p.ls_proj, p.avg_points, p.daily_dk
        blend = ls
        if ppg > 0:
            blend = (1 - PPG_WEIGHT) * ls + PPG_WEIGHT * ppg
        # The daily file only gets a vote where it DISAGREES with LineStar; that
        # disagreement is directional, not noise. When the daily file sits 2-6
        # points below LineStar, LineStar came in 3.3 too high (19 of 21 slates);
        # when it sits 6+ above, LineStar came in 4.3 too low (15 of 21). The
        # truth lands between the two, so a third vote is exactly right.
        if daily > 0 and abs(daily - ls) >= DAILY_DISAGREE:
            blend = (blend * 2 + daily) / 3.0
        blend = max(blend, 0.1)
        ratio = blend / ls
        p.proj = round(blend, 1)
        p.floor = round(p.floor * ratio, 1)
        p.ceil = round(p.ceil * ratio, 1)
        if p.raw_ceil:
            p.raw_ceil = p.raw_ceil * ratio
        if abs(p.proj - ls) >= 1.5:
            moved += 1
            p.notes.append(f"blend {p.proj:.1f} (LS {ls:.1f}"
                           + (f", PPG {ppg:.1f}" if ppg > 0 else "")
                           + (f", daily {daily:.1f}" if daily > 0 and
                              abs(daily - ls) >= DAILY_DISAGREE else "") + ")")
    return moved


# ---------------- slate helpers ----------------
def _slate_date():
    """Today's date in US Eastern — the date the BUILD happened.

    It used to take a `players` argument and ignore it, which read as "the slate's
    date" everywhere it was used. It is not: a 10pm ET tip rolls past midnight, so
    a build and a late swap on the same slate can land on different dates. Nothing
    that has to identify a slate may key off this — see _news_baseline, which
    matches on the game set instead.
    """
    from datetime import datetime, timedelta
    et = datetime.utcnow() - timedelta(hours=4)  # WNBA plays in summer -> EDT
    return et.date().isoformat()


def _slate_type(players):
    pool = [p for p in players if p.proj > 0]
    cheap_best = max((p.proj for p in pool if p.salary <= 5500), default=0.0)
    return "stars-and-scrubs" if cheap_best >= 16 else "balanced"


def _apply_removals(players, remove_names):
    """Zero a removed player AND push ~65% of their production onto teammates,
    weighted toward same-position replacements (their minutes/usage don't
    vanish — they flow to the next guys up). Ownership is left untouched: it's
    LineStar's real projected ownership, and our redistribution shouldn't
    inflate a replacement's ownership. Returns the removed names."""
    removed = [(p, p.proj) for p in players
               if normalize_name(p.name) in remove_names and p.proj > 0]
    for p, _ in removed:
        p.proj = p.floor = p.ceil = 0.0
        p.notes.append("removed — out/traded/benched")
    for p, vac in removed:
        mates = [q for q in players if q.team == p.team and q.proj > 0]
        if not mates:
            continue
        w = {id(q): q.proj * (1.6 if q.pos == p.pos else 1.0) for q in mates}
        tot = sum(w.values()) or 1.0
        for q in mates:
            bump = min(0.65 * vac * w[id(q)] / tot, 0.40 * q.proj, 8.0)
            if bump <= 0.3:
                continue
            q.proj = round(q.proj + bump, 1)
            q.floor = round(q.floor + bump * 0.7, 1)
            q.ceil = round(q.ceil + bump * 1.1, 1)
            q.notes.append(f"+{bump:.0f} ({p.name} out)")
    return [{"name": p.name, "proj": round(vac, 1), "salary": p.salary, "risk": p.risk}
            for p, vac in removed]


_NAME_SKIP = {"g", "f", "c", "gf", "fc", "pg", "sg", "sf", "pf", "pos",
              "position", "team", "salary", "sal", "name", "player", "core",
              "opp", "opponent", "proj", "projection", "own", "ownership",
              "value", "notes", "x"}


def _name_cell(cell):
    """Does this field look like a player's name? -> bool"""
    cell = (cell or "").strip().strip('"').strip()
    if len(cell) < 2:
        return False
    low = cell.lower()
    if low in _NAME_SKIP or low.startswith(("name", "player")):
        return False
    if not any(ch.isalpha() for ch in cell):
        return False            # 6900, $5,300, 12.4 — a salary or a projection
    if len(cell) <= 3 and cell.isupper():
        return False            # LVA, NYL, SEA — a team code, never a person
    return True


# One player and one percentage per line: "A'ja Wilson 40". The number may carry
# a % and may be separated by a comma, a colon, an equals or just spaces.
_CAP_LINE = re.compile(r"^(.*?)[\s,:=]+(\d{1,3}(?:\.\d+)?)\s*%?$")


def _parse_player_caps(text, players, default_pct):
    """-> ({dk_id: share 0-1}, [lines that matched no one], [lines misread])

    A line carrying its own number uses it; a bare name falls back to
    `default_pct`, which is what the 🔒 marks on the slate rows send. An
    unmatched name is REPORTED, never dropped — silently ignoring a cap you
    typed is the one failure this control exists to prevent.
    """
    by_name = {normalize_name(p.name): p for p in players}
    caps, missing, bad = {}, [], []
    for raw in re.split(r"[\r\n;]+", text or ""):
        line = raw.strip()
        if not line:
            continue
        m = _CAP_LINE.match(line)
        if m:
            name, pct = m.group(1).strip(), float(m.group(2))
        else:
            name, pct = line, default_pct
        key = normalize_name(name)
        if not key:
            bad.append(line)
            continue
        p = by_name.get(key)
        if p is None:                       # tolerate a partial or misspelt name
            hits = [v for k, v in by_name.items() if k.startswith(key)]
            p = hits[0] if len(hits) == 1 else None
        if p is None:
            missing.append(line)
            continue
        caps[p.dk_id] = min(1.0, max(0.0, pct / 100.0))
    return caps, missing, bad


def _parse_names(text):
    """Turn a pasted sheet into a set of normalized player names.

    Accepts both shapes without being told which:

      one per line          a column dragged out of a spreadsheet, possibly
                            with POS / SALARY / TEAM columns alongside it
      comma separated       a list typed or pasted on a single line

    This used to take the first tab/comma field of each line, which is right
    for the first and silently wrong for the second — a forty-name list pasted
    on one line came back holding one name and said nothing about it. So split
    on every separator and decide field by field whether the thing is
    name-shaped, which handles either layout and a mix of the two.
    """
    names = set()
    for cell in re.split(r"[\t,;\r\n]", text or ""):
        if _name_cell(cell):
            names.add(normalize_name(cell))
    return names


def _raw_names(text):
    """{normalized: as the user typed it} — so an unmatched name can be echoed
    back in their spelling rather than in the normalizer's lowercase."""
    out = {}
    for cell in re.split(r"[\t,;\r\n]", text or ""):
        if _name_cell(cell):
            out.setdefault(normalize_name(cell), (cell or "").strip().strip('"').strip())
    return out


# ---------------- export / serialization ----------------
def _upload_str(p):
    """DK-import string. We only ever have LineStar's own IDs (which are NOT DK
    IDs), so lineups export by name for manual entry."""
    return p.name if (not p.dk_id or p.dk_id.startswith(("dff-", "ls-"))) else f"{p.name} ({p.dk_id})"


_SLOTS = ["G", "G", "F", "F", "F", "UTIL"]


def _slots_payload(slots):
    return [{"slot": s, "name": p.name, "team": p.team, "pos": p.pos,
             "salary": p.salary, "proj": round(p.proj, 1), "core": p.core,
             "pool": p.in_pool, "starter": p.starter, "risk": p.risk and not p.core,
             # ownership and game are now construction rules, not just colour —
             # show them where the lineup is shown
             "own": round(p.ownership, 1), "game": p.game}
            for s, p in zip(_SLOTS, slots)]


def _stacks_payload(lu):
    return [f"{g}:{sum(1 for p in lu.players if p.game == g)}"
            for g in lu.games()
            if sum(1 for p in lu.players if p.game == g) >= 2]


def _alt_payload(alt):
    """Serialize a lineup's pool-legal alternative (P2), or None."""
    if not alt:
        return None
    slots = alt.dk_slots()
    return {
        "salary": alt.salary, "proj": alt.proj, "totalOwn": alt.total_own,
        "ceiling": round(alt.metrics.get("ceiling", 0), 1),
        "cores": sum(1 for p in alt.players if p.core),
        "stacks": _stacks_payload(alt),
        "players": _slots_payload(slots),
        "upload": [_upload_str(p) for p in slots],
    }


def _coach(playable, lineups, options, slate_type, had_minutes, removed_info, pool_names):
    """A read on the build — not edits. Explains what the data supports and flags
    where the user's settings diverge, so impulse overrides (forcing 2 cores over
    a misread flag, cutting a play the data liked) happen consciously, not by
    reflex. Checks against the DATA, never the outcome."""
    notes = []
    n = len(lineups) or 1
    expo = {}
    for lu in lineups:
        for p in lu.players:
            expo[p.name] = expo.get(p.name, 0) + 1

    rel = ("read (display only — nothing is gated)" if had_minutes
           else "not loaded — add the daily-projections CSV to see them")
    notes.append(("info", f"Baseline: {slate_type} slate, {n} lineups, minutes {rel}."))

    # Where the set landed on the two things the 24-contest review said were
    # costing us most: ownership position and sub-10%-owned bodies.
    if lineups:
        avg_own = sum(lu.total_own for lu in lineups) / len(lineups)
        thin = sum(1 for lu in lineups for p in lu.players if p.ownership < SUB10_OWN)
        zero_thin = sum(1 for lu in lineups
                        if not any(p.ownership < SUB10_OWN for p in lu.players))
        notes.append(("info",
            f"Ownership read: your set averages {round(avg_own)}% total projected ownership, "
            f"{zero_thin} of {n} lineups carry no sub-10%-owned player, and there are {thin} "
            f"such players across the set. The winning tier ran ZERO of them in 58% of lineups "
            f"against 37% for the field — that's the gap the review said was costing you most."))
        studless = sum(1 for lu in lineups
                       if not any(p.salary >= STUD_SALARY for p in lu.players))
        if studless:
            notes.append(("warn",
                f"{studless} of {n} lineups have no ${STUD_SALARY:,}+ player — the slate may not "
                f"have enough of them. Zero-stud lineups reach the top 1% at a third the rate."))

    two_game = len({p.game for p in playable if p.game}) == 2
    if two_game and lineups:
        splits = {}
        for lu in lineups:
            g = {}
            for p in lu.players:
                g[p.game] = g.get(p.game, 0) + 1
            splits[max(g.values())] = splits.get(max(g.values()), 0) + 1
        shape = ", ".join(f"{k}-{ROSTER_SIZE - k}: {v}" for k, v in sorted(splits.items()))
        notes.append(("info",
            f"Two-game slate, so the shape rules are on ({shape}). The balanced 3-3 split is the "
            f"worst construction measured (cash 17% vs 29% for 5-1) and the majority sits in the "
            f"higher-owned game, which paid in 7 of 7 slates. Your whole set leans on that game — "
            f"that's the trade the data asks for, but if it lays an egg the set goes with it."))

    cores = [p for p in playable if p.core]

    # Core report card — grade each core on ceiling + Vegas environment + ownership.
    # This checks the DATA, not the outcome, and never overrides the sharp who set
    # the cores; it's a second opinion so a weak anchor is a conscious choice.
    if cores:
        impls = [p.implied for p in playable if p.implied > 0]
        league = (sum(impls) / len(impls)) if impls else 0.0
        for c in sorted(cores, key=lambda p: -p.ownership):
            own = f"{round(c.ownership)}% owned"
            spot_bad = c.spread >= 8 or (league and c.implied and c.implied < league - 5)
            spot = (f"{'+' if c.spread > 0 else ''}{c.spread:g} dog, {round(c.implied)} implied"
                    if c.implied else "")
            if c.risk or c.ceil < 25:
                why = ("a sub-14-min non-rotation body (gated for everyone but you)" if c.risk
                       else f"a thin {round(c.ceil)} ceiling")
                extra = f" in a rough spot ({spot})" if spot_bad and spot else ""
                notes.append(("warn",
                    f"Core check — {c.name}: {why}{extra}, {own}. That's mandatory-exposure territory, "
                    f"not a build-around — the data would lean lighter here."))
            elif spot_bad:
                notes.append(("warn",
                    f"Core check — {c.name}: {round(c.ceil)} ceiling but a rough spot ({spot}), {own}. "
                    f"Upside's capped by the game environment — anchor if you must, but the ceiling is limited."))
            else:
                notes.append(("good",
                    f"Core check — {c.name}: {round(c.ceil)} ceiling, {own}, decent spot. Solid anchor."))
        gc = {}
        for c in cores:
            gc[c.game] = gc.get(c.game, 0) + 1
        g, cnt = max(gc.items(), key=lambda kv: kv[1])
        if cnt >= 2:
            notes.append(("info",
                f"Note: {cnt} of your {len(cores)} cores are in {g} — they rise and fall together, so one "
                f"bad game sinks the group. Spread anchors across games when you can."))

        # Core-exposure floor — every core is guaranteed real presence, so a
        # conviction play can't get buried at 1-of-N. Floor is data-driven from
        # the slate shape (more cores -> thinner floor each; more lineups -> more).
        import math as _math
        min_cores_set = _int(options.get("minCores"), 1)
        if min_cores_set > 0:
            floor_ct = int(_math.ceil(n / (len(cores) + 1)))
            notes.append(("info",
                f"Core floor: each of your {len(cores)} core(s) is guaranteed at least {floor_ct} of {n} "
                f"lineups — a play you believe in can't get squeezed out. Grade above tells you if the data "
                f"agrees; the call to keep or drop a weak core is yours, not the tool's."))

    # Pool gaps — strong, low-owned plays the sharp's pool is missing. Advisory:
    # the tool never adds them, it just surfaces the miss. The list recomputes
    # every build, so once you add one to the pool it drops off on its own.
    if pool_names:
        gaps = [p for p in playable
                if not p.in_pool and not p.risk and p.ceil >= 28 and p.ownership <= 15]
        gaps.sort(key=lambda p: -p.ceil)
        if gaps:
            lst = "; ".join(f"{p.name} ({round(p.ceil)} ceil, {round(p.ownership)}% own, ${p.salary:,})"
                            for p in gaps[:3])
            notes.append(("good",
                f"Pool gaps — strong low-owned plays NOT in your pool: {lst}. Your sharp may have passed "
                f"on purpose; if not, add them. (Each drops off here once you add it.)"))

    min_cores = _int(options.get("minCores"), 1) if cores else 0
    if cores and min_cores >= 2:
        chalk = max(cores, key=lambda p: p.ownership)
        pct = round(expo.get(chalk.name, 0) / n * 100)
        notes.append(("warn",
            f"You set {min_cores} cores per lineup — the whole set now leans on your cores. "
            f"{chalk.name} (chalkiest at {round(chalk.ownership)}% owned) is in {pct}% of lineups; if a "
            f"core busts, most of the set busts with it. The data floor is 1 — go to 2 only when you trust "
            f"every core."))

    if pool_names and _int(options.get("maxOffPool"), 0) >= 1:
        notes.append(("info",
            "Off-pool darts are allowed — each of those lineups carries a pool-only alternative to compare."))

    nc = [(nm, c) for nm, c in expo.items()
          if not any(p.name == nm and p.core for p in playable)]
    if nc:
        nm, c = max(nc, key=lambda x: x[1])
        pct = round(c / n * 100)
        p = next((q for q in playable if q.name == nm), None)
        if pct >= 55 and p:
            if p.value >= 2.8:
                notes.append(("good",
                    f"{nm} is your heaviest play ({pct}% of lineups) — and it's earned: ${p.salary:,}, "
                    f"proj {round(p.proj, 1)}, solid value. The tool's confident; don't cut it on a hunch."))
            else:
                notes.append(("info",
                    f"{nm} is your heaviest play ({pct}% of lineups) — modest value. Fine, just "
                    f"know your set leans on that one spot."))

    # Team-concentration read — the TOR lesson. Underowned starters stacked from
    # one team look independently great but ride a single game script; a blowout
    # sinks them together. The engine caps this, but surface it so the lean is a
    # conscious call.
    if lineups:
        tslots = {}
        for lu in lineups:
            for p in lu.players:
                tslots[p.team] = tslots.get(p.team, 0) + 1
        total = sum(tslots.values()) or 1
        teams_n = len({p.team for p in playable if p.team}) or 1
        top_team, top_ct = max(tslots.items(), key=lambda kv: kv[1])
        share, even = top_ct / total, 1 / teams_n
        if teams_n >= 2 and share >= even * 1.5:
            notes.append(("info",
                f"Team lean: {round(share * 100)}% of your roster slots are {top_team}, the pool's heaviest "
                f"team (an even split would be {round(even * 100)}%). They share one game script — capped so it "
                f"can't run away, but if {top_team} gets blown out that whole lean goes with it."))

    for r in removed_info:
        tag = "" if r["risk"] else f" (projected {r['proj']} at ${r['salary']:,})"
        notes.append(("info",
            f"You removed {r['name']}{tag}. Right call if it's a confirmed scratch — but if it's a hunch, "
            f"the data itself liked this play; the tool only misses late news you can see."))

    return [{"type": t, "text": x} for t, x in notes]


PORT = int(os.environ.get("PORT", "8000"))


def _build_warnings(report, unmatched_pool, requested, lineups=()):
    """Everything the build quietly gave up, turned into plain sentences.

    The tool is allowed to relax a constraint rather than hand back nothing. It is
    not allowed to do it silently: every failure this project has shipped was a
    fallback that worked and said nothing, and the UI reporting a full count for a
    short set is how a 12-entry contest gets 3 lineups uploaded to it.
    """
    out = []
    for note in report.get("relaxed", []):
        out.append(note)
    got = report.get("returned")
    if got is not None and got < requested:
        out.append(f"Only {got} of the {requested} lineups you asked for could be "
                   f"built — this board cannot field more distinct legal rosters. "
                   f"Check the pool and the OUT list before you upload.")
    if unmatched_pool:
        out.append("Not on this slate, so ignored: " + ", ".join(unmatched_pool))
    # Exposure is now REPORTED rather than capped. The cap it replaced was fake —
    # it rejected candidates and then a fill-to-N pass put about 2 in every 12
    # back in over the top of it, so realised exposure ran at 76% against a 60%
    # setting and nothing said so. A number you can see beats a limit that lies.
    # On a two-game board a genuinely dominant player will land near 100%, which
    # is usually right and occasionally not — so it gets said out loud.
    if lineups:
        counts = {}
        for lu in lineups:
            for pl in lu.players:
                counts[pl.name] = counts.get(pl.name, 0) + 1
        n = len(lineups)
        heavy = sorted((c, nm) for nm, c in counts.items() if c >= 0.7 * n)
        if heavy:
            worst = ", ".join(f"{nm} {c} of {n}" for c, nm in sorted(heavy, reverse=True)[:3])
            out.append(f"Heavy exposure: {worst}. That is the build following the "
                       f"projections, not a bug — but if you want it reined in, mark "
                       f"the player 🔒 on the slate row and set the cap.")
    return out


def run_board(csv_text: str, options: dict) -> dict:
    """The slate board, with the projection the BUILD will actually use.

    The page draws its own board straight from the LineStar CSV so you can mark
    players before a build exists. That parse reads one column, `Projected`,
    while the build runs on a blend of LineStar, the season average and the daily
    file — so the table you pick from showed Rhyne Howard at 36.8 while every
    lineup on the same screen showed him at 35.4, and the numbers a decision gets
    made on were not the numbers the decision was made with.

    This returns the blended board from the same three functions the build calls,
    in the same order, so there is one projection on the screen instead of two.
    """
    text = (csv_text or "").strip()
    if not text:
        return {"error": "Drop your LineStar projections CSV."}
    players = parse_linestar(text)
    if sum(1 for p in players if p.proj > 0) < ROSTER_SIZE:
        return {"error": "Couldn't read that file as a LineStar export "
                         "(or it has no projected players)."}
    had_minutes = apply_daily_projections(players, options.get("minutes") or "")
    blend_projections(players)
    _apply_removals(players, _parse_names(options.get("remove")))
    return {
        "hadMinutes": had_minutes,
        "players": [{
            "name": p.name, "team": p.team, "pos": p.pos, "salary": p.salary,
            "game": p.game, "proj": round(p.proj, 1),
            # The raw number too, so the page can show what moved and by how
            # much rather than silently replacing one figure with another.
            "lsProj": round(p.ls_proj, 1),
            "own": round(p.ownership, 1), "implied": round(p.implied, 1),
            "starter": p.starter,
        } for p in players if p.proj > 0],
    }


def run_optimize(csv_text: str, options: dict) -> dict:
    """Project from LineStar + build lineups, returning plain dicts for the GUI."""
    text = (csv_text or "").strip()
    if not text:
        return {"error": "Drop your LineStar projections CSV."}
    players = parse_linestar(text)
    if sum(1 for p in players if p.proj > 0) < ROSTER_SIZE:
        return {"error": "Couldn't read that file as a LineStar export "
                         "(or it has no projected players)."}
    source_label = "linestar"

    # Minutes read from the daily-projections file. LineStar still owns the
    # projection; this only gates out sub-14-min non-rotation bodies (a gate, not
    # a grade — the data killed reliability grading). Optional; no file, no gate.
    had_minutes = apply_daily_projections(players, options.get("minutes") or "")
    if had_minutes:
        source_label += " + minutes"

    # Blend LineStar with season PPG (and the daily file where it disagrees).
    # This is the projection the rest of the build runs on — see blend_projections.
    blended = blend_projections(players)
    source_label += " · blended"

    # Manual removals (late scratch / missed shootaround the projection hasn't
    # caught). Zero them and flow their minutes/usage to teammates.
    removed_info = _apply_removals(players, _parse_names(options.get("remove")))
    removed = [r["name"] for r in removed_info]

    # Cores + pool. Cores count as in-pool and earn their place through exposure
    # levers only (min_cores + the exposure floor), never a projection edge; the
    # pool itself is a build constraint enforced in the engine (max_off_pool).
    core_names = _parse_names(options.get("cores"))
    pool_names = _parse_names(options.get("pool"))
    # A name that matches nobody used to vanish without a word, and the build
    # carried on around the hole. Confirmed both ways: one misspelled core left
    # the single core that DID match sitting on a 10-of-20 floor (the floor is
    # ceil(n / (cores + 1)), so losing two cores triples the third one's share),
    # and a misspelled pool matched nothing, fell through the engine's "pool too
    # thin" relaxation and shipped lineups with two off-pool players each under a
    # maxOffPool of 0. Neither said anything. Now they are reported, and a core
    # that does not match stops the build — that one is your conviction play and
    # a silent substitution is the opposite of what the tool is for.
    known = {normalize_name(p.name) for p in players}
    typed = dict(_raw_names(options.get("pool")))
    typed.update(_raw_names(options.get("cores")))
    show = lambda n: typed.get(n, n)
    unmatched_cores = sorted(show(n) for n in core_names if n not in known)
    unmatched_pool = sorted(show(n) for n in pool_names if n not in known)
    if unmatched_cores:
        return {"error": "These cores don't match any player on the slate: "
                         + ", ".join(unmatched_cores)
                         + ". Fix the spelling (or drop them) and build again — "
                           "carrying on would quietly rebalance the cores that did "
                           "match.",
                "unmatchedCores": unmatched_cores,
                "unmatchedPool": unmatched_pool, "source": source_label}
    for p in players:
        nm = normalize_name(p.name)
        p.core = nm in core_names
        p.in_pool = p.core or (nm in pool_names)
        if p.proj <= 0:
            continue
        if p.core:
            # Cores are the ANCHOR plays — the sharp's picks that keep landing in
            # winners. They used to get a +6% projection/ceiling edge on top; a
            # 7-slate review removed it. Cores DID outperform everything else
            # (30.1 actual vs 24.5 for the rest of the pool vs 12.8 slate-wide) —
            # but they hit 1.01x their projection, i.e. they MEET it rather than
            # beat it. The edge was double-counting a belief already priced into
            # the projection that got them cored. Their pull now comes from
            # min_cores and the exposure floor, which are exposure levers rather
            # than a thumb on the projection.
            p.notes.append("GT core")
        elif pool_names and not p.in_pool:
            p.notes.append("off-pool")

    # Only now is it known who you named, and being named is the licence — see
    # revive_pooled_zeros.
    revived_note = []
    revive_pooled_zeros(players, options.get("minutes") or "", revived_note)

    playable = [p for p in players if p.proj > 0]
    if len(playable) < ROSTER_SIZE:
        return {"error": "Not enough playable players — check the file.",
                "source": source_label}

    cores = [p for p in playable if p.core]
    # A core is the sharp's conviction play — it gets a guaranteed exposure floor
    # in the engine and is never nudged on projection.
    # It's never faded or demoted for grading "weak": the tool grades each core in
    # the coach report so the call is conscious, but the machine never overrides
    # the sharp's pick (that's what buried DiJonai at 1-of-N). Everyone else earns
    # their spot on the data.
    max_off_pool = _int(options.get("maxOffPool"), 0) if pool_names else None
    # Per-player exposure caps — rein in a specific heavy play without lowering
    # the global cap (which on a short slate would needlessly hobble the studs).
    # Each line carries its OWN percentage, because one number for everybody is
    # not how the decision is made: you might want a punt at 10% and a stud at
    # 40% in the same build. A line with no number falls back to the slider,
    # which is what the 🔒 marks on the slate rows still send.
    n_lu = _int(options.get("n"), 20)
    player_caps, cap_missing, cap_bad = _parse_player_caps(
        options.get("capPlayers"), players, _float(options.get("capPct"), 30))
    # Shares -> lineup counts, which is what the engine's select_final compares.
    # Floor of zero, not one: "cap him at 0" has to mean zero, not one lineup.
    player_caps = {i: max(0, int(round(v * n_lu))) for i, v in player_caps.items()}
    # Decide the slate read ONCE, so the engine's salary reserve and the UI badge
    # are the same determination (not two independent computations on different
    # player sets).
    slate_type = _slate_type(players)
    build_report = {}
    # A cap you typed that matched nobody is a warning, not a shrug. Seeded here
    # so it rides the same channel as everything else the build gave up.
    if cap_missing:
        build_report.setdefault("relaxed", []).append(
            "no player on this slate matches these caps, so they are NOT in "
            "force: " + "; ".join(cap_missing))
    if cap_bad:
        build_report.setdefault("relaxed", []).append(
            "these cap lines could not be read and were ignored: "
            + "; ".join(cap_bad))
    # Never revive anyone quietly — a resurrected scratch is the failure mode.
    for note in revived_note:
        build_report.setdefault("relaxed", []).append(note)
    lineups = optimize_gpp(
        players,
        n=_int(options.get("n"), 20),
        pool_size=max(120, _int(options.get("n"), 20) * 8),
        # Per-lineup team cap of 3: no single team can be more than half a roster.
        # Raising it to 4 survived the held-out test on CASH (72 against 67) but
        # it is also what stopped the tool building the 8-25 winner, and first
        # place is the objective here, so it stays at 3 as a setting you can move
        # rather than a default that quietly trades jackpots for min-cashes.
        max_per_team=_int(options.get("maxPerTeam"), 3),
        # Ownership lean. POSITIVE leans toward the field's consensus, negative
        # fades it. Now defaults to ZERO — see simulate_and_score for the numbers.
        # The short version: at +0.35 we sat at the 75th within-slate ownership
        # percentile and the top-1% tier sits at the 63rd, so leaning in was
        # walking past the winners into the crowd. Neutral was picked on all 23
        # held-out folds, on both cash and dollars. Fading is worse still.
        own_lean=_float(options.get("ownLean"), 0.0),
        n_sims=_int(options.get("sims"), 5000),
        cores=cores,
        # Anchor rule: every lineup built around at least this many cores (which
        # ones vary across the set). Default 1 when cores are set — held out, the
        # set built with this on cashed 3 more lineups per 23 slates than the same
        # tool with it off, in the same direction at 12 and at 20 entries.
        min_cores=(_int(options.get("minCores"), 1) if cores else 0),
        max_off_pool=max_off_pool,
        stars_and_scrubs=(slate_type == "stars-and-scrubs"),
        player_caps=player_caps,
        # The two-game shape rules, and a switch that now actually switches.
        slate_rules=str(options.get("slateRules", "on")) != "off",
        # Salary floor. Was 800 on the field's leftover table; measured on our
        # own builds across 18 slates it cost 1.2 of mean and 5 cashes and bought
        # no tail, so it sits at the level that is measured identical to having
        # no floor at all. See MAX_LEFTOVER in the engine for the numbers.
        max_leftover=_int(options.get("maxLeftover"), 2000),
        # The engine has always taken a seed; nothing ever passed it, so every
        # build was seed 0 and "run it on eight seeds" quietly produced the same
        # run eight times. An exponent test earlier this month was nearly read as
        # a +$94 result on exactly that basis. A knob you cannot vary is a knob
        # you cannot measure, so it is wired through — default unchanged.
        seed=_int(options.get("seed"), 0),
        report=build_report,
    )

    result = {
        "source": source_label,
        "slateType": slate_type,
        "poolActive": bool(pool_names),
        "removed": removed,
        # Everything the build had to give up, said out loud. A thin board used
        # to return 3 lineups for a requested 12 with no error and no note.
        "warnings": _build_warnings(build_report, unmatched_pool,
                                    _int(options.get("n"), 20), lineups),
        "unmatchedPool": unmatched_pool,
        "coach": _coach(playable, lineups, options, slate_type, had_minutes,
                        removed_info, pool_names),
        "slate": {
            "date": _slate_date(),
            "games": sorted({p.game for p in players if p.game}),
        },
        "out": [p.name for p in players if p.status == "OUT"][:40],
        "players": [{
            "name": p.name, "team": p.team, "pos": p.pos, "salary": p.salary,
            "game": p.game, "proj": round(p.proj, 1), "floor": round(p.floor, 1),
            "ceil": round(p.ceil, 1), "own": round(p.ownership, 1),
            "min": round(p.minutes, 0), "stuffer": round(p.stuffer, 1), "risk": p.risk,
            "core": p.core, "starter": p.starter,
            "notes": "; ".join(p.notes),
        } for p in sorted(playable, key=lambda p: -p.proj)],
        "lineups": [{
            "rank": i + 1, "salary": lu.salary, "proj": lu.proj,
            "ceiling": round(lu.metrics.get("ceiling", 0), 1),
            "mean": round(lu.metrics.get("mean", 0), 1),
            "totalOwn": lu.total_own,
            "cores": sum(1 for p in lu.players if p.core),
            "risk": sum(1 for p in lu.players if p.risk and not p.core),
            "stacks": _stacks_payload(lu),
            "offPool": sum(1 for p in lu.players if not p.in_pool),
            "players": _slots_payload(lu.dk_slots()),
            "upload": [_upload_str(p) for p in lu.dk_slots()],
            "alt": _alt_payload(lu.alt),
        } for i, lu in enumerate(lineups)],
    }
    _log_build(result, players, lineups, options)
    return result


# ---------------- build log ----------------
# Every review since the first one has asked for this. Each build appends one
# JSON line so a later analysis can ask what we actually did on a given day
# rather than reconstructing it from the DK export. We log only what is known
# BEFORE the slate runs; finishes, actual points and real ownership come from
# the post-slate LineStar and DK standings files, joined on the slate date.
#
# Successive records for one date are the audit trail for mid-day core/pool
# edits: each carries its own timestamp and the core/pool set in force at the
# time, so a change shows up as a new record rather than overwriting anything.
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "logs", "builds.jsonl")
# Late swap keeps its own file. One record per run, and every record carries
# BOTH policies' decisions — the one that was acted on and the one that was not —
# so a single night grades news-only against free re-optimisation instead of
# only telling you how the policy you happened to run did.
SWAP_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "logs", "swaps.jsonl")


def _lineup_log(lu, med_implied, game_totals):
    """The shape of one lineup, in the terms the reviews keep asking about."""
    teams, games = {}, {}
    for p in lu.players:
        teams[p.team] = teams.get(p.team, 0) + 1
        games[p.game] = games.get(p.game, 0) + 1
    top_team = max(teams, key=lambda t: teams[t])
    top_game = max(games, key=lambda g: games[g])
    implied = max((p.implied for p in lu.players if p.team == top_team), default=0.0)
    return {
        "salary": lu.salary,
        "leftover": SALARY_CAP - lu.salary,
        "proj": lu.proj,
        "ceiling": round(lu.metrics.get("ceiling", 0), 1),
        "totalOwn": lu.total_own,
        # the two counts the review split hardest on, logged so the next one
        # doesn't have to reconstruct them from the DK export
        "subTenOwned": sum(1 for p in lu.players if p.ownership < SUB10_OWN),
        "studs": sum(1 for p in lu.players if p.salary >= STUD_SALARY),
        "teamStack": teams[top_team],
        "stackTeam": top_team,
        "stackImplied": round(implied, 1),
        # the cut the field data keeps splitting on: a 3-stack of a low-total
        # team is the worst construction on the board
        "stackAboveMedian": (implied >= med_implied) if teams[top_team] >= 3 else None,
        "gameStack": games[top_game],
        "stackGame": top_game,
        # the whole game's combined implied total, not just the rostered half
        "gameTotal": round(game_totals.get(top_game, 0.0), 1),
        "cores": [p.name for p in lu.players if p.core],
        "offPool": [p.name for p in lu.players if not p.in_pool],
        "players": [{
            "name": p.name, "team": p.team, "pos": p.pos, "salary": p.salary,
            "proj": round(p.proj, 1), "ceil": round(p.ceil, 1),
            # The RAW LineStar number and the starter flag, alongside the blended
            # projection. Late swap compares raw to raw: the blend mixes in a
            # season average, and a season average is precisely the thing that
            # does not know a player was benched an hour ago, so comparing
            # blended numbers hides the news the comparison exists to find.
            "lsProj": round(p.ls_proj, 1), "starter": p.starter,
            "own": round(p.ownership, 1), "min": round(p.minutes, 1),
            "implied": round(p.implied, 1), "core": p.core, "pool": p.in_pool,
        } for p in lu.dk_slots()],
    }


def _log_build(result, players, lineups, options):
    """Append one record per build. Never let logging break a build."""
    try:
        from datetime import datetime
        team_implied = {p.team: p.implied for p in players if p.implied > 0}
        implieds = sorted(team_implied.values())
        med = implieds[len(implieds) // 2] if implieds else 0.0
        game_totals = {}
        for p in players:
            if p.game and p.team in team_implied:
                game_totals.setdefault(p.game, {})[p.team] = team_implied[p.team]
        game_totals = {g: sum(v.values()) for g, v in game_totals.items()}
        record = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "slate": result.get("slate", {}).get("date"),
            "games": result.get("slate", {}).get("games", []),
            "slateType": result.get("slateType"),
            "medianImplied": round(med, 1),
            # Which projection produced this build. Every review has asked for
            # it: without it a change in results can be described across builds
            # but never attributed to the input that caused it.
            "projection": {
                "source": result.get("source"),
                "ppgWeight": PPG_WEIGHT,
                "dailyDisagree": DAILY_DISAGREE,
                "ceilingWeight": CEILING_WEIGHT,
            },
            "options": {k: options.get(k) for k in (
                "n", "ownLean", "maxPerTeam", "minCores", "maxOffPool",
                "slateRules", "capPct", "capPlayers", "maxLeftover")
                if k in options},
            "gateMinutes": GATE_MINUTES,
            "cores": [p.name for p in players if p.core],
            "pool": [p.name for p in players if p.in_pool],
            "removed": result.get("removed", []),
            # What the build had to give up, kept with the build rather than only
            # shown on screen — the next review reads this file, not the browser.
            "warnings": result.get("warnings", []),
            "lineups": [_lineup_log(lu, med, game_totals) for lu in lineups],
        }
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:  # noqa: BLE001
        # Still never break a build over logging — but do not disappear either.
        # This was a bare `except Exception: pass`, so a schema, disk or
        # permission problem wrote no line and said nothing, and late swap then
        # silently read an OLDER build as its news baseline. Same class as the two
        # silent failures this project has already shipped.
        result.setdefault("warnings", []).append(
            f"Build log not written ({exc.__class__.__name__}: {exc}). Late swap's "
            f"news detection reads that log, so it may fall back to an older build "
            f"of this slate.")


# ---------------- DK entries file (DKEntries*.csv) ----------------
# One DK export carries BOTH your contest entries (Entry ID + the 6 filled slots)
# AND the full player pool with real DK IDs, salaries and game start times. That
# makes it the best input we have: real IDs mean a re-uploadable export, and the
# start times mean late swap can work out which games are locked on its own.
def _dk_game_start(info):
    """'MIN@PDX 08/12/2026 10:00PM ET' -> ('MIN@PDX', datetime | None)."""
    import re
    from datetime import datetime
    m = re.match(r"\s*(\w+@\w+)\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)",
                 info or "")
    if not m:
        return (info or "").strip().split(" ")[0], None
    try:
        dt = datetime.strptime(f"{m.group(2)} {m.group(3).replace(' ', '')}",
                               "%m/%d/%Y %I:%M%p")
    except ValueError:
        dt = None
    return m.group(1), dt


def _dk_name_id(cell):
    """'Paige Bueckers (43810941)' -> ('Paige Bueckers', '43810941', False).

    Once a player's game tips, DK appends a ' (LOCKED)' marker to the same cell —
    'Marina Mabrey (43810951) (LOCKED)'. Strip it and return it as a flag: it's a
    per-PLAYER lock straight from DK, which is more precise than inferring locks
    from game start times.
    """
    import re
    s = (cell or "").strip()
    locked = bool(re.search(r"\(LOCKED\)\s*$", s, re.I))
    if locked:
        s = re.sub(r"\s*\(LOCKED\)\s*$", "", s, flags=re.I)
    m = re.match(r"\s*(.+?)\s*\((\d+)\)\s*$", s)
    return (m.group(1), m.group(2), locked) if m else (s, "", locked)


def parse_dk_entries(text):
    """Parse a DK entries export into {slots, entries, pool, games}.

    slots   — the roster order DK expects, read from the header (e.g. G,G,F,F,F,UTIL)
    entries — [{entryId, contest, contestId, fee, names[]}] (names empty = reservation)
    pool    — {normalized name: {dkId, name, guard, salary, game, start, team}}
    games   — {game key: ISO start time or None}
    """
    import csv as _csv
    import io
    import re
    rows = list(_csv.reader(io.StringIO((text or "").lstrip("﻿"))))
    slots, entries, pool, games = [], [], {}, {}
    pi = None  # column index of 'Name + ID' in the embedded player pool
    for r in rows:
        if not r:
            continue
        if pi is None:
            for i, c in enumerate(r):
                if c.strip() == "Name + ID":
                    pi = i
                    break
            # NOTE: no `continue` here. DK embeds the player-pool header partway
            # down the file, on a row that is ALSO a real contest entry — skipping
            # it silently dropped one of the user's entries. The pool-data check
            # below is guarded on the ID column being numeric, so the header row
            # can't be mistaken for a player anyway.
        if r[0].strip() == "Entry ID" and len(r) > 9:
            slots = [c.strip() for c in r[4:10] if c.strip()]
        elif r[0].strip().isdigit() and len(r) > 9:
            parsed = [_dk_name_id(c) for c in r[4:10] if c.strip()]
            entries.append({"entryId": r[0].strip(), "contest": r[1].strip(),
                            "contestId": r[2].strip(), "fee": r[3].strip(),
                            "names": [p[0] for p in parsed],
                            "locked": [p[0] for p in parsed if p[2]]})
        if pi is not None and len(r) > pi + 7 and r[pi + 2].strip().isdigit():
            name = r[pi + 1].strip()
            info = r[pi + 5].strip()
            game, start = _dk_game_start(info)
            # DK replaces the matchup with 'In Progress' (or 'Final') once a game
            # tips, so there's no game key to read — fall back to the per-player
            # (LOCKED) marker, which is present on exactly those players.
            in_play = not re.match(r"^\w+@\w+", info)
            locked = _dk_name_id(r[pi])[2] or in_play
            pool[normalize_name(name)] = {
                "dkId": r[pi + 2].strip(), "name": name,
                "guard": r[pi + 3].strip().upper().startswith("G"),
                "salary": int(_f(r[pi + 4])), "game": None if in_play else game,
                "start": start.isoformat() if start else None,
                "team": r[pi + 6].strip(), "locked": locked,
            }
            if not in_play and (game not in games or (start and not games.get(game))):
                games[game] = start.isoformat() if start else None
    return {"slots": slots or ["G", "G", "F", "F", "F", "UTIL"],
            "entries": entries, "pool": pool, "games": games}


def _util_holds_latest(roster, slots, pool, locked_names=()):
    """Re-slot so the UTIL spot holds the player from the LAST game to tip.

    Same six players — only which slot each one sits in changes, which DK allows
    and which costs nothing. It buys late-swap flexibility: UTIL is the one spot
    that takes a guard OR a forward, so it should be the last to lock. If UTIL
    holds an early-game player it locks early and the only slot still open in the
    final game is a G or F, which can only be refilled from that position; with
    the late-game player in UTIL the open slot accepts anyone.

    Locked players never move — DK pins a locked player to its slot.
    """
    if "UTIL" not in slots:
        return roster
    util = slots.index("UTIL")
    roster = list(roster)
    locked_names = set(locked_names)

    def movable(i):
        return normalize_name(roster[i].name) not in locked_names

    def start_of(i):
        rec = pool.get(normalize_name(roster[i].name)) or {}
        return rec.get("start") or ""   # in-progress/unknown sorts earliest

    if not movable(util):
        return roster
    # Try the latest-starting movable player first, then work back — a swap is
    # only legal if whoever currently holds UTIL can fill the vacated slot.
    for i in sorted((j for j in range(len(roster)) if movable(j)),
                    key=start_of, reverse=True):
        if i == util:
            return roster                      # already right
        if not start_of(i) or start_of(i) <= start_of(util):
            return roster                      # nothing later than UTIL already is
        occ = roster[util]
        if slots[i] == "G" and not occ.is_guard:
            continue
        if slots[i] == "F" and occ.is_guard:
            continue
        roster[i], roster[util] = roster[util], roster[i]
        return roster
    return roster


def _roster_problem(filled):
    """Why DK would reject this row, or "" if it wouldn't.

    The writer used to do no legality check of its own at all: handed a 4-guard,
    $62,700 roster it slotted a guard into a forward cell and wrote the row with
    an empty warning. Today's engine cannot produce such a roster, so this is a
    belt for a path that has no braces — and the late-swap and hand-edit paths
    reach this same writer.
    """
    if any(p is None for p in filled):
        return "an empty slot"
    ids = [p.get("dkId") for p in filled]
    if len(set(ids)) != len(ids):
        return "the same player twice"
    guards = sum(1 for p in filled if p.get("guard"))
    if guards < MIN_GUARDS:
        return f"{guards} guards, DK needs at least {MIN_GUARDS}"
    if len(filled) - guards < MIN_FORWARDS:
        return f"{len(filled) - guards} forwards, DK needs at least {MIN_FORWARDS}"
    salary = sum(int(p.get("salary") or 0) for p in filled)
    if salary > SALARY_CAP:
        return f"${salary:,} of salary, over the ${SALARY_CAP:,} cap"
    # Only checkable when every game is known. DK blanks the matchup once a game
    # tips, so mid-slate a legal roster can look single-game here; don't cry wolf
    # at the exact moment late swap is writing the file.
    games = [p.get("game") for p in filled]
    if all(games) and len(set(games)) < 2:
        return "only one game — DK requires two"
    return ""


def build_dk_upload(dk, lineups_names):
    """Fill our generated lineups into the DK entries file's slot order and return
    re-uploadable CSV text. Uses the file's REAL DK IDs, so it imports directly
    instead of needing manual entry."""
    slots, pool = dk["slots"], dk["pool"]
    entries = dk["entries"]
    if not entries:
        return None, "No contest entries found in that DK file."
    lines = ["Entry ID,Contest Name,Contest ID,Entry Fee," + ",".join(slots)]
    missing, illegal = set(), []
    n = min(len(entries), len(lineups_names))
    for e, names in zip(entries[:n], lineups_names[:n]):
        recs = []
        for nm in names:
            p = pool.get(normalize_name(nm))
            if not p:
                missing.add(nm)
            recs.append(p or {"name": nm, "dkId": "", "guard": False})
        guards = [p for p in recs if p.get("guard")]
        forwards = [p for p in recs if not p.get("guard")]
        filled, used = [], set()
        for s in slots:  # G/F slots first, UTIL takes whoever's left
            src = guards if s.upper().startswith("G") else forwards
            pick = next((p for p in src if id(p) not in used), None)
            if s.upper() == "UTIL" or pick is None:
                pick = next((p for p in recs if id(p) not in used), None)
            if pick is not None:
                used.add(id(pick))
            filled.append(pick)
        # UTIL should carry the latest game, so it stays swappable longest.
        class _P:  # the re-slotter works on objects with .name/.is_guard
            def __init__(s, r):
                s.name, s.is_guard, s.rec = r["name"], r.get("guard", False), r
        wrapped = [_P(p) for p in filled if p]
        if len(wrapped) == len(filled):
            filled = [w.rec for w in _util_holds_latest(wrapped, slots, pool)]
        bad = _roster_problem(filled)
        if bad:
            illegal.append(f"entry {e['entryId']}: {bad}")
        cells = [f'"{p["name"]} ({p["dkId"]})"' if p and p.get("dkId")
                 else f'"{p["name"]}"' if p else "" for p in filled]
        cname = e["contest"].replace('"', '""')
        lines.append(f'{e["entryId"]},"{cname}",{e["contestId"]},{e["fee"]},'
                     + ",".join(cells))
    # A cell without a DK ID is a row DraftKings rejects. This used to be written
    # anyway and reported as a success — a green tick followed by the reason the
    # file would not import. Reproduced three ways: a truncated export, the wrong
    # slate's export, and a lineup holding a name that is not in the pool. Refuse
    # instead: a missing file is a problem you can fix in a minute, a file that
    # looks filled and silently fails at DK costs the whole night.
    if missing:
        return None, ("Not written — no DK ID on this slate for: "
                      + ", ".join(sorted(missing))
                      + ". That is usually the wrong slate's entries file, or a "
                        "truncated download. Re-export from DraftKings and try "
                        "again.")
    if illegal:
        return None, ("Not written — these rosters are not legal for DK: "
                      + "; ".join(illegal[:5])
                      + ("" if len(illegal) <= 5 else f" (+{len(illegal) - 5} more)"))
    notes = []
    if len(lineups_names) > len(entries):
        notes.append(f"{len(lineups_names)} lineups, {len(entries)} entries — filled {n}.")
    elif len(entries) > len(lineups_names):
        notes.append(f"{len(lineups_names)} lineups, {len(entries)} entries — "
                     f"{len(entries) - len(lineups_names)} left unchanged.")
    return "\n".join(lines) + "\n", " · ".join(notes)


def run_dk_fill(dk_text, lineups_names):
    dk = parse_dk_entries(dk_text)
    if not dk["pool"]:
        return {"error": "Couldn't read that as a DK entries file (no player pool found)."}
    if not lineups_names:
        return {"error": "Generate lineups first, then fill the DK file."}
    csv_text, warn = build_dk_upload(dk, lineups_names)
    if csv_text is None:
        return {"error": warn}
    return {"csv": csv_text, "warn": warn,
            "filled": min(len(dk["entries"]), len(lineups_names))}


# ---------------- late swap ----------------
# React to lineup news on already-entered lineups mid-slate. Locked players are
# fixed constraints; the open slots plus whatever salary they leave are a smaller
# instance of the problem the main engine already solves, so we score candidate
# rosters with the SAME Monte-Carlo simulator rather than by raw projection.
#
# "React to news" is the whole design, and it is narrower than what this used to
# do. The 24-contest review measured both halves of the old behaviour: swaps
# forced by a scratch or a benching gained 24.5 points an entry and were positive
# 15 times out of 15, while re-optimising slots nobody had said anything about
# averaged 3.4 with 15 of 29 positive — statistically nothing, and on one night
# it cost 125 points across 8 entries. So by default a lineup is left alone
# unless something changed about a player in it, and even then only that player
# (plus one spare slot, so the replacement can be afforded) is allowed to move.
# See SWAP_NEWS_PROJ_DROP for how "changed" is decided.
#
# The one addition over a normal build is position awareness. By late swap, the
# locked players have already scored (LineStar's `Scored` column carries live
# actuals), so we know whether a lineup is running ahead of or behind its own
# projection, and we know from ownership whether the field took the same hit. A
# lineup that is uniquely behind has to swing for upside to win; one that is
# uniquely ahead should protect. That becomes ONE dial per lineup — aggression —
# which tilts its ranking between the median and the ceiling. Nothing else about
# selection uses ownership: the 5-slate back-test showed projected ownership is
# noise for picking players, and it is only used here to read our position
# relative to the field, which is a different job.
_LS_SLOTS = ["G", "G", "F", "F", "F", "UTIL"]
# Late swap is deliberately CONSERVATIVE after review. On the one slate where
# swaps really fired it cost 125 points across 11 lineups, systematically trading
# high scorers for lower-owned busts (Iriafen 40 -> Onyenwere 8, Citron 46 ->
# Young 23). n=1, so that's a red flag rather than proof — but the mechanism it
# used to chase is the same ownership-fading the wider review found -EV, so the
# bar to touch a lineup is now high enough that only real news moves it.
SWAP_MIN_GAIN = 6.0        # was 2.0 — sub-noise churn is how the damage happened
# The bar a move must clear when NOTHING was said about anyone in the lineup.
#
# News-only was the whole policy, and it is the wrong shape. Every trigger in
# _news_names is about a player in YOUR lineup getting worse; nothing fires when
# a player you do not hold gets better. LineStar shaves a whole team when it does
# not know the starting five, then resolves hours later — your player never got
# worse, so there was no news, so the lineup held while the board underneath it
# rearranged. And post-lock is the moment of MAXIMUM information, not minimum:
# real ownership, real scores and a real leaderboard for every game that started,
# against a builder that had only a vendor's simulation of 10,000 strangers.
#
# THE ASSUMPTION UNDERNEATH ALL OF THIS IS NOW TESTED. "The later number is
# better" was reasoning, not evidence, until the archive was asked. Ten slates
# with a lock file, a mid-slate pull and actuals, restricted to players whose
# game had not started (287 of them, the only ones a swap can reach):
#
#   27 players moved 4% or more.  MAE against actual: lock 10.24, late 4.76,
#   late better on 9 of 10 slates. Excluding the ruled-out and newly-projected,
#   the 17 genuine revisions go 9.31 -> 7.05, late better on 12 of 17.
#   UP moves specifically: 11 of them, 9.86 -> 7.12, late better on 9 of 11.
#   The seven 25%+ rises projected 9.1 at lock, 17.2 late, and SCORED 19.5.
#
# And the mechanism is what we guessed, not scatter: on 9 of 10 slates the
# most-affected team held 50-100% of that slate's movers while holding 18-56% of
# its players, 5 of 7 big up-movers had a teammate revised DOWN in the same file,
# and the 9 bench-to-starter flips realised +9.3 against lock while the 4
# starter-to-bench flips realised -12.2. It is a starting-five resolving.
#
# Sub-threshold moves are noise in both directions — 65 small ups realised -0.7,
# 51 small downs -0.1 — which is what the news thresholds are for.
#
# But the reason news-only existed is real, and it is in the numbers:
#
#   news-forced swaps        simulated gain 24-45   ->   8 for 8 positive
#   free re-optimisation     simulated gain  6-13   ->   realised -30 to +35
#
# At a 6-13 point edge the simulator has no discriminating power at all. What
# separated the good swaps from the churn was the SIZE of the gain, not the
# reason for it — news was only ever a proxy for "something moved 20+ points".
# So the gate goes and the bar takes over: news keeps the low bar it earned,
# everything else has to clear the noise floor the data actually measured.
#
# MEASURED, and 20 was too high. The seven mid-slate snapshots were replayed on
# this code — 97 entries, 15,915 scored candidate rosters — taking each entry's
# best discretionary candidate with the rank gate applied, and scoring the
# REALISED change rather than the simulated one:
#
#   bar   moves   realised   mean    positive   slates positive
#     6      30       +359   +12.0     26/30          6 of 7
#    10      22       +323   +14.7     20/22          6 of 6
#    12      17       +284   +16.7     15/17          6 of 6
#    15      11       +156   +14.1      9/11          5 of 5
#    20       8        +98   +12.2      6/8           3 of 3
#
# By band the separation is sharp: moves worth 6-10 simulated points realised
# +4.4 (6 of 8 positive), 10-15 realised +15.2 (11 of 11), 15-20 realised +19.2
# (3 of 3). Realised change parts from zero at about 10, not 20 — and a bar of 20
# left 14 moves worth roughly +225 on the table, ALL FOURTEEN POSITIVE.
#
# So it drops to 10, which takes every good move and still excludes the marginal
# 6-10 band. The review's own read was "10-12 as the estimate, not a constant",
# on 22 entries across 7 slates. Every decision is still logged with its gain, so
# this moves again when there are more nights.
SWAP_DISCRETIONARY_GAIN = 10.0
SWAP_OFF_POOL_MIN_GAIN = 12.0   # projection a player OUTSIDE the pool must add
SWAP_MAX_LEFTOVER = 2000   # match the build's salary floor; still a preference
                           # rather than a filter, since locks can strand money.
                           # Moved with the build's floor when that was measured
                           # on 18 slates and found to cost cashes for no tail —
                           # this path was never tested separately, and leaving
                           # it at 700 would have had late swap refusing rosters
                           # on exactly the rule the build had just dropped.
                           # Untested here in its own right: it gates a swap
                           # that strands salary unless the gain is large, and
                           # at 2,000 it will gate far less often.
SWAP_TOP_PER_POS = 14      # candidate breadth per position (keeps combos sane)
SWAP_MAX_PER_TEAM = 3      # same team-correlation cap the build uses


def _max_per_team(roster):
    counts = {}
    for p in roster:
        counts[p.team] = counts.get(p.team, 0) + 1
    return max(counts.values()) if counts else 0


def _max_per_game(roster):
    counts = {}
    for p in roster:
        counts[p.game] = counts.get(p.game, 0) + 1
    return max(counts.values()) if counts else 0


def _pace_read(locked, scored):
    """How a lineup stands, from its own locked players.

    Returns (banked, expected, deficit, field_weight). `deficit` is actual minus
    projected on the players already played — negative means running behind.
    `field_weight` scales that by ownership: a bust on a highly-owned player hurt
    the whole field, so it barely moves us; a bust on a play nobody had leaves us
    uniquely behind and is worth reacting to.
    """
    banked = expected = 0.0
    wsum = wtot = 0.0
    for p in locked:
        act = scored.get(normalize_name(p.name))
        if act is None:
            continue
        banked += act
        expected += p.proj
        own = max(min(p.ownership, 100.0), 1.0) / 100.0
        # low ownership -> this swing is ours alone -> weight it fully
        wsum += (act - p.proj) * (1.0 - own)
        wtot += abs(act - p.proj) * (1.0 - own) or 0.0
    return banked, expected, banked - expected, wsum


def _aggression(deficit_weighted):
    """Map a field-adjusted deficit to a 0..1 dial.

    0.5 = neutral (rank on the tool's usual ceiling). Above 0.5 chases upside
    because we're uniquely behind; below 0.5 protects because we're uniquely
    ahead. Deliberately gentle and clamped — this is an unvalidated read, so it
    nudges the ranking rather than overriding it.

    This is the FALLBACK read, used when no contest file is supplied. With the
    standings we replace it with the real thing: an actual leaderboard position.
    """
    return max(0.15, min(0.85, 0.5 - deficit_weighted / 60.0))


# DK writes a roster as "F Name F Name F Name G Name G Name UTIL Name", with
# LOCKED standing in for a player whose game has not started. UTIL comes first in
# the alternation so it is not matched as a bare U-less token; the capture group
# keeps the slot labels, which is what lets us tell this file from another sport's.
_STANDINGS_SLOT = re.compile(r"\s*\b(UTIL|G|F)\s+")


def _standings_revealed(cell, slots):
    """-> the set of normalized names DK has revealed on this roster, or None if
    the cell isn't a roster layout we recognise.

    A SET, not a slot-by-slot list, because a set is all this file contains.
    Measured on the 5,945 rosters of contest 195934561, unanimously:

      * DK writes the roster grouped by position, F F F G G UTIL — NOT the
        G G F F F UTIL that the entries export asks you to fill in.
      * Inside each group the revealed players come first, ordered by salary
        descending, and the LOCKED placeholders fill the rest of the group.

    So the cell says WHO has tipped. It cannot say which slot any of them sits
    in — DK has already reordered them, and nothing in the file records where
    they started. The old code read it slot by slot anyway, against the entries
    file's slot order, which is how 2026-09-22 put a locked forward into a G
    slot, left her in the lineup twice and took the roster to $55,500 against a
    $50,000 cap. It went unnoticed for so long because no standings export in
    the archive contains a single LOCKED — every one was pulled after its
    contest finished, when the groups are full and any read looks plausible.

    A revealed name is not merely newer information, it is FINAL: DK only
    reveals a player once their game has tipped, and a tipped player cannot be
    taken out of a lineup. So where this file has a name, that player is
    entered, full stop — which is exactly the claim a set can carry.
    """
    parts = _STANDINGS_SLOT.split(cell or "")
    # split on a capturing group -> [lead, label, name, label, name, ...]
    if not parts or parts[0].strip():
        return None                      # text before the first slot label
    labels, names = parts[1::2], [p.strip() for p in parts[2::2]]
    if len(labels) != len(names) or sorted(labels) != sorted(slots):
        return None                      # not this slate's roster shape at all
    return {normalize_name(n) for n in names if n not in ("", "LOCKED")}


def _roster_illegal(names, revealed, slots, pool):
    """Why this roster can't be what is entered on DK, or "".

    Every repair drawn from the contest file goes through here before it is
    believed. Taking DK's word over the entries export is right, but only once
    we are sure we read DK correctly, and a bad read rewrites a roster silently.
    So the result has to survive what DK itself guarantees. Not defensive
    padding: the 2026-09-22 misread tripped four of these five.
    """
    norm = [normalize_name(n) for n in names]
    if len(set(norm)) != len(norm):
        return "it puts the same player in two slots"
    recs = [pool.get(n) for n in norm]
    for nm, rec in zip(names, recs):
        if rec is None:
            return f"{nm} is not in this slate's player pool"
    for slot, rec, nm in zip(slots, recs, names):
        if slot == "G" and not rec["guard"]:
            return f"it puts {nm}, a forward, in a G slot"
        if slot == "F" and rec["guard"]:
            return f"it puts {nm}, a guard, in an F slot"
    salary = sum(int(rec["salary"] or 0) for rec in recs)
    if salary > SALARY_CAP:
        return f"it costs ${salary:,}, over the ${SALARY_CAP:,} cap"
    # A player DK has revealed is playing for this entry and cannot be taken out
    # of it. If a repair drops one, the repair is wrong.
    gone = [n for n in revealed if n not in norm]
    if gone:
        missing = (pool.get(gone[0]) or {}).get("name") or gone[0]
        return f"it drops {missing}, who DK says is already playing for you"
    return ""


def _reconcile_with_dk(names, revealed, slots, pool):
    """-> (roster, ""), (None, "") when nothing needs changing, or (None, why).

    The contest file is what DK holds RIGHT NOW, including a hand edit made
    after the entries export was downloaded — that is the whole reason to read
    it, and a stale roster is worse than useless once you have fixed a lineup by
    hand. But it reports a SET of tipped players, not slots (see
    _standings_revealed), so reconciling is a question about membership:

      add   DK has them, our roster doesn't -> the entries file is stale
      drop  our roster has them, their game has tipped, DK does NOT list them
            -> they are provably not entered; a tipped player DK does not show
               for this entry is a player this entry does not have

    `drop` is a proof, not a guess, which is what makes the repair safe. When
    the two don't balance, the player who was replaced had not tipped yet and
    nothing in this file says who they were — so say so and leave the roster
    alone rather than picking a victim. Re-downloading the entries file is the
    fix for that, and it is a one-line ask.
    """
    import itertools
    ours = [normalize_name(n) for n in names]
    add = sorted(n for n in revealed if n not in ours)
    if not add:
        return None, ""                  # DK shows nobody we don't already have
    drop = [i for i, n in enumerate(ours)
            if (pool.get(n) or {}).get("locked") and n not in revealed]
    nice = lambda n: (pool.get(n) or {}).get("name") or n
    if len(drop) != len(add):
        have = ", ".join(nice(n) for n in add)
        return None, (f"DK has {have} on this entry and your file doesn't, but "
                      f"the file doesn't say who they replaced")
    # Which slot each one lands in is ours to choose — DK regrouped the roster
    # before writing it, so the file cannot tell us. Any assignment DK would
    # accept is the right answer. Prefer the smallest change: drop the new
    # player straight into the slot the old one held.
    held = set(drop)
    for perm in itertools.permutations(add):
        cand = list(names)
        for i, n in zip(drop, perm):
            cand[i] = nice(n)
        if not _roster_illegal(cand, revealed, slots, pool):
            return cand, ""
    # That fails whenever the swap crossed a position — a forward out for a
    # guard leaves a guard standing in an F slot. DK re-slots freely and so may
    # we, with one thing fixed: a player whose game has tipped is pinned to her
    # slot, and the entries export is DK's own word on which slot that is.
    pinned = {i for i, n in enumerate(ours)
              if i not in held and (pool.get(n) or {}).get("locked")}
    free = [i for i in range(len(names)) if i not in pinned]
    movable = ([n for i, n in enumerate(names) if i not in pinned and i not in held]
               + [nice(n) for n in add])
    for perm in itertools.permutations(movable):
        cand = list(names)
        for i, n in zip(free, perm):
            cand[i] = n
        if not _roster_illegal(cand, revealed, slots, pool):
            return cand, ""
    return None, ("no legal roster fits " + ", ".join(nice(n) for n in add)
                  + " around the players of yours that have already tipped")


def parse_contest_standings(text, slots=None):
    """Parse a DK contest-standings export.

    Two blocks share the file: the live leaderboard (Rank, EntryId, Points,
    Lineup) and a per-player summary (Player, %Drafted, FPTS). Note what DK does
    and doesn't reveal — an opponent's players are hidden until their game
    starts, so every lineup shows a 'LOCKED' placeholder per still-to-come slot.
    That means we can count how many slots each rival has left, but not who is in
    them, and the %Drafted block only covers players already revealed. Real
    ownership for the players we might swap TO therefore isn't available; we use
    this for standing, and keep LineStar's projected ownership for differentiation.
    """
    import csv as _csv
    import io
    rows = list(_csv.reader(io.StringIO((text or "").lstrip("﻿"))))
    slots = list(slots or _LS_SLOTS)
    entries, own = [], {}
    for r in rows[1:]:
        if len(r) > 5 and r[0].strip().isdigit():
            entries.append({
                "rank": int(r[0].strip()),
                "entryId": r[1].strip(),
                "points": _f(r[4]),
                "hidden": r[5].count("LOCKED"),
                # The names DK has actually revealed on this roster. For our own
                # entries this is the live truth about what is entered — it is
                # what DK holds right now, including a hand edit made after the
                # entries file was exported. Kept so late swap can notice that
                # the entries file it was given is out of date. None means the
                # cell was not in a layout we recognise.
                "revealed": _standings_revealed(r[5], slots),
                # Kept verbatim so an unrecognised layout can be shown rather
                # than merely counted — this is the one thing that makes a
                # format we have never seen diagnosable instead of a shrug.
                "lineupCell": r[5].strip(),
            })
        # The player block lists each player once PER ROSTER SLOT (A'ja shows up
        # as F 57.2% and again as UTIL 1.0%), so true ownership is the sum across
        # a player's rows — taking one row undercounts badly.
        if len(r) > 9 and r[7].strip() and r[9].strip().endswith("%"):
            n = normalize_name(r[7].strip())
            own[n] = own.get(n, 0.0) + _f(r[9].strip().rstrip("%"))
    return {"entries": entries, "ownership": own, "field": len(entries)}


def _avg_open_slot(players, locked_names):
    """Ownership-weighted mean projection of a still-to-play slot — used to
    estimate what rivals' hidden slots will add."""
    live = [p for p in players
            if p.proj > 0 and normalize_name(p.name) not in locked_names]
    if not live:
        return 0.0
    wt = sum(p.ownership for p in live) or 1.0
    return sum(p.proj * p.ownership for p in live) / wt


# Where a projected finish sits in the field, in percent, at the two anchors that
# matter: in contention to win, and on the cash line.
SWAP_WIN_PCT = 1.0
SWAP_CASH_PCT = 20.0
SWAP_CHASE_PCT = 70.0   # at or below this deep, commit fully to chasing


def _field_finals(contest, avg_slot, exclude_ids=()):
    """Every rival's projected finish, ascending: what they've banked plus their
    still-hidden slots at an average slot's value. Our own entries are excluded so
    we're not ranked against ourselves."""
    return sorted(e["points"] + e["hidden"] * avg_slot
                  for e in contest["entries"] if e["entryId"] not in exclude_ids)


def _aggression_from_rank(my_final, field_sorted):
    """Aggression from where THIS lineup projects to finish in the field.

    The earlier version compared our median projected final against the score
    projected to WIN, which is not a like-for-like comparison: by construction
    only ~1% of entries can be top 1%, so nearly every lineup showed a gap and
    the dial pinned at maximum chase for all of them — useless as a signal, and
    it fired hardest exactly when a whole game busted and the field was equally
    hurt. Ranking our projection against THEIR projections puts both sides on the
    same scale, so a lineup sitting at the field median reads as average (which
    it is) instead of desperate.
    """
    import bisect
    n = len(field_sorted)
    if not n:
        return 0.5
    ahead = n - bisect.bisect_left(field_sorted, my_final)   # rivals projected above us
    pct = ahead / n * 100.0
    if pct <= SWAP_WIN_PCT:                 # projecting to win -> hold position
        return 0.15
    if pct <= SWAP_CASH_PCT:                # between winning and cashing
        t = (pct - SWAP_WIN_PCT) / (SWAP_CASH_PCT - SWAP_WIN_PCT)
        return 0.15 + t * 0.35
    t = min(1.0, (pct - SWAP_CASH_PCT) / (SWAP_CHASE_PCT - SWAP_CASH_PCT))
    return 0.5 + t * 0.35


def _swap_candidates(players, locked_names, used_names, budget):
    """Unlocked, projecting players who could still fill an open slot.

    Applies the build's minutes gate too: a body projected under the rotation
    floor is a lottery ticket there and is no better here, so late swap must not
    hand one to a lineup the build deliberately kept clean.
    """
    out = []
    for p in players:
        if p.proj <= 0 or p.salary > budget:
            continue
        if p.risk and not p.core:
            continue
        n = normalize_name(p.name)
        if n in locked_names or n in used_names:
            continue
        out.append(p)
    out.sort(key=lambda p: -p.proj)
    g = [p for p in out if p.is_guard][:SWAP_TOP_PER_POS]
    f = [p for p in out if not p.is_guard][:SWAP_TOP_PER_POS]
    return g, f


def _slot_roster(lineup, open_idx, chosen, slots):
    """Put `chosen` into the open slots, leaving locked players exactly where DK
    has them (an upload that moves a locked player is rejected)."""
    roster = [None] * ROSTER_SIZE
    for i in range(ROSTER_SIZE):
        if i not in open_idx:
            roster[i] = lineup[i]
    gs = [p for p in chosen if p.is_guard]
    fs = [p for p in chosen if not p.is_guard]
    for i in open_idx:
        if slots[i] == "G" and gs:
            roster[i] = gs.pop(0)
        elif slots[i] == "F" and fs:
            roster[i] = fs.pop(0)
    rest = gs + fs
    for i in open_idx:
        if roster[i] is None and rest:
            roster[i] = rest.pop(0)
    return None if any(r is None for r in roster) else roster


def _enumerate_rosters(lineup, players, locked_names, open_idx, slots, cap=400):
    """Legal rosters reachable from this lineup, best-projection combos first.

    We only need a strong shortlist, not the whole space — the simulator ranks
    them afterwards, and it's the ranking that decides.
    """
    import itertools
    keepers = [lineup[i] for i in range(ROSTER_SIZE) if i not in open_idx]
    budget = SALARY_CAP - sum(p.salary for p in keepers)
    used = {normalize_name(p.name) for p in keepers}
    need_g = sum(1 for i in open_idx if slots[i] == "G")
    need_f = sum(1 for i in open_idx if slots[i] == "F")
    need_u = sum(1 for i in open_idx if slots[i] == "UTIL")
    gpool, fpool = _swap_candidates(players, locked_names, used, budget)
    # the players currently in the open slots always stay on the table, so
    # "leave it alone" competes fairly with every alternative
    for p in (lineup[i] for i in open_idx):
        pool = gpool if p.is_guard else fpool
        if all(normalize_name(q.name) != normalize_name(p.name) for q in pool):
            pool.append(p)
    combos = []
    for gs in itertools.combinations(gpool, need_g):
        sg = sum(p.salary for p in gs)
        if sg > budget:
            continue
        for fs in itertools.combinations(fpool, need_f):
            base = list(gs) + list(fs)
            sal = sg + sum(p.salary for p in fs)
            if sal > budget:
                continue
            if need_u:
                taken = {normalize_name(p.name) for p in base}
                for u in itertools.chain(gpool, fpool):
                    un = normalize_name(u.name)
                    if un in taken or sal + u.salary > budget:
                        continue
                    combos.append((sum(p.proj for p in base) + u.proj, base + [u]))
            else:
                combos.append((sum(p.proj for p in base), base))
    combos.sort(key=lambda c: -c[0])
    # The roster as it stands goes in FIRST and unconditionally. Everything
    # downstream diffs against it — gain, what's coming out, what's going in — so
    # if the salary/position filters or the `cap` cut it from the list there'd be
    # nothing to compare to, and a swap would be reported with no OUT/IN to show.
    out = [list(lineup)]
    seen = {frozenset(normalize_name(lineup[i].name) for i in open_idx)}
    for _, chosen in combos:
        key = frozenset(normalize_name(p.name) for p in chosen)
        if key in seen:
            continue
        seen.add(key)
        roster = _slot_roster(lineup, open_idx, chosen, slots)
        # Same team-correlation cap the build enforces: more than this from one
        # team is a bet on a single game script, not a lineup. And DK's own rule
        # that a roster must span at least two games — an all-one-game lineup is
        # rejected at upload, so a swap must never create one.
        if (roster and _max_per_team(roster) <= SWAP_MAX_PER_TEAM
                and _max_per_game(roster) <= ROSTER_SIZE - 1):
            out.append(roster)
        if len(out) >= cap:
            break
    if not out:   # locks alone can already exceed the cap — don't strand the lineup
        for _, chosen in combos[:cap]:
            roster = _slot_roster(lineup, open_idx, chosen, slots)
            if roster:
                out.append(roster)
    return out


# Chase-side ownership penalty, now OFF.
#
# The idea was that a lineup which is behind has to differentiate to win, so
# chasing should fade the field. Two independent findings killed it. The 7-slate
# review found fading is -EV at this field size generally (chalk won 6 of 7, and
# the winning lineups consistently OUT-owned ours). And on the single slate where
# late swap actually fired, this penalty is what moved 40-point players out for
# 8-point ones. Keeping the constant at 0 rather than deleting the term: the
# aggression read is still shown, and if logging ever justifies a tilt this is
# the one line to change.
SWAP_OWN_TILT = 0.0


# Small tiebreak against loading up on the game that tips first. Filling a slot
# from the earliest game locks it immediately; the same points from a later game
# keep the slot swappable if news breaks. Deliberately tiny — this should only
# separate rosters that are otherwise close, never drive a decision.
SWAP_EARLY_PENALTY = 0.4


def _score_rosters(rosters, players, aggression, n_sims, seed, early_names=()):
    """Rank with the main tool's simulator.

    Protect (below 0.5) simply weights the median harder — it does NOT buy chalk.
    Hugging the field would mean paying ceiling for ownership, and a low-owned
    play that projects the same is free differentiation. So the ownership tilt is
    one-sided: it applies only when chasing.
    """
    from engine import Lineup, simulate_and_score
    lus = [Lineup(list(r)) for r in rosters]
    simulate_and_score(lus, players, sims=n_sims, own_lean=0.0, seed=seed)
    k = max(0.0, aggression - 0.5) * SWAP_OWN_TILT
    early = set(early_names or ())
    for lu in lus:
        m = lu.metrics
        # 0 -> mean, 0.5 -> the usual 85th-percentile ceiling, 1 -> p95
        if aggression <= 0.5:
            t = aggression / 0.5
            base = m["mean"] + t * (m["ceiling"] - m["mean"])
        else:
            t = (aggression - 0.5) / 0.5
            base = m["ceiling"] + t * (m["p95"] - m["ceiling"])
        soon = sum(1 for p in lu.players if normalize_name(p.name) in early)
        m["swapScore"] = base - k * lu.total_own - SWAP_EARLY_PENALTY * soon
    return lus


def _swap_payload(p, scored, pool_names=None):
    act = scored.get(normalize_name(p.name))
    return {"name": p.name, "team": p.team, "salary": p.salary,
            "proj": round(p.proj, 1), "own": round(p.ownership, 1),
            "scored": None if act is None else round(act, 1),
            "offPool": bool(pool_names) and normalize_name(p.name) not in pool_names}


def _roster_payload(roster, slots, scored, locked_names, pool_names, core_names):
    """The full six in DK slot order, so the UI can show before vs after side by
    side instead of just the two or three names that moved."""
    out = []
    for slot, p in zip(slots, roster):
        n = normalize_name(p.name)
        act = scored.get(n)
        out.append({
            "slot": slot, "name": p.name, "team": p.team, "pos": p.pos,
            "salary": p.salary, "proj": round(p.proj, 1),
            "own": round(p.ownership, 1),
            "scored": None if act is None else round(act, 1),
            "locked": n in locked_names,
            "core": n in (core_names or ()),
            "offPool": bool(pool_names) and n not in pool_names,
        })
    return out


# News-driven swapping only.
#
# This is the sharpest result the 24-contest review produced about late swap,
# and it says most of what this tool was doing had no edge:
#
#   swaps forced by news (OUT / benched / projection cut)
#       +24.5 pts per entry, positive 15 of 15, cash 3 -> 9
#   discretionary re-optimisation of un-started slots, no status change
#       +3.4 pts per entry, 15 of 29 positive, p=0.49 — indistinguishable
#       from noise, and one such night cost 125 points across 8 entries
#
# The mechanism is clear from the player side: LineStar-bench players who
# actually started scored 2.1x their projection (+11.6) and expected starters who
# sat lost 13.5, and every one of the 44 starter-flag disagreements happened in a
# game AFTER the first tip. So the whole edge is reacting to lineup news inside
# the swap window; re-shuffling players nobody has said anything about is churn.
#
# We hold a lineup unless something actually changed about a player in it. The
# switch exists because the tool advises rather than overrules — but it defaults
# to the side the data is on.
SWAP_NEWS_PROJ_DROP = 0.25   # share of projection lost that counts as news
SWAP_NEWS_MIN_DROP = 4.0     # ...and at least this many points, so noise is out

# A benching, detected WITHOUT the logged baseline.
#
# The projection-cut test above has two holes, both reproduced on a controlled
# slate. It needs a logged build for this exact game set, so a night without one
# sees nothing but scratches — and a benching is not a scratch. Worse, when the
# baseline IS there the cut gets damped away: a benched player's LineStar
# projection collapses but her SEASON AVERAGE does not, and blend_projections
# mixes the two, so a projection cut to a quarter came through as 29 -> 18 and a
# milder one never cleared the 25% gate at all. The mechanism of a benching is
# exactly what defeats the detector built to catch it.
#
# The starter flag has neither problem. It is in the fresh file, it needs no
# history, and the review already measured this as the whole edge: LineStar-bench
# players who actually started scored 2.1x their projection (+11.6), expected
# starters who sat lost 13.5, and all 44 starter-flag disagreements landed in a
# game after the first tip.
#
# It fires only when there is something to DO about it — a starter at the same
# roster position, costing no more, projecting materially better. A sixth woman
# who is priced for it and projects fine is not news, and this is what keeps the
# rule from re-opening every bench body in the set and bringing back the churn.
SWAP_BENCH_EDGE = 1.5        # replacement must project at least this multiple


def _news_baseline(games):
    """{norm name: proj} from the most recent logged build of THIS slate.

    Matched on the slate's GAME SET, not on a date. It used to be matched on
    _slate_date(), which returns today's ET date — so a build made in the
    afternoon and a swap run after a 10pm tip rolled past midnight were looking at
    two different days and found nothing. Confirmed by replaying 8-29 against the
    repo's own build log for 8-29: hadBaseline came back False, which means the
    "projection cut" half of news detection could never fire on a real night. A
    game set identifies a slate exactly and does not drift over midnight.

    Takes the LAST matching record rather than merging them all. Successive builds
    on one slate are the audit trail of mid-day core and pool edits; the baseline
    wants what we believed at lock, not an average of every draft.
    """
    want = sorted(games or [])
    if not want:
        return {}
    best = None
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if sorted(rec.get("games") or []) == want:
                    best = rec          # append-only log: last match is the latest
    except OSError:
        return {}
    out = {}
    for lu in (best or {}).get("lineups", []):
        for p in lu.get("players", []):
            nm = normalize_name(p.get("name", ""))
            if nm:
                # Prefer the raw LineStar number where the record carries it, so
                # the comparison is raw against raw. Records written before that
                # field existed fall back to the blended one.
                out[nm] = {"proj": p.get("proj", 0.0),
                           "ls": p.get("lsProj", p.get("proj", 0.0)),
                           "starter": p.get("starter")}
    return out


def _news_names(players, baseline, locked_names=()):
    """Players something has actually been said about since the build.

    Ruled out and benched are both readable from the fresh file alone. A
    projection cut needs the logged baseline, so that test only fires when a
    build for this slate was logged — see the note on SWAP_BENCH_EDGE for why
    that is not enough on its own.
    """
    news = {}
    # Who could actually take the slot: a starter whose game has not tipped.
    raw = lambda p: p.ls_proj if p.ls_proj > 0 else p.proj
    live = [p for p in players if p.proj > 0 and p.starter
            and normalize_name(p.name) not in locked_names]
    for p in players:
        nm = normalize_name(p.name)
        if p.proj <= 0 or p.status == "OUT":
            news[nm] = "ruled out"
            continue
        b = baseline.get(nm)
        if b:
            # Raw against raw — see the note in _lineup_log.
            was, now = b["ls"] or 0.0, raw(p)
            drop = was - now
            if was > 0 and drop >= SWAP_NEWS_MIN_DROP \
                    and drop >= was * SWAP_NEWS_PROJ_DROP:
                news[nm] = f"projection cut {was:.0f} to {now:.0f}"
                continue
            # ...and UP. Every other trigger here is about a player in your
            # lineup getting worse, which is why nothing ever fired when a
            # player you do NOT hold got better — the exact case LineStar
            # creates when it hedges a team it cannot call and then resolves it.
            # A big raw move is new information whichever way it points.
            #
            # It has to be measured raw, for the reason the cut test is: the
            # blend mixes in a season average that does not know the role
            # changed, so a 22-point raw jump arrives as an 11-point blended one
            # and a 20-point bar is quietly a 40-point raw bar.
            rise = now - was
            if was > 0 and rise >= SWAP_NEWS_MIN_DROP \
                    and rise >= was * SWAP_NEWS_PROJ_DROP:
                news[nm] = f"projection up {was:.0f} to {now:.0f}"
                continue
            if b.get("starter") and not p.starter:
                news[nm] = "was starting at lock, now on the bench"
                continue
            if p.starter and not b.get("starter"):
                news[nm] = "was on the bench at lock, now starting"
                continue
        if p.starter or nm in locked_names:
            continue
        # Benched, and there is a same-position starter who costs no more and
        # projects materially better. Report the alternative, because that is the
        # fact that makes it actionable rather than a status line.
        alt = [q for q in live
               if q.is_guard == p.is_guard and q.salary <= p.salary
               and raw(q) >= raw(p) * SWAP_BENCH_EDGE
               and normalize_name(q.name) != nm]
        if alt:
            best = max(alt, key=raw)
            news[nm] = (f"on the bench — {best.name} is starting at "
                        f"${best.salary:,} and projects {raw(best):.0f} "
                        f"against {raw(p):.0f}")
    return news


def _dk_row(entry, roster, dk_pool):
    """One re-uploadable DK entries line for this roster."""
    cells = []
    for p in roster:
        info = dk_pool.get(normalize_name(p.name))
        cells.append(f'"{p.name} ({info["dkId"]})"' if info else f'"{p.name}"')
    cname = entry["contest"].replace('"', '""')
    return (f'{entry["entryId"]},"{cname}",{entry["contestId"]},{entry["fee"]},'
            + ",".join(cells))


def run_late_swap(csv_text, dk_text, contest_text=None, options=None):
    """DK entries file + updated LineStar (+ optional contest standings) ->
    recommended swaps and a re-uploadable DK file. See the module note above."""
    options = options or {}
    players = parse_linestar((csv_text or "").strip())
    scored = parse_linestar_scored((csv_text or "").strip())
    # Mirror the build's pipeline exactly, so the same player is judged the same
    # way in both places: minutes gate, then the projection blend.
    apply_daily_projections(players, options.get("minutes") or "")
    blend_projections(players)
    if sum(1 for p in players if p.proj > 0) < ROSTER_SIZE:
        return {"error": "Drop your UPDATED LineStar CSV — it carries the new "
                         "projections and the live scores late swap needs."}
    if not (dk_text or "").strip():
        return {"error": "Upload your DK entries export (DKEntries*.csv)."}
    dk = parse_dk_entries(dk_text)
    if not dk["pool"]:
        return {"error": "Couldn't read that as a DK entries file (no player pool found)."}
    entries = [e for e in dk["entries"] if len(e["names"]) == ROSTER_SIZE]
    if not entries:
        return {"error": "That DK file has no filled-in lineups yet."}
    slots = dk["slots"] or _LS_SLOTS
    # DK's own (LOCKED) markers are the authority on what can still move.
    locked_names = {n for n, p in dk["pool"].items() if p.get("locked")}
    by_norm = {normalize_name(p.name): p for p in players}
    n_sims = _int(options.get("sims"), 3000)
    n_lu = len(entries)
    stale = []     # entries where DK disagrees with the entries file we were given

    # Optional contest standings: replaces the projection-based pace proxy with a
    # real leaderboard position, and gives actual contest ownership for the
    # players already revealed.
    contest = (parse_contest_standings(contest_text, slots)
               if (contest_text or "").strip() else None)
    field_finals = []
    my_rank = {}
    if contest and contest["entries"]:
        avg_slot = _avg_open_slot(players, locked_names)
        mine = {e["entryId"] for e in entries}
        field_finals = _field_finals(contest, avg_slot, mine)
        my_rank = {e["entryId"]: e for e in contest["entries"]}
        for p in players:  # prefer real ownership where DK has revealed it
            actual = contest["ownership"].get(normalize_name(p.name))
            if actual is not None and actual > 0:
                p.ownership = actual
        # The entries export is a snapshot; the contest file is what DK holds
        # RIGHT NOW. Edit a lineup on the site, re-download the contest file and
        # the two disagree — and the tool used to carry on against the roster it
        # built, silently, which is worse than useless once you have fixed a
        # lineup by hand. So compare them, and trust DK.
        unreadable = []
        for e in entries:
            live = my_rank.get(e["entryId"])
            if not live:
                continue
            shown = live.get("revealed")
            if shown is None:
                unreadable.append(live)
                continue                        # layout unknown — do not guess
            fixed, why = _reconcile_with_dk(
                e["names"], shown, slots, dk["pool"])
            if why:
                stale.append(
                    f"Entry {e['entryId']}: your DK entries file is out of date "
                    f"and can't be repaired from the contest file — {why}. "
                    "Leaving this lineup as your entries file has it; "
                    "re-download the entries file to fix it properly.")
                continue
            if fixed is None:
                continue                        # the two already agree
            was = [o for o, n in zip(e["names"], fixed) if o != n]
            now = [n for o, n in zip(e["names"], fixed) if o != n]
            e["names"] = fixed
            stale.append(
                f"Entry {e['entryId']}: your DK entries file is out of date. DK "
                f"has " + ", ".join(f"{n} where the file says {o}"
                                    for o, n in zip(was, now))
                + ". Those games have already tipped, so DK's version is the one "
                  "that counts and late swap is using it.")
        if unreadable:
            stale.append(
                f"Couldn't read the roster layout in the contest file for "
                f"{len(unreadable)} of your entries, so it was only used for "
                f"standing, not for what is entered. Expected the "
                f"{len(slots)} slots {' '.join(sorted(set(slots)))}; "
                f"got: {unreadable[0]['lineupCell'][:160]!r}")

    # Cores carry over from the build — protected, not optimized away.
    core_names = _parse_names(options.get("cores"))
    # The pool carries over too. Cores count as in-pool, same as in the build.
    pool_names = _parse_names(options.get("pool"))
    if pool_names:
        pool_names |= core_names
    for p in players:   # the minutes gate exempts cores, same as the build
        nmp = normalize_name(p.name)
        p.core = nmp in core_names
        p.in_pool = p.core or (nmp in pool_names)
    # Same licence as the build: a player you named, whom LineStar has at 0, is
    # read off the daily file instead. Without this a pooled zero stays invisible
    # to late swap too — so the one player the sharp knew about could never be
    # swapped IN either, which is half of the problem this is meant to fix.
    revive_pooled_zeros(players, options.get("minutes") or "", stale)
    # What has actually changed since the build. Everything downstream keys off
    # this: no news about a lineup means the lineup is left alone.
    # News-only is the ONLY mode now. Replayed on all seven mid-slate snapshots in
    # the results, free re-optimisation scored +$86 — all of it one slate where
    # the "mid-slate" LineStar pull carried unchanged projections and no live
    # scores, so 16 of 16 swaps were pure churn that happened to land. Drop that
    # slate and it is -$22 with two slates actively wrecked (52.9 -> 31.5 and
    # 40.0 -> 0.0). News-only was +$24 across the same seven, helped 2, hurt 0,
    # and both gains were a player who had been ruled out.
    #
    # SWAP_MIN_GAIN cannot rescue free mode: simulated gains of 6-13 points mapped
    # to realised changes from -30 to +35, i.e. the threshold does not separate
    # signal from noise at all. News swaps carried simulated gains of 24-45 and
    # went 8 for 8.
    #
    # That is why news-only existed and why it is no longer the gate — the fix is
    # the BAR, not the trigger. See SWAP_DISCRETIONARY_GAIN. Both policies are
    # still evaluated on every run and both are logged; only one is acted on.
    news_only = str(options.get("newsOnly", "off")) != "off"
    baseline = _news_baseline(sorted({p.game for p in players if p.game}))
    news = _news_names(players, baseline, locked_names)
    # Players in the next game to tip: filling a slot from there costs optionality.
    starts = sorted({p["start"] for p in dk["pool"].values()
                     if p.get("start") and not p.get("locked")})
    early_names = ({n for n, p in dk["pool"].items()
                    if p.get("start") == starts[0] and not p.get("locked")}
                   if len(starts) > 1 else set())

    # Pass 1: per lineup, work out where it stands and rank its legal rosters.
    ranked, base_rows = [], []
    for e in entries:
        lineup = [by_norm.get(normalize_name(n)) for n in e["names"]]
        if any(p is None for p in lineup):
            miss = [n for n, p in zip(e["names"], lineup) if p is None]
            base_rows.append({"entryId": e["entryId"],
                              "error": f"not in the LineStar file: {', '.join(miss)}"})
            ranked.append(None)
            continue
        # A core is a conviction play — late swap protects it rather than
        # treating it as an anonymous name to be optimized away. It becomes
        # releasable only when there is actual news about it: ruled out, or a
        # projection cut big enough to count. That is the one case where holding
        # the sharp's pick is holding a player nobody expects to play.
        open_idx = [i for i in range(ROSTER_SIZE)
                    if normalize_name(lineup[i].name) not in locked_names
                    and not (normalize_name(lineup[i].name) in core_names
                             and lineup[i].proj > 0
                             and normalize_name(lineup[i].name) not in news)]
        locked_players = [lineup[i] for i in range(ROSTER_SIZE) if i not in open_idx]
        banked, expected, deficit, wdef = _pace_read(locked_players, scored)
        # Real standing beats the proxy: if the contest file gave us this entry,
        # drive aggression off the actual gap to a winning score.
        me = my_rank.get(e["entryId"])
        rank = proj_final = pct = None
        if me is not None and field_finals:
            banked = me["points"] or banked
            # Project OUR finish the same way we projected theirs, then rank it
            # against them — like for like.
            proj_final = banked + sum(p.proj for i, p in enumerate(lineup)
                                      if i in open_idx or normalize_name(p.name) not in locked_names)
            aggr = _aggression_from_rank(proj_final, field_finals)
            # Keep the percentile itself, not just the dial it produced. The dial
            # tilts ranking between alternatives; the percentile is what decides
            # whether this lineup is allowed a discretionary move at all.
            ahead = len(field_finals) - bisect.bisect_left(field_finals, proj_final)
            pct = 100.0 * ahead / len(field_finals)
            rank = me["rank"]
        else:
            aggr = _aggression(wdef)
        # Which slots we are actually willing to move. In news-only mode the
        # answer is "the ones something was said about, plus one spare to make
        # the salary work" — and that has to shape the ENUMERATION, not filter
        # it afterwards, because the top combinations by projection all move
        # several slots at once and the focused ones never make the shortlist.
        news_idx = [i for i in open_idx
                    if normalize_name(lineup[i].name) in news]
        if news_only and news_idx:
            spare = [i for i in open_idx if i not in news_idx]
            open_sets = [news_idx] + [news_idx + [j] for j in spare]
        else:
            open_sets = [open_idx]
        rosters, seen_r = [], set()
        for oset in open_sets:
            for r in _enumerate_rosters(lineup, players, locked_names, oset, slots):
                key = frozenset(normalize_name(p.name) for p in r)
                if key not in seen_r:
                    seen_r.add(key)
                    rosters.append(r)
        if not rosters:
            rosters = [list(lineup)]
        lus = _score_rosters(rosters, players, aggr, n_sims, seed=len(base_rows),
                             early_names=early_names)
        cur = frozenset(normalize_name(p.name) for p in lineup)
        for lu in lus:
            lu.metrics["isCurrent"] = frozenset(
                normalize_name(p.name) for p in lu.players) == cur
        lus.sort(key=lambda l: -l.metrics["swapScore"])
        ranked.append(lus)
        # News about anyone still movable in THIS lineup. A cored player is
        # normally held out of open_idx, but news about a core is exactly the case
        # where it should be released, so check the whole roster's open slots.
        why = [f"{lineup[i].name}: {news[normalize_name(lineup[i].name)]}"
               for i in range(ROSTER_SIZE)
               if normalize_name(lineup[i].name) not in locked_names
               and normalize_name(lineup[i].name) in news]
        base_rows.append({
            "entryId": e["entryId"], "open": len(open_idx),
            "banked": round(banked, 1), "expected": round(expected, 1),
            "pace": round(deficit, 1), "aggression": round(aggr, 2),
            "rank": rank, "projFinal": None if proj_final is None else round(proj_final, 1),
            "pct": None if pct is None else round(pct, 3),
            "lineup": lineup, "news": why,
        })

    # Pass 2: commit lineup by lineup under a portfolio exposure cap, so the set
    # stays diversified instead of every entry converging on the same few plays.
    # Locked players count toward exposure — they're already committed.
    # Seed with the exposure the user ALREADY has, every player, locked or not.
    # The cap's job here is to stop the swap process from concentrating the set
    # further — not to churn a lineup purely to unwind exposure the user chose,
    # which would mean downgrading a roster for no gain.
    counts = {}
    for row, lus in zip(base_rows, ranked):
        if lus is None:
            continue
        for p in row["lineup"]:
            n = normalize_name(p.name)
            counts[n] = counts.get(n, 0) + 1
    results, changed, gain_total = [], 0, 0.0
    for row, lus in zip(base_rows, ranked):
        if lus is None:
            results.append({"entryId": row["entryId"], "error": row["error"]})
            continue
        lineup = row["lineup"]
        current = next((l for l in lus if l.metrics.get("isCurrent")), None)
        cur_score = current.metrics["swapScore"] if current else None
        here = {normalize_name(p.name) for p in lineup}

        # A mid-slate exposure cap used to sit here. It is gone with the build's:
        # held out over 23 slates the cap changed no outcome, and the one moment
        # it could bind is the worst possible one — when a player is ruled out in
        # six lineups, the replacement you want is the best one, six times, not
        # the fifth-best because the first hit a quota.

        cur_proj = sum(p.proj for p in lineup)
        blocked_by_pool = None      # why the obvious upgrade was turned down
        # Projecting to WIN: the rank gate, and the only thing rank is allowed to
        # decide. It does not rank anything and it does not tilt anything — it
        # answers one coarse question, "am I clearly winning", which is the only
        # question this estimate is good enough to answer. Rivals' unplayed slots
        # are modelled as a single average with no variance, so "I am 40th" is
        # much softer than it looks and 40th can become 100th by itself. A number
        # that unreliable must not drive a decision, but it can refuse one: a
        # lineup already projecting to win does not get re-optimised on a
        # discretionary edge. News can still move it.
        #
        # The estimate is as soft as feared and the gate was still right. Across
        # 8 snapshots, 217 entries projecting top 1% finished there 13% of the
        # time — and it is almost entirely about how much is left to play: 60%
        # held with 1 slot hidden, 30% with 2, 4% with 3, 0% with 5. Median rank
        # error is 14-20 percentile points. But on the one slate the gate fired
        # (9 entries), their best discretionary candidates would have realised
        # -13.8 on average, 1 of 9 positive. Refusing to act on a bad estimate
        # was correct even though the estimate was bad.
        #
        # The obvious refinement — protect only when 2 or fewer slots are left,
        # since a 3-slot top-1% projection holds 4% of the time — would make the
        # gate fire LESS. It is not taken yet: the 9 entries it fired on are the
        # only evidence, and whether they had 3+ slots open is unknown. Narrowing
        # a gate that went 9 for 9 on the strength of a rate computed elsewhere
        # is how a good rule gets deleted.
        winning = row.get("pct") is not None and row["pct"] <= SWAP_WIN_PCT
        disc_bar = float("inf") if winning else SWAP_DISCRETIONARY_GAIN
        # And when there IS news, react to the news — don't let one scratch
        # license a rebuild of the whole roster. The measured edge is in
        # replacing the player something was said about; every additional slot
        # that moves alongside it is discretionary re-optimisation, which
        # measured at noise. One spare slot is allowed because a straight
        # one-for-one swap often can't be afforded under the cap.
        news_cap = len(row["news"]) + 1

        def consider(*, news_slots_only, disc):
            """Best roster clearing the bars. -> (pick, blocked_by_pool note).

            Called twice per lineup — once under news-only and once free — so one
            night's results grade both policies. Only one of the two is acted on;
            both are logged.
            """
            blocked = None
            for lu in lus:
                if lu.metrics.get("isCurrent"):
                    continue
                if lu.salary > SALARY_CAP:
                    continue
                outgoing = here - {normalize_name(p.name) for p in lu.players}
                incoming = {normalize_name(p.name) for p in lu.players} - here
                # A move is news-forced when a player something was SAID about is
                # leaving OR arriving. Only "leaving" was ever checked, which is
                # what made the whole thing one-directional: it reacted to your
                # player collapsing and never to a better one becoming available.
                out_news = any(n in news for n in outgoing)
                forced = out_news or any(n in news for n in incoming)
                if news_slots_only:
                    # The ORIGINAL policy, kept intact so the shadow comparison
                    # stays honest: only a slot whose own occupant has news may
                    # move, plus one spare for the salary. An arriving news
                    # player does not open a slot here — that is precisely the
                    # blind spot the free policy is being tested against.
                    if not out_news or len(outgoing) > news_cap:
                        continue
                    if len([n for n in outgoing if n not in news]) > 1:
                        continue
                    need = SWAP_MIN_GAIN
                else:
                    need = SWAP_MIN_GAIN if forced else disc
                if cur_score is not None and \
                        lu.metrics["swapScore"] - cur_score < need:
                    continue
                if SALARY_CAP - lu.salary > SWAP_MAX_LEFTOVER and current and \
                        lu.metrics["swapScore"] - cur_score < SWAP_MIN_GAIN * 2:
                    continue  # only strand salary for a clearly better roster
                # Pool discipline. The pool is the vetted list; reaching outside
                # it for a couple of points is a bad trade, because the
                # projection edge is inside the model's error bars while the pool
                # encodes judgement the model doesn't have. So an unvetted name
                # has to clear a much higher bar — a real projection jump, not a
                # rounding win.
                if pool_names:
                    incoming_off = [p for p in lu.players
                                    if normalize_name(p.name) not in here
                                    and normalize_name(p.name) not in pool_names]
                    # Scaled by how many unvetted names it takes: two of them
                    # have to earn twice as much, so a roster can't sneak several
                    # marginal off-pool plays in under one lump gain.
                    gain_proj = sum(p.proj for p in lu.players) - cur_proj
                    # The high off-pool bar exists to stop DISCRETIONARY reaching
                    # outside the vetted list. A move forced by news is not that:
                    # the pool was written before anyone knew this player would
                    # be benched, and holding her because her replacement was not
                    # on a list drawn up at noon is the pool overruling the one
                    # category of swap the review measured as reliably good
                    # (+24.5 per entry, positive 15 of 15).
                    #
                    # The high bar is pointed the right way: replayed at MATCHED
                    # simulated gain, off-pool incoming players underperform
                    # in-pool ones by 10-14 realised points (+3 against +18 in
                    # the 0-10 band, +5 against +15 in 10-20). The pool is
                    # carrying real information, not just the sharp's habit.
                    bar = SWAP_MIN_GAIN if forced else SWAP_OFF_POOL_MIN_GAIN
                    if incoming_off and gain_proj < bar * len(incoming_off):
                        # Say which bar stopped it. A hold reported as "no move
                        # clears the gain threshold" reads as "nothing better
                        # exists", when what actually happened is that the
                        # upgrade was outside the pool you typed and missed a bar
                        # you were never shown.
                        if row["news"] and blocked is None:
                            blocked = (
                                f"{', '.join(p.name for p in incoming_off)} would "
                                f"fix it (+{gain_proj:.1f} proj) but is not in "
                                f"your pool, and an off-pool move needs "
                                f"+{bar * len(incoming_off):.0f}")
                        continue
                return lu, blocked
            return None, blocked

        # Both policies, every run. `news_only` decides which one is acted on.
        news_pick, news_blocked = consider(news_slots_only=True, disc=float("inf"))
        free_pick, free_blocked = consider(news_slots_only=False, disc=disc_bar)
        pick = news_pick if news_only else free_pick
        blocked_by_pool = news_blocked if news_only else free_blocked
        held = pick is None and not row["news"] and row["open"] > 0
        final = pick.players if pick else list(lineup)
        if pick:  # move exposure from the dropped players onto the added ones
            now = {normalize_name(p.name) for p in final}
            for n in here - now:
                counts[n] = max(0, counts.get(n, 0) - 1)
            for n in now - here:
                counts[n] = counts.get(n, 0) + 1
        rec = {
            "entryId": row["entryId"], "open": row["open"],
            # Nothing left to move: the stance is meaningless here, so say so
            # rather than labelling a finished lineup "chasing upside".
            "settled": row["open"] == 0,
            "banked": row["banked"], "expected": row["expected"], "pace": row["pace"],
            "aggression": row["aggression"], "rank": row.get("rank"),
            "projFinal": row.get("projFinal"),
            "salary": sum(p.salary for p in final),
            "proj": round(sum(p.proj for p in final), 1),
            "score": round((pick or current).metrics["swapScore"], 1) if (pick or current) else None,
            "keep": pick is None,
            "news": row["news"],
            # why we are holding, so a "keep" is never a silent shrug
            "hold": (None if pick else blocked_by_pool
                     or ("projecting to win — only news moves this one" if winning
                         else f"nothing clears +{disc_bar:.0f}" if not row["news"]
                         else "no move clears the gain threshold")),
        }
        # What the OTHER policy would have done, so one night grades both.
        best = max((l for l in lus if not l.metrics.get("isCurrent")),
                   key=lambda l: l.metrics["swapScore"], default=None)
        rec["shadow"] = {
            "actedOn": "newsOnly" if news_only else "free",
            "newsOnlyWouldMove": news_pick is not None,
            "freeWouldMove": free_pick is not None,
            "sameChoice": (news_pick is free_pick),
            "winning": bool(winning),
            "discBar": None if disc_bar == float("inf") else disc_bar,
            # the best roster on the board regardless of any bar — so the log can
            # say what was left on the table, not just what was taken
            "bestGain": (round(best.metrics["swapScore"] - cur_score, 1)
                         if best is not None and cur_score is not None else None),
            "newsGain": (round(news_pick.metrics["swapScore"] - cur_score, 1)
                         if news_pick is not None and cur_score is not None else None),
            "freeGain": (round(free_pick.metrics["swapScore"] - cur_score, 1)
                         if free_pick is not None and cur_score is not None else None),
            "freeRoster": ([p.name for p in free_pick.players]
                           if free_pick is not None else None),
            "newsRoster": ([p.name for p in news_pick.players]
                           if news_pick is not None else None),
        }
        if pick:
            # Never report a change without the diff that explains it — the UI
            # renders these unconditionally when keep is false.
            was = {normalize_name(p.name) for p in lineup}
            now = {normalize_name(p.name) for p in final}
            rec["gain"] = (round(pick.metrics["swapScore"] - cur_score, 1)
                           if cur_score is not None else 0.0)
            rec["out"] = [_swap_payload(p, scored) for p in lineup
                          if normalize_name(p.name) not in now]
            rec["in"] = [_swap_payload(p, scored, pool_names) for p in final
                         if normalize_name(p.name) not in was]
            rec["projGain"] = round(sum(p.proj for p in final) - cur_proj, 1)
            changed += 1
            gain_total += rec["gain"]
        rec["roster"] = final
        rec["was"] = lineup
        results.append(rec)

    # Re-uploadable DK file: every entry, changed or not, in DK's slot order.
    # Each entry also carries its own before/after row, so the UI can let you
    # decline an individual swap and still emit a complete, valid file.
    csv_header = "Entry ID,Contest Name,Contest ID,Entry Fee," + ",".join(slots)
    lines = [csv_header]
    for e, rec in zip(entries, results):
        roster = rec.pop("roster", None)
        was = rec.pop("was", None)
        if not roster:
            continue
        # Keep UTIL on the latest game so it's the last slot to lock.
        roster = _util_holds_latest(roster, slots, dk["pool"], locked_names)
        # `was` is shown exactly as it sits in DK — re-slotting it would
        # misrepresent what you actually entered.
        rec["after"] = _roster_payload(roster, slots, scored, locked_names,
                                       pool_names, core_names)
        rec["before"] = _roster_payload(list(was), slots, scored, locked_names,
                                        pool_names, core_names)
        rec["rowAfter"] = _dk_row(e, roster, dk["pool"])
        rec["rowBefore"] = _dk_row(e, was, dk["pool"])
        lines.append(rec["rowAfter"])
    win_score = None
    if field_finals:  # informational: what the top 1% is projected to finish on
        win_score = round(field_finals[int(len(field_finals) * 0.99)], 1)
    out = {
        "lockedPlayers": len(locked_names),
        "field": contest["field"] if contest else None,
        "winScore": win_score,
        "entries": len(entries),
        "changed": changed,
        "gain": round(gain_total, 1),
        "newsOnly": news_only,
        "newsCount": len(news),
        "hadBaseline": bool(baseline),
        # Without a baseline only "ruled out" news is detectable — a projection
        # CUT cannot be seen, because there is nothing to compare against. Say so
        # instead of quietly running at half strength.
        "warnings": stale + ([] if baseline else [
            "No pre-lock build found for this slate, so late swap can only react "
            "to players ruled OUT and to benchings — it cannot see a projection "
            "cut. Build this slate first (even once) and the full news check "
            "comes back."]),
        "slots": slots,
        "csvHeader": csv_header,
        "dkCsv": ("\n".join(lines) + "\n") if len(lines) > 1 else None,
        "swaps": results,
    }
    out["_scored"] = scored
    _log_swap(out, players, baseline, options, news)
    out.pop("_scored", None)
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path not in ("/api/optimize", "/api/lateswap", "/api/dkfill",
                             "/api/board"):
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/dkfill":  # no LineStar needed — pure slotting
                result = run_dk_fill(payload.get("dk") or "",
                                     payload.get("lineups") or [])
                return self._send(400 if result.get("error") else 200,
                                  json.dumps(result))
            csv_text = payload.get("csv") or ""
            if not csv_text.strip():
                return self._send(400, json.dumps(
                    {"error": "Drop your LineStar projections CSV."}))
            if self.path == "/api/board":
                result = run_board(csv_text, payload.get("options") or {})
            elif self.path == "/api/lateswap":
                result = run_late_swap(csv_text, payload.get("dk") or "",
                                       payload.get("contest") or "",
                                       payload.get("options") or {})
            else:
                result = run_optimize(csv_text, payload.get("options", {}))
            code = 400 if result.get("error") else 200
            self._send(code, json.dumps(result))
        except Exception as e:  # noqa: BLE001
            self._send(500, json.dumps({"error": str(e)}))


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"\n  WNBA DFS optimizer running at  {url}")
    print("  Press Ctrl+C to stop.\n")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


# The GUI is defined in gui.py to keep this file focused on the server.
from gui import INDEX_HTML  # noqa: E402


if __name__ == "__main__":
    main()
