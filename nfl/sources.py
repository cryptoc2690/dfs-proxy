"""Readers for the files a build runs on — Stokastic's two exports, the DK
entries export, an optional LineStar export, and the sharp's sheet.

Column names are sniffed rather than hard-coded, because Stokastic's exports
have moved before and one renamed header should not take the tool down on a
Sunday morning. Every reader reports what it matched so a wrong guess is
visible instead of silent.

Keys are DK player IDs wherever a file carries them. The brief lists the name
joins that break otherwise: Kyle Pitts Sr., Michael Pittman Jr., A.J. vs AJ
Brown, Travis Etienne Jr., and the DST nickname convention.
"""

from __future__ import annotations

import csv
import io
import re

from dk import Player, normalize_name

# --- column sniffing -----------------------------------------------------
# Each entry: canonical name -> candidate header fragments, best first. Match
# is case-insensitive on the stripped header with punctuation removed.
PROJ_COLUMNS = {
    "name":      ["name", "player", "playername"],
    "pos":       ["pos", "position"],
    "team":      ["team", "teamabbrev", "tm"],
    "opp":       ["opp", "opponent"],
    "salary":    ["salary", "sal", "dksalary"],
    "proj":      ["projectedfp", "projfp", "projection", "projpts", "proj", "fpts",
                  "projectedpoints"],
    "own":       ["ownership%", "ownership", "own%", "own", "projown",
                  "projectedownership", "pown"],
    "sd":        ["stddev", "standarddeviation", "sd", "stdev", "sigma"],
    "boom":      ["boom", "boompct", "boom%"],
    "cpt_own":   ["projectedcptownership", "cptownership", "captainownership",
                  "cptown", "captainown"],
    "cpt_opt":   ["cptoptimal%", "cptoptimal", "captainoptimal", "cptopt", "optimalcpt"],
    "dk_id":     ["dkid", "playerid", "id"],
}

# Headers a canonical field must NOT contain. This exists because of the
# showdown captain trap: a plain substring match for "ownership" happily binds
# to "CPT Ownership", which is a different quantity on a different denominator
# (captain shares sum to 100%, all shares to 600%). Reading the wrong one
# silently poisons every ownership and duplication figure downstream.
PROJ_EXCLUDE = {
    "own":    ["cpt", "captain"],
    "salary": ["cpt", "captain"],
    "proj":   ["cpt", "captain"],
    "pos":    ["roster"],
}


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9%]", "", (h or "").lower())


def _match_columns(headers, spec, exclude=None):
    """-> ({canonical: actual header}, [unmatched canonical names])

    Exact match wins, then prefix, then substring — and headers containing an
    excluded fragment are never eligible. Without the ordering, a short
    canonical name like "own" binds to whatever long header happens to contain
    it.
    """
    exclude = exclude or {}
    norm = {_norm_header(h): h for h in headers}
    found, missing, loose = {}, [], []
    bound = set()          # a header serves ONE field — see below
    for key, candidates in spec.items():
        bad = exclude.get(key, [])
        allowed = {n: h for n, h in norm.items()
                   if not any(b in n for b in bad) and h not in bound}
        hit, exact = None, True
        for c in candidates:
            if c in allowed:
                hit = allowed[c]
                break
        if hit is None:
            exact = False
            for c in candidates:
                pre = [h for n, h in allowed.items() if n.startswith(c)]
                if pre:
                    hit = min(pre, key=len)
                    break
        if hit is None:
            for c in candidates:
                sub = [h for n, h in allowed.items() if c in n]
                if sub:
                    hit = min(sub, key=len)
                    break
        if hit:
            # Never let two fields read one column. A vendor rename to
            # "Proj Own" used to bind BOTH projection and ownership to it,
            # turning every projection into an ownership number with no
            # warning anywhere.
            found[key] = hit
            bound.add(hit)
            if not exact:
                loose.append((key, hit))
        else:
            missing.append(key)
    return found, missing, loose


def _f(v):
    """Tolerant float: handles '', '12.3%', '$4,500', '(1.2)'."""
    s = str(v or "").strip().replace("%", "").replace("$", "").replace(",", "")
    if not s or s in {"-", "--", "N/A", "NA", "None"}:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        return -float(s) if neg else float(s)
    except ValueError:
        return 0.0


