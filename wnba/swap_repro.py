"""Reproduce both late-swap failures on a controlled slate.

1. A player is benched after the build — projection craters, a cheaper starter
   at the same position now projects 2x — and the tool holds the lineup.
2. The roster is edited on DK, the CONTEST file is re-downloaded and uploaded,
   and the tool still shows the roster it built.

Everything here is synthetic so each gate can be turned on and off in isolation.
"""
import sys, csv, io, json, os, random, tempfile
# Resolve the package from this file, not from an absolute path: these
# lived in a session scratchpad that no longer exists.
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
import app

LS_COLS = ["Name", "Position", "Team", "VersusStr", "Salary", "Projected", "PPG",
           "Floor", "Ceiling", "ProjOwn", "StartingStatus", "VegasImplied", "Vegas"]


def linestar(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=LS_COLS)
    w.writeheader(); w.writerows(rows)
    return buf.getvalue()


def board(seed=1):
    rng = random.Random(seed)
    rows = []
    for gi, (a, b) in enumerate([("SEA", "NYL"), ("MIN", "PHO")]):
        for team, opp in ((a, b), (b, a)):
            for i in range(10):
                sal = [10800, 9200, 7800, 6800, 6000, 5400, 4800, 4200, 3600, 3000][i]
                proj = round(sal / 320.0 + rng.uniform(-2, 2), 1)
                rows.append({
                    "Name": f"{team} Player{i}", "Position": "PG" if i % 2 else "SF",
                    "Team": team, "VersusStr": f"@{opp}" if team == b else f"vs {opp}",
                    "Salary": sal, "Projected": proj, "PPG": round(proj * 0.95, 1),
                    "Floor": round(proj * 0.6, 1), "Ceiling": round(proj * 1.45, 1),
                    "ProjOwn": round(max(1.0, 28 - 2.4 * i - 6 * gi), 1),
                    "StartingStatus": "1" if i < 5 else "2",
                    "VegasImplied": 84 + 3 * gi, "Vegas": -2.5})
    return rows


def dk_entries(pool, rosters, tipped=()):
    """A real DK entries export: filled entry rows on the left, the player pool
    block on the right, exactly as DK hands it back once you have entered.

    `tipped` names players whose game has started. DK marks those with a
    per-player (LOCKED) in the Name + ID column, and that marker is what tells
    the reconciler a player is provably in or out of an entry."""
    ids = {nm: did for nm, did, _g, _s, _t, _gm in pool}
    tipped = set(tipped)
    hdr = ("Entry ID,Contest Name,Contest ID,Entry Fee,G,G,F,F,F,UTIL,,"
           "Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,"
           "AvgPointsPerGame")
    lines = [hdr]
    for i, (nm, did, gu, sal, team, game) in enumerate(pool):
        if i < len(rosters):
            cells = ",".join(f"{n} ({ids[n]})" for n in rosters[i])
            left = f"{1000+i},WNBA $15K,99,$5,{cells}"
        else:
            left = ",,,,,,,,,"
        mark = " (LOCKED)" if nm in tipped else ""
        lines.append(f"{left},,{'G' if gu else 'F'},{nm} ({did}){mark},{nm},{did},"
                     f"{'G' if gu else 'F'},{sal},{game} 09/18/2026 10:00PM ET,{team},12.5")
    return "\n".join(lines) + "\n"


POOL = []
for gi, (a, b) in enumerate([("SEA", "NYL"), ("MIN", "PHO")]):
    for team in (a, b):
        for i in range(10):
            POOL.append((f"{team} Player{i}", f"{20000+len(POOL)}", i % 2 == 1,
                         [10800, 9200, 7800, 6800, 6000, 5400, 4800, 4200, 3600,
                          3000][i], team, f"{a}@{b}"))

N = 3
LOG = tempfile.mkdtemp() + "/builds.jsonl"
app.LOG_PATH = LOG

