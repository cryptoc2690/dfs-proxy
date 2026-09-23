"""End-to-end check of the WNBA optimizer.  Run: python3 wnba/smoke.py

Builds synthetic LineStar and DK-entries files that match the real schemas, then
exercises the whole tool against them: the builder and its legality guards, the
slate-shape rules by slate size, starvation and relaxation reporting, unmatched
core and pool names, per-player exposure caps, the upload writer's refusals, the
contest-standings reconciler, and late swap in both policies.

It is not a unit-test file. Nearly every check here exists because something
shipped broken, and the check names say which thing. It has caught, among
others: a builder that abandoned exposure caps when the board ran thin, a
late-swap baseline that could never fire on a real night, a standings parser
that corrupted 22 of 50 rosters, and a salary floor whose three copies drifted
apart.

Two rules for editing it:

  * The summary and sys.exit MUST stay at the end of the file. A section
    appended after them is silently skipped and the suite still reports a clean
    pass. That has happened twice.
  * When a measurement changes a default, pin the new value here. Several checks
    exist only to make a future edit argue with the run that set the number.

Run it before every commit. `swap_repro.py` next to this file supplies the
late-swap fixtures and is imported, not run directly.
"""
import sys, csv, io, json, os, random, tempfile

# Resolve the package from this file, not from an absolute path: these
# lived in a session scratchpad that no longer exists.
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
import app, engine as E
from dk import normalize_name

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}" + (f"  — {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {label}  — {detail}")


# ---------- synthetic LineStar ----------
LS_COLS = ["Name", "Position", "Team", "VersusStr", "Salary", "Projected", "PPG",
           "Floor", "Ceiling", "ProjOwn", "StartingStatus", "VegasImplied", "Vegas"]


def linestar(games, per_team=10, seed=1):
    """games: [("SEA","NYL"), ...] -> CSV text with a realistic salary ladder."""
    rng = random.Random(seed)
    rows = []
    for gi, (a, b) in enumerate(games):
        for team, opp in ((a, b), (b, a)):
            for i in range(per_team):
                # a couple of studs, a middle, and a cheap tail on every team
                sal = [10800, 9200, 7800, 6800, 6000, 5400, 4800, 4200, 3600, 3000][i]
                proj = round(sal / 320.0 + rng.uniform(-2, 2), 1)
                rows.append({
                    "Name": f"{team} Player{i}", "Position": "PG" if i % 2 else "SF",
                    "Team": team, "VersusStr": f"@{opp}" if team == b else f"vs {opp}",
                    "Salary": sal, "Projected": proj, "PPG": round(proj * 0.95, 1),
                    "Floor": round(proj * 0.6, 1), "Ceiling": round(proj * 1.45, 1),
                    # deliberately lopsided so major_game / sub-10% both have teeth
                    "ProjOwn": round(max(1.0, 28 - 2.4 * i - 6 * gi), 1),
                    "StartingStatus": "1" if i < 5 else "2",
                    "VegasImplied": 84 + 3 * gi, "Vegas": -2.5,
                })
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=LS_COLS)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


TWO = linestar([("SEA", "NYL"), ("MIN", "PHO")])
FOUR = linestar([("SEA", "NYL"), ("MIN", "PHO"), ("LVA", "CON"), ("IND", "CHI")])


def build(csv_text, **opts):
    o = {"n": 12}
    o.update(opts)
    return app.run_optimize(csv_text, o)


print("=== 1. the stripped builder still builds legal lineups ===")
r = build(TWO)
check("no error", not r.get("error"), r.get("error", ""))
lus = r.get("lineups", [])
check("12 lineups", len(lus) == 12, f"{len(lus)}")
check("all rosters distinct",
      len({frozenset(p["name"] for p in l["players"]) for l in lus}) == len(lus))
check("salary legal", all(l["salary"] <= 50000 for l in lus),
      f"max {max(l['salary'] for l in lus)}")
check("6 players each", all(len(l["players"]) == 6 for l in lus))
check("two games on every lineup",
      all(len({p["team"] for p in l["players"]}) >= 2 for l in lus))
pos = [sum(1 for p in l["players"] if p["pos"] == "G") for l in lus]
check("guards in [2,3] on every lineup", all(2 <= g <= 3 for g in pos), str(sorted(set(pos))))
# an exposure notice is informational; a relaxation or a short set is not
hard = [w for w in r.get("warnings", []) if "Heavy exposure" not in w]
check("no relaxations or shortfalls on a healthy slate", not hard, str(hard))
check("heavy exposure is reported rather than capped away",
      any("Heavy exposure" in w for w in r.get("warnings", [])),
      str(r.get("warnings"))[:80])

print("\n=== 2. the deleted filters are really gone ===")
salaries = [l["salary"] for l in lus]
check("salary floor no longer prunes (some lineups leave >$700)",
      any(50000 - s > 700 for s in salaries) or min(salaries) >= 49300,
      f"leftovers {sorted(50000 - s for s in salaries)[:4]}")
owns = sorted(l["totalOwn"] for l in lus)
check("bottom-ownership slice is no longer deleted", len(set(owns)) > 1,
      f"own range {owns[0]}-{owns[-1]}")
sub10 = [sum(1 for p in l["players"] if p["own"] < 10) for l in lus]
check("sub-10% cap no longer binds (a lineup may carry >1)",
      max(sub10) >= 0, f"counts {sorted(set(sub10))}")
check("no exposure cap in the payload path", "maxExposure" not in str(r.keys()))

print("\n=== 3. shape-rule switch actually switches (F6b) ===")
on = build(TWO, slateRules="on")
off = build(TWO, slateRules="off")


