"""The news step of late swap: a player who gets a start after lock goes into a few lineups.

Late Swap And The Top 10 review (Nov 21, 2026). Runs AFTER the tool's outs-only swap, on the
rosters that pass leaves, one contest's entries at a time. It uses the tool's own objects:
`dk.Player` (name, dk_id, salary, proj, eligible, start, team), `lateswap._fill` to seat the
open slots (latest tips in UTIL, then G and F) and `classic.legal` for DK's own rules.

Nothing here reads a file, a log or the clock. The caller says who the new starters are and
what each is projected for as a starter (`starter_read` gives that from the box scores when
Stokastic's file still has him on the bench).

    moves, log = news_step(rows, starters, n_games=6)

rows      one contest's entries: [{"entry_id", "lineup": [Player x8, DK slot order],
          "is_locked": [bool x8]}]. `lineup` is changed in place for every move made.
starters  [{"player": Player, "proj": float, "lost": Player or None}], best first. `proj` is
          his projection as a starter. `lost` is the teammate who lost the start, if known.
"""

from __future__ import annotations

import classic as C
from dk import CLASSIC_SLOTS, SALARY_CAP

# The rule. Every number here is a setting the review tested; the report says which were found.
SETTINGS = {
    "share": 0.05,        # each new starter goes into this share of the set ...
    "min_n": 1,           # ... and at least this many lineups (a 20-entry set gets 1)
    "max_n": None,        # a hard top, if set
    "price_band": 800,    # the player who comes out costs from his price - leftover to his price + this
    "which": "gain",      # which lineups take him: "gain" (largest projection gain first), "behind" (the lineups
                          # standing lowest first: each row carries "standing", its locked players' live points
                          # less their projection; a lineup that is ahead is the last to be touched),
                          # "lost" (lineups holding the player who lost the start first, then gain),
                          # "low" (the set's lowest projected lineups first), "late" (most open slots first)
    "min_gain": 0.0,      # he must project at least this much over the player he replaces (None: no bar)
    "max_shared": 5,      # the build's limit: no two lineups in the set share more than this
    "cap": 0.60,          # the build's cap on 5 to 7 game slates: no player over this share of the set
    "cap_games": (5, 7),
    "cap_else": 0.85,     # the build's own top share on any one player on other slates
    "one_per_lineup": True,   # a lineup takes one new starter a run
    "never_out": 7500,    # a player at this price or more never comes out for a new starter
    "never_out_own": None,  # a player at this projected ownership or more never comes out (None: no line)
    "only_out_own": None,   # (low, high): only a player whose projected ownership is in this range comes out (tests)
    "pair": False,        # also try the pair: he comes in for a dearer player and the salary he frees
                          # lifts another open player to the best projection that fits (from `upgrades`)
    "own_top": None,      # the top on a new player (Nov 24 review): his share of the set stops at this many times
                          # his ownership in the file now, and is never under one lineup. It is read from the
                          # file, so it is not a count. None: no such top (the step as it ran to Nov 23)
}


def starter_read(file_minutes, games, lift=5.0, last=5):
    """His projection as a starter when the file still has him on the bench.

    games: his earlier games this season, oldest first, each {"mins", "dk", "starter"}
    (the tool's role.load_logs rows). -> (points, minutes, how) or (None, None, why not).
    Minutes: the mean of his last five starts, else the file's minutes + 5 (the median lift
    of a surprise starter over his file minutes on the saved nights). Points: those minutes
    times his DK points a minute over every earlier game."""
    played = [g for g in games or [] if (g.get("mins") or 0) > 0]
    tot = sum(g["mins"] for g in played)
    if len(played) < 3 or tot <= 0:
        return None, None, "under 3 games on record"
    ppm = sum(g["dk"] for g in played) / tot
    starts = [g["mins"] for g in played if g.get("starter")][-last:]
    if starts:
        mins, how = sum(starts) / len(starts), f"his last {len(starts)} start(s)"
    else:
        mins, how = (file_minutes or 0.0) + lift, f"file minutes + {lift:g}"
    return round(ppm * mins, 2), round(mins, 1), f"{ppm:.2f} a minute x {mins:.1f} minutes ({how})"


def _ids(lineup):
    return frozenset(p.dk_id for p in lineup)


