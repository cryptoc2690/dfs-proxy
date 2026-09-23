"""Does coring help, and what does it cost in spread?

One WNBA slate: 2026-09-17, CON@ATL LAS@DAL LVA@SEA PHX@PDX WAS@CHI. The build
log says the cores that night were A'ja Wilson / Jordan Harrison / Serah
Williams. Contest 195703605 finished, so its standings carry every player's real
DK score AND the real 1,902-entry field to rank against.

Two questions, which need different amounts of evidence:

  A. did coring score better?      one slate. An anecdote with seed error bars,
                                   NOT a result. Reported as such.
  B. does coring compress the      a property of the BUILD, not of the outcome.
     spread of your own lineups?   Measurable on any slate, any number of seeds.

Traps handled: the standings player block lists each player once PER ROSTER SLOT
with identical FPTS (dedupe, never sum), and names need the same normalisation
the tool uses or the join silently returns zeros.
"""
import sys, csv, io, json, statistics as st

# Resolve the package from this file, not from an absolute path: these
# lived in a session scratchpad that no longer exists.
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import app
from dk import normalize_name

# The slate files this reads were uploaded to a chat session and are not
# in the repo. Point WNBA_SLATE_DIR at a directory holding them to re-run;
# the script is kept for its METHOD, which is the part worth having.
import os
U = os.environ.get("WNBA_SLATE_DIR", "/root/.claude/uploads/c746c511-d086-55ac-83f0-a7baebe445cb/")
if not U.endswith("/"):
    U += "/"
if not os.path.isdir(U):
    raise SystemExit(f"slate files not found in {U!r}; set WNBA_SLATE_DIR to a directory holding them")
LS = open(U + "9d14f36f-WNBA_Linestar_Projetions_9-17.csv", encoding="utf-8-sig").read()
DAILY = open(U + "d3b5f3de-WNBA_Daily_Projections_9-17.csv", encoding="utf-8-sig").read()
STAND = open(U + "aa576726-contest-standings-195703605-2.csv", encoding="utf-8-sig").read()
CORES = ["A'ja Wilson", "Jordan Harrison", "Serah Williams"]
SEEDS = list(range(8))
N = 15          # what the log says was actually built that night

# ---------------------------------------------------------------- actuals ---
actual, field = {}, []
for r in csv.reader(io.StringIO(STAND)):
    if len(r) > 10 and r[7].strip() and r[10].strip():
        try:
            actual[normalize_name(r[7].strip())] = float(r[10])
        except ValueError:
            pass
    if len(r) > 5 and r[0].strip().isdigit():
        try:
            field.append(float(r[4]))
        except ValueError:
            pass
field.sort(reverse=True)
assert len(actual) > 80, f"actuals join failed: {len(actual)} players"
assert len(field) > 1000, f"field join failed: {len(field)} entries"
print(f"actuals for {len(actual)} players; field of {len(field):,} entries")
print(f"field: 1st {field[0]:.1f}  top1% {field[len(field)//100]:.1f}  "
      f"top20% {field[len(field)//5]:.1f}  median {field[len(field)//2]:.1f}")


def rank_of(score):
    lo, hi = 0, len(field)
    while lo < hi:                       # how many field entries beat us
        mid = (lo + hi) // 2
        if field[mid] > score:
            lo = mid + 1
        else:
            hi = mid
    return lo + 1


def run(cores, seed):
    o = {"n": N, "seed": seed, "minutes": DAILY}
    if cores:
        o["cores"] = "\n".join(cores)
    r = app.run_optimize(LS, o)
    if r.get("error"):
        raise SystemExit(r["error"])
    out = []
    for l in r["lineups"]:
        names = [normalize_name(p["name"]) for p in l["players"]]
        miss = [n for n in names if n not in actual]
        out.append({"score": sum(actual.get(n, 0.0) for n in names),
                    "miss": len(miss), "names": names})
    return out


rows = []
for seed in SEEDS:
    for label, cores in (("core", CORES), ("nocore", None)):
        lus = run(cores, seed)
        scores = [l["score"] for l in lus]
        ranks = [rank_of(s) for s in scores]
        rows.append({
            "label": label, "seed": seed,
            "best": max(scores), "mean": st.mean(scores),
            "sd": st.pstdev(scores),
            "bestrank": min(ranks),
            "top1": sum(1 for r_ in ranks if r_ <= len(field) * 0.01),
            "top5": sum(1 for r_ in ranks if r_ <= len(field) * 0.05),
            "top20": sum(1 for r_ in ranks if r_ <= len(field) * 0.20),
            "unscored": sum(l["miss"] for l in lus),
        })
        print(f"  seed {seed} {label:7s} best {max(scores):6.1f} "
              f"mean {st.mean(scores):6.1f} sd {st.pstdev(scores):5.1f} "
              f"bestrank {min(ranks):5d} top1 {rows[-1]['top1']:2d} "
              f"top20 {rows[-1]['top20']:2d}")

print("\n" + "=" * 70)
for k in ("best", "mean", "sd", "bestrank", "top1", "top5", "top20"):
    c = [r[k] for r in rows if r["label"] == "core"]
    n = [r[k] for r in rows if r["label"] == "nocore"]
    print(f"{k:9s} core {st.mean(c):8.2f} +/- {st.pstdev(c):6.2f}   "
          f"nocore {st.mean(n):8.2f} +/- {st.pstdev(n):6.2f}   "
          f"diff {st.mean(c) - st.mean(n):+8.2f}")
print(f"\nroster slots with no actual: {sum(r['unscored'] for r in rows)} "
      f"of {len(rows) * N * 6} (a player nobody in the contest rostered has no "
      f"FPTS row, so these score 0 and drag BOTH arms equally)")

# Reproduction check: your 15 real entries that night scored a mean of 201.7,
# best 219.8. If the no-core arm lands nowhere near that, the rebuild is not the
# build you actually made and nothing downstream of it means anything.
print("your real 15 that night: mean 201.7  best 219.8  best rank 278/1902")
json.dump(rows, open("core_test_out.json", "w"), indent=1)
