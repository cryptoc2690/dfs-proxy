"""DraftKings WNBA Classic — contest rules and CSV parsing.

This module knows nothing about projections or solving. It only turns a
DraftKings salary export into clean Player records and encodes the DK
WNBA Classic ruleset in one place, so the rest of the codebase never
hard-codes a cap number or a roster slot.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field

# --- DraftKings WNBA Classic ruleset ------------------------------------
# Roster: 6 players filling G, G, F, F, UTIL, UTIL.
# DK only splits the WNBA into Guards and Forwards (no centers), so every
# player is either G-eligible or F-eligible. A UTIL slot takes either.
SALARY_CAP = 50_000
ROSTER_SIZE = 6
MIN_GUARDS = 2   # two dedicated G slots must be filled by guards
MIN_FORWARDS = 2  # two dedicated F slots must be filled by forwards
# => guards selected in [2, 4], forwards = 6 - guards.

# DraftKings scoring (WNBA == NBA formula, including the DD/TD bonuses).
SCORING = {
    "pts": 1.0,
    "fg3m": 0.5,
    "reb": 1.25,
    "ast": 1.5,
    "stl": 2.0,
    "blk": 2.0,
    "turnover": -0.5,
}
DOUBLE_DOUBLE_BONUS = 1.5
TRIPLE_DOUBLE_BONUS = 3.0

# Status values in the DK CSV that mean "will not play" — hard-excluded.
OUT_STATUSES = {"OUT", "O"}
# Statuses that mean "playing but risky" — kept, but flagged.
QUESTIONABLE_STATUSES = {"Q", "GTD", "D", "DTD", "P"}


@dataclass
class Player:
    name: str
    dk_id: str
    salary: int
    team: str
    opponent: str
    game: str            # e.g. "SEA@NYL"
    is_guard: bool       # True => fills G/UTIL, False => fills F/UTIL
    avg_points: float    # DK "AvgPointsPerGame" — the CSV's only projection input
    status: str          # "", "OUT", "Q", ...
    starting: str        # DK "Starting" flag if present
    game_date: str = ""  # slate date, YYYY-MM-DD, parsed from Game Info

    # Filled in by the projection layer (see projections.py). Kept here so a
    # Player is the single object that flows through the whole pipeline.
    proj: float = 0.0
    floor: float = 0.0
    ceil: float = 0.0
    ownership: float = 0.0   # projected ownership %, 0..100 (GPP leverage)
    notes: list[str] = field(default_factory=list)

    @property
    def pos(self) -> str:
        return "G" if self.is_guard else "F"

    @property
    def out(self) -> bool:
        return self.status.upper() in OUT_STATUSES

    @property
    def questionable(self) -> bool:
        return self.status.upper() in QUESTIONABLE_STATUSES

    @property
    def value(self) -> float:
        """Projected points per $1,000 of salary — the core cash yardstick."""
        return self.proj / (self.salary / 1000.0) if self.salary else 0.0

    def label(self) -> str:
        return f"{self.name} ({self.dk_id})"


def _parse_game(game_info: str) -> str:
    """'SEA@NYL 08/05/2026 07:00PM ET' -> 'SEA@NYL'."""
    m = re.match(r"\s*([A-Z]{2,4}@[A-Z]{2,4})", game_info or "")
    return m.group(1) if m else (game_info or "").strip()


def _parse_game_date(game_info: str) -> str:
    """'SEA@NYL 08/05/2026 07:00PM ET' -> '2026-08-05'."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", game_info or "")
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else ""


def normalize_name(name: str) -> str:
    """Fold accents/punctuation/case so DK names match balldontlie names.

    'Marine Johannès' -> 'marine johannes', "Flau'Jae Johnson" -> 'flaujae johnson'.
    """
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[.'\-]", "", n.lower())
    n = re.sub(r"\s+", " ", n).strip()
    # Drop common suffixes that DK/BDL disagree on.
    return re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n).strip()


def fantasy_points(stat: dict) -> float:
    """DraftKings fantasy points from a balldontlie box-score row, including
    the double-double / triple-double bonuses (the piece the NBA proxy omits)."""
    pts = stat.get("pts") or 0
    reb = stat.get("reb") or 0
    ast = stat.get("ast") or 0
    stl = stat.get("stl") or 0
    blk = stat.get("blk") or 0
    to = stat.get("turnover") or 0
    fg3 = stat.get("fg3m") or 0
    fp = (pts * SCORING["pts"] + fg3 * SCORING["fg3m"] + reb * SCORING["reb"]
          + ast * SCORING["ast"] + stl * SCORING["stl"] + blk * SCORING["blk"]
          + to * SCORING["turnover"])
    doubles = sum(1 for v in (pts, reb, ast, stl, blk) if v >= 10)
    if doubles >= 3:
        fp += TRIPLE_DOUBLE_BONUS
    elif doubles >= 2:
        fp += DOUBLE_DOUBLE_BONUS
    return round(fp, 2)


def load_players(csv_path: str) -> list[Player]:
    """Parse a DraftKings WNBA salary export into Player records."""
    players: list[Player] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            roster_pos = (row.get("Roster Position") or "").upper()
            # "G/UTIL" -> guard, "F/UTIL" -> forward. Fall back to Position.
            is_guard = "G" in roster_pos.split("/")[0] if "/" in roster_pos \
                else "G" in (row.get("Position") or "")
            game = _parse_game(row.get("Game Info", ""))
            team = (row.get("TeamAbbrev") or "").strip()
            opp = ""
            if "@" in game:
                a, b = game.split("@", 1)
                opp = b if a == team else a
            try:
                salary = int(float(row.get("Salary") or 0))
            except ValueError:
                salary = 0
            try:
                avg = float(row.get("AvgPointsPerGame") or 0)
            except ValueError:
                avg = 0.0
            players.append(Player(
                name=(row.get("Name") or "").strip(),
                dk_id=(row.get("ID") or "").strip(),
                salary=salary,
                team=team,
                opponent=opp,
                game=game,
                is_guard=bool(is_guard),
                avg_points=avg,
                status=(row.get("Status") or "").strip(),
                starting=(row.get("Starting") or "").strip(),
                game_date=_parse_game_date(row.get("Game Info", "")),
            ))
    return players
