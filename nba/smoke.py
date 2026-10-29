"""End-to-end check of the NBA optimizer.  Run: python3 nba/smoke.py

Builds synthetic Stokastic projections, Stokastic lineups, DK entries and DK
contest-standings files that match the real layouts — including their traps —
and runs the whole tool against them: the readers, both builders and their
legality, slot eligibility in the upload file, several contests in one export,
the pool, cores, per-player caps, the late-scratch removal, the coach, the log,
and grading against standings.

The layouts come from real files where one existed (a real NBA showdown DK
export, NYK @ SAS 06/13/2026; the WNBA standings trap) and from the old Base44
tool's parsers where one did not (the Stokastic NBA exports). The first real
NBA files of the season are the check on the second group.

Rules for editing it, inherited from wnba/smoke.py:

  * The summary and sys.exit MUST stay at the end of the file. A section
    appended after them is silently skipped and the suite still reports a clean
    pass. That happened twice on WNBA, once as 80/80 with five tests never run.
  * When a measurement changes a default, pin the new value here, so a future
    edit has to argue with the run that set it.

Run it before every commit.
"""
import csv
import io
import json
import os
import math
import random
import sys
import tempfile
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent))
import app                                   # noqa: E402
import classic as C                          # noqa: E402
import engine as E                           # noqa: E402
import sources as S                          # noqa: E402
from dk import CLASSIC_SLOTS, normalize_name  # noqa: E402

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}" + (f"  — {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {label}  — {detail}")


# Keep the build log out of the real one.
_TMP = tempfile.mkdtemp(prefix="nba_smoke_")
app.LOG_PATH = os.path.join(_TMP, "nba_builds.jsonl")

# ---------- synthetic Stokastic projections ----------
PROJ_COLS = ["Player", "Salary", "Position", "Team", "Opponent", "Minutes", "FPPM",
             "Projection", "Value", "Ownership %", "Optimal %", "Leverage", "Std Dev",
             "Boom", "Bust", "Ceiling", "Floor", "Injury", "Starting"]
POS_LADDER = ["PG", "SF", "C", "SG", "PF", "PG/SG", "SG/SF", "SF/PF", "PF/C", "PG", "C", "SF"]
SAL_LADDER = [11000, 9400, 8200, 7400, 6800, 6000, 5400, 4800, 4200, 3700, 3300, 3000]
TIP = ["07:00PM", "07:30PM", "08:00PM", "09:00PM", "10:00PM", "10:30PM"]


def slate(games, seed=1, cpt=False):
    """-> [dict] player rows. Deliberately lopsided ownership so duplication and
    the chalk have teeth; accented and suffixed names so the joins are tested."""
    rng = random.Random(seed)
    rows, pid = [], 50000000
    for gi, (a, b) in enumerate(games):
        for team, opp in ((a, b), (b, a)):
            for i in range(12):
                sal = SAL_LADDER[i]
                proj = round(sal / 1000.0 * 5.2 + rng.uniform(-4, 4), 2)
                name = f"{team} Player{i}"
                if team == games[0][0] and i == 0:
                    name = "Nikola Jokić"
                if team == games[0][1] and i == 1:
                    name = "Jaren Jackson Jr."
                pid += 1
                rows.append({
                    "Player": name, "Salary": sal, "Position": POS_LADDER[i],
                    "Team": team, "Opponent": opp, "Minutes": 36 - 2 * i,
                    "FPPM": round(proj / max(36 - 2 * i, 8), 2), "Projection": proj,
                    "Value": round(proj / sal * 1000, 2),
                    "Ownership %": round(max(0.5, 38 - 3.2 * i - 4 * gi + rng.uniform(-2, 2)), 1),
                    "Optimal %": round(max(0.0, 30 - 2.5 * i), 1), "Leverage": 0.0,
                    "Std Dev": round(0.22 * proj + 3.5, 2), "Boom": 10, "Bust": 20,
                    "Ceiling": round(proj * 1.25, 1), "Floor": round(proj * 0.75, 1),
                    "Injury": "", "Starting": "true" if i < 5 else "false",
                    "_id": str(pid), "_cid": str(pid + 900000), "_gi": gi,
                })
                if cpt:
                    rows[-1]["CPT Ownership %"] = round(max(0.2, 16 - 1.5 * i), 1)
    # classic ownership sums to ~800
    tot = sum(r["Ownership %"] for r in rows)
    for r in rows:
        r["Ownership %"] = round(r["Ownership %"] * (800.0 if not cpt else 500.0) / tot, 2)
    return rows


def proj_csv(rows, extra_cols=()):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=PROJ_COLS + list(extra_cols), extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _elig(pos):
    base = set(pos.split("/"))
    out = [s for s in CLASSIC_SLOTS if base & C.SLOT_TAKES[s]]
    return "/".join(out)


def game_key(r, games):
    a, b = games[r["_gi"]]
    return f"{a}@{b}"


def dk_classic(rows, games, contests=((150, "NBA $15K mini-MAX", "900001", "$0.50"),)):
    """A DK classic entries export in the real layout: entries, a blank column,
    Instructions, and the player pool whose header sits on an ENTRY row."""
    header = ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"] + list(CLASSIC_SLOTS) + ["", "Instructions"]
    ents, eid = [], 7000000000
    for n, name, cid, fee in contests:
        for _ in range(n):
            eid += 1
            ents.append([str(eid), name, cid, fee] + [""] * 8 + [""])
    pool_hdr = ["Position", "Name + ID", "Name", "ID", "Roster Position", "Salary",
                "Game Info", "TeamAbbrev", "AvgPointsPerGame"]
    pool = [[r["Position"], f"{r['Player']} ({r['_id']})", r["Player"], r["_id"],
             _elig(r["Position"]), str(r["Salary"]),
             f"{game_key(r, games)} 01/15/2027 {TIP[r['_gi'] % len(TIP)]} ET",
             r["Team"], "30.0"] for r in rows]
    out = [header]
    body = []
    for i in range(max(len(ents), 7 + 1 + len(pool))):
        row = ents[i] if i < len(ents) else [""] * 13
        row = list(row)
        if i < 6:
            row.append(f"{i + 1}. an instruction line")
        elif i == 7:
            row += pool_hdr
        elif i > 7 and i - 8 < len(pool):
            row += pool[i - 8]
        body.append(row)
    out += body
    buf = io.StringIO()
    csv.writer(buf).writerows(out)
    return buf.getvalue()


def field_csv(rows, n=1500, seed=5):
    """A Stokastic NBA lineups export: metric columns, the `Lineups` column that
    holds all eight names in ONE comma-separated cell, then PG..UTIL cells as
    'Name (id)' — one of them with double-escaped quote debris."""
    rng = random.Random(seed)
    ps = [dict(r) for r in rows]
    for p in ps:
        p["_el"] = set(_elig(p["Position"]).split("/"))
    hdr = ["Simulated ROI", "Projected FP", "OwnSum", "Win%", "Top 10%", "Cash%",
           "Dupes", "Lineups", "Salary"] + list(CLASSIC_SLOTS)
    out, seen = [hdr], set()
    tries = 0
    while len(out) <= n and tries < n * 50:
        tries += 1
        used, sal, lu = set(), 0, []
        for s in CLASSIC_SLOTS:
            el = [p for p in ps if s in p["_el"] and p["_id"] not in used
                  and sal + p["Salary"] + 3000 * (7 - len(lu)) <= 50000]
            if not el:
                break
            w = [p["Ownership %"] ** 1.5 + 0.1 for p in el]
            p = rng.choices(el, weights=w)[0]
            lu.append(p)
            used.add(p["_id"])
            sal += p["Salary"]
        if len(lu) != 8 or len({p["_gi"] for p in lu}) < 2:
            continue
        k = frozenset(used)
        if k in seen:
            continue
        seen.add(k)
        cells = [f"{p['Player']} ({p['_id']})" for p in lu]
        if len(out) == 3:
            cells[0] = '\\"' + cells[0] + '\\"'     # the quote-debris trap
        own = sum(p["Ownership %"] for p in lu)
        out.append([f"{rng.uniform(-50, 80):.1f}%", round(sum(p["Projection"] for p in lu), 1),
                    f"{own:.1f}%", f"{rng.uniform(0, 0.2):.3f}%", f"{rng.uniform(0, 12):.2f}%",
                    f"{rng.uniform(5, 30):.1f}%", str(int(own // 60)),
                    ", ".join(p["Player"] for p in lu), sal] + cells)
    buf = io.StringIO()
    csv.writer(buf).writerows(out)
    return buf.getvalue()


GAMES5 = [("DEN", "OKC"), ("BOS", "NYK"), ("LAL", "GSW"), ("MIA", "ORL"), ("SAS", "DAL")]
R5 = slate(GAMES5)
PROJ5 = proj_csv(R5)
DK5 = dk_classic(R5, GAMES5, contests=((150, "NBA $15K mini-MAX", "900001", "$0.50"),
                                       (20, "NBA $8K And-One", "900002", "$1"),
                                       (1, "NBA $10K Free Contest", "900003", "$0")))
FIELD5 = field_csv(R5)

print("\n-- readers --")
pl, rep = S.read_projections(PROJ5)
check("projections: 120 players read", len(pl) == 120, str(len(pl)))
check("projections: every column matched by exact name", not rep["loose"], str(rep["loose"]))
check("projections: Std Dev, Minutes and Starting are read",
      all(k in rep["matched"] for k in ("sd", "minutes", "starting")), str(rep["matched"]))
check("projections: ownership is Ownership %, not Optimal %",
      rep["matched"].get("own") == "Ownership %", rep["matched"].get("own"))
_ren = PROJ5.replace("Projection,", "Proj Pts,", 1).replace("Ownership %", "Proj Own", 1)
_pl2, _rep2 = S.read_projections(_ren)
check("a renamed 'Proj Own' column does not become the projection",
      _rep2["matched"].get("proj") == "Proj Pts" and _rep2["matched"].get("own") == "Proj Own",
      str(_rep2["matched"]))
jok = next(p for p in pl if p.name == "Nikola Jokić")
check("PG/SG eligibility widens to G and UTIL",
      next(p for p in pl if p.pos == "PG/SG").eligible == {"PG", "SG", "G", "UTIL"})
check("starting flag parsed", jok.starting is True, str(jok.starting))

dk5 = S.read_dk_entries(DK5)
check("DK: 171 entries across three contests, the pool-header row included",
      len(dk5["entries"]) == 171, str(len(dk5["entries"])))
check("DK: three contests kept apart", len(app._contests(dk5["entries"])) == 3)
check("DK: 120 players in the embedded pool", len(dk5["pool"]) == 120, str(len(dk5["pool"])))
check("DK: slots are PG..UTIL", tuple(dk5["slots"]) == CLASSIC_SLOTS, str(dk5["slots"]))
_pf = next(v for v in dk5["pool"].values() if v["pos"] == "PF/C")
check("DK: Roster Position becomes eligibility", _pf["eligible"] == {"PF", "C", "F", "UTIL"},
      str(_pf["eligible"]))
check("DK: tip time parsed from Game Info", _pf["start"].startswith("2027-01-15T"), _pf["start"])
check("DK: an accented name joins", normalize_name("Nikola Jokić") in dk5["pool"])

_by_id = {p.dk_id: p for p in pl}
_by_nm = {normalize_name(p.name): p for p in pl}
fe, frep = S.read_field(FIELD5, _by_id, _by_nm)
check("field: the Lineups column is NOT read as a roster column",
      frep.get("roster_columns") == list(CLASSIC_SLOTS), str(frep.get("roster_columns")))
check("field: every lineup resolves, quote debris included",
      frep["parsed"] == frep["rows"] and not frep["unresolved_rosters"],
      f"{frep['parsed']} of {frep['rows']}")
check("field: Win% stored as a fraction", all(e["win"] < 0.01 for e in fe))

print("\n-- classic build --")


def build(**opts):
    base = {"n": 40, "sims": 400, "seed": 0, "fieldCap": 47000}
    base.update(opts)
    return app.run_build(PROJ5, FIELD5, DK5, base)


r = build()
check("no error", not r.get("error"), r.get("error", ""))
lus = r.get("lineups", [])
check("40 lineups", len(lus) == 40, str(len(lus)))
check("all distinct", len({frozenset(p["name"] for p in l["players"]) for l in lus}) == len(lus))
check("8 players each, in PG..UTIL order",
      all([p["slot"] for p in l["players"]] == list(CLASSIC_SLOTS) for l in lus))
check("salary legal", all(l["salary"] <= 50000 for l in lus))
_ok_elig = all(p["slot"] in next(q for q in pl if q.name == p["name"]).eligible
               for l in lus for p in l["players"])
check("every player sits in a slot he is eligible for", _ok_elig)
check("two games on every lineup",
      all(len({next(q for q in pl if q.name == p["name"]).game for p in l["players"]}) >= 2
          for l in lus))
check("an upload file was written", bool(r.get("dkCsv")))
_up = list(csv.reader(io.StringIO(r["dkCsv"])))
check("upload: 40 + 20 + 1 rows — every contest takes the top of one list",
      len(_up) - 1 == 61, str(len(_up) - 1))
check("upload: the 20-max holds the top 20 of the same ranking",
      [row[4:] for row in _up[1:21]] == [row[4:] for row in _up[41:61]])
check("upload: every cell carries a DK id",
      all(c.rstrip(")").split("(")[-1].isdigit() for row in _up[1:] for c in row[4:]))
_starts = {normalize_name(v["name"]): v["start"] for v in dk5["pool"].values()}
_by_nm5 = {normalize_name(v["name"]): v for v in dk5["pool"].values()}
_pl_by_nm = {normalize_name(p.name): p for p in pl}


def _util_best(row):
    """Is UTIL's tip the latest any legal assignment of these eight allows?"""
    eight = [_pl_by_nm[normalize_name(c.split(" (")[0])] for c in row[4:12]]
    for p in eight:
        p.eligible = set(_by_nm5[normalize_name(p.name)]["eligible"])
    st = lambda p: _by_nm5[normalize_name(p.name)]["start"]
    best = max(st(a[7]) for a in C.assignments(eight, limit=5000))
    return st(eight[7]) == best


_util_late = sum(1 for row in _up[1:41] if _util_best(row))
check("upload: UTIL holds the latest tip any legal slotting allows",
      _util_late == 40, f"{_util_late} of 40")
_vend = [l for l in app._field_lineups(fe, "classic")[0]][:5]
_idx = E.dupe_index(_vend)
check("duplication: a roster the field holds is charged scale * (A + B * copies)",
      abs(E.estimated_dupes(_vend[0], _idx, scale=4.0)
          - 4.0 * (E.DUPE_A + E.DUPE_B * (1 + _vend[0].metrics["vdupes"]))
          * E.leftover_factor(_vend[0])) < 1e-9)
_chalk = C.Lineup(sorted(pl, key=lambda p: -p.ownership)[:8])
_punt = C.Lineup(sorted(pl, key=lambda p: p.ownership)[:8])
check("duplication: chalk is charged more than a punt off the field's pool",
      E.estimated_dupes(_chalk, {}, 1.0, 50000) > 100 * E.estimated_dupes(_punt, {}, 1.0, 50000))
check("duplication is in the ranking: classic score divides by (1 + dupes) ** DUPE_EXP",
      all(abs(l.metrics["score"] - l.metrics["win"] / (1 + l.metrics["dupes"]) ** E.DUPE_EXP)
          <= 1e-3 * l.metrics["win"] + 1e-5
          for l in E.rank([C.Lineup(pl[:8]), _chalk], E.simulate(pl, 200), [150.0] * 200, 200, {},
                          field_n=50000)))
check("win rates come from the field bar", any(l["win"] > 0 for l in lus))
check("the coach speaks", isinstance(r.get("coach"), list))

r_seed = build(seed=1)
check("the seed actually varies the build (lesson 3)",
      [l["players"] for l in r_seed.get("lineups", [])] != [l["players"] for l in lus])

print("\n-- pinned defaults --")
check("MAX_LEFTOVER is 2000 (WNBA: measured identical to no floor)", C.MAX_LEFTOVER == 2000)
check("DUPE_EXP is 0.25 (NFL showdown's price, until NBA re-fits it)", E.DUPE_EXP == 0.25)
check("OWN_LEAN is 0", E.OWN_LEAN == 0.0)
check("GAME_SD is 0 (WNBA residual correlations ~0; NBA unmeasured)", E.GAME_SD == 0.0)
check("no board-wide exposure cap by default", E.PLAYER_CAP == 1.0)
check("FILL_EXP is 3 (NFL: flattening bought variance, not money)", E.FILL_EXP == 3.0)
check("the WNBA removal constants", (app.REMOVE_SHARE, app.REMOVE_SAME_POS,
                                     app.REMOVE_CAP_SHARE, app.REMOVE_CAP_PTS)
      == (0.65, 1.6, 0.40, 8.0))

print("\n-- pool, cores, caps, removal --")
_pool_names = [x["Player"] for x in R5 if not x["Player"][-1].isdigit()
               or int(x["Player"][-1]) % 2 == 0]
rp = build(pool="\n".join(_pool_names), maxOffPool="0", split=40, poolAdds="off")
_in = {normalize_name(n) for n in _pool_names}
check("pool: every player comes from the sheet at off-pool 0",
      not rp.get("error") and all(normalize_name(p["name"]) in _in
                                  for l in rp["lineups"] for p in l["players"]),
      rp.get("error", ""))
# The old core rule (floor + construction weight), kept behind neutral=off.
rc = build(cores="Nikola Jokić\nMIA Player4\nNobody Mcghost", neutral="off")
check("a misspelled core is named back", any("Nobody Mcghost".lower() in n["text"].lower()
                                            for n in rc.get("notes", [])))
_jk = sum(1 for l in rc["lineups"] if any(p["name"] == "Nikola Jokić" for p in l["players"]))
_mi = sum(1 for l in rc["lineups"] if any(p["name"] == "MIA Player4" for p in l["players"]))
check("core floors hold: ceil(n / (cores + 1)) each", _jk >= 14 and _mi >= 14, f"{_jk}, {_mi}")
check("the coach grades each core", sum(1 for n in rc["coach"] if "Core check" in n["text"]) == 2)
check("the coach says what the floor did",
      any("Core floor" in n["text"] and "of 40" in n["text"] for n in rc["coach"]))
rk = build(capPlayers="Nikola Jokić 10\nGhost Man 20")
_jk2 = sum(1 for l in rk["lineups"] if any(p["name"] == "Nikola Jokić" for p in l["players"]))
check("a typed cap holds", _jk2 <= 4, f"{_jk2} of 40")
check("an unmatched cap is reported, not dropped",
      any("Ghost Man" in n["text"] for n in rk["notes"]))
_mate_before = next(p for p in pl if p.team == "DEN" and p.name == "DEN Player5").proj
rr = build(remove="Nikola Jokić, Not A Player")
check("a removed player is in no lineup",
      not any(p["name"] == "Nikola Jokić" for l in rr["lineups"] for p in l["players"]))
check("an unknown removal is reported",
      any("not a player" in n["text"].lower() and "NOT removed" in n["text"] for n in rr["notes"]))
_pl3, _ = S.read_projections(PROJ5)
_info, _miss = app.apply_removals(_pl3, {normalize_name("Nikola Jokić")})
_mate = next(p for p in _pl3 if p.name == "DEN Player5")
check("removal pushes production to teammates, capped at 8",
      0 < _mate.proj - _mate_before <= 8.0, f"{_mate_before} -> {_mate.proj}")
check("removal leaves ownership alone",
      _mate.ownership == next(p for p in pl if p.name == "DEN Player5").ownership)
check("the coach notes the removal", any("You removed" in n["text"] for n in rr["coach"]))

print("\n-- refusals --")
_wrong = dk_classic(slate([("PHX", "SAC"), ("CHI", "IND")], seed=9), [("PHX", "SAC"), ("CHI", "IND")])
rw = app.run_build(PROJ5, FIELD5, _wrong, {"n": 10, "sims": 200})
check("the wrong slate's DK file stops the build", bool(rw.get("error")), rw.get("error", "")[:60])
_c = next(p for p in pl if p.pos == "C")
_ents = [dk5["entries"][0]]
_pl4, _ = S.read_projections(PROJ5)
app._attach_dk(_pl4, dk5)
_center = next(p for p in _pl4 if p.pos == "C")
_eight = [p for p in _pl4 if p.pos != "C"][:7] + [_center]
_bad = app._check_upload(_ents, [[_center] + _eight[:7]], list(CLASSIC_SLOTS), "classic", dk5)
check("a center written into the PG slot is refused", any("PG slot" in b for b in _bad), str(_bad[:2]))
check("the same player twice is refused",
      any("twice" in b for b in app._check_upload(_ents, [[_center] * 8], list(CLASSIC_SLOTS),
                                                  "classic", dk5)))

print("\n-- showdown --")
SD_GAMES = [("NYK", "SAS")]
RSD = slate(SD_GAMES, seed=3, cpt=True)
PROJ_SD = proj_csv(RSD, extra_cols=("CPT Ownership %",))


def dk_showdown(rows):
    header = ["Entry ID", "Contest Name", "Contest ID", "Entry Fee", "CPT"] + ["UTIL"] * 5 + ["", "Instructions"]
    ents = [[str(8000000000 + i), "NBA Showdown $30K mini-MAX", "910001", "$0.50"] + [""] * 6 + [""]
            for i in range(30)]
    pool = []
    for x in rows:
        for slot, pid, sal in (("CPT", x["_cid"], int(x["Salary"] * 1.5)), ("UTIL", x["_id"], x["Salary"])):
            pool.append([x["Position"], f"{x['Player']} ({pid})", x["Player"], pid, slot, str(sal),
                         "NYK@SAS 06/13/2026 08:30PM ET", x["Team"], "30.0"])
    out = [header]
    for i in range(max(len(ents), 8 + len(pool))):
        row = list(ents[i]) if i < len(ents) else [""] * 11
        if i == 7:
            row += ["Position", "Name + ID", "Name", "ID", "Roster Position", "Salary",
                    "Game Info", "TeamAbbrev", "AvgPointsPerGame"]
        elif i > 7 and i - 8 < len(pool):
            row += pool[i - 8]
        out.append(row)
    buf = io.StringIO()
    csv.writer(buf).writerows(out)
    return buf.getvalue()


DK_SD = dk_showdown(RSD)
rs = app.run_build(PROJ_SD, "", DK_SD, {"n": 30, "sims": 400, "fieldCap": 30000})
check("showdown: no error", not rs.get("error"), rs.get("error", ""))
check("showdown: detected from the DK file", rs.get("format") == "showdown")
check("showdown: 30 lineups of CPT + 5 UTIL",
      len(rs["lineups"]) == 30 and all([p["slot"] for p in l["players"]] == ["CPT"] + ["UTIL"] * 5
                                       for l in rs["lineups"]))
check("showdown: salary counts the captain at 1.5x and stays legal",
      all(l["salary"] <= 50000 for l in rs["lineups"]))
check("showdown: both teams on every lineup",
      all(len({p["team"] for p in l["players"]}) == 2 for l in rs["lineups"]))
_sdup = list(csv.reader(io.StringIO(rs.get("dkCsv") or "")))
_cpt_ids = {x["_cid"] for x in RSD}
check("showdown: the captain cell carries the CAPTAIN id",
      len(_sdup) == 31 and all(row[4].rstrip(")").split("(")[-1] in _cpt_ids for row in _sdup[1:]))

print("\n-- LineStar: logged, never used --")


def linestar_csv(rows, games, scored=False):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "Position", "Team", "VersusStr", "Salary", "Projected", "PPG",
                "ProjOwn", "StartingStatus", "VegasImplied", "Vegas", "VegasTotals", "Scored"])
    for x in rows:
        a, b = games[x["_gi"]]
        vs = f"@{b}" if x["Team"] == a else f"vs {a}"
        out = x["Player"] == "DEN Player3"          # LineStar has him out
        w.writerow([x["Player"], x["Position"], x["Team"], vs, x["Salary"],
                    0 if out else round(x["Projection"] * 0.97 + 0.5, 2),
                    x["Projection"], x["Ownership %"], 4 if out else (1 if x["Starting"] == "true" else 2),
                    115.5 if x["Team"] == a else 110.0, -5.5 if x["Team"] == a else 5.5, 225.5,
                    round(x["Projection"], 1) if scored else 0])
    return buf.getvalue()


LS5 = linestar_csv(R5, GAMES5)
rl = app.run_build(PROJ5, FIELD5, DK5, {"n": 40, "sims": 400, "seed": 0, "fieldCap": 47000},
                   linestar_text=LS5)
check("LineStar: the build is IDENTICAL with and without it",
      [l["players"] for l in rl["lineups"]] == [l["players"] for l in lus])
check("LineStar: agreement with Stokastic is reported",
      any("agree at r =" in n["text"] for n in rl["notes"]))
check("LineStar: a player it has out while Stokastic projects him is flagged",
      any("DEN Player3" in n["text"] and "Late scratch" in n["text"] for n in rl["notes"]))
_ll = [json.loads(l) for l in open(app.LOG_PATH, encoding="utf-8")][-1]
check("LineStar: Vegas and its projection are in the log",
      (_ll["contest_state"].get("vegas") or {}).get("DEN", {}).get("implied") == 115.5
      and any(p["ls_proj"] is not None for p in _ll["players"]), str(_ll["contest_state"].get("vegas"))[:80])
rpost = app.run_build(PROJ5, FIELD5, DK5, {"n": 10, "sims": 200},
                      linestar_text=linestar_csv(R5, GAMES5, scored=True))
check("LineStar: a post-game file is refused for logging, loudly",
      any("post-game" in n["text"] for n in rpost["notes"]))

print("\n-- the wrong projections file is refused (Oct 20) --")


def etr_csv(rows, rename=None):
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL)
    w.writerow(["Player", "Pos", "Team", "Opp", "Salary", "FPTS", "Value", "Ceiling",
                "ProjOwn", "Site", "Slate", "Scenario", "DKSlateID", "DKName"])
    for x in rows:
        nm = (rename or {}).get(x["Player"], x["Player"])
        w.writerow([nm, x["Position"].split("/")[0], x["Team"], x["Opponent"],
                    f"${x['Salary']:,}", round(x["Projection"] + 0.7, 1), 5.0,
                    round(x["Projection"] * 1.2, 1), f"{x['Ownership %']}%",
                    "DK", "MAIN", "Default", x["_id"], nm])
    return buf.getvalue()


