"""What the late swap page shows at each wave: who got a start, who lost one, who is out.

Late Swap And The Top 10 review (Nov 21, 2026). Today the tool compares the fresh file with the
build's LINEUPS, so a new starter we did not hold has no line to compare against and is never
shown (Wolf and Graves on Nov 20). This compares the fresh file with the WHOLE file the build
read (`logs/inputs/<day>/<stamp>/proj.csv`, saved since Nov 7), and takes typed names for news
Stokastic's file does not show yet.

    news = see_news(players, lock_players, locked, rows, typed, logs, jk, needs)

players       the fresh pull, the tool's own `read_projections` list, DK ids and tips already set
lock_players  the build's projections file read the same way
locked        normalised names DK has locked (from the entries export)
rows          every entry: [{"entry_id", "contest_id", "lineup": [Player x8], "is_locked": [...]}]
typed         [{"name", "status": "starting" | "bench" | "out", "call": "viable" | "not playable" | ""}]
logs, jk      `role.load_logs(today)` and `role._joiner(logs["games"])`, for his earlier starts
needs         the chalk sheet's table (`chalk_sheet.load_needs()`), for what he needs at his price

Nothing here moves a lineup. `starters_for_step` turns ticked rows into the list `news_step` takes,
and `shadow` says what the step would do for every new starter, to be saved and graded.
"""

from __future__ import annotations

import copy

from dk import normalize_name

import news_swap as NS

TICK = {"share": 0.05, "min_gain": None, "never_out_own": 15.0}     # the settings a ticked new starter runs with
TICK_MATE = dict(TICK, never_out=10 ** 6)                            # a ticked teammate of a starter who is out: he may be a
                                                                     # stud, so a dear player may come out for him (never chalk)

# The automatic version (Quick Ask, Nov 21): no share and no count. He goes into every lineup where he is the better
# play, by the fresh file, than the lowest projection within $800 above his price; never for a player at $7,500+ or
# 15%+ own; the build's own limits kept (no two lineups share more than 5; the 60% cap on 5 to 7 games, 85% elsewhere).
AUTO = {"share": 1.0, "min_gain": 0.0, "never_out": 7500, "never_out_own": 15.0}

SHAPE_LINES = {"left": 300, "chalk": (-1, 3), "under10": 2, "min_own": 5.0, "sum_own": (0.8, 1.5)}   # T1's fixed lines


def shape_allow(lock_players, lines=None):
    """A check for `news_step(allow=...)`: the changed lineup must still pass the build's shape, with each player's
    ownership as the FRESH file has it. "A field lineup" is worked out from the build's file, as the build did.
    Use it on 5 to 7 game slates, where the build uses the shape. It is what stops a pile there."""
    L_ = dict(SHAPE_LINES, **(lines or {}))
    act = [p for p in lock_players if p.proj > 0]
    ech = sum(p.ownership / 100.0 for p in act if p.ownership >= 15)
    eso = sum(p.ownership ** 2 / 100.0 for p in act)

    def allow(lineup):
        o = [p.ownership for p in lineup]
        return (50000 - sum(p.salary for p in lineup) <= L_["left"]
                and L_["chalk"][0] <= sum(1 for x in o if x >= 15) - ech <= L_["chalk"][1]
                and sum(1 for x in o if x < 10) <= L_["under10"] and min(o) >= L_["min_own"]
                and L_["sum_own"][0] <= sum(o) / eso <= L_["sum_own"][1])
    return allow


