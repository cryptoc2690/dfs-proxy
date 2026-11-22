"""mark_records.py: the players behind each chalk-sheet mark, for the hover on the page.

For every saved night it rebuilds that night's chalk sheet with the tool's own `chalk_sheet()`
(so a mark here is exactly the mark the page would show), judged by the break-even table fitted
WITHOUT that night, and then looks up what each marked player scored in the box scores.

    python mark_records.py                       -> mark_records.json beside this file
    python mark_records.py --out x.json --lines all_lines.json --before 11-21

Runs on the tool's own modules. In the tool it needs no arguments: it reads `logs/espn/`,
`logs/needs_cache.json` (the fit's per-night cache, which the morning refit keeps current) and the
saved nights folder the fit reads. Outside the tool: NBA_TOOL (the folder with role.py, boxread.py,
newbuild.py, sources.py, chalk_sheet.py, fit_needs.py), NBA_ESPN, NBA_HISTORY, NBA_NEEDS_CACHE.

It takes a few seconds: nothing is fitted night by night except the table (a least-squares line
per band from the cached rows). A night missing from the cache is worked out once and kept in
memory; it is not written back to the fit's cache from here.

What a player scored is DraftKings' own figure from that night's mini-MAX standings where the fit
has it (the rows the fit keeps), else the box score. A player who did not play is listed and marked
"played": false, and is left out of every count.

Where it runs: beside chalk_sheet.py in the tool it uses the tool's modules. Anywhere else the
copies of chalk_sheet.py and fit_needs.py beside this file are used, and the rest (role, newbuild,
sources) come from NBA_TOOL (default ../tool1122). NBA_TOOL_FIRST=1 turns that round: every module
comes from NBA_TOOL where it has one.

The votes of the old rules are not built (history is passed empty): they are not part of any
mark's record.
"""
import datetime as dt
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.environ.get("NBA_TOOL") or (HERE if os.path.exists(os.path.join(HERE, "role.py")) else os.path.join(HERE, "..", "tool1122"))
TOOL = os.path.abspath(TOOL)
for _p in ((HERE, TOOL) if os.environ.get("NBA_TOOL_FIRST") else (TOOL, HERE)):     # the last one put in is looked in first
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import role as RL                              # noqa: E402
RL.FETCH = False
import newbuild as NB                          # noqa: E402
import sources as SRC                          # noqa: E402


def _load(name):
    """chalk_sheet and fit_needs by file, so the copy meant is the copy used: the one beside this
    file first (NBA_TOOL_FIRST=1: the tool's first). Both of them put NBA_TOOL at the front of the
    search path when it is set, which would otherwise pull the second one from the tool."""
    import importlib.util
    for folder in ((TOOL, HERE) if os.environ.get("NBA_TOOL_FIRST") else (HERE, TOOL)):
        path = os.path.join(folder, name + ".py")
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
            return mod
    raise ImportError(name)


CS = _load("chalk_sheet")
FN = _load("fit_needs")

HISTORY = os.environ.get("NBA_HISTORY") or FN.HISTORY_DIR
ESPN = os.environ.get("NBA_ESPN") or os.path.join(TOOL, "logs", "espn")
CACHE = os.environ.get("NBA_NEEDS_CACHE") or next(
    (p for p in (os.path.join(TOOL, "logs", "needs_cache.json"), os.path.join(HERE, "needs_cache.json")) if os.path.exists(p)), "")

WARNING_MARKS_NOT = ("cheap and clears", "clears by the most")     # as the page: every other mark is a warning
_CLOCK = re.compile(r"\d{1,2}\.\d{2}\s*(am|pm)|late swap", re.I)
_DATE = re.compile(r"(\d{1,2})-(\d{1,2})(?:\D|$)")


def pre_lock_nights(folder=None, before=None):
    """[(season key, 'M-D', projections path, mini-MAX standings path)], oldest first.

    As fit_needs.nights(), with one difference that matters: a file saved AFTER lock (a clock time
    or "late swap" in its name) is never taken while the night has another file. fit_needs.nights()
    takes "11-20 7.52pm late swap" for Nov 20, whose ownership is the field's after the news."""
    folder = folder or HISTORY
    cut = FN._season_key(*map(int, before.split("-"))) if before else None
    found = {}
    for pat, rank in (("NBA Stokastic Projections Main *.csv", 0), ("NBA Stokastic Data Hub Projections Main *.csv", 1)):
        for path in sorted(glob.glob(os.path.join(folder, pat))):
            tail = os.path.basename(path).split("Main", 1)[1]
            m = _DATE.search(tail)
            if not m:
                continue
            date = f"{int(m.group(1))}-{int(m.group(2))}"
            key = FN._season_key(int(m.group(1)), int(m.group(2)))
            if cut and key >= cut:
                continue
            rest = tail[m.end(2):].replace(".csv", "").strip()
            late = 1 if _CLOCK.search(rest) else 0
            pull = int(rest) if rest.isdigit() else 0
            pref = (late, rank, 0 if not rest else 1 if pull > 0 else 2, -pull, rest)
            st = os.path.join(folder, f"NBA Contest Standing Main mini-MAX {date}.csv")
            if os.path.exists(st) and (date not in found or pref < found[date][4]):
                found[date] = (key, date, path, st, pref)
    return [v[:4] for v in sorted(found.values())]


