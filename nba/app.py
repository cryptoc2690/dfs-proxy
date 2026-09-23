"""NBA — server, builder and DK upload writer, for both DraftKings formats.

Classic (PG/SG/SF/PF/C/G/F/UTIL, the main slate) and Showdown (CPT + 5 UTIL)
share this file, the simulator, the readers and the page. The process is the
NFL tool's, by the user's decision: Stokastic's projections, Stokastic's lineups
export read as the opponent field, our own lineups ranked on beating that field,
and a DK entries export filled and checked for upload. Which format a build is
comes from the files — the DK export names its slots — and is said out loud.

    python3 nba/app.py                      # opens the drop-your-files page

    python3 nba/app.py --proj proj.csv --field lineups.csv --dk DKEntries.csv \\
        --n 150 --split 75 --field-cap 47000 --out upload.csv

    python3 nba/app.py --grade contest-standings-123.csv   # after the slate

Late swap is deliberately absent: it is paused pending its own discussion.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import classic as C
import engine as E
from dk import (CLASSIC_SIZE, CLASSIC_SLOTS, SALARY_CAP, SD_ROSTER_SIZE,
                ShowdownLineup, normalize_name, positions_of, slots_for)
from gui import INDEX_HTML
from sources import (read_dk_entries, read_field, read_linestar, read_projections,
                     read_sharp,
                     read_standings, payout_ladder, _roster_key)

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "logs", "nba_builds.jsonl")
PORT = int(os.environ.get("PORT", "8020"))


def _read(path):
    """Read a dropped file; a DK standings .zip with one CSV is read through."""
    if not path:
        return ""
    if str(path).lower().endswith(".zip"):
        import zipfile
        with zipfile.ZipFile(path) as z:
            inner = [n for n in z.namelist()
                     if n.lower().endswith(".csv") and "__MACOSX" not in n]
            if not inner:
                return ""
            with z.open(inner[0]) as fh:
                return fh.read().decode("utf-8-sig", errors="replace")
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


def _share(v, d):
    """A cap or share. "28" means 28%, not 2,800%."""
    x = _f(v, d)
    return x / 100.0 if x > 1.0 else x


_CAP_LINE = re.compile(r"^(.*?)[\s,:=]+(\d{1,3}(?:\.\d+)?)\s*%?$")


def _player_caps(text, players):
    """-> ({dk_id: share}, [unmatched lines], [unreadable lines]). An unmatched
    name is reported, never dropped: silently ignoring a cap you typed is the
    failure this control exists to prevent."""
    by_name = {normalize_name(p.name): p for p in players}
    caps, missing, bad = {}, [], []
    for raw in re.split(r"[\r\n;]+", text or ""):
        line = raw.strip()
        if not line:
            continue
        m = _CAP_LINE.match(line)
        if not m:
            bad.append(line)
            continue
        key = normalize_name(m.group(1).strip())
        p = by_name.get(key)
        if p is None and key:
            hits = [v for k, v in by_name.items() if k.startswith(key)]
            p = hits[0] if len(hits) == 1 else None
        if p is None:
            missing.append(line)
            continue
        caps[p.dk_id] = min(1.0, max(0.0, float(m.group(2)) / 100.0))
    return caps, missing, bad


# ---------------- late scratch: the WNBA removal ----------------
# A player ruled out after the projections were pulled (the Stephon Castle case:
# out five minutes before lock, and Stokastic could not update in time). Done
# exactly as WNBA does it, by decision: zero him, and push about 65% of his
# production onto teammates, weighted toward the same position — his minutes
# and usage do not vanish, they flow to the next men up. Ownership is left
# alone: it is the field's number, and our redistribution must not inflate a
# replacement's ownership.
#
# The constants are WNBA's and have not been measured on NBA — where scoring
# runs about a third higher the 8-point per-player cap may bind more. A fresh
# Stokastic pull is always the better fix when there is time for one.
REMOVE_SHARE = 0.65
REMOVE_SAME_POS = 1.6
REMOVE_CAP_SHARE = 0.40
REMOVE_CAP_PTS = 8.0


def apply_removals(players, names):
    """-> ([{"name","proj","salary"}], [unmatched names])"""
    names = set(names or ())
    known = {normalize_name(p.name) for p in players}
    unmatched = sorted(n for n in names if n not in known)
    removed = [(p, p.proj) for p in players
               if normalize_name(p.name) in names and p.proj > 0]
    for p, _ in removed:
        p.proj = 0.0
        p.removed = True
        p.notes.append("removed — ruled out after the projections")
    for p, vac in removed:
        mates = [q for q in players if q.team == p.team and q.proj > 0 and not q.removed]
        if not mates:
            continue
        mine = positions_of(p.pos)
        w = {id(q): q.proj * (REMOVE_SAME_POS if positions_of(q.pos) & mine else 1.0)
             for q in mates}
        tot = sum(w.values()) or 1.0
        for q in mates:
            bump = min(REMOVE_SHARE * vac * w[id(q)] / tot,
                       REMOVE_CAP_SHARE * q.proj, REMOVE_CAP_PTS)
            if bump <= 0.3:
                continue
            ratio = (q.proj + bump) / q.proj
            q.proj = round(q.proj + bump, 2)
            q.sd = round(q.sd * ratio, 2) if q.sd > 0 else q.sd
            q.notes.append(f"+{bump:.1f} ({p.name} out)")
    return ([{"name": p.name, "proj": round(v, 1), "salary": p.salary}
             for p, v in removed], unmatched)


# ---------------- DK ids, eligibility, and the field ----------------
def _attach_dk(players, dk):
    """Swap in DK's ids, and take DK's word on eligibility and tip time.

    An empty id counts as a miss, not a hit: a hit on an empty id once put a
    player's normalised NAME into the upload file where a number belongs (NFL).
    """
    if not dk or not dk.get("pool"):
        return 0, [], []
    hit, miss, no_cpt = 0, [], []
    for p in players:
        rec = dk["pool"].get(normalize_name(p.name))
        if rec and rec.get("dk_id"):
            p.dk_id = rec["dk_id"]
            p.cpt_dk_id = rec.get("cpt_dk_id") or ""
            if rec.get("eligible"):
                p.eligible = set(rec["eligible"])
            p.start = rec.get("start") or ""
            p.team = p.team or rec.get("team") or ""
            if not p.cpt_dk_id:
                no_cpt.append(p.name)
            hit += 1
        else:
            miss.append(p.name)
    return hit, miss, no_cpt


def _field_lineups(entries, fmt):
    """Vendor rows -> lineup objects the engine can score. Illegal rows (a
    roster DK would refuse) are dropped here: they cannot be entered, so they
    cannot be the vendor arm, though they still count as opponents."""
    out, bad = [], 0
    for e in entries:
        if fmt == "showdown":
            if e.get("cpt") is None or len(e.get("flex") or []) != SD_ROSTER_SIZE - 1:
                bad += 1
                continue
            lu = ShowdownLineup(e["cpt"], e["flex"], source="vendor")
        else:
            ps = e.get("flex") or []
            if len(ps) != CLASSIC_SIZE:
                bad += 1
                continue
            lu = C.Lineup(ps, slotting=ps, source="vendor")
        lu.metrics = {"vdupes": e.get("dupes") or 0.0, "win": e.get("win", 0.0),
                      "top10": e.get("top10", 0.0)}
        out.append(lu)
    return out, bad


def _enterable(lu, fmt):
    """Can this vendor roster actually be entered today?"""
    if any(p.proj <= 0 or p.removed for p in lu.players):
        return False
    if fmt == "showdown":
        return (lu.salary <= SALARY_CAP and len({p.team for p in lu.players}) >= 2)
    return C.legal(lu.players) == ""


def _pool_short(players, min_proj):
    """Which classic slots the sharp's sheet cannot fill. -> [slot, ...]"""
    elig = [p for p in players if p.in_pool and p.proj >= min_proj and p.salary > 0]
    if len(elig) < CLASSIC_SIZE:
        return list(CLASSIC_SLOTS)
    return [s for s in CLASSIC_SLOTS if not any(s in p.eligible for p in elig)] or \
        ([] if C.assignments(_greedy_cover(elig), limit=1) else ["(no legal eight)"])


def _greedy_cover(elig):
    """Eight pool players that between them try to cover every slot — only used
    to ask whether the sheet can make ANY legal eight."""
    chosen = []
    for s in CLASSIC_SLOTS:
        c = next((p for p in sorted(elig, key=lambda p: len(p.eligible))
                  if s in p.eligible and p not in chosen), None)
        if c:
            chosen.append(c)
    for p in elig:
        if len(chosen) >= CLASSIC_SIZE:
            break
        if p not in chosen:
            chosen.append(p)
    return chosen[:CLASSIC_SIZE]


