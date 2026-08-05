"""Local WNBA DFS optimizer — a self-contained web app.

Run it, open the browser, drag in your DraftKings CSV, get lineups. No cloud,
no base44, no Vercel. The balldontlie calls happen here in the server process,
so there's no CORS problem and your API key never leaves your machine.

    pip install -r requirements.txt
    python app.py                 # opens http://localhost:8000

Set BALLDONTLIE_API_KEY in your environment (or paste it into the GUI once)
to get live props-based projections; without it the app still works off the
CSV's own averages.
"""

from __future__ import annotations

import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dk import load_players_from_text, normalize_name
from engine import build_gpp as optimize_gpp
from projections import _estimate_ownership, make_projector


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_dff(text):
    """Parse a DailyFantasyFuel cheatsheet -> {norm_name: {proj, own, inj}}.

    Handles two shapes: a well-formed export (row width == header, read by
    column name) and the column-shifted export this project was tested against
    (missing the injury_status field, so everything after position_alt slides
    left) — detected by row width and read positionally.
    """
    import csv as _csv
    import io
    rows = list(_csv.reader(io.StringIO((text or "").lstrip("﻿"))))
    if len(rows) < 2:
        return {}
    header = [h.strip() for h in rows[0]]
    out = {}
    for r in rows[1:]:
        if len(r) == len(header) and "ppg_projection" in header:
            d = dict(zip(header, r))
            name = f"{d.get('first_name','')} {d.get('last_name','')}"
            proj, own = _f(d.get("ppg_projection")), _f(d.get("ownership_projection"))
            inj = (d.get("injury_status") or d.get("position_alt") or "").strip()
            sal, pos, team = int(_f(d.get("salary"))), d.get("position", ""), d.get("team", "")
            opp = d.get("opp", "")
            l5, l10, szn = _f(d.get("L5_fppg_avg")), _f(d.get("L10_fppg_avg")), _f(d.get("szn_fppg_avg"))
        elif len(r) >= 18:  # column-shifted export: read by position
            name, inj = f"{r[0]} {r[1]}", r[3].strip()
            proj, own = _f(r[16]), 0.0
            sal, pos, team, opp = int(_f(r[11])), r[2].strip(), r[6].strip(), r[7].strip()
            l5, l10, szn = _f(r[13]), _f(r[14]), _f(r[15])
        else:
            continue
        nm = normalize_name(name)
        if nm:
            out[nm] = {"name": name.strip(), "proj": proj, "own": own, "inj": inj,
                       "sal": sal, "pos": pos, "team": team, "opp": opp,
                       "l5": l5, "l10": l10, "szn": szn}
    return out


def _dff_range(proj, l5, l10, szn):
    """Floor/ceiling anchored to the PROJECTION (which already reflects current
    minutes/role), with a modest boom bump for players whose RECENT form is
    trending above their projection. Deliberately ignores the season average for
    upside — a stale high season number (role since collapsed) must NOT invent a
    ceiling, or low-minutes punts sneak in. So ceiling stays ~1.4x-1.6x of a
    real projection, never divorced from it."""
    recent = max([v for v in (l5, l10) if v > 0] or [proj])
    boom = 1.4 + 0.2 * max(0.0, min(1.0, recent / proj - 1)) if proj > 0 else 1.4
    return round(proj * 0.70, 1), round(proj * boom, 1)


def apply_dff(players, text):
    """Project from a DFF cheatsheet. Returns a source label."""
    dmap = parse_dff(text)
    if not dmap:
        return None
    real_own = any(d["own"] > 0 for d in dmap.values())
    for p in players:
        d = dmap.get(normalize_name(p.name))
        if not d:
            p.proj = p.avg_points
            p.floor, p.ceil = round(p.avg_points * 0.72, 1), round(p.avg_points * 1.4, 1)
            p.notes.append("no DFF match — DK avg")
            continue
        if str(d["inj"]).upper() in ("O", "OUT"):
            p.status = "OUT"
            p.proj = p.floor = p.ceil = 0.0
            p.notes.append("OUT — excluded")
            continue
        p.proj = round(d["proj"], 1)
        p.floor, p.ceil = _dff_range(d["proj"], d["l5"], d["l10"], d["szn"])
        p.notes.append("DFF projection")
        if real_own and d["own"] > 0:
            p.ownership = d["own"]
    if real_own:
        for p in players:
            if p.proj > 0 and p.ownership == 0:
                p.ownership = 8.0
        return "dff+ownership"
    _estimate_ownership(players)
    return "dff"


