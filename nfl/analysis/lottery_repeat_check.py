"""Fewer missed players than tickets: all five must still be spent.

Five lineups are reserved for the lottery, so handing one back to the normal
build wastes it on a roster the other 145 already cover. With three missed the
list cycles, the expensive ones repeating first, and each repeat has to come
out as a DIFFERENT roster around the same player.

Run: python3 nfl/analysis/lottery_repeat_check.py
"""
import sys, collections
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import app

import os
U = os.environ.get("NFL_SLATE_DIR",
                   "/root/.claude/uploads/c746c511-d086-55ac-83f0-a7baebe445cb/")
if not U.endswith("/"):
    U += "/"
if not os.path.isdir(U):
    raise SystemExit(f"slate files not found in {U!r}; set NFL_SLATE_DIR")
PROJ = open(U + "33015160-DK_NFL_ATL__GB_Pre_Contest_Projections.csv", encoding="utf-8-sig").read()
FIELD = open(U + "b84979e6-DK_NFL_ATL__GB_Pre_Contest_Lineups.csv", encoding="utf-8-sig").read()

# Lloyd, Blair, Redman, Muse, Whyle off the sheet -> only three go missed.
POOL = """Bijan Robinson, Jordan Love, Christian Watson, Michael Penix Jr.,
Drake London, Matthew Golden, Tucker Kraft, Kyle Pitts Sr., Chris Brooks,
Trey Smack, Packers, Nick Folk, Brian Robinson Jr., Kaleb Johnson, Falcons,
Jahan Dotson, Jonnu Smith, Skyy Moore, Olamide Zaccheaus, Austin Hooper,
Bo Melton, Charlie Woerner"""

r = app.run_build(PROJ, FIELD, "", {"n": 150, "format": "showdown",
                                    "pool": POOL, "maxOffPool": 0})
if r.get("error"):
    raise SystemExit(r["error"])
for n in r["notes"]:
    if "Lottery" in n["text"]:
        print(f"note: {n['text']}")

tail = r["lineups"][-5:]
typed = {t.strip() for t in POOL.replace("\n", " ").split(",") if t.strip()}
counts = collections.Counter()
for lu in r["lineups"]:
    for p in lu["players"]:
        counts[p["name"]] += 1
zero = sorted(t for t in typed if counts[t] == 0)
print(f"typed-pool players at zero: {zero}")
print(f"\nthe five tickets:")
keys = set()
for lu in tail:
    keys.add(frozenset(p["name"] for p in lu["players"]))
    tm = collections.Counter(p["team"] for p in lu["players"])
    shape = "-".join(str(v) for v in sorted(tm.values(), reverse=True))
    print(f"  {shape}  ${lu['salary']:>6,}  proj {lu['proj']:>5.1f}  "
          + ", ".join(p["name"] for p in lu["players"]))
print(f"\ndistinct rosters among the five tickets: {len(keys)} of 5")
bad = [i for i, lu in enumerate(r["lineups"])
       if lu["salary"] > 50000 or len({p["name"] for p in lu["players"]}) != 6
       or len({p["team"] for p in lu["players"]}) < 2]
print(f"legality over {len(r['lineups'])}: " + ("ALL LEGAL" if not bad else str(bad[:5])))
