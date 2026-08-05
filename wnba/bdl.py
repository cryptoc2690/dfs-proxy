"""balldontlie WNBA API client + live projection enrichment.

Base https://api.balldontlie.io, prefix /wnba/v1. Projections are props-first
(player-props points/reb/ast/threes), with game odds for implied totals and
box scores + advanced stats + injuries + pace as the fallback. There is no
confirmed-lineups feed, so "who plays / how many minutes" is inferred from
recent minutes and injury status.

Requires BALLDONTLIE_API_KEY (GOAT tier). If the key is missing or the API
errors, BalldontlieProjector falls back cleanly to the CSV projection.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

from dk import Player, fantasy_points, normalize_name

BASE = "https://api.balldontlie.io/wnba/v1"


class BDLError(RuntimeError):
    pass


class BDLClient:
    def __init__(self, api_key: str, *, timeout: int = 20, max_retries: int = 3):
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self.max_retries = max_retries
        # macOS python.org builds often ship without a usable cert store, which
        # makes urllib fail HTTPS verification. Try a verified context first and
        # fall back to unverified (local tool, user's own key, known host).
        try:
            self._ctx = ssl.create_default_context()
        except Exception:  # noqa: BLE001
            self._ctx = None
        self.insecure = False

    def _get(self, path: str, params: dict | None = None) -> dict:
        qs = ""
        if params:
            parts = []
            for k, v in params.items():
                if v is None:
                    continue
                if isinstance(v, (list, tuple)):
                    parts += [f"{urllib.parse.quote(str(k))}[]={urllib.parse.quote(str(i))}"
                              for i in v]
                else:
                    parts.append(f"{urllib.parse.quote(str(k))}={urllib.parse.quote(str(v))}")
            qs = "?" + "&".join(parts)
        url = f"{BASE}{path}{qs}"
        last_err = None
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(url, headers={"Authorization": self.api_key})
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode()[:180]
                except Exception:  # noqa: BLE001
                    pass
                if e.code in (401, 403):
                    raise BDLError(f"Auth failed (HTTP {e.code}). The API key was "
                                   f"rejected or your plan doesn't include WNBA. {body}")
                if e.code == 404:
                    raise BDLError(f"Endpoint not found (404): {path}")
                last_err = BDLError(f"HTTP {e.code} {e.reason} {body}".strip())
                if e.code == 429:  # rate limited — back off and retry
                    time.sleep(min(2 ** attempt, 5))
                    continue
                break
            except ssl.SSLCertVerificationError:
                # Retry unverified once (macOS missing certs).
                if not self.insecure:
                    self._ctx = ssl._create_unverified_context()
                    self.insecure = True
                    continue
                last_err = BDLError("SSL certificate verification failed even after "
                                    "fallback.")
                break
            except Exception as e:  # noqa: BLE001
                # Include the type name — some errors (timeouts) have blank str().
                last_err = BDLError(f"{type(e).__name__}: {e}".strip())
                time.sleep(min(2 ** attempt, 4))
        raise last_err or BDLError(f"request failed: {url}")

    def paginate(self, path: str, params: dict, *, cap: int = 2000) -> list[dict]:
        """Follow cursor pagination, accumulating up to `cap` rows."""
        params = dict(params)
        params.setdefault("per_page", 100)
        rows: list[dict] = []
        cursor = None
        while True:
            if cursor is not None:
                params["cursor"] = cursor
            payload = self._get(path, params)
            rows += payload.get("data", [])
            cursor = (payload.get("meta") or {}).get("next_cursor")
            if not cursor or len(rows) >= cap:
                break
        return rows


def _season_for(date_str: str, override: int | None) -> int:
    if override:
        return override
    return int(date_str[:4]) if len(date_str) >= 4 else 2026


class BalldontlieProjector:
    name = "balldontlie"

    def __init__(self, api_key: str | None = None, *, season: int | None = None,
                 recent_days: int = 24, recent_games: int = 5,
                 recent_weight: float = 0.6):
        self.api_key = api_key or os.environ.get("BALLDONTLIE_API_KEY")
        self.season = season
        self.recent_days = recent_days
        self.recent_games = recent_games
        self.recent_weight = recent_weight

    # -- main entry --------------------------------------------------------
    def project(self, players: list[Player]) -> list[Player]:
        from projections import CsvProjector
        if not self.api_key:
            for p in players:
                p.notes.append("no BALLDONTLIE_API_KEY — csv fallback")
            return CsvProjector().project(players)
        try:
            self._enrich(players)
        except Exception as e:  # noqa: BLE001
            for p in players:
                p.notes.append(f"bdl error ({e}) — csv fallback")
            return CsvProjector().project(players)
        return players

    # -- enrichment --------------------------------------------------------
    def _enrich(self, players: list[Player]) -> None:
        from datetime import datetime, timedelta

        from projections import _estimate_ownership

        client = BDLClient(self.api_key)
        slate_date = _mode_date(players)
        season = _season_for(slate_date, self.season)

        # team abbrev -> id (needed for stat queries)
        teams = client.paginate("/teams", {})
        abbr_to_id = {t["abbreviation"]: t["id"] for t in teams}
        slate_abbrs = {p.team for p in players if p.team}
        slate_team_ids = [abbr_to_id[a] for a in slate_abbrs if a in abbr_to_id]

        # recent games for the slate teams -> game ids
        end = slate_date
        start = (datetime.fromisoformat(slate_date)
                 - timedelta(days=self.recent_days)).date().isoformat()
        games = client.paginate("/games", {
            "start_date": start, "end_date": end, "team_ids": slate_team_ids,
            "seasons": [season],
        })
        final_games = [g for g in games if str(g.get("status", "")).lower()
                       in ("final", "closed") or g.get("home_team_score")]
        game_ids = [g["id"] for g in final_games]

        # box scores for those games
        stats = client.paginate("/player_stats", {"game_ids": game_ids}, cap=6000) \
            if game_ids else []

        # per-player recent DK fantasy points + minutes, most-recent first
        by_player: dict[int, list[dict]] = defaultdict(list)
        game_date = {g["id"]: g.get("date", "") for g in final_games}
        game_teams = {g["id"]: (g["home_team"]["id"], g["visitor_team"]["id"])
                      for g in final_games if g.get("home_team")}
        for s in stats:
            pid = (s.get("player") or {}).get("id")
            gid = (s.get("game") or {}).get("id") or s.get("game_id")
            if not pid or gid not in game_date:
                continue
            mins = _to_min(s.get("min"))
            by_player[pid].append({
                "date": game_date.get(gid, ""), "min": mins,
                "fp": fantasy_points(s) if mins > 0 else 0.0,
                "team_id": (s.get("team") or {}).get("id"),
                "game_id": gid,
                # raw components — used to fill stats that have no prop line
                "stl": s.get("stl") or 0, "blk": s.get("blk") or 0,
                "turnover": s.get("turnover") or 0,
                "pts": s.get("pts") or 0, "reb": s.get("reb") or 0,
                "ast": s.get("ast") or 0, "fg3m": s.get("fg3m") or 0,
            })
        for pid in by_player:
            by_player[pid].sort(key=lambda r: r["date"], reverse=True)

        # league + opponent defense-vs-position (G/F) from the same box scores
        dvp, league_pos_avg = self._compute_dvp(stats, game_teams, abbr_to_id)

        # team pace multipliers from season advanced stats
        pace_mult = self._pace_mults(client, season)

        # tonight's games -> game ids (for odds + props, which are per-game)
        slate_games = client.paginate("/games", {"dates": [slate_date]})
        slate_game_ids = [g["id"] for g in slate_games]

        # betting odds -> implied team totals -> game environment multiplier.
        implied = self._implied_totals(client, slate_game_ids, slate_games, abbr_to_id)
        env_mult = {}
        if implied:
            avg_it = sum(implied.values()) / len(implied)
            env_mult = {a: _clamp(v / avg_it, 0.90, 1.12) for a, v in implied.items()}

        # player props -> market-implied stat lines per balldontlie player id.
        # This is the strongest projection input: the market already prices
        # minutes, matchup, pace and injuries, so a props-based projection is
        # NOT re-scaled by DvP/pace (that would double-count).
        props = self._player_props(client, slate_game_ids)

        # injuries (status by normalized name)
        inj = {}
        try:
            for i in client.paginate("/player_injuries",
                                     {"team_ids": slate_team_ids}):
                pl = i.get("player") or {}
                nm = normalize_name(f"{pl.get('first_name','')} {pl.get('last_name','')}")
                inj[nm] = (i.get("status") or "").strip()
        except Exception:  # noqa: BLE001 — injuries optional
            pass

        # bdl player index by normalized name
        bdl_by_name: dict[str, int] = {}
        for s in stats:
            pl = s.get("player") or {}
            if pl.get("id"):
                bdl_by_name[normalize_name(
                    f"{pl.get('first_name','')} {pl.get('last_name','')}")] = pl["id"]

        # opponent lookup for the slate
        opp_of = {p.team: p.opponent for p in players}

        for p in players:
            nm = normalize_name(p.name)
            status = inj.get(nm, "") or p.status
            if status.upper() in {"OUT", "O"}:
                p.status = status
                p.proj = p.floor = p.ceil = 0.0
                p.notes.append("OUT — excluded")
                continue

            pid = bdl_by_name.get(nm)
            recent = by_player.get(pid, []) if pid else []
            played = [r for r in recent if r["min"] > 0][: self.recent_games]
            fps = [r["fp"] for r in played]
            recent_std = ((sum((f - (sum(fps) / len(fps))) ** 2 for f in fps)
                           / len(fps)) ** 0.5) if len(fps) >= 2 else 0.0

            props_line = props.get(pid) if pid else None
            if props_line:
                # Primary path: build DK points from market prop lines, filling
                # steals/blocks/turnovers from recent averages (rarely propped).
                proj = self._dk_from_props(props_line, played)
                std = recent_std or proj * 0.26
                base_src = "props"
                p.notes.append("market props")
            elif len(played) >= 2:
                weights = [self.recent_weight ** i for i in range(len(fps))]
                wsum = sum(weights)
                recent_fp = sum(f * w for f, w in zip(fps, weights)) / wsum
                proj = 0.65 * recent_fp + 0.35 * p.avg_points
                std = recent_std
                base_src = "form"
            else:
                proj = p.avg_points
                std = p.avg_points * 0.28
                base_src = "dkavg"
                p.notes.append("thin sample — leaned on DK avg")

            # Only scale by matchup/pace/environment when NOT using props (the
            # market already prices those in).
            opp = opp_of.get(p.team, "")
            m_mult = 1.0
            if base_src != "props":
                if opp and league_pos_avg.get(p.pos):
                    allowed = dvp.get((opp, p.pos))
                    if allowed:
                        m_mult = _clamp(allowed / league_pos_avg[p.pos], 0.85, 1.18)
                pm = _clamp(pace_mult.get(p.team, 1.0), 0.92, 1.10)
                em = env_mult.get(p.team, 1.0)
                proj = proj * m_mult * pm * em
                if m_mult >= 1.06:
                    p.notes.append(f"soft matchup vs {opp}")
                elif m_mult <= 0.9:
                    p.notes.append(f"tough matchup vs {opp}")

            p.proj = round(proj, 1)
            p.floor = round(max(0.0, proj - 1.05 * std), 1)
            p.ceil = round(proj + 1.55 * std, 1)
            if status and status.upper() not in {"OUT", "O"} and status != p.status:
                p.status = status
            if p.questionable:
                p.proj = round(p.proj * 0.9, 1)
                p.floor = round(p.floor * 0.7, 1)
                p.notes.append(f"{p.status} — risk haircut")

        _estimate_ownership(players)

    # -- helpers -----------------------------------------------------------
    def _compute_dvp(self, stats, game_teams, abbr_to_id):
        """Average DK FP allowed by each defense to Guards vs Forwards."""
        id_to_abbr = {v: k for k, v in abbr_to_id.items()}
        allowed = defaultdict(list)
        pos_all = defaultdict(list)
        for s in stats:
            mins = _to_min(s.get("min"))
            if mins < 8:
                continue
            gid = (s.get("game") or {}).get("id") or s.get("game_id")
            if gid not in game_teams:
                continue
            team_id = (s.get("team") or {}).get("id")
            home, away = game_teams[gid]
            def_id = away if team_id == home else home
            def_abbr = id_to_abbr.get(def_id)
            pos = (s.get("player") or {}).get("position") or ""
            role = "G" if "G" in pos.upper() else ("F" if pos else "")
            if not def_abbr or not role:
                continue
            fp = fantasy_points(s)
            allowed[(def_abbr, role)].append(fp)
            pos_all[role].append(fp)
        dvp = {k: sum(v) / len(v) for k, v in allowed.items() if v}
        league = {r: (sum(v) / len(v) if v else 0.0) for r, v in pos_all.items()}
        return dvp, league

    def _pace_mults(self, client, season) -> dict[str, float]:
        try:
            rows = client.paginate("/team_season_advanced_stats", {"season": season})
        except Exception:  # noqa: BLE001
            return {}
        paces = {}
        for r in rows:
            abbr = (r.get("team") or {}).get("abbreviation")
            pace = r.get("pace")
            if abbr and pace:
                paces[abbr] = pace
        if not paces:
            return {}
        avg = sum(paces.values()) / len(paces)
        return {a: (p / avg if avg else 1.0) for a, p in paces.items()}

    # ------------------------------------------------------------------
    # Odds + props parsers. balldontlie's live-odds JSON field names are not
    # 100%% pinned here (sandbox can't reach the API to confirm), so all field
    # lookups go through _first() over a list of likely keys. If a real record
    # differs, only these two maps need editing.
    # ------------------------------------------------------------------
    def _implied_totals(self, client, game_ids, slate_games, abbr_to_id) -> dict:
        """abbr -> implied team total, from game betting odds."""
        id_to_abbr = {v: k for k, v in abbr_to_id.items()}
        g_home_away = {}
        for g in slate_games:
            h = (g.get("home_team") or {}).get("abbreviation")
            a = (g.get("visitor_team") or {}).get("abbreviation")
            g_home_away[g["id"]] = (h, a)
        out = {}
        for gid in game_ids:
            try:
                rows = self._safe_get(client, "/odds", {"game_id": gid})
            except Exception:  # noqa: BLE001
                continue
            row = _prefer_vendor(rows)
            if not row:
                continue
            total = _num(_first(row, "total_value", "total", "over_under", "game_total"))
            spread_home = _num(_first(row, "spread_home_value", "spread_home",
                                      "home_spread", "spread"))
            if total is None:
                continue
            home, away = g_home_away.get(gid, (None, None))
            if spread_home is None:
                if home:
                    out[home] = total / 2
                if away:
                    out[away] = total / 2
            else:
                if home:
                    out[home] = total / 2 - spread_home / 2
                if away:
                    out[away] = total / 2 + spread_home / 2
        return out

    def _player_props(self, client, game_ids) -> dict:
        """player_id -> {stat_key: line}. stat_key in
        {pts, reb, ast, fg3m, stl, blk, pra, dd_prob}."""
        market_map = {
            "points": "pts", "player_points": "pts", "pts": "pts",
            "rebounds": "reb", "player_rebounds": "reb", "reb": "reb",
            "assists": "ast", "player_assists": "ast", "ast": "ast",
            "threes": "fg3m", "three_pointers_made": "fg3m", "3pt": "fg3m",
            "player_threes": "fg3m", "fg3m": "fg3m",
            "steals": "stl", "blocks": "blk",
            "points_rebounds_assists": "pra", "pra": "pra",
            "double_double": "dd", "double-double": "dd",
        }
        # collect all vendor lines per (player, stat), then take the median line
        collected: dict[tuple, list[float]] = {}
        dd_probs: dict[int, list[float]] = {}
        for gid in game_ids:
            try:
                rows = self._safe_get(client, "/odds/player_props", {"game_id": gid})
            except Exception:  # noqa: BLE001
                continue
            for r in rows:
                pid = _first(r, "player_id", "playerId")
                market = str(_first(r, "market", "prop_type", "stat_type",
                                    "name", "market_key") or "").lower().strip()
                stat = market_map.get(market)
                if pid is None or not stat:
                    continue
                if stat == "dd":
                    prob = _implied_prob(_first(r, "over_odds", "over", "price_over",
                                                "odds_over"))
                    if prob is not None:
                        dd_probs.setdefault(pid, []).append(prob)
                    continue
                line = _num(_first(r, "line_value", "line", "threshold", "value",
                                   "over_under", "point"))
                if line is not None:
                    collected.setdefault((pid, stat), []).append(line)
        out: dict[int, dict] = {}
        for (pid, stat), lines in collected.items():
            out.setdefault(pid, {})[stat] = _median(lines)
        for pid, probs in dd_probs.items():
            out.setdefault(pid, {})["dd_prob"] = _median(probs)
        return out

    def _dk_from_props(self, line: dict, played: list[dict]) -> float:
        """Convert market prop lines to DK fantasy points, filling stats that
        have no prop from recent per-game averages."""
        def recent_avg(key):
            vals = [r.get(key, 0) for r in played] if played else []
            return sum(vals) / len(vals) if vals else 0.0

        pts = line.get("pts", recent_avg("pts"))
        reb = line.get("reb", recent_avg("reb"))
        ast = line.get("ast", recent_avg("ast"))
        fg3 = line.get("fg3m", recent_avg("fg3m"))
        stl = line.get("stl", recent_avg("stl"))
        blk = line.get("blk", recent_avg("blk"))
        to = recent_avg("turnover")  # turnovers essentially never propped
        # If only a PRA line exists, prefer the sum of individual lines; else PRA.
        if "pra" in line and not ({"pts", "reb", "ast"} & set(line)):
            pra = line["pra"]
            pts, reb, ast = pra * 0.52, pra * 0.28, pra * 0.20  # rough split
        fp = (pts + 1.25 * reb + 1.5 * ast + 0.5 * fg3 + 2 * stl + 2 * blk
              - 0.5 * to)
        # double-double bonus: use the milestone market's implied prob if we have
        # it, else infer from how many of pts/reb/ast/stl/blk lines clear ~10.
        if "dd_prob" in line:
            fp += 1.5 * _clamp(line["dd_prob"], 0.0, 1.0)
        else:
            near = sum(1 for v in (pts, reb, ast, stl, blk) if v >= 9.5)
            if near >= 2:
                fp += 1.5 * 0.55
        return round(fp, 2)

    def _safe_get(self, client, path, params) -> list:
        payload = client._get(path, params)
        if isinstance(payload, dict):
            return payload.get("data", []) or []
        return payload or []


def _first(d: dict, *keys):
    """Return the first present, non-None value among keys."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _implied_prob(american_odds):
    """American odds -> implied probability (no vig removal)."""
    o = _num(american_odds)
    if o is None or o == 0:
        return None
    return 100 / (o + 100) if o > 0 else (-o) / (-o + 100)


