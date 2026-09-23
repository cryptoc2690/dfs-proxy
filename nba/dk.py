"""DraftKings NBA — both rulesets, the Player record, and the showdown Lineup.

CLASSIC (the main slate): eight players, $50,000, PG / SG / SF / PF / C / G / F /
UTIL, from at least two games. G takes a PG or SG, F an SF or PF, UTIL anyone.
The slot order is the one DK's entries export and upload file use; the old
Base44 tool wrote its upload in exactly this order and DK took it.

SHOWDOWN (one game): CPT + five UTIL, $50,000. The captain scores 1.5x and costs
1.5x — confirmed on a real export, NYK @ SAS 06/13/2026: Wembanyama CPT $20,100
against UTIL $13,400. Note DK heads the five flex columns UTIL on NBA, where NFL
heads them FLEX; a reader keyed on the word FLEX finds nothing here.

Scoring (DK NBA): point 1, made three +0.5, rebound 1.25, assist 1.5, steal 2,
block 2, turnover -0.5, double-double +1.5, triple-double +3. Nothing in the
build computes it — projections arrive already in DK points — but the bonuses are
why a rebounding big carries ceiling a points-only view does not see.

This module knows nothing about projections or solving.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

SALARY_CAP = 50_000
CAPTAIN_MULT = 1.5              # showdown: applies to BOTH points and salary
SD_ROSTER_SIZE = 6              # 1 CPT + 5 UTIL

CLASSIC_SLOTS = ("PG", "SG", "SF", "PF", "C", "G", "F", "UTIL")
CLASSIC_SIZE = len(CLASSIC_SLOTS)
BASE_POS = ("PG", "SG", "SF", "PF", "C")
# Which base positions each classic slot accepts.
SLOT_TAKES = {
    "PG": {"PG"}, "SG": {"SG"}, "SF": {"SF"}, "PF": {"PF"}, "C": {"C"},
    "G": {"PG", "SG"}, "F": {"SF", "PF"},
    "UTIL": set(BASE_POS),
}


def positions_of(text: str) -> set:
    """'PG/SG' -> {'PG', 'SG'}. Tolerates 'G', 'F' and 'G/F' as vendors write
    them, by widening to the base positions they stand for."""
    out = set()
    for tok in re.split(r"[/,\s]+", (text or "").upper()):
        if tok in BASE_POS:
            out.add(tok)
        elif tok == "G":
            out |= {"PG", "SG"}
        elif tok == "F":
            out |= {"SF", "PF"}
    return out


def slots_for(positions: set) -> set:
    """The classic slots a player with these base positions may fill."""
    return {s for s, takes in SLOT_TAKES.items() if positions & takes}


@dataclass
class Player:
    name: str
    dk_id: str           # DK's classic / UTIL id
    pos: str             # as written, e.g. "PG/SG"
    team: str
    opponent: str
    salary: int          # classic / UTIL salary; a captain costs 1.5x
    proj: float          # projection in DK points, UTIL basis
    ownership: float     # projected ownership per roster slot, %: sums to
                         # ~800 on classic, ~500 across the five showdown UTILs
    cpt_dk_id: str = ""  # showdown: DK lists every player twice, different ids
    sd: float = 0.0      # outcome standard deviation, DK points
    boom: float = 0.0
    cpt_own: float = 0.0     # showdown: ownership AS captain (sums to ~100)
    cpt_optimal: float = 0.0
    minutes: float = 0.0     # logged, not used to gate anything
    starting: object = None  # True / False / None when the file does not say
    start: str = ""          # ISO tip time from the DK file, "" if unknown
    ls_proj: object = None   # LineStar's projection — logged, never used
    ls_status: str = ""      # LineStar StartingStatus — logged, never used
    # The classic slots this player may fill. Set from DK's own Roster Position
    # column when the entries export is loaded — that is the authority on
    # eligibility — and from the projections' position otherwise.
    eligible: set = field(default_factory=set)
    core: bool = False
    in_pool: bool = False
    removed: bool = False
    notes: list = field(default_factory=list)

    def __post_init__(self):
        if not self.eligible:
            self.eligible = slots_for(positions_of(self.pos))

    @property
    def game(self) -> str:
        """Both teams, sorted, so the two sides of one game share a key."""
        a, b = self.team or "", self.opponent or ""
        return "@".join(sorted([a, b])) if a and b else (a or b)

    def cpt_salary(self) -> int:
        return int(round(self.salary * CAPTAIN_MULT))

    def upload_id(self, as_captain: bool) -> str:
        if as_captain and self.cpt_dk_id:
            return self.cpt_dk_id
        return self.dk_id


class ShowdownLineup:
    """One showdown roster: a captain plus five UTIL."""

    __slots__ = ("cpt", "flex", "metrics", "source")

    def __init__(self, cpt: Player, flex: list, source: str = "mine"):
        self.cpt = cpt
        self.flex = list(flex)
        self.metrics: dict = {}
        self.source = source          # "mine" | "vendor" — the A/B arm

    @property
    def players(self) -> list:
        return [self.cpt] + self.flex

    def weighted(self):
        """(player, multiplier) pairs — what the simulator scores."""
        return [(self.cpt, CAPTAIN_MULT)] + [(p, 1.0) for p in self.flex]

    @property
    def salary(self) -> int:
        return self.cpt.cpt_salary() + sum(p.salary for p in self.flex)

    @property
    def proj(self) -> float:
        return round(self.cpt.proj * CAPTAIN_MULT + sum(p.proj for p in self.flex), 2)

    @property
    def own_sum(self) -> float:
        return round(self.cpt.cpt_own + sum(p.ownership for p in self.flex), 1)

    def ids(self) -> list:
        return [p.dk_id for p in self.players]

    def key(self) -> tuple:
        """Captain matters: the same six with another captain is another lineup."""
        return (self.cpt.dk_id, frozenset(p.dk_id for p in self.flex))

    def overlap_ids(self) -> list:
        """Captain slot marked, so two captain variants of one six overlap at
        four rather than six. NFL learned this the hard way: comparing plain ids
        deleted every captain variant after the first, and a second captain
        variant was what won DEN @ KC."""
        return [self.cpt.dk_id + "#cpt"] + [p.dk_id for p in self.flex]

    def team_split(self) -> tuple:
        counts: dict = {}
        for p in self.players:
            counts[p.team] = counts.get(p.team, 0) + 1
        vals = sorted(counts.values(), reverse=True)
        return (vals[0], vals[1] if len(vals) > 1 else 0)

    def split_label(self) -> str:
        a, b = self.team_split()
        return f"{a}-{b}"

    def shape_label(self) -> str:
        return self.split_label()

    def major_side(self):
        """Which team this lineup is a bet ON, or None for an even split."""
        counts: dict = {}
        for p in self.players:
            counts[p.team] = counts.get(p.team, 0) + 1
        best = sorted(counts.items(), key=lambda kv: -kv[1])
        if len(best) < 2 or best[0][1] > best[1][1]:
            return best[0][0] if best else None
        return None


def normalize_name(name: str) -> str:
    """Fold accents, punctuation, case and suffixes so names join across files.

    NBA's traps are the accented names (Jokić, Dončić, Valančiūnas — Stokastic
    and DK do not always agree on the diacritics), the initials (P.J. / PJ
    Washington, C.J. McCollum) and the suffixes (Jaren Jackson Jr., Gary Trent
    Jr., Robert Williams III). DK player IDs are the real key; names are the
    fallback that has to survive all three.
    """
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[.'’\-,]", "", n.lower())
    n = re.sub(r"\s+", " ", n).strip()
    return re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n).strip()