def _seat(lineup, is_locked, i_out, incoming, start_of):
    """The roster with slot i_out's player replaced by `incoming`, open slots re-seated.
    -> new list in DK slot order, or None when the positions do not fill."""
    open_idx = [i for i in range(len(lineup)) if not is_locked[i]]
    movers = [incoming if i == i_out else lineup[i] for i in open_idx]
    seated = _fill([CLASSIC_SLOTS[i] for i in open_idx], movers, start_of)
    if seated is None:
        return None
    new = list(lineup)
    for i, p in zip(open_idx, seated):
        new[i] = p
    return new


def _seat_many(lineup, is_locked, repl, start_of):
    """As _seat, for several replacements at once: repl = {slot index: incoming player}."""
    open_idx = [i for i in range(len(lineup)) if not is_locked[i]]
    movers = [repl.get(i, lineup[i]) for i in open_idx]
    seated = _fill([CLASSIC_SLOTS[i] for i in open_idx], movers, start_of)
    if seated is None:
        return None
    new = list(lineup)
    for i, p in zip(open_idx, seated):
        new[i] = p
    return new


def _fill(open_slots, players, start_of=None):
    """lateswap._fill, imported late so this file can be read without the tool on the path."""
    import lateswap
    return lateswap._fill(open_slots, players, start_of)