def auto_step(rows, news, players, n_games, lock_players=None, settings=None):
    """The automatic late swap from the fresh file alone, one contest's rows at a time (the caller groups by contest).
    New starters the FILE shows (typed names are not used) and the best remaining starter of a team whose listed
    starter is out. `rows` are changed in place. -> (moves, log)."""
    st = dict(AUTO)
    st.update(settings or {})
    by = {normalize_name(p.name): p for p in players}
    allow = shape_allow(lock_players) if (lock_players and 5 <= n_games <= 7) else None
    who = []
    for r in news["newStarters"]:
        k = normalize_name(r["name"])
        if r["locked"] or "file" not in r["source"] or k not in by:
            continue
        who.append({"player": by[k], "proj": r["projNow"], "lost": None})
    for r in news.get("gainsFromOut", []):
        k = normalize_name(r["name"])
        if not r["locked"] and k in by and all(k != normalize_name(w["player"].name) for w in who):
            who.append({"player": by[k], "proj": r["projNow"], "lost": None})
    who.sort(key=lambda s: -(s["proj"] / max(1.0, s["player"].salary / 1000.0)))
    return NS.news_step(rows, who, n_games, settings=st, allow=allow)


SHADOW_VERSIONS = {
    # name: settings for news_step. Logged every run for every new starter; none of them moves a lineup.
    "bar": {"share": 0.05},                                   # in only where he out-projects the player he replaces
    "tick": {"share": 0.05, "min_gain": None, "never_out_own": 15.0},   # what a tick does: in whatever he projects,
                                                              # but never for a player at 15%+ projected own
    "pairBar": {"share": 0.05, "pair": True},                 # the pair, only where the two project at least as much
    "tickSuggested": {"share": 0.05, "min_gain": None, "never_out_own": 15.0, "only": "suggested"},   # the tick, only for
                                                              # starters whose read is at or over what they need
    "tickBehind": {"share": 0.05, "min_gain": None, "never_out_own": 15.0, "which": "behind"},   # the tick, the lineups
                                                              # standing lowest first (rows carry "standing")
    "flipBar": {"share": 0.05, "pair": True, "never_out": 10 ** 6},   # the same with no price line on who comes out:
                                                              # a stud may come out for him and the salary lift another spot
}


def _need(needs, salary, own):
    if not needs:
        return None, None
    import chalk_sheet
    be, big = chalk_sheet.need(needs, salary, own)
    return round(be, 1), round(big, 1)


def _chance(line, proj, sd):
    if line is None:
        return None
    import chalk_sheet
    return round(chalk_sheet.chance(line, proj, sd if sd and sd > 0 else proj / 4.0), 2)


