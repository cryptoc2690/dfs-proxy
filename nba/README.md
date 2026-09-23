# NBA lineup builder — classic and showdown

Builds DraftKings NBA lineups from Stokastic's exports and writes a file you can
upload straight back to DK. Pure Python, no installs. The process is the NFL
tool's; the construction thinking is WNBA's; nothing is carried over from the
old Base44 tool except what its parsers knew about the file layouts.

## Run it

Double-click **`NBA-Optimizer.command`**, or:

```
cd ~/Desktop/dfs-proxy && git pull && python3 nba/app.py      # http://127.0.0.1:8020
```

Drop the files, press **Build lineups**, then **Download DK file**. The command
line runs the same build:

```
python3 nba/app.py --proj proj.csv --field lineups.csv --dk DKEntries.csv \
  --n 150 --split 75 --field-cap 47000 --linestar linestar.csv --out upload.csv
```

## The files

| Slot | File | Needed? |
|---|---|---|
| 1 | Stokastic **projections** export | **Yes** |
| 2 | Stokastic **lineups** export | Strongly recommended — it is the opponent field |
| 3 | Your **DK entries** export | Yes, for an uploadable file |
| 4 | **LineStar** export | Optional — Vegas, its projection and starting status, logged only |
| 5 | DK **contest standings** | After the slate, to grade the build |

Pull 1 and 2 in the same minute: they carry separate ownership snapshots.

**LineStar is logged, never used — and that is checked.** NFL logs its Vegas
and never builds on it, and measured its projection as the same number as
Stokastic's on one slate. NBA logs a little more: Vegas (Stokastic's NBA file has
none, and the old proxy that supplied it is retired), LineStar's projection
beside Stokastic's (never compared on NBA — the log is what lets the grader
screen it before anything uses it), and its starting status. The build is
byte-identical with and without the file (`smoke.py` checks it). The one thing
it says before lock: anyone LineStar has at 0 or out while Stokastic still
projects him, so you can decide on a late scratch.

Classic or showdown is read from the DK file's slot header (`PG … UTIL` or
`CPT, UTIL × 5`), falling back to the number of teams in the projections.

## How a build works

1. **Read** the projections (columns are sniffed and anything matched by guess is
   named), join DK's player IDs, and take DK's `Roster Position` column as the
   authority on which slots a player may fill.
2. **Simulate** every player, calibrated exactly to Stokastic's projection and
   Std Dev. No team or game correlation by default — WNBA measured it at about
   zero; NBA has not been measured, so the knob (`GAME_SD`) is at 0.
3. **Build** thousands of candidates. Classic fills slots rather than players —
   base positions, then G and F, then UTIL — so every roster is legal by
   construction. Showdown picks the captain first. Weight is projection cubed.
   No shape rules.
4. **Rank** each candidate on how often it clears the field's 99th-percentile
   score in the same simulated world, divided by `(1 + expected duplicates) ^
   0.25`. Duplication counts on classic as well as showdown, because NBA's small
   pool of real scorers makes the right eight somebody else's eight too (the old
   standings files show 4-, 8- and 10-way ties at the top of real fields).
5. **Select** score-first under your cores' floors and any per-player caps you
   typed. There is no board-wide exposure cap: heavy exposure is reported.
6. **Write** the DK file with every contest in the export getting the top of
   one ranked list, slotted so the latest tips sit in UTIL, G and F. Every row is
   checked against DK's rules before the file exists.
7. **Log** one line per entry to `nba/logs/nba_builds.jsonl`.

`--split` runs NFL's A/B: that many lineups from our builder and the rest
re-ranked out of Stokastic's pool, in the same contest, tagged in the log.

## Settings

Pool and cores (type-ahead or pasted lists), players allowed off the pool, a
per-player cap list (`Name 20`, one per line), late scratches to remove, the
ownership lean (neutral by default), contest size, and the minimum projection
for a roster spot.

**Late scratch** works the WNBA way: the player is zeroed and about 65% of his
production goes to teammates, weighted to his position. A fresh Stokastic pull
is better when there is time. **Late swap is not in this tool** — it is paused
pending its own discussion.

The page also gives WNBA's read on the build: a check on each core (ceiling
for its position, starting flag, minutes), how many lineups each core ended up
in against its floor, the heaviest play, the team lean, and removals. **Pool
gaps** use NFL's rule — real upside for the position, with ownership that lags
it — and list the whole set.

## Grading

```
python3 nba/app.py --grade contest-standings-123.csv \
  --prize-pool 15000 --first-prize 2000 --paid-from 3000 --paid-to 9900
```

or drop the standings into slot 5. It finds the build you actually entered (the
one whose rosters match what DK holds for your Entry IDs), then reports ranks,
cashes, real duplication, results by arm and by shape, and every player you
used: projected vs actual points, projected vs real ownership, your exposure.

Before trusting any of it, it rebuilds each entry's Points from the players'
FPTS. If those do not agree, the file was not read cleanly and it says so.
Real ownership is the **sum** of a player's rows in DK's player block, which
lists him once per roster slot.

## What is borrowed and waiting on NBA results

Every default below came from another sport, is pinned in `smoke.py`, and is
the first thing to re-fit once graded NBA slates exist:

| Setting | Value | From |
|---|---|---|
| Duplication price `DUPE_A`, `DUPE_B`, `DUPE_EXP` | 0.45, 0.67, 0.25 | NFL showdown, four contests |
| Field bar | 99th percentile | NFL |
| Construction exponent | 3 | NFL, five contests × eight seeds |
| Ownership lean | 0 | WNBA 23 folds, NFL showdown |
| Game correlation | 0 | WNBA residuals |
| Classic max leftover | $2,000 | WNBA / NFL |
| Showdown max leftover | $5,000 | NFL showdown |
| Removal share / caps | 65%, 40%, 8 pts | WNBA |

**The file layouts are the other unknown.** The Stokastic NBA column names come
from the old Base44 tool's parsers, not from a file in hand. The first real
projections and lineups export of the season should be run through
`python3 nba/app.py --proj … --field … --dk …` and the notes read: every column
it matched is listed, any guess is flagged, and the ownership total is checked
against 800.

## Tests

```
python3 nba/smoke.py
```

Synthetic files in the real layouts, including their traps: the pool header on
an entry row, three contests in one export, the one-cell `Lineups` column,
quote debris, accented names, per-slot ownership rows, DK's grouped standings
order. Run it before every commit. Its summary and exit stay at the end of the
file — a section added after them is silently skipped.