def _prefer_vendor(rows: list, vendor: str = "draftkings"):
    if not rows:
        return None
    for r in rows:
        if str(r.get("vendor", "")).lower() == vendor:
            return r
    return rows[0]


def _to_min(raw) -> int:
    if raw is None:
        return 0
    s = str(raw)
    if ":" in s:
        s = s.split(":")[0]
    try:
        return int(float(s))
    except ValueError:
        return 0


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _mode_date(players: list[Player]) -> str:
    dates = [p.game_date for p in players if p.game_date]
    if not dates:
        from datetime import date
        return date.today().isoformat()
    return max(set(dates), key=dates.count)


def check_key(api_key: str) -> dict:
    """Quick liveness check for the GUI's 'Test key' button. One light call to
    /wnba/v1/teams. Returns {ok, message, insecure, games_today?}."""
    api_key = (api_key or "").strip()
    if not api_key:
        return {"ok": False, "message": "No API key entered."}
    try:
        client = BDLClient(api_key, timeout=15, max_retries=2)
        teams = client._get("/teams", {"per_page": 1})
        n = len((teams or {}).get("data", []))
        if n == 0:
            return {"ok": False, "message": "Key accepted but no WNBA teams "
                    "returned — check that your plan includes the WNBA."}
        msg = "Key works — WNBA access confirmed."
        if client.insecure:
            msg += " (Using an unverified HTTPS connection because macOS is " \
                   "missing certificates — fine for use; run the Python " \
                   "'Install Certificates.command' to silence this.)"
        return {"ok": True, "message": msg, "insecure": client.insecure}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}
