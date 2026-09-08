"""NFL Showdown build — read the files, make the lineups, write the upload.

    python3 nfl/app.py --proj stok_proj.csv --field stok_lineups.csv \
        --dk DKEntries.csv --n 150 --split 75 --out upload.csv

Everything is optional except --proj. Without --dk you get a preview but no
uploadable file (only DK's export carries your Entry IDs and DK's player IDs).
Without --field you lose the duplication model and the opponent set, which is
the one component that cannot be built cold-start — so supply it when you can.

`--split K` runs the A/B: K entries from our builder, the rest re-ranked from
the vendor pool, tagged in the log. A split inside ONE contest is the only
design that removes slate luck from the comparison.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine as E
from dk import ROSTER_SIZE, SALARY_CAP, Lineup, normalize_name
from sources import read_dk_entries, read_field, read_projections, read_sharp

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "logs", "nfl_builds.jsonl")


def _read(path):
    if not path:
        return ""
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def _apply_sharp(players, pool_names, core_names):
    for p in players:
        n = normalize_name(p.name)
        p.core = n in core_names
        p.in_pool = p.core or (n in pool_names)
    return (sum(1 for p in players if p.core),
            sum(1 for p in players if p.in_pool))


def _attach_dk_ids(players, dk):
    """Swap in DK's real player IDs. Names are the only bridge between
    Stokastic and DK here, and the brief lists exactly which ones break, so
    report the misses loudly rather than shipping a file with wrong IDs."""
    if not dk or not dk.get("pool"):
        return 0, []
    hit, miss, no_cpt = 0, [], []
    for p in players:
        rec = dk["pool"].get(normalize_name(p.name))
        if rec:
            p.dk_id = rec["dk_id"] or p.dk_id
            p.cpt_dk_id = rec["cpt_dk_id"]
            if not p.cpt_dk_id and p.proj > 0:
                no_cpt.append(p.name)
            hit += 1
        else:
            miss.append(p.name)
    return hit, miss, no_cpt


def _dk_rows(entries, lineups, contest_hdr):
    """One re-uploadable DK line per entry, captain first."""
    lines = [contest_hdr]
    for e, lu in zip(entries, lineups):
        cells = [f'"{p.name.strip()} ({p.upload_id(i == 0)})"'
                 for i, p in enumerate([lu.cpt] + lu.flex)]
        cname = (e["contest"] or "").replace('"', '""')
        lines.append(f'{e["entry_id"]},"{cname}",{e["contest_id"]},{e["fee"]},'
                     + ",".join(cells))
    return "\n".join(lines) + "\n"


def _log(lineups, meta):
    """One JSON line per ENTRY, not per build.

    The `source` field is what makes the whole comparison possible; without it
    logged at build time there is no way to answer whether the custom builder
    earned its complexity. Structure fields are logged so a later review can
    test the mechanism (was it the 5-1 splits?) and not just the outcome.
    """
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        ts = datetime.now().astimezone().isoformat(timespec="seconds")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            for i, lu in enumerate(lineups):
                rec = {
                    "ts": ts, "slate": meta.get("slate"), "format": "showdown",
                    "contest_id": meta.get("contest_id"),
                    "entry_id": meta.get("entry_ids", [None] * len(lineups))[i]
                                if i < len(meta.get("entry_ids", [])) else None,
                    "source": lu.source,
                    "captain": lu.cpt.name, "captain_id": lu.cpt.dk_id,
                    "players": [{"name": p.name, "id": p.dk_id, "pos": p.pos,
                                 "team": p.team, "salary": p.salary,
                                 "proj": p.proj, "own": p.ownership,
                                 "boom": p.boom, "core": p.core,
                                 "pool": p.in_pool}
                                for p in [lu.cpt] + lu.flex],
                    "salary": lu.salary, "leftover": SALARY_CAP - lu.salary,
                    "proj": lu.proj, "own_sum": lu.own_sum,
                    "split": lu.split_label(), "major_team": lu.major_team(),
                    "has_dst": any(p.is_dst for p in lu.players),
                    "metrics": lu.metrics,
                    "settings": meta.get("settings", {}),
                    "contest_state": meta.get("contest_state", {}),
                }
                fh.write(json.dumps(rec) + "\n")
    except Exception as exc:                     # never let logging break a build
        print(f"  ! log write failed: {exc}", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description="DK NFL Showdown builder")
    ap.add_argument("--proj", required=True, help="Stokastic projections CSV")
    ap.add_argument("--field", help="Stokastic lineups CSV (the opponent field)")
    ap.add_argument("--dk", help="DK entries export (needed for an upload file)")
    ap.add_argument("--pool", help="sharp's pool, one name per line")
    ap.add_argument("--cores", help="sharp's cores, one name per line")
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--split", type=int, default=0,
                    help="how many of --n come from OUR builder; rest from vendor")
    ap.add_argument("--sims", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--own-lean", type=float, default=E.OWN_LEAN,
                    help="positive leans toward the field (showdown default)")
    ap.add_argument("--captain-cap", type=float, default=E.CAPTAIN_CAP)
    ap.add_argument("--min-captains", type=int, default=E.MIN_CAPTAINS)
    ap.add_argument("--player-cap", type=float, default=E.PLAYER_CAP)
    ap.add_argument("--max-leftover", type=int, default=E.MAX_LEFTOVER,
                    help="most salary a lineup may leave unspent")
    ap.add_argument("--min-proj", type=float, default=E.MIN_PROJ,
                    help="a roster spot must project at least this much")
    ap.add_argument("--max-off-pool", type=int, default=None,
                    help="max non-pool players per lineup (needs --pool)")
    ap.add_argument("--entries-at-build", type=int, default=None,
                    help="contest entries so far — fill %% is a real edge, log it")
    ap.add_argument("--field-cap", type=int, default=None)
    ap.add_argument("--expect-entries", type=int, default=None,
                    help="your read of the FINAL field size; scales duplication "
                         "because the vendor pool models a capped field")
    ap.add_argument("--out", default="nfl_upload.csv")
    a = ap.parse_args(argv)

    print("== reading ==")
    players, rep = read_projections(_read(a.proj))
    if rep.get("error"):
        print(f"  projections: {rep['error']}")
        if rep.get("headers"):
            print(f"  headers seen: {rep['headers']}")
        return 2
    print(f"  projections: {rep['players']} players from {rep['rows']} rows")
    print(f"    matched: {', '.join(f'{k}<-{v}' for k, v in rep['matched'].items())}")
    if rep["unmatched"]:
        print(f"    NOT FOUND (degrading): {', '.join(rep['unmatched'])}")

    teams = sorted({p.team for p in players if p.team})
    print(f"  teams: {teams}")
    if len(teams) != 2:
        print("  ! showdown expects exactly 2 teams — check the file/filters")
    # Fill in opponents when the file didn't carry them.
    if len(teams) == 2:
        for p in players:
            if not p.opponent and p.team:
                p.opponent = teams[1] if p.team == teams[0] else teams[0]

    dk = read_dk_entries(_read(a.dk)) if a.dk else None
    if dk:
        hit, miss, no_cpt = _attach_dk_ids(players, dk)
        print(f"  DK entries: {len(dk['entries'])} entries, "
              f"{len(dk['pool'])} pool players, {hit} IDs matched")
        if miss:
            print(f"    ! {len(miss)} unmatched by name: {', '.join(miss[:8])}"
                  + (" ..." if len(miss) > 8 else ""))
        if no_cpt:
            print(f"    ! no CAPTAIN id for: {', '.join(no_cpt[:8])} — these "
                  f"cannot be captained in the upload file")

    by_id = {p.dk_id: p for p in players}
    by_name = {normalize_name(p.name): p for p in players}
    field, frep = ([], {})
    if a.field:
        field, frep = read_field(_read(a.field), by_id=by_id, by_name=by_name)
        if frep.get("error"):
            print(f"  field: {frep['error']} — headers {frep.get('headers')}")
        else:
            print(f"  field: {frep['parsed']} of {frep['rows']} vendor lineups "
                  f"parsed ({frep.get('unresolved_rosters', 0)} unresolved), "
                  f"slots {frep['cpt_col']} + {len(frep['flex_cols'])} flex")

    pool_names = read_sharp(_read(a.pool)) if a.pool else set()
    core_names = read_sharp(_read(a.cores)) if a.cores else set()
    ncore, npool = _apply_sharp(players, pool_names, core_names)
    if pool_names or core_names:
        print(f"  sharp: {ncore} cores, {npool} in pool")

    # --- the arithmetic check that catches the per-slot ownership trap -----
    # Showdown reports ownership per ROSTER SLOT, and the six slots are split
    # across two columns: the flex column sums to 500% (five slots) and the
    # captain column to 100% (one). Together 600%. Reading either one as "total
    # ownership" silently corrupts every leverage and duplication figure, which
    # is why this prints on every run rather than living in a comment.
    flex_own = sum(p.ownership for p in players)
    cpt_own = sum(p.cpt_own for p in players)
    print(f"  ownership: flex {flex_own:.0f}% + captain {cpt_own:.0f}% "
          f"= {flex_own + cpt_own:.0f}% across six slots")
    if not (450 <= flex_own <= 650 and 550 <= flex_own + cpt_own <= 650):
        print("    ! expected flex ~500% and flex+captain ~600%. Check which")
        print("    ! ownership column was read — they are different quantities.")

    print("\n== simulating ==")
    mat = E.simulate(players, sims=a.sims, seed=a.seed)
    bar, sampled = E.field_bar(field, mat, a.sims, seed=a.seed) if field else (None, 0)
    if bar:
        srt = sorted(bar)
        print(f"  field bar from {sampled} opponents: "
              f"median {srt[len(srt)//2]:.1f}, p10 {srt[len(srt)//10]:.1f}")
    else:
        print("  no field file — ranking on simulated score alone, no win rate")
    idx = E.dupe_index(field) if field else {}
    modelled = E.field_size(field) if field else 0
    dupe_scale = 1.0
    if modelled and a.expect_entries:
        dupe_scale = max(1.0, a.expect_entries / modelled)
    if field:
        print(f"  vendor pool: {len(idx):,} distinct lineups modelling "
              f"{modelled:,.0f} opponent entries")
        if a.expect_entries:
            print(f"  expecting {a.expect_entries:,} real entries -> "
                  f"duplication scaled x{dupe_scale:.2f}")
        elif a.field_cap and modelled < a.field_cap * 0.9:
            print(f"  ! contest holds up to {a.field_cap:,}. If it fills past "
                  f"{modelled:,.0f}, duplication here is understated — pass "
                  f"--expect-entries with your read of the final field.")

    n_mine = a.n if a.split == 0 else min(a.split, a.n)
    n_vendor = a.n - n_mine
    chosen = []

    if n_mine:
        print(f"\n== building {n_mine} (ours) ==")
        cands = E.build_candidates(
            players, max(4000, n_mine * 30), teams=teams,
            rng=__import__("random").Random(a.seed),
            max_off_pool=a.max_off_pool, max_leftover=a.max_leftover,
            min_proj=a.min_proj,
        )
        print(f"  candidates: {len(cands)}")
        if not cands:
            print("  ! built nothing — check salaries/teams in the projections file")
            return 3
        if bar:
            E.rank(cands, mat, bar, a.sims, idx, own_lean=a.own_lean,
                   dupe_scale=dupe_scale)
        else:
            for lu in cands:
                sc = E.score_lineup(lu, mat, a.sims)
                lu.metrics = {"mean": round(sum(sc) / a.sims, 2),
                              "dupes": round(E.estimated_dupes(lu, idx, scale=dupe_scale), 2)}
                lu.metrics["score"] = lu.metrics["mean"] / (1 + lu.metrics["dupes"])
            cands.sort(key=lambda l: -l.metrics["score"])
        mine = E.select(cands, n_mine, captain_cap=a.captain_cap,
                        min_captains=a.min_captains, player_cap=a.player_cap,
                        split_targets=E.SPLIT_TARGETS)
        chosen += mine

    if n_vendor:
        print(f"\n== taking {n_vendor} (vendor, re-ranked) ==")
        if not field:
            print("  ! --split needs --field; skipping the vendor arm")
        else:
            chosen += E.vendor_arm(field, n_vendor, players_by_id=by_id,
                                   captain_cap=a.captain_cap,
                                   min_captains=a.min_captains,
                                   player_cap=a.player_cap,
                                   dupe_scale=dupe_scale)

    if not chosen:
        print("no lineups produced")
        return 3

    # --- report -----------------------------------------------------------
    print(f"\n== {len(chosen)} lineups ==")
    splits, caps, arms = {}, {}, {}
    for lu in chosen:
        splits[lu.split_label()] = splits.get(lu.split_label(), 0) + 1
        caps[lu.cpt.name] = caps.get(lu.cpt.name, 0) + 1
        arms[lu.source] = arms.get(lu.source, 0) + 1
    print(f"  arms:     {splits and arms}")
    print(f"  splits:   {dict(sorted(splits.items(), reverse=True))}")
    print(f"  captains: {len(caps)} distinct, top = "
          + ", ".join(f"{k} {v}" for k, v in
                      sorted(caps.items(), key=lambda kv: -kv[1])[:5]))
    sal = [lu.salary for lu in chosen]
    print(f"  salary:   {min(sal)}-{max(sal)}")
    own = [lu.own_sum for lu in chosen]
    print(f"  own sum:  {sum(own)/len(own):.0f} avg")
    dup = [lu.metrics.get("dupes", 0) for lu in chosen]
    print(f"  est dupes: {sum(dup)/len(dup):.2f} avg, {max(dup):.1f} max")

    print("\n  top 5:")
    for lu in chosen[:5]:
        m = lu.metrics
        print(f"   {lu.split_label()} CPT {lu.cpt.name:<22} "
              f"${lu.salary} proj {lu.proj:6.1f} own {lu.own_sum:5.1f} "
              f"win {m.get('win', 0):.4f} dup {m.get('dupes', 0):.1f}")
        print(f"        " + ", ".join(f"{p.name}({p.team})" for p in lu.flex))

    # --- upload file -------------------------------------------------------
    meta = {
        "slate": datetime.now().astimezone().date().isoformat(),
        "settings": {"n": a.n, "split": a.split, "sims": a.sims,
                     "own_lean": a.own_lean, "captain_cap": a.captain_cap,
                     "min_captains": a.min_captains, "player_cap": a.player_cap,
                     "max_off_pool": a.max_off_pool, "seed": a.seed},
        "contest_state": {"entries_at_build": a.entries_at_build,
                          "field_cap": a.field_cap,
                          "expect_entries": a.expect_entries,
                          "vendor_field_modelled": modelled,
                          "dupe_scale": round(dupe_scale, 3),
                          "fill_pct": (round(100.0 * a.entries_at_build / a.field_cap, 2)
                                       if a.entries_at_build and a.field_cap else None)},
    }
    if dk and dk["entries"]:
        entries = dk["entries"][:len(chosen)]
        if len(entries) < len(chosen):
            print(f"\n  ! DK file has {len(entries)} entries but {len(chosen)} "
                  f"lineups were built — writing {len(entries)}")
            chosen = chosen[:len(entries)]
        hdr = "Entry ID,Contest Name,Contest ID,Entry Fee," + ",".join(dk["slots"])
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(_dk_rows(entries, chosen, hdr))
        print(f"\n  wrote {a.out} ({len(chosen)} entries)")
        meta["entry_ids"] = [e["entry_id"] for e in entries]
        meta["contest_id"] = entries[0]["contest_id"]
    else:
        print("\n  no --dk file, so no uploadable CSV was written")

    if meta["contest_state"]["fill_pct"] is not None:
        f = meta["contest_state"]["fill_pct"]
        print(f"  contest fill at build: {f}% "
              f"({'OVERLAY — pool is guaranteed' if f < 84.1 else 'no overlay'})")

    _log(chosen, meta)
    print(f"  logged {len(chosen)} entries to {LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