ETR5 = etr_csv(R5, rename={"Nikola Jokić": "N. Jokic"})
re_ = app.run_build(ETR5, FIELD5, DK5, {"n": 10, "sims": 200})
check("an ETR file in the projections slot stops the build, by name",
      bool(re_.get("error")) and "ETR's projections file" in re_["error"], re_.get("error", "")[:90])
check("...and the drop slot says so before any build",
      app._describe("proj", ETR5)["ok"] is False
      and "NOT Stokastic" in app._describe("proj", ETR5)["msg"])
_nosd = proj_csv(R5).replace("Std Dev", "Spread", 1)
check("a projections file with no Std Dev stops the build",
      "no Std Dev" in (app.run_build(_nosd, FIELD5, DK5, {"n": 10, "sims": 200}).get("error") or ""))
_ovr = app.run_build(_nosd, FIELD5, DK5, {"n": 10, "sims": 200, "allowNonStokastic": True})
check("...unless deliberately overridden, and then it still warns",
      not _ovr.get("error") and any("No Std Dev" in n["text"] for n in _ovr["notes"]))
check("Stokastic's own file is accepted and labelled",
      app._describe("proj", PROJ5)["msg"].startswith("Stokastic ✓"))

print("\n-- rounding out the pool --")
ra = build(pool="\n".join(_pool_names), maxOffPool="0", split=40)
_la = [json.loads(l) for l in open(app.LOG_PATH, encoding="utf-8")][-1]
_adds = _la["settings"]["poolAdds"]
_byname = {x["Player"]: x for x in R5}
check("round-out: up to 5 cheap and 5 mid adds, all off the sheet",
      0 < len(_adds["cheap"]) <= 5 and 0 < len(_adds["mid"]) <= 5
      and not (set(_adds["cheap"]) | set(_adds["mid"])) & set(_pool_names), str(_adds))
