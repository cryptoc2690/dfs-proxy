"""Lottery tickets on a MAIN SLATE.

A 672-player board has far more places for a pooled player to vanish than a
30-man showdown board, so the ticket ought to matter more here. Build 150 with
the tickets off and on and check: who was at zero, one punt per ticket, that
the tickets are ordinary stacked lineups under the normal rules, and legality.
"""
import sys, collections, csv, random
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
PROJ = open(U + "df545586-DK_NFL_Main_Pre_Contest_Projections.csv", encoding="utf-8-sig").read()
FIELD = open(U + "be43c560-DK_NFL_Main_Pre_Contest_Lineups.csv", encoding="utf-8-sig").read()

# A realistic sharp sheet: the top of the board by projection at each position,
# plus a deliberate tail of cheap bodies that the builder will never reach.
rows = [r for r in csv.DictReader(open(U + "df545586-DK_NFL_Main_Pre_Contest_Projections.csv",
                                       encoding="utf-8-sig"))
        if float(r["Projected FP"]) > 0]
byp = collections.defaultdict(list)
for r in rows:
    byp[r["Position"]].append(r)
for v in byp.values():
    v.sort(key=lambda r: -float(r["Projected FP"]))
sheet = []
for pos, k in (("QB", 8), ("RB", 14), ("WR", 20), ("TE", 8), ("DST", 4)):
    sheet += [r["Player"] for r in byp[pos][:k]]
# the tail: cheapest non-zero bodies, the kind that never get drawn
tail = sorted(rows, key=lambda r: int(r["Salary"]))[:8]
sheet += [r["Player"] for r in tail]
POOL = ", ".join(dict.fromkeys(sheet))
print(f"sheet: {len(set(sheet))} names\n")


def run(on):
    r = app.run_build(PROJ, FIELD, "", {"n": 150, "format": "classic",
                                        "pool": POOL, "maxOffPool": 0,
                                        "lottery": "on" if on else "off"})
    if r.get("error"):
        raise SystemExit(r["error"])
    seen = collections.Counter()
    for lu in r["lineups"]:
        for p in lu["players"]:
            seen[p["name"]] += 1
    return r, seen


base_r, base = run(False)
new_r, new = run(True)
typed = [n.strip() for n in POOL.split(",") if n.strip()]
zb = [n for n in typed if base.get(n, 0) == 0]
za = [n for n in typed if new.get(n, 0) == 0]
print(f"typed-pool players at ZERO: before {len(zb)}  after {len(za)}")
print(f"  before: {zb}")
print(f"  after : {za}")
for note in new_r.get("notes", []):
    if "Lottery" in note["text"] or "lottery" in note["text"]:
        print(f"\nnote: {note['text']}")

pj = lambda r: sum(l["proj"] for l in r["lineups"]) / len(r["lineups"])
print(f"\nmean lineup projection: {pj(base_r):.2f} -> {pj(new_r):.2f} "
      f"({pj(new_r) - pj(base_r):+.2f})")

newly = set(zb) - set(za)
tail5 = new_r["lineups"][-5:]
per = [sum(1 for pl in lu["players"] if pl["name"] in newly) for lu in tail5]
print(f"punts per lottery lineup: {per}  "
      + ("OK" if per and max(per) <= 1 else "MORE THAN ONE"))
print("\nthe five tickets:")
for lu in tail5:
    pos = collections.Counter(pl["pos"] for pl in lu["players"])
    qb = next((pl for pl in lu["players"] if pl["pos"] == "QB"), None)
    stack = sum(1 for pl in lu["players"]
                if qb and pl["team"] == qb["team"] and pl["pos"] in ("WR", "TE"))
    punt = [pl["name"] for pl in lu["players"] if pl["name"] in newly]
    print(f"  ${lu['salary']:>6,} proj {lu['proj']:>6.1f}  QB {qb['name'] if qb else '?':<20s}"
          f" stack {stack}  punt {punt}")

bad = []
for i, lu in enumerate(new_r["lineups"]):
    ps = lu["players"]
    c = collections.Counter(p["pos"] for p in ps)
    if len(ps) != 9: bad.append((i, "size"))
    if len({p["name"] for p in ps}) != 9: bad.append((i, "dupe player"))
    if lu["salary"] > 50000: bad.append((i, f"${lu['salary']:,}"))
    if c["QB"] != 1 or c["DST"] != 1: bad.append((i, "QB/DST"))
    if c["RB"] < 2 or c["WR"] < 3 or c["TE"] < 1: bad.append((i, "positions"))
print(f"\nlegality over {len(new_r['lineups'])}: "
      + ("ALL LEGAL" if not bad else f"{len(bad)} BROKEN {bad[:4]}"))