def see_news(players, lock_players, locked, rows=None, typed=None, logs=None, jk=None, needs=None):
    """-> {"newStarters": [...], "lostStarts": [...], "outs": [...], "notes": [...]} in showing order."""
    now = {normalize_name(p.name): p for p in players}
    was = {normalize_name(p.name): p for p in lock_players}
    locked = set(locked or ())
    games = (logs or {}).get("games") or {}
    jk = jk or (lambda p: normalize_name(p.name))
    held = {}
    for r in rows or []:
        for p in r["lineup"]:
            d = held.setdefault(normalize_name(p.name), {})
            d[r.get("contest_id")] = d.get(r.get("contest_id"), 0) + 1
    typed_by = {normalize_name(t["name"]): t for t in typed or [] if t.get("name")}
    notes = [f"Typed name not on this slate: {t['name']}" for k, t in typed_by.items() if k not in now]

    def is_out(k):
        p = now.get(k)
        t = typed_by.get(k) or {}
        return t.get("status") == "out" or p is None or p.proj <= 0

    out = {"newStarters": [], "lostStarts": [], "outs": [], "notes": notes}
    team_was_starters = {}
    for k, b in was.items():
        if b.starting is True and b.proj > 0:
            team_was_starters.setdefault(b.team, []).append(k)

    for k, p in now.items():
        b = was.get(k)
        t = typed_by.get(k) or {}
        if b is None or b.proj <= 0:
            continue                                    # not an active player in the build's file
        base = {"name": p.name, "team": p.team, "tip": p.start, "price": p.salary, "locked": k in locked,
                "held": held.get(k, {}), "call": t.get("call") or "",
                "minutesWas": round(b.minutes or 0, 1), "minutesNow": round(p.minutes or 0, 1),
                "projWas": round(b.proj, 1), "projNow": round(p.proj, 1),
                "ownWas": round(b.ownership, 2), "ownNow": round(p.ownership, 2)}
        if is_out(k):
            if b.proj < 5 and k not in held:
                continue                                # a deep-bench scratch nobody holds is not shown
            out["outs"].append(dict(base, source="typed" if t.get("status") == "out" and p.proj > 0 else "file"))
            continue
        file_start = b.starting is False and p.starting is True
        typed_start = t.get("status") == "starting" and b.starting is not True
        file_bench = b.starting is True and p.starting is False
        typed_bench = t.get("status") == "bench" and b.starting is True
        if file_start or typed_start:
            g = games.get(jk(p), [])
            pts, mins, how = NS.starter_read(b.minutes, g)
            starts = [x for x in g if x.get("starter")]
            if file_start:
                used, used_how = p.proj, "Stokastic's projection now"
            elif pts is not None:
                used, used_how = max(pts, b.proj), how
            else:
                used = b.proj * ((b.minutes or 0) + 5.0) / max(1.0, b.minutes or 0)
                used_how = "file projection scaled to file minutes + 5 (under 3 games on record)"
            lost = [x for x in team_was_starters.get(p.team, [])
                    if is_out(x) or (now.get(x) is not None and now[x].starting is False)
                    or (typed_by.get(x) or {}).get("status") == "bench"]
            a_reg_out = any(is_out(x) for x in lost)
            be, big = _need(needs, p.salary, b.ownership)
            row = dict(base, source="file" if file_start and not typed_start else "typed" if not file_start else "file and typed",
                       read={"points": pts, "minutes": mins, "how": how},
                       projUsed=round(used, 2), projUsedFrom=used_how,
                       starts=len(starts), startMinutes=[x["mins"] for x in starts[-5:]],
                       startPoints=[x["dk"] for x in starts[-5:]],
                       replaced=[was[x].name for x in lost],
                       why="a regular out" if a_reg_out else "a coach's choice" if lost else "not known yet",
                       needs=be, bigGame=big,
                       chanceNeeds=_chance(be, used, p.sd), chanceBigGame=_chance(big, used, p.sd),
                       # the one read that kept the tick's gain with half the swaps: his box-score read as a starter is at
                       # or over what he needs at his price. A suggestion only; nothing starts ticked.
                       readOverNeeds=(None if pts is None or be is None else round(pts - be, 1)),
                       suggestTick=bool(pts is not None and be is not None and pts >= be and k not in locked))
            out["newStarters"].append(row)
        elif file_bench or typed_bench:
            out["lostStarts"].append(dict(base, source="file" if file_bench else "typed"))
    # When a listed starter is out: the teammate who gains. On the saved nights the team's highest-projected remaining
    # listed starter reached his big-game line 4 times in 13; the other starters 6 in 37.
    out["gainsFromOut"] = []
    seen = set()
    for r in out["outs"]:
        k = normalize_name(r["name"])
        b = was.get(k)
        if b is None or b.starting is not True or b.team in seen:
            continue
        mates = [now[x] for x in team_was_starters.get(b.team, []) if x != k and not is_out(x) and x in now]
        if not mates:
            continue
        seen.add(b.team)
        m = max(mates, key=lambda q: q.proj)
        mk = normalize_name(m.name)
        be, big = _need(needs, m.salary, was[mk].ownership if mk in was else m.ownership)
        out["gainsFromOut"].append({"name": m.name, "team": m.team, "tip": m.start, "price": m.salary, "locked": mk in locked,
                                    "because": r["name"], "projWas": round(was[mk].proj, 1) if mk in was else None,
                                    "projNow": round(m.proj, 1), "ownWas": round(was[mk].ownership, 2) if mk in was else None,
                                    "held": held.get(mk, {}), "needs": be, "bigGame": big,
                                    "chanceBigGame": _chance(big, m.proj, m.sd)})
    for k in ("newStarters", "lostStarts", "outs", "gainsFromOut"):
        out[k].sort(key=lambda r: (r["locked"], r["tip"] or "", -r["projNow"]))
    return out