check("round-out: cheap band is $4,500 or less, mid $4,600-$7,500, all projected 14+",
      all(_byname[n]["Salary"] <= 4500 and _byname[n]["Projection"] >= 14 for n in _adds["cheap"])
      and all(4600 <= _byname[n]["Salary"] <= 7500 and _byname[n]["Projection"] >= 14
              for n in _adds["mid"]))
_off = [x for x in R5 if x["Player"] not in _pool_names and x["Projection"] >= 14]
_best_cheap = sorted((x for x in _off if x["Salary"] <= 4500),
                     key=lambda x: -x["Projection"] / x["Salary"])[:5]
check("round-out: they are the best points per $1,000 in the band",
      set(_adds["cheap"]) == {x["Player"] for x in _best_cheap})
check("round-out: the build names them", any("Rounded out your pool" in n["text"] for n in ra["notes"]))
check("round-out: added players may be used, nobody else off the sheet is",
      all(p["name"] in _pool_names or p["name"] in _adds["cheap"] + _adds["mid"]
          + _adds["stud"] for l in ra["lineups"] for p in l["players"]))
check("round-out: every stud add is held to his minimum",
      all(sum(1 for l in ra["lineups"] for p in l["players"] if p["name"] == nm) >= k
          for nm, k in _adds["studFloors"].items()), str(_adds["studFloors"]))