def _name_and_id(cell: str):
    """'Tua Tagovailoa (43727257)' -> ('Tua Tagovailoa', '43727257').

    Also survives a bare name, and a trailing ' (LOCKED)' if DK adds one."""
    s = (cell or "").strip().strip('"')
    s = re.sub(r"\s*\(LOCKED\)\s*$", "", s, flags=re.I)
    m = re.match(r"^(.*?)\s*\((\d{5,})\)\s*$", s)
    if m:
        return m.group(1).strip(), m.group(2)
    return s, ""


# --- 1. Stokastic projections -------------------------------------------
def read_projections(text):
    """The player pool. -> (players, report)

    Salary, projection and ownership are required; everything else degrades.
    Ceiling, Floor, Bust and Value are deliberately NOT read — the brief proved
    they are closed-form restatements of projection and standard deviation
    (Ceiling = Proj + 0.675 sd at R-squared 1.0000), so they carry no
    information. Boom is read, because it is the one distribution column with
    genuinely independent content (~36%).
    """
    rows = list(csv.DictReader(io.StringIO((text or "").lstrip("﻿"))))
    if not rows:
        return [], {"error": "empty file"}
    cols, missing, loose = _match_columns(rows[0].keys(), PROJ_COLUMNS, PROJ_EXCLUDE)
    for req in ("name", "salary", "proj"):
        if req not in cols:
            return [], {"error": f"no '{req}' column found"}
    players, skipped, dupes = [], 0, []
    seen_names = set()
    bad_proj, unknown_pos = [], []      # cells that quietly became 0 / nobody
    for r in rows:
        raw_name = (r.get(cols["name"]) or "").strip()
        if not raw_name:
            continue
        name, embedded_id = _name_and_id(raw_name)
        dk_id = (r.get(cols["dk_id"], "") or "").strip() if "dk_id" in cols else ""
        dk_id = dk_id or embedded_id
        salary = int(_f(r.get(cols["salary"])))
        raw_proj = (r.get(cols["proj"]) or "").strip()
        proj = _f(raw_proj)
        if raw_proj and proj == 0.0 and not _looks_numeric(raw_proj):
            bad_proj.append(name)       # "-", "N/A", "TBD": not a zero, a hole
        if salary <= 0:
            skipped += 1
            continue
        # One row per player. Two rows for the same person become two Player
        # objects that later collect the SAME DK id, and a lineup holding both
        # writes that id twice — a roster DK rejects, which nothing downstream
        # notices because the lineup's id set silently collapses to one short.
        key = normalize_name(name)
        if key in seen_names:
            dupes.append(name)
            continue
        seen_names.add(key)
        pos = (r.get(cols.get("pos", ""), "") or "").strip().upper()
        pos = "DST" if pos in {"DEF", "D", "DST", "D/ST"} else pos
        if proj > 0 and pos not in {"QB", "RB", "WR", "TE", "K", "DST"}:
            unknown_pos.append(f"{name} ({pos or 'blank'})")
        p = Player(
            name=name, dk_id=dk_id or normalize_name(name), pos=pos,
            team=(r.get(cols.get("team", ""), "") or "").strip().upper(),
            opponent=(r.get(cols.get("opp", ""), "") or "").strip().upper(),
            salary=salary, proj=proj,
            ownership=_f(r.get(cols.get("own", ""))) if "own" in cols else 0.0,
            sd=_f(r.get(cols.get("sd", ""))) if "sd" in cols else 0.0,
            boom=_f(r.get(cols.get("boom", ""))) if "boom" in cols else 0.0,
            cpt_own=_f(r.get(cols.get("cpt_own", ""))) if "cpt_own" in cols else 0.0,
            cpt_optimal=_f(r.get(cols.get("cpt_opt", ""))) if "cpt_opt" in cols else 0.0,
        )
        players.append(p)
    return players, {"matched": cols, "unmatched": missing, "loose": loose,
                     "players": len(players), "duplicate_rows": dupes,
                     "bad_proj": bad_proj, "unknown_pos": unknown_pos}


def _looks_numeric(s):
    t = str(s).strip().replace("%", "").replace("$", "").replace(",", "")
    if t.startswith("(") and t.endswith(")"):
        t = t[1:-1]
    try:
        float(t)
        return True
    except ValueError:
        return False