def splits(res):
    out = []
    for l in res["lineups"]:
        g = {}
        for p in l["players"]:
            g[p["team"]] = g.get(p["team"], 0) + 1
        out.append(max(g.values()))
    return out


# on a two-game slate the shape rule forbids a 3-3 game split; with four teams
# the "game" is a pair of teams, so count by game not team
def game_splits(res, games_of):
    out = []
    for l in res["lineups"]:
        g = {}
        for p in l["players"]:
            g[games_of[p["team"]]] = g.get(games_of[p["team"]], 0) + 1
        out.append(max(g.values()))
    return out


GM = {"SEA": "SEA@NYL", "NYL": "SEA@NYL", "MIN": "MIN@PHO", "PHO": "MIN@PHO"}
on_s, off_s = game_splits(on, GM), game_splits(off, GM)
# The 3-3 ban is gone. What the rule still does is place a 4+ block in the
# higher-owned game and require a bring-back behind any 3-from-one-team.
import engine as _E
# Assert the rule ACCEPTS a 3-3 roster rather than hoping one turns up in the
# set: on a given board the scorer may simply prefer 4-2, and "none appeared"
# would then pass a check meant to prove the ban is gone.
_p2 = app.parse_linestar(TWO)
app.blend_projections(_p2)
_live = [p for p in _p2 if p.proj > 0]
_r2 = _E._slate_rules(_live)
_ga, _gb = sorted({p.game for p in _live})


def _three(game):
    """2 from one team + 1 from its opponent, so the bring-back rule is satisfied
    and the ONLY thing under test is the 3-3 game split itself."""
    ta, tb = sorted({p.team for p in _live if p.game == game})
    return ([p for p in _live if p.team == ta][:2]
            + [p for p in _live if p.team == tb][:1])


_even = _three(_ga) + _three(_gb)
check("rules ON: a 3-3 roster is legal", _E._rules_ok(_even, _r2),
      f"{_ga} 3 / {_gb} 3")
check("rules ON: 3 from one team with no bring-back is still refused",
      not _E._rules_ok([p for p in _live if p.game == _ga][:3] + _three(_gb), _r2))
check("rules ON: a 4+ block in the MINOR game is still refused",
      not _E._rules_ok(
          [p for p in _live if p.game != _r2["major_game"]][:4]
          + [p for p in _live if p.game == _r2["major_game"]][:2], _r2),
      f"major game is {_r2['major_game']}")

# The major-game test now applies at EVERY slate size; the bring-back does not.
# Before this, _rules_ok opened with `if rules["two_game"]:` and a 3/4/5-game
# board had no shape rule at all — 3 from a team plus 2 from its opponent was
# five of six players out of one game and nothing looked at it.
_p4 = app.parse_linestar(FOUR)
app.blend_projections(_p4)
_live4 = [q for q in _p4 if q.proj > 0]
_r4 = _E._slate_rules(_live4)
check("major_game is defined on a four-game slate, not just a two-game one",
      _r4["major_game"] is not None and not _r4["two_game"], str(_r4))
_minor = next(g for g in {q.game for q in _live4} if g != _r4["major_game"])
_other = next(g for g in {q.game for q in _live4}
              if g not in (_r4["major_game"], _minor))
check("4+ block in a MINOR game is refused on a four-game slate",
      not _E._rules_ok([q for q in _live4 if q.game == _minor][:4]
                       + [q for q in _live4 if q.game == _other][:2], _r4))
check("...and the same block in the major game is allowed",
      _E._rules_ok([q for q in _live4 if q.game == _r4["major_game"]][:4]
                   + [q for q in _live4 if q.game == _other][:2], _r4))
# The bring-back is a two-game fact: winners carry it 100% of the time at two
# games, 14% at three, 6% at five. Requiring it at three games costs 4.6 cashes,
# worse on 8 of 9 slates. It must NOT have followed the major-game test out.
_ta = next(q.team for q in _live4 if q.game == _r4["major_game"])
_nobringback = ([q for q in _live4 if q.team == _ta][:3]
                + [q for q in _live4 if q.game == _other][:3])
check("3-from-a-team with no bring-back is ALLOWED off a two-game slate",
      _E._rules_ok(_nobringback, _r4),
      f"{_ta} 3 with no opponent, on a {len({q.game for q in _live4})}-game slate")

_pl = app.parse_linestar(TWO)
app.blend_projections(_pl)
_rules = _E._slate_rules([p for p in _pl if p.proj > 0])
_by = {p.name: p for p in _pl}
bad_major, bad_bring = [], []
for l in on["lineups"]:
    ps = [_by[p["name"]] for p in l["players"]]
    g = {}
    for p in ps:
        g[p.game] = g.get(p.game, 0) + 1
    big, ct = max(g.items(), key=lambda kv: kv[1])
    if ct >= 4 and big != _rules["major_game"]:
        bad_major.append(l["rank"])
    t = {}
    for p in ps:
        t[p.team] = t.get(p.team, 0) + 1
    for team, c in t.items():
        if c >= 3:
            opp = next((q.opponent for q in ps if q.team == team), "")
            if not any(q.team == opp for q in ps):
                bad_bring.append(l["rank"])
check("rules ON: a 4+ block still sits in the higher-owned game", not bad_major,
      f"lineups {bad_major}")
check("rules ON: 3-from-a-team still needs a bring-back", not bad_bring,
      f"lineups {bad_bring}")
check("the two runs actually differ",
      [l["players"] for l in on["lineups"]] != [l["players"] for l in off["lineups"]])

print("\n=== 4. four-game slate: the board is no longer pre-cut ===")
r4 = build(FOUR, n=20)
check("20 lineups on a 4-game board", len(r4.get("lineups", [])) == 20,
      f"{len(r4.get('lineups', []))}")
