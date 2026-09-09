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


def _pool_gaps_note(players, mat, sims, min_proj):
    """Strong, low-owned plays the sharp's sheet does not list. -> [Player]

    Advisory only: the tool never adds these, it surfaces the miss and leaves
    the call where it belongs. The list recomputes every build, so once a name
    is added to the pool it drops off on its own.

    The bars come from the SLATE, not from constants. A showdown board has ~30
    playable players and a main slate ~330, and any fixed ceiling-and-ownership
    threshold that suits one is noise on the other. Upside is the simulated 90th
    percentile — the real one out of the correlated sim, not a vendor Ceiling
    column, which is just projection plus 0.675 sigma and says nothing new.
    """
    playable = [p for p in players
                if p.proj >= min_proj and p.salary > 0 and p.dk_id in mat]
    if len(playable) < 12:
        return []
    p90 = {}
    for p in playable:
        row = sorted(mat[p.dk_id])
        p90[p.dk_id] = row[min(int(sims * 0.90), sims - 1)]
    ups = sorted(p90.values())
    owns = sorted(p.ownership for p in playable)
    up_bar = ups[len(ups) // 2]            # above-median upside
    own_bar = owns[len(owns) // 2]         # below-median ownership
    gaps = [p for p in playable
            if not p.in_pool and not p.core
            and p90[p.dk_id] >= up_bar and p.ownership <= own_bar]
    # Highest ceiling first, not best points-per-dollar. Per-dollar hands back a
    # list of cheap quarterbacks every time — QB scoring is high relative to QB
    # salary — and you roster one of those, so four of them is not a list you
    # can act on. Capped at two per position for the same reason.
    gaps.sort(key=lambda p: -p90[p.dk_id])
    out, per = [], {}
    for p in gaps:
        if per.get(p.pos, 0) >= 2:
            continue
        per[p.pos] = per.get(p.pos, 0) + 1
        out.append((p, p90[p.dk_id]))
        if len(out) >= 4:
            break
    return out


def _attach_dk_ids(players, dk):
    """Swap in DK's real player IDs.

    Showdown lists every player twice under two different ids — once as CPT at
    1.5x salary, once as FLEX — so both are carried and the writer picks by
    slot. A flex id in the captain cell is a file DK will not accept.

    A record with an EMPTY flex id counts as a miss, not a hit. It used to count
    as a hit, which meant the player kept the placeholder id `read_projections`
    gave him — his own normalised name — and that name string went into the
    upload file where a number belongs.
    """
    if not dk or not dk.get("pool"):
        return 0, [], []
    hit, miss, no_cpt = 0, [], []
    for p in players:
        rec = dk["pool"].get(normalize_name(p.name))
        if rec and rec.get("dk_id"):
            p.dk_id = rec["dk_id"]
            p.cpt_dk_id = rec["cpt_dk_id"]
            if not p.cpt_dk_id:
                no_cpt.append(p.name)
            hit += 1
        else:
            # Listed regardless of projection. A player projected 0 today can
            # still be carried into a lineup by the vendor arm, which resolves
            # its rosters by name.
            miss.append(p.name)
    return hit, miss, no_cpt


def _roster(lu, fmt):
    """The lineup's players in the order DK's columns expect them."""
    if fmt == "showdown":
        return [lu.cpt] + lu.flex
    return lu.slots()          # QB, RB, RB, WR, WR, WR, TE, FLEX, DST


def _check_upload(entries, lineups, slots, fmt, dk):
    """Everything that would make DK reject the file. -> [problem strings]

    This is the last gate before an upload file exists, and it is deliberately
    written against the FINISHED rows rather than trusting the builder that
    produced them. Every rule the builder already enforces is checked again here
    from the other side, because the builder enforcing it is exactly the
    assumption that has failed before: a name that did not join DK, a captain
    with no captain id, the same player reaching a roster twice through two
    projection rows. None of those raise; they just produce a file DK bounces,
    and by then the slate has locked.
    """
    bad = []
    width = len(slots)
    flex_ids = {v["dk_id"] for v in (dk or {}).get("pool", {}).values() if v["dk_id"]}
    cpt_ids = {v["cpt_dk_id"] for v in (dk or {}).get("pool", {}).values()
               if v["cpt_dk_id"]}
    seen_entries = set()
    for e, lu in zip(entries, lineups):
        eid = e["entry_id"]
        where = f"entry {eid}"
        if eid in seen_entries:
            bad.append(f"{where}: written more than once")
        seen_entries.add(eid)
        ps = _roster(lu, fmt)
        if len(ps) != width or any(p is None for p in ps):
            bad.append(f"{where}: {sum(1 for p in ps if p is not None)} players "
                       f"for {width} roster slots")
            continue
        ids = [p.upload_id(i == 0) if fmt == "showdown" else p.dk_id
               for i, p in enumerate(ps)]
        for p, pid in zip(ps, ids):
            if not str(pid).isdigit():
                bad.append(f"{where}: {p.name.strip()} has no DK player ID "
                           f"(got “{pid}”) — that name never matched the DK file")
        if len({p.dk_id for p in ps}) != width:
            names = [p.name.strip() for p in ps]
            dup = [n for n in names if names.count(n) > 1]
            bad.append(f"{where}: the same player appears twice"
                       + (f" ({dup[0]})" if dup else ""))
        if flex_ids or cpt_ids:
            if fmt == "showdown":
                if ids[0] not in cpt_ids:
                    bad.append(f"{where}: {ps[0].name.strip()} is in the CAPTAIN "
                               f"cell with a non-captain ID — DK will reject it")
                for p, pid in zip(ps[1:], ids[1:]):
                    if pid not in flex_ids:
                        bad.append(f"{where}: {p.name.strip()} has an ID that is "
                                   f"not in the DK player list")
            else:
                for p, pid in zip(ps, ids):
                    if pid not in flex_ids:
                        bad.append(f"{where}: {p.name.strip()} has an ID that is "
                                   f"not in the DK player list")
        if lu.salary > SALARY_CAP:
            bad.append(f"{where}: ${lu.salary:,} is over the ${SALARY_CAP:,} cap")
        if fmt == "showdown" and len({p.team for p in ps if p.team}) < 2:
            bad.append(f"{where}: every player is from one team — showdown needs "
                       f"both")
        if fmt == "classic":
            want = {"QB": 1, "DST": 1}
            got = {}
            for p in ps:
                got[p.pos] = got.get(p.pos, 0) + 1
            for pos, need in want.items():
                if got.get(pos, 0) != need:
                    bad.append(f"{where}: {got.get(pos, 0)} {pos} where DK wants "
                               f"{need}")
            if ps[7] is not None and ps[7].pos not in C.FLEX_POS:
                bad.append(f"{where}: a {ps[7].pos} is in the FLEX slot")
    return bad


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
    if rep.get("duplicate_rows"):
        say("warn", f"{len(rep['duplicate_rows'])} duplicate row(s) in the "
                    f"projections file, kept once each: "
                    + ", ".join(rep["duplicate_rows"][:6]))

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
            say("warn", f"{len(miss)} player(s) in the projections file have no "
                        f"DK match by name and cannot be used: "
                        + ", ".join(miss[:10]) + (" …" if len(miss) > 10 else ""))
        if hit and hit < 0.8 * (hit + len(miss)):
            return {"error": f"Only {hit} of {hit + len(miss)} players matched "
                             f"the DK entries file. That is almost certainly the "
                             f"wrong slate's DK export — check the download.",
                    "notes": notes}
        if no_cpt and fmt == "showdown":
            say("info", f"{len(no_cpt)} player(s) have no CAPTAIN id in the DK "
                        f"file, so they will not be captained: "
                        + ", ".join(no_cpt[:6]))

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
    # Never build more lineups than there are entries to carry them. Building
    # 150 against a 40-entry DK file used to write the first 40 and drop the
    # rest, which quietly deleted the whole vendor arm and left the diagnostics
    # describing 150 lineups that were never uploaded.
    if dk and dk.get("entries") and len(dk["entries"]) < n:
        say("warn", f"The DK file has {len(dk['entries'])} entries, so building "
                    f"{len(dk['entries'])} lineups, not {n}. Enter or reserve "
                    f"the rest on DK and download again if you want more.")
        if split > 0:      # keep the A/B ratio you asked for, at the new size
            split = max(1, round(split * len(dk["entries"]) / n))
        n = len(dk["entries"])
        split = min(split, n) if split > 0 else split

    mat = E.simulate(players, sims=sims, seed=seed)
    bar, sampled = M.field_bar(field, mat, sims, seed=seed) if field else (None, 0)
    idx = M.dupe_index(field) if field else {}
    modelled = M.field_size(field) if field else 0
    # NFL contests of this size fill, so the contest's max entries IS the field
    # size unless told otherwise. Leaving it blank used to mean duplication was
    # measured against the ~50,000 opponents the vendor models instead of the
    # 237,812 that actually enter — understating it by nearly 5x.
    field_cap = _i(o.get("fieldCap"), 0)
    fill_pct = _f(o.get("fillPct"), 100.0)
    fill_pct = min(max(fill_pct, 1.0), 100.0)
    expect = _i(o.get("expectEntries"), 0) or int(round(field_cap * fill_pct / 100.0))
    dupe_scale = max(1.0, expect / modelled) if (modelled and expect) else 1.0

    if bar:
        srt = sorted(bar)
        say("info", f"Score to beat, from {sampled:,} sampled opponents: "
                    f"{srt[len(srt) // 2]:.0f} median.")
        say("info", f"Vendor pool models {modelled:,.0f} opponent entries in "
                    f"{len(idx):,} distinct lineups.")
        if expect:
            say("info", f"Scaling duplication ×{dupe_scale:.2f} for a "
                        f"{expect:,}-entry field"
                        + (f" ({fill_pct:.0f}% of {field_cap:,})."
                           if field_cap and fill_pct < 100 else "."))
        elif modelled:
            say("warn", f"No contest size given, so duplication is measured "
                        f"against the {modelled:,.0f} opponents the vendor "
                        f"models. A real contest is bigger, so the numbers "
                        f"below understate it — put the contest's max entries "
                        f"in the settings.")
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
    elif raw_off is None or raw_off == "":
        off_pool = 0          # not specified: a pool means the pool
    elif isinstance(raw_off, str) and raw_off.lower() in ("none", "off", "nolimit"):
        # "No limit" has to MEAN no limit. It used to fall into the same branch
        # as "not set" and become 0 — the strictest setting there is, the exact
        # opposite of what the option says. Note the ordering: an unset CLI flag
        # arrives as the None OBJECT and must not be read as the word "none".
        off_pool = None
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

    if pool_names:
        gaps = _pool_gaps_note(players, mat, sims, _f(o.get("minProj"), M.MIN_PROJ))
        if gaps:
            say("good", "Pool gaps — high-upside, low-owned plays NOT on your "
                        "sheet: "
                        + "; ".join(f"{p.name.strip()} ({up:.0f} ceiling, "
                                    f"{p.ownership:.0f}% owned, ${p.salary:,})"
                                    for p, up in gaps)
                        + ". Your sharp may have passed on purpose. If not, add "
                          "them — each drops off this list once you do.")
        elif fmt == "showdown":
            say("info", "No pool gaps. On a ~30 player showdown board ownership "
                        "tracks upside closely, so there is rarely anything both "
                        "strong and unowned — expect this line most nights.")
        else:
            say("info", "No pool gaps: nothing outside your sheet combines "
                        "above-median upside with below-median ownership.")

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
            # Only players DK gave a captain id can go in the captain cell. The
            # writer used to fall back to their flex id, which produces a file
            # DK rejects — and the build merely warned, then captained them.
            cpt_pool = [p for p in players
                        if p.proj >= _f(o.get("minProj"), E.MIN_PROJ)
                        and p.salary > 0 and (p.cpt_dk_id or not dk)]
            cands = E.build_candidates(players, max(4000, n_mine * 30),
                                       teams=teams,
                                       cpt_pool=cpt_pool or None, **common)
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
        dupe_kw = {"field_n": modelled}
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


    if n_vendor:
        if not field:
            say("warn", f"The vendor half of the split needs the vendor lineup "
                        f"file. Building all {n} from our own builder instead, "
                        f"so every entry still gets a lineup.")
            shape_kw = ({"split_targets": shape_targets} if fmt == "showdown"
                        else {"stack_targets": shape_targets})
            extra = M.select(cands, n, core_floors=floors,
                             exclude={lu.key() for lu in chosen},
                             **shape_kw, **caps)
            chosen += extra[:n_vendor]
            if len(chosen) < n:
                say("warn", f"Only {len(chosen)} distinct lineups could be built "
                            f"for {n} entries.")
        else:
            # What our own arm already took. Both arms rank the same player pool
            # on the same objective, so they land on the same rosters — a real
            # 75/75 showdown split produced three identical pairs before this.
            mine_keys = {lu.key() for lu in chosen}
            # The pool and the cores are your instructions, not our preference,
            # so they have to bind on ALL 150 entries. They used to apply only
            # to our own half while the notes said "every player must come from
            # it" — and 70 of the vendor 75 held off-pool players.
            vfield = field
            # Their lineups can captain a player DK gave no captain id — the
            # writer would then put his flex id in the captain cell, which DK
            # rejects. Drop those rosters rather than let the whole file fail
            # the gate below.
            if fmt == "showdown" and dk:
                before = len(vfield)
                vfield = [e for e in vfield
                          if e.get("cpt") is not None and e["cpt"].cpt_dk_id]
                if len(vfield) < before:
                    say("info", f"Skipped {before - len(vfield):,} of their "
                                f"lineups that captain a player with no DK "
                                f"captain ID.")
            if off_pool is not None:
                def _off(e):
                    ps = ([e["cpt"]] + e["flex"]) if fmt == "showdown" else e["flex"]
                    return sum(1 for p in ps if p and not p.in_pool and not p.core)
                vfield = [e for e in field if _off(e) <= off_pool]
                say("info" if vfield else "warn",
                    f"Vendor pool filtered to your player pool: "
                    f"{len(vfield):,} of {len(field):,} of their lineups qualify.")
                if not vfield:
                    say("warn", "None of their lineups fit your pool, so the "
                                "vendor half is built from ours instead.")
            vcores = ({p.dk_id: max(1, -(-n_vendor // (len(core_ids) + 1)))
                       for p in players if p.core and p.proj > 0}
                      if core_ids and n_vendor else None)
            if vfield and fmt == "showdown":
                chosen += E.vendor_arm(vfield, n_vendor, players_by_id=by_id,
                                       dupe_scale=dupe_scale,
                                       exclude=mine_keys, core_floors=vcores,
                                       **caps)
            elif vfield:
                chosen += C.vendor_arm(vfield, n_vendor, dupe_scale=dupe_scale,
                                       exclude=mine_keys, core_floors=vcores,
                                       **caps)
            else:
                shape_kw = ({"split_targets": shape_targets} if fmt == "showdown"
                            else {"stack_targets": shape_targets})
                extra = M.select(cands, n, core_floors=floors,
                                 exclude=mine_keys, **shape_kw, **caps)
                chosen += extra[:n_vendor]

    # Top up from our own candidates if either arm came up short — a vendor pool
    # thinned by the pool filter or by missing captain IDs can leave entries
    # with no lineup at all, and an entry with no lineup is an entry that scores
    # zero. This is the last chance to notice.
    if len(chosen) < n and n_mine and cands:
        shape_kw = ({"split_targets": shape_targets} if fmt == "showdown"
                    else {"stack_targets": shape_targets})
        top_up = M.select(cands, n, core_floors=floors,
                          exclude={lu.key() for lu in chosen},
                          **shape_kw, **caps)
        need = n - len(chosen)
        chosen += top_up[:need]
        if len(chosen) >= n:
            say("info", f"Topped up {need} entries from our own builder to cover "
                        f"all {n}.")

    if not chosen:
        return {"error": "No lineups produced.", "notes": notes}

    # Did the core floors hold across ALL the entries, not just our own half?
    # Never assume they did. The one failure this tool has been bitten by twice
    # is a core landing in a fraction of the entries the user was told it would,
    # and it fails QUIETLY — the top-up loop stops when no legal swap exists and
    # says nothing. So check the finished set, name the player, say how short.
    if core_ids:
        want = max(1, -(-n // (len(core_ids) + 1)))
        short = []
        for p in players:
            if not p.core or p.proj <= 0:
                continue
            got = sum(1 for lu in chosen if p.dk_id in lu.ids())
            if got < want:
                short.append(f"{p.name.strip()} is in {got} of {len(chosen)}, "
                             f"not {want}")
        if short:
            say("warn", "A core could not be given its full share — "
                        + "; ".join(short) + ". Usually the pool, the salary cap "
                        "or the stack rules leave nowhere legal to put them.")
        else:
            say("good", f"Every core is in at least {want} of {len(chosen)} "
                        f"entries.")

    if dk and dk.get("entries") and len(chosen) < min(n, len(dk["entries"])):
        say("warn", f"Only {len(chosen)} lineups could be built for "
                    f"{min(n, len(dk['entries']))} entries. The remaining "
                    f"entries will have no lineup and score zero — widen the "
                    f"pool or lower the minimum projection.")

    # Belt and braces. Every path above dedupes, but this is the one error that
    # is invisible in the output and costs a real entry, so it is checked on the
    # finished set rather than trusted.
    keys = [lu.key() for lu in chosen]
    dupes = len(keys) - len(set(keys))
    if dupes:
        say("warn", f"{dupes} of these {len(chosen)} entries duplicate another "
                    f"one exactly. That should not happen — do not upload until "
                    f"it is looked at.")
    else:
        say("good", f"All {len(chosen)} entries are distinct rosters.")

    # Where these entries sit against the actual opponents, on the two axes the
    # brief says decide a main slate. Ownership is the least-trusted finding in
    # the whole report, so the honest thing is to SHOW the size of the bet being
    # made rather than bake a direction in and stay quiet about it.
    if field:
        # Count the field's ownership over the same slots ours is counted over.
        # A showdown captain carries his OWN ownership number on a different
        # denominator, and leaving it out of the field's total while keeping it
        # in ours compared a six-slot sum against a five-slot one — which made
        # these lineups look far chalkier against the field than they are.
        def _own_of(e):
            flex = sum(p.ownership for p in (e.get("flex") or []))
            cpt = e.get("cpt")
            return flex + (cpt.cpt_own if cpt is not None else 0.0)
        f_own = sorted(_own_of(e) for e in field)
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
        "contest_state": {"field_cap": field_cap or None,
                          "fill_pct": fill_pct,
                          "expect_entries": expect or None,
                          "vendor_field_modelled": modelled,
                          "dupe_scale": round(dupe_scale, 3)},
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
            # Check the finished rows before there is a file to download. A file
            # DK bounces is worse than no file: you find out at lock, with no
            # time to rebuild.
            problems = _check_upload(ents, chosen, dk["slots"], fmt, dk)
            if problems:
                say("warn", f"NOT writing an upload file — {len(problems)} "
                            f"problem(s) DraftKings would reject:")
                for line in problems[:12]:
                    say("warn", "    " + line)
                if len(problems) > 12:
                    say("warn", f"    …and {len(problems) - 12} more.")
                say("warn", "Fix the input files and build again. Almost always "
                            "this means a player's name differs between the "
                            "Stokastic and DK exports — re-download both.")
            else:
                dk_csv = _dk_rows(ents, chosen, header, fmt)
                say("good", f"Upload file checked: {len(ents)} rows, every player "
                            f"ID valid for its slot, no repeats, none over the cap.")
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
    ap.add_argument("--field-cap", type=int, default=None,
                    help="contest max entries")
    ap.add_argument("--fill-pct", type=float, default=100.0,
                    help="how full it will get, %% of max (default 100)")
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
        "maxOffPool": a.max_off_pool,
        "fieldCap": a.field_cap, "fillPct": a.fill_pct,
        "expectEntries": a.expect_entries,
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