# --- 2. Stokastic lineup pool = the simulated FIELD -----------------------
# This file is NOT a set of recommendations. The brief proved it is an
# ownership-matched model of your opponents: sum(1 + Dupes) lands exactly on the
# configured pool size, and player exposure tracks projected ownership at
# r = 0.996. We read it to learn what the field builds, which is the one thing
# that cannot be produced cold-start.
def read_field(text, by_id=None, by_name=None):
    """Their lineup pool. -> (entries, report)

    Read POSITIONALLY, not through DictReader. The export's roster columns are
    literally headed `CPT,FLEX,FLEX,FLEX,FLEX,FLEX`, and a dict keyed on header
    name silently keeps only the last of five identically-named columns — so
    every lineup would resolve to one flex player instead of five, and the whole
    field model would come back empty without erroring.

    Each entry: {"cpt": Player|None, "flex": [Player], "dupes", "win"} — the
    two vendor columns the build actually uses. Slots resolve against the
    projections pool, so the field and our own builds share Player objects.
    """
    rows = list(csv.reader(io.StringIO((text or "").lstrip("﻿"))))
    if len(rows) < 2:
        return [], {"error": "empty file"}
    headers = rows[0]
    norm = [_norm_header(h) for h in headers]

    # Roster columns by POSITION. Whichever columns actually hold
    # 'Name (12345678)' cells ARE the roster, which handles both shapes without
    # naming either: showdown heads them CPT + five FLEX, classic heads them
    # QB/RB/RB/WR/WR/WR/TE/FLEX/DST.
    looks = [i for i in range(len(headers))
             if sum(1 for r in rows[1:40]
                    if i < len(r) and re.search(r"\(\d{5,}\)", r[i] or "")) > 20]
    cpt_i = next((i for i, h in enumerate(norm) if h in ("cpt", "captain")), None)
    if cpt_i is not None and looks:
        flex_i = [i for i in looks if i != cpt_i]
    elif looks:
        cpt_i, flex_i = None, looks          # classic: no captain
    else:
        return [], {"error": "no roster columns found (no 'Name (id)' cells)"}
    if cpt_i is not None:
        flex_i = flex_i[:5]
        if len(flex_i) < 5:
            return [], {"error": "could not find CPT + 5 FLEX columns"}
    elif len(flex_i) < 6:
        return [], {"error": f"only {len(flex_i)} roster columns found"}

    def col(*names):
        for n in names:
            if n in norm:
                return norm.index(n)
        for n in names:
            for i, h in enumerate(norm):
                if h.startswith(n):
                    return i
        return None

    ci = {"dupes": col("dupes"), "win": col("win%", "win")}

    by_id = by_id or {}
    by_name = by_name or {}

    def resolve(cell):
        name, pid = _name_and_id(cell)
        if pid and pid in by_id:
            return by_id[pid]
        return by_name.get(normalize_name(name))

    def num(r, key):
        i = ci.get(key)
        return _f(r[i]) if i is not None and i < len(r) else 0.0

    entries, unresolved = [], 0
    for r in rows[1:]:
        if not r or len(r) <= max(flex_i):
            continue
        cpt = resolve(r[cpt_i]) if cpt_i is not None else None
        flex = [resolve(r[i]) for i in flex_i]
        if (cpt_i is not None and cpt is None) or any(p is None for p in flex):
            unresolved += 1
            continue
        entries.append({
            "cpt": cpt, "flex": flex,
            # Their Win% is a PERCENT ("0.065%"); ours is a fraction. Stored
            # raw, the two were compared and displayed as if they were the same
            # unit, making the vendor arm look 100x better than it is.
            "dupes": num(r, "dupes"), "win": num(r, "win") / 100.0,
        })
    return entries, {"format": "showdown" if cpt_i is not None else "classic",
                     "rows": len(rows) - 1, "parsed": len(entries),
                     "unresolved_rosters": unresolved}


