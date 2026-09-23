"""Read tonight's real files through the tool and report what it now does.

The point is the reconciliation: the entries export was downloaded at 07:50 and
the contest file is the same slate, so every one of the 50 entries should come
back as "already agrees" and NOT as 22 rewritten rosters.
"""
import sys
# Resolve the package from this file, not from an absolute path: these
# lived in a session scratchpad that no longer exists.
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import app
from dk import normalize_name

# The slate files this reads were uploaded to a chat session and are not
# in the repo. Point WNBA_SLATE_DIR at a directory holding them to re-run;
# the script is kept for its METHOD, which is the part worth having.
import os
U = os.environ.get("WNBA_SLATE_DIR", "/root/.claude/uploads/c746c511-d086-55ac-83f0-a7baebe445cb/")
if not U.endswith("/"):
    U += "/"
if not os.path.isdir(U):
    raise SystemExit(f"slate files not found in {U!r}; set WNBA_SLATE_DIR to a directory holding them")
entries_txt = open(U + "95120663-DKEntries-14.csv", encoding="utf-8-sig").read()
cont_txt = open(U + "1c0a0d1c-contest-standings-195934561.csv",
                encoding="utf-8-sig").read()

dk = app.parse_dk_entries(entries_txt)
slots = dk["slots"]
print(f"entries file: {len(dk['entries'])} entries, slots {' '.join(slots)}")

contest = app.parse_contest_standings(cont_txt, slots)
print(f"contest file: {contest['field']:,} rosters")
by_id = {e["entryId"]: e for e in contest["entries"]}

unreadable = agree = repaired = refused = 0
for e in dk["entries"]:
    live = by_id.get(e["entryId"])
    if not live:
        continue
    shown = live["revealed"]
    if shown is None:
        unreadable += 1
        print(f"  UNREADABLE {e['entryId']}: {live['lineupCell']!r}")
        continue
    fixed, why = app._reconcile_with_dk(e["names"], shown, slots, dk["pool"])
    if why:
        refused += 1
        print(f"  REFUSED  {e['entryId']}: {why}")
    elif fixed is None:
        agree += 1
    else:
        repaired += 1
        ch = [(o, n) for o, n in zip(e["names"], fixed) if o != n]
        print(f"  REPAIRED {e['entryId']}: " +
              ", ".join(f"{o} -> {n}" for o, n in ch))
print(f"\n{agree} agree, {repaired} repaired, {refused} refused, "
      f"{unreadable} unreadable")

# every revealed name must actually be on the roster we ended up with
holes = 0
for e in dk["entries"]:
    live = by_id.get(e["entryId"])
    if not live or live["revealed"] is None:
        continue
    have = {normalize_name(n) for n in e["names"]}
    holes += sum(1 for n in live["revealed"] if n not in have)
print(f"revealed players missing from the roster we will use: {holes}")

# and the headline: the entry that got wrecked last night
e = next(x for x in dk["entries"] if x["entryId"] == "5266239772")
sal = sum(dk["pool"][normalize_name(n)]["salary"] for n in e["names"])
print(f"\n5266239772 -> {', '.join(e['names'])}")
print(f"  ${sal:,}, {len(set(e['names']))} distinct players")
print(f"  legal: {app._roster_illegal(e['names'], by_id['5266239772']['revealed'], slots, dk['pool']) or 'yes'}")
