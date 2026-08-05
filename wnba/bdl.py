"""Lean balldontlie WNBA enrichment — used only where the market adds real EV
on top of the DFF projections. Four adjustments, all optional and fully
graceful: if the key is missing or any call fails, the tool just runs on DFF.

1. Real recent minutes  -> precise out-of-rotation detection (beats DFF's
   fantasy-average proxy; catches trades/benchings like Betnijah/Monique).
2. Vegas implied totals  -> scale ceilings and target stacks by the true game
   environment.
3. Market vs DFF          -> when the market projection strongly disagrees with
   DFF, move toward the market (catches same-day news the sheet missed).
4. PRA under-juice haircut -> if the book juices a player's PRA to the UNDER,
   a slight projection hit — waived if they're a top blocks+steals producer
   (defense still scores even when PRA sags).

Endpoints (base https://api.balldontlie.io/wnba/v1): games, odds,
odds/player_props, player_stats, player_season_stats.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta

from dk import normalize_name

BASE = "https://api.balldontlie.io/wnba/v1"


class _Client:
    def __init__(self, api_key):
        self.key = (api_key or "").strip()
        try:
            self.ctx = ssl.create_default_context()
        except Exception:  # noqa: BLE001
            self.ctx = None
        self.insecure = False

    def get(self, path, params=None):
        qs = ""
        if params:
            parts = []
            for k, v in params.items():
                if v is None:
                    continue
                for item in (v if isinstance(v, (list, tuple)) else [v]):
                    parts.append(f"{urllib.parse.quote(str(k))}[]={urllib.parse.quote(str(item))}"
                                 if isinstance(v, (list, tuple))
                                 else f"{urllib.parse.quote(str(k))}={urllib.parse.quote(str(item))}")
            qs = "?" + "&".join(parts)
        url = f"{BASE}{path}{qs}"
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"Authorization": self.key})
                with urllib.request.urlopen(req, timeout=20, context=self.ctx) as r:
                    return json.loads(r.read().decode())
            except Exception as e:  # noqa: BLE001 — macOS often lacks certs
                reason = getattr(e, "reason", None)
                if (isinstance(reason, ssl.SSLCertVerificationError)
                        or "CERTIFICATE_VERIFY_FAILED" in str(e)) and not self.insecure:
                    self.ctx = ssl._create_unverified_context()
                    self.insecure = True
                    continue
                if attempt == 2:
                    raise
        return {}

    def paginate(self, path, params, cap=6000):
        params = dict(params)
        params.setdefault("per_page", 100)
        rows, cursor = [], None
        while True:
            if cursor is not None:
                params["cursor"] = cursor
            data = self.get(path, params)
            rows += data.get("data", [])
            cursor = (data.get("meta") or {}).get("next_cursor")
            if not cursor or len(rows) >= cap:
                return rows


# ---- small helpers ------------------------------------------------------
def _first(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _implied_prob(american):
    o = _num(american)
    if o is None or o == 0:
        return None
    return 100 / (o + 100) if o > 0 else (-o) / (-o + 100)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return 0.0 if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def _to_min(raw):
    if raw is None:
        return 0
    s = str(raw).split(":")[0]
    try:
        return int(float(s))
    except ValueError:
        return 0


# ---- the one entry point ------------------------------------------------
def enrich(api_key, players, slate_date, season):
    """Return a dict of market signals keyed by normalized player name, plus
    implied team totals. Never raises — partial failures degrade to {}.
    """
    out = {"implied": {}, "league_implied": None, "minutes": {}, "stocks_rank": {},
           "market": {}, "ok": False, "notes": []}
    if not (api_key or "").strip():
        return out
    c = _Client(api_key)
    try:
        games = c.get("/games", {"dates": [slate_date], "per_page": 100}).get("data", [])
    except Exception as e:  # noqa: BLE001
        out["notes"].append(f"balldontlie unreachable: {type(e).__name__}")
        return out
    out["ok"] = True
    game_ids = [g["id"] for g in games]
    home_away = {g["id"]: ((g.get("home_team") or {}).get("abbreviation"),
                           (g.get("visitor_team") or {}).get("abbreviation")) for g in games}
    team_ids = {t for g in games for t in ((g.get("home_team") or {}).get("id"),
                                           (g.get("visitor_team") or {}).get("id")) if t}

    _implied_totals(c, slate_date, home_away, out)
    _recent_minutes_and_stocks(c, team_ids, slate_date, season, out)
    _market_projections(c, game_ids, out)
    return out


def _implied_totals(c, slate_date, home_away, out):
    # Game odds are date-based (props are per-game); one call for the slate.
    try:
        rows = c.get("/odds", {"dates": [slate_date]}).get("data", [])
    except Exception:  # noqa: BLE001 — fall back to a bare per-date query
        try:
            rows = c.get("/odds", {"date": slate_date}).get("data", [])
        except Exception as e:  # noqa: BLE001
            out["notes"].append(f"odds skipped: {type(e).__name__}")
            return
    by_game = {}
    for r in rows:
        gid = r.get("game_id") or (r.get("game") or {}).get("id")
        if gid is None:
            continue
        if gid not in by_game or str(r.get("vendor", "")).lower() == "draftkings":
            by_game[gid] = r
    vals = {}
    for gid, row in by_game.items():
        total = _num(_first(row, "total_value", "total", "over_under", "game_total"))
        sh = _num(_first(row, "spread_home_value", "spread_home", "home_spread", "spread"))
        h, a = home_away.get(gid, (None, None))
        if total is None or not h:
            continue
        vals[h] = total / 2 - (sh / 2 if sh is not None else 0)
        vals[a] = total / 2 + (sh / 2 if sh is not None else 0)
    if vals:
        out["implied"] = vals
        out["league_implied"] = sum(vals.values()) / len(vals)
    elif rows:
        out["notes"].append("odds: no totals parsed")


def _recent_minutes_and_stocks(c, team_ids, slate_date, season, out):
    try:
        start = (datetime.fromisoformat(slate_date) - timedelta(days=21)).date().isoformat()
        games = c.paginate("/games", {"start_date": start, "end_date": slate_date,
                                      "team_ids": list(team_ids), "seasons": [season]}, cap=400)
        finals = [g for g in games if str(g.get("status", "")).lower() in ("final", "closed")
                  or g.get("home_team_score")]
        gids = [g["id"] for g in finals]
        stats = c.paginate("/player_stats", {"game_ids": gids}, cap=6000) if gids else []
        by_name = defaultdict(list)
        for s in stats:
            pl = s.get("player") or {}
            nm = normalize_name(f"{pl.get('first_name','')} {pl.get('last_name','')}")
            if not nm:
                continue
            by_name[nm].append(s)
        stocks = {}
        for nm, rows in by_name.items():
            rows = sorted(rows, key=lambda s: (s.get("game") or {}).get("date", ""), reverse=True)[:5]
            mins = [_to_min(s.get("min")) for s in rows]
            out["minutes"][nm] = sum(mins) / len(mins) if mins else 0.0
            played = [s for s in rows if _to_min(s.get("min")) > 0]
            if played:
                stocks[nm] = sum((s.get("blk") or 0) + (s.get("stl") or 0) for s in played) / len(played)
        # rank stocks (1 = best); top players exempt from the PRA haircut
        for i, (nm, _) in enumerate(sorted(stocks.items(), key=lambda kv: -kv[1]), 1):
            out["stocks_rank"][nm] = i
    except Exception as e:  # noqa: BLE001
        out["notes"].append(f"minutes skipped: {type(e).__name__}")


def _market_projections(c, game_ids, out):
    """Per player: a DK-points market projection (from pts/reb/ast/threes lines)
    and whether the PRA line is juiced to the under."""
    market_map = {"points": "pts", "player_points": "pts", "pts": "pts",
                  "rebounds": "reb", "player_rebounds": "reb", "reb": "reb",
                  "assists": "ast", "player_assists": "ast", "ast": "ast",
                  "threes": "fg3m", "three_pointers_made": "fg3m", "player_threes": "fg3m",
                  "points_rebounds_assists": "pra", "pra": "pra"}
    try:
        lines = defaultdict(lambda: defaultdict(list))   # name -> stat -> [line]
        pra_juice = defaultdict(list)                     # name -> [under_prob - over_prob]
        for gid in game_ids:
            rows = c.get("/odds/player_props", {"game_id": gid}).get("data", [])
            for r in rows:
                market = str(_first(r, "market", "prop_type", "stat_type", "name", "market_key") or "").lower().strip()
                stat = market_map.get(market)
                if not stat:
                    continue
                nm = normalize_name(_first(r, "player_name", "player_full_name") or "")
                if not nm:
                    continue
                line = _num(_first(r, "line_value", "line", "threshold", "value", "over_under", "point"))
                if line is not None:
                    lines[nm][stat].append(line)
                if stat == "pra":
                    po = _implied_prob(_first(r, "over_odds", "price_over", "odds_over", "over"))
                    pu = _implied_prob(_first(r, "under_odds", "price_under", "odds_under", "under"))
                    if po is not None and pu is not None:
                        pra_juice[nm].append(pu - po)
        for nm, stats in lines.items():
            med = {s: _median(v) for s, v in stats.items()}
            proj = None
            if "pts" in med or "reb" in med or "ast" in med:
                proj = (med.get("pts", 0) + 1.25 * med.get("reb", 0) + 1.5 * med.get("ast", 0)
                        + 0.5 * med.get("fg3m", 0))
            elif "pra" in med:
                proj = med["pra"] * 1.05  # PRA carries most DK scoring
            entry = {"proj": round(proj, 1) if proj else None}
            if nm in pra_juice:
                entry["pra_under"] = _median(pra_juice[nm]) > 0.03   # under favored
            out["market"][nm] = entry
    except Exception as e:  # noqa: BLE001
        out["notes"].append(f"props skipped: {type(e).__name__}")