# --- 3. DK entries export -------------------------------------------------
def read_dk_entries(text):
    """DK's own export: your Entry IDs, and the player pool with REAL DK IDs.

    Same shape as the WNBA file, including the trap that the embedded player
    pool's header sits partway down the file on a row that is also a real entry.
    """
    rows = list(csv.reader(io.StringIO((text or "").lstrip("﻿"))))
    slots, entries, pool = [], [], {}
    pi = None
    for r in rows:
        if not r:
            continue
        if pi is None:
            for i, c in enumerate(r):
                if (c or "").strip() == "Name + ID":
                    pi = i
                    break
            # deliberately no `continue`: this row can also be a real entry
        if (r[0] or "").strip() == "Entry ID" and len(r) > 5:
            # Roster columns run from index 4 until the first blank header.
            # Read the COUNT rather than assuming: showdown is 6 (CPT + 5 FLEX)
            # and classic is 9 (QB/RB/RB/WR/WR/WR/TE/FLEX/DST), and a hard-coded
            # six silently truncates every classic lineup to its first six slots.
            slots = []
            for c in r[4:]:
                if not (c or "").strip():
                    break
                slots.append(c.strip())
        elif (r[0] or "").strip().isdigit() and len(r) > 5:
            entries.append({
                "entry_id": r[0].strip(),
                "contest": (r[1] or "").strip(),
                "contest_id": (r[2] or "").strip(),
                "fee": (r[3] or "").strip(),
            })
        if pi is not None and len(r) > pi + 5 and (r[pi + 2] or "").strip().isdigit():
            name = (r[pi + 1] or "").strip()
            key = normalize_name(name)
            slot = (r[pi + 3] or "").strip().upper()
            # Showdown lists every player TWICE — once as CPT at 1.5x salary and
            # once as FLEX — under two different DK ids. Keep both. Keying on
            # name alone would let whichever row came last win, and a flex id in
            # the captain cell is a file DK will not accept.
            rec = pool.setdefault(key, {"dk_id": "", "cpt_dk_id": ""})
            if slot == "CPT":
                rec["cpt_dk_id"] = (r[pi + 2] or "").strip()
            else:
                rec["dk_id"] = (r[pi + 2] or "").strip()
    return {"slots": slots or ["CPT", "FLEX", "FLEX", "FLEX", "FLEX", "FLEX"],
            "entries": entries, "pool": pool}


# --- 4. LineStar (optional) ----------------------------------------------
# Tested against actual results on NE @ SEA: LineStar's projections correlate
# with the truth at r = 0.618 against Stokastic's 0.630, identical MAE and RMSE,
# and a head-to-head of 13-17 on absolute error. Its Ceiling, Safety, Consensus
# and PPG columns all correlate NEGATIVELY with what Stokastic's projection got
# wrong, which is regression to the mean rather than new information. So none of
# it is read into the build.
#
# Two things here ARE unique, and neither is a projection:
#   VEGAS  — the spread, total and per-team implied points. Stokastic's showdown
#            export carries none of it, and it is the natural candidate for
#            deciding the team split, which is a bet on game script.
#   SCORED — actual fantasy points, which makes this the results file.
LINESTAR_COLUMNS = {
    "name": ["name", "player"], "team": ["team"], "scored": ["scored"],
    "spread": ["vegas"], "total": ["vegastotals"], "implied": ["vegasimplied"],
    "ml": ["vegasml"],
}


def read_linestar(text):
    """-> (by normalized name: {...}, report). Vegas context and actual scores.

    Deliberately does NOT return projections for the build to use. That was
    measured and it adds nothing; reading it in would be a lever with no
    evidence behind it.
    """
    rows = list(csv.DictReader(io.StringIO((text or "").lstrip("﻿"))))
    if not rows:
        return {}, {"error": "empty file"}
    cols, missing, _loose = _match_columns(rows[0].keys(), LINESTAR_COLUMNS)
    if "name" not in cols:
        return {}, {"error": "no player name column"}
    if not any(k in cols for k in ("scored", "implied", "total", "spread")):
        return {}, {"error": "no Vegas or Scored columns — not a LineStar export"}
    out, teams, scored = {}, {}, 0
    for r in rows:
        name = (r.get(cols["name"]) or "").strip()
        if not name:
            continue
        rec = {k: _f(r.get(cols[k])) for k in
               ("scored", "spread", "total", "implied", "ml") if k in cols}
        rec["team"] = (r.get(cols.get("team", ""), "") or "").strip().upper()
        if rec.get("scored"):
            scored += 1
        out[normalize_name(name)] = rec
        if rec["team"] and rec["team"] not in teams and rec.get("implied"):
            teams[rec["team"]] = {"implied": rec.get("implied"),
                                  "spread": rec.get("spread"),
                                  "total": rec.get("total"),
                                  "ml": rec.get("ml")}
    return out, {"players": len(out), "with_scores": scored,
                 "teams": teams, "is_results": scored >= 5}


# --- 5. The sharp's sheet -------------------------------------------------
def read_sharp(text):
    """One name per line, first tab/comma field. -> set of normalized names.

    Deliberately the same permissive reader the WNBA tool uses, so a pasted
    column out of a spreadsheet works without cleaning.
    """
    names = set()
    for line in (text or "").splitlines():
        cell = line.split("\t")[0].split(",")[0].strip()
        if len(cell) > 1 and not cell.lower().startswith(("name", "player")):
            names.add(normalize_name(_name_and_id(cell)[0]))
    return names
