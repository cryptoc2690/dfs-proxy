"""Do the lottery tickets reach the players the set missed?

ATL @ GB, 150 entries. The tool returned 0 of 900 roster spots for Austin Hooper
and the winning lineup had him. Build with the tickets off and on, compare
coverage and cost, and re-check legality — lottery() EDITS chosen rosters.
"""
import sys, collections
# Resolve the package from this file, not from an absolute scratchpad path.
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import app, engine as E

# Slate files were uploaded to a chat session and are not in the repo. Point
# NFL_SLATE_DIR at a directory holding them to re-run; the METHOD is the part
# worth keeping.
import os
U = os.environ.get("NFL_SLATE_DIR",
                   "/root/.claude/uploads/c746c511-d086-55ac-83f0-a7baebe445cb/")
if not U.endswith("/"):
    U += "/"
if not os.path.isdir(U):
    raise SystemExit(f"slate files not found in {U!r}; set NFL_SLATE_DIR")
PROJ = open(U + "33015160-DK_NFL_ATL__GB_Pre_Contest_Projections.csv",
            encoding="utf-8-sig").read()
FIELD = open(U + "b84979e6-DK_NFL_ATL__GB_Pre_Contest_Lineups.csv",
             encoding="utf-8-sig").read()

# The sheet he typed: the green + white names from the pool image.
POOL = """Bijan Robinson, Jordan Love, Christian Watson, Michael Penix Jr.,
Drake London, Matthew Golden, MarShawn Lloyd, Tucker Kraft, Kyle Pitts Sr.,
Chris Brooks, Trey Smack, Packers, Nick Folk, Brian Robinson Jr.,
Kaleb Johnson, Falcons, Jahan Dotson, Jonnu Smith, Skyy Moore,
Olamide Zaccheaus, Austin Hooper, Bo Melton, J. Michael Sturdivant,
Charlie Woerner, Mark Redman"""


def run(on):
    r = app.run_build(PROJ, FIELD, "", {"n": 150, "format": "showdown",
                                        "pool": POOL, "maxOffPool": 0,
                                        "lottery": "on" if on else "off"})
    if r.get("error"):
        raise SystemExit(r["error"])
    seen = collections.Counter()
    for lu in r["lineups"]:
        for p in lu["players"]:          # any slot counts as covered now
            seen[p["name"]] += 1
    return r, seen


base_r, base = run(False)
new_r, new = run(True)

names = sorted(set(base) | set(new))
typed = [n.strip() for n in POOL.replace("\n", " ").split(",") if n.strip()]
print(f"{'player':24s} {'before':>7} {'after':>6}")
moved = 0
for n in typed:
    b, a = base.get(n, 0), new.get(n, 0)
    if b != a:
        moved += 1
        flag = "  <-- was ZERO" if b == 0 else ""
        print(f"{n:24s} {b:>7} {a:>6}{flag}")
print(f"\n{moved} players changed exposure")

zb = [n for n in typed if base.get(n, 0) == 0]
za = [n for n in typed if new.get(n, 0) == 0]
print(f"typed-pool players at ZERO roster spots: before {len(zb)} {zb}")
print(f"                                        after {len(za)} {za}")

pj = lambda r: sum(l["proj"] for l in r["lineups"]) / len(r["lineups"])
print(f"\nmean lineup projection: {pj(base_r):.2f} -> {pj(new_r):.2f} "
      f"({pj(new_r) - pj(base_r):+.2f})")
print(f"lineups: {len(base_r['lineups'])} -> {len(new_r['lineups'])}")
for note in new_r.get("notes", []):
    if "lottery" in note["text"]:
        print(f"note: {note['text']}")

# ---------------------------------------------------------------------------
# Legality. _cover_repair EDITS a chosen roster in place, so every rule DK
# enforces has to be re-checked on the output, not assumed from construction.
# ---------------------------------------------------------------------------
bad = []
for i, lu in enumerate(new_r["lineups"]):
    ps = lu["players"]
    ids = [p.get("id") or p["name"] for p in ps]
    if len(ps) != 6:
        bad.append((i, f"{len(ps)} players"))
    if len(set(p["name"] for p in ps)) != len(ps):
        bad.append((i, "duplicate player"))
    if lu["salary"] > 50000:
        bad.append((i, f"${lu['salary']:,} over cap"))
    if len({p["team"] for p in ps if p.get("team")}) < 2:
        bad.append((i, "one team only"))
print(f"\nlegality over {len(new_r['lineups'])} rosters: "
      + ("ALL LEGAL" if not bad else f"{len(bad)} BROKEN {bad[:5]}"))
mx = max(l["salary"] for l in new_r["lineups"])
print(f"max salary {mx:,}")