check("round-out: each added player is marked in the log",
      any(p.get("added") in ("cheap", "mid") for p in _la["players"]) or not any(
          p["name"] in _adds["cheap"] + _adds["mid"] for p in _la["players"]))
rn = build(pool="\n".join(_pool_names), maxOffPool="0", split=40, poolAdds="off")
check("round-out: the switch turns it off",
      not any("Rounded out" in n["text"] for n in rn["notes"]))
check("round-out: pinned at 5 per band, 14+ projected, $4,500 / $4,600-$7,500",
      (app.ADD_N, app.ADD_MIN_PROJ, app.ADD_CHEAP_MAX, app.ADD_MID_MIN, app.ADD_MID_MAX)
      == (5, 14.0, 4500, 4600, 7500))

print("\n-- minimum lineups for a player --")
_sheet = [x["Player"] for x in R5 if x["Player"] not in ("DEN Player10", "DEN Player11")]
rf = build(pool="\n".join(_sheet), maxOffPool="0",
           floorPlayers="DEN Player10 2\nNobody Here 3\nDEN Player11 two", split=40,
           poolAdds="off",
           fileNames={"proj": "DK_NBA_Main_Pre_Contest_Projections.csv", "dk": "DKEntries.csv"})
_d10 = sum(1 for l in rf["lineups"] if any(p["name"] == "DEN Player10" for p in l["players"]))
check("a typed minimum puts an off-sheet player in at least that many lineups",
      _d10 >= 2, f"{_d10} of 40")
check("...and not far more (a minimum, not a core)", _d10 <= 8, f"{_d10} of 40")
check("...and the build confirms it met the minimum",
      any(n["type"] == "good" and "minimum" in n["text"] and "DEN Player10" in n["text"]
          for n in rf["notes"]))
check("an unmatched minimum is named back", any("Nobody Here" in n["text"] for n in rf["notes"]))
check("a minimum with no number is named back", any("DEN Player11 two" in n["text"]
                                                      for n in rf["notes"]))
check("the pool still binds everyone else",
      all(p["name"] in _sheet or p["name"] == "DEN Player10"
          for l in rf["lineups"] for p in l["players"]))
_lf = [json.loads(l) for l in open(app.LOG_PATH, encoding="utf-8")][-1]
check("the log records which files ran, by name and fingerprint",
      _lf["files"]["proj"]["name"] == "DK_NBA_Main_Pre_Contest_Projections.csv"
      and len(_lf["files"]["proj"]["sha1"]) == 12 and _lf["files"]["field"]["sha1"])
check("the log records the minimums", _lf["settings"]["floorPlayers"] == {"DEN Player10": 2})

print("\n-- late swap --")
import lateswap as LS                        # noqa: E402
LS.SWAP_LOG_PATH = os.path.join(_TMP, "nba_swaps.jsonl")
_g0 = {x["Team"] for x in R5 if x["_gi"] == 0}           # DEN@OKC is in progress
_entries40 = [row for row in _up[1:41]]                    # the first build's 150-max rows


def dk_midslate(rows, games, entry_rows):
    """The first build's entries, mid-slate: DEN@OKC tipped (LOCKED, In Progress)."""
    header = ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"] + list(CLASSIC_SLOTS) + ["", "Instructions"]
    team_of = {x["_id"]: x["Team"] for x in rows}
    ents = []
    for er in entry_rows:
        cells = []
        for c in er[4:12]:
            pid = c.rstrip(")").split("(")[-1]
            cells.append(c + (" (LOCKED)" if team_of[pid] in _g0 else ""))
        ents.append(er[:4] + cells + [""])
    pool = [[x["Position"], f"{x['Player']} ({x['_id']})" + (" (LOCKED)" if x["Team"] in _g0 else ""),
             x["Player"], x["_id"], _elig(x["Position"]), str(x["Salary"]),
             "In Progress" if x["Team"] in _g0 else
             f"{game_key(x, games)} 01/15/2027 {TIP[x['_gi'] % len(TIP)]} ET",
             x["Team"], "30.0"] for x in rows]
    out = [header]
    for i in range(max(len(ents), 8 + len(pool))):
        row = list(ents[i]) if i < len(ents) else [""] * 13
        if i == 7:
            row += ["Position", "Name + ID", "Name", "ID", "Roster Position", "Salary",
                    "Game Info", "TeamAbbrev", "AvgPointsPerGame"]
        elif i > 7 and i - 8 < len(pool):
            row += pool[i - 8]
        out.append(row)
    b = io.StringIO()
    csv.writer(b).writerows(out)
    return b.getvalue()


def stand_midslate(entry_rows, field_rows, stale_id=None):
    """Live standings: DEN@OKC players revealed with points so far and 12 minutes
    still to play each; everyone else LOCKED with 48."""
    name_of = {x["_id"]: x["Player"] for x in R5}
    team_of = {x["_id"]: x["Team"] for x in R5}
    so_far = {x["Player"]: round(x["Projection"] * 0.75, 2) for x in R5}
    out = [["Rank", "EntryId", "EntryName", "TimeRemaining", "Points", "Lineup", "",
            "Player", "Roster Position", "%Drafted", "FPTS"]]
    allr = [(er[0], [c.rstrip(")").split("(")[-1].strip() for c in er[4:12]]) for er in entry_rows]
    id_of = {x["Player"]: x["_id"] for x in R5}
    allr += [(str(9500000000 + k), [id_of[p.name] for p in fr["flex"]])
             for k, fr in enumerate(field_rows)]
    for k, (eid, ids) in enumerate(allr):
        pts, left, cell, swapped = 0.0, 0, [], [False]
        for s_, pid in zip(sorted(CLASSIC_SLOTS), ids):
            if team_of[pid] in _g0:
                nm = name_of[pid]
                if eid == stale_id and not swapped[0]:
                    swapped[0] = True
                    nm = next(x["Player"] for x in R5 if x["Team"] in _g0 and x["Player"] not in
                              [name_of[i] for i in ids])
                pts += so_far[nm]
                left += 12
                cell.append(f"{s_} {nm}")
            else:
                left += 48
                cell.append(f"{s_} LOCKED")
        out.append([k + 1, eid, "u", left, round(pts, 2), " ".join(cell), "", "", "", "", ""])
    b = io.StringIO()
    csv.writer(b).writerows(out)
    return b.getvalue()


