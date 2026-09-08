"""DraftKings NFL Showdown (Captain Mode) — the ruleset and the Player record.

Showdown is one game. Every player from both teams is draftable, kickers and
defenses included. You pick a CAPTAIN, who scores 1.5x and *costs* 1.5x, plus
five FLEX. Confirmed against a real Stokastic export in the research brief:
Maye/Brown/Holani/Henry/Stevenson/Doubs came to 76.43 raw, and 85.99 with Maye
captained (76.43 + 0.5 x 19.11); salary 33,600 + 15,000 = 48,600.

This module knows nothing about projections or solving.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

SALARY_CAP = 50_000
ROSTER_SIZE = 6          # 1 CPT + 5 FLEX
CAPTAIN_MULT = 1.5       # applies to BOTH points and salary

# Positions that catch passes, so they ride the same passing-game factor as the
# QB. This is what makes a stack a stack (see engine.simulate).
PASS_CATCHERS = {"WR", "TE", "RB"}   # RB only partially — see engine.PASS_SHARE


@dataclass
class Player:
    name: str
    dk_id: str           # DK's FLEX id
    pos: str             # QB / RB / WR / TE / K / DST
    team: str
    opponent: str
    salary: int          # FLEX salary; captain costs round(salary * 1.5)
    proj: float          # projection, FLEX basis
    ownership: float     # projected ownership across the five FLEX slots
                         # (Stokastic's column sums to 500%, not 600%)
    # DK's CAPTAIN id — a DIFFERENT number for the same player. Showdown lists
    # every player twice, once per slot, at 1.5x salary as captain. Writing the
    # flex id into the captain cell produces a file DK will not accept. Filled
    # in from the DK entries export, which is the only source that carries it.
    cpt_dk_id: str = ""
    sd: float = 0.0      # outcome standard deviation
    boom: float = 0.0    # P(score > 5x salary/1000), %
    cpt_own: float = 0.0 # projected ownership AS captain, % (sums to 100%)
    cpt_optimal: float = 0.0  # how often they captain the sim's optimal lineup, %
    core: bool = False
    in_pool: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def is_dst(self) -> bool:
        return self.pos == "DST"

    @property
    def is_qb(self) -> bool:
        return self.pos == "QB"

    def cpt_salary(self) -> int:
        return int(round(self.salary * CAPTAIN_MULT))

    def upload_id(self, as_captain: bool) -> str:
        """The id DK expects for the slot this player is filling."""
        if as_captain and self.cpt_dk_id:
            return self.cpt_dk_id
        return self.dk_id


class Lineup:
    """One showdown roster: a captain plus five flex."""

    __slots__ = ("cpt", "flex", "metrics", "source")

    def __init__(self, cpt: Player, flex: list[Player], source: str = "mine"):
        self.cpt = cpt
        self.flex = list(flex)
        self.metrics: dict[str, float] = {}
        self.source = source          # "mine" | "vendor" — the A/B arm

    @property
    def players(self) -> list[Player]:
        return [self.cpt] + self.flex

    @property
    def salary(self) -> int:
        return self.cpt.cpt_salary() + sum(p.salary for p in self.flex)

    @property
    def proj(self) -> float:
        return round(self.cpt.proj * CAPTAIN_MULT
                     + sum(p.proj for p in self.flex), 2)

    @property
    def own_sum(self) -> float:
        """Projected ownership across the six roster spots."""
        return round(self.cpt.cpt_own + sum(p.ownership for p in self.flex), 1)

    def ids(self) -> list[str]:
        return [p.dk_id for p in self.players]

    def key(self) -> tuple:
        """Identity of the roster. Captain matters — same six with a different
        captain is a genuinely different lineup, and DK treats it as one."""
        return (self.cpt.dk_id, frozenset(p.dk_id for p in self.flex))

    def team_split(self) -> tuple[int, int]:
        """(bigger side, smaller side), e.g. (5, 1) or (3, 3)."""
        counts: dict[str, int] = {}
        for p in self.players:
            counts[p.team] = counts.get(p.team, 0) + 1
        vals = sorted(counts.values(), reverse=True)
        return (vals[0], vals[1] if len(vals) > 1 else 0)

    def split_label(self) -> str:
        a, b = self.team_split()
        return f"{a}-{b}"

    def major_team(self) -> str:
        counts: dict[str, int] = {}
        for p in self.players:
            counts[p.team] = counts.get(p.team, 0) + 1
        return max(counts, key=lambda t: counts[t])


def normalize_name(name: str) -> str:
    """Fold accents/punctuation/case/suffixes so names match across sources.

    The brief flags the exact joins that break here: Kyle Pitts Sr., Michael
    Pittman Jr., 'A.J.' vs 'AJ' Brown, Travis Etienne Jr. Names are the fallback;
    DK player IDs are the real key.
    """
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[.'\-,]", "", n.lower())
    n = re.sub(r"\s+", " ", n).strip()
    return re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n).strip()