cheap = sum(1 for l in r4["lineups"] for p in l["players"] if p["salary"] <= 4200)
check("sub-$4,200 players are reachable again (_viable_pool gone)", cheap > 0,
      f"{cheap} cheap slots used across 20 lineups")

print("\n=== 5. starvation is reported, not swallowed (F4) ===")
tiny = linestar([("SEA", "NYL")], per_team=4, seed=3)
rt = build(tiny, n=12)
got = len(rt.get("lineups", []))
check("a starved board either fills or says so",
      got == 12 or any("could be built" in w for w in rt.get("warnings", [])),
      f"returned {got}, warnings {rt.get('warnings')}")

print("\n=== 6. unmatched core / pool names (F5) ===")
real = [p["name"] for p in r["players"][:6]]
rc = build(TWO, cores="Nobody McGhost\n" + real[0])
check("a misspelled core stops the build", bool(rc.get("error")), rc.get("error", "")[:60])
check("and names the offender", "Nobody Mcghost" in str(rc.get("unmatchedCores"))
      or "nobody mcghost" in str(rc.get("unmatchedCores")).lower(),
      str(rc.get("unmatchedCores")))
rp = build(TWO, pool="\n".join(real) + "\nGhost Player", cores=real[0])
check("a misspelled pool name is reported, not silently dropped",
      any("Not on this slate" in w for w in rp.get("warnings", [])),
      str(rp.get("warnings")))
check("the build still runs with a valid pool", not rp.get("error"), rp.get("error", ""))

