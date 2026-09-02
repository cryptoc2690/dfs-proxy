"""DraftKings WNBA Classic — the ruleset and the Player record.

This module knows nothing about projections or solving. It encodes the DK
WNBA Classic ruleset in one place, so the rest of the codebase never
hard-codes a cap number or a roster slot, and it defines the Player object
that flows through the whole pipeline.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# --- DraftKings WNBA Classic ruleset ------------------------------------
# Roster: 6 players filling G, G, F, F, F, UTIL (DK's own slot order in the
# entries export). DK only splits the WNBA into Guards and Forwards (no
# centers), so every player is G- or F-eligible; the UTIL slot takes either.
SALARY_CAP = 50_000
ROSTER_SIZE = 6
MIN_GUARDS = 2    # two dedicated G slots must be filled by guards
MIN_FORWARDS = 3  # three dedicated F slots must be filled by forwards
# => the 6th (UTIL) is any -> guards in [2, 3], forwards in [3, 4].


@dataclass
class Player:
    name: str
    dk_id: str
    salary: int
    team: str
    opponent: str
    game: str            # e.g. "SEA@NYL"
    is_guard: bool       # True => fills G/UTIL, False => fills F/UTIL
    avg_points: float    # LineStar "PPG" — the player's season average
    status: str          # "" or "OUT"

    proj: float = 0.0
    floor: float = 0.0
    ceil: float = 0.0
    ownership: float = 0.0   # projected ownership %, 0..100
    core: bool = False       # flagged as a core play in a game-theory pool
    in_pool: bool = False    # member of the sharp's pool (build constraint set)
    starter: bool = False    # LineStar StartingStatus == 1 (confirmed starter)
    minutes: float = 0.0     # projected minutes (from the daily-projections file)
    stuffer: float = 0.0     # DK pts from NON-scoring cats (reb/ast/stl/blk) = floor
    ls_proj: float = 0.0     # LineStar's raw projection, before the blend
    daily_dk: float = 0.0    # DK points implied by the daily file's stat line
    risk: bool = False       # gated: projected minutes below the non-rotation floor
    raw_ceil: float = 0.0    # LineStar's ceiling before the low-minute cap, so the
                             # minutes pass can give it back to rotation players
    implied: float = 0.0     # team's Vegas implied total (game environment)
    spread: float = 0.0      # team's Vegas spread (+ = underdog)
    notes: list[str] = field(default_factory=list)

    @property
    def pos(self) -> str:
        return "G" if self.is_guard else "F"

    @property
    def value(self) -> float:
        """Projected points per $1,000 of salary."""
        return self.proj / (self.salary / 1000.0) if self.salary else 0.0


def normalize_name(name: str) -> str:
    """Fold accents/punctuation/case so names match across sources.

    'Marine Johannès' -> 'marine johannes', "Flau'Jae Johnson" -> 'flaujae johnson'.
    """
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[.'\-]", "", n.lower())
    n = re.sub(r"\s+", " ", n).strip()
    # Drop common suffixes that sources disagree on.
    return re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n).strip()
