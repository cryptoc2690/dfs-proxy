"""The name match (Core Hold, Name Match And Nov 25, Nov 26).

A projected player whose name finds no DK player (DK writes "Nic Claxton",
Stokastic's projections "Nicolas Claxton") was dropped by the join: he could
be in no lineup and every lineup of Stokastic's file that held him was
skipped. This module settles it with nothing typed.

  the seat       Stokastic's lineups file writes every lineup twice: once as
                 his own names (the "Lineups" column) and once as DK's
                 "Name (id)" cells, in the same order. A projected name that
                 sits on one DK id in every lineup naming him is that player.
  the price tag  with no file loaded (late swap) or no lineup naming him: the
                 one DK player nobody matched by name, on his team, at his
                 salary, with his last name.

A match is made only when: the DK player was matched by no projected name;
his DK salary is the projection file's salary; the seat and the price tag do
not point at two different DK players. Anything else is left unmatched and
reported by `unmatched` (the build then stops on the ones that matter).

Nothing is renamed. The DK record gets a second key, the projection's
spelling, so every join in the tool that looks a player up by his projected
name finds DK's record, his id, his tip time and his lock. `fixes` maps the
projection's key to DK's key for the other files spelled DK's way (LineStar,
the standings, a pool typed with DK's spelling).
"""

from __future__ import annotations

import collections
import csv
import io
import re

from dk import normalize_name

SLOT_COLS = ("PG", "SG", "SF", "PF", "C", "G", "F", "UTIL")
_ID = re.compile(r"\((\d{5,})\)\s*(?:\(LOCKED\))?\s*$", re.I)


def last_name(name):
    t = normalize_name(name).split()
    return t[-1] if t else ""


def seats(field_texts):
    """{projection-side key: Counter(DK id)} read off the lineups file(s): the
    name in place i of the "Lineups" column and the id in slot column i."""
    out = collections.defaultdict(collections.Counter)
    for text in field_texts or ():
        if not (text or "").strip():
            continue
        rows = csv.reader(io.StringIO(text.lstrip("﻿")))
        try:
            head = [h.strip() for h in next(rows)]
        except StopIteration:
            continue
        up = [h.upper() for h in head]
        if "LINEUPS" not in up or any(s not in up for s in SLOT_COLS):
            continue
        li, si = up.index("LINEUPS"), [up.index(s) for s in SLOT_COLS]
        for r in rows:
            if len(r) <= max(si + [li]):
                continue
            names = [x.strip() for x in r[li].split(",")]
            if len(names) != len(si):
                continue
            for nm, j in zip(names, si):
                m = _ID.search(r[j] or "")
                if m and nm:
                    out[normalize_name(nm)][m.group(1)] += 1
    return out


def match(players, dk, field_texts=(), seat=None):
    """-> (matches, unmatched). matches: [(player, DK record, how)] for projected
    players with no DK record under their own name; unmatched: [(player, why)].
    seat: the answer of seats() when the caller already has it."""
    pool = (dk or {}).get("pool") or {}
    if not pool:
        return [], []
    keys = {normalize_name(p.name) for p in players}
    free = {k: rec for k, rec in pool.items() if k not in keys and rec.get("dk_id")}
    by_id = {rec["dk_id"]: (k, rec) for k, rec in pool.items() if rec.get("dk_id")}
    seat = seats(field_texts) if seat is None else seat
    out, still, taken = [], [], set()
    for p in players:
        k = normalize_name(p.name)
        if k in pool and pool[k].get("dk_id"):
            continue
        team, sal, ln = (p.team or "").strip().upper(), int(p.salary or 0), last_name(p.name)
        tag = [fk for fk, rec in free.items() if rec.get("team") == team
               and int(rec.get("salary") or 0) == sal and last_name(rec["name"]) == ln]
        ids = seat.get(k) or {}
        by_seat = None
        if len(ids) == 1:
            pid = next(iter(ids))
            if pid in by_id and by_id[pid][0] in free:
                by_seat = by_id[pid][0]
        pick = why = None
        if len(ids) > 1:
            why = "Stokastic's lineups put him on more than one DK id"
        elif by_seat and int(free[by_seat].get("salary") or 0) != sal:
            why = (f"the lineups file seats him on {free[by_seat]['name']}, whose DK salary "
                   f"(${int(free[by_seat].get('salary') or 0):,}) is not his (${sal:,})")
        elif by_seat and len(tag) == 1 and tag[0] != by_seat:
            why = (f"the lineups file seats him on {free[by_seat]['name']} but his team, salary "
                   f"and last name point at {free[tag[0]]['name']}")
        elif by_seat:
            pick, how = by_seat, "his seat in Stokastic's lineups" + (
                "; same team, salary and last name" if tag == [by_seat] else "")
        elif len(tag) == 1:
            pick, how = tag[0], "the only unmatched DK player on his team at his salary with his last name"
        elif len(tag) > 1:
            why = "more than one unmatched DK player on his team at his salary with his last name"
        else:
            why = "no unmatched DK player on his team at his salary with his last name"
        if pick and pick in taken:
            pick, why = None, "two projected players point at the same DK player"
        if pick:
            taken.add(pick)
            out.append((p, free[pick], how))
        else:
            still.append((p, why))
    return out, still


def alias(dk, players, field_texts=()):
    """Run the match and give each matched DK record the projection's spelling as
    a second key; entry cells holding him are re-keyed to that spelling (their DK
    spelling is kept as "dk_name" for the upload line).
    -> {"fixes": {projection key: DK key}, "matched": [...], "unmatched": [...]}"""
    matched, still = match(players, dk, field_texts)
    fixes = {}
    for p, rec, how in matched:
        k = normalize_name(p.name)
        dk["pool"][k] = rec
        fixes[k] = normalize_name(rec["name"])
        p.dk_name = rec["name"]
        for e in dk.get("entries") or ():
            for c in e.get("cells") or ():
                if c and c.get("dk_id") == rec["dk_id"] and normalize_name(c["name"]) != k:
                    c["dk_name"] = c["name"]
                    c["name"] = p.name
    return {"fixes": fixes, "matched": matched, "unmatched": still}


def also(mapping, fixes):
    """A file keyed by DK's spelling (LineStar, the standings' ownership): the
    projection's spelling becomes a second key of the same row."""
    for pk, dkk in (fixes or {}).items():
        if pk not in mapping and dkk in mapping:
            mapping[pk] = mapping[dkk]
    return mapping


def also_names(names, fixes):
    """A typed list: DK's spelling of a matched player counts as his name."""
    return set(names) | {pk for pk, dkk in (fixes or {}).items() if dkk in names}


def matters(p, why, pool_names=(), seat=None, own_line=2.0):
    """An unmatched projected player the build must not go on without: he is in
    the typed pool, or Stokastic's lineups name him, or he projects 2%+ owned."""
    k = normalize_name(p.name)
    if (p.proj or 0) <= 0:
        return False
    return bool(k in set(pool_names or ()) or (seat or {}).get(k) or (p.ownership or 0) >= own_line)
