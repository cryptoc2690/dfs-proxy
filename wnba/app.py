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
from projections import make_projector


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

    projector = make_projector(source, season=season)
    players = projector.project(load_players_from_text(csv_text))

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
        return {"error": "Not enough playable players after projections. "
                         "Check the CSV.", "source": getattr(projector, "name", "?")}

    cores = [p for p in playable if p.core]
    lineups = optimize_gpp(
        players,
        n=_int(options.get("n"), 20),
        pool_size=max(120, _int(options.get("n"), 20) * 8),
        min_stack=_int(options.get("stack"), 2),
        max_per_team=_int(options.get("maxPerTeam"), 4),
        max_exposure=_float(options.get("maxExposure"), 0.6),
        leverage=_float(options.get("leverage"), 0.35),
        n_sims=_int(options.get("sims"), 5000),
        cores=cores,
        # No forced count — the data (projection vs. leverage) decides how many
        # cores land in each lineup; exposure caps keep them diversified.
        min_cores=0,
    )

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
            "upload": [p.label() for p in lu.dk_slots()],
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
            csv_text = payload.get("csv") or ""
            if not csv_text.strip():
                return self._send(400, json.dumps({"error": "No CSV provided."}))
            result = run_optimize(csv_text, payload.get("options", {}))
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
