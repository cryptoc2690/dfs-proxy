"""Local WNBA DFS optimizer — a self-contained web app.

Run it, open the browser, drop in a DFF cheatsheet (and optionally the DK
salary CSV for player IDs), get lineups. Pure standard library — nothing to
install, no cloud, no API keys.

    python app.py                 # opens http://localhost:8000
"""

from __future__ import annotations

import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dk import load_players_from_text, normalize_name
from engine import build_gpp as optimize_gpp
from projections import CsvProjector, _estimate_ownership


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
            opp, date = d.get("opp", ""), d.get("game_date", "")
            l5, l10, szn = _f(d.get("L5_fppg_avg")), _f(d.get("L10_fppg_avg")), _f(d.get("szn_fppg_avg"))
        elif len(r) >= 18:  # column-shifted export: read by position
            name, inj, date = f"{r[0]} {r[1]}", r[3].strip(), r[4].strip()
            proj, own = _f(r[16]), 0.0
            sal, pos, team, opp = int(_f(r[11])), r[2].strip(), r[6].strip(), r[7].strip()
            l5, l10, szn = _f(r[13]), _f(r[14]), _f(r[15])
        else:
            continue
        nm = normalize_name(name)
        if nm:
            out[nm] = {"name": name.strip(), "proj": proj, "own": own, "inj": inj,
                       "sal": sal, "pos": pos, "team": team, "opp": opp, "date": date,
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


def _slate_date(players):
    dates = [p.game_date for p in players if p.game_date]
    if dates:
        return max(set(dates), key=dates.count)
    from datetime import datetime, timedelta
    et = datetime.utcnow() - timedelta(hours=4)  # WNBA plays in summer -> EDT
    return et.date().isoformat()


def _apply_removals(players, remove_names):
    """Zero a removed player AND push ~65% of their production onto teammates,
    weighted toward same-position replacements (their minutes/usage don't
    vanish — they flow to the next guys up). Returns the removed names."""
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


def _slate_type(players):
    pool = [p for p in players if p.proj > 0]
    cheap_best = max((p.proj for p in pool if p.salary <= 5500), default=0.0)
    return "stars-and-scrubs" if cheap_best >= 16 else "balanced"


def _apply_market(players, mk):
    """Apply the four balldontlie market adjustments in priority order."""
    league = mk.get("league_implied")
    for p in players:
        if p.proj <= 0:
            continue
        nm = normalize_name(p.name)

        # 1. Real recent minutes -> out of rotation (most reliable rotation cut).
        mins = mk["minutes"].get(nm)
        if mins is not None and mins < 8:
            p.proj = p.floor = p.ceil = 0.0
            p.notes.append("bdl: ~0 recent minutes")
            continue

        # 2. Market vs DFF -> move toward the market on a big disagreement (news).
        m = mk["market"].get(nm, {})
        mproj = m.get("proj")
        if mproj and abs(mproj - p.proj) >= 6 and abs(mproj - p.proj) >= 0.25 * p.proj:
            delta = mproj - p.proj
            ratio = (p.ceil / p.proj) if p.proj else 1.45
            p.proj, p.floor, p.ceil = round(mproj, 1), round(mproj * 0.7, 1), round(mproj * ratio, 1)
            p.notes.append(f"market {'+' if delta > 0 else ''}{round(delta)}")

        # 3. PRA juiced under -> slight hit, waived for top-10 blocks+steals.
        if m.get("pra_under") and mk["stocks_rank"].get(nm, 999) > 10:
            p.proj = round(p.proj * 0.93, 1)
            p.ceil = round(p.ceil * 0.95, 1)
            p.notes.append("PRA juiced under")

        # 4. Vegas implied total -> scale the ceiling by the game environment.
        if league and p.team in mk["implied"]:
            env = min(max(mk["implied"][p.team] / league, 0.9), 1.12)
            p.ceil = round(p.ceil * env, 1)


def _out_of_rotation(d):
    """Barely any production over the last 5 games (and little over the last 10)
    means the player isn't really in the rotation right now, no matter what a
    stale season average says. Exclude them — unless the projection itself is
    high enough (>=15) that the source clearly expects a return-to-role."""
    return d.get("l5", 0) <= 3 and d.get("l10", 0) <= 5 and d.get("proj", 0) < 15


def apply_dff(players, text):
    """Project from a DFF cheatsheet. Returns a source label."""
    dmap = parse_dff(text)
    if not dmap:
        return None
    real_own = any(d["own"] > 0 for d in dmap.values())
    for p in players:
        d = dmap.get(normalize_name(p.name))
        if not d:
            # DFF is the source of truth: it lists everyone expected to play, so
            # a DK-CSV player DFF omits is a scratch/trade/benching DK just hasn't
            # flagged yet (Betnijah, Makani, ...). Exclude them — do NOT resurrect
            # them from DraftKings' stale season average.
            p.proj = p.floor = p.ceil = 0.0
            p.notes.append("not in DFF — excluded")
            continue
        if str(d["inj"]).upper() in ("O", "OUT"):
            p.status = "OUT"
            p.proj = p.floor = p.ceil = 0.0
            p.notes.append("OUT — excluded")
            continue
        if _out_of_rotation(d):
            p.proj = p.floor = p.ceil = 0.0
            p.notes.append("0 recent minutes — out of rotation")
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
        out = str(d["inj"]).upper() in ("O", "OUT") or _out_of_rotation(d)
        p = Player(name=d["name"], dk_id=f"dff-{i}", salary=d["sal"], team=d["team"],
                   opponent=d["opp"], game=f"{d['team']}@{d['opp']}",
                   is_guard=d["pos"].strip().upper().startswith("G"),
                   avg_points=d["proj"], status=("OUT" if out else ""),
                   starting="", game_date=d.get("date", ""))
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
    csv_text = (csv_text or "").strip()
    dff_text = options.get("dff", "") or ""
    if csv_text:  # DK file is the pool + IDs; DFF (if given) overlays projections
        players = load_players_from_text(csv_text)
        dff_label = apply_dff(players, dff_text) if dff_text.strip() else None
        source_label = dff_label or "csv-only"
        if not dff_label:
            CsvProjector().project(players)
    elif dff_text.strip():  # DFF-only — build the pool from the cheatsheet
        dmap = parse_dff(dff_text)
        if not dmap:
            return {"error": "Couldn't read that DFF cheatsheet."}
        players = build_players_from_dff(dmap)
        has_own = any(d["own"] > 0 for d in dmap.values())
        source_label = ("dff+ownership" if has_own else "dff") + " · names only (no DK IDs)"
    else:
        return {"error": "Upload a DraftKings salary CSV or a DFF cheatsheet."}

    # Manual removals (out / traded / benched — e.g. a late scratch the sheet
    # hasn't caught, or someone who missed shootaround). Zero them and push
    # their minutes/usage onto teammates, so the redistribution is real rather
    # than just deleting a body. Do it BEFORE market/ownership so both reflect
    # the post-removal projections.
    removed = _apply_removals(players, _parse_names(options.get("remove")))
    if removed and "ownership" not in source_label:
        _estimate_ownership(players)

    # Optional balldontlie market layer — real recent minutes, Vegas implied
    # totals, market-vs-DFF divergence, and the PRA-under-juice haircut. Only
    # where it adds EV; degrades silently to pure DFF if the key/API is absent.
    api_key = (options.get("apiKey") or "").strip()
    if api_key:
        slate_date = _slate_date(players)
        try:
            from bdl import enrich
            mk = enrich(api_key, players, slate_date, int(slate_date[:4]))
        except Exception:  # noqa: BLE001
            mk = {"ok": False}
        if mk.get("ok"):
            had_own = "ownership" in source_label
            _apply_market(players, mk)
            source_label += " + market"
            if mk.get("notes"):   # surface any sub-feed that didn't parse
                source_label += " (" + "; ".join(mk["notes"]) + ")"
            if not had_own:       # proj changed -> refresh the value-based ownership
                _estimate_ownership(players)

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
        return {"error": "Not enough playable players — check the file.",
                "source": source_label}

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

    return {
        "source": source_label,
        "slateType": _slate_type(players),
        "removed": removed,
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
            } for slot, p in zip(["F", "F", "F", "G", "G", "UTIL"], lu.dk_slots())],
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
        if self.path != "/api/optimize":
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
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