_mid = dk_midslate(R5, GAMES5, _entries40)
_dkm = S.read_dk_entries(_mid)
check("swap: the mid-slate export reads locks from the cells and the pool",
      sum(1 for v in _dkm["pool"].values() if v["locked"]) == 24
      and any(c["locked"] for e in _dkm["entries"] for c in e["cells"]))
_stand = stand_midslate(_entries40, fe[:300])
LS_LOG = app.LOG_PATH
s0 = LS.run_late_swap(PROJ5, _mid, _stand, {}, log_path=LS_LOG)
check("swap: runs", not s0.get("error"), s0.get("error", ""))
_s150 = [s for s in s0["swaps"] if s["contest"].startswith("NBA $15K")]
# No news here, so the only moves allowed are free ones that clear the no-news
# bar (+14). Whether the synthetic build leaves one available depends on the
# build, so the rule is checked, not a count.
_moved = [s for s in s0["swaps"] if not s.get("hold")]
check("swap: with no news, every move clears the +14 bar and carries no news",
      all((s.get("gain") or 0) >= LS.SWAP_DISC_GAIN and not s.get("news") for s in _moved)
      and s0["changed"] == len(_moved) and len(_moved) <= 2,
      f"{s0['changed']} changed: " + ", ".join(str(s.get("gain")) for s in _moved))
check("swap: every hold says why", all(s.get("hold") for s in _s150 if s not in _moved))
_one = next(s for s in _s150 if s.get("timeLeft"))
check("swap: projected final counts the clock, not just the score",
      _one["projFinal"] is not None and _one["projFinal"] > _one["banked"] + 50,
      f"now {_one['banked']}, left {_one['timeLeft']}, final {_one['projFinal']}")
check("swap: the projected top-1% line is reported", s0.get("winLine"))
# a held, unlocked player is ruled out
_held = {}
for er in _entries40:
    for c in er[4:12]:
        nm = c.split(" (")[0]
        if next(x["Team"] for x in R5 if x["Player"] == nm) not in _g0:
            _held[nm] = _held.get(nm, 0) + 1
_out_nm = max(_held, key=_held.get)
s1 = LS.run_late_swap(PROJ5, _mid, _stand, {"remove": _out_nm}, log_path=LS_LOG)
_rows1 = [s for s in s1["swaps"] if s["contest"].startswith("NBA $15K")]
_holding = [s for s in _rows1 if any(_out_nm in n for n in s["news"])]
check("swap: news is found when a held player is ruled out",
      _out_nm.lower() in str(s1["news"]).lower() or normalize_name(_out_nm) in s1["news"])
check("swap: every lineup holding the ruled-out player is moved",
      _holding and all(not s["keep"] and _out_nm in s["out"] for s in _holding),
      f"{sum(1 for s in _holding if not s['keep'])} of {len(_holding)}")
check("swap: news moves are marked as news", all(s["forced"] for s in _holding if not s["keep"]))
check("swap: news moves beat what was entered by +8",
      all(s["gain"] >= LS.SWAP_NEWS_GAIN for s in s1["swaps"] if not s["keep"] and s["forced"]))
_nn = lambda s: {n.split(":")[0] for n in s["news"]}
check("swap: a news move takes the news player out",
      all(_nn(s) & set(s["out"]) for s in s1["swaps"] if not s["keep"] and s["forced"]))
check("swap: a move that keeps the news player is a no-news move (+14)",
      all(not s["forced"] and s["gain"] >= LS.SWAP_DISC_GAIN for s in s1["swaps"]
          if not s["keep"] and s["news"] and not _nn(s) & set(s["out"])))
check("swap: a lineup with no news moves only on +14",
      all(s["gain"] >= LS.SWAP_DISC_GAIN for s in s1["swaps"] if not s["keep"] and not s["forced"]))
check("swap: the narrow news-only choice is still logged beside it",
      any(s["shadow"]["newsRoster"] for s in s1["swaps"] if not s["keep"]))
_ok_locks = True
for s in s1["swaps"]:
    before = next(csv.reader([s["rowBefore"]]))[4:]
    after = next(csv.reader([s["rowAfter"]]))[4:]
    for b_, a_ in zip(before, after):
        pid = b_.rstrip(")").split("(")[-1]
        if next(x["Team"] for x in R5 if x["_id"] == pid) in _g0 and b_ != a_:
            _ok_locks = False
check("swap: no locked player ever leaves his slot", _ok_locks)
_pool_el = {v["dk_id"]: v for v in _dkm["pool"].values()}
_legal = True
for s in s1["swaps"]:
    ids = [c.rstrip(")").split("(")[-1] for c in next(csv.reader([s["rowAfter"]]))[4:]]
    if (len(set(ids)) != 8 or sum(_pool_el[i]["salary"] for i in ids) > 50000
            or any(sl not in _pool_el[i]["eligible"] for sl, i in zip(CLASSIC_SLOTS, ids))):
        _legal = False
check("swap: every written row is DK-legal (eligibility, cap, no repeats)", _legal)
_bar = LS.SWAP_NEWS_GAIN
LS.SWAP_NEWS_GAIN = 1000.0                   # no ordinary move can clear this
_s4 = LS.run_late_swap(PROJ5, _mid, _stand, {"remove": _out_nm}, log_path=LS_LOG)
LS.SWAP_NEWS_GAIN = _bar
check("swap: a ruled-out player is swapped EVERY time, whatever the bar",
      not any(_out_nm in s["rowAfter"] for s in _s4["swaps"])
      and all(not s["keep"] for s in _s4["swaps"] if any(_out_nm in n for n in s["news"])))
check("swap: the ruled-out player is in no written row",
      not any(_out_nm in s["rowAfter"] for s in s1["swaps"]))
check("swap: both policies are evaluated on every lineup",
      all("freeGain" in s["shadow"] and "newsGain" in s["shadow"] for s in _rows1))
_sl = [json.loads(l) for l in open(LS.SWAP_LOG_PATH, encoding="utf-8")]
check("swap: each run is logged with both policies", len(_sl) == 3 and "shadow" in _sl[-1]["swaps"][0])
_core = next(c.split(" (")[0] for c in _entries40[0][4:12]
             if next(x["Team"] for x in R5 if x["Player"] == c.split(" (")[0]) not in _g0
             and c.split(" (")[0] != _out_nm)
s2 = LS.run_late_swap(PROJ5, _mid, _stand, {"remove": _out_nm, "cores": _core,
                                           "neutral": "off"}, log_path=LS_LOG)
check("swap: a core with no news is never swapped out",
      not any(_core in s["out"] for s in s2["swaps"] if not s["keep"]))
_stale_id = _entries40[3][0]
s3 = LS.run_late_swap(PROJ5, _mid, stand_midslate(_entries40, fe[:300], stale_id=_stale_id), {},
                      log_path=LS_LOG)
check("swap: an entries file DK contradicts is left alone and named",
      any(s["entryId"] == _stale_id and "out of date" in (s.get("hold") or "") for s in s3["swaps"]))
check("swap: an ETR file is refused here too",
      "not Stokastic's" in (LS.run_late_swap(ETR5, _mid, _stand, {}, log_path=LS_LOG).get("error") or ""))
check("swap: a showdown file is refused (everything locks at once)",
      "classic only" in (LS.run_late_swap(PROJ5, DK_SD, "", {}, log_path=LS_LOG).get("error") or ""))
check("swap: bars pinned at WNBA x1.4 (8 news / 14 no news / 17 off pool)",
      (LS.SWAP_NEWS_GAIN, LS.SWAP_DISC_GAIN, LS.SWAP_OFF_POOL_GAIN) == (8.0, 14.0, 17.0))

print("\n-- standings and grading --")
_log = [json.loads(l) for l in open(app.LOG_PATH, encoding="utf-8")]
check("the log carries one line per entry", len(_log) > 100, str(len(_log)))
_last = _log[-1]
check("log: shape, minutes, starting, spread and the slate's game set",
      all(k in _log[0] for k in ("build_id", "game_block", "team_block", "slate_games", "games", "projection"))
      and all(k in _log[0]["players"][0] for k in ("minutes", "starting", "sd", "slot")))

# Standings for the first classic build: our entries plus a field, one roster
# duplicated three times, and DK's grouped-by-position roster order.
_first_ts = _log[0]["build_id"]
_ours = [x for x in _log if x["build_id"] == _first_ts and x["contest_id"] == "900001"]
_rng = random.Random(4)
_actual = {x["Player"]: round(max(0.0, x["Projection"] + _rng.gauss(0, 8)) * 4) / 4 for x in R5}


def _cell(names):
    grp = sorted(names, key=lambda n: next(x["Position"] for x in R5 if x["Player"] == n))
    labels = sorted(["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"])   # NOT upload order
    return " ".join(f"{lab} {n}" for lab, n in zip(labels, grp))


_entries = []
for x in _ours:
    names = [p["name"] for p in x["players"]]
    _entries.append((x["entry_id"], names))