# ---------------- pool gaps (advisory) ----------------
GAP_MIN_CEILING = 0.70     # top 30% of the position on simulated upside
GAP_MIN_LEVERAGE = 0.20    # ceiling rank must beat ownership rank by this much
GAP_MIN_POS = 5
GAP_SHOW = 12


def _pool_gaps_note(players, mat, sims, min_proj):
    """Strong, low-owned plays the sharp's sheet does not list. NFL's rule,
    ranked within the PRIMARY position (the first one listed), because across
    positions a center's upside and a guard's are not the same number. Advisory:
    the tool never adds these."""
    playable = [p for p in players if p.proj >= min_proj and p.salary > 0
                and p.dk_id in mat and not p.removed]
    if len(playable) < 12:
        return []
    prim = lambda p: (p.pos.split("/")[0] if p.pos else "?")
    p90 = {p.dk_id: sorted(mat[p.dk_id])[min(int(sims * 0.9), sims - 1)] for p in playable}
    pct = {}
    for pos in {prim(p) for p in playable}:
        grp = [p for p in playable if prim(p) == pos]
        ref = grp if len(grp) >= GAP_MIN_POS else playable
        ups = sorted(p90[q.dk_id] for q in ref)
        owns = sorted(q.ownership for q in ref)
        for p in grp:
            pct[p.dk_id] = (sum(1 for x in ups if x < p90[p.dk_id]) / len(ups),
                            sum(1 for x in owns if x < p.ownership) / len(owns))
    gaps = [p for p in playable if not p.in_pool and not p.core
            and pct[p.dk_id][0] >= GAP_MIN_CEILING
            and pct[p.dk_id][0] - pct[p.dk_id][1] >= GAP_MIN_LEVERAGE]
    gaps.sort(key=lambda p: -(pct[p.dk_id][0] - pct[p.dk_id][1]))
    return [(p, p90[p.dk_id]) for p in gaps]


# ---------------- the coach: a read on the build, not edits ----------------
# WNBA's _coach, carried over by request. It explains what the data supports and
# flags where the settings diverge from it, so an override (a weak core, a cut
# the data liked) is made consciously rather than by reflex. It checks the DATA,
# never the outcome, and it never changes the build — the call stays yours.
#
# WNBA graded a core on ceiling, Vegas spot and ownership. Stokastic's NBA file
# carries no Vegas, so the spot half is replaced by what it does carry: the
# starting flag and projected minutes. The ceiling is the simulated 90th
# percentile ranked within the core's own position, because an absolute bar
# (WNBA's 25) does not travel between sports or positions.
CORE_THIN_PCT = 0.50       # below the position's median simulated upside
CORE_LOW_MINUTES = 24.0


def _p90(mat, p, sims):
    row = mat.get(p.dk_id)
    return sorted(row)[min(int(sims * 0.9), sims - 1)] if row else 0.0


def _coach(players, chosen, mat, sims, removed, pool_names, off_pool, floors_total):
    notes = []
    n = len(chosen) or 1
    live = [p for p in players if p.proj > 0 and p.dk_id in mat]
    prim = lambda p: (p.pos.split("/")[0] if p.pos else "?")
    up = {p.dk_id: _p90(mat, p, sims) for p in live}
    count = {}
    for lu in chosen:
        for p in lu.players:
            count[p.dk_id] = count.get(p.dk_id, 0) + 1

    cores = sorted((p for p in players if p.core), key=lambda p: -p.ownership)
    for c in cores:
        if c.proj <= 0:
            notes.append(("warn", f"Core check — {c.name}: projected 0 (out, or removed). "
                                  f"He is in no lineup; drop him from the cores."))
            continue
        grp = [q for q in live if prim(q) == prim(c)] or live
        pct = sum(1 for q in grp if up[q.dk_id] < up[c.dk_id]) / len(grp)
        why = []
        if pct < CORE_THIN_PCT:
            why.append(f"a thin ceiling for a {prim(c)} ({up[c.dk_id]:.0f}, "
                       f"{pct:.0%} of the position below him)")
        if c.starting is False:
            why.append("not listed as starting")
        if 0 < c.minutes < CORE_LOW_MINUTES:
            why.append(f"{c.minutes:.0f} projected minutes")
        own = f"{c.ownership:.0f}% owned"
        if why:
            notes.append(("warn", f"Core check — {c.name}: " + ", ".join(why) + f", {own}. "
                                  f"That is mandatory-exposure territory, not a "
                                  f"build-around — the data would lean lighter here."))
        else:
            notes.append(("good", f"Core check — {c.name}: {up[c.dk_id]:.0f} ceiling "
                                  f"(top {100 - pct * 100:.0f}% of {prim(c)}s), {own}"
                                  + (f", {c.minutes:.0f} min" if c.minutes else "")
                                  + ". Solid anchor."))
    if len(cores) >= 2:
        by_game = {}
        for c in cores:
            by_game.setdefault(c.game, []).append(c.name)
        g, names = max(by_game.items(), key=lambda kv: len(kv[1]))
        if len(names) >= 2:
            notes.append(("info", f"{len(names)} of your {len(cores)} cores are in {g} "
                                  f"({', '.join(names)}) — they rise and fall together."))
    if floors_total:
        want = next(iter(floors_total.values()))
        held = []
        for c in cores:
            if c.proj <= 0:
                continue
            got = count.get(c.dk_id, 0)
            held.append(f"{c.name} in {got} of {n} — "
                        + ("carried by the floor" if got <= want * 1.25 else "wanted anyway"))
        if held:
            notes.append(("info", f"Core floor: each core is guaranteed at least {want} of "
                                  f"{n} lineups (the floor is n / (cores + 1), so more cores "
                                  f"spread it thinner). What that did: " + "; ".join(held)
                                  + ". A core only moves a player the ranking would "
                                    "otherwise under-use."))

    non_core = [(count.get(p.dk_id, 0), p) for p in live if not p.core]
    if non_core:
        c, p = max(non_core, key=lambda t: t[0])
        pct = 100.0 * c / n
        if pct >= 55:
            value = p.proj / max(p.salary / 1000.0, 0.1)
            notes.append(("good" if value >= 5.0 else "info",
                          f"{p.name} is your heaviest play ({pct:.0f}% of lineups, field "
                          f"{p.ownership:.0f}%): ${p.salary:,}, proj {p.proj:.1f}, "
                          f"{value:.1f}x value. "
                          + ("Earned — don't cut it on a hunch." if value >= 5.0 else
                             "Modest value; know the set leans on that one spot.")))

    slots, teams_n = {}, len({p.team for p in live if p.team}) or 1
    for lu in chosen:
        for p in lu.players:
            slots[p.team] = slots.get(p.team, 0) + 1
    if slots and teams_n >= 3:
        t, ct = max(slots.items(), key=lambda kv: kv[1])
        share, even = ct / sum(slots.values()), 1.0 / teams_n
        if share >= even * 1.5:
            notes.append(("info", f"Team lean: {share:.0%} of your roster slots are {t} "
                                  f"(an even split is {even:.0%}). They share one game "
                                  f"script — if {t} gets blown out, that lean goes with it."))

    for r in removed:
        notes.append(("info", f"You removed {r['name']} (projected {r['proj']} at "
                              f"${r['salary']:,}). Right call if it is a confirmed scratch; "
                              f"if it is a hunch, the data liked this play."))
    if pool_names and off_pool:
        notes.append(("info", f"Up to {off_pool} off-pool player(s) per lineup allowed — "
                              f"they are marked in the lineup and exposure tables."))
    return [{"type": t, "text": x} for t, x in notes]