def build_players_from_dff(dmap):
    """Build the player pool from a DFF cheatsheet alone (no DK file). No DK
    IDs are available, so lineups export by name for manual entry."""
    from dk import Player
    players = []
    for i, (nm, d) in enumerate(dmap.items()):
        if d["sal"] < 3000:
            continue
        out = str(d["inj"]).upper() in ("O", "OUT")
        p = Player(name=d["name"], dk_id=f"dff-{i}", salary=d["sal"], team=d["team"],
                   opponent=d["opp"], game=f"{d['team']}@{d['opp']}",
                   is_guard=d["pos"].strip().upper().startswith("G"),
                   avg_points=d["proj"], status=("OUT" if out else ""),
                   starting="", game_date="")
        if out:
            p.proj = p.floor = p.ceil = 0.0
        else:
            p.proj = round(d["proj"], 1)
            p.floor, p.ceil = _dff_range(d["proj"], d["l5"], d["l10"], d["szn"])
            if d["own"] > 0:
                p.ownership = d["own"]
        players.append(p)
    if any(d["own"] > 0 for d in dmap.values()):
        for p in players:
            if p.proj > 0 and p.ownership == 0:
                p.ownership = 8.0
    else:
        _estimate_ownership(players)
    return players


def _upload_str(p):
    """DK-import string. Uses the real 'Name (ID)' when a DK file supplied the
    ID; otherwise just the name (DFF-only run -> manual entry)."""
    return p.name if (not p.dk_id or p.dk_id.startswith("dff-")) else f"{p.name} ({p.dk_id})"


def _parse_names(text):
    """Turn pasted lines (possibly with extra spreadsheet columns) into a set of
    normalized player names. Takes the first tab/comma field of each line."""
    names = set()
    for line in (text or "").splitlines():
        cell = line.split("\t")[0].split(",")[0].strip()
        if len(cell) > 1:
            names.add(normalize_name(cell))
    return names

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8000"))