_dup = _entries[0][1]
for k in range(3):
    _entries.append((str(9100000000 + k), _dup))
for k, row in enumerate(fe[:400]):
    _entries.append((str(9200000000 + k), [p.name for p in row["flex"]]))
_entries.append(("9300000000", ["LOCKED"] * 8))
_scored = sorted(((sum(_actual[n] for n in names) if names[0] != "LOCKED" else 0.0, eid, names)
                  for eid, names in _entries), reverse=True)
_hdr = ["Rank", "EntryId", "EntryName", "TimeRemaining", "Points", "Lineup", "", "Player",
        "Roster Position", "%Drafted", "FPTS"]
_prow = []
for x in R5:     # every player listed once PER SLOT, ownership split across rows
    _prow.append([x["Player"], "UTIL", f"{x['Ownership %'] * 0.3:.2f}%", _actual[x["Player"]]])
    _prow.append([x["Player"], x["Position"].split("/")[0], f"{x['Ownership %'] * 0.7:.2f}%",
                  _actual[x["Player"]]])
_srows = [_hdr]
for i, (pts, eid, names) in enumerate(_scored):
    row = [str(i + 1), eid, "user", "0", f"{pts:.2f}",
           _cell(names) if names[0] != "LOCKED" else " ".join(f"{s} LOCKED" for s in sorted(CLASSIC_SLOTS)), ""]
    row += _prow[i] if i < len(_prow) else ["", "", "", ""]
    _srows.append(row)
_sb = io.StringIO()
csv.writer(_sb).writerows(_srows)
STAND = _sb.getvalue()

st, srep = S.read_standings(STAND)
check("standings: every entry row read", srep["entries"] == len(_entries), str(srep["entries"]))
check("standings: ownership is the SUM of a player's slot rows (WNBA's trap)",
      abs(srep["ownership"][normalize_name("Nikola Jokić")] - R5[0]["Ownership %"]) < 0.05,
      f"{srep['ownership'][normalize_name('Nikola Jokić')]:.2f} vs {R5[0]['Ownership %']}")
check("standings: a LOCKED roster is not read as players",
      srep["copies"].get(("", frozenset(["locked"]))) is None)
check("standings: the parse reproduces the file's own Points",
      srep["points_checked"] > 0 and srep["points_agree"] == srep["points_checked"],
      f"{srep['points_agree']} of {srep['points_checked']}")
check("standings: an unknown slot layout is unreadable, not guessed",
      S._standings_roster("QB Josh Allen WR Someone") is None
      or "QB" not in (S._standings_roster("QB Josh Allen WR Someone") or ([], []))[0])
g = app.grade(STAND, log_path=app.LOG_PATH)
check("grade: no error", not g.get("error"), g.get("error", ""))
check("grade: our entries matched by DK Entry ID",
      g.get("contest", {}).get("matched_by_entry_id") == len(_ours),
      f"{g.get('contest', {}).get('matched_by_entry_id')} of {len(_ours)}")
check("grade: picks the build that was actually entered, roster for roster",
      g.get("contest", {}).get("roster_match") == len(_ours),
      f"{g.get('contest', {}).get('roster_match')} of {len(_ours)}")
check("grade: the tripled roster shows 4 real copies",
      g.get("overall", {}).get("copies_max", 0) >= 4, str(g.get("overall", {}).get("copies_max")))
check("grade: the player table has projected vs actual and real ownership",
      g.get("players") and all(p["actual"] is not None and p["own_real"] is not None
                               for p in g["players"]))

# ---- an old night's projections are refused (Oct 21: the morning Data Hub
# file still held Oct 20's games and passed under a green tick) ----
from dk import Player as _P
_olog = os.path.join(_TMP, "old_slate.jsonl")
with open(_olog, "w", encoding="utf-8") as _fh:
    _fh.write(json.dumps({"slate": "2000-01-01", "games": ["AAA@BBB", "CCC@DDD"]}) + "\n")
_mk = lambda t, o: _P(name=t + "1", dk_id="1", pos="PG", team=t, opponent=o, salary=5000, proj=20,
                       ownership=10)
_oldp = [_mk("AAA", "BBB"), _mk("BBB", "AAA"), _mk("CCC", "DDD"), _mk("DDD", "CCC")]
check("old slate: the same games as an earlier day's build are named as that day",
      app._old_slate(_oldp, _olog) == "2000-01-01", str(app._old_slate(_oldp, _olog)))
check("old slate: a different set of games passes",
      app._old_slate(_oldp[:2] + [_mk("EEE", "FFF"), _mk("FFF", "EEE")], _olog) is None)
check("old slate: one game (showdown) is never called old — back-to-backs repeat",
      app._old_slate(_oldp[:2], _olog) is None)
_LP, app.LOG_PATH = app.LOG_PATH, _olog
_d = app._describe("proj", "Player,Salary,Position,Team,Opponent,Projection,Ownership %,Std Dev\n"
                   + "".join(f"{t}1,5000,PG,{t},{o},20,10,8\n" for t, o in
                             (("AAA", "BBB"), ("BBB", "AAA"), ("CCC", "DDD"), ("DDD", "CCC"))))
app.LOG_PATH = _LP
check("old slate: slot 1 refuses the file and says which day it is from",
      not _d.get("ok") and "2000-01-01" in _d.get("msg", ""), str(_d))

# ---- stud adds: top 3 by projection, above $7,500, under 10% owned, off the
# sheet, each with a minimum of 2x projected ownership (Oct 22) ----
_st = lambda nm, sal, pr, own, **kw: _P(name=nm, dk_id=nm, pos="SG", team="AAA",
                                       opponent="BBB", salary=sal, proj=pr,
                                       ownership=own, **kw)
_sp = [_st("Edw", 8800, 44.9, 3.3), _st("Ban", 8700, 43.0, 2.2), _st("Sen", 8900, 42.2, 1.2),
       _st("Reav", 8000, 42.0, 5.6), _st("Chalk", 9000, 50.0, 30.0),
       _st("Cheap", 7500, 46.0, 1.0), _st("OnSheet", 9500, 55.0, 2.0)]
_sp[-1].in_pool = True
_got = app._stud_adds(_sp, 150)
check("studs: top 3 by projection above $7,500 under 10% owned, off the sheet",
      [p.name for p, _k in _got] == ["Edw", "Ban", "Sen"], str([p.name for p, _k in _got]))
check("studs: minimum is 2x projected ownership in lineups (10 / 7 / 4 of 150)",
      [k for _p, k in _got] == [10, 7, 4], str([k for _p, k in _got]))
check("studs: a player capped at 0 is passed over for the next one",
      [p.name for p, _k in app._stud_adds(_sp, 150, {"Edw"})] == ["Ban", "Sen", "Reav"])
check("studs: the minimum is at least one lineup in a small set",
      all(k >= 1 for _p, k in app._stud_adds(_sp, 20)))

# ---- salary left cuts the duplicate estimate (NBA Salary Left Report 10-22) ----
class _LU(C.Lineup):
    def __init__(self, sal):
        self._sal = sal
    salary = property(lambda self: self._sal)
check("salary left: $0 / $100-300 / $400-700 / $800-1,200 / more = x1 / .5 / .15 / .05 / .03",
      [E.leftover_factor(_LU(50000 - x)) for x in (0, 300, 700, 1200, 2000)]
      == [1.0, 0.5, 0.15, 0.05, 0.03])
check("salary left: showdown is untouched (measured on classic only)",
      E.leftover_factor(E.ShowdownLineup.__new__(E.ShowdownLineup)) == 1.0)

# ---- the info section and the cheap tier's range (Oct 23) ----
_lo = _st("Lo", 3100, 7.8, 1.1); _lo.sd = 1.0
_hi = _st("Hi", 6000, 30.0, 20.0); _hi.sd = 9.0
check("range: under 10 projected, the spread is at least 6 points",
      E.player_sd(_lo) == 6.0 and E.player_sd(_hi) == 9.0)
_inf = ra["info"]
check("info: confirmations collapse into short check labels",
      any("distinct lineups" in c for c in _inf["checks"])
      and any(c.startswith("Stokastic projections") for c in _inf["checks"]), str(_inf["checks"]))
check("info: every warning lands in the problem line",
      len(_inf["problems"]) == sum(1 for x in ra["notes"] if x["type"] == "warn"))
_used = {p["name"] for l in ra["lineups"] for p in l["players"]}
check("info: the zero list is pool / added players in no lineup, and only them",
      all(z["name"] not in _used for z in _inf["zeroes"])
      and all(z["name"] in _pool_names or z["from"] != "pool" for z in _inf["zeroes"]))
check("info: overweights are 15+ over the field, with floor and ceiling numbers",
      all(o["over"] >= 15 and o["floorKeeps"] is not None and o["ceilingAdds"] is not None
          for o in _inf["overweights"]), str(_inf["overweights"])[:200])
check("info: a same-range peer is within $600 and a 40-60% coin flip",
      all(40 <= q["beats"] <= 60 for o in _inf["overweights"] for q in o["peers"]))