# ---------------- exposure ----------------
def _exposure(chosen, fmt):
    n = len(chosen) or 1
    ply, teams, cpt = {}, {}, {}
    for lu in chosen:
        for p in lu.players:
            rec = ply.setdefault(p.dk_id, {"name": p.name, "pos": p.pos, "team": p.team,
                                           "n": 0, "own": p.ownership, "core": p.core,
                                           "off": _off_sheet(p)})
            rec["n"] += 1
            teams.setdefault(p.team, {"team": p.team, "slots": 0, "lineups": 0})["slots"] += 1
        for t in {p.team for p in lu.players}:
            teams.setdefault(t, {"team": t, "slots": 0, "lineups": 0})["lineups"] += 1
        if fmt == "showdown":
            cpt[lu.cpt.dk_id] = cpt.get(lu.cpt.dk_id, 0) + 1
    out = []
    for k, r in ply.items():
        pct = 100.0 * r["n"] / n
        out.append({"name": r["name"], "pos": r["pos"], "team": r["team"], "n": r["n"],
                    "pct": round(pct, 1), "own": round(r["own"], 1), "core": r["core"],
                    "off": r["off"], "edge": round(pct - r["own"], 1),
                    "cpt": cpt.get(k, 0)})
    out.sort(key=lambda r: -r["n"])
    tm = [{"team": t, "lineups": r["lineups"], "pct": round(100.0 * r["lineups"] / n, 1),
           "slots": r["slots"]} for t, r in teams.items()]
    tm.sort(key=lambda r: -r["lineups"])
    return {"players": out, "teams": tm}


def _off_sheet(p):
    return p is not None and bool(getattr(p, "_sheet", False)) and not p.in_pool and not p.core


# ---------------- the upload file ----------------
def _roster(lu, fmt, start_of=None):
    """Players in the order DK's columns expect."""
    if fmt == "showdown":
        return [lu.cpt] + lu.flex
    if start_of:
        s = C.best_slotting(lu.players, start_of)
        if s:
            lu.slotting = s
    return lu.slots()


def _check_upload(entries, rosters, slots, fmt, dk):
    """Everything that would make DK reject the file, checked against the
    FINISHED rows rather than trusting the builder (NFL's last gate)."""
    bad = []
    pool = (dk or {}).get("pool", {})
    flex_ids = {v["dk_id"] for v in pool.values() if v.get("dk_id")}
    cpt_ids = {v["cpt_dk_id"] for v in pool.values() if v.get("cpt_dk_id")}
    elig = {v["dk_id"]: v.get("eligible") or set() for v in pool.values() if v.get("dk_id")}
    seen = set()
    for e, ps in zip(entries, rosters):
        where = f"entry {e['entry_id']}"
        if e["entry_id"] in seen:
            bad.append(f"{where}: written more than once")
        seen.add(e["entry_id"])
        if ps is None or len(ps) != len(slots) or any(p is None for p in ps):
            bad.append(f"{where}: roster does not fill the {len(slots)} slots")
            continue
        ids = [p.upload_id(i == 0) if fmt == "showdown" else p.dk_id
               for i, p in enumerate(ps)]
        for p, pid in zip(ps, ids):
            if not str(pid).isdigit():
                bad.append(f"{where}: {p.name} has no DK player ID — that name "
                           f"never matched the DK file")
        if len({p.dk_id for p in ps}) != len(ps):
            bad.append(f"{where}: the same player appears twice")
        sal = (ps[0].cpt_salary() + sum(p.salary for p in ps[1:])) if fmt == "showdown" \
            else sum(p.salary for p in ps)
        if sal > SALARY_CAP:
            bad.append(f"{where}: ${sal:,} is over the ${SALARY_CAP:,} cap")
        if fmt == "showdown":
            if cpt_ids and ids[0] not in cpt_ids:
                bad.append(f"{where}: {ps[0].name} is in the CAPTAIN cell with a "
                           f"non-captain ID — DK will reject it")
            if flex_ids and any(pid not in flex_ids for pid in ids[1:]):
                bad.append(f"{where}: a UTIL id is not in the DK player list")
            if len({p.team for p in ps}) < 2:
                bad.append(f"{where}: every player is from one team")
        else:
            if flex_ids and any(pid not in flex_ids for pid in ids):
                bad.append(f"{where}: an id is not in the DK player list")
            for s, p in zip(slots, ps):
                if elig and s not in elig.get(p.dk_id, set()):
                    bad.append(f"{where}: {p.name} is in the {s} slot and DK does "
                               f"not list him there ({'/'.join(sorted(elig.get(p.dk_id, [])))})")
            if len({p.game for p in ps}) < 2:
                bad.append(f"{where}: only one game — DK requires two")
    return bad


def _contests(entries):
    """Split a DK export into its contests, in file order. One download carries
    several — a real NBA export held three, including a $0 free contest."""
    order, byid = [], {}
    for e in entries:
        cid = e.get("contest_id") or ""
        if cid not in byid:
            byid[cid] = []
            order.append(cid)
        byid[cid].append(e)
    return [(cid, (byid[cid][0].get("contest") or "").strip(), byid[cid]) for cid in order]


def _assign(contests, lineups):
    """Every contest gets the TOP of one ranked list (NFL, by decision): a
    20-max takes the best 20, a 150-max all 150. Separate prize pools, so the
    same lineup in two contests is two shots at first, not a duplicate."""
    pairs = []
    for _cid, _name, ents in contests:
        for e, lu in zip(ents, lineups):
            pairs.append((e, lu))
    return pairs


def _dk_rows(entries, rosters, header, fmt):
    lines = [header]
    for e, ps in zip(entries, rosters):
        cells = [f'"{p.name} ({p.upload_id(i == 0) if fmt == "showdown" else p.dk_id})"'
                 for i, p in enumerate(ps)]
        cname = (e["contest"] or "").replace('"', '""')
        lines.append(f'{e["entry_id"]},"{cname}",{e["contest_id"]},{e["fee"]},'
                     + ",".join(cells))
    return "\n".join(lines) + "\n"


# ---------------- the build log ----------------
def _log(lineups, rosters, meta):
    """One JSON line per ENTRY — the asset every later review reads.

    It has to answer "what did we believe, and what did we build, at this
    moment" on its own: the projection and its spread, minutes and starting
    flag, ownership, the settings, the contest, the vendor field's size, and
    each lineup's SHAPE — the game and team blocks against the slate's game
    count, which is where WNBA found construction inverting with slate size and
    where NBA's version of that finding will have to come from.
    """
    fmt = meta.get("format")
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        now = datetime.now().astimezone()
        ts = now.isoformat(timespec="seconds")
        # One id per BUILD. The timestamp is not enough: two builds in the same
        # second shared one, and grading then read them as a single 80-entry
        # build of 40 entries.
        build_id = now.isoformat(timespec="microseconds")
        ids = meta.get("entry_ids") or []
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            for i, (lu, ps) in enumerate(zip(lineups, rosters)):
                row = {
                    "ts": ts, "build_id": build_id, "slate": meta.get("slate"),
                    "games": meta.get("games"),
                    "format": fmt, "contest_id": meta.get("contest_ids", [None] * (i + 1))[i]
                    if i < len(meta.get("contest_ids") or []) else None,
                    "entry_id": ids[i] if i < len(ids) else None,
                    "source": lu.source,
                    "players": [{"name": p.name, "id": p.dk_id, "pos": p.pos,
                                 "slot": s, "team": p.team, "salary": p.salary,
                                 "proj": p.proj, "sd": round(E.player_sd(p), 2),
                                 "own": p.ownership,
                                 "cpt_own": p.cpt_own if fmt == "showdown" else None,
                                 "minutes": p.minutes, "starting": p.starting,
                                 "ls_proj": p.ls_proj, "ls_status": p.ls_status,
                                 "core": p.core, "pool": p.in_pool}
                                for s, p in zip(meta.get("slots") or [], ps)],
                    "salary": lu.salary, "leftover": SALARY_CAP - lu.salary,
                    "proj": lu.proj, "own_sum": lu.own_sum,
                    "metrics": lu.metrics,
                    "settings": meta.get("settings", {}),
                    "projection": meta.get("projection", {}),
                    "contest_state": meta.get("contest_state", {}),
                    "removed": meta.get("removed", []),
                }
                if fmt == "showdown":
                    row.update({"captain": lu.cpt.name, "captain_id": lu.cpt.dk_id,
                                "shape": lu.split_label(), "major_team": lu.major_side()})
                else:
                    gb, bg, tb, bt, used = lu.blocks()
                    row.update({"shape": lu.shape_label(), "game_block": gb,
                                "block_game": bg, "team_block": tb, "block_team": bt,
                                "games_used": used,
                                "slate_games": len(meta.get("games") or []),
                                "studs": sum(1 for p in lu.players
                                             if p.salary >= C.STUD_SALARY)})
                fh.write(json.dumps(row) + "\n")
        return None
    except Exception as exc:                  # never let logging break a build…
        return f"Build log not written ({exc.__class__.__name__}: {exc})."  # …or vanish