rows = board()
pre = linestar(rows)
built = app.run_optimize(pre, {"n": N, "seed": 0})
if built.get("error"):
    raise SystemExit(built["error"])
lineup0 = [p["name"] for p in built["lineups"][0]["players"]]
print(f"build logged: {os.path.exists(LOG)}   entry #1000 = {', '.join(lineup0)}")

# Bench a rostered player: crater her projection and flip her starter flag. Then
# make a $100-cheaper same-position player project 2x, exactly as described.
by = {r["Name"]: r for r in rows}
victim = next(n for n in lineup0 if by[n]["Salary"] >= 6000)
vic = by[victim]
print(f"\nbenching {victim}: ${vic['Salary']:,}, {vic['Projected']} proj, "
      f"position {vic['Position']}, starter flag {vic['StartingStatus']}")
vic["Projected"] = round(vic["Projected"] * 0.25, 1)
vic["StartingStatus"] = "2"                      # now a bench player
vic["Ceiling"] = round(vic["Projected"] * 1.45, 1)

repl = next(r for r in rows if r["Team"] == vic["Team"] and r["Name"] != victim
            and r["Position"] == vic["Position"] and r["Name"] not in lineup0)
repl["Salary"] = int(vic["Salary"]) - 100
repl["StartingStatus"] = "1"
repl["Projected"] = round(vic["Projected"] * 2.0, 1)
repl["Ceiling"] = round(repl["Projected"] * 1.45, 1)
print(f"promoting {repl['Name']}: ${repl['Salary']:,}, {repl['Projected']} proj, "
      f"starter flag 1")
post = linestar(rows)

ROSTERS = [[p["name"] for p in l["players"]] for l in built["lineups"]]
upload = dk_entries(POOL, ROSTERS)

print("\n" + "=" * 70)
print("BUG 1 — the benching")
print("=" * 70)
res = app.run_late_swap(post, upload, None, {})
if res.get("error"):
    raise SystemExit(res["error"])
print(f"  hadBaseline   {res.get('hadBaseline')}")
print(f"  newsCount     {res.get('newsCount')}")
print(f"  changed       {res.get('changed')} of {res.get('entries')}")
for s in res["swaps"][:1]:
    print(f"  entry {s['entryId']}: keep={s.get('keep')}  hold={s.get('hold')!r}")
    print(f"    news seen: {s.get('news')}")
    print(f"    aggression {s.get('aggression')} -> the UI prints this as a stance")

print("\n" + "=" * 70)
print("BUG 2 — the roster was edited on DK, only the contest file was re-uploaded")
print("=" * 70)
# The user swaps the benched player out by hand on DK. The DK ENTRIES export they
# hold is the old one; the CONTEST file they re-download shows the new roster.
fixed = [repl["Name"] if n == victim else n for n in lineup0]
cont = io.StringIO()
w = csv.writer(cont)
w.writerow(["Rank", "EntryId", "EntryName", "TimeRemaining", "Points", "Lineup",
            "", "Player", "Roster Position", "%Drafted", "FPTS"])
w.writerow(["1", "1000", "me", "0", "0",
            " ".join(f"{sl} {x}" for sl, x in zip(app._LS_SLOTS, fixed)),
            "", "", "", "", ""])
for i in range(2, 60):
    w.writerow([str(i), str(9000 + i), "rival", "0", str(120 - i),
                "G LOCKED G LOCKED F LOCKED F LOCKED F LOCKED UTIL LOCKED",
                "", "", "", "", ""])
res2 = app.run_late_swap(post, upload, cont.getvalue(), {})
shown = [p["name"] for p in (res2["swaps"][0].get("before") or [])]
print(f"  roster I actually have on DK : {', '.join(fixed)}")
print(f"  roster the tool is working on: {', '.join(shown) if shown else '(n/a)'}")
print(f"  agrees with DK: {shown == fixed}")
print(f"  any warning about the disagreement: "
      f"{[w for w in res2.get('warnings', []) if 'roster' in str(w).lower()] or 'NONE'}")
