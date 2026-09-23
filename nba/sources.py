"""Readers for the files an NBA build runs on — Stokastic's projections and
lineups exports, the DK entries export, DK's contest standings, and the sharp's
sheet. Ported from nfl/sources.py; the NFL notes on why each reader is shaped
the way it is still apply, and the NBA-specific traps are written where they
bite.

Column names are sniffed rather than hard-coded, and every reader reports what
it matched, so a renamed vendor column is visible instead of silent.

Where the NBA layouts come from, since no NBA Stokastic export was on hand when
this was written: the old Base44 NBA tool's parsers (lineup-optimizer-pro,
LineupParsers.jsx and slateParser.jsx), which read the real files for a season.
Projections: Player, Salary, Position, Team, Opponent, Minutes, FPPM,
Projection, Value, Ownership %, Optimal %, Leverage, Std Dev, Boom, Bust,
Ceiling, Floor, Injury, Starting. Lineups: Simulated ROI, Projected FP, OwnSum,
Win%, Top 10%, Cash%, Dupes, Lineups, Salary, PG, SG, SF, PF, C, G, F, UTIL.
The first real file of the season is the check on all of this.
"""

from __future__ import annotations

import csv
import io
import re

from dk import (CLASSIC_SIZE, CLASSIC_SLOTS, SD_ROSTER_SIZE, Player,
                normalize_name, positions_of, slots_for)

# --- column sniffing -----------------------------------------------------
PROJ_COLUMNS = {
    "name":      ["name", "player", "playername"],
    "pos":       ["pos", "position"],
    "team":      ["team", "teamabbrev", "tm"],
    "opp":       ["opp", "opponent"],
    "salary":    ["salary", "sal", "dksalary"],
    "proj":      ["projection", "projectedfp", "projfp", "projpts", "proj", "fpts",
                  "projectedpoints"],
    "own":       ["ownership%", "ownership", "own%", "own", "projown",
                  "projectedownership", "pown"],
    "sd":        ["stddev", "standarddeviation", "sd", "stdev", "sigma"],
    "boom":      ["boom", "boompct", "boom%"],
    "cpt_own":   ["projectedcptownership", "cptownership", "captainownership",
                  "cptown", "captainown"],
    "cpt_opt":   ["cptoptimal%", "cptoptimal", "captainoptimal", "cptopt", "optimalcpt"],
    "minutes":   ["minutes", "projmin", "projectedminutes", "mins", "min"],
    "starting":  ["starting", "starter", "start"],
    "dk_id":     ["dkid", "playerid", "id"],
}

# A canonical field must NOT bind to a header containing one of these. The
# showdown captain trap is NFL's (a plain "ownership" match binds to "CPT
# Ownership", a different quantity on a different denominator). NBA adds three:
# Stokastic ships "Optimal %" next to "Ownership %", "FPPM" next to the
# projection, and "Minutes" must never be read as a projection or vice versa.
PROJ_EXCLUDE = {
    "own":      ["cpt", "captain", "optimal"],
    "salary":   ["cpt", "captain"],
    "proj":     ["cpt", "captain", "own", "fppm", "min"],
    "pos":      ["roster"],
    "minutes":  ["fppm"],
    "starting": ["salary"],
}


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9%]", "", (h or "").lower())


def _match_columns(headers, spec, exclude=None):
    """-> ({canonical: header}, [unmatched], [(canonical, header) matched loosely])

    Exact beats prefix beats substring, excluded fragments are never eligible,
    and one header serves one field — see nfl/sources.py for the bug each of
    those three rules came from.
    """
    exclude = exclude or {}
    norm = {_norm_header(h): h for h in headers}
    found, missing, loose = {}, [], []
    bound = set()
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


def _looks_numeric(s):
    t = str(s).strip().replace("%", "").replace("$", "").replace(",", "")
    if t.startswith("(") and t.endswith(")"):
        t = t[1:-1]
    try:
        float(t)
        return True
    except ValueError:
        return False


def _clean_cell(cell):
    """Strip the quote debris the NBA pool export carries. The old tool had a
    dedicated cleaner for cells arriving as '\\"Name (123)\\"', which means the
    real file has, at least once, double-escaped its quotes."""
    s = (cell or "").strip()
    s = re.sub(r'^(\\?")+|(\\?")+$', "", s)
    return s.strip()