# ---------------- the build ----------------
def run_build(proj_text, field_text="", dk_text="", options=None, linestar_text=""):
    """The whole build. Returns plain dicts, so the CLI and the page share it."""
    o = options or {}
    notes = []

    def say(kind, text):
        notes.append({"type": kind, "text": text})

    players, rep = read_projections(proj_text or "")
    if rep.get("error"):
        return {"error": f"Projections file: {rep['error']}"}
    say("info", f"Projections: {rep['players']} players. Columns — "
                + ", ".join(f"{k} from “{v}”" for k, v in rep["matched"].items()))
    if rep.get("loose"):
        say("warn", "Columns matched by GUESS, not exact name — check these: "
                    + ", ".join(f"{k} <- “{h}”" for k, h in rep["loose"]))
    if "sd" not in rep["matched"]:
        say("warn", "No Std Dev column, so every player's spread is a stand-in "
                    f"(the larger of {E.SD_FLOOR:g} and {E.SD_SHARE:.0%} of "
                    "projection, WNBA's measured residual). The build runs; its "
                    "ceilings are guesses.")
    for key, what in (("duplicate_rows", "duplicate row(s), kept once"),
                      ("bad_proj", "non-numeric projection(s), read as 0"),
                      ("unknown_pos", "blank or unknown position(s), cannot be rostered")):
        if rep.get(key):
            say("warn", f"{len(rep[key])} {what}: " + ", ".join(rep[key][:6]))

    teams = sorted({p.team for p in players if p.team})
    if len(teams) == 2:
        for p in players:
            if not p.opponent and p.team:
                p.opponent = teams[1] if p.team == teams[0] else teams[0]

    dk = read_dk_entries(dk_text) if (dk_text or "").strip() else None

    fmt = o.get("format") or ""
    why = "you chose it"
    if fmt not in ("showdown", "classic"):
        if dk and dk.get("entries"):
            fmt = "showdown" if dk["slots"][0] == "CPT" else "classic"
            why = f"the DK file's slots are {', '.join(dk['slots'])}"
        else:
            fmt = "showdown" if len(teams) <= 2 else "classic"
            why = f"the projections cover {len(teams)} teams"
    say("info", f"Reading this as {'SHOWDOWN' if fmt == 'showdown' else 'CLASSIC'} "
                f"— {why}.")
    if fmt == "classic" and dk and dk.get("entries") and tuple(dk["slots"]) != CLASSIC_SLOTS:
        say("warn", f"The DK file's slots are {', '.join(dk['slots'])}, not DK's NBA "
                    f"classic {', '.join(CLASSIC_SLOTS)}. Is this the right export?")

    if dk:
        if not dk.get("pool"):
            return {"error": f"The DK file has {len(dk['entries'])} entry rows but no "
                             f"player pool, so it is an upload file, not DK's export. "
                             f"Download the entries file again — the one with the "
                             f"player list to the right of the entries.", "notes": notes}
        hit, miss, no_cpt = _attach_dk(players, dk)
        say("good", f"DK entries: {len(dk['entries'])} entries, {hit} player IDs matched.")
        if miss:
            live_miss = [n for n in miss if next((p for p in players if p.name == n)).proj > 0]
            if live_miss:
                say("warn", f"{len(live_miss)} projected player(s) have no DK match by "
                            f"name and cannot be used: " + ", ".join(live_miss[:10]))
        if hit < 0.8 * (hit + len(miss)):
            return {"error": f"Only {hit} of {hit + len(miss)} players matched the DK "
                             f"entries file — almost certainly the wrong slate's export.",
                    "notes": notes}
        if fmt == "showdown" and no_cpt:
            say("info", f"{len(no_cpt)} player(s) have no CAPTAIN id and will not be "
                        f"captained: " + ", ".join(no_cpt[:6]))
        players = [p for p in players if str(p.dk_id).isdigit()]
    if fmt == "classic":
        no_elig = [p.name for p in players if p.proj > 0 and not p.eligible]
        if no_elig:
            say("warn", f"{len(no_elig)} player(s) have no slot they can fill: "
                        + ", ".join(no_elig[:6]))

    games = sorted({p.game for p in players if p.game and p.proj > 0})

    # LineStar, if given: Vegas, its projection and its starting status, all
    # LOGGED and none of it used — see sources.read_linestar for why, and for
    # what NFL found when it measured LineStar's projection against Stokastic's.
    vegas = None
    if (linestar_text or "").strip():
        ls, lrep = read_linestar(linestar_text)
        if lrep.get("error"):
            say("warn", f"LineStar file: {lrep['error']}")
        elif lrep.get("is_results"):
            say("warn", "That LineStar file already has scores in it — it is a "
                        "post-game export. Nothing from it was logged.")
        else:
            vegas = lrep.get("teams") or None
            both, zeroed = [], []
            for p in players:
                rec = ls.get(normalize_name(p.name))
                if not rec:
                    continue
                p.ls_proj = rec.get("proj")
                p.ls_status = rec.get("status") or ""
                if p.proj > 0 and p.ls_proj is not None:
                    both.append((p.proj, p.ls_proj))
                    if p.ls_proj <= 0 or p.ls_status == "4":
                        zeroed.append(p)
            msg = (f"LineStar: Vegas for {len(vegas or {})} teams, and its projection "
                   f"for {len(both)} players, logged beside Stokastic's. Not used in "
                   f"the build.")
            if len(both) >= 10:
                mx = sum(a for a, _ in both) / len(both)
                my = sum(b for _, b in both) / len(both)
                cov = sum((a - mx) * (b - my) for a, b in both)
                va = sum((a - mx) ** 2 for a, _ in both) ** 0.5
                vb = sum((b - my) ** 2 for _, b in both) ** 0.5
                far = sum(1 for a, b in both if abs(a - b) >= 6)
                msg += (f" The two agree at r = {cov / (va * vb or 1):.2f}; "
                        f"{far} player(s) differ by 6+ points.")
            say("info", msg)
            if zeroed:
                # The one thing in the file worth saying before lock. Advisory:
                # the removal box is yours to use, the tool never removes anyone.
                say("warn", "LineStar has these at 0 or out while Stokastic still "
                            "projects them: " + ", ".join(
                                f"{p.name} ({p.proj:.1f})" for p in zeroed[:10])
                            + ". If one is a real scratch, put him in Late scratch.")

    # Pool and cores. Marked before removal, so a removed core is a known name.
    pool_names = read_sharp(o.get("pool") or "")
    core_names = read_sharp(o.get("cores") or "")
    known = {normalize_name(p.name) for p in players}
    for p in players:
        n = normalize_name(p.name)
        p.core = n in core_names
        p.in_pool = p.core or (n in pool_names)
        p._sheet = bool(pool_names)
    missed = sorted((pool_names | core_names) - known)
    if missed:
        say("warn", f"{len(missed)} name(s) on your sheet match no player on this slate "
                    f"and were ignored: " + ", ".join(missed[:12]))
    if pool_names or core_names:
        say("info", f"Sharp's sheet: {sum(1 for p in players if p.core)} cores, "
                    f"{sum(1 for p in players if p.in_pool)} in pool.")

    # Late scratches — the WNBA removal.
    removed, rem_missing = apply_removals(players, read_sharp(o.get("remove") or ""))
    if rem_missing:
        say("warn", "Not on this slate, so NOT removed: " + ", ".join(rem_missing))
    for r in removed:
        say("info", f"Removed {r['name']} (was {r['proj']} at ${r['salary']:,}); about "
                    f"{REMOVE_SHARE:.0%} of that production went to his teammates. "
                    f"A fresh Stokastic pull is the better fix when there is time.")

    by_id = {p.dk_id: p for p in players}
    by_name = {normalize_name(p.name): p for p in players}
    field, frep, bad_rows = [], {}, 0
    if (field_text or "").strip():
        raw, frep = read_field(field_text, by_id=by_id, by_name=by_name)
        if frep.get("error"):
            say("warn", f"Stokastic lineups file: {frep['error']}")
        elif frep.get("format") != fmt:
            say("warn", f"That lineups file is a {frep['format']} pool but this is a "
                        f"{fmt} build — ignoring it. Download the right slate's lineups.")
        else:
            field, bad_rows = _field_lineups(raw, fmt)
            say("good", f"Stokastic lineups: {len(field):,} of {frep['rows']:,} read as "
                        f"the opponent field (roster columns: "
                        f"{', '.join(frep.get('roster_columns') or [])}).")
            if frep.get("unresolved_rosters"):
                say("warn", f"{frep['unresolved_rosters']:,} of their lineups hold a player "
                            f"the projections do not list, so they were skipped.")

    # Ownership sanity: the column must sum to the slot count, or the wrong
    # column was read and every duplication figure downstream is wrong.
    live = [p for p in players if p.salary > 0]
    tot_own = sum(p.ownership for p in live)
    if fmt == "classic":
        ok_own = 700 <= tot_own <= 900
        say("good" if ok_own else "warn",
            f"Ownership: {tot_own:.0f}% across the slate — expected about 800 for "
            f"eight roster slots." + ("" if ok_own else " Check which column was read."))
    else:
        cpt_tot = sum(p.cpt_own for p in live)
        say("info", f"Ownership: {tot_own:.0f}% UTIL + {cpt_tot:.0f}% captain. Expected "
                    f"about 500 + 100; NBA showdown's Stokastic columns are not yet "
                    f"confirmed from a real file, so check this against it.")

    sims = _i(o.get("sims"), 4000)
    seed = _i(o.get("seed"), 0)
    n = max(1, _i(o.get("n"), 150))
    split = o.get("split")
    split = None if split in (None, "") else _i(split, 0)
    if dk and dk.get("entries"):
        per = {}
        for e in dk["entries"]:
            per[e["contest_id"]] = per.get(e["contest_id"], 0) + 1
        biggest = max(per.values())
        if biggest < n:
            say("warn", f"The biggest contest in the DK file holds {biggest} entries, so "
                        f"building {biggest} lineups, not {n}.")
            if split:
                split = max(1, round(split * biggest / n))
            n = biggest
            if split is not None:
                split = min(split, n)

    mat = E.simulate(players, sims=sims, seed=seed)
    bar, sampled = E.field_bar(field, mat, sims, seed=seed) if field else (None, 0)
    idx = E.dupe_index(field) if field else {}
    modelled = E.field_size(field) if field else 0
    field_cap = _i(o.get("fieldCap"), 0)
    fill_pct = min(max(_f(o.get("fillPct"), 100.0), 1.0), 100.0)
    expect = _i(o.get("expectEntries"), 0) or int(round(field_cap * fill_pct / 100.0))
    dupe_scale = max(1.0, expect / modelled) if (modelled and expect) else 1.0
    if bar:
        srt = sorted(bar)
        say("info", f"Score to beat (the field's 99th percentile, {sampled:,} sampled "
                    f"opponents): {srt[len(srt) // 2]:.0f} median across simulations.")
        say("info", f"Stokastic's pool models {modelled:,.0f} opponent entries in "
                    f"{len(idx):,} distinct lineups.")
        if expect:
            say("info", f"Scaling duplication ×{dupe_scale:.2f} for a {expect:,}-entry "
                        f"field.")
        else:
            say("warn", "No contest size given, so duplication is measured against the "
                        "field Stokastic models, which is smaller than a real contest. "
                        "The $0.50 150-max main slate has run 47,000-71,000 entries.")
    else:
        say("warn", "No Stokastic lineups file, so there is no opponent field: lineups "
                    "are ranked on simulated mean, with no win rate and no real "
                    "duplication estimate. That is a much weaker build.")

    min_proj = _f(o.get("minProj"), E.MIN_PROJ)
    raw_off = o.get("maxOffPool")
    if not pool_names:
        off_pool = None
    elif raw_off is None or raw_off == "":
        off_pool = 0
    elif isinstance(raw_off, str) and raw_off.lower() in ("none", "off", "nolimit"):
        off_pool = None
    else:
        off_pool = _i(raw_off, 0)
    if pool_names and off_pool == 0 and fmt == "classic":
        short = _pool_short(players, min_proj)
        if short:
            off_pool = None
            say("warn", f"Your pool cannot fill {', '.join(short)}, so it is treated "
                        f"as a shortlist rather than a hard filter. Cores still get "
                        f"their guaranteed share.")
    if pool_names and off_pool == 0 and fmt == "showdown":
        if sum(1 for p in players if p.in_pool and p.proj >= min_proj) < SD_ROSTER_SIZE:
            off_pool = None
            say("warn", "Your pool holds fewer than six playable names — treating it "
                        "as a shortlist.")
    if pool_names and off_pool is not None:
        say("info", "Pool is a build constraint: " + ("every player must come from it."
                    if not off_pool else f"up to {off_pool} off-pool player(s) per lineup."))
    if pool_names:
        gaps = _pool_gaps_note(players, mat, sims, min_proj)
        if gaps:
            shown = gaps[:GAP_SHOW]
            say("good", f"Pool gaps — {len(gaps)} play(s) with real upside for the "
                        f"position and ownership that lags it, NOT on your sheet: "
                        + "; ".join(f"{p.name} ({up:.0f} ceiling, {p.ownership:.0f}% "
                                    f"owned, ${p.salary:,})" for p, up in shown)
                        + ". Adding to the POOL only makes him legal; make him a CORE "
                          "to guarantee he is used.")

    caps = {"player_cap": _share(o.get("playerCap"), E.PLAYER_CAP)}
    pcaps, cap_missing, cap_bad = _player_caps(o.get("capPlayers"), players)
    if pcaps:
        caps["player_caps"] = pcaps
        say("info", "Per-player caps in force — " + ", ".join(
            f"{p.name} {round(pcaps[p.dk_id] * 100)}%" for p in players if p.dk_id in pcaps))
    if cap_missing:
        say("warn", "No player on this slate matches these caps, so they are NOT in "
                    "force: " + "; ".join(cap_missing))
    if cap_bad:
        say("warn", "These cap lines need a name and a percentage — ignored: "
                    + "; ".join(cap_bad))
    if fmt == "showdown":
        caps["captain_cap"] = _share(o.get("captainCap"), E.CAPTAIN_CAP)
    max_leftover = _i(o.get("maxLeftover"),
                      E.SD_MAX_LEFTOVER if fmt == "showdown" else C.MAX_LEFTOVER)

    n_mine = n if split is None else max(0, min(split, n))
    n_vendor = n - n_mine
    chosen, cands = [], []
    core_ids = [p.dk_id for p in players if p.core and p.proj > 0]
    floors = {cid: max(1, -(-n_mine // (len(core_ids) + 1))) for cid in core_ids} \
        if core_ids and n_mine else None
    floors_total = {cid: max(1, -(-n // (len(core_ids) + 1))) for cid in core_ids} \
        if core_ids else None

    def build(off, leftover):
        rng = __import__("random").Random(seed)
        count = max(4000, n_mine * 30)
        if fmt == "showdown":
            cpt_pool = [p for p in players if p.proj >= min_proj and p.salary > 0
                        and (p.cpt_dk_id or not dk)]
            return E.build_showdown(players, count, rng=rng, max_off_pool=off,
                                    cpt_pool=cpt_pool or None, max_leftover=leftover,
                                    min_proj=min_proj)
        return C.build_candidates(players, count, rng=rng, max_off_pool=off,
                                  min_proj=min_proj, max_leftover=leftover)

    if n_mine or n_vendor:
        cands = build(off_pool, max_leftover)
        if not cands and off_pool is not None:
            cands = build(None, max_leftover)
            if cands:
                say("warn", "Your pool could not produce a single legal roster, so it "
                            "was treated as a shortlist.")
        if not cands and max_leftover is not None:
            cands = build(off_pool, None)
            if cands:
                say("warn", f"No roster could spend within ${max_leftover:,} of the cap, "
                            f"so the leftover filter was dropped for this slate.")
        if not cands:
            return {"error": "Built no legal lineups. Check salaries, positions and "
                             "teams in the projections file.", "notes": notes}
        E.rank(cands, mat, bar, sims, idx, own_lean=_f(o.get("ownLean"), E.OWN_LEAN),
               dupe_scale=dupe_scale, field_n=modelled)
        if n_mine:
            if floors:
                say("info", f"Each of your {len(core_ids)} core(s) is guaranteed at least "
                            f"{next(iter(floors.values()))} of {n_mine} lineups.")
            chosen += E.select(cands, n_mine, core_floors=floors, **caps)
            say("info", f"Built {len(chosen)} lineups from {len(cands):,} candidates.")

    if n_vendor:
        vfield = [lu for lu in field if _enterable(lu, fmt)]
        if fmt == "showdown" and dk:
            vfield = [lu for lu in vfield if lu.cpt.cpt_dk_id]
        if off_pool is not None:
            vfield = [lu for lu in vfield
                      if sum(1 for p in lu.players if not p.in_pool and not p.core) <= off_pool]
        if vfield:
            chosen += E.vendor_arm(vfield, n_vendor, dupe_scale=dupe_scale,
                                   core_floors=floors_total, prior=chosen, **caps)
        else:
            say("warn", "No usable vendor lineups for their half of the split "
                        + ("(no lineups file)" if not field else
                           "(none fit your pool and today's scratches)")
                        + f", so all {n} come from our own builder.")
    if len(chosen) < n and cands:
        need = n - len(chosen)
        chosen += E.select(cands, need, core_floors=floors_total, prior=chosen, **caps)
    if not chosen:
        return {"error": "No lineups produced.", "notes": notes}
    if len(chosen) < n:
        say("warn", f"Only {len(chosen)} distinct lineups for {n} entries — the rest "
                    f"will have no lineup and score zero.")

    # Checked on the finished set, never assumed: caps, core floors, duplicates.
    if pcaps:
        real = {}
        for lu in chosen:
            for p in lu.players:
                real[p.dk_id] = real.get(p.dk_id, 0) + 1
        over = [(p, real.get(p.dk_id, 0), int(round(pcaps[p.dk_id] * len(chosen))))
                for p in players if p.dk_id in pcaps
                and real.get(p.dk_id, 0) > int(round(pcaps[p.dk_id] * len(chosen)))]
        if over:
            say("warn", "The board could not fill the set under your caps, so these ran "
                        "over: " + ", ".join(f"{p.name} {g} of {len(chosen)} against {w}"
                                             for p, g, w in over))
    coach = _coach(players, chosen, mat, sims, removed, pool_names, off_pool,
                   floors_total)
    if core_ids:
        want = max(1, -(-len(chosen) // (len(core_ids) + 1)))
        short = [f"{p.name} is in {sum(1 for lu in chosen if p.dk_id in lu.ids())} of "
                 f"{len(chosen)}, not {want}" for p in players
                 if p.core and p.proj > 0
                 and sum(1 for lu in chosen if p.dk_id in lu.ids()) < want]
        say("warn" if short else "good",
            ("A core could not be given its full share — " + "; ".join(short))
            if short else f"Every core is in at least {want} of {len(chosen)} entries.")
    keys = [lu.key() for lu in chosen]
    if len(keys) != len(set(keys)):
        say("warn", f"{len(keys) - len(set(keys))} entries duplicate another exactly — do "
                    f"not upload until that is looked at.")
    else:
        say("good", f"All {len(chosen)} entries are distinct rosters.")
    heavy = sorted(((sum(1 for lu in chosen if p.dk_id in lu.ids()), p.name)
                    for p in players if p.proj > 0), reverse=True)
    heavy = [(c, nm) for c, nm in heavy if c >= 0.7 * len(chosen)][:3]
    if heavy:
        say("info", "Heavy exposure: " + ", ".join(f"{nm} {c} of {len(chosen)}"
                                                   for c, nm in heavy)
                    + ". That is the build following the projections, not a bug — "
                      "type a per-player cap if you want it reined in.")
    if field:
        f_own = sorted(lu.own_sum for lu in field)
        mine_own = sum(lu.own_sum for lu in chosen) / len(chosen)
        pct = 100.0 * sum(1 for v in f_own if v < mine_own) / len(f_own)
        say("info", f"Ownership: your lineups average {mine_own:.0f}% against the field's "
                    f"{f_own[len(f_own) // 2]:.0f}% median — higher than {pct:.0f}% of "
                    f"the field.")

    # ---- the upload file ----
    slots = list(dk["slots"]) if dk and dk.get("entries") else (
        ["CPT"] + ["UTIL"] * 5 if fmt == "showdown" else list(CLASSIC_SLOTS))
    start_of = (lambda p: p.start or "") if dk else None
    rostered_all = [_roster(lu, fmt, start_of) for lu in chosen]
    settings = {"n": n, "split": split, "sims": sims, "seed": seed, "format": fmt,
                "ownLean": _f(o.get("ownLean"), E.OWN_LEAN),
                "playerCap": caps["player_cap"], "captainCap": caps.get("captain_cap"),
                "minProj": min_proj, "maxLeftover": max_leftover,
                "maxOffPool": off_pool, "cores": sorted(core_names),
                "pool": sorted(pool_names), "capPlayers": o.get("capPlayers") or ""}
    meta = {"slate": datetime.now().astimezone().date().isoformat(), "games": games,
            "format": fmt, "slots": slots, "settings": settings,
            "removed": [r["name"] for r in removed],
            "projection": {"source": "stokastic", "columns": rep["matched"],
                           "sd_from_file": "sd" in rep["matched"],
                           "game_sd": E.GAME_SD, "dupe_exp": E.DUPE_EXP,
                           "fill_exp": E.FILL_EXP},
            "contest_state": {"field_cap": field_cap or None, "fill_pct": fill_pct,
                              "expect_entries": expect or None,
                              "vendor_field_modelled": modelled,
                              "dupe_scale": round(dupe_scale, 3),
                              "vegas": vegas}}
    dk_csv, log_lus, log_rosters = None, chosen, rostered_all
    if dk and dk.get("entries"):
        if len(slots) != len(rostered_all[0]):
            say("warn", f"The DK file has {len(slots)} roster slots but a {fmt} lineup "
                        f"has {len(rostered_all[0])} — no upload file written.")
        else:
            contests = _contests(dk["entries"])
            if len(contests) > 1:
                say("info", f"{len(contests)} contests in this DK file; each gets the top "
                            f"of the same ranked list: "
                            + "; ".join(f"{len(es)} entries in {nm or cid}"
                                        for cid, nm, es in contests))
            for cid, nm, es in contests:
                if len(es) > len(chosen):
                    say("warn", f"{nm or cid} has {len(es)} entries but only "
                                f"{len(chosen)} lineups were built.")
            pairs = _assign(contests, list(zip(chosen, rostered_all)))
            ents = [e for e, _ in pairs]
            log_lus = [lr[0] for _, lr in pairs]
            log_rosters = [lr[1] for _, lr in pairs]
            problems = _check_upload(ents, log_rosters, slots, fmt, dk)
            if problems:
                say("warn", f"NOT writing an upload file — {len(problems)} problem(s) "
                            f"DraftKings would reject:")
                for line in problems[:12]:
                    say("warn", "    " + line)
            else:
                dk_csv = _dk_rows(ents, log_rosters,
                                  "Entry ID,Contest Name,Contest ID,Entry Fee,"
                                  + ",".join(slots), fmt)
                say("good", f"Upload file checked: {len(ents)} rows, every player ID "
                            f"valid for its slot, no repeats, none over the cap.")
                meta["entry_ids"] = [e["entry_id"] for e in ents]
                meta["contest_ids"] = [e["contest_id"] for e in ents]
    else:
        say("warn", "No DK entries file, so there is no uploadable CSV — that export is "
                    "the only source of your Entry IDs and DK's player IDs.")
    err = _log(log_lus, log_rosters, meta)
    if err:
        say("warn", err + " Grading reads that log, so this night will not be gradable.")

    shapes, heads = {}, {}
    for lu in chosen:
        k = lu.shape_label()
        shapes[k] = shapes.get(k, 0) + 1
        if fmt == "showdown":
            heads[lu.cpt.name] = heads.get(lu.cpt.name, 0) + 1
    arms = {}
    for lu in chosen:
        arms[lu.source] = arms.get(lu.source, 0) + 1
    return {
        "notes": notes, "coach": coach, "format": fmt, "teams": teams, "games": games,
        "summary": {
            "n": len(chosen), "arms": arms,
            "shapeLabel": "team split" if fmt == "showdown" else "biggest game / team block",
            "splits": dict(sorted(shapes.items(), key=lambda kv: -kv[1])[:8]),
            "captains": len(heads),
            "topCaptains": sorted(heads.items(), key=lambda kv: -kv[1])[:6],
            "salaryLo": min(lu.salary for lu in chosen),
            "salaryHi": max(lu.salary for lu in chosen),
            "projAvg": round(sum(lu.proj for lu in chosen) / len(chosen), 1),
            "ownAvg": round(sum(lu.own_sum for lu in chosen) / len(chosen), 1),
            "dupeAvg": round(sum(lu.metrics.get("dupes", 0) for lu in chosen)
                             / len(chosen), 2),
        },
        "exposure": _exposure(chosen, fmt),
        "lineups": [_lineup_payload(lu, r, fmt, slots) for lu, r in zip(chosen, rostered_all)],
        "dkCsv": dk_csv,
        "logPath": LOG_PATH,
    }


def _lineup_payload(lu, roster, fmt, slots):
    return {
        "source": lu.source, "shape": lu.shape_label(),
        "salary": lu.salary, "leftover": SALARY_CAP - lu.salary,
        "proj": lu.proj, "ownSum": lu.own_sum,
        "win": round(lu.metrics.get("win", 0.0), 5),
        "dupes": round(lu.metrics.get("dupes", 0.0), 2),
        "players": [{"slot": s, "name": p.name, "team": p.team, "pos": p.pos,
                     "salary": p.cpt_salary() if s == "CPT" else p.salary,
                     "proj": round(p.proj * (1.5 if s == "CPT" else 1.0), 1),
                     "own": round(p.cpt_own if s == "CPT" else p.ownership, 1),
                     "core": p.core, "pool": p.in_pool, "off": _off_sheet(p)}
                    for s, p in zip(slots, roster)],
    }


# ---------------- grading ----------------
def grade(standings_text, slate=None, ladder=None, log_path=None):
    """Score logged entries against DK's contest standings. -> report dict

    The standings file is the whole answer on NBA: it holds every entry's rank
    and points, every player's actual FPTS and real %Drafted, and how many
    entries held each roster — the only measurement of duplication that is not
    a model of itself. The parse is checked by rebuilding each entry's Points
    from its players' FPTS; if those do not agree, nothing below is trusted.

    Also produces the one table the old NBA chats said they never had: for every
    player we used, projected vs actual points and projected vs actual
    ownership, next to our exposure.
    """
    stand, srep = read_standings(standings_text or "")
    if srep.get("error"):
        return {"error": f"Standings file: {srep['error']}"}
    actual = srep["actual"]
    spts = srep["points_sorted"]
    try:
        with open(log_path or LOG_PATH, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
    except OSError:
        return {"error": "No build log yet — nothing to grade."}
    by_entry = {e["entry_id"]: e for e in stand if e.get("entry_id")}
    if slate:
        rows = [r for r in rows if r.get("slate") == slate]
    # WHICH build was entered. Rebuilding during the day logs several builds
    # against the same Entry IDs, and the latest is not necessarily the one
    # uploaded. The standings say what DK actually holds for each entry, so pick
    # the build whose rosters match it most often (ties -> latest). Grading a
    # build that was not entered measures the log, not the tool.
    bid = lambda r: r.get("build_id") or r.get("ts")
    stamps = sorted({bid(r) for r in rows if bid(r)}, reverse=True)
    pick, best_match, roster_match = None, -1, 0
    for ts in stamps:
        grp = [r for r in rows if bid(r) == ts
               and str(r.get("entry_id") or "") in by_entry]
        if not grp:
            continue
        same = sum(1 for r in grp
                   if set(by_entry[str(r["entry_id"])]["names"])
                   == {normalize_name(p["name"]) for p in r.get("players") or []})
        if same > best_match:
            pick, best_match, roster_match = grp, same, same
    if pick is None:
        for ts in stamps:
            grp = [r for r in rows if bid(r) == ts]
            names = {normalize_name(p["name"]) for r in grp for p in r.get("players") or []}
            if names and len(names & set(actual)) / len(names) >= 0.9:
                pick = grp
                break
    if pick is None:
        return {"error": "No logged build matches that standings file."}
    fmt = pick[0].get("format", "classic")
    graded, unknown = [], set()
    for r in pick:
        ps = r.get("players") or []
        total, ok = 0.0, True
        for p in ps:
            k = normalize_name(p["name"])
            if k not in actual:
                unknown.add(p["name"])
                ok = False
                break
            total += actual[k] * (1.5 if p.get("slot") == "CPT" else 1.0)
        if not ok:
            continue
        g = {"score": round(total, 2), "source": r.get("source"),
             "shape": r.get("shape"), "entry_id": r.get("entry_id"),
             "head": r.get("captain"), "proj": r.get("proj"), "own": r.get("own_sum")}
        hit = by_entry.get(str(r.get("entry_id") or ""))
        g["rank"] = hit["rank"] if hit else max(1, len(spts) - bisect.bisect_left(spts, total))
        g["pct"] = round(100.0 * g["rank"] / len(spts), 2)
        if ladder:
            g["paid"] = round(ladder(g["rank"]), 2)
            g["cashed"] = g["paid"] > 0
        labels = ["CPT"] + ["UTIL"] * 5 if fmt == "showdown" else None
        g["copies"] = srep["copies"].get(
            _roster_key([normalize_name(p["name"]) for p in ps], labels), 0)
        graded.append(g)
    if not graded:
        return {"error": "Could not score any logged entry from that file."}
    graded.sort(key=lambda g: -g["score"])

    def stat(rs):
        sc = sorted((g["score"] for g in rs), reverse=True)
        rk = sorted(g["rank"] for g in rs)
        out = {"n": len(sc), "best": sc[0], "median": sc[len(sc) // 2],
               "mean": round(sum(sc) / len(sc), 2), "best_rank": rk[0],
               "median_pct": round(100.0 * rk[len(rk) // 2] / len(spts), 1)}
        cash = [g for g in rs if g.get("cashed") is not None]
        if cash:
            out["cashed"] = sum(1 for g in cash if g["cashed"])
            out["paid"] = round(sum(g.get("paid") or 0.0 for g in cash), 2)
        cp = sorted(g["copies"] for g in rs)
        out["copies_median"], out["copies_max"] = cp[len(cp) // 2], cp[-1]
        return out

    arms, shapes = {}, {}
    for g in graded:
        arms.setdefault(g["source"] or "?", []).append(g)
        shapes.setdefault(g["shape"] or "?", []).append(g)
    # Player table: projected vs actual, projected vs real ownership, exposure.
    expo, info = {}, {}
    for r in pick:
        for p in r.get("players") or []:
            k = normalize_name(p["name"])
            expo[k] = expo.get(k, 0) + 1
            info[k] = p
    players = []
    for k, c in sorted(expo.items(), key=lambda kv: -kv[1]):
        p = info[k]
        real_own = srep["ownership"].get(k)
        players.append({"name": p["name"], "exposure": round(100.0 * c / len(pick), 1),
                        "proj": p.get("proj"), "actual": actual.get(k),
                        "gap": (round(actual[k] - p["proj"], 1)
                                if k in actual and p.get("proj") is not None else None),
                        "own_proj": p.get("own"),
                        "own_real": round(real_own, 1) if real_own is not None else None})
    return {
        "slate": pick[0].get("slate"), "format": fmt, "entries": len(graded),
        "parse_check": {"checked": srep["points_checked"], "agree": srep["points_agree"],
                        "unreadable_rosters": srep["unreadable"]},
        "contest": {"field": len(spts), "winning_score": spts[-1],
                    "median_score": spts[len(spts) // 2],
                    "matched_by_entry_id": sum(1 for r in pick
                                               if str(r.get("entry_id") or "") in by_entry),
                    # Entries whose logged roster IS what DK holds. Short of
                    # the matched count means rosters changed after the build
                    # (a hand edit or a swap), and those rows grade a lineup
                    # that was never played.
                    "roster_match": roster_match},
        "overall": stat(graded),
        "by_arm": {k: stat(v) for k, v in sorted(arms.items())},
        "by_shape": {k: stat(v) for k, v in sorted(shapes.items())},
        "players": players,
        "top": graded[:5],
        "unscored": sorted(unknown)[:8],
    }


# ---------------- file checks for the page ----------------
def _describe(kind, text):
    """Read a dropped file just far enough to say what it is."""
    if not (text or "").strip():
        return {"ok": False, "msg": "that file is empty"}
    try:
        if kind == "proj":
            players, rep = read_projections(text)
            if rep.get("error"):
                return {"ok": False, "msg": f"not a projections export ({rep['error']})"}
            live = [p for p in players if p.proj > 0]
            teams = sorted({p.team for p in live if p.team})
            if not live:
                return {"ok": False, "msg": "no projected players in that file"}
            return {"ok": True, "format": "showdown" if len(teams) <= 2 else "classic",
                    "msg": f"{len(live)} players, " + (" @ ".join(teams) if len(teams) <= 2
                                                       else f"{len(teams) // 2} games")}
        if kind == "field":
            _e, rep = read_field(text)
            if rep.get("error"):
                return {"ok": False, "msg": f"not a lineups export ({rep['error']})"}
            return {"ok": True, "format": rep.get("format"),
                    "msg": f"{rep['rows']:,} opponent lineups ({rep.get('format')})"}
        if kind == "dk":
            dk = read_dk_entries(text)
            if not dk["pool"]:
                return {"ok": False, "msg": "no player pool — is this DK's entries export?"}
            if not dk["entries"]:
                return {"ok": False, "msg": f"{len(dk['pool'])} players but no entries — "
                                            f"enter the contest on DK first"}
            sd = dk["slots"][0] == "CPT"
            return {"ok": True, "format": "showdown" if sd else "classic",
                    "msg": f"{len(dk['entries'])} entries, {len(dk['pool'])} players "
                           f"({'showdown' if sd else 'classic'}), "
                           f"{len(_contests(dk['entries']))} contest(s)"}
        if kind == "linestar":
            _ls, rep = read_linestar(text)
            if rep.get("error"):
                return {"ok": False, "msg": f"not a LineStar export ({rep['error']})"}
            if rep.get("is_results"):
                return {"ok": False, "msg": "that is a post-game LineStar file — grade "
                                            "with the DK standings instead"}
            return {"ok": True, "msg": f"{rep['players']} players, Vegas for "
                                       f"{len(rep['teams'])} teams (logged only)"}
        if kind == "standings":
            rows, rep = read_standings(text)
            if rep.get("error"):
                return {"ok": False, "msg": rep["error"]}
            return {"ok": True, "msg": f"{rep['entries']:,} entries, parse check "
                                       f"{rep['points_agree']}/{rep['points_checked']}"}
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
        if self.path not in ("/api/build", "/api/players", "/api/check", "/api/grade"):
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            p = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/players":
                players, rep = read_projections(p.get("proj") or "")
                if rep.get("error"):
                    return self._send(400, json.dumps({"error": rep["error"]}))
                teams = sorted({q.team for q in players if q.team})
                return self._send(200, json.dumps({
                    "teams": teams, "format": "showdown" if len(teams) <= 2 else "classic",
                    "players": [{"name": q.name, "team": q.team, "pos": q.pos,
                                 "salary": q.salary, "proj": round(q.proj, 1),
                                 "own": round(q.ownership, 1)}
                                for q in sorted(players, key=lambda x: -x.proj) if q.proj > 0]}))
            if self.path == "/api/grade":
                lad = payout_ladder(p.get("prizePool"), p.get("firstPrize"),
                                    p.get("paidFrom"), p.get("paidTo"))
                g = grade(p.get("standings") or "", p.get("slate"), lad)
                return self._send(400 if g.get("error") else 200, json.dumps(g))
            if self.path == "/api/check":
                return self._send(200, json.dumps(
                    _describe(p.get("kind") or "", p.get("text") or "")))
            if not (p.get("proj") or "").strip():
                return self._send(400, json.dumps({"error": "Drop the Stokastic "
                                                            "projections CSV first."}))
            result = run_build(p.get("proj") or "", p.get("field") or "",
                               p.get("dk") or "", p.get("options") or {},
                               linestar_text=p.get("linestar") or "")
            self._send(400 if result.get("error") else 200, json.dumps(result))
        except Exception as exc:                             # noqa: BLE001
            self._send(500, json.dumps({"error": str(exc)}))


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"NBA optimizer (classic and showdown) — {url}")
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
    ap = argparse.ArgumentParser(description="DK NBA lineup builder — classic and showdown")
    ap.add_argument("--proj", help="Stokastic projections CSV")
    ap.add_argument("--field", help="Stokastic lineups CSV (the opponent field)")
    ap.add_argument("--dk", help="DK entries export (needed for an upload file)")
    ap.add_argument("--linestar", help="LineStar export — Vegas, projection and "
                                       "starting status, logged only")
    ap.add_argument("--grade", help="DK contest standings (.csv or .zip): score the "
                                    "logged build against it")
    ap.add_argument("--grade-slate", help="which logged slate to grade (YYYY-MM-DD)")
    ap.add_argument("--prize-pool", type=float)
    ap.add_argument("--first-prize", type=float)
    ap.add_argument("--paid-from", type=int, help="first rank paying the minimum")
    ap.add_argument("--paid-to", type=int, help="last paid rank")
    ap.add_argument("--pool", help="sharp's pool file")
    ap.add_argument("--cores", help="sharp's cores file")
    ap.add_argument("--remove", help="late scratches, comma-separated names")
    ap.add_argument("--caps", help="per-player caps, 'Name 20; Name 40'")
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--split", type=int, default=None,
                    help="entries from OUR builder; the rest come from Stokastic's "
                         "re-ranked pool. Omitted = all ours, 0 = all theirs.")
    ap.add_argument("--sims", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--format", choices=("showdown", "classic"), default=None)
    ap.add_argument("--own-lean", type=float, default=None)
    ap.add_argument("--player-cap", type=float, default=None)
    ap.add_argument("--captain-cap", type=float, default=None)
    ap.add_argument("--max-leftover", type=int, default=None)
    ap.add_argument("--min-proj", type=float, default=None)
    ap.add_argument("--max-off-pool", default=None)
    ap.add_argument("--field-cap", type=int, default=None, help="contest max entries")
    ap.add_argument("--fill-pct", type=float, default=100.0)
    ap.add_argument("--out", default="nba_upload.csv")
    a = ap.parse_args(argv)

    if a.grade:
        lad = payout_ladder(a.prize_pool, a.first_prize, a.paid_from, a.paid_to)
        g = grade(_read(a.grade), a.grade_slate, lad)
        if g.get("error"):
            print(f"ERROR: {g['error']}")
            return 2
        pc = g["parse_check"]
        print(f"\n== {g['entries']} entries graded — {g['slate']} ({g['format']}) ==")
        print(f"  parse check: {pc['agree']} of {pc['checked']} entries' Points rebuilt "
              f"from player FPTS; {pc['unreadable_rosters']} rosters unreadable")
        c = g["contest"]
        print(f"  contest {c['field']:,} entries, winner {c['winning_score']:.1f}, "
              f"median {c['median_score']:.1f}")
        print(f"  entries: {c['matched_by_entry_id']} found by Entry ID, "
              f"{c['roster_match']} of them exactly as built"
              + ("" if c["roster_match"] == c["matched_by_entry_id"] else
                 " — the rest changed after the build, so they grade a lineup "
                 "that was not played"))
        for label, block in (("overall", {"all": g["overall"]}), ("by arm", g["by_arm"]),
                             ("by shape", g["by_shape"])):
            print(f"  {label}:")
            for k, v in sorted(block.items(), key=lambda kv: kv[1]["best_rank"]):
                money = (f"  cashed {v['cashed']}  ${v['paid']:,.2f}"
                         if "cashed" in v else "")
                print(f"    {str(k):<18} n={v['n']:<4} best #{v['best_rank']:,}  "
                      f"median {v['median_pct']:.0f}%  copies {v['copies_median']}/"
                      f"{v['copies_max']}{money}")
        print("  players (exposure · proj → actual · proj own → real own):")
        for p in g["players"][:20]:
            print(f"    {p['name'][:22]:<23}{p['exposure']:>5.0f}%  "
                  f"{p['proj']!s:>6} → {p['actual']!s:<6} {p['own_proj']!s:>5}% → "
                  f"{p['own_real']!s}%")
        return 0

    if not a.proj:
        return serve()
    res = run_build(_read(a.proj), _read(a.field), _read(a.dk), {
        "pool": _read(a.pool), "cores": _read(a.cores), "remove": a.remove or "",
        "capPlayers": a.caps or "", "n": a.n, "split": a.split, "sims": a.sims,
        "seed": a.seed, "format": a.format, "ownLean": a.own_lean,
        "playerCap": a.player_cap, "captainCap": a.captain_cap,
        "maxLeftover": a.max_leftover, "minProj": a.min_proj,
        "maxOffPool": a.max_off_pool, "fieldCap": a.field_cap, "fillPct": a.fill_pct},
        linestar_text=_read(a.linestar))
    for note in res.get("notes", []):
        print(f"  [{note['type']}] {note['text']}")
    if res.get("error"):
        print(f"\nERROR: {res['error']}")
        return 2
    if res.get("coach"):
        print("\n  -- the read on this build --")
        for note in res["coach"]:
            print(f"  [{note['type']}] {note['text']}")
    s = res["summary"]
    print(f"\n== {s['n']} lineups ({res['format']}) ==  arms {s['arms']}")
    print(f"  salary {s['salaryLo']}-{s['salaryHi']}   proj avg {s['projAvg']}   "
          f"own avg {s['ownAvg']}   dupes avg {s['dupeAvg']}")
    for p in (res.get("exposure") or {}).get("players", [])[:20]:
        print(f"    {p['name'][:22]:<23}{p['team']:<5}{p['pct']:>6.1f}%  field "
              f"{p['own']:>5.1f}%  edge {p['edge']:+.1f}")
    if res.get("dkCsv"):
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(res["dkCsv"])
        print(f"\n  wrote {a.out}")
    print(f"  logged to {res['logPath']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
