"""Standalone reproduction of the 2026-09-22 contest-standings corruption.

Run: python3 wnba/analysis/standings_parse.py

On that slate the tool rewrote 22 of 50 rosters from the contest-standings file
and every rewrite was wrong. Entry 5266239772 came out holding Kiki Iriafen
twice, once in a G slot she is not eligible for, at $55,500 against a $50,000
cap. The cause: DK writes a standings roster grouped by position, F F F G G
UTIL, with the revealed players first by salary inside each group — NOT the
G G F F F UTIL that its own entries export asks you to fill in. Measured
unanimously across all 5,945 rosters of contest 195934561. The parser read one
against the other by index.

The smoke suite covers this ground as part of the whole tool; this file is the
short version that shows the failure and the fix on their own, and it needs no
slate data. If it ever stops reproducing, the reconciler's contract has moved.
"""
import sys

# Resolve the package from this file, not from an absolute path: this lived in a
# session scratchpad that no longer exists.
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
from app import _standings_revealed, _reconcile_with_dk, _roster_illegal, _LS_SLOTS

S = _LS_SLOTS                                    # G G F F F UTIL, the ENTRIES order
DK = ["F", "F", "F", "G", "G", "UTIL"]           # what DK actually writes
ok = fail = 0


def chk(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


cell = lambda names: " ".join(f"{s} {n}" for s, n in zip(DK, names))

print("=== DK's real layout reads as a set of who has tipped ===")
chk("one tipped forward, everyone else hidden",
    _standings_revealed(cell(["Kiki Iriafen"] + ["LOCKED"] * 5), S),
    {"kiki iriafen"})
chk("a finished contest reveals all six",
    _standings_revealed(cell(["Kiki Iriafen", "Nneka Ogwumike", "A'ja Wilson",
                              "Caitlin Clark", "Courtney Williams",
                              "Morgan Maly"]), S),
    {"kiki iriafen", "nneka ogwumike", "aja wilson", "caitlin clark",
     "courtney williams", "morgan maly"})
chk("nothing tipped yet is an empty set, not unknown",
    _standings_revealed(cell(["LOCKED"] * 6), S), set())
chk("the entries order still reads — same slots, any order",
    _standings_revealed(
        "G LOCKED G LOCKED F Kiki Iriafen F LOCKED F LOCKED UTIL LOCKED", S),
    {"kiki iriafen"})

print("\n=== a cell that is not this slate's roster is None, never a guess ===")
for label, c in [("a showdown roster", "CPT A'ja Wilson FLEX Chelsea Gray"),
                 ("a short roster", "F Kiki Iriafen"),
                 ("nothing at all", ""),
                 ("junk", "no idea")]:
    chk(label, _standings_revealed(c, S), None)

# ---------------------------------------------------------------------------
# The entry that was wrecked, with its real salaries and eligibility.
# ---------------------------------------------------------------------------
POOL = {
    "maya caldwell":     {"guard": True,  "salary": 4300,  "locked": False,
                          "name": "Maya Caldwell"},
    "courtney williams": {"guard": True,  "salary": 8000,  "locked": False,
                          "name": "Courtney Williams"},
    "kiki iriafen":      {"guard": False, "salary": 10000, "locked": True,
                          "name": "Kiki Iriafen"},
    "nneka ogwumike":    {"guard": False, "salary": 10600, "locked": False,
                          "name": "Nneka Ogwumike"},
    "aja wilson":        {"guard": False, "salary": 12800, "locked": False,
                          "name": "A'ja Wilson"},
    "morgan maly":       {"guard": True,  "salary": 4100,  "locked": False,
                          "name": "Morgan Maly"},
    "shakira austin":    {"guard": False, "salary": 11100, "locked": True,
                          "name": "Shakira Austin"},
    "leila lacan":       {"guard": True,  "salary": 7700,  "locked": True,
                          "name": "Leila Lacan"},
    "emma cannon":       {"guard": False, "salary": 3500,  "locked": True,
                          "name": "Emma Cannon"},
}
ENTERED = ["Maya Caldwell", "Courtney Williams", "Kiki Iriafen",
           "Nneka Ogwumike", "A'ja Wilson", "Morgan Maly"]   # $49,800, legal

print("\n=== the entry DK's file describes is the one you entered ===")
chk("only Kiki has tipped, and you already have her — nothing to change",
    _reconcile_with_dk(ENTERED, {"kiki iriafen"}, S, POOL), (None, ""))
chk("an entry with nothing tipped is left alone",
    _reconcile_with_dk(ENTERED, set(), S, POOL), (None, ""))

print("\n=== what the old index-by-index read produced, now refused ===")
# Reading DK's slot 0 (a forward) against the entries file's slot 0 (a guard).
WRECKED = ["Kiki Iriafen"] + ENTERED[1:]
sal = sum(POOL[n.lower().replace("'", "")]["salary"]
          for n in ["Kiki Iriafen"] + ENTERED[1:])
print(f"  the corrupted roster: {', '.join(WRECKED)}  (${sal:,})")
chk("same player in two slots", _roster_illegal(WRECKED, set(), S, POOL),
    "it puts the same player in two slots")
chk("a forward in a G slot",
    _roster_illegal(["Shakira Austin"] + ENTERED[1:], set(), S, POOL),
    "it puts Shakira Austin, a forward, in a G slot")
chk("over the cap",
    _roster_illegal(["Leila Lacan"] + ENTERED[1:], set(), S, POOL),
    "it costs $53,200, over the $50,000 cap")
chk("dropping someone DK says is already playing for you",
    _roster_illegal(ENTERED, {"shakira austin"}, S, POOL),
    "it drops Shakira Austin, who DK says is already playing for you")

print("\n=== a hand edit DK can PROVE is still repaired ===")
# Leila (a guard, G slot) was swapped for Emma (a forward) before either tipped;
# both have tipped since and DK shows Emma, not Leila. Emma cannot stand in the
# G slot Leila held, so the repair has to re-slot around Kiki, who is pinned.
STALE = ["Maya Caldwell", "Leila Lacan", "Kiki Iriafen",
         "Nneka Ogwumike", "A'ja Wilson", "Morgan Maly"]
fixed, why = _reconcile_with_dk(STALE, {"emma cannon", "kiki iriafen"}, S, POOL)
chk("the repair succeeds", why, "")
chk("Leila is gone and Emma is in a slot a forward can hold",
    bool(fixed) and "Leila Lacan" not in fixed
    and fixed[S.index("UTIL")] == "Emma Cannon", True)
chk("the tipped player never moved slot", bool(fixed) and fixed[2] == "Kiki Iriafen", True)
chk("and the result is legal",
    _roster_illegal(fixed or [], {"emma cannon", "kiki iriafen"}, S, POOL), "")

print("\n=== what the file cannot answer, it says so ===")
# Both tipped players are already on the roster bar one, and the player they
# replaced had not tipped — so nothing in the file names the victim.
_, why2 = _reconcile_with_dk(ENTERED, {"kiki iriafen", "leila lacan"}, S, POOL)
chk("an unprovable edit is refused, not guessed at",
    "doesn't say who they replaced" in why2, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