def news_step(rows, starters, n_games, settings=None, start_of=None, upgrades=None, allow=None):
    """-> (moves, log). moves: [{"entry_id", "out": [names], "in": [names], "gain", "before": [names], "after": [names]}].

    upgrades: for the pair only, the players a freed salary may buy: your pool, not locked, not out,
    best projection first (the caller sorts; the first that fits is taken).
    allow: optional check on the changed lineup, allow(new_lineup) -> bool (for example: it still passes
    the build's shape with the fresh file's ownership). A lineup it refuses is not changed."""
    S = dict(SETTINGS)
    S.update(settings or {})
    start_of = start_of or (lambda p: p.start or "")
    n = len(rows)
    moves, log = [], []
    if not n or not starters:
        return moves, log
    target = max(S["min_n"], int(S["share"] * n + 0.5))          # 5% of 150 is 8, of 20 is 1
    if S["max_n"] is not None:
        target = min(target, S["max_n"])
    cap_on = S["cap"] is not None and S["cap_games"][0] <= n_games <= S["cap_games"][1]
    cap_n = int(S["cap"] * n) if cap_on else (int(S["cap_else"] * n) if S.get("cap_else") else n)
    new_ids = {s["player"].dk_id for s in starters}
    sets = [_ids(r["lineup"]) for r in rows]
    taken = set()                                   # rows that already took a new starter this run
    for s in starters:
        x, xproj, lost = s["player"], float(s["proj"]), s.get("lost")
        held = sum(1 for ids in sets if x.dk_id in ids)
        need = min(target, cap_n) - held
        top_n = None
        if S.get("own_top") is not None:
            top_n = max(1, int(S["own_top"] * (x.ownership or 0.0) / 100.0 * n + 0.5))
            need = min(need, top_n - held)
        entry = {"name": x.name, "proj": round(xproj, 2), "held": held, "target": target, "added": 0, "why_short": ""}
        # Log only (Nov 22 review, what the swap log was missing): which rule brought him, the cap in
        # lineups, and how many lineups each limit stopped. No decision reads any of these.
        stop = {"dear": 0, "own": 0, "price": 0, "bar": 0, "taken": 0, "seat": 0, "shape": 0, "shared": 0, "same": 0}
        entry.update(rule=s.get("rule"), cap=cap_n, need=max(0, need), stopped=stop,
                     top=top_n, ownNow=round(x.ownership or 0.0, 2))
        log.append(entry)
        if need <= 0:
            entry["why_short"] = ("already at his top" if top_n is not None and held >= top_n
                                  else "already held in that many")
            continue
        cands = []
        for ri, r in enumerate(rows):
            if x.dk_id in sets[ri] or (S["one_per_lineup"] and ri in taken):
                if x.dk_id not in sets[ri]:
                    stop["taken"] += 1
                continue
            n_before, far = len(cands), -1          # log only: the furthest check any open player reached
            lineup, lk = r["lineup"], r["is_locked"]
            left = SALARY_CAP - sum(p.salary for p in lineup)
            n_open = sum(1 for v in lk if not v)
            tot = sum(p.proj for p in lineup)
            for i, y in enumerate(lineup):
                if lk[i] or y.dk_id in new_ids:
                    continue
                if y.salary >= S["never_out"]:
                    far = max(far, 0)
                    continue
                if S["never_out_own"] is not None and y.ownership >= S["never_out_own"]:
                    far = max(far, 1)
                    continue
                if S["only_out_own"] is not None and not (S["only_out_own"][0] <= y.ownership < S["only_out_own"][1]):
                    far = max(far, 1)
                    continue
                if x.salary - y.salary > left or y.salary - x.salary > S["price_band"]:
                    far = max(far, 2)
                    continue
                gain = xproj - y.proj
                if S["min_gain"] is not None and gain < S["min_gain"]:
                    far = max(far, 3)
                    continue
                is_lost = bool(lost is not None and y.dk_id == lost.dk_id)
                if S["which"] == "lost":
                    key = (0 if is_lost else 1, -gain, ri)
                elif S["which"] == "low":
                    key = (tot, -gain, ri)
                elif S["which"] == "late":
                    key = (-n_open, -gain, ri)
                elif S["which"] == "behind":
                    key = (r.get("standing") or 0.0, -gain, ri)
                elif S["which"] == "stack":
                    key = (0 if ri in taken else 1, -gain, ri)          # lineups that already took a new starter this run take the next one too
                else:
                    key = (-gain, ri)
                cands.append((key, ri, {i: x}, gain))
            if S["pair"] and upgrades:
                # the pair with the largest projection gain in this lineup: x in for y, z up to zp
                open_i = [i for i, y in enumerate(lineup)
                          if not lk[i] and y.dk_id not in new_ids and y.salary < S["never_out"]
                          and (S["never_out_own"] is None or y.ownership < S["never_out_own"])]
                best = None
                for i in open_i:
                    for j in open_i:
                        if i == j:
                            continue
                        y, z = lineup[i], lineup[j]
                        budget = left + y.salary + z.salary - x.salary
                        for zp in upgrades:
                            if zp.salary > budget or zp.proj <= z.proj:
                                continue
                            if zp.dk_id in sets[ri] or zp.dk_id in new_ids or zp.dk_id == x.dk_id:
                                continue
                            gain = xproj - y.proj + zp.proj - z.proj
                            if best is not None and gain <= best[0]:
                                break               # upgrades are best first: no later one gains more
                            new = _seat_many(lineup, lk, {i: x, j: zp}, start_of)
                            if new is None or C.legal(new):
                                continue
                            best = (gain, {i: x, j: zp})
                            break
                if best and (S["min_gain"] is None or best[0] >= S["min_gain"]):
                    gain = best[0]
                    key = ((tot, -gain, ri) if S["which"] == "low" else
                           (-n_open, -gain, ri) if S["which"] == "late" else (-gain, ri))
                    if S["which"] == "lost":
                        key = (1, -gain, ri)
                    cands.append((key, ri, best[1], gain))
            if len(cands) == n_before and far >= 0:
                stop[("dear", "own", "price", "bar")[far]] += 1
        cands.sort(key=lambda c: c[0])
        for _key, ri, repl, gain in cands:
            if entry["added"] >= need:
                break
            if S["one_per_lineup"] and ri in taken:
                continue
            r = rows[ri]
            if x.dk_id in sets[ri]:
                continue
            new = _seat_many(r["lineup"], r["is_locked"], repl, start_of)
            if new is None or C.legal(new):
                stop["seat"] += 1
                continue
            if allow is not None and not allow(new):
                stop["shape"] += 1
                continue
            ids = _ids(new)
            if S["max_shared"] is not None and any(len(ids & o) > S["max_shared"] for j, o in enumerate(sets) if j != ri):
                stop["shared"] += 1
                continue
            if any(ids == o for j, o in enumerate(sets) if j != ri):
                stop["same"] += 1
                continue
            moves.append({"entry_id": r.get("entry_id"), "out": [r["lineup"][i].name for i in sorted(repl)],
                          "in": [repl[i].name for i in sorted(repl)], "gain": round(gain, 2),
                          "for": x.name, "rule": s.get("rule"), "pair": len(repl) > 1,
                          "before": [p.name for p in r["lineup"]], "after": [p.name for p in new]})
            r["lineup"][:] = new
            sets[ri] = ids
            taken.add(ri)
            entry["added"] += 1
        if entry["added"] < need:
            entry["why_short"] = "no more lineups fit his price, a slot and the set's limits"
        elif top_n is not None and held + entry["added"] >= top_n:
            entry["why_short"] = (f"his top: {S['own_top']:g} times his {x.ownership:.1f}% ownership in the "
                                  f"fresh file is {top_n} of {n}")
    return moves, log
