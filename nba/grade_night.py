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

Two more blocks (20-Max And Pool Add Report 11-09, build list 5):
  - where each played set lost and gained against each shark's set and the
    contest's top 1%: (our share - theirs) x his real points, player by player.
    The lines sum exactly to the gap between the two sets' mean lineups;
  - the night's 20-max rebuilt with seeds 0-5 from the same saved inputs, so the
    set played can be placed inside its own draw's spread.

Reads the saved nights folder (NBA_MAIN, default ~/Downloads/NBA Results 1/Main):
  NBA Contest Standing Main <kind> <M-D>.csv
  NBA DK Entries Main All Contests FINAL <M-D>.csv  (or one FINAL file per contest)
and the build's inputs the tool saved in logs/inputs/<YYYY-MM-DD>/<HHMMSS>/.
Writes nothing to the real logs: the old build runs with temporary log paths.
"""

import bisect
import collections
import statistics
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


def decompose(ours, theirs, St, top=5):
    """(our mean - their mean, [(name, our %, their %, points, cost)]) sorted by cost."""
    fp = lambda k: St["ptab"].get(k, {}).get("fpts", 0.0)
    a = collections.Counter(k for r in ours for k in r)
    b = collections.Counter(k for r in theirs for k in r)
    rows = []
    for k in set(a) | set(b):
        sa, sb = a[k] / len(ours), b[k] / len(theirs)
        rows.append((St["ptab"].get(k, {}).get("name", k), 100 * sa, 100 * sb, fp(k),
                     (sa - sb) * fp(k)))
    gap = sum(r[4] for r in rows)
    rows.sort(key=lambda r: r[4])
    return gap, rows[:top], [r for r in rows[::-1][:3] if r[4] > 0]


def print_decompose(label, ours, theirs, St):
    gap, cost, gain = decompose(ours, theirs, St)
    f = lambda r: f"{r[0]} {r[4]:+.1f} ({r[1]:.0f}% vs {r[2]:.0f}%, {r[3]:.0f})"
    print(f"    vs {label:<19} {gap:+6.1f} | cost: " + ", ".join(f(r) for r in cost if r[4] < 0))
    if gain:
        print(f"    {'':<22}        | gained: " + ", ".join(f(r) for r in gain))


def seed_spread(inp, options, date, seeds=range(6)):
    """The night's 20-max sets rebuilt with the new tool, one per seed, by contest
    kind. Temporary log paths; the real logs are untouched. The winners' ranges
    read only the nights before `date` ('M-D'), as the live build did: the night's
    own standings sit in the same folder once it is saved."""
    rd = lambda n: open(os.path.join(inp, n), encoding="utf-8-sig").read() \
        if os.path.exists(os.path.join(inp, n)) else ""
    fields = [rd(f) for f in sorted(os.listdir(inp)) if f.startswith("lineups_")]
    kind_of = {}
    with open(os.path.join(inp, "dk.csv"), encoding="utf-8-sig", newline="") as fh:
        for r in csv.reader(fh):
            if r and r[0].strip().isdigit() and len(r) > 2:
                kind_of[r[2].strip()] = contest_kind(r[1])
    saved = (app.LOG_PATH, NB.LOG_DIR, NB.load_history)
    m, d = (int(x) for x in date.split("-"))
    cut = NB._season_key(m, d)
    real_load = NB.load_history
    NB.load_history = lambda folder=None: [
        r for r in real_load(folder)
        if NB._season_key(*(int(x) for x in r["date"].split("-"))) < cut]
    out = collections.defaultdict(list)
    try:
        for sd in seeds:
            tmp = tempfile.mkdtemp(prefix="nba_seed_")
            app.LOG_PATH, NB.LOG_DIR = os.path.join(tmp, "b.jsonl"), tmp
            r = app.run_build(rd("proj.csv"), fields, rd("dk.csv"), dict(options, seed=sd),
                              rd("linestar.csv"))
            if r.get("error") or not os.path.exists(app.LOG_PATH):
                continue
            sets = collections.defaultdict(list)
            with open(app.LOG_PATH, encoding="utf-8") as fh:
                for line in fh:
                    row = json.loads(line)
                    sets[kind_of.get(str(row.get("contest_id") or ""))].append(
                        [norm(p["name"]) for p in row["players"]])
            for k, v in sets.items():
                if k and len(v) <= NB.SMALL_SET:
                    out[k].append((sd, v))
    finally:
        app.LOG_PATH, NB.LOG_DIR, NB.load_history = saved
    return out


VALUE_BANDS = ((0, 4.8, "value under 4.8"), (4.8, 5.5, "4.8-5.5"), (5.5, 6.5, "5.5-6.5"),
               (6.5, 99, "6.5+"))
RANK_BANDS = ((1, 1, "best value in his $500 band"), (2, 3, "2nd-3rd"), (4, 99, "4th or worse"))


def pool_tilt(inp, options, St, final):
    """Pool players at 5%+ own: ours / top 1% / sharks' median, by value group."""
    pool = {norm(x) for x in ((options.get("pool") or "") + "\n" +
                              (options.get("cores") or "")).splitlines() if x.strip()}
    if not pool:
        return
    P = {}
    with open(os.path.join(inp, "proj.csv"), encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            k = norm(r.get("Player") or r.get("Name") or "")
            sal = _f(r.get("Salary"))
            if k in pool and sal > 0:
                P[k] = {"name": r.get("Player") or k, "sal": sal, "own": _f(r.get("Ownership %")),
                        "val": _f(r.get("Projected FP")) / (sal / 1000.0)}
    for k, v in P.items():
        band = [x for x in P.values() if int(x["sal"] // 500) == int(v["sal"] // 500)]
        v["rank"] = 1 + sum(1 for x in band if x["val"] > v["val"])
    keys = [k for k, v in P.items() if v["own"] >= 5]
    if not keys:
        return
    share = lambda ros, k: 100.0 * sum(1 for r in ros if k in r) / max(1, len(ros))
    cols = []
    for kind in ("mini-MAX", "Four Point Play", "And-One", "Sharpshooter"):
        S = St.get(kind)
        if not S or not final.get(kind):
            continue
        top = [e["names"] for e in S["entries"] if e["names"] and e["rank"] <= S["n"] * 0.01]
        sh = [[e["names"] for e in S["entries"] if e["user"] == u and e["names"]] for u in SHARKS]
        sh = [x for x in sh if len(x) >= 10]
        cols.append((kind, {k: (share(final[kind], k), share(top, k),
                                statistics.median(share(x, k) for x in sh) if sh else None)
                            for k in keys}))
    if not cols:
        return
    print("\nThe pick's tilt — pool players at 5%+ own: ours / top 1% / sharks' median "
          "(ours minus the top 1%)")
    for title, groups, key in (("by value", VALUE_BANDS, "val"), ("by value rank in his band",
                                                                 RANK_BANDS, "rank")):
        print(f"  {title}:")
        for lo, hi, label in groups:
            ks = [k for k in keys if lo <= P[k][key] < hi or (key == "rank" and lo <= P[k][key] <= hi)]
            if not ks:
                continue
            cells = []
            for kind, d in cols:
                o = statistics.mean(d[k][0] for k in ks)
                t = statistics.mean(d[k][1] for k in ks)
                m = [d[k][2] for k in ks if d[k][2] is not None]
                cells.append(f"{kind[:10]} {o:4.0f}/{t:3.0f}/" + (f"{statistics.mean(m):3.0f}" if m else "  -")
                             + f" ({o - t:+.0f})")
            print(f"    {label:<28} ({len(ks):>2})  " + " | ".join(cells))
    print("  players (value, rank in band, projected own; ours / top 1% per contest):")
    for k in sorted(keys, key=lambda k: -P[k]["own"]):
        v = P[k]
        print(f"    {v['name'][:22]:<23} {v['val']:4.2f} #{v['rank']:<2} {v['own']:4.0f}%  " +
              " | ".join(f"{d[k][0]:3.0f}/{d[k][1]:3.0f}" for _kind, d in cols))


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

    # The build as it was that night: role marks read only the box scores before
    # it, and a build saved before the weak chalk rule (Nov 13) is rebuilt
    # without the rule, so seed 0 still reproduces the played set.
    st = typed.get("settings") or {}
    options = dict(options, slateDate=day, weakChalkOff=("weakChalk" not in st))
    if "filePick" not in ((st.get("sets") or {}).get("big") or {}):
        options["filePick"] = "top10"          # built before the Nov 13 file pick
    spread = seed_spread(inp, options, date)
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
        sharks = {}
        for u in SHARKS:
            ros = [e["names"] for e in S["entries"] if e["user"] == u and e["names"]]
            if len(ros) >= 10:
                sharks[u] = ros
                print(f"  {u:<19}", fmt(grade(ros, S)))
        if final.get(kind):
            print("  where the played set lost and gained (our share vs theirs, real points):")
            for u, ros in sharks.items():
                print_decompose(u, final[kind], ros, S)
            top = [e["names"] for e in S["entries"] if e["names"] and e["rank"] <= S["n"] * 0.01]
            if top:
                print_decompose("the top 1%", final[kind], top, S)
        if kind in spread:
            gs = [(sd, grade(v, S)) for sd, v in spread[kind]]
            print("  the 20-max rebuilt, seeds 0-5:  " + "  ".join(
                f"{g['vs_field']:+.1f} (best {g['best']:,})" for _sd, g in gs))
            if final.get(kind):
                pm = grade(final[kind], S)["vs_field"]
                below = sum(1 for _sd, g in gs if g["vs_field"] < pm)
                print(f"  played {pm:+.1f}: above {below} of {len(gs)} seeds")

    pool_tilt(inp, options, St, final)

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