def run_optimize(csv_text: str, options: dict) -> dict:
    """Project + build lineups, returning plain dicts for the GUI."""
    api_key = (options.get("apiKey") or "").strip()
    if api_key:
        os.environ["BALLDONTLIE_API_KEY"] = api_key
    source = options.get("source") or "auto"
    season = _int(options.get("season"), None)

    csv_text = (csv_text or "").strip()
    dff_text = options.get("dff", "") or ""
    projector = None
    if csv_text:  # DK file is the pool + IDs; DFF (if given) overlays projections
        players = load_players_from_text(csv_text)
        dff_label = apply_dff(players, dff_text) if dff_text.strip() else None
        if dff_label:
            source_label_base = dff_label
        else:
            projector = make_projector(source, season=season)
            projector.project(players)
            source_label_base = None  # computed below from notes
    elif dff_text.strip():  # DFF-only — build the pool from the cheatsheet
        dmap = parse_dff(dff_text)
        if not dmap:
            return {"error": "Couldn't read that DFF cheatsheet."}
        players = build_players_from_dff(dmap)
        has_own = any(d["own"] > 0 for d in dmap.values())
        source_label_base = ("dff+ownership" if has_own else "dff") + " · names only (no DK IDs)"
    else:
        return {"error": "Upload a DraftKings salary CSV or a DFF cheatsheet."}

    # Game-theory pool as an OWNERSHIP signal, never a filter — a sharp play
    # outside his pool is rare but real, so nobody is excluded. Cores read as
    # heavy chalk; in-pool as chalk; off-pool gets a discount so a strong one
    # surfaces as leverage and the optimizer will happily use it.
    core_names = _parse_names(options.get("cores"))
    pool_names = _parse_names(options.get("pool"))
    for p in players:
        nm = normalize_name(p.name)
        p.core = nm in core_names
        if p.proj <= 0:
            continue
        if p.core:
            # A sharp's core is real signal (these hit), so give a small
            # projection edge — enough to surface them naturally, not force a
            # count. Tiny ownership tick keeps the leverage math honest.
            p.proj = round(p.proj * 1.06, 1)
            p.ceil = round(p.ceil * 1.06, 1)
            p.ownership = min(p.ownership + 4, 65)
            p.notes.append("GT core")
        elif pool_names and nm in pool_names:
            p.ownership = min(p.ownership + 5, 65)
        elif pool_names:  # off his pool -> contrarian leverage
            p.ownership = max(p.ownership * 0.6, 0.5)
            p.notes.append("off-pool leverage")

    playable = [p for p in players if p.proj > 0]
    if len(playable) < 6:
        return {"error": "Not enough playable players (check the CSV, or you "
                         "faded too many).", "source": source_label_base or "?"}

    cores = [p for p in playable if p.core]
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
        # No forced count — the data (projection vs. leverage) decides how many
        # cores land in each lineup; exposure caps keep them diversified.
        min_cores=0,
        # Differentiation: no two of your lineups may share more than this many
        # players, so the set is unique even when everyone runs the same
        # projections (WNBA's whole problem). The data still drives which plays;
        # this just forces the set to spread — including the A'ja on/off split.
        max_overlap=_int(options.get("maxOverlap"), 4),
    )

    if source_label_base:
        source_label = source_label_base
    else:
        csv_fallback = any("csv fallback" in n or "no BALLDONTLIE_API_KEY" in n
                           for p in players for n in p.notes)
        props_used = any("market props" in n for p in players for n in p.notes)
        source_label = ("props-first" if props_used else
                        "csv-only" if csv_fallback else
                        getattr(projector, "name", "?"))

    return {
        "source": source_label,
        "slate": {
            "date": next((p.game_date for p in players if p.game_date), ""),
            "games": sorted({p.game for p in players if p.game}),
        },
        "out": [p.name for p in players if p.out],
        "players": [{
            "name": p.name, "team": p.team, "pos": p.pos, "salary": p.salary,
            "game": p.game, "proj": round(p.proj, 1), "floor": round(p.floor, 1),
            "ceil": round(p.ceil, 1), "own": round(p.ownership, 1),
            "core": p.core, "notes": "; ".join(p.notes),
        } for p in sorted(playable, key=lambda p: -p.proj)],
        "lineups": [{
            "rank": i + 1, "salary": lu.salary, "proj": lu.proj,
            "ceiling": round(lu.metrics.get("ceiling", 0), 1),
            "mean": round(lu.metrics.get("mean", 0), 1),
            "totalOwn": lu.total_own,
            "cores": sum(1 for p in lu.players if p.core),
            "stacks": [f"{g}:{sum(1 for p in lu.players if p.game == g)}"
                       for g in lu.games()
                       if sum(1 for p in lu.players if p.game == g) >= 2],
            "players": [{
                "slot": slot, "name": p.name, "team": p.team, "pos": p.pos,
                "salary": p.salary, "proj": round(p.proj, 1), "core": p.core,
            } for slot, p in zip(["G", "G", "F", "F", "UTIL", "UTIL"], lu.dk_slots())],
            "upload": [_upload_str(p) for p in lu.dk_slots()],
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
        if self.path not in ("/api/optimize", "/api/check"):
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/check":
                from bdl import check_key
                return self._send(200, json.dumps(check_key(payload.get("apiKey", ""))))
            opts = payload.get("options", {})
            csv_text = payload.get("csv") or ""
            if not csv_text.strip() and not (opts.get("dff") or "").strip():
                return self._send(400, json.dumps(
                    {"error": "Upload a DraftKings CSV or a DFF cheatsheet."}))
            result = run_optimize(csv_text, opts)
            code = 400 if result.get("error") else 200
            self._send(code, json.dumps(result))
        except Exception as e:  # noqa: BLE001
            self._send(500, json.dumps({"error": str(e)}))


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    key = "set" if os.environ.get("BALLDONTLIE_API_KEY") else "not set (CSV-only until you add it)"
    print(f"\n  WNBA DFS optimizer running at  {url}")
    print(f"  BALLDONTLIE_API_KEY: {key}")
    print("  Press Ctrl+C to stop.\n")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


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


# The GUI is defined in gui.py to keep this file focused on the server.
from gui import INDEX_HTML  # noqa: E402


if __name__ == "__main__":
    main()