def _name_and_id(cell: str):
    """'Nikola Jokic (43812345)' -> ('Nikola Jokic', '43812345').

    Also survives a bare name, quote debris, and a trailing ' (LOCKED)'."""
    s = _clean_cell(cell)
    s = re.sub(r"\s*\(LOCKED\)\s*$", "", s, flags=re.I)
    m = re.match(r"^(.*?)\s*\((\d{5,})\)\s*$", s)
    if m:
        return m.group(1).strip(), m.group(2)
    return s, ""


def _starting(v):
    s = str(v or "").strip().lower()
    if s in ("true", "yes", "y", "1", "confirmed", "c"):
        return True
    if s in ("false", "no", "n", "0"):
        return False
    return None


# --- 1. Stokastic projections -------------------------------------------
def read_projections(text):
    """The player pool. -> (players, report)

    Salary, projection and ownership are required; everything else degrades.
    Ceiling, Floor, Bust and Value are NOT read: on NFL they were measured as
    closed-form restatements of projection and Std Dev (Ceiling = Proj + 0.675
    sd at R-squared 1.0000). Whether Stokastic's NBA file is built the same way
    is untested, and the simulator gets its spread from Std Dev, so reading them
    would only add a second, possibly redundant, number. Minutes and Starting
    ARE read, for the log: they are the two facts about a player the reviews
    kept wishing they had recorded.
    """
    rows = list(csv.DictReader(io.StringIO((text or "").lstrip("﻿"))))
    if not rows:
        return [], {"error": "empty file"}
    cols, missing, loose = _match_columns(rows[0].keys(), PROJ_COLUMNS, PROJ_EXCLUDE)
    for req in ("name", "salary", "proj"):
        if req not in cols:
            return [], {"error": f"no '{req}' column found"}
    players, dupes, seen = [], [], set()
    bad_proj, unknown_pos = [], []
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
            bad_proj.append(name)
        if salary <= 0:
            continue
        key = normalize_name(name)
        if key in seen:              # one row per player — see nfl/sources.py
            dupes.append(name)
            continue
        seen.add(key)
        pos = (r.get(cols.get("pos", ""), "") or "").strip().upper()
        if proj > 0 and not positions_of(pos):
            unknown_pos.append(f"{name} ({pos or 'blank'})")
        g = lambda k: _f(r.get(cols[k])) if k in cols else 0.0
        p = Player(
            name=name, dk_id=dk_id or key, pos=pos,
            team=(r.get(cols.get("team", ""), "") or "").strip().upper(),
            opponent=(r.get(cols.get("opp", ""), "") or "").strip().upper()
                     .lstrip("@").replace("VS ", "").strip(),
            salary=salary, proj=proj, ownership=g("own"), sd=g("sd"),
            boom=g("boom"), cpt_own=g("cpt_own"), cpt_optimal=g("cpt_opt"),
            minutes=g("minutes"),
            starting=(_starting(r.get(cols["starting"])) if "starting" in cols
                      else None),
        )
        players.append(p)
    return players, {"matched": cols, "unmatched": missing, "loose": loose,
                     "players": len(players), "duplicate_rows": dupes,
                     "bad_proj": bad_proj, "unknown_pos": unknown_pos}


# --- 2. Stokastic lineups export = the simulated FIELD --------------------
# Not a list of recommendations. On NFL the export was proven to be an
# ownership-matched model of the opponents (rows + Dupes reconstruct the
# configured field size exactly; exposure tracks ownership at r = 0.996), and
# the user has settled that NBA works the same way. We read it to learn what the
# field builds, and score our own lineups against it.
_ROSTER_CELL = re.compile(r"^[^,]*\(\d{5,}\)$")
_SLOT_HEADERS = set(CLASSIC_SLOTS) | {"CPT", "FLEX", "CAPTAIN"}