# ---- neutral by default (NBA Neutral Build Report 10-24) ----
import collections as _co
check("neutral plan by slate size: 2 no rail no vendor / 4 rail no vendor / 6 rail + vendor / 11 loose rail + quota",
      (E.neutral_plan(2)["railShare"], E.neutral_plan(2)["vendor"]) == (None, False)
      and E.neutral_plan(4)["rail"] == 1.25 and not E.neutral_plan(4)["vendor"]
      and E.neutral_plan(6)["rail"] == 1.25 and E.neutral_plan(6)["vendor"]
      and E.neutral_plan(11)["rail"] == 1.5 and E.neutral_plan(11)["quota"])
check("neutral team limit at 6 games is 63% of entries",
      round(100 * E.neutral_plan(6)["railShare"]) == 63)
rn0 = build(cores="Nikola Jokić\nMIA Player4\nNobody Mcghost")
check("neutral: a misspelled core is still named back",
      any("nobody mcghost" in n["text"].lower() for n in rn0["notes"]))
check("neutral: typed cores carry no floor and no core check",
      not any("Core check" in n["text"] for n in rn0["coach"])
      and not rn0["info"]["cores"])
check("neutral: the build says so in the check line",
      any(c.startswith("neutral: 5 games") for c in rn0["info"]["checks"]), str(rn0["info"]["checks"]))
_lim = math.ceil(E.neutral_plan(5)["railShare"] * 40)
_tc = _co.Counter(t for l in rn0["lineups"] for t in {p["team"] for p in l["players"]})
check("neutral: no team over the rail", max(_tc.values()) <= _lim, f"{_tc.most_common(2)} vs {_lim}")
_gm = {}
for _a, _b in GAMES5:
    _gm[_a] = _gm[_b] = "@".join(sorted([_a, _b]))
_main = _co.Counter()
for l in rn0["lineups"]:
    if l["source"] != "mine":
        continue
    _g = _co.Counter(_gm[p["team"]] for p in l["players"])
    _main[_g.most_common(1)[0][0]] += 1
check("neutral: our arm's main games are spread across the slate",
      len(_main) >= 4 and max(_main.values()) <= 0.4 * sum(_main.values()), str(_main))
_qp = [p for p in pl if p.proj > 0]
_qc = C.build_candidates_quota(_qp, 600, 0.45, rng=random.Random(1))
_qt = _co.Counter(t for lu in _qc for t in {p.team for p in lu.players})
_plain = C.build_candidates(_qp, 600, rng=random.Random(1))
_pt = _co.Counter(t for lu in _plain for t in {p.team for p in lu.players})
check("quota generation pulls the most-held team down",
      max(_qt.values()) / len(_qc) < max(_pt.values()) / len(_plain),
      f"{max(_qt.values()) / len(_qc):.2f} vs {max(_pt.values()) / len(_plain):.2f}")

# ---- late-swap bugs from the Oct 23 night slate ----
_bp = _st("Bench Guy", 5000, 18.0, 5.0, starting=False); _bp.eligible = {"SG", "G", "UTIL"}
_sp = _st("Cheap Starter", 3600, 27.0, 5.0, starting=True); _sp.eligible = {"SG", "G", "UTIL"}
_nb = LS.news_names([_bp, _sp], {"bench guy": {"proj": 18.0, "starting": False}}, set(), {"bench guy"})
check("swap news: a bench player the build already knew about is not news", not _nb, str(_nb))
_nb2 = LS.news_names([_bp, _sp], {}, set(), {"bench guy"})
check("swap news: with no build to compare, a held bench player beaten by a cheaper starter is",
      "bench guy" in _nb2)
check("swap news: the bench rule ignores players you do not hold",
      not LS.news_names([_bp, _sp], {}, set(), set()))
_s5 = LS.run_late_swap(PROJ5, _mid, _stand, {"cores": _core, "neutral": "off"}, log_path=LS_LOG)
check("swap: cores with no pool are not a pool (nothing held for being off-pool)",
      not any("off your pool" in (x.get("hold") or "") for x in _s5["swaps"]))

# ---- late swap and 20-max from the Follow-up Report 10-25 ----
check("swap: good news does not reopen, bad news does",
      not LS.is_bad_news("projection up 20 to 27") and not LS.is_bad_news(
          "was on the bench at lock, now starting (projection +2%, minutes 1% moved)")
      and LS.is_bad_news("ruled out") and LS.is_bad_news("projection cut 30 to 20")
      and LS.is_bad_news("was starting at lock, now on the bench (projection -9%, minutes 9% moved)"))
_bm = _st("Moved Guy", 5000, 27.0, 5.0, starting=False); _bm.minutes = 28.0
_mv = []
_nw = LS.news_names([_bm], {"moved guy": {"proj": 28.0, "starting": True, "minutes": 29.0}},
                    set(), {"moved guy"}, _mv)
_mc = _st("Minutes Guy", 6000, 30.0, 5.0, starting=True); _mc.minutes = 24.0
check("swap news: a starter's minutes cut 25%+ and 5+ is news",
      "minutes guy" in LS.news_names([_mc], {"minutes guy": {"proj": 31.0, "starting": True,
                                                            "minutes": 33.0}}, set(), {"minutes guy"}))
check("swap news: a small minutes dip is not",
      not LS.news_names([_mc], {"minutes guy": {"proj": 31.0, "starting": True,
                                                "minutes": 27.0}}, set(), {"minutes guy"}))
check("swap: a move to the bench under 8% is a role change, not news",
      not _nw and _mv and _mv[0]["counted"] is False, str(_mv))
_bm.proj = 25.0
_nw = LS.news_names([_bm], {"moved guy": {"proj": 28.0, "starting": True, "minutes": 29.0}},
                    set(), {"moved guy"}, [])
check("swap: a move to the bench with the projection down 8%+ is news", "moved guy" in _nw)
check("swap: no-news swaps are on by default, at +14",
      s0["noNewsSwaps"] is True and s0["bars"]["noNews"] == LS.SWAP_DISC_GAIN)
_s6 = LS.run_late_swap(PROJ5, _mid, _stand, {"noNewsSwaps": "off"}, log_path=LS_LOG)
check("swap: no-news swaps can be switched off",
      _s6["noNewsSwaps"] is False and all(x["forced"] for x in _s6["swaps"] if not x["keep"]))
check("swap: each run saves its input files for replay",
      any(os.path.exists(os.path.join(_TMP, "swap_inputs", d, "dk.csv"))
          for d in os.listdir(os.path.join(_TMP, "swap_inputs"))))
_r20 = build(n=20, split=11)
check("20-max on 5 games: 15 ours / 5 Stokastic",
      sum(1 for l in _r20["lineups"] if l["source"] == "mine") == 15
      or not any(l["source"] == "vendor" for l in _r20["lineups"]),
      str(_co.Counter(l["source"] for l in _r20["lineups"])))
_pc20 = _co.Counter(p["name"] for l in _r20["lineups"] for p in l["players"])
_cap20 = [c for c in _r20["info"]["checks"] if "tier cap" in c]
check("20-max: the tier cap is on at 5 games and no player passes 10 of 20",
      _cap20 and max(_pc20.values()) <= 10, f"{_cap20} {_pc20.most_common(2)}")
check("grading logs each player's simulated range against what he scored",
      (g.get("ranges") or {}).get("players", 0) > 50
      and os.path.exists(os.path.join(_TMP, "nba_player_ranges.jsonl")), str(g.get("ranges")))
check("grading logs the build's bar against the real top-1% and top-0.1% lines",
      (g.get("lines") or {}).get("top1") is not None
      and os.path.exists(os.path.join(_TMP, "nba_bar_lines.jsonl")), str(g.get("lines")))
_rcore = build(cores="Nikola Jokić")
check("cores typed under the slate rule are read back, not '0 cores'",
      any("1 cores read, treated as pool players" in n["text"] for n in _rcore["notes"]))
check("grading tracks each team's starter and bench residuals",
      isinstance(g.get("teams"), list), str(g.get("teams"))[:120])


print("\n-- the slate decides (Thorough Review 10-27) --")
check("slate rules: 3 games off/off, 6 on/on, 11 neutral off with adds on",
      E.slate_rules(3) == {"neutral": False, "adds": False}
      and E.slate_rules(6) == {"neutral": True, "adds": True}
      and E.slate_rules(11) == {"neutral": False, "adds": True})
GAMES3 = GAMES5[:3]
R3 = slate(GAMES3, seed=3)
PROJ3, FIELD3, DK3 = proj_csv(R3), field_csv(R3), dk_classic(R3, GAMES3)
_pool3 = "\n".join(x["Player"] for i, x in enumerate(R3) if i % 2 == 0)
r3 = app.run_build(PROJ3, FIELD3, DK3, {"n": 40, "sims": 400, "seed": 0, "split": 40,
                                         "fieldCap": 47000, "pool": _pool3})
_l3 = [json.loads(l) for l in open(app.LOG_PATH, encoding="utf-8")][-1]["settings"]
check("3 games on auto: neutral off and no round-out adds, said in the check line",
      _l3["neutral"] is None and not (_l3["poolAdds"]["cheap"] or _l3["poolAdds"]["stud"])
      and "3 games: neutral off, round-out off" in r3["info"]["checks"],
      f"{r3['info']['checks']}")
