"""Local WNBA DFS optimizer — a self-contained web app, powered by LineStar.

Run it, open the browser, drop in a LineStar projections CSV, get lineups.
LineStar carries everything we need in one file — projection, floor, ceiling,
real projected ownership, starter status and Vegas implied totals — so there's
no cheatsheet to reconcile and no external API. Pure standard library.

    python app.py                 # opens http://localhost:8000
"""

from __future__ import annotations

import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dk import ROSTER_SIZE, SALARY_CAP, Player, normalize_name
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

    Projection, floor, ceiling and projected ownership all come straight from
    the file. Floor/ceiling are sanity-checked against the projection (LineStar
    occasionally ships a 0 floor or a ceiling barely above the projection); when
    they look wrong we fall back to a projection-anchored band so the leverage
    math — which divides by ceiling — still behaves.
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
            avg_points=_f(d.get("PPG")), status="", starting="",
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
        elif ceil > proj * 2.5:
            # Broken high ceiling: LineStar inflates the ceiling on some low-minute
            # bench players (Okot: 5.48 proj / 5 min, but a 22 ceiling). A ceiling
            # that's a wild multiple of the projection isn't real upside, it's a
            # data artifact — and it would make a 5-minute punt look like the best
            # cheap play on the board. Cap it to a sane band around the projection.
            ceil = round(proj * 2.5, 1)
        if floor <= 0 or floor < proj * 0.35 or floor >= proj:
            floor = round(proj * 0.6, 1)
        p.proj = round(proj, 1)
        p.ceil = round(ceil, 1)
        p.floor = round(floor, 1)
        # Floor a playable player's ownership at 1%: leverage keys on ownership
        # per point of ceiling, so a blank/0 ProjOwn would otherwise read as the
        # most contrarian play on the board (free leverage) purely from missing
        # data, not from being genuinely under-owned.
        own = _f(d.get("ProjOwn"))
        p.ownership = own if own > 0 else 1.0
        p.notes.append("LineStar" if p.starter else "LineStar · bench")
        players.append(p)
    return players


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
# Reliability GATE (not a grade). Back-testing 5 slates of results killed the
# graded version: minutes and stat-stuffer had ~zero correlation with bust rate
# (busts ran 50-62% at every minute level), so rationing "risk bodies" bought
# nothing. The one thing minutes cleanly flag is genuine non-rotation — a body
# projected under this floor is a lottery ticket, not a play — so we GATE those
# out of the auto-build (cores exempt) and stop grading everyone else. Minutes +
# stuffer stay on screen as info; they just no longer push players around.
GATE_MINUTES = 14.0


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
        p.risk = p.minutes < GATE_MINUTES  # gate flag: genuine non-rotation only
        p.notes.append(f"gated: {p.minutes:.0f} proj min (non-rotation)" if p.risk
                       else f"{p.minutes:.0f} min, stuffer {p.stuffer:.0f}")
        # Cross-source disagreement flag (informational; LineStar's proj is used).
        if abs(d["compdk"] - p.proj) >= 6 and abs(d["compdk"] - p.proj) >= 0.3 * p.proj:
            p.notes.append(f"src split: proj-file {d['compdk']:.0f} vs LS {p.proj:.0f}")
    return True


# ---------------- recency / ownership nudge ----------------
# The field over-owns players trending up — today's projection well above their
# season baseline (a recent big game, a new role). Projected ownership under-
# rates them, so leverage would wrongly read them as contrarian. We nudge their
# ownership toward reality: gentle and capped, because proj-vs-season is a
# directional proxy, not exact. This never benches anyone — it only makes the
# leverage math honest, so a trending play gets played the right amount and the
# real differentiation lands elsewhere.
RECENCY_TREND_MIN = 1.20    # proj this many x above season PPG => trending
RECENCY_NUDGE = 0.8         # ownership bump per unit of trend above 1.0
RECENCY_NUDGE_CAP = 1.8     # never more than this x on one player's ownership