def _year_of(today):
    first = today.year if today.month >= 7 else today.year - 1
    return lambda m: first if m >= 7 else first + 1


def night_rows(nights, cache, year_of):
    """{date: the fit's rows for that night}: from the fit's cache where the cached night was made
    from the same projections file, else worked out here."""
    out = {}
    for _key, date, ppath, spath in nights:
        rec = cache.get(date)
        if rec and rec.get("stamp") and rec["stamp"][0] == os.path.basename(ppath):
            out[date] = rec["rows"]
        else:
            out[date] = FN.night_rows(date, ppath, spath, year_of)
    return out


def table_without(rows_by_night, date):
    rows = [r for d, v in rows_by_night.items() if d != date for r in v]
    even, _src, _cnt, big, _bign = FN.fit_tables(rows)
    others = [d for d in rows_by_night if d != date]
    return {"break_even_multiple": even, "big_game_multiple": big, "nights": len(others),
            "fitted_on": "each night left out", "last_night": others[-1] if others else None}


def night_lines(date, ppath, table, year_of, espn=None, dk_points=None):
    """Every line of that night's sheet (the tool's own chalk_sheet, the table as given), each with
    what he scored. dk_points: {name key: DraftKings' points} from the fit's rows. -> (games, [line dicts])."""
    espn = espn or ESPN
    m, d = map(int, date.split("-"))
    day = dt.date(year_of(m), m, d)
    text = open(ppath, encoding="utf-8-sig").read()
    players, _rep = SRC.read_projections(text)
    inj = NB.injury_tags(text)
    active = [p for p in players if NB.is_active(p, inj)]
    games = len({p.game for p in active if "@" in (p.game or "")})
    logs = RL.load_logs(day, folder=espn)
    S = CS.chalk_sheet(players, active, logs, [], games, {}, [], today=day, inj=inj, needs=table)
    after = RL.load_logs(day + dt.timedelta(days=1), folder=espn)
    G = after.get("games") or {}
    jk = RL._joiner(G)
    by_name = {p.name: p for p in players}
    have_box = any(g["date"] == day for gs in G.values() for g in gs[-3:])
    lines = []
    for x in S["lines"]:
        p = by_name.get(x["name"])
        g = [q for q in G.get(jk(p), []) if q["date"] == day] if p is not None else []
        k = FN.normalize_name(x["name"])
        played = bool(g) or bool(dk_points and k in dk_points)
        scored = (round(dk_points[k], 2) if dk_points and k in dk_points else round(g[-1]["dk"], 2) if g
                  else (0.0 if have_box else None))
        warn = [mk["mark"] for mk in x["marks"] if mk["mark"] not in WARNING_MARKS_NOT]
        suggested = any(t.get("suggested") for t in x.get("ticks") or [])
        lines.append({
            "night": date, "games": games, "player": x["name"], "team": x["team"], "price": x["price"],
            "projected_own": x["own"], "needs": x["break_even"], "big_game_score": x["big_game"],
            "projection": x["projection"], "gap": x["gap"],
            "gap_unrounded": (round(p.proj - CS.need(table, p.salary, p.ownership)[0], 3) if p is not None else x["gap"]),
            "starting": x["starting"],
            "nobody_out": x["nobody_out"], "who_was_out": list(x.get("out_on_team") or []),
            "scored": scored, "played": played, "reached_it": bool(played and scored >= x["break_even"]),
            "big_game": bool(played and scored >= x["big_game"]),
            "marks": [mk["mark"] for mk in x["marks"]], "cap_suggested": suggested,
            "group": ("cap suggested" if suggested else "warning mark, no cap suggested" if warn
                      else "under what he needs, no mark" if x["gap"] < 0 else "the rest of the chalk"),
            "why_on_sheet": x.get("why_on_sheet"),
        })
    return games, lines


def _tally(rows):
    """Counts over the players who played (a late scratch is listed, not counted)."""
    out = sum(1 for r in rows if not r["played"])
    rows = [r for r in rows if r["played"]]
    return {"players": len(rows), "nights": len({r["night"] for r in rows}),
            "reached_it": sum(1 for r in rows if r["reached_it"]), "big_game": sum(1 for r in rows if r["big_game"]),
            "did_not_play": out}