check("slot 1 says what the slate will get",
      app._describe("proj", PROJ3)["msg"].endswith("→ neutral off, round-out off")
      and app._describe("proj", PROJ5)["msg"].endswith("→ neutral on, round-out on"))
r3h = app.run_build(PROJ3, FIELD3, DK3, {"n": 40, "sims": 400, "seed": 0, "split": 40,
                                          "fieldCap": 47000, "pool": _pool3,
                                          "neutral": "on", "poolAdds": "on"})
_l3h = [json.loads(l) for l in open(app.LOG_PATH, encoding="utf-8")][-1]["settings"]
check("on/off by hand still overrides, and the build says so",
      _l3h["neutral"] and _l3h["poolAdds"]["cheap"]
      and any("set by hand" in x["text"] for x in r3h["notes"]))
check("the log carries the slate rules, the arms as built and how each pick was filled",
      _l3["slateRules"]["games"] == 3 and sum(_l3["arms"].values()) == 40
      and sum(_l3["filledBy"].values()) == 40, f"{_l3['arms']} {_l3['filledBy']}")
_cpl = app._player_caps("Nikola Jokić 50\nDEN Player1 33%\nDEN Player2 200",
                        S.read_projections(PROJ5)[0], 150)
_jid = next(p.dk_id for p in pl if p.name == "Nikola Jokić")
check("caps: a bare number is lineups, % is a percentage, a count over the set is named back",
      abs(_cpl[0][_jid] - 50 / 150) < 1e-9 and 0.33 in _cpl[0].values()
      and _cpl[3] == ["DEN Player2 200"], str(_cpl))
_rc = build(capPlayers="Nikola Jokić 3")
check("caps: 'Jokic 3' holds him to 3 of 40 lineups",
      sum(1 for l in _rc["lineups"] for p in l["players"] if p["name"] == "Nikola Jokić") <= 3)
check("20-max on 5-7 games: nothing past the 1,000th candidate",
      all(l["depth"] <= app.SMALL_DEPTH or l["via"] in ("minimum", "deep")
          for l in _r20["lineups"]), str(sorted(l["depth"] for l in _r20["lineups"])[-3:]))
_c5 = C.build_candidates(S.read_projections(PROJ5)[0], 2000, rng=random.Random(1))
for _i, _lu in enumerate(_c5):
    _lu.metrics = {"score": 1.0 / (_i + 1)}
_p5 = E.neutral_plan(5)
_s3 = E.select_neutral(_c5, 3, _p5)
check("rotation with fewer lineups than games: no false 'could not fit'",
      _p5["short"] == 0 and len({E._main_games(l)[0] for l in _s3}) == 3,
      f"short {_p5['short']}")
_vf = [l for l in _c5[:50]]
for _lu in _vf:
    _lu.metrics["vdupes"] = 3.0
_va = E.vendor_arm(_vf, 5)
check("Stokastic's half gets the salary-left factor too",
      all(abs(l.metrics["dupes"] - round(E.expected_copies(4.0, 1.0)
                                         * E.leftover_factor(l), 2)) < 0.02 for l in _va))


print("\n-- bar field and ranges (Bar Field And Ranges Report 10-28) --")
from dataclasses import replace as _rep  # noqa: E402
from dk import Player as _PP  # noqa: E402


def _skew(row):
    m = sum(row) / len(row)
    sd = (sum((x - m) ** 2 for x in row) / len(row)) ** 0.5
    return sum(((x - m) / sd) ** 3 for x in row) / len(row)


_pz = [_PP("Big", "b1", "C", "AAA", "BBB", 8000, 30.0, 8.0, sd=8.0),
       _PP("Small", "s1", "PG", "AAA", "BBB", 3500, 5.0, 1.0, sd=6.0)]
_mz = E.simulate(_pz, sims=20000, seed=3)
check("ranges: 10+ projected draws are even (normal), not right-skewed",
      abs(_skew(_mz["b1"])) < 0.1, f"skew {_skew(_mz['b1']):.2f}")
check("ranges: the mean and SD still land on Stokastic's numbers",
      abs(sum(_mz["b1"]) / 20000 - 30.0) < 0.05
      and abs((sum((x - 30.0) ** 2 for x in _mz["b1"]) / 20000) ** 0.5 - 8.0) < 0.1)
check("ranges: under 10 projected keeps the right-skewed gamma",
      _skew(_mz["s1"]) > 1.0, f"skew {_skew(_mz['s1']):.2f}")

_fl, _ = app._field_lineups(fe, "classic")
_sh = E.field_shares(_fl)
_pb = [_rep(p, ownership=_sh.get(p.dk_id, 0.0)) for p in pl]
_wb, _rk = E.field_weights(_fl, _pb)
check("bar field: nobody 5+ points off, nothing is raked",
      _rk == [] and _wb == [1.0 + l.metrics.get("vdupes", 0.0) for l in _fl])
_mid_id = next(i for i, v in sorted(_sh.items(), key=lambda kv: -kv[1]) if 15 < v < 40)
_near_id = next(i for i, v in _sh.items() if i != _mid_id and 10 < v < 60)
_pc = [_rep(p, ownership=_sh[p.dk_id] + 12.0) if p.dk_id == _mid_id
       else _rep(p, ownership=_sh.get(p.dk_id, 0.0) + 3.0) if p.dk_id == _near_id
       else _rep(p, ownership=_sh.get(p.dk_id, 0.0)) for p in pl]
_wc, _rk = E.field_weights(_fl, _pc)
_after = E.field_shares(_fl, _wc)
check("bar field: a player 12 points light in the file is raked to his projected ownership",
      len(_rk) == 1 and _rk[0][0].dk_id == _mid_id
      and abs(_after[_mid_id] - (_sh[_mid_id] + 12.0)) < 0.5,
      f"{_sh[_mid_id]:.1f} -> {_after[_mid_id]:.1f}, target {_sh[_mid_id] + 12:.1f}")
check("bar field: a player only 3 points off is not a target",
      all(r[0].dk_id != _near_id for r in _rk))
_rb = app.run_build(PROJ5, FIELD5, DK5, {"n": 10, "sims": 200})
_lb = [json.loads(l) for l in open(app.LOG_PATH, encoding="utf-8")][-1]
_sb = [json.loads(l) for l in open(app._side_log("nba_slates.jsonl"), encoding="utf-8")][-1]
check("each player's share of Stokastic's lineups file is logged (build and slate logs)",
      all(p.get("file_share") is not None for p in _lb["players"])
      and all("file_share" in p for p in _sb["players"]))
check("ETR is gone: no slot, no reader",
      not hasattr(S, "read_etr") and "s_etr" not in open(
          os.path.join(os.path.dirname(app.__file__), "gui.py"), encoding="utf-8").read())

print("\n-- the DK file fills in lineups, ours and contest size --")
check("contest size from the name: $30K mini-MAX at $0.50 is ~71,300 (real 71,343)",
      abs(app.contest_size("NBA $30K mini-MAX [150 Entry Max]", "$0.50") - 71343) < 400,
      str(app.contest_size("NBA $30K mini-MAX [150 Entry Max]", "$0.50")))
check("contest size: $20K And-One at $1 ~23,800; $50K Four Point Play at $4 ~14,900",
      abs(app.contest_size("NBA $20K And-One", "$1") - 23781) < 150
      and abs(app.contest_size("NBA $50K Four Point Play [20 Entry Max]", "$4") - 14863) < 100)
check("contest size: an unreadable name gives 0, not a guess",
      app.contest_size("NBA Free Contest", "$0") == 0 and app.contest_size("", "") == 0)
check("ours: half of 150, 11 of a 20-max", app.auto_ours(150) == 75 and app.auto_ours(20) == 11)
_ra = app.run_build(PROJ5, FIELD5, DK5, {"n": "auto", "split": "auto", "fieldCap": "auto",
                                         "sims": 200})
_sa = [json.loads(l) for l in open(app.LOG_PATH, encoding="utf-8")][-1]["settings"]
check("blank boxes: lineups, ours and contest size all come from the DK file",
      not _ra.get("error") and _sa["n"] == 150 and _sa["split"] == 75
      and abs(_sa["fieldCap"] - 35688) < 200 and _sa["auto"] == {"n": True, "split": True,
                                                                   "size": True},
      f"{_sa['n']} {_sa['split']} {_sa['fieldCap']}")
check("the check line says it", any("from the DK file" in n["text"] for n in _ra["notes"]))
_d = app._describe("dk", DK5)
check("slot 3 shows the contest, its lineups and its size",
      _d["ok"] and _d["n"] == 150 and _d["size"] > 30000 and "lineups" in _d["msg"], _d["msg"])

# The summary and exit MUST stay last — appending a new section after them
# silently skips it and the suite still reports a clean pass.
print(f"\n{'=' * 56}\n  {ok} passed, {fail} failed\n{'=' * 56}")
sys.exit(1 if fail else 0)