def apply_recency_nudge(players):
    for p in players:
        if p.proj <= 0 or p.avg_points <= 0:
            continue
        trend = p.proj / p.avg_points
        if trend < RECENCY_TREND_MIN:
            continue
        before = p.ownership
        mult = min(1 + RECENCY_NUDGE * (trend - 1), RECENCY_NUDGE_CAP)
        p.ownership = round(min(p.ownership * mult, 75.0), 1)
        p.trending = True
        p.notes.append(f"🔥 trending {trend:.1f}x season · own {before:.0f}→{p.ownership:.0f}")


# ---------------- slate helpers ----------------
def _slate_date(players):
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


def _parse_names(text):
    """Turn pasted lines (possibly with extra spreadsheet columns) into a set of
    normalized player names. Takes the first tab/comma field of each line."""
    names = set()
    for line in (text or "").splitlines():
        cell = line.split("\t")[0].split(",")[0].strip()
        if len(cell) > 1:
            names.add(normalize_name(cell))
    return names


# ---------------- export / serialization ----------------
def _upload_str(p):
    """DK-import string. We only ever have LineStar's own IDs (which are NOT DK
    IDs), so lineups export by name for manual entry."""
    return p.name if (not p.dk_id or p.dk_id.startswith(("dff-", "ls-"))) else f"{p.name} ({p.dk_id})"


_SLOTS = ["F", "F", "F", "G", "G", "UTIL"]


