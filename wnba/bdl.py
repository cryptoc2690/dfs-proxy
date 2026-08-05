"""balldontlie WNBA API client + live projection enrichment.

Endpoints confirmed against the official balldontlie MCP source
(base https://api.balldontlie.io, prefix /wnba/v1). WNBA has NO odds and
NO player-props endpoints and NO confirmed-lineups feed, so projections are
built from box scores + advanced stats + injuries + team pace, and "who
plays / how many minutes" is inferred from recent minutes and injury status
rather than read from a lineup card.

Requires BALLDONTLIE_API_KEY (GOAT tier). If the key is missing or the API
errors, BalldontlieProjector falls back cleanly to the CSV projection.
"""

from __future__ import annotations

import os
import time
import urllib.parse
import urllib.request
from collections import defaultdict

from dk import Player, fantasy_points, normalize_name

BASE = "https://api.balldontlie.io/wnba/v1"


class BDLClient:
    def __init__(self, api_key: str, *, timeout: int = 30, max_retries: int = 4):
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

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
        req = urllib.request.Request(url, headers={"Authorization": self.api_key})
        last_err = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    import json
                    return json.loads(r.read().decode())
            except Exception as e:  # noqa: BLE001 — retry on any transient error
                last_err = e
                # 429 / transient: exponential backoff.
                time.sleep(2 ** attempt)
        raise RuntimeError(f"BDL request failed: {url}: {last_err}")

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
            })
        for pid in by_player:
            by_player[pid].sort(key=lambda r: r["date"], reverse=True)

        # league + opponent defense-vs-position (G/F) from the same box scores
        dvp, league_pos_avg = self._compute_dvp(stats, game_teams, abbr_to_id)

        # team pace multipliers from season advanced stats
        pace_mult = self._pace_mults(client, season)

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

            if len(played) >= 2:
                fps = [r["fp"] for r in played]
                weights = [self.recent_weight ** i for i in range(len(fps))]
                wsum = sum(weights)
                recent_fp = sum(f * w for f, w in zip(fps, weights)) / wsum
                base = 0.65 * recent_fp + 0.35 * p.avg_points
                std = (sum((f - recent_fp) ** 2 for f in fps) / len(fps)) ** 0.5
            else:
                base = p.avg_points
                std = p.avg_points * 0.28
                p.notes.append("thin bdl sample — leaned on DK avg")

            # matchup: opponent FP allowed to this position vs league average
            opp = opp_of.get(p.team, "")
            m_mult = 1.0
            if opp and league_pos_avg.get(p.pos):
                allowed = dvp.get((opp, p.pos))
                if allowed:
                    m_mult = _clamp(allowed / league_pos_avg[p.pos], 0.85, 1.18)

            pm = _clamp(pace_mult.get(p.team, 1.0), 0.92, 1.10)
            proj = base * m_mult * pm

            p.proj = round(proj, 1)
            p.floor = round(max(0.0, proj - 1.05 * std), 1)
            p.ceil = round(proj + 1.55 * std, 1)
            if status and status.upper() not in {"OUT", "O"} and status != p.status:
                p.status = status
            if p.questionable:
                p.proj = round(p.proj * 0.9, 1)
                p.floor = round(p.floor * 0.7, 1)
                p.notes.append(f"{p.status} — risk haircut")
            if m_mult >= 1.06:
                p.notes.append(f"soft matchup vs {opp}")
            elif m_mult <= 0.9:
                p.notes.append(f"tough matchup vs {opp}")

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
