"""After-the-fact check for one night: the new tool against the old one.

    python3 nba/grade_night.py 11-7              # the night's last build
    python3 nba/grade_night.py 11-7 192146       # a specific build (logs/inputs/<day>/<stamp>)

Grades, on the night's real contest standings, with the winning measures only
(average lineup against the field; top 0.1% / 1% / 10% counts; best rank; the
tail; points behind first):
  - the entries actually played (the FINAL DK export), contest by contest;
  - the old tool's set, rebuilt by run_build_old from the SAME saved inputs
    (pool, caps, removals, files) — "an old-tool build", since its simulation
    draws differently each run;
  - the consistent sharks and LeVerticalDFS;
and prints the mini-MAX players: ours, the old tool's, the field's, the top 1%'s.

Reads the saved nights folder (NBA_MAIN, default ~/Downloads/NBA Results 1/Main):
  NBA Contest Standing Main <kind> <M-D>.csv
  NBA DK Entries Main All Contests FINAL <M-D>.csv  (or one FINAL file per contest)
and the build's inputs the tool saved in logs/inputs/<YYYY-MM-DD>/<HHMMSS>/.
Writes nothing to the real logs: the old build runs with temporary log paths.
"""

import bisect
import collections
import csv
import glob
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import app                                   # noqa: E402
import newbuild as NB                        # noqa: E402
from dk import normalize_name as norm        # noqa: E402

MAIN = os.environ.get("NBA_MAIN") or os.path.expanduser("~/Downloads/NBA Results 1/Main")
SHARKS = ["herm023", "Artrell59", "JfIyer", "Big_Bank_Hank_780", "ZzZ_JaZ", "LeVerticalDFS"]
SLOTS = ("PG", "SG", "SF", "PF", "C", "G", "F", "UTIL")
_LU = re.compile(r"(?:^|\s)(?:PG|SG|SF|PF|UTIL|C|G|F)\s")


def contest_kind(name):
    c = (name or "").lower()
    if "mini" in c:
        return "mini-MAX"
    if "four point" in c:
        return "Four Point Play"
    if "sharpshooter" in c:
        return "Sharpshooter"
    if "and-one" in c or "and one" in c:
        return "And-One"
    return None


def _f(v):
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except ValueError:
        return 0.0


def read_standings(path):
    """-> {n, entries: [{rank, user, pts, names}], ptab: {name: {name, own, fpts}}, pts_sorted}"""
    ents, ptab = [], {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))[1:]
    for r in rows:
        if len(r) > 10 and r[7].strip():
            k = norm(r[7])
            if k in ptab:
                ptab[k]["own"] += _f(r[9])
            else:
                ptab[k] = {"name": r[7], "own": _f(r[9]), "fpts": _f(r[10])}
        if len(r) > 5 and r[1].strip() and r[0].strip().isdigit():
            m = re.match(r"^(.*) \((\d+)/(\d+)\)$", r[2])
            names = [norm(x) for x in (p.strip() for p in _LU.split(" " + r[5].strip() + " "))
                     if x] if r[5].strip() else []
            ents.append({"rank": int(r[0]), "user": m.group(1) if m else r[2],
                         "pts": _f(r[4]), "names": names})
    return {"n": len(ents), "entries": ents, "ptab": ptab,
            "pts_sorted": sorted((e["pts"] for e in ents), reverse=True)}


def read_entries(path):
    """A DK entries export -> [{eid, contest_id, kind, names}] (filled entries only)."""
    out = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            eid = (r.get("Entry ID") or "").strip()
            cells = [r.get(s) or "" for s in SLOTS]
            if not eid.isdigit() or not all(c.strip() for c in cells):
                continue
            out.append({"eid": eid, "contest_id": (r.get("Contest ID") or "").strip(),
                        "kind": contest_kind(r.get("Contest Name")),
                        "names": [norm(re.sub(r"\(LOCKED\)|\(\d+\)", "", c)) for c in cells]})
    return out


