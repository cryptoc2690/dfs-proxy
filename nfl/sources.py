"""Readers for the four files a showdown build runs on.

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
    "proj":      ["projfp", "projection", "projpts", "proj", "fpts", "projectedpoints"],
    "own":       ["ownership%", "ownership", "own%", "own", "projown",
                  "projectedownership", "pown"],
    "sd":        ["stddev", "standarddeviation", "sd", "stdev", "sigma"],
    "boom":      ["boom", "boompct", "boom%"],
    "cpt_own":   ["cptownership", "captainownership", "cptown", "captainown"],
    "cpt_opt":   ["cptoptimal", "captainoptimal", "cptopt", "optimalcpt"],
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
    found, missing = {}, []
    for key, candidates in spec.items():
        bad = exclude.get(key, [])
        allowed = {n: h for n, h in norm.items()
                   if not any(b in n for b in bad)}
        hit = None
        for c in candidates:
            if c in allowed:
                hit = allowed[c]
                break
        if hit is None:
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
            found[key] = hit
        else:
            missing.append(key)
    return found, missing


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
    cols, missing = _match_columns(rows[0].keys(), PROJ_COLUMNS, PROJ_EXCLUDE)
    for req in ("name", "salary", "proj"):
        if req not in cols:
            return [], {"error": f"no '{req}' column found",
                        "headers": list(rows[0].keys())}
    players, skipped = [], 0
    for r in rows:
        raw_name = (r.get(cols["name"]) or "").strip()
        if not raw_name:
            continue
        name, embedded_id = _name_and_id(raw_name)
        dk_id = (r.get(cols["dk_id"], "") or "").strip() if "dk_id" in cols else ""
        dk_id = dk_id or embedded_id
        salary = int(_f(r.get(cols["salary"])))
        proj = _f(r.get(cols["proj"]))
        if salary <= 0:
            skipped += 1
            continue
        pos = (r.get(cols.get("pos", ""), "") or "").strip().upper()
        pos = "DST" if pos in {"DEF", "D", "DST", "D/ST"} else pos
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
    return players, {"matched": cols, "unmatched": missing,
                     "rows": len(rows), "players": len(players),
                     "skipped_no_salary": skipped}


# --- 2. Stokastic lineup pool = the simulated FIELD -----------------------
# This file is NOT a set of recommendations. The brief proved it is an
# ownership-matched model of your opponents: sum(1 + Dupes) lands exactly on the
# configured pool size, and player exposure tracks projected ownership at
# r = 0.996. We read it to learn what the field builds, which is the one thing
# that cannot be produced cold-start.
FIELD_COLUMNS = {
    "win":   ["win%", "win", "winpct", "simwin"],
    "top10": ["top10%", "top10", "top10pct"],
    "cash":  ["cash%", "cash", "cashpct"],
    "dupes": ["dupes", "dupe", "duplicates", "dups"],
    "roi":   ["simulatedroi", "simroi", "roi"],
    "ownsum": ["ownsum", "totalownership", "ownershipsum", "sumownership"],
    "stack": ["stacktype", "stack"],
}


def read_field(text, by_id=None, by_name=None):
    """Their lineup pool. -> (entries, report)

    Each entry: {"cpt": Player|None, "flex": [Player], "dupes": float,
                 "win": float, "roi": float, "raw": {...}}
    Player slots are resolved against the projections pool when one is supplied,
    so the field and our own builds share objects.
    """
    rdr = csv.DictReader(io.StringIO((text or "").lstrip("﻿")))
    rows = list(rdr)
    if not rows:
        return [], {"error": "empty file"}
    headers = list(rows[0].keys())
    cols, _ = _match_columns(headers, FIELD_COLUMNS)

    # Roster columns: a CPT column plus FLEX columns, in file order. Fall back to
    # any column whose cells look like 'Name (12345678)'.
    roster_cols = [h for h in headers
                   if re.search(r"\b(cpt|captain|flex|util|player\s*\d)\b",
                                h or "", re.I)]
    if len(roster_cols) < 6:
        roster_cols = [h for h in headers
                       if sum(1 for r in rows[:40]
                              if re.search(r"\(\d{5,}\)", str(r.get(h) or ""))) > 20]
    cpt_col = next((h for h in roster_cols
                    if re.search(r"\b(cpt|captain)\b", h or "", re.I)), None)
    if cpt_col is None and roster_cols:
        cpt_col = roster_cols[0]          # DK/Stokastic both put the captain first
    flex_cols = [h for h in roster_cols if h != cpt_col]

    by_id = by_id or {}
    by_name = by_name or {}

    def resolve(cell):
        name, pid = _name_and_id(cell)
        if pid and pid in by_id:
            return by_id[pid]
        return by_name.get(normalize_name(name))

    entries, unresolved = [], 0
    for r in rows:
        cpt = resolve(r.get(cpt_col)) if cpt_col else None
        flex = [resolve(r.get(c)) for c in flex_cols]
        if cpt is None or any(p is None for p in flex):
            unresolved += 1
        entries.append({
            "cpt": cpt,
            "flex": [p for p in flex if p is not None],
            "dupes": _f(r.get(cols.get("dupes", ""))) if "dupes" in cols else 0.0,
            "win": _f(r.get(cols.get("win", ""))) if "win" in cols else 0.0,
            "top10": _f(r.get(cols.get("top10", ""))) if "top10" in cols else 0.0,
            "cash": _f(r.get(cols.get("cash", ""))) if "cash" in cols else 0.0,
            "roi": _f(r.get(cols.get("roi", ""))) if "roi" in cols else 0.0,
            "own_sum": _f(r.get(cols.get("ownsum", ""))) if "ownsum" in cols else 0.0,
            "stack": (r.get(cols.get("stack", ""), "") or "").strip() if "stack" in cols else "",
        })
    return entries, {"matched": cols, "roster_cols": roster_cols,
                     "cpt_col": cpt_col, "rows": len(rows),
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
        if (r[0] or "").strip() == "Entry ID" and len(r) > 6:
            slots = [c.strip() for c in r[4:10] if c.strip()]
        elif (r[0] or "").strip().isdigit() and len(r) > 6:
            cells = [c for c in r[4:10] if (c or "").strip()]
            entries.append({
                "entry_id": r[0].strip(),
                "contest": (r[1] or "").strip(),
                "contest_id": (r[2] or "").strip(),
                "fee": (r[3] or "").strip(),
                "names": [_name_and_id(c)[0] for c in cells],
            })
        if pi is not None and len(r) > pi + 5 and (r[pi + 2] or "").strip().isdigit():
            name = (r[pi + 1] or "").strip()
            pool[normalize_name(name)] = {
                "dk_id": (r[pi + 2] or "").strip(),
                "name": name,
                "roster_pos": (r[pi + 3] or "").strip().upper(),
                "salary": int(_f(r[pi + 4])),
            }
    return {"slots": slots or ["CPT", "FLEX", "FLEX", "FLEX", "FLEX", "FLEX"],
            "entries": entries, "pool": pool}


# --- 4. The sharp's sheet -------------------------------------------------
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