def starters_for_step(news, players, ticked=None):
    """The list `news_step` takes: every new starter whose game has not tipped, or only the ticked names.
    Best value first (projection used per $1,000)."""
    by = {normalize_name(p.name): p for p in players}
    tick = None if ticked is None else {normalize_name(n) for n in ticked}
    rows = []
    for r in news["newStarters"]:
        k = normalize_name(r["name"])
        if r["locked"] or k not in by or (tick is not None and k not in tick):
            continue
        lost = [by[normalize_name(n)] for n in r["replaced"] if normalize_name(n) in by]
        rows.append({"player": by[k], "proj": r["projUsed"], "lost": lost[0] if len(lost) == 1 else None})
    rows.sort(key=lambda s: -(s["proj"] / max(1.0, s["player"].salary / 1000.0)))
    return rows


def mates_for_step(news, players, ticked):
    """The list `news_step` takes for ticked teammates of a starter who is out (run with TICK_MATE)."""
    by = {normalize_name(p.name): p for p in players}
    tick = {normalize_name(n) for n in ticked or ()}
    rows = [{"player": by[normalize_name(r["name"])], "proj": r["projNow"], "lost": None}
            for r in news.get("gainsFromOut", []) if not r["locked"] and normalize_name(r["name"]) in tick and normalize_name(r["name"]) in by]
    return sorted(rows, key=lambda s: -(s["proj"] / max(1.0, s["player"].salary / 1000.0)))


def upgrades_for(players, locked, pool_names, news):
    """For the pair: your pool, not locked, not out, best projection first."""
    out_names = {normalize_name(r["name"]) for r in news["outs"]}
    pool = {normalize_name(n) for n in pool_names or ()}
    ok = [p for p in players if p.proj > 0 and normalize_name(p.name) not in locked
          and normalize_name(p.name) not in out_names and (not pool or normalize_name(p.name) in pool)]
    return sorted(ok, key=lambda p: -p.proj)


def _copy_rows(rows):
    return [dict(r, lineup=list(r["lineup"]), is_locked=list(r["is_locked"])) for r in rows]


def shadow(rows, news, players, n_games, upgrades=None, versions=None):
    """What the step would do for EVERY new starter, one contest at a time, on copies.
    -> {contest_id: {version: {"moves": [...], "log": [...]}}}. Save it with the swap's log row."""
    starters = starters_for_step(news, players)
    by_contest = {}
    for r in rows:
        by_contest.setdefault(r.get("contest_id"), []).append(r)
    res = {}
    for cid, rs in by_contest.items():
        res[cid] = {}
        for name, st in (versions or SHADOW_VERSIONS).items():
            st = dict(st)
            who = starters
            if st.pop("only", None) == "suggested":
                sug = {normalize_name(r["name"]) for r in news["newStarters"] if r.get("suggestTick")}
                who = [x for x in starters if normalize_name(x["player"].name) in sug]
            mv, lg = NS.news_step(_copy_rows(rs), copy.copy(who), n_games, settings=st,
                                  upgrades=upgrades if st.get("pair") else None)
            res[cid][name] = {"moves": [{k: m[k] for k in ("entry_id", "out", "in", "gain")} for m in mv], "log": lg}
        mates = mates_for_step(news, players, [r["name"] for r in news.get("gainsFromOut", [])])
        mv, lg = NS.news_step(_copy_rows(rs), mates, n_games, settings=TICK_MATE)
        res[cid]["mateOfOut"] = {"moves": [{k: m[k] for k in ("entry_id", "out", "in", "gain")} for m in mv], "log": lg}
    return res
