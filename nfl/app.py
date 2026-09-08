"""NFL — server, builder and DK upload writer, for both DraftKings formats.

Showdown (1 CPT + 5 FLEX from one game) and Classic (the Sunday main slate,
QB/RB/RB/WR/WR/WR/TE/FLEX/DST) share this file, the simulator, the readers and
the page; the roster rules, construction and selection live in `engine.py` and
`classic.py` respectively. Which one a build is comes from the files, not from
the user: the DK entries export names its roster slots in the header, and
failing that a showdown board is one game against a main slate's twelve. Getting
that wrong is not a subtle bug — it is a six-player lineup written into nine
columns — so it is decided once, said out loud, and passed down.

Run it with no arguments and it opens the drop-your-files page, the same way
the WNBA tool does:

    python3 nfl/app.py

Or drive it from the command line for scripted runs:

    python3 nfl/app.py --proj proj.csv --field lineups.csv --dk DKEntries.csv \
        --n 150 --split 75 --out upload.csv

Only the projections file is strictly required. Without the DK entries export
there is no uploadable file — that export is the only thing carrying your Entry
IDs and DK's per-slot player IDs. Without the vendor lineup pool you lose the
opponent set and the duplication model, which is the one component that cannot
be built with no results history.

`split` runs the A/B: that many entries from our builder, the rest re-ranked out
of the vendor pool, tagged in the log. Splitting inside ONE contest is the only
design that removes slate luck from the comparison.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import classic as C
import engine as E
from dk import SALARY_CAP, normalize_name
from gui import INDEX_HTML
from sources import read_dk_entries, read_field, read_projections, read_sharp

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "logs", "nfl_builds.jsonl")
PORT = int(os.environ.get("PORT", "8010"))


def _read(path):
    if not path:
        return ""
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _i(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def _pool_gaps(players):
    """-> (off-pool players a legal classic roster needs, which seats are short)

    Classic needs a QB, two RBs, three WRs, a TE, a flex and a DST out of the
    same sheet. A pool that cannot cover all nine is not a constraint, it is a
    build that returns nothing — and the failure is silent and slow, because the
    builder spends its whole try budget rejecting rosters it can never complete.
    So work out up front exactly how many seats the sheet cannot fill, and say
    which ones.
    """
    have = {}
    for p in players:
        if p.in_pool and p.proj > 0 and p.salary > 0:
            have[p.pos] = have.get(p.pos, 0) + 1
    short = [f"{need} {pos}" + ("s" if need > 1 else "")
             for pos, need in (("QB", 1), ("RB", 2), ("WR", 3),
                               ("TE", 1), ("DST", 1))
             if have.get(pos, 0) < need]
    # The most in-pool players a legal roster could hold: each position capped at
    # its maximum, and the RB/WR/TE seats capped at seven between them.
    flexish = min(min(have.get("RB", 0), 3) + min(have.get("WR", 0), 4)
                  + min(have.get("TE", 0), 2), 7)
    usable = min(have.get("QB", 0), 1) + min(have.get("DST", 0), 1) + flexish
    return max(0, C.ROSTER_SIZE - usable), short


def _stack_targets(raw):
    """'3:45,2:40,1:15' -> {3: 0.45, 2: 0.40, 1: 0.15}.

    Weights are normalised to sum to one, so the page can send whole percents
    and the command line can send fractions and both mean the same thing. Left
    un-normalised, "45" would ask for 45 x 150 lineups at that depth.
    """
    out = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[int(k)] = float(v)
            except (TypeError, ValueError):
                return dict(C.STACK_TARGETS)
    else:
        for part in str(raw or "").replace(";", ",").split(","):
            if ":" not in part:
                continue
            d, w = part.split(":", 1)
            try:
                out[int(d.strip())] = float(w.strip())
            except ValueError:
                continue
    out = {d: w for d, w in out.items() if w > 0}
    total = sum(out.values())
    if not out or total <= 0:
        return dict(C.STACK_TARGETS)
    return {d: w / total for d, w in out.items()}


def _attach_dk_ids(players, dk):
    """Swap in DK's real player IDs.

    Showdown lists every player twice under two different ids — once as CPT at
    1.5x salary, once as FLEX — so both are carried and the writer picks by
    slot. A flex id in the captain cell is a file DK will not accept.
    """
    if not dk or not dk.get("pool"):
        return 0, [], []
    hit, miss, no_cpt = 0, [], []
    for p in players:
        rec = dk["pool"].get(normalize_name(p.name))
        if rec:
            p.dk_id = rec["dk_id"] or p.dk_id
            p.cpt_dk_id = rec["cpt_dk_id"]
            if not p.cpt_dk_id and p.proj > 0:
                no_cpt.append(p.name)
            hit += 1
        elif p.proj > 0:
            miss.append(p.name)
    return hit, miss, no_cpt


def _roster(lu, fmt):
    """The lineup's players in the order DK's columns expect them."""
    if fmt == "showdown":
        return [lu.cpt] + lu.flex
    return lu.slots()          # QB, RB, RB, WR, WR, WR, TE, FLEX, DST


def _dk_rows(entries, lineups, header, fmt):
    lines = [header]
    for e, lu in zip(entries, lineups):
        ps = _roster(lu, fmt)
        cells = [f'"{p.name.strip()} ({p.upload_id(i == 0) if fmt == "showdown" else p.dk_id})"'
                 for i, p in enumerate(ps)]
        cname = (e["contest"] or "").replace('"', '""')
        lines.append(f'{e["entry_id"]},"{cname}",{e["contest_id"]},{e["fee"]},'
                     + ",".join(cells))
    return "\n".join(lines) + "\n"


def _log(lineups, meta):
    """One JSON line per ENTRY, not per build.

    The source arm is what makes the comparison possible; without it recorded at
    build time there is no way to answer whether the custom builder earned its
    complexity. The structure fields are logged so a later review can test the
    mechanism — was it the lopsided splits? — and not just the outcome.
    """
    fmt = meta.get("format", "showdown")
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        ts = datetime.now().astimezone().isoformat(timespec="seconds")
        ids = meta.get("entry_ids") or []
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            for i, lu in enumerate(lineups):
                ps = _roster(lu, fmt)
                row = {
                    "ts": ts, "slate": meta.get("slate"), "format": fmt,
                    "contest_id": meta.get("contest_id"),
                    "entry_id": ids[i] if i < len(ids) else None,
                    "source": lu.source,
                    "players": [{"name": p.name, "id": p.dk_id, "pos": p.pos,
                                 "team": p.team, "salary": p.salary,
                                 "proj": p.proj, "own": p.ownership,
                                 "boom": p.boom, "core": p.core,
                                 "pool": p.in_pool}
                                for p in ps if p is not None],
                    "salary": lu.salary, "leftover": SALARY_CAP - lu.salary,
                    "proj": lu.proj, "own_sum": lu.own_sum,
                    "has_dst": any(p.is_dst for p in lu.players),
                    "metrics": lu.metrics,
                    "settings": meta.get("settings", {}),
                    "contest_state": meta.get("contest_state", {}),
                }
                if fmt == "showdown":
                    row.update({"captain": lu.cpt.name,
                                "captain_id": lu.cpt.dk_id,
                                "split": lu.split_label(),
                                "major_team": lu.major_team()})
                else:
                    # The structure fields a later review needs to test the
                    # MECHANISM — was it the stack depth? — and not just the
                    # outcome. Stack depth is to classic what the split is to
                    # showdown, so it is logged with the same care.
                    q = lu.qb()
                    row.update({"qb": q.name if q else None,
                                "qb_id": q.dk_id if q else None,
                                "qb_team": q.team if q else None,
                                "stack": lu.stack_depth(),
                                "bring_back": lu.bring_back(),
                                "shape": lu.stack_label()})
                fh.write(json.dumps(row) + "\n")
    except Exception as exc:                     # never let logging break a build
        print(f"  ! log write failed: {exc}", file=sys.stderr)


def _lineup_payload(lu, fmt="showdown"):
    if fmt == "showdown":
        players = [{"slot": "CPT" if i == 0 else "FLEX", "name": p.name.strip(),
                    "team": p.team, "pos": p.pos,
                    "salary": p.cpt_salary() if i == 0 else p.salary,
                    "proj": round(p.proj * (1.5 if i == 0 else 1.0), 1),
                    "own": round(p.cpt_own if i == 0 else p.ownership, 1),
                    "core": p.core, "pool": p.in_pool}
                   for i, p in enumerate([lu.cpt] + lu.flex)]
        shape = lu.split_label()
    else:
        players = [{"slot": s, "name": p.name.strip(), "team": p.team,
                    "pos": p.pos, "salary": p.salary, "proj": round(p.proj, 1),
                    "own": round(p.ownership, 1), "core": p.core,
                    "pool": p.in_pool}
                   for s, p in zip(C.ROSTER, lu.slots()) if p is not None]
        shape = lu.stack_label()
    return {
        "source": lu.source, "split": shape,
        "salary": lu.salary, "leftover": SALARY_CAP - lu.salary,
        "proj": lu.proj, "ownSum": lu.own_sum,
        "win": round(lu.metrics.get("win", 0.0), 5),
        "dupes": round(lu.metrics.get("dupes", 0.0), 2),
        "players": players,
    }


def run_build(proj_text, field_text="", dk_text="", options=None):
    """The whole build. Returns plain dicts, so the CLI and the page share it."""
    o = options or {}
    notes = []

    def say(kind, text):
        notes.append({"type": kind, "text": text})

    players, rep = read_projections(proj_text or "")
    if rep.get("error"):
        return {"error": f"Projections file: {rep['error']}",
                "headers": rep.get("headers")}
    say("info", f"Projections: {rep['players']} players read. Columns matched — "
                + ", ".join(f"{k} from “{v}”" for k, v in rep["matched"].items()))
    if rep["unmatched"]:
        say("info", "Not present, so skipped: " + ", ".join(rep["unmatched"]))

    teams = sorted({p.team for p in players if p.team})
    if len(teams) == 2:
        for p in players:
            if not p.opponent and p.team:
                p.opponent = teams[1] if p.team == teams[0] else teams[0]

    dk = read_dk_entries(dk_text) if (dk_text or "").strip() else None

    # Which game is this? A showdown board is one game, two teams, and DK heads
    # its first roster column CPT; a main slate is a dozen games and starts at
    # QB. Both files say so independently, so ask whichever is present rather
    # than making the user pick — a wrong answer here is not a subtle bug, it is
    # a six-player lineup uploaded into nine slots.
    fmt = o.get("format") or ""
    why = "you chose it"
    if fmt not in ("showdown", "classic"):
        if dk and dk.get("slots"):
            fmt = "showdown" if dk["slots"][0].strip().upper() == "CPT" else "classic"
            why = f"the DK file's slots are {', '.join(dk['slots'])}"
        else:
            fmt = "showdown" if len(teams) <= 2 else "classic"
            why = f"the projections cover {len(teams)} teams"
    M = E if fmt == "showdown" else C
    say("info", f"Reading this as a {'SHOWDOWN' if fmt == 'showdown' else 'MAIN SLATE (classic)'} "
                f"build — {why}.")
    if fmt == "showdown" and len(teams) != 2:
        say("warn", f"Expected exactly two teams for a showdown, found {teams}.")
    if fmt == "classic" and len(teams) < 4:
        say("warn", f"Only {len(teams)} teams on a main-slate build — check the "
                    f"projections file covers the whole slate.")

    if dk:
        hit, miss, no_cpt = _attach_dk_ids(players, dk)
        say("good", f"DK entries: {len(dk['entries'])} entries, {hit} player IDs matched.")
        if miss:
            say("warn", f"{len(miss)} projected players had no DK match by name: "
                        + ", ".join(miss[:8]) + (" …" if len(miss) > 8 else ""))
        if no_cpt and fmt == "showdown":
            say("warn", "No CAPTAIN id for " + ", ".join(no_cpt[:6])
                        + " — they cannot be captained in the upload file.")

    by_id = {p.dk_id: p for p in players}
    by_name = {normalize_name(p.name): p for p in players}
    field, frep = [], {}
    if (field_text or "").strip():
        field, frep = read_field(field_text, by_id=by_id, by_name=by_name)
        if frep.get("error"):
            say("warn", f"Vendor lineup file: {frep['error']}")
        elif frep.get("format") != fmt:
            say("warn", f"That lineup file is a {frep['format']} pool but this is "
                        f"a {fmt} build — ignoring it. Download the lineups for "
                        f"the right slate.")
            field = []
        else:
            say("good", f"Vendor pool: {frep['parsed']:,} of {frep['rows']:,} "
                        f"lineups read as the opponent field.")
            if frep.get("unresolved_rosters"):
                say("warn", f"{frep['unresolved_rosters']:,} of those lineups held "
                            f"a player the projections file does not list, so they "
                            f"were skipped.")

    pool_names = read_sharp(o.get("pool") or "")
    core_names = read_sharp(o.get("cores") or "")
    for p in players:
        n = normalize_name(p.name)
        p.core = n in core_names
        p.in_pool = p.core or (n in pool_names)
    if pool_names or core_names:
        say("info", f"Sharp's sheet: {sum(1 for p in players if p.core)} cores, "
                    f"{sum(1 for p in players if p.in_pool)} in pool.")

    # The arithmetic check for the per-slot ownership trap. Showdown reports
    # ownership per ROSTER SLOT across two columns on different denominators:
    # flex sums to 500% (five slots), captain to 100% (one). Neither one is
    # "total ownership", and reading the wrong one silently corrupts every
    # leverage and duplication figure — so this runs on every build.
    flex_own = sum(p.ownership for p in players)
    cpt_own = sum(p.cpt_own for p in players)
    if fmt == "showdown":
        ok_own = 450 <= flex_own <= 650 and 550 <= flex_own + cpt_own <= 650
        say("good" if ok_own else "warn",
            f"Ownership: {flex_own:.0f}% across the five flex slots + {cpt_own:.0f}% "
            f"captain = {flex_own + cpt_own:.0f}% over six slots."
            + ("" if ok_own else " Expected about 500 + 100. Check which ownership "
                                 "column was read — they are different quantities."))
    else:
        # Classic has one ownership column and nine roster slots, so it must sum
        # to 900. Anything else means the wrong column was matched, and every
        # leverage and duplication figure downstream would be quietly wrong.
        ok_own = 800 <= flex_own <= 1000
        say("good" if ok_own else "warn",
            f"Ownership: {flex_own:.0f}% across nine roster slots."
            + ("" if ok_own else " Expected about 900. Check which ownership "
                                 "column was read."))

    sims = _i(o.get("sims"), 4000)
    seed = _i(o.get("seed"), 0)
    n = max(1, _i(o.get("n"), 150))
    split = _i(o.get("split"), 0)

    mat = E.simulate(players, sims=sims, seed=seed)
    bar, sampled = M.field_bar(field, mat, sims, seed=seed) if field else (None, 0)
    idx = M.dupe_index(field) if field else {}
    modelled = M.field_size(field) if field else 0
    expect = _i(o.get("expectEntries"), 0)
    field_cap = _i(o.get("fieldCap"), 0)
    dupe_scale = max(1.0, expect / modelled) if (modelled and expect) else 1.0

    if bar:
        srt = sorted(bar)
        say("info", f"Score to beat, from {sampled:,} sampled opponents: "
                    f"{srt[len(srt) // 2]:.0f} median.")
        say("info", f"Vendor pool models {modelled:,.0f} opponent entries in "
                    f"{len(idx):,} distinct lineups.")
        if expect:
            say("info", f"Scaling duplication ×{dupe_scale:.2f} for an expected "
                        f"{expect:,}-entry field.")
        elif field_cap and modelled < field_cap * 0.9:
            say("warn", f"This contest holds up to {field_cap:,} but the vendor "
                        f"pool models {modelled:,.0f}. If it fills past that, "
                        f"duplication below is understated — put your read of "
                        f"the final field size in “expected entries”.")
    else:
        say("warn", "No vendor lineup file, so there is no opponent field: "
                    "lineups are ranked on simulated score alone, with no win "
                    "rate and no real duplication estimate.")

    # The sharp's pool is a real build constraint when one is given. On a ~40
    # player showdown board a hard filter is defensible; the brief argued
    # against it only for main slates, where intersecting a 25-name sheet with a
    # 300-player board over-constrains badly.
    raw_off = o.get("maxOffPool")
    if not pool_names:
        off_pool = None
    elif raw_off in (None, "", "off"):
        off_pool = 0
    else:
        off_pool = _i(raw_off, 0)
    if pool_names and off_pool == 0 and fmt == "classic":
        need_off, short = _pool_gaps(players)
        if need_off:
            # A sheet that cannot fill nine seats is a shortlist, not a build
            # constraint, and any partial allowance would be a number invented
            # here rather than one the sharp meant. Drop the constraint and say
            # so — the cores keep their guaranteed floors, which is where the
            # conviction actually lives.
            off_pool = None
            say("warn", (("Your pool is short of " + ", ".join(short) + ", so ")
                         if short else "Your pool cannot fill all nine seats, so ")
                        + f"it only covers {C.ROSTER_SIZE - need_off} of "
                          f"{C.ROSTER_SIZE}. Treating it as a shortlist rather "
                          f"than a hard filter. Your cores still get their "
                          f"guaranteed share.")
    if pool_names and off_pool is not None:
        say("info", "Pool is a build constraint: "
                    + ("every player must come from it."
                       if not off_pool else
                       f"up to {off_pool} off-pool player(s) per lineup."))

    n_mine = n if split <= 0 else min(split, n)
    n_vendor = n - n_mine
    chosen = []
    shape_targets = (E.SPLIT_TARGETS if fmt == "showdown" else
                     _stack_targets(o.get("stackTargets")))
    caps = dict(player_cap=_f(o.get("playerCap"), M.PLAYER_CAP))
    if fmt == "showdown":
        caps.update(captain_cap=_f(o.get("captainCap"), E.CAPTAIN_CAP),
                    min_captains=_i(o.get("minCaptains"), E.MIN_CAPTAINS))
    else:
        caps.update(qb_cap=_f(o.get("qbCap"), C.QB_CAP),
                    dst_cap=_f(o.get("dstCap"), C.DST_CAP))

    if n_mine:
        rng = __import__("random").Random(seed)
        common = dict(rng=rng, max_off_pool=off_pool,
                      max_leftover=_i(o.get("maxLeftover"), M.MAX_LEFTOVER),
                      min_proj=_f(o.get("minProj"), M.MIN_PROJ))
        if fmt == "showdown":
            cands = E.build_candidates(players, max(4000, n_mine * 30),
                                       teams=teams, **common)
        else:
            cands = C.build_candidates(
                players, max(4000, n_mine * 30), stack_targets=shape_targets,
                bring_back_share=_f(o.get("bringBack"), C.BRING_BACK_SHARE),
                **common)
        if not cands:
            return {"error": "Built no legal lineups. Check the salaries, "
                             "positions and teams in the projections file"
                             + (" — and whether your pool can cover all nine "
                                "roster slots." if pool_names else "."),
                    "notes": notes}
        dupe_kw = {} if fmt == "showdown" else {"field_n": modelled}
        if bar:
            M.rank(cands, mat, bar, sims, idx,
                   own_lean=_f(o.get("ownLean"), M.OWN_LEAN),
                   dupe_scale=dupe_scale, **dupe_kw)
        else:
            for lu in cands:
                sc = M.score_lineup(lu, mat, sims)
                d = M.estimated_dupes(lu, idx, scale=dupe_scale, **dupe_kw)
                lu.metrics = {"mean": round(sum(sc) / sims, 2), "dupes": round(d, 2)}
                lu.metrics["score"] = lu.metrics["mean"] / (1 + d)
            cands.sort(key=lambda l: -l.metrics["score"])
        # Every core the sharp set is guaranteed a share of the entries, so a
        # conviction pick cannot be squeezed out by the tool's own preferences.
        core_ids = [p.dk_id for p in players if p.core and p.proj > 0]
        floors = None
        if core_ids:
            per = max(1, -(-n_mine // (len(core_ids) + 1)))
            floors = {cid: per for cid in core_ids}
            say("info", f"Each of your {len(core_ids)} core(s) is guaranteed at "
                        f"least {per} of {n_mine} lineups.")
        if fmt == "showdown":
            chosen += E.select(cands, n_mine, split_targets=shape_targets,
                               core_floors=floors, **caps)
        else:
            chosen += C.select(cands, n_mine, stack_targets=shape_targets,
                               core_floors=floors, **caps)
        say("info", f"Built {len(chosen)} lineups from {len(cands):,} candidates.")
        # Did the floors actually hold? Never assume they did. The one failure
        # this tool has already been bitten by is a core landing in a fraction of
        # the entries the user was told it would, and it fails QUIETLY: the
        # top-up loop stops when no legal swap exists and says nothing. So check
        # the result, name the player, and say how short it came.
        if floors:
            short = []
            for p in players:
                if not p.core or p.proj <= 0:
                    continue
                got = sum(1 for lu in chosen if p.dk_id in lu.ids())
                if got < floors.get(p.dk_id, 0):
                    short.append(f"{p.name.strip()} in {got}, not {floors[p.dk_id]}")
            if short:
                say("warn", "A core could not be given its full share — "
                            + "; ".join(short) + ". Usually the pool, the salary "
                            "cap or the stack rules leave nowhere legal to put "
                            "them.")

    if n_vendor:
        if not field:
            say("warn", "The vendor half of the split needs the vendor lineup "
                        "file; skipping it.")
        elif fmt == "showdown":
            chosen += E.vendor_arm(field, n_vendor, players_by_id=by_id,
                                   dupe_scale=dupe_scale, **caps)
        else:
            chosen += C.vendor_arm(field, n_vendor, dupe_scale=dupe_scale, **caps)

    if not chosen:
        return {"error": "No lineups produced.", "notes": notes}

    # Where these entries sit against the actual opponents, on the two axes the
    # brief says decide a main slate. Ownership is the least-trusted finding in
    # the whole report, so the honest thing is to SHOW the size of the bet being
    # made rather than bake a direction in and stay quiet about it.
    if field:
        f_own = sorted(sum(p.ownership for p in (e.get("flex") or []))
                       for e in field)
        if f_own:
            mine_own = sum(lu.own_sum for lu in chosen) / len(chosen)
            pct = 100.0 * sum(1 for v in f_own if v < mine_own) / len(f_own)
            say("info" if 10 <= pct <= 90 else "warn",
                f"Ownership: your lineups average {mine_own:.0f}% against the "
                f"field's {f_own[len(f_own) // 2]:.0f}% median, which is higher "
                f"than {pct:.0f}% of the field."
                + ("" if 10 <= pct <= 90 else
                   " That is a large bet on one direction; the ownership lean "
                   "setting is what moves it."))
    if fmt == "classic" and field:
        def _depth(ps):
            q = next((p for p in ps if p.is_qb), None)
            return 0 if not q else sum(
                1 for p in ps if p is not q and p.team == q.team
                and p.pos in ("WR", "TE"))
        fd, n_f = {}, 0
        for e in field:
            ps = e.get("flex") or []
            if len(ps) == C.ROSTER_SIZE:
                fd[_depth(ps)] = fd.get(_depth(ps), 0) + 1
                n_f += 1
        if n_f:
            mine = {}
            for lu in chosen:
                mine[lu.stack_depth()] = mine.get(lu.stack_depth(), 0) + 1
            say("info", "Stack depth, yours vs the field: " + "; ".join(
                f"QB+{d} {100.0 * mine.get(d, 0) / len(chosen):.0f}% "
                f"vs {100.0 * fd.get(d, 0) / n_f:.0f}%"
                for d in sorted(set(mine) | set(fd))))

    entries_at_build = _i(o.get("entriesAtBuild"), 0)
    fill = (round(100.0 * entries_at_build / field_cap, 1)
            if entries_at_build and field_cap else None)
    if fill is not None:
        say("good" if fill < 84.1 else "info",
            f"Contest is {fill}% full. Break-even fill on a guaranteed pool is "
            f"84.1%" + (" — every entry is worth more than it costs."
                        if fill < 84.1 else "."))

    settings = {"n": n, "split": split, "sims": sims, "seed": seed,
                "format": fmt,
                "ownLean": _f(o.get("ownLean"), M.OWN_LEAN),
                "playerCap": _f(o.get("playerCap"), M.PLAYER_CAP),
                "minProj": _f(o.get("minProj"), M.MIN_PROJ),
                "maxOffPool": off_pool,
                "cores": sorted(core_names), "pool": sorted(pool_names)}
    if fmt == "showdown":
        settings.update({"captainCap": caps["captain_cap"],
                         "minCaptains": caps["min_captains"],
                         "splitTargets": shape_targets})
    else:
        settings.update({"qbCap": caps["qb_cap"], "dstCap": caps["dst_cap"],
                         "stackTargets": {str(k): v for k, v in shape_targets.items()},
                         "bringBack": _f(o.get("bringBack"), C.BRING_BACK_SHARE)})
    meta = {
        "slate": datetime.now().astimezone().date().isoformat(),
        "format": fmt,
        "settings": settings,
        "contest_state": {"entries_at_build": entries_at_build or None,
                          "field_cap": field_cap or None,
                          "expect_entries": expect or None,
                          "vendor_field_modelled": modelled,
                          "dupe_scale": round(dupe_scale, 3), "fill_pct": fill},
    }

    dk_csv = None
    if dk and dk["entries"]:
        if len(dk["slots"]) != len(_roster(chosen[0], fmt)):
            say("warn", f"The DK file has {len(dk['slots'])} roster slots but a "
                        f"{fmt} lineup has {len(_roster(chosen[0], fmt))} — no "
                        f"upload file written. Is that the entries export for "
                        f"this slate?")
        else:
            ents = dk["entries"][:len(chosen)]
            if len(ents) < len(chosen):
                say("warn", f"The DK file has {len(ents)} entries but {len(chosen)} "
                            f"lineups were built — writing the first {len(ents)}.")
                chosen = chosen[:len(ents)]
            header = ("Entry ID,Contest Name,Contest ID,Entry Fee,"
                      + ",".join(dk["slots"]))
            dk_csv = _dk_rows(ents, chosen, header, fmt)
            meta["entry_ids"] = [e["entry_id"] for e in ents]
            meta["contest_id"] = ents[0]["contest_id"]
    else:
        say("warn", "No DK entries file, so there is no uploadable CSV — that "
                    "export is the only source of your Entry IDs and DK's "
                    "per-slot player IDs.")

    _log(chosen, meta)

    arms, shapes, heads = {}, {}, {}
    for lu in chosen:
        arms[lu.source] = arms.get(lu.source, 0) + 1
        if fmt == "showdown":
            shapes[lu.split_label()] = shapes.get(lu.split_label(), 0) + 1
            head = lu.cpt
        else:
            key = f"QB+{lu.stack_depth()}"
            shapes[key] = shapes.get(key, 0) + 1
            head = lu.qb()
        if head is not None:
            heads[head.name.strip()] = heads.get(head.name.strip(), 0) + 1
    return {
        "notes": notes,
        "format": fmt,
        "teams": teams,
        "summary": {
            "n": len(chosen), "arms": arms,
            "headLabel": "captains" if fmt == "showdown" else "QBs",
            "shapeLabel": "team split" if fmt == "showdown" else "stack",
            "splits": dict(sorted(shapes.items(), reverse=True)),
            "captains": len(heads),
            "topCaptains": sorted(heads.items(), key=lambda kv: -kv[1])[:6],
            "salaryLo": min(lu.salary for lu in chosen),
            "salaryHi": max(lu.salary for lu in chosen),
            "projAvg": round(sum(lu.proj for lu in chosen) / len(chosen), 1),
            "ownAvg": round(sum(lu.own_sum for lu in chosen) / len(chosen), 1),
            "dupeAvg": round(sum(lu.metrics.get("dupes", 0) for lu in chosen)
                             / len(chosen), 2),
            "fill": fill,
        },
        "lineups": [_lineup_payload(lu, fmt) for lu in chosen],
        "dkCsv": dk_csv,
        "logPath": LOG_PATH,
    }


def _describe(kind, text):
    """Read a dropped file just far enough to say what it is.

    A drop slot that silently accepts anything is worse than no slot at all —
    you find out at build time, or not at all. Each file is checked against what
    that slot actually needs and the answer goes straight back to the page.
    """
    text = text or ""
    if not text.strip():
        return {"ok": False, "msg": "that file is empty"}
    try:
        if kind == "proj":
            players, rep = read_projections(text)
            if rep.get("error"):
                return {"ok": False,
                        "msg": f"not a projections export ({rep['error']})"}
            live = [p for p in players if p.proj > 0]
            teams = sorted({p.team for p in live if p.team})
            if not live:
                return {"ok": False, "msg": "no projected players in that file"}
            where = (" @ ".join(teams) if len(teams) <= 2
                     else f"{len(teams)} teams / {len(teams) // 2} games")
            return {"ok": True, "msg": f"{len(live)} players, {where}",
                    "format": "showdown" if len(teams) <= 2 else "classic"}
        if kind == "field":
            entries, rep = read_field(text)
            if rep.get("error"):
                return {"ok": False,
                        "msg": f"not a lineups export ({rep['error']})"}
            return {"ok": True, "msg": f"{rep['rows']:,} opponent lineups "
                                       f"({rep.get('format', '?')})",
                    "format": rep.get("format")}
        if kind == "dk":
            dk = read_dk_entries(text)
            n_e, n_p = len(dk["entries"]), len(dk["pool"])
            if not n_p:
                return {"ok": False, "msg": "no player pool found — is this the "
                                            "DK entries export?"}
            if not n_e:
                return {"ok": False, "msg": f"{n_p} players but no entries — "
                                            f"enter the contest on DK first, "
                                            f"then download again"}
            slots = dk["slots"]
            sd = bool(slots) and slots[0].strip().upper() == "CPT"
            if sd:
                cpt = sum(1 for v in dk["pool"].values() if v["cpt_dk_id"])
                extra = f"showdown, {cpt} with captain IDs"
            else:
                extra = f"classic, {len(slots)} slots"
            return {"ok": True, "msg": f"{n_e} entries, {n_p} players ({extra})",
                    "format": "showdown" if sd else "classic"}
    except Exception as exc:                                 # noqa: BLE001
        return {"ok": False, "msg": f"could not read that file: {exc}"}
    return {"ok": False, "msg": "unknown file kind"}


# ---------------- server ----------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path not in ("/api/build", "/api/players", "/api/check"):
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            p = json.loads(self.rfile.read(length) or b"{}")

            # The page asks for the slate's players as soon as the projections
            # file lands, so the pool and core boxes can type-ahead. Parsed
            # server-side deliberately: the column matching already lives here
            # and a second implementation in JS would drift from it.
            if self.path == "/api/players":
                players, rep = read_projections(p.get("proj") or "")
                if rep.get("error"):
                    return self._send(400, json.dumps({"error": rep["error"]}))
                teams = sorted({q.team for q in players if q.team})
                return self._send(200, json.dumps({
                    "teams": teams,
                    "format": "showdown" if len(teams) <= 2 else "classic",
                    "players": [{"name": q.name.strip(), "team": q.team,
                                 "pos": q.pos, "salary": q.salary,
                                 "proj": round(q.proj, 1),
                                 "own": round(q.ownership, 1)}
                                for q in sorted(players, key=lambda x: -x.proj)
                                if q.proj > 0],
                }))

            # Confirm a dropped file is the thing the slot expects, so a wrong
            # or unreadable file says so instead of sitting there looking loaded.
            if self.path == "/api/check":
                return self._send(200, json.dumps(
                    _describe(p.get("kind") or "", p.get("text") or "")))

            if not (p.get("proj") or "").strip():
                return self._send(400, json.dumps(
                    {"error": "Drop the Stokastic projections CSV first."}))
            result = run_build(p.get("proj") or "", p.get("field") or "",
                               p.get("dk") or "", p.get("options") or {})
            self._send(400 if result.get("error") else 200, json.dumps(result))
        except Exception as exc:                             # noqa: BLE001
            self._send(500, json.dumps({"error": str(exc)}))


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"NFL Showdown optimizer — {url}")
    print("Drop your files in the browser. Ctrl-C here to stop.")
    try:
        webbrowser.open(url)
    except Exception:                                        # noqa: BLE001
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


# ---------------- command line ----------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="DK NFL Showdown builder")
    ap.add_argument("--proj", help="Stokastic projections CSV")
    ap.add_argument("--field", help="Stokastic lineups CSV (the opponent field)")
    ap.add_argument("--dk", help="DK entries export (needed for an upload file)")
    ap.add_argument("--pool", help="sharp's pool, one name per line")
    ap.add_argument("--cores", help="sharp's cores, one name per line")
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--split", type=int, default=0)
    ap.add_argument("--sims", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--format", choices=("showdown", "classic"), default=None,
                    help="override the auto-detected slate format")
    ap.add_argument("--own-lean", type=float, default=None)
    ap.add_argument("--captain-cap", type=float, default=E.CAPTAIN_CAP)
    ap.add_argument("--min-captains", type=int, default=E.MIN_CAPTAINS)
    ap.add_argument("--player-cap", type=float, default=None)
    ap.add_argument("--qb-cap", type=float, default=C.QB_CAP)
    ap.add_argument("--dst-cap", type=float, default=C.DST_CAP)
    ap.add_argument("--bring-back", type=float, default=C.BRING_BACK_SHARE)
    ap.add_argument("--stack-targets", default=None,
                    help="classic stack quotas, e.g. 3:0.45,2:0.40,1:0.15")
    ap.add_argument("--max-leftover", type=int, default=None)
    ap.add_argument("--min-proj", type=float, default=None)
    ap.add_argument("--max-off-pool", type=int, default=None)
    ap.add_argument("--entries-at-build", type=int, default=None)
    ap.add_argument("--field-cap", type=int, default=None)
    ap.add_argument("--expect-entries", type=int, default=None)
    ap.add_argument("--out", default="nfl_upload.csv")
    a = ap.parse_args(argv)

    if not a.proj:                    # no files named -> open the page
        return serve()

    res = run_build(_read(a.proj), _read(a.field), _read(a.dk), {
        "pool": _read(a.pool), "cores": _read(a.cores),
        "n": a.n, "split": a.split, "sims": a.sims, "seed": a.seed,
        "format": a.format,
        "ownLean": a.own_lean, "captainCap": a.captain_cap,
        "minCaptains": a.min_captains, "playerCap": a.player_cap,
        "qbCap": a.qb_cap, "dstCap": a.dst_cap, "bringBack": a.bring_back,
        "stackTargets": a.stack_targets,
        "maxLeftover": a.max_leftover, "minProj": a.min_proj,
        "maxOffPool": a.max_off_pool, "entriesAtBuild": a.entries_at_build,
        "fieldCap": a.field_cap, "expectEntries": a.expect_entries,
    })
    for note in res.get("notes", []):
        print(f"  [{note['type']}] {note['text']}")
    if res.get("error"):
        print(f"\nERROR: {res['error']}")
        return 2
    s = res["summary"]
    print(f"\n== {s['n']} lineups ({res.get('format')}) ==")
    print(f"  arms      {s['arms']}")
    print(f"  {s['shapeLabel']:<9} {s['splits']}")
    print(f"  {s['headLabel']:<9} {s['captains']} distinct, top "
          + ", ".join(f"{k} {v}" for k, v in s["topCaptains"][:5]))
    print(f"  salary    {s['salaryLo']}-{s['salaryHi']}")
    print(f"  proj avg  {s['projAvg']}   own avg {s['ownAvg']}   "
          f"dupes avg {s['dupeAvg']}")
    if res.get("dkCsv"):
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(res["dkCsv"])
        print(f"\n  wrote {a.out}")
    print(f"  logged to {res['logPath']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