def record_text(m):
    """The line the sheet prints under a mark, made from the list, so the count on the page is the
    list on the hover."""
    n, r, b = m["players"], m["reached_it"], m["big_game"]
    if not n:
        return "no earlier player-nights"
    plural = lambda k, w: f"{k} {w}" + ("" if k == 1 else "s")
    txt = f"{plural(b, 'big game')} in {n}; {r} reached what he needed"
    two, rest = m["by_slate"]["2 game slates"], m["by_slate"]["3 or more games"]
    if two["players"] * 3 >= n:              # a third or more of the list is two-game nights: say what is left without them
        txt += (f" (outside two-game nights: {rest['players']} players, {rest['reached_it']} reached, "
                f"{plural(rest['big_game'], 'big game')})")
    return txt


def records_for_sheet(path=None):
    """{mark name: {"record": text, "players": [the list for the hover]}} from mark_records.json;
    {} when the file is not there (chalk_sheet's own strings then stay). chalk_sheet() calls this
    once and, for each mark on a line, takes the record and the list from it."""
    try:
        with open(path or os.path.join(HERE, "mark_records.json"), encoding="utf-8") as fh:
            M = json.load(fh)["marks"]
    except (OSError, ValueError, KeyError):
        return {}
    return {name: {"record": m.get("record_text") or record_text(m), "players": m["rows"]} for name, m in M.items()}


def build(before=None, today=None, espn=None, cache_path=None, history=None):
    today = today or dt.date.today()
    year_of = _year_of(today)
    nights = pre_lock_nights(history, before)
    try:
        cache = json.load(open(cache_path or CACHE, encoding="utf-8")) if (cache_path or CACHE) else {}
    except (OSError, ValueError):
        cache = {}
    rows_by = night_rows(nights, cache, year_of)
    all_lines = []
    for _key, date, ppath, _spath in nights:
        _g, lines = night_lines(date, ppath, table_without(rows_by, date), year_of, espn,
                                {r["key"]: r["real"] for r in rows_by.get(date) or []})
        all_lines += [x for x in lines if x["scored"] is not None]
    printed = {}
    for own, price, gap, starting, nobody in ((30, 6000, -2, True, False), (20, 5000, -1, True, False), (20, 6000, -1, False, False),
                                              (20, 5000, -0.2, True, True), (20, 5000, 0.5, True, True), (20, 5000, 3, True, False), (30, 6000, 7, True, False)):
        for mk in CS.player_marks(own, price, gap, starting, nobody):
            printed.setdefault(mk["mark"], {"rule": mk["rule"], "sheet_prints": mk["record"], "suggested_tick": mk["suggested_tick"]})
    marks = {}
    for name, info in printed.items():
        rows = [x for x in all_lines if name in x["marks"]]
        rec = dict(info, **_tally(rows))
        rec["by_price"] = {"under $5,500": _tally([x for x in rows if x["price"] < CS.CHEAP]), "$5,500 and up": _tally([x for x in rows if x["price"] >= CS.CHEAP])}
        rec["by_slate"] = {"2 game slates": _tally([x for x in rows if x["games"] <= 2]), "3 or more games": _tally([x for x in rows if x["games"] >= 3])}
        rec["record_text"] = record_text(rec)
        rec["rows"] = rows
        marks[name] = rec
    groups = {}
    for gname in ("cap suggested", "warning mark, no cap suggested", "under what he needs, no mark"):
        rows = [x for x in all_lines if x["group"] == gname and x["why_on_sheet"] != "an old rule votes on him"]
        groups[gname] = dict(_tally(rows), rows=rows)
    out = {"made": today.isoformat(), "nights": len(nights), "first_night": nights[0][1] if nights else None,
           "last_night": nights[-1][1] if nights else None,
           "how": "each night's sheet rebuilt by the tool's chalk_sheet() with the break-even table fitted without that night; scores from the box scores",
           "marks": marks, "groups": groups}
    return out, all_lines


if __name__ == "__main__":
    a = sys.argv[1:]
    opt = lambda k, dflt=None: a[a.index(k) + 1] if k in a else dflt
    today = dt.date.fromisoformat(opt("--today")) if opt("--today") else None
    out, lines = build(before=opt("--before"), today=today, espn=opt("--espn"), cache_path=opt("--cache"), history=opt("--history"))
    path = opt("--out", os.path.join(HERE, "mark_records.json"))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    if opt("--lines"):
        with open(opt("--lines"), "w", encoding="utf-8") as fh:
            json.dump(lines, fh)
    print(f"{out['nights']} nights ({out['first_night']} to {out['last_night']}) -> {path}")
    for name, m in out["marks"].items():
        print(f"  {name}: {m['record_text']}   [chalk_sheet.py prints: {m['sheet_prints']}]")
    for name, g in out["groups"].items():
        print(f"  group '{name}': {g['players']} players, {g['reached_it']} reached it, {g['big_game']} big games")