def _slots_payload(slots):
    return [{"slot": s, "name": p.name, "team": p.team, "pos": p.pos,
             "salary": p.salary, "proj": round(p.proj, 1), "core": p.core,
             "pool": p.in_pool, "starter": p.starter, "risk": p.risk and not p.core}
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

    rel = ("on — sub-14-min non-rotation bodies gated out of the build"
           if had_minutes else "OFF — add the daily-projections CSV to turn it on")
    notes.append(("info", f"Baseline: {slate_type} slate, {n} lineups, minutes gate {rel}."))

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


HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8000"))


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

    # Recency: bump the ownership of trending-up plays toward reality so leverage
    # reads them as the chalk they'll become (not as contrarian). Uses today's
    # proj vs season PPG — both already in the LineStar file, no API.
    apply_recency_nudge(players)

    # Manual removals (late scratch / missed shootaround the projection hasn't
    # caught). Zero them and flow their minutes/usage to teammates.
    removed_info = _apply_removals(players, _parse_names(options.get("remove")))
    removed = [r["name"] for r in removed_info]

    # Cores + pool. Cores get a small projection edge and count as in-pool; the
    # pool itself is a build constraint enforced in the engine (max_off_pool).
    core_names = _parse_names(options.get("cores"))
    pool_names = _parse_names(options.get("pool"))
    for p in players:
        nm = normalize_name(p.name)
        p.core = nm in core_names
        p.in_pool = p.core or (nm in pool_names)
        if p.proj <= 0:
            continue
        if p.core:
            # Cores are the ANCHOR plays — the sharp's picks that keep landing in
            # winners. Small projection edge, and NO ownership penalty: fading
            # them for being chalk fought their inclusion (A'ja stranded at
            # 8-17%). Differentiation comes from the other five spots; the
            # per-lineup minimum (min_cores) makes every lineup build around them.
            p.proj = round(p.proj * 1.06, 1)
            p.ceil = round(p.ceil * 1.06, 1)
            p.notes.append("GT core")
        elif pool_names and not p.in_pool:
            p.notes.append("off-pool")

    playable = [p for p in players if p.proj > 0]
    if len(playable) < ROSTER_SIZE:
        return {"error": "Not enough playable players — check the file.",
                "source": source_label}

    cores = [p for p in playable if p.core]
    # A core is the sharp's conviction play — it only ever gets an UPWARD nudge
    # (the projection edge above) and a guaranteed exposure floor in the engine.
    # It's never faded or demoted for grading "weak": the tool grades each core in
    # the coach report so the call is conscious, but the machine never overrides
    # the sharp's pick (that's what buried DiJonai at 1-of-N). Everyone else earns
    # their spot on the data.
    max_off_pool = _int(options.get("maxOffPool"), 0) if pool_names else None
    # Per-player exposure caps — rein in a specific heavy play without lowering
    # the global cap (which on a short slate would needlessly hobble the studs).
    n_lu = _int(options.get("n"), 20)
    cap_names = _parse_names(options.get("capPlayers"))
    player_caps = {}
    if cap_names:
        cap_ct = max(1, round(_float(options.get("capPct"), 30) / 100.0 * n_lu))
        for p in players:
            if normalize_name(p.name) in cap_names:
                player_caps[p.dk_id] = cap_ct
    # Decide the slate read ONCE, so the engine's salary reserve and the UI badge
    # are the same determination (not two independent computations on different
    # player sets).
    slate_type = _slate_type(players)
    lineups = optimize_gpp(
        players,
        n=_int(options.get("n"), 20),
        pool_size=max(120, _int(options.get("n"), 20) * 8),
        min_stack=_int(options.get("stack"), 2),
        # Per-lineup team cap of 3 (was 4): no single team can be more than half a
        # roster. A 4-from-one-team lineup is a pure correlation bet on one game
        # script (the TOR blow-up), not a game stack — kill it by default; raise
        # maxPerTeam only for a deliberate shootout stack.
        max_per_team=_int(options.get("maxPerTeam"), 3),
        max_exposure=_float(options.get("maxExposure"), 0.6),
        # Ownership tilt, dialed to a light tiebreak. Back-test: projected
        # ownership had ~zero correlation with actual value and chalk beat
        # contrarian in 4 of 5 slates, so leverage is a gentle differentiator now,
        # not a lineup-wide fade.
        leverage=_float(options.get("leverage"), 0.05),
        n_sims=_int(options.get("sims"), 5000),
        cores=cores,
        # Anchor rule: every lineup built around at least this many cores (which
        # ones vary across the set). Default 1 when cores are set — the sharp's
        # cores keep landing in winners, so guarantee the build is around them.
        min_cores=(_int(options.get("minCores"), 1) if cores else 0),
        max_overlap=_int(options.get("maxOverlap"), 4),
        max_off_pool=max_off_pool,
        stars_and_scrubs=(slate_type == "stars-and-scrubs"),
        # Don't leave money on the table — winners used ~99.5% of the cap, so keep
        # unspent salary tight. Relaxes only if the slate can't field enough
        # lineups. (Reliability is now a gate applied at pool build, not a per-
        # lineup ration, so there's no max_risk knob anymore.)
        max_leftover=_int(options.get("maxLeftover"), 700),
        player_caps=player_caps,
    )

    return {
        "source": source_label,
        "slateType": slate_type,
        "poolActive": bool(pool_names),
        "removed": removed,
        "coach": _coach(playable, lineups, options, slate_type, had_minutes,
                        removed_info, pool_names),
        "slate": {
            "date": _slate_date(players),
            "games": sorted({p.game for p in players if p.game}),
        },
        "out": [p.name for p in players if p.status == "OUT"][:40],
        "players": [{
            "name": p.name, "team": p.team, "pos": p.pos, "salary": p.salary,
            "game": p.game, "proj": round(p.proj, 1), "floor": round(p.floor, 1),
            "ceil": round(p.ceil, 1), "own": round(p.ownership, 1),
            "min": round(p.minutes, 0), "stuffer": round(p.stuffer, 1), "risk": p.risk,
            "trending": p.trending, "core": p.core, "starter": p.starter,
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


def dk_locked_games(games):
    """Games whose tip-off has already passed (ET), i.e. no longer swappable."""
    from datetime import datetime, timedelta
    now = datetime.utcnow() - timedelta(hours=4)  # WNBA season -> EDT
    out = []
    for g, iso in games.items():
        if not iso:
            continue
        try:
            if datetime.fromisoformat(iso) <= now:
                out.append(g)
        except ValueError:
            pass
    return sorted(out)


def build_dk_upload(dk, lineups_names):
    """Fill our generated lineups into the DK entries file's slot order and return
    re-uploadable CSV text. Uses the file's REAL DK IDs, so it imports directly
    instead of needing manual entry."""
    slots, pool = dk["slots"], dk["pool"]
    entries = dk["entries"]
    if not entries:
        return None, "No contest entries found in that DK file."
    lines = ["Entry ID,Contest Name,Contest ID,Entry Fee," + ",".join(slots)]
    missing = set()
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
        cells = [f'"{p["name"]} ({p["dkId"]})"' if p and p.get("dkId")
                 else f'"{p["name"]}"' if p else "" for p in filled]
        cname = e["contest"].replace('"', '""')
        lines.append(f'{e["entryId"]},"{cname}",{e["contestId"]},{e["fee"]},'
                     + ",".join(cells))
    notes = []
    if missing:
        notes.append(f"no DK ID for: {', '.join(sorted(missing))}")
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
# Re-optimize already-entered lineups mid-slate. Locked players are fixed
# constraints; the open slots plus whatever salary they leave are just a smaller
# instance of the problem the main engine already solves, so we score candidate
# rosters with the SAME Monte-Carlo simulator rather than by raw projection —
# GPP is won on lineup upside, not on the safest median.
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
_LS_SLOTS = ["F", "F", "F", "G", "G", "UTIL"]
SWAP_MIN_GAIN = 2.0        # ignore sub-noise "improvements"
SWAP_MAX_LEFTOVER = 1500   # preference, not a filter — locks can strand salary
SWAP_TOP_PER_POS = 14      # candidate breadth per position (keeps combos sane)


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


# How many points of gap-to-target moves the dial from neutral to fully
# committed. Roughly what a swap of a few slots can realistically swing.
SWAP_GAP_SCALE = 40.0


def parse_contest_standings(text):
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
    entries, own = [], {}
    for r in rows[1:]:
        if len(r) > 5 and r[0].strip().isdigit():
            entries.append({
                "rank": int(r[0].strip()),
                "entryId": r[1].strip(),
                "points": _f(r[4]),
                "hidden": r[5].count("LOCKED"),
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


def _field_targets(contest, avg_slot):
    """Project every rival's finish (points banked + hidden slots x an average
    slot) and return the score needed for the top 1% and the top 20%."""
    finals = sorted((e["points"] + e["hidden"] * avg_slot for e in contest["entries"]),
                    reverse=True)
    if not finals:
        return 0.0, 0.0
    top = finals[max(0, min(len(finals) - 1, int(len(finals) * 0.01)))]
    cash = finals[max(0, min(len(finals) - 1, int(len(finals) * 0.20)))]
    return top, cash


def _aggression_from_field(my_final, target):
    """Dial from the real gap to the score that wins. Behind -> chase, on pace or
    ahead -> protect. No proxy, no ownership weighting — the leaderboard already
    reflects what the field did."""
    gap = target - my_final
    return max(0.15, min(0.85, 0.5 + gap / SWAP_GAP_SCALE))


def _swap_candidates(players, locked_names, used_names, budget):
    """Unlocked, projecting players who could still fill an open slot."""
    out = []
    for p in players:
        if p.proj <= 0 or p.salary > budget:
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
        if roster:
            out.append(roster)
        if len(out) >= cap:
            break
    return out


# How hard the CHASE side leans on lineup ownership. Back-testing 8/12 showed the
# percentile tilt alone is nearly cosmetic: summing six players washes out
# individual variance (central limit), so the top roster by the median and by p99
# was literally the same one. What separates a lineup when you're behind is being
# different from the field, so that's the lever chase has to pull. Below ~0.6 it
# never actually changed a pick. This is NOT the blanket contrarian fade the
# 5-slate back-test rejected — it applies only when we're genuinely behind and
# need separation to win.
SWAP_OWN_TILT = 0.70


def _score_rosters(rosters, players, aggression, n_sims, seed):
    """Rank with the main tool's simulator.

    Protect (below 0.5) simply weights the median harder — it does NOT buy chalk.
    Hugging the field would mean paying ceiling for ownership, and a low-owned
    play that projects the same is free differentiation. So the ownership tilt is
    one-sided: it applies only when chasing.
    """
    from engine import Lineup, simulate_and_score
    lus = [Lineup(list(r)) for r in rosters]
    simulate_and_score(lus, players, sims=n_sims, leverage=0.0, seed=seed)
    k = max(0.0, aggression - 0.5) * SWAP_OWN_TILT
    for lu in lus:
        m = lu.metrics
        # 0 -> mean, 0.5 -> the usual 85th-percentile ceiling, 1 -> p95
        if aggression <= 0.5:
            t = aggression / 0.5
            base = m["mean"] + t * (m["ceiling"] - m["mean"])
        else:
            t = (aggression - 0.5) / 0.5
            base = m["ceiling"] + t * (m["p95"] - m["ceiling"])
        m["swapScore"] = base - k * lu.total_own
    return lus


def _swap_payload(p, scored):
    act = scored.get(normalize_name(p.name))
    return {"name": p.name, "team": p.team, "salary": p.salary,
            "proj": round(p.proj, 1), "own": round(p.ownership, 1),
            "scored": None if act is None else round(act, 1)}


def run_late_swap(csv_text, dk_text, contest_text=None, options=None):
    """DK entries file + updated LineStar (+ optional contest standings) ->
    recommended swaps and a re-uploadable DK file. See the module note above."""
    options = options or {}
    players = parse_linestar((csv_text or "").strip())
    scored = parse_linestar_scored((csv_text or "").strip())
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
    cap_ct = max(1, round(_float(options.get("maxExposure"), 0.6) * n_lu))

    # Optional contest standings: replaces the projection-based pace proxy with a
    # real leaderboard position, and gives actual contest ownership for the
    # players already revealed.
    contest = parse_contest_standings(contest_text) if (contest_text or "").strip() else None
    target = cash_line = avg_slot = 0.0
    my_rank = {}
    if contest and contest["entries"]:
        avg_slot = _avg_open_slot(players, locked_names)
        target, cash_line = _field_targets(contest, avg_slot)
        my_rank = {e["entryId"]: e for e in contest["entries"]}
        for p in players:  # prefer real ownership where DK has revealed it
            actual = contest["ownership"].get(normalize_name(p.name))
            if actual is not None and actual > 0:
                p.ownership = actual

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
        open_idx = [i for i in range(ROSTER_SIZE)
                    if normalize_name(lineup[i].name) not in locked_names]
        locked_players = [lineup[i] for i in range(ROSTER_SIZE) if i not in open_idx]
        banked, expected, deficit, wdef = _pace_read(locked_players, scored)
        # Real standing beats the proxy: if the contest file gave us this entry,
        # drive aggression off the actual gap to a winning score.
        me = my_rank.get(e["entryId"])
        rank = proj_final = None
        if me is not None:
            banked = me["points"] or banked
            proj_final = banked + sum(lineup[i].proj for i in open_idx)
            aggr = _aggression_from_field(proj_final, target)
            rank = me["rank"]
        else:
            aggr = _aggression(wdef)
        rosters = _enumerate_rosters(lineup, players, locked_names, open_idx, slots)
        if not rosters:
            rosters = [list(lineup)]
        lus = _score_rosters(rosters, players, aggr, n_sims, seed=len(base_rows))
        cur = frozenset(normalize_name(p.name) for p in lineup)
        for lu in lus:
            lu.metrics["isCurrent"] = frozenset(
                normalize_name(p.name) for p in lu.players) == cur
        lus.sort(key=lambda l: -l.metrics["swapScore"])
        ranked.append(lus)
        base_rows.append({
            "entryId": e["entryId"], "open": len(open_idx),
            "banked": round(banked, 1), "expected": round(expected, 1),
            "pace": round(deficit, 1), "aggression": round(aggr, 2),
            "rank": rank, "projFinal": None if proj_final is None else round(proj_final, 1),
            "lineup": lineup,
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
        open_names = {normalize_name(lineup[i].name) for i in range(ROSTER_SIZE)
                      if normalize_name(lineup[i].name) not in locked_names}

        here = {normalize_name(p.name) for p in lineup}

        def exposure_ok(lu):
            # Only players this swap would ADD are checked — someone already in
            # the lineup isn't made more concentrated by staying.
            for p in lu.players:
                n = normalize_name(p.name)
                if n in here:
                    continue
                if counts.get(n, 0) + 1 > cap_ct:
                    return False
            return True

        pick, why = None, ""
        for lu in lus:
            if lu.metrics.get("isCurrent"):
                continue
            if lu.salary > SALARY_CAP:
                continue
            if not exposure_ok(lu):
                continue
            if SALARY_CAP - lu.salary > SWAP_MAX_LEFTOVER and current and \
                    lu.metrics["swapScore"] - cur_score < SWAP_MIN_GAIN * 2:
                continue  # only strand salary for a clearly better roster
            pick = lu
            break
        if pick and cur_score is not None and \
                pick.metrics["swapScore"] - cur_score < SWAP_MIN_GAIN:
            pick = None  # not worth the churn
        final = pick.players if pick else list(lineup)
        if pick:  # move exposure from the dropped players onto the added ones
            now = {normalize_name(p.name) for p in final}
            for n in here - now:
                counts[n] = max(0, counts.get(n, 0) - 1)
            for n in now - here:
                counts[n] = counts.get(n, 0) + 1
        rec = {
            "entryId": row["entryId"], "open": row["open"],
            "banked": row["banked"], "expected": row["expected"], "pace": row["pace"],
            "aggression": row["aggression"], "rank": row.get("rank"),
            "projFinal": row.get("projFinal"),
            "salary": sum(p.salary for p in final),
            "proj": round(sum(p.proj for p in final), 1),
            "score": round((pick or current).metrics["swapScore"], 1) if (pick or current) else None,
            "keep": pick is None,
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
            rec["in"] = [_swap_payload(p, scored) for p in final
                         if normalize_name(p.name) not in was]
            changed += 1
            gain_total += rec["gain"]
        rec["roster"] = final
        results.append(rec)

    # Re-uploadable DK file: every entry, changed or not, in DK's slot order.
    lines = ["Entry ID,Contest Name,Contest ID,Entry Fee," + ",".join(slots)]
    for e, rec in zip(entries, results):
        roster = rec.pop("roster", None)
        if not roster:
            continue
        cells = []
        for p in roster:
            info = dk["pool"].get(normalize_name(p.name))
            cells.append(f'"{p.name} ({info["dkId"]})"' if info else f'"{p.name}"')
        cname = e["contest"].replace('"', '""')
        lines.append(f'{e["entryId"]},"{cname}",{e["contestId"]},{e["fee"]},'
                     + ",".join(cells))
    return {
        "lockedPlayers": len(locked_names),
        "field": contest["field"] if contest else None,
        "target": round(target, 1) if contest else None,
        "cashLine": round(cash_line, 1) if contest else None,
        "entries": len(entries),
        "changed": changed,
        "gain": round(gain_total, 1),
        "slots": slots,
        "dkCsv": ("\n".join(lines) + "\n") if len(lines) > 1 else None,
        "swaps": results,
    }


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
        if self.path not in ("/api/optimize", "/api/lateswap", "/api/dkfill"):
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
            if self.path == "/api/lateswap":
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
