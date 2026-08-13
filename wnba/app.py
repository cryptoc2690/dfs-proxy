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
        contest = e["contest"].replace('"', '""')
        lines.append(f'{e["entryId"]},"{contest}",{e["contestId"]},{e["fee"]},'
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
# Re-optimize already-entered lineups against an UPDATED LineStar after news
# (a scratch, a surprise benching). Keep locked players (games already tipped)
# and your good plays fixed; move only off dead weight (projecting under a
# threshold) and reinvest the freed salary for the highest projection. No
# ownership term on purpose: late swap is inherently leverage-positive (you pivot
# off news-killed chalk onto news-created value), and the ownership numbers are
# stale the moment the news drops — so projection is the only honest objective.
# Default high, so the panel RE-OPTIMIZES every unlocked slot out of the box.
# It used to default low ("only drop dead weight"), which meant that once games
# tipped — when there's rarely any dead weight left — every entry came back
# "keep as-is" unless you knew to move a slider. Lower it for news-only mode.
LATE_RELEASE_DEFAULT = 45.0
_LS_SLOTS = ["F", "F", "F", "G", "G", "UTIL"]


def parse_entered_lineups(text):
    """Parse a DK-style entered-lineups export (header F,F,F,G,G,UTIL then one
    row of 6 names per lineup) into a list of 6-name lists."""
    import csv as _csv
    import io
    out = []
    for row in _csv.reader(io.StringIO((text or "").lstrip("﻿"))):
        cells = [c.strip() for c in row if c and c.strip()]
        if len(cells) < ROSTER_SIZE:
            continue
        if all(c.upper() in ("F", "G", "UTIL", "GUARD", "FORWARD") for c in cells[:ROSTER_SIZE]):
            continue  # header row
        out.append(cells[:ROSTER_SIZE])
    return out


def _optimize_swap(lineup, players, locked_games, release_max_proj, slots=None,
                   locked_names=None, ftop=20, gtop=20):
    """One lineup, slotted in `slots` order (DK files use G,G,F,F,F,UTIL; our own
    export uses F,F,F,G,G,UTIL). A slot is releasable only if its game is OPEN and
    the player is dead weight (proj < release_max_proj); studs and locked players
    stay. Refill releasable slots to maximize (projection, then salary) under the
    cap. Returns a dict the GUI renders."""
    import itertools
    slots = slots or _LS_SLOTS
    locked_names = locked_names or set()

    def game_of(p):
        return p.game or p.team

    def is_locked(p):  # DK's own per-player lock wins; game locks are the fallback
        return normalize_name(p.name) in locked_names or game_of(p) in locked_games

    open_idx = [i for i in range(ROSTER_SIZE)
                if not is_locked(lineup[i]) and lineup[i].proj < release_max_proj]
    keepers = [lineup[i] for i in range(ROSTER_SIZE) if i not in open_idx]
    released = [lineup[i] for i in open_idx]
    keeper_names = {p.name for p in keepers}
    budget = SALARY_CAP - sum(p.salary for p in keepers)
    needF = sum(1 for i in open_idx if slots[i] == "F")
    needG = sum(1 for i in open_idx if slots[i] == "G")
    needU = sum(1 for i in open_idx if slots[i] == "UTIL")

    cand = [p for p in players if not is_locked(p) and p.proj >= 12.0
            and p.name not in keeper_names]
    cand = list({p.name: p for p in (cand + released)}.values())
    rel_names = {p.name for p in released}

    def trim(lst, top):  # top-N by proj, but never drop an original keep-option
        lst = sorted(lst, key=lambda p: -p.proj)
        return lst[:top] + [p for p in lst[top:] if p.name in rel_names]
    forwards = trim([p for p in cand if not p.is_guard], ftop)
    guards = trim([p for p in cand if p.is_guard], gtop)

    best = None  # (proj, salary, chosen)
    for fs in itertools.combinations(forwards, needF):
        for gs in itertools.combinations(guards, needG):
            base = list(fs) + list(gs)
            sal = sum(p.salary for p in base)
            if sal > budget:
                continue
            if needU:
                used = {p.name for p in base}
                for u in cand:
                    if u.name in used or sal + u.salary > budget:
                        continue
                    key = (sum(p.proj for p in base) + u.proj, sal + u.salary)
                    if best is None or key > (best[0], best[1]):
                        best = (key[0], key[1], base + [u])
            else:
                key = (sum(p.proj for p in base), sal)
                if best is None or key > (best[0], best[1]):
                    best = (key[0], key[1], base)

    old_proj = round(sum(p.proj for p in lineup), 1)
    old_sal = sum(p.salary for p in lineup)
    if best is None:  # nothing legal to change
        return {"keep": True, "oldProj": old_proj, "oldSalary": old_sal,
                "roster": list(lineup), "note": "no legal swap"}
    chosen = best[2]
    orig = {p.name for p in released}
    new = {p.name for p in chosen}
    out_names, in_names = orig - new, new - orig
    # Rebuild the roster IN SLOT ORDER: locked/kept players stay exactly where
    # they were (DK pins a locked player to its slot), and the chosen players
    # fill the vacated slots by type. Without this the export could move a
    # locked player to a different slot and DK would reject the upload.
    new6 = [None] * ROSTER_SIZE
    for i in range(ROSTER_SIZE):
        if i not in open_idx:
            new6[i] = lineup[i]
    gs = [p for p in chosen if p.is_guard]
    fs = [p for p in chosen if not p.is_guard]
    for i in open_idx:
        if slots[i] == "G" and gs:
            new6[i] = gs.pop(0)
        elif slots[i] == "F" and fs:
            new6[i] = fs.pop(0)
    leftover_picks = gs + fs
    for i in open_idx:
        if new6[i] is None and leftover_picks:
            new6[i] = leftover_picks.pop(0)
    if any(p is None for p in new6):  # couldn't slot legally — leave it alone
        return {"keep": True, "oldProj": old_proj, "oldSalary": old_sal,
                "roster": list(lineup)}
    new_proj = round(sum(p.proj for p in new6), 1)
    if not out_names or new_proj - old_proj < 2.0:  # skip trivial churn
        return {"keep": True, "oldProj": old_proj, "oldSalary": old_sal,
                "roster": list(lineup)}
    info = lambda p: {"name": p.name, "team": p.team, "salary": p.salary,
                      "proj": round(p.proj, 1), "own": round(p.ownership, 1),
                      "starter": p.starter}
    return {
        "keep": False, "oldProj": old_proj, "newProj": new_proj,
        "gain": round(new_proj - old_proj, 1),
        "oldSalary": old_sal, "newSalary": sum(p.salary for p in new6),
        "leftover": SALARY_CAP - sum(p.salary for p in new6),
        "out": [info(p) for p in released if p.name in out_names],
        "in": [info(p) for p in chosen if p.name in in_names],
        "roster": new6,
    }


def late_swap(entered, players, locked_games, release_max_proj, slots=None, labels=None,
              locked_names=None):
    by_norm = {normalize_name(p.name): p for p in players}
    results = []
    for idx, names in enumerate(entered, 1):
        label = (labels[idx - 1] if labels and idx <= len(labels) else None) or f"L{idx}"
        lineup, missing = [], []
        for nm in names:
            p = by_norm.get(normalize_name(nm))
            (lineup if p else missing).append(p if p else nm)
        if len(lineup) != ROSTER_SIZE:
            results.append({"lineup": idx, "label": label,
                            "error": f"couldn't match: {', '.join(missing)}"})
            continue
        rec = _optimize_swap(lineup, players, set(locked_games), release_max_proj, slots,
                             locked_names)
        rec["lineup"] = idx
        rec["label"] = label
        results.append(rec)
    return results


def run_late_swap(csv_text, lineups_text, locked_games, release_max_proj,
                  dk_text=None, auto_lock=True):
    """Late swap from either our own lineups CSV or a DK entries export. The DK
    file is preferred: it names the slot order and carries game start times, so
    locked games are detected automatically (the user can still override)."""
    players = parse_linestar((csv_text or "").strip())
    if sum(1 for p in players if p.proj > 0) < ROSTER_SIZE:
        return {"error": "Drop your updated LineStar CSV first (the same file the "
                         "optimizer uses)."}
    slots, labels, autodetected, locked_names = None, None, [], set()
    slate_games = sorted({p.game for p in players if p.game})
    if (dk_text or "").strip():
        dk = parse_dk_entries(dk_text)
        # DK marks locked players directly — use that as the source of truth.
        locked_names = {n for n, p in dk["pool"].items() if p.get("locked")}
        used_entries = [e for e in dk["entries"] if len(e["names"]) == ROSTER_SIZE]
        entered = [e["names"] for e in used_entries]
        labels = [f'#{e["entryId"]}' for e in used_entries]
        slots = dk["slots"]
        if dk["games"]:
            slate_games = sorted(dk["games"])
        if not entered:
            return {"error": "That DK file has no filled-in lineups yet — enter your "
                             "lineups on DK first, then export."}
        # DK's own per-player (LOCKED) markers are ground truth for what can still
        # move. Only fall back to locking by tip-off time when the file carries no
        # markers at all — otherwise a file exported the morning after a slate
        # (start times now in the past) would lock every game and freeze the whole
        # board, even though DK itself says those players are still swappable.
        if locked_names:
            autodetected = []
        else:
            autodetected = dk_locked_games(dk["games"])
            if auto_lock:
                locked_games = sorted(set(locked_games) | set(autodetected))
    else:
        entered = parse_entered_lineups(lineups_text)
        if not entered:
            return {"error": "Couldn't read that lineups file — upload your DK entries "
                             "export, or a CSV with an F,F,F,G,G,UTIL header and one "
                             "row of 6 names per lineup."}
    swaps = late_swap(entered, players, locked_games, float(release_max_proj),
                      slots, labels, locked_names)
    # Build a re-uploadable DK file from the swapped rosters. This is the actual
    # deliverable — reading OUT/IN cards and hand-editing 15 entries on a phone
    # before lock isn't realistic. Every entry is written (changed or not) so the
    # whole file can go back to DK in one upload.
    dk_csv, changed = None, 0
    if (dk_text or "").strip():
        out_slots = slots or _LS_SLOTS
        lines = ["Entry ID,Contest Name,Contest ID,Entry Fee," + ",".join(out_slots)]
        for e, s in zip(used_entries, swaps):
            roster = s.get("roster")
            if not roster:
                continue
            if not s.get("keep"):
                changed += 1
            cells = []
            for p in roster:
                rec = dk["pool"].get(normalize_name(p.name))
                cells.append(f'"{p.name} ({rec["dkId"]})"' if rec else f'"{p.name}"')
            contest = e["contest"].replace('"', '""')
            lines.append(f'{e["entryId"]},"{contest}",{e["contestId"]},{e["fee"]},'
                         + ",".join(cells))
        if len(lines) > 1:
            dk_csv = "\n".join(lines) + "\n"
    for s in swaps:  # Player objects aren't JSON-serializable
        s.pop("roster", None)
    return {
        "games": slate_games,
        "locked": sorted(locked_games),
        "autoLocked": autodetected,
        "lockedPlayers": len(locked_names),
        "slots": slots or _LS_SLOTS,
        "dkCsv": dk_csv,
        "changed": changed,
        "gain": round(sum(s.get("gain", 0) for s in swaps), 1),
        "swaps": swaps,
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
                result = run_late_swap(csv_text, payload.get("lineups") or "",
                                       payload.get("locked") or [],
                                       payload.get("releaseMaxProj", LATE_RELEASE_DEFAULT),
                                       payload.get("dk") or "",
                                       payload.get("autoLock", True))
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