def read_field(text, by_id=None, by_name=None):
    """Their lineup pool. -> (entries, report)

    Each entry: {"cpt": Player|None, "flex": [Player], "dupes", "win", "top10"}.
    Classic entries carry no captain and eight flex; showdown carries a captain
    and five.

    Read POSITIONALLY. Two traps decide how the roster columns are found:

    - The showdown export heads its five flex columns identically (UTIL x5 on
      NBA), and a dict keyed on header keeps only the last of them — every
      lineup would resolve to one flex player and the field would come back
      empty without an error. That is NFL's trap and it is NBA's too.
    - The NBA classic export has a `Lineups` column holding all eight names in
      ONE quoted, comma-separated cell. Anything that finds roster columns by
      "a cell with a player in it" would take that column as a ninth slot. So a
      roster column is one whose cells are a SINGLE 'Name (id)' — no comma — or,
      failing that, one headed by a roster slot name.
    """
    rows = list(csv.reader(io.StringIO((text or "").lstrip("﻿"))))
    if len(rows) < 2:
        return [], {"error": "empty file"}
    headers = [_clean_cell(h) for h in rows[0]]
    norm = [_norm_header(h) for h in headers]
    sample = rows[1:40]
    looks = [i for i in range(len(headers))
             if sum(1 for r in sample if i < len(r)
                    and _ROSTER_CELL.match(_clean_cell(r[i]))) > min(20, len(sample) // 2)]
    if not looks:
        # No ids in the cells: fall back to the slot headers themselves.
        looks = [i for i, h in enumerate(headers) if h.strip().upper() in _SLOT_HEADERS]
    if not looks:
        return [], {"error": "no roster columns found"}
    cpt_i = next((i for i in looks if norm[i] in ("cpt", "captain")), None)
    flex_i = [i for i in looks if i != cpt_i]
    if cpt_i is not None:
        if len(flex_i) < SD_ROSTER_SIZE - 1:
            return [], {"error": "could not find CPT + 5 UTIL columns"}
        flex_i = flex_i[:SD_ROSTER_SIZE - 1]
    elif len(flex_i) != CLASSIC_SIZE:
        return [], {"error": f"{len(flex_i)} roster columns found, a classic "
                             f"lineup has {CLASSIC_SIZE}"}

    def col(*names):
        for n in names:
            if n in norm:
                return norm.index(n)
        for n in names:
            for i, h in enumerate(norm):
                if h.startswith(n):
                    return i
        return None

    ci = {"dupes": col("dupes"), "win": col("win%", "win"),
          "top10": col("top10%", "top10")}
    by_id, by_name = by_id or {}, by_name or {}

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
        if not r or len(r) <= max(flex_i + [cpt_i or 0]):
            continue
        cpt = resolve(r[cpt_i]) if cpt_i is not None else None
        flex = [resolve(r[i]) for i in flex_i]
        if (cpt_i is not None and cpt is None) or any(p is None for p in flex):
            unresolved += 1
            continue
        entries.append({
            "cpt": cpt, "flex": flex,
            # Percent columns ("0.065%") stored as fractions — see nfl/sources.py
            # for the 100x display bug that came from mixing the two.
            "dupes": num(r, "dupes"), "win": num(r, "win") / 100.0,
            "top10": num(r, "top10") / 100.0,
        })
    return entries, {"format": "showdown" if cpt_i is not None else "classic",
                     "rows": len(rows) - 1, "parsed": len(entries),
                     "unresolved_rosters": unresolved,
                     "roster_columns": [headers[i] for i in ([cpt_i] if cpt_i is not None else []) + flex_i]}


# --- 3. DK entries export -------------------------------------------------
def _dk_start(info):
    """'NYK@SAS 06/13/2026 08:30PM ET' -> ('NYK@SAS', '2026-06-13T20:30')."""
    m = re.match(r"\s*(\w+@\w+)\s+(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})\s*([AP]M)",
                 info or "", re.I)
    if not m:
        return (info or "").strip().split(" ")[0], ""
    mo, d, y, h, mi, ap = (m.group(i) for i in range(2, 8))
    h = int(h) % 12 + (12 if ap.upper() == "PM" else 0)
    return m.group(1), f"{int(y):04d}-{int(mo):02d}-{int(d):02d}T{h:02d}:{mi}"


def read_dk_entries(text):
    """DK's own export: your Entry IDs, and the player pool with REAL DK IDs.

    Layout, read off a real NBA export (NYK @ SAS, 06/13/2026): entries in
    columns 0-3 with the roster to their right, then a blank column, then
    'Instructions', then the embedded player pool — Position, Name + ID, Name,
    ID, Roster Position, Salary, Game Info, TeamAbbrev, AvgPointsPerGame. Three
    traps, all live on that file:

    - The pool's header sits partway down on a row that is ALSO a real entry, so
      that row must be read as both (no `continue`).
    - ONE download carries several contests — that file held a $0.50 150-max, a
      $5 Zone and a single-entry $0 free contest — so contests are kept apart.
    - Showdown lists every player twice (CPT and UTIL) under different ids.

    `Roster Position` is DK's own statement of eligibility ('PG/G/UTIL' on
    classic) and is what the build uses to decide which slot a player may fill.
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
        if (r[0] or "").strip() == "Entry ID" and len(r) > 5:
            slots = []
            for c in r[4:]:
                if not (c or "").strip():
                    break
                slots.append(c.strip().upper())
        elif (r[0] or "").strip().isdigit() and len(r) > 5:
            entries.append({
                "entry_id": r[0].strip(),
                "contest": (r[1] or "").strip(),
                "contest_id": (r[2] or "").strip(),
                "fee": (r[3] or "").strip(),
            })
        if pi is not None and len(r) > pi + 6 and (r[pi + 2] or "").strip().isdigit():
            name = (r[pi + 1] or "").strip()
            key = normalize_name(name)
            roster_pos = (r[pi + 3] or "").strip().upper()
            game, start = _dk_start(r[pi + 5])
            rec = pool.setdefault(key, {
                "name": name, "dk_id": "", "cpt_dk_id": "", "pos": "",
                "eligible": set(), "salary": 0, "game": game, "start": start,
                "team": (r[pi + 6] or "").strip().upper()})
            rec["pos"] = (r[pi - 1] or "").strip().upper() if pi >= 1 else ""
            if roster_pos == "CPT":
                rec["cpt_dk_id"] = (r[pi + 2] or "").strip()
            else:
                rec["dk_id"] = (r[pi + 2] or "").strip()
                rec["salary"] = int(_f(r[pi + 4]))
                rec["eligible"] = {t for t in re.split(r"[/\s]+", roster_pos)
                                   if t in CLASSIC_SLOTS} or slots_for(positions_of(rec["pos"]))
    return {"slots": slots or list(CLASSIC_SLOTS), "entries": entries, "pool": pool}


# --- 4. DK contest standings ----------------------------------------------
_STAND_SLOT = re.compile(r"\s*\b(CPT|UTIL|PG|SG|SF|PF|C|G|F)\s+")


def _standings_roster(cell):
    """'C Name F Name G Name ...' -> ([slot labels], [names]) or None.

    Split on a CAPTURING group so the labels come back too, and the caller can
    check them against the slate's roster shape. A name that happens to contain
    a slot token (a stray ' F ' in a middle name) breaks the alternation, and
    the label check is what catches it rather than a silent misread.
    """
    parts = _STAND_SLOT.split(cell or "")
    if not parts or parts[0].strip():
        return None
    labels, names = parts[1::2], [p.strip() for p in parts[2::2]]
    if len(labels) != len(names):
        return None
    return labels, names


def read_standings(text):
    """DK's contest standings export. -> (rows, report)

    Each row: {"entry_id", "rank", "points", "names", "labels", "readable"}.
    Names are normalised, captain first on showdown.

    Four things about this file, three inherited and one NBA-specific:

    - Two tables side by side: entries on the left (Rank, EntryId, EntryName,
      TimeRemaining, Points, Lineup) and a per-player block on the right
      (Player, Roster Position, %Drafted, FPTS) sharing row numbers only.
    - The player block lists a player once PER ROSTER SLOT (WNBA measured it:
      one player as F 57.2% and again as UTIL 1.0%). Real ownership is the SUM
      of a player's rows. On NBA classic a guard can appear as PG, G and UTIL,
      so reading one row undercounts him badly — and any OwnSum ever computed
      that way is not comparable with a projected one.
    - On showdown the CPT row's FPTS already has 1.5x applied, so actual points
      come from the non-captain rows.
    - DK writes the roster grouped by position, NOT in the upload file's slot
      order (WNBA, contest 195934561, unanimous). Names are therefore read as a
      SET of who is on the roster, never slot by slot.

    Every parse is checked by reproducing a number the file already contains:
    the report counts how many entries' player FPTS add up to their Points.
    """
    rows, actual, own, cpt_own, seen = [], {}, {}, {}, {}
    rdr = csv.DictReader(io.StringIO((text or "").lstrip("﻿")))
    if not rdr.fieldnames or "Lineup" not in rdr.fieldnames:
        return [], {"error": "no Lineup column — is that the standings export?"}
    unreadable = 0
    for r in rdr:
        if (r.get("Rank") or "").strip():
            got = _standings_roster(r.get("Lineup") or "")
            try:
                rank = int((r.get("Rank") or "").strip())
            except ValueError:
                rank = None
            if rank is None:
                continue
            if got is None:
                unreadable += 1
                rows.append({"entry_id": (r.get("EntryId") or "").strip(),
                             "rank": rank, "points": _f(r.get("Points")),
                             "names": [], "labels": [], "readable": False})
                continue
            labels, names = got
            if "CPT" in labels:                        # captain first
                i = labels.index("CPT")
                labels = [labels[i]] + labels[:i] + labels[i + 1:]
                names = [names[i]] + names[:i] + names[i + 1:]
            rows.append({"entry_id": (r.get("EntryId") or "").strip(),
                         "rank": rank, "points": _f(r.get("Points")),
                         "names": [normalize_name(_name_and_id(n)[0]) for n in names],
                         "labels": labels,
                         "readable": "LOCKED" not in names})
        who = (r.get("Player") or "").strip()
        if who:
            k = normalize_name(who)
            slot = (r.get("Roster Position") or "").strip().upper()
            pct = _f(r.get("%Drafted"))
            if slot == "CPT":
                cpt_own[k] = cpt_own.get(k, 0.0) + pct
            else:
                own[k] = own.get(k, 0.0) + pct
                actual[k] = _f(r.get("FPTS"))
    if not rows:
        return [], {"error": "no entry rows found (every Rank was empty)"}
    for e in rows:
        if e["readable"]:
            seen[_roster_key(e["names"], e["labels"])] = \
                seen.get(_roster_key(e["names"], e["labels"]), 0) + 1
    # Reproduce the file's own Points from its own FPTS — the parse check.
    checked = agree = 0
    for e in rows:
        if not e["readable"] or not all(n in actual for n in e["names"]):
            continue
        tot = sum(actual[n] * (1.5 if lab == "CPT" else 1.0)
                  for n, lab in zip(e["names"], e["labels"]))
        checked += 1
        agree += abs(tot - e["points"]) < 0.26
    pts = sorted(e["points"] for e in rows)
    return rows, {"entries": len(rows), "points_sorted": pts, "actual": actual,
                  "ownership": own, "cpt_ownership": cpt_own, "copies": seen,
                  "players": len(actual), "unreadable": unreadable,
                  "points_checked": checked, "points_agree": agree}


def _roster_key(names, labels=None):
    """Captain matters on showdown, so it is kept out of the unordered set."""
    if labels and labels[0] == "CPT":
        return (names[0], frozenset(names[1:]))
    return ("", frozenset(names))


# --- 5. LineStar (optional) ----------------------------------------------
# What NFL does with LineStar, checked before copying it: Vegas is LOGGED against
# every build and never used (4,650 NFL log rows carry it; nothing has tested it
# yet), and its projection was measured on one NFL slate as the same number as
# Stokastic's (r 0.618 vs 0.630, identical MAE), so NFL does not read it.
#
# NBA reads a little more, and still uses none of it in the build:
#   VEGAS      Stokastic's NBA projections carry no Vegas, and the api/ proxy
#              that used to supply it is retired, so this is the only source.
#              Blowout risk and pace cannot be tested later without it logged.
#   PROJECTED  logged beside Stokastic's number. NBA has never measured the two
#              against each other, and "a second projection source to cross-
#              check Stokastic" was an idea the old chats deferred. The log is
#              what lets the grader answer it — screen first (lesson 1), use
#              later only if it survives.
#   STARTING   LineStar's StartingStatus (1 starter, 2 bench, 4 out/inactive).
# Column names are the ones the WNBA tool reads off real LineStar exports.
LINESTAR_COLUMNS = {
    "name": ["name", "player"], "team": ["team"], "scored": ["scored"],
    "spread": ["vegas"], "total": ["vegastotals", "vegastotal"],
    "implied": ["vegasimplied"], "ml": ["vegasml"],
    "proj": ["projected", "projection", "proj"], "own": ["projown"],
    "status": ["startingstatus"],
}


def read_linestar(text):
    """-> ({norm name: {...}}, report). Nothing here feeds the build."""
    rows = list(csv.DictReader(io.StringIO((text or "").lstrip("﻿"))))
    if not rows:
        return {}, {"error": "empty file"}
    cols, _missing, loose = _match_columns(rows[0].keys(), LINESTAR_COLUMNS,
                                           {"proj": ["own"], "spread": ["total", "implied", "ml"]})
    if "name" not in cols:
        return {}, {"error": "no player name column"}
    if not any(k in cols for k in ("scored", "implied", "total", "spread", "proj")):
        return {}, {"error": "no Vegas, projection or Scored columns — not a LineStar export"}
    out, teams, scored = {}, {}, 0
    for r in rows:
        name = (r.get(cols["name"]) or "").strip()
        if not name:
            continue
        rec = {k: _f(r.get(cols[k])) for k in
               ("scored", "spread", "total", "implied", "ml", "proj", "own") if k in cols}
        rec["status"] = (r.get(cols["status"]) or "").strip() if "status" in cols else ""
        rec["team"] = (r.get(cols.get("team", ""), "") or "").strip().upper()
        if rec.get("scored"):
            scored += 1
        out[normalize_name(name)] = rec
        if rec["team"] and rec["team"] not in teams and rec.get("implied"):
            teams[rec["team"]] = {k: rec.get(k) for k in ("implied", "spread", "total", "ml")}
    return out, {"players": len(out), "with_scores": scored, "teams": teams,
                 "is_results": scored >= 5, "matched": cols, "loose": loose}


# --- 6. The sharp's sheet -------------------------------------------------
SHARP_SKIP = {"pg", "sg", "sf", "pf", "c", "g", "f", "util", "cpt", "pos",
              "position", "team", "salary", "sal", "name", "player", "core",
              "opp", "opponent", "proj", "projection", "own", "ownership",
              "value", "notes", "x"}


def _sharp_cell(cell):
    cell = (cell or "").strip().strip('"').strip()
    if len(cell) < 2:
        return False
    low = cell.lower()
    if low in SHARP_SKIP or low.startswith(("name", "player")):
        return False
    if not any(ch.isalpha() for ch in cell):
        return False            # a salary or a projection
    if len(cell) <= 3 and cell.isupper():
        return False            # a team code, never a person
    return True


def read_sharp(text):
    """The sharp's sheet, pasted one per line or comma-separated. -> set of names."""
    names = set()
    for cell in re.split(r"[\t,;\r\n]", text or ""):
        if _sharp_cell(cell):
            names.add(normalize_name(_name_and_id(cell)[0]))
    return names


def payout_ladder(prize_pool, first_prize, paid_from, paid_to):
    """-> f(rank) = dollars, or None. Same fit as nfl/sources.py: first place and
    a power-law down to the flat band that pays the minimum.

    For the $0.50 150-max NBA mini-MAX the old chats recorded 1st about $2,000-
    $2,500, rank 100 about $8, rank 300 about $4, and about 21% of the field
    cashing (9,972 of 47,562). Those are the anchors to type in.
    """
    try:
        prize_pool = float(prize_pool); first_prize = float(first_prize)
        paid_from = int(paid_from); paid_to = int(paid_to)
    except (TypeError, ValueError):
        return None
    if not (prize_pool > 0 and first_prize > 0 and 1 < paid_from <= paid_to):
        return None
    flat = paid_to - paid_from + 1
    need = prize_pool - flat
    lo, hi = 0.5, 3.0
    for _ in range(60):
        b = (lo + hi) / 2
        tot = sum(max(1.0, first_prize * r ** (-b)) for r in range(1, paid_from))
        if tot > need:
            lo = b
        else:
            hi = b
    b = (lo + hi) / 2
    return lambda rank: (max(1.0, first_prize * rank ** (-b))
                         if 1 <= rank <= paid_to else 0.0)
