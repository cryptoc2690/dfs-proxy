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

from dk import ROSTER_SIZE, Player, normalize_name
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
# Reliability thresholds. A play is trustworthy only if it BOTH plays enough AND
# stuffs the stat sheet — minutes alone don't cut it (a 28-minute one-category
# scorer busts to nothing on a cold night). Below either bar => a "risk body",
# which the engine rations to at most one per lineup.
RISK_MIN_MINUTES = 18.0
RISK_MIN_STUFFER = 8.0   # DK pts from reb/ast/stl/blk — production that shows up
                         # regardless of shooting


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
    classify each playable player as reliable or a risk body. LineStar keeps
    ownership of the projection; this only adds the reliability read (and a note
    where the two sources disagree hard — LineStar's number still wins)."""
    dmap = parse_daily_projections(text)
    if not dmap:
        return False  # no reliability read without the file; cap stays off
    for p in players:
        if p.proj <= 0:
            continue
        d = dmap.get(normalize_name(p.name))
        if not d:  # unmatched -> fall back to the starter label
            p.risk = not p.starter
            continue
        p.minutes = d["minutes"]
        p.stuffer = d["stuffer"]
        p.risk = p.minutes < RISK_MIN_MINUTES or p.stuffer < RISK_MIN_STUFFER
        why = []
        if p.minutes < RISK_MIN_MINUTES:
            why.append(f"{p.minutes:.0f}min")
        if p.stuffer < RISK_MIN_STUFFER:
            why.append(f"stuffer {p.stuffer:.0f}")
        p.notes.append("risk: " + ", ".join(why) if p.risk
                       else f"reliable ({p.minutes:.0f}min, stuffer {p.stuffer:.0f})")
        # Cross-source disagreement flag (informational; LineStar's proj is used).
        if abs(d["compdk"] - p.proj) >= 6 and abs(d["compdk"] - p.proj) >= 0.3 * p.proj:
            p.notes.append(f"src split: proj-file {d['compdk']:.0f} vs LS {p.proj:.0f}")
    return True


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
    return [p.name for p, _ in removed]


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

    # Reliability read from the daily-projections file (minutes + stat-stuffer
    # floor). LineStar still owns the projection; this only classifies each play
    # as reliable vs a risk body, which the engine rations. Optional — falls back
    # to the starter label if no file is supplied.
    had_minutes = apply_daily_projections(players, options.get("minutes") or "")
    if had_minutes:
        source_label += " + minutes"

    # Manual removals (late scratch / missed shootaround the projection hasn't
    # caught). Zero them and flow their minutes/usage to teammates.
    removed = _apply_removals(players, _parse_names(options.get("remove")))

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
    max_off_pool = _int(options.get("maxOffPool"), 0) if pool_names else None
    # Decide the slate read ONCE, so the engine's salary reserve and the UI badge
    # are the same determination (not two independent computations on different
    # player sets).
    slate_type = _slate_type(players)
    lineups = optimize_gpp(
        players,
        n=_int(options.get("n"), 20),
        pool_size=max(120, _int(options.get("n"), 20) * 8),
        min_stack=_int(options.get("stack"), 2),
        max_per_team=_int(options.get("maxPerTeam"), 4),
        max_exposure=_float(options.get("maxExposure"), 0.6),
        leverage=_float(options.get("leverage"), 0.15),
        n_sims=_int(options.get("sims"), 5000),
        cores=cores,
        # Anchor rule: every lineup built around at least this many cores (which
        # ones vary across the set). Default 1 when cores are set — the sharp's
        # cores keep landing in winners, so guarantee the build is around them.
        min_cores=(_int(options.get("minCores"), 1) if cores else 0),
        max_overlap=_int(options.get("maxOverlap"), 4),
        max_off_pool=max_off_pool,
        stars_and_scrubs=(slate_type == "stars-and-scrubs"),
        # Ration bust-prone bodies. A "risk body" is low-minute OR scoring-
        # dependent (thin stat-stuffer floor) — the kind that busts to nothing
        # (Sophie: 28 min, 5.75 FP). At most this many per lineup; reliable cheap
        # stuffers (Cotie, Kiah Stokes) are unlimited and fill the rest. Only
        # enforced when the minutes file gave us a real reliability read.
        max_risk=(_int(options.get("maxRisk"), 1) if had_minutes else None),
    )

    return {
        "source": source_label,
        "slateType": slate_type,
        "poolActive": bool(pool_names),
        "removed": removed,
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
            "core": p.core, "starter": p.starter, "notes": "; ".join(p.notes),
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
        if self.path != "/api/optimize":
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            opts = payload.get("options", {})
            csv_text = payload.get("csv") or ""
            if not csv_text.strip():
                return self._send(400, json.dumps(
                    {"error": "Drop your LineStar projections CSV."}))
            result = run_optimize(csv_text, opts)
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