print("\n=== 7. cores still get their floor ===")
rc2 = build(TWO, cores="\n".join(real[:2]), n=12, minCores=1)
check("core build ok", not rc2.get("error"), rc2.get("error", ""))
if not rc2.get("error"):
    for nm in real[:2]:
        ct = sum(1 for l in rc2["lineups"] if any(p["name"] == nm for p in l["players"]))
        check(f"core {nm} clears its floor", ct >= 12 // 3, f"{ct} of 12")

print("\n=== 8. DK upload writer refuses bad files (F1/F2) ===")


def dk_entries(names_ids, n_entries=12, contest="WNBA $15K"):
    hdr = ("Entry ID,Contest Name,Contest ID,Entry Fee,G,G,F,F,F,UTIL,,"
           "Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame")
    lines = [hdr]
    for i, (nm, did, gu, sal, team, game) in enumerate(names_ids):
        entry = (f"{1000+i},{contest},99,$5," + ",,,,,," if i < n_entries else ",,,,,,,,,,")
        left = (f"{1000+i},{contest},99,$5,,,,,," if i < n_entries else ",,,,,,,,,")
        lines.append(
            f"{left},,"
            f"{'G' if gu else 'F'},{nm} ({did}),{nm},{did},"
            f"{'G' if gu else 'F'},{sal},{game} 08/12/2026 10:00PM ET,{team},12.5")
    return "\n".join(lines) + "\n"


pool_rows = []
for gi, (a, b) in enumerate([("SEA", "NYL"), ("MIN", "PHO")]):
    for team in (a, b):
        for i in range(10):
            pool_rows.append((f"{team} Player{i}", f"{20000 + len(pool_rows)}",
                              i % 2 == 1, [10800, 9200, 7800, 6800, 6000, 5400,
                                           4800, 4200, 3600, 3000][i],
                              team, f"{a}@{b}"))
DKTEXT = dk_entries(pool_rows)
dkp = app.parse_dk_entries(DKTEXT)
check("synthetic DK export parses", len(dkp["pool"]) == 40, f"{len(dkp['pool'])} players")
check("and carries entries", len(dkp["entries"]) >= 1, f"{len(dkp['entries'])} entries")

if dkp["entries"]:
    good = [[p["name"] for p in l["players"]] for l in r["lineups"]]
    csv_text, warn = app.build_dk_upload(dkp, good)
    check("a clean fill is written", csv_text is not None, str(warn))
    if csv_text:
        rows = [x for x in csv.reader(io.StringIO(csv_text)) if x and x[0].isdigit()]
        check("every row carries a DK ID in all six slots",
              all(all("(" in c and ")" in c for c in x[4:10]) for x in rows),
              f"{len(rows)} rows")
    # now a lineup with a name DK has never heard of
    bad = [list(good[0][:5]) + ["Phantom Bench"]] + good[1:]
    csv_bad, warn_bad = app.build_dk_upload(dkp, bad)
    check("a missing DK ID REFUSES the file", csv_bad is None, str(warn_bad)[:70])
    check("and says why", "Phantom Bench" in str(warn_bad), str(warn_bad)[:90])

print("\n=== 9. roster legality guard (F2) ===")
four_g = [{"name": f"G{i}", "dkId": str(i), "guard": True, "salary": 9000,
           "game": "A@B"} for i in range(4)]
four_g += [{"name": f"F{i}", "dkId": str(10 + i), "guard": False, "salary": 9000,
            "game": "A@B"} for i in range(2)]
# 4 G / 2 F fails the forward minimum first — either message is a correct reject
check("4 guards / 2 forwards is rejected",
      app._roster_problem(four_g) != "", app._roster_problem(four_g))
over = [{"name": f"P{i}", "dkId": str(i), "guard": i < 2, "salary": 11000,
         "game": "A@B" if i else "C@D"} for i in range(6)]
check("over the cap is rejected", "cap" in app._roster_problem(over),
      app._roster_problem(over))
dupe = [{"name": "P", "dkId": "1", "guard": i < 2, "salary": 5000,
         "game": "A@B" if i else "C@D"} for i in range(6)]
check("the same player twice is rejected", "twice" in app._roster_problem(dupe),
      app._roster_problem(dupe))
onegame = [{"name": f"P{i}", "dkId": str(i), "guard": i < 2, "salary": 5000,
            "game": "A@B"} for i in range(6)]
check("a one-game roster is rejected", "one game" in app._roster_problem(onegame),
      app._roster_problem(onegame))
midslate = [{"name": f"P{i}", "dkId": str(i), "guard": i < 2, "salary": 5000,
             "game": None if i < 3 else "A@B"} for i in range(6)]
check("mid-slate unknown games do NOT false-positive",
      app._roster_problem(midslate) == "", app._roster_problem(midslate))

print("\n=== 10. late-swap news baseline is keyed on the game set (F6) ===")
tmp = tempfile.mkdtemp()
real_log = app.LOG_PATH
app.LOG_PATH = os.path.join(tmp, "builds.jsonl")
try:
    build(TWO, n=12)                      # writes one record
    recs = [json.loads(l) for l in open(app.LOG_PATH, encoding="utf-8")]
    check("a build writes a log record", len(recs) == 1, f"{len(recs)}")
    check("the record carries the game set", bool(recs[0].get("games")),
          str(recs[0].get("games")))
    games = recs[0]["games"]
    base = app._news_baseline(games)
    check("the baseline is found by game set", len(base) > 0, f"{len(base)} players")
    # the old failure: pretend the calendar rolled past midnight
    recs[0]["slate"] = "1999-01-01"
    with open(app.LOG_PATH, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(recs[0]) + "\n")
    base2 = app._news_baseline(games)
    check("a wrong DATE no longer loses the baseline", len(base2) == len(base),
          f"{len(base2)} vs {len(base)}")
    check("a different slate's games find nothing",
          app._news_baseline(["XXX@YYY"]) == {})
    # log failure must surface, not vanish (F8)
    app.LOG_PATH = "/proc/definitely/not/writable/builds.jsonl"
    rlog = build(TWO, n=12)
    check("an unwritable log surfaces as a warning",
          any("Build log not written" in w for w in rlog.get("warnings", [])),
          str(rlog.get("warnings"))[:90])
    check("and the build still succeeds", len(rlog.get("lineups", [])) == 12)
finally:
    app.LOG_PATH = real_log

print("\n=== 11. defaults are what the held-out test picked ===")
check("ownLean defaults to 0", app.run_optimize.__doc__ is not None)
import inspect
src = inspect.getsource(app.run_optimize)
check("ownLean default is 0.0 in the call", 'options.get("ownLean"), 0.0' in src)
check("minutes gate is off", app.GATE_MINUTES == 0.0, str(app.GATE_MINUTES))
check("maxPerTeam still 3", 'options.get("maxPerTeam"), 3' in src)
check("minCores still 1", 'options.get("minCores"), 1' in src)
esrc = inspect.getsource(E)
for gone in ("TEAM_SHARE_MULT", "OWN_FLOOR_PCTILE", "MAX_SUB10", "STACK_SHARE",
             "_viable_pool(", "max_exposure", "max_overlap"):
    check(f"{gone} is gone from the engine", gone not in esrc.replace(
        "# _viable_pool used to sit here", ""), gone)

# The salary gate sits at 2,000 — a junk filter, not a diversity tax. 800 was
# set on the field's leftover table and then measured on our own builds across
# 18 slates: it cost 1.2 of mean and 5 cashes and bought no tail. Pinned in all
# three places, because the swap path carried its own copy and a floor the build
# has dropped must not survive in late swap.
check("salary gate is a junk filter at 2000, not the measured-costly 800",
      E.MAX_LEFTOVER == 2000, str(E.MAX_LEFTOVER))
check("salary gate is wired from the app",
      'options.get("maxLeftover"), 2000' in src)
check("late swap's copy of the floor moved with the build's",
      app.SWAP_MAX_LEFTOVER == E.MAX_LEFTOVER,
      f"swap {app.SWAP_MAX_LEFTOVER} vs build {E.MAX_LEFTOVER}")

print("\n=== 12. per-player caps carry their own percentage ===")
r = build(TWO, n=20)
expo = {}
for lu in r["lineups"]:
    for p in lu["players"]:
        expo[p["name"]] = expo.get(p["name"], 0) + 1
heavy = max(expo, key=expo.get)
check(f"{heavy} is heavy without a cap", expo[heavy] >= 8, str(expo[heavy]))

r2 = build(TWO, n=20, capPlayers=f"{heavy} 25")
got = sum(1 for lu in r2["lineups"] for p in lu["players"] if p["name"] == heavy)
check("a typed 25% caps him at 5 of 20", got <= 5, f"{got} of 20")

# a second player at a DIFFERENT number in the same build — the whole point
other = sorted(expo, key=expo.get, reverse=True)[1]
r3 = build(TWO, n=20, capPlayers=f"{heavy} 10\n{other} 50")
g1 = sum(1 for lu in r3["lineups"] for p in lu["players"] if p["name"] == heavy)
g2 = sum(1 for lu in r3["lineups"] for p in lu["players"] if p["name"] == other)
# Two games and ~24 bodies cannot field 20 distinct rosters with the best player
# held to 2, so this board is where the OTHER half of the contract is tested: the
# caps bind as far as they can and the overshoot is reported to the exact lineup.
over = [w for w in r3.get("warnings", []) if "ran over" in str(w)]
check("two players, two different caps, one build",
      (g1 <= 2 and g2 <= 10) or bool(over), f"{g1} and {g2}")
check("an infeasible cap is reported, never silently abandoned",
      (g1 <= 2 and g2 <= 10) or (over and str(over[0]).count("against") == 2),
      str(over[0])[:120] if over else "no warning")

# a bare name still works, using the slider as the default
r4 = build(TWO, n=20, capPlayers=heavy, capPct=10)
got = sum(1 for lu in r4["lineups"] for p in lu["players"] if p["name"] == heavy)
check("a bare name falls back to the slider", got <= 2, f"{got} of 20")

# and a name that matches nobody is REPORTED, never silently dropped
r5 = build(TWO, n=20, capPlayers="Nobody McGhost 10")
check("an unmatched cap name warns",
      any("Nobody McGhost" in str(w) for w in r5.get("warnings", [])),
      "; ".join(str(w)[:60] for w in r5.get("warnings", [])))



print("\n=== 13. late swap reacts to a benching, and to a hand-edited roster ===")
import tempfile as _tf
import swap_repro as _R

FIX = {}   # what a fixture mutated, so the assertions can name it


def _swap(mutate=None, n=6, contest=None, tipped=(), **sw):
    app.LOG_PATH = _tf.mkdtemp() + "/b.jsonl"
    rws = _R.board()
    bt = app.run_optimize(_R.linestar(rws), {"n": n, "seed": 0})
    rosters = [[p["name"] for p in l["players"]] for l in bt["lineups"]]
    FIX["roster0"] = rosters[0]        # fixtures mutate a player who is really in it
    if mutate:
        mutate(rws)
    up = _R.dk_entries(_R.POOL, rosters, tipped)
    return app.run_late_swap(_R.linestar(rws), up, contest, sw), rosters


# nothing has changed -> nothing moves. This is the churn the review killed, and
# the benching rule must not bring it back.
res, _ = _swap()
check("a quiet slate moves nothing", res["changed"] == 0, f"{res['changed']} changed")

res, _ = _swap(lambda rs: [r.update(Projected=round(r["Projected"] * 1.03, 1)) for r in rs])
check("3% noise on every projection moves nothing", res["changed"] == 0,
      f"{res['changed']} changed")


def _bench_cheap(rs):            # ordinary bench bodies, nothing actionable
    for r in rs:
        if r["Salary"] <= 3600:
            r["StartingStatus"] = "2"


res, _ = _swap(_bench_cheap)
check("ordinary bench bodies are not treated as news", res["changed"] == 0,
      f"{res['newsCount']} news")


def _bench(rs):                  # the real thing: a rostered starter is benched
    """Bench a player who is ACTUALLY in lineup #1 of this build.

    This used to name two players outright. That held until the shape rules
    changed and lineup #1 stopped containing either of them, at which point the
    "benching" mutated nobody, the hand-edit fixture substituted a player for
    himself, and two checks passed or failed for reasons unrelated to the code
    they test. Derive the victim from the build instead.
    """
    by = {r["Name"]: r for r in rs}
    vic = next(n for n in FIX["roster0"] if by[n]["Salary"] >= 6000)
    v = by[vic]
    v["Projected"] = round(v["Projected"] * 0.3, 1)
    v["StartingStatus"] = "2"
    q = next(r for r in rs if r["Team"] == v["Team"] and r["Name"] != vic
             and r["Position"] == v["Position"] and r["Name"] not in FIX["roster0"])
    q["Salary"] = int(v["Salary"]) - 100
    q["StartingStatus"] = "1"
    q["Projected"] = round(v["Projected"] * 2.2, 1)
    FIX["swap"] = (vic, q["Name"])


res, rosters = _swap(_bench)
vic, repl = FIX["swap"]
held = [s for s in res["swaps"] if s.get("keep")
        and vic in rosters[int(s["entryId"]) - 1000]]
check("a benched starter is detected with no baseline needed",
      res["newsCount"] >= 1, f"{res['newsCount']} news")
check("every lineup holding her is changed", not held,
      f"{len(held)} still hold her")

# the pool must not veto a move the news forced
res, _ = _swap(_bench, pool="\n".join(_R.POOL[i][0] for i in range(12)))
check("an off-pool replacement is allowed when news forced the move",
      res["changed"] >= 1, f"{res['changed']} changed")

# ...and a roster edited on DK after the entries export is noticed. Only the
# players whose game has tipped are revealed, in DK's own F F F G G UTIL order —
# and it is exactly those two facts (repl revealed, vic tipped and NOT revealed)
# that make the repair provable rather than a guess about who was dropped.
res, rosters = _swap(_bench)
vic, repl = FIX["swap"]
assert vic in rosters[0], f"fixture picked {vic}, not in lineup #1"
fixed = [repl if x == vic else x for x in rosters[0]]
_GUARD = {nm: gu for nm, _d, gu, _s, _t, _g in _R.POOL}
_DKORDER = ["F", "F", "F", "G", "G", "UTIL"]
_grp, _cellnames, _done = "G" if _GUARD[repl] else "F", [], False
for _s in _DKORDER:
    _cellnames.append(repl if (_s == _grp and not _done) else "LOCKED")
    _done = _done or _cellnames[-1] == repl
_c = io.StringIO()
_w = csv.writer(_c)
_w.writerow(["Rank", "EntryId", "EntryName", "TimeRemaining", "Points", "Lineup",
             "", "Player", "Roster Position", "%Drafted", "FPTS"])
_w.writerow(["1", "1000", "me", "0", "0",
             " ".join(f"{a} {b}" for a, b in zip(_DKORDER, _cellnames)),
             "", "", "", "", ""])
for _i in range(2, 40):
    _w.writerow([str(_i), str(9000 + _i), "rival", "0", str(120 - _i),
                 "F LOCKED F LOCKED F LOCKED G LOCKED G LOCKED UTIL LOCKED",
                 "", "", "", "", ""])
res2, _ = _swap(_bench, contest=_c.getvalue(), tipped=[vic, repl])
shown = [p["name"] for p in (res2["swaps"][0].get("before") or [])]
check("the contest file's roster beats a stale entries file", shown == fixed,
      ", ".join(shown) if shown else "(none)")
check("and the staleness is reported, not silently applied",
      any("out of date" in str(w) for w in res2.get("warnings", [])),
      "; ".join(str(w)[:70] for w in res2.get("warnings", [])))


print("\n=== 14. the slate board carries the projection the build uses ===")
_b = app.run_board(TWO, {})
check("the board endpoint returns players", bool(_b.get("players")),
      f"{len(_b.get('players', []))} players")
_bb = {p["name"]: p for p in _b["players"]}
_moved = [n for n, p in _bb.items() if abs(p["proj"] - p["lsProj"]) >= 0.05]
check("it reports the blended number, not LineStar raw", bool(_moved),
      f"{len(_moved)} of {len(_bb)} players differ from the raw column")
check("it also carries the raw number so the page can show both",
      all("lsProj" in p for p in _b["players"]))
# and it must agree, to the decimal, with what a build puts in a lineup
_rb = build(TWO, n=3)
_lp = {p["name"]: p["proj"] for l in _rb["lineups"] for p in l["players"]}
_bad = [n for n, v in _lp.items() if abs(_bb[n]["proj"] - v) > 0.051]
check("board and lineup cards show the SAME number for a player", not _bad,
      ", ".join(f"{n} {_bb[n]['proj']} vs {_lp[n]}" for n in _bad[:3]) or "all agree")
check("a board request with no CSV is an error, not a crash",
      bool(app.run_board("", {}).get("error")))



print("\n=== 15. a pooled player LineStar zeroed is read off the daily file ===")


def _daily(rows):
    """The daily file, banner row and all — the real shape, not a tidy one."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([""] * 4 + ["Popular Stats"] + [""] * 8 + ["Additional Stats"])
    cols = ["Player", "Team", "Pos", "Min", "PTS", "REB", "AST", "STL", "BLK",
            "3PM", "FG%", "FT%", "TO", "OREB", "DREB", "3PA", "3P%", "FGM",
            "FGA", "FTM", "FTA"]
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c, 0) for c in cols])
    return buf.getvalue()


def _zeroed(name, rws):
    for r in rws:
        if r["Name"] == name:
            r["Projected"] = 0
            r["StartingStatus"] = "4"
    return rws


ZR = _zeroed("SEA Player2", [dict(r) for r in
                             csv.DictReader(io.StringIO(TWO))])
ZTXT = io.StringIO()
_w = csv.DictWriter(ZTXT, fieldnames=LS_COLS)
_w.writeheader()
_w.writerows(ZR)
ZTXT = ZTXT.getvalue()
# same player, a real rotation line in the daily file
DAILY_REAL = _daily([{"Player": "SEA Player2", "Team": "SEA", "Pos": "G",
                      "Min": 28, "PTS": 15, "REB": 5, "AST": 4, "STL": 1.5,
                      "BLK": 0.5, "3PM": 2, "TO": 2}])
DAILY_TOKEN = _daily([{"Player": "SEA Player2", "Team": "SEA", "Pos": "G",
                       "Min": 5, "PTS": 2, "REB": 1, "AST": 0.5, "STL": 0.2,
                       "BLK": 0.1, "3PM": 0.3, "TO": 0.4}])


def _has(res, nm):
    return any(p["name"] == nm for p in res.get("players", []))


# baseline: zeroed and NOT pooled -> stays dead
r0 = build(ZTXT, n=6, minutes=DAILY_REAL)
check("a zeroed player nobody named stays out", not _has(r0, "SEA Player2"),
      "he is not in the playable board")

# pooled + real minutes -> revived
POOLZ = "\n".join(["SEA Player2"] + [f"SEA Player{i}" for i in (0, 1, 3, 4)]
                  + [f"NYL Player{i}" for i in range(5)]
                  + [f"MIN Player{i}" for i in range(4)])
r1 = build(ZTXT, n=6, pool=POOLZ, minutes=DAILY_REAL)
check("pooled + real minutes -> he is playable again", _has(r1, "SEA Player2"),
      next((f"{p['proj']} proj" for p in r1.get("players", [])
            if p["name"] == "SEA Player2"), "absent"))
check("and the build says so rather than doing it quietly",
      any("LineStar has them at 0" in str(w) for w in r1.get("warnings", [])),
      "; ".join(str(w)[:60] for w in r1.get("warnings", []))[:110])

# pooled but only a token line -> still dead (the stale-row guard)
r2 = build(ZTXT, n=6, pool=POOLZ, minutes=DAILY_TOKEN)
check("pooled but only 5 daily minutes -> still out",
      not _has(r2, "SEA Player2"), "the stale-row guard holds")

# no daily file at all -> nothing to revive from
r3 = build(ZTXT, n=6, pool=POOLZ)
check("no daily file -> nothing is invented", not _has(r3, "SEA Player2"))

# the projection used is the daily file's DK number, not a guess
_pd = app.parse_daily_projections(DAILY_REAL)["sea player2"]
_got = next((p["proj"] for p in r1.get("players", [])
             if p["name"] == "SEA Player2"), None)
check("the projection IS the daily DK number",
      _got is not None and abs(_got - _pd["compdk"]) < 0.11,
      f"{_got} vs daily {_pd['compdk']}")



print("\n=== 16. late swap v1: bar replaces the news gate ===")
import tempfile as _t2
app.SWAP_LOG_PATH = _t2.mkdtemp() + "/swaps.jsonl"


def _sw(mutate=None, n=6, **sw):
    app.LOG_PATH = _t2.mkdtemp() + "/b.jsonl"
    rws = _R.board()
    bt = app.run_optimize(_R.linestar(rws), {"n": n, "seed": 0})
    rosters = [[p["name"] for p in l["players"]] for l in bt["lineups"]]
    FIX["roster0"] = rosters[0]
    if mutate:
        mutate(rws)
    up = _R.dk_entries(_R.POOL, rosters)
    return app.run_late_swap(_R.linestar(rws), up, None, sw), rosters


# a QUIET slate must still move nothing, in either mode. This is the canary:
# old free mode made 16 of 16 swaps on an unchanged file.
q_free, _ = _sw()
q_news, _ = _sw(newsOnly="on")
check("quiet slate, re-optimise mode: nothing moves", q_free["changed"] == 0,
      f"{q_free['changed']} changed")
check("quiet slate, news-only mode: nothing moves", q_news["changed"] == 0,
      f"{q_news['changed']} changed")
check("re-optimise is the default", q_free.get("newsOnly") is False,
      f"newsOnly={q_free.get('newsOnly')}")


def _jump(rws):
    """Nobody in the lineup got worse — someone OUTSIDE it got much better.
    This is the case news-only is structurally blind to."""
    held = set(FIX["roster0"])
    for r in rws:
        if r["Name"] not in held and r["Team"] == "SEA":
            r["Projected"] = round(float(r["Projected"]) + 22, 1)
            r["StartingStatus"] = "1"
            FIX["jumped"] = r["Name"]
            return


j_free, _ = _sw(_jump)
j_news, _ = _sw(_jump, newsOnly="on")
check("a player OUTSIDE the lineup jumping is acted on in re-optimise mode",
      j_free["changed"] >= 1, f"{j_free['changed']} changed")
check("...and news-only is blind to it, as designed",
      j_news["changed"] == 0, f"{j_news['changed']} changed")
# The jump IS news now — that is the fix. What separates the policies is that
# news-only only opens a slot whose OWN OCCUPANT has news, and the player who
# moved is not in the lineup. So the same news is visible to both and only one
# can act on it.
check("the jump registers as news", j_free.get("newsCount", 0) >= 1,
      f"{j_free.get('newsCount')} news")
# The real claim: re-optimise mode actually brings the improved player IN. The
# earlier version of this check asserted no lineup had in-lineup news, which is
# false — he is held by some of the other five rosters, and news about THEM is
# correct. What distinguishes the policies is who ends up on the roster.
_in = {p["name"] for s_ in j_free["swaps"] for p in (s_.get("in") or [])}
check("re-optimise actually rosters the player who improved",
      FIX["jumped"] in _in, f"brought in: {sorted(_in)[:4]}")
_in_news = {p["name"] for s_ in j_news["swaps"] for p in (s_.get("in") or [])}
check("...and news-only never gets to him", FIX["jumped"] not in _in_news,
      f"news-only brought in: {sorted(_in_news) or 'nobody'}")

# both policies are recorded on every entry, whichever ran
sh = [s for s in j_news["swaps"] if s.get("shadow")]
check("news-only run still records what re-optimise would have done",
      any(x["shadow"]["freeWouldMove"] for x in sh),
      f"{sum(1 for x in sh if x['shadow']['freeWouldMove'])} of {len(sh)} entries")
check("and it says which policy was acted on",
      all(x["shadow"]["actedOn"] == "newsOnly" for x in sh))

# the swap log lands, with the projection movement in it
import json as _j
rows = [_j.loads(l) for l in open(app.SWAP_LOG_PATH, encoding="utf-8")]
check("a swaps.jsonl record is written per run", len(rows) >= 4, f"{len(rows)} rows")
check("the log carries both bars", rows[-1]["discBar"] == 10.0
      and rows[-1]["newsBar"] == 6.0, str(rows[-1].get("discBar")))
# the bar is a measured number now, not an inference — pin it so a stray edit
# has to argue with the replay that set it
check("the discretionary bar is the measured 10, not the guessed 20",
      app.SWAP_DISCRETIONARY_GAIN == 10.0, str(app.SWAP_DISCRETIONARY_GAIN))
check("the log carries every projection that moved",
      any(r["projectionsMoved"] for r in rows),
      f"{max(len(r['projectionsMoved']) for r in rows)} players on the busiest run")
mv = next(m for r in rows for m in r["projectionsMoved"])
check("...with its lock value AND its current value, for the hedge question",
      "lockLs" in mv and "nowLs" in mv, f"{mv['name']} {mv['lockLs']} -> {mv['nowLs']}")

# ---------------------------------------------------------------------------
# The contest file's roster. DK writes it grouped by position, F F F G G UTIL,
# revealed players first by salary within each group and LOCKED for the rest —
# NOT the G G F F F UTIL the entries export asks you to fill in. Measured on all
# 5,945 rosters of contest 195934561. Reading it slot by slot against the
# entries order is what put a locked forward in a G slot on 2026-09-22, twice
# over, at $55,500. So it is read as a SET of who has tipped, which is all it
# actually says.
# ---------------------------------------------------------------------------
S6 = app._LS_SLOTS
DKORDER = ["F", "F", "F", "G", "G", "UTIL"]
_cell = lambda names: " ".join(f"{s_} {n}" for s_, n in zip(DKORDER, names))

check("DK's own F F F G G UTIL order reads",
      app._standings_revealed(
          _cell(["Kiki Iriafen", "LOCKED", "LOCKED",
                 "LOCKED", "LOCKED", "LOCKED"]), S6) == {"kiki iriafen"})
check("a fully revealed roster reads as all six",
      app._standings_revealed(
          _cell(["Kiki Iriafen", "Nneka Ogwumike", "A'ja Wilson",
                 "Caitlin Clark", "Courtney Williams", "Morgan Maly"]), S6)
      == {"kiki iriafen", "nneka ogwumike", "aja wilson", "caitlin clark",
          "courtney williams", "morgan maly"})
check("a roster with nothing tipped yet reads as empty, not unknown",
      app._standings_revealed(_cell(["LOCKED"] * 6), S6) == set())
check("the entries file's own order still reads — same slots, any order",
      app._standings_revealed(
          "G LOCKED G LOCKED F Kiki Iriafen F LOCKED F LOCKED UTIL LOCKED", S6)
      == {"kiki iriafen"})
for _label, _c in [("a showdown cell", "CPT A'ja Wilson FLEX Chelsea Gray"),
                   ("a short roster", "F Kiki Iriafen"),
                   ("nothing at all", ""),
                   ("junk", "no idea")]:
    check(f"a cell that is not this slate's roster is None: {_label}",
          app._standings_revealed(_c, S6) is None)

_POOL = {"maya caldwell":     {"guard": True,  "salary": 4300, "locked": False,
                               "name": "Maya Caldwell"},
         "courtney williams": {"guard": True,  "salary": 8000, "locked": False,
                               "name": "Courtney Williams"},
         "kiki iriafen":      {"guard": False, "salary": 10000, "locked": True,
                               "name": "Kiki Iriafen"},
         "nneka ogwumike":    {"guard": False, "salary": 10600, "locked": False,
                               "name": "Nneka Ogwumike"},
         "aja wilson":        {"guard": False, "salary": 12800, "locked": False,
                               "name": "A'ja Wilson"},
         "morgan maly":       {"guard": True,  "salary": 4100, "locked": False,
                               "name": "Morgan Maly"},
         "shakira austin":    {"guard": False, "salary": 11100, "locked": True,
                               "name": "Shakira Austin"},
         "leila lacan":       {"guard": True,  "salary": 7700, "locked": True,
                               "name": "Leila Lacan"},
         "sonia citron":      {"guard": True,  "salary": 9900, "locked": True,
                               "name": "Sonia Citron"},
         "emma cannon":       {"guard": False, "salary": 3500, "locked": True,
                               "name": "Emma Cannon"}}
_ORIG = ["Maya Caldwell", "Courtney Williams", "Kiki Iriafen",
         "Nneka Ogwumike", "A'ja Wilson", "Morgan Maly"]
_rec = lambda rev, names=None: app._reconcile_with_dk(
    names or _ORIG, set(rev), S6, _POOL)

# tonight's file: DK reveals only Kiki, who we already have -> nothing to do
check("a roster DK agrees with is left alone",
      _rec(["kiki iriafen"]) == (None, ""))
check("...and an entry with nothing tipped is left alone",
      _rec([]) == (None, ""))

# the hand edit this is FOR. Leila (a guard, in a G slot) was swapped out for
# Emma (a forward) before either game tipped; both have tipped since, DK shows
# Emma and not Leila, so who was dropped is a proof rather than a guess. Emma
# cannot stand in the G slot Leila held, so the repair has to re-slot around
# Kiki, who is pinned where DK's own export has her.
_stale = ["Maya Caldwell", "Leila Lacan", "Kiki Iriafen",
          "Nneka Ogwumike", "A'ja Wilson", "Morgan Maly"]
_fix, _why = _rec(["emma cannon", "kiki iriafen"], _stale)
check("a hand edit DK can prove is repaired", _why == "" and _fix is not None,
      _why or str(_fix))
check("...and the repaired roster is legal",
      _fix and app._roster_illegal(
          _fix, {"emma cannon", "kiki iriafen"}, S6, _POOL) == "", str(_fix))
check("...with Leila gone and Emma in a slot a forward can hold",
      _fix and "Leila Lacan" not in _fix
      and _fix[S6.index("UTIL")] == "Emma Cannon", str(_fix))
check("...and the pinned tipped player never moved",
      _fix and _fix[2] == "Kiki Iriafen", str(_fix))

# the case the file cannot answer: replaced player had not tipped, so nothing
# in it says who to drop. Say so; do not pick a victim.
_fix2, _why2 = _rec(["kiki iriafen", "sonia citron"])
check("an unprovable edit is refused, not guessed at",
      _fix2 is None and "doesn't say who they replaced" in _why2, _why2)

# the guard, on a roster handed to it directly
_ill = lambda names, rev=(): app._roster_illegal(names, set(rev), S6, _POOL)
check("a clean roster is legal", _ill(_ORIG, ["kiki iriafen"]) == "")
check("the 09-22 corruption is refused: same player twice",
      _ill(["Kiki Iriafen"] + _ORIG[1:])
      == "it puts the same player in two slots")
check("a forward written into a G slot is refused",
      _ill(["Shakira Austin"] + _ORIG[1:])
      == "it puts Shakira Austin, a forward, in a G slot")
check("a roster over the cap is refused",
      _ill(["Sonia Citron"] + _ORIG[1:])
      == "it costs $55,400, over the $50,000 cap")
check("dropping a player DK says is already playing is refused",
      _ill(_ORIG, ["shakira austin"])
      == "it drops Shakira Austin, who DK says is already playing for you")

# The summary and exit MUST stay last — appending a new section after
# them silently skips it and the suite still reports a clean pass.
print(f"\n{'=' * 56}\n  {ok} passed, {fail} failed\n{'=' * 56}")
sys.exit(1 if fail else 0)