def grade(rosters, St):
    """Winning measures for a set of rosters (lists of normalised names)."""
    fp = lambda k: St["ptab"].get(k, {}).get("fpts", 0.0)
    neg = [-x for x in St["pts_sorted"]]
    n = St["n"]
    pts = [sum(fp(k) for k in r) for r in rosters]
    ranks = sorted(1 + bisect.bisect_left(neg, -p) for p in pts)
    field_mean = sum(e["pts"] for e in St["entries"] if e["names"]) / max(
        1, sum(1 for e in St["entries"] if e["names"]))
    worst = ranks[-max(1, len(ranks) // 10):]
    return {"n": len(rosters), "vs_field": sum(pts) / len(pts) - field_mean,
            "top01": sum(1 for r in ranks if r <= max(1, n * 0.001)),
            "top1": sum(1 for r in ranks if r <= n * 0.01),
            "top10": sum(1 for r in ranks if r <= n * 0.10),
            "best": ranks[0], "tail": 100 * sorted(worst)[len(worst) // 2] / n,
            "behind": St["pts_sorted"][0] - max(pts)}


def fmt(g):
    return (f"{g['vs_field']:+6.1f} | top0.1 {g['top01']:>2} top1 {g['top1']:>2} top10 "
            f"{g['top10']:>3} of {g['n']:>3} | best {g['best']:>6,} | tail {g['tail']:.0f}% "
            f"| behind {g['behind']:.1f}")


def last_build(day):
    folder = os.path.join(HERE, "logs", "inputs", day)
    stamps = sorted(d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))) \
        if os.path.isdir(folder) else []
    return stamps[-1] if stamps else None


def old_tool_set(inp, options):
    """The old tool's set from the same saved inputs, by contest id."""
    rd = lambda n: open(os.path.join(inp, n), encoding="utf-8-sig").read() \
        if os.path.exists(os.path.join(inp, n)) else ""
    tmp = tempfile.mkdtemp(prefix="nba_grade_")
    saved = (app.LOG_PATH, NB.LOG_DIR)
    app.LOG_PATH, NB.LOG_DIR = os.path.join(tmp, "b.jsonl"), tmp
    try:
        dk = rd("dk.csv")
        picked = set(options.get("contests") or [])
        lines = dk.splitlines()
        keep = [lines[0]] + [ln for ln in lines[1:] if not picked or not ln.split(",")[0].strip().isdigit()
                             or (len(ln.split(",")) > 2 and ln.split(",")[2].strip() in picked)]
        oo = {k: v for k, v in options.items() if k in ("pool", "cores", "remove", "capPlayers",
                                                         "floorPlayers")}
        oo.update({"n": "auto", "split": "auto", "fieldCap": "auto", "allowOldSlate": True})
        fields = sorted(f for f in os.listdir(inp) if f.startswith("lineups_"))
        r = app.run_build_old(rd("proj.csv"), rd(fields[0]) if fields else "",
                              "\n".join(keep) + "\n", oo, rd("linestar.csv"))
        if r.get("error"):
            return None, r["error"]
        out = collections.defaultdict(list)
        with open(app.LOG_PATH, encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                out[str(row.get("contest_id") or "")].append([norm(p["name"]) for p in row["players"]])
        return out, None
    finally:
        app.LOG_PATH, NB.LOG_DIR = saved


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    date = argv[0]
    m, d = (int(x) for x in date.split("-"))
    year = 2026 if m >= 7 else 2027                  # the 2026-27 season
    day = f"{year}-{m:02d}-{d:02d}"
    stamp = argv[1] if len(argv) > 1 else last_build(day)
    if not stamp:
        print(f"No saved build inputs for {day} in logs/inputs/.")
        return 2
    inp = os.path.join(HERE, "logs", "inputs", day, stamp)
    typed = json.load(open(os.path.join(inp, "typed.json"), encoding="utf-8"))
    options = typed.get("options") or {}

    St = {}
    for fn in glob.glob(os.path.join(MAIN, f"NBA Contest Standing Main * {date}.csv")):
        k = contest_kind(os.path.basename(fn))
        if k:
            St[k] = read_standings(fn)
    if not St:
        print(f"No standings for {date} in {MAIN}.")
        return 2
    final = collections.defaultdict(list)
    for fn in glob.glob(os.path.join(MAIN, f"NBA DK Entries Main * FINAL {date}.csv")):
        for e in read_entries(fn):
            if e["kind"]:
                final[e["kind"]].append(e["names"])
    old_by_id, err = old_tool_set(inp, options)
    kind_of = {}
    with open(os.path.join(inp, "dk.csv"), encoding="utf-8-sig", newline="") as fh:
        for r in csv.reader(fh):
            if r and r[0].strip().isdigit() and len(r) > 2:
                kind_of[r[2].strip()] = contest_kind(r[1])
    old = {kind_of.get(cid): v for cid, v in (old_by_id or {}).items()}

    print(f"== {date} (build {stamp}, pool {len([x for x in (options.get('pool') or '').splitlines() if x.strip()])} names) ==")
    if err:
        print(f"  old tool rebuild failed: {err}")
    for kind in ("mini-MAX", "Four Point Play", "And-One", "Sharpshooter"):
        if kind not in St:
            continue
        S = St[kind]
        print(f"\n{kind} — {S['n']:,} entries, 1st {S['pts_sorted'][0]:.2f}, top-1% line "
              f"{S['pts_sorted'][max(0, int(S['n'] * 0.01) - 1)]:.2f}")
        if final.get(kind):
            print("  played (FINAL)     ", fmt(grade(final[kind], S)))
        if old.get(kind):
            print("  old tool, rebuilt  ", fmt(grade(old[kind], S)))
        for u in SHARKS:
            ros = [e["names"] for e in S["entries"] if e["user"] == u and e["names"]]
            if len(ros) >= 10:
                print(f"  {u:<19}", fmt(grade(ros, S)))

    S = St.get("mini-MAX")
    if S and final.get("mini-MAX"):
        top = [e["names"] for e in S["entries"] if e["names"] and e["rank"] <= S["n"] * 0.01]
        fld = [e["names"] for e in S["entries"] if e["names"]]
        share = lambda ros, k: 100 * sum(1 for r in ros if k in r) / max(1, len(ros))
        ours = collections.Counter(k for r in final["mini-MAX"] for k in r)
        olds = collections.Counter(k for r in old.get("mini-MAX", []) for k in r)
        keys = {k for k, _ in ours.most_common(12)} | {k for k, _ in olds.most_common(8)}
        keys |= {k for k in S["ptab"] if share(top, k) >= 40}
        print("\nmini-MAX players: played / old tool / field / top 1% / real points")
        for k in sorted(keys, key=lambda k: -share(top, k)):
            print(f"  {S['ptab'].get(k, {}).get('name', k)[:22]:<23} "
                  f"{share(final['mini-MAX'], k):5.0f}% {share(old.get('mini-MAX', []), k):5.0f}% "
                  f"{share(fld, k):5.0f}% {share(top, k):5.0f}% "
                  f"{S['ptab'].get(k, {}).get('fpts', 0):6.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
