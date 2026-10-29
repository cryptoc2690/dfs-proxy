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

**Slot 1 takes Stokastic's projections and nothing else.** A file with no Std Dev
column, or with ETR's columns (FPTS, ProjOwn, DKSlateID), is refused by name and
the build does not run. On Oct 20 ETR's file ran in that slot unnoticed; the
only signal was a note. The log now records every file's name and a short
fingerprint, so a mix-up is visible at grading too.

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
   Std Dev. Players projected 10+ draw from an even (normal) range; under 10, a
   right-skewed one, since that night is mostly minutes that may not come. No
   team or game correlation by default — WNBA measured it at about zero; NBA has
   not been measured, so the knob (`GAME_SD`) is at 0.
3. **Build** thousands of candidates. Classic fills slots rather than players —
   base positions, then G and F, then UTIL — so every roster is legal by
   construction. Showdown picks the captain first. Weight is projection cubed.
   No shape rules.
4. **Rank** each candidate on how often it clears the field's 99th-percentile
   score in the same simulated world (the field is Stokastic's lineups file; on
   classic, a player whose share of it is 5+ points off Stokastic's projected
   ownership is re-weighted to the projected number), divided by `(1 + expected duplicates) ^
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

## The slate decides (classic)

The tool sets two things from the game count, so there's nothing to click (`engine.slate_rules`, from `NBA Thorough Review Report 10-27.md`):

| Games | Neutral build | Pool round-out |
|---|---|---|
| 2–4 | off | off |
| 5–7 | on | on |
| 8+ | off | on |

Slot 1 shows the choice next to the games, and the check line repeats it. Both settings are **Auto** on the page; On and Off override, and the build says "set by hand". Late swap follows the same rule. Cores are plain pool players either way; only a hand-set Off brings back the old core floor.

## Neutral build (5–7 games)

Built the way NFL showdown is: field ownership is shown, never steers.
- **Main game rotated:** each lineup's biggest game block is rotated evenly across the slate's games.
- **Sides 50/50:** inside that game, the two teams are held even.
- **Team limit, sized by slate:** none at 2 games; 1.25× the even share at 3–7 games (63% of entries at 6); 1.5× at 8+ (47% at 11). The even share is 1 − (1 − 1/2G)^8.
- **Stokastic's half:** dropped at 2–4 games; kept at 5+ and held to the same rules.
- **Cores** are plain pool players. With no pool typed, they carry nothing.
- **8+ games (only when set On by hand):** candidates are generated with team quotas, so the limit can be reached.
- **20-max:** no lineup from past the 1,000th candidate; a short slot is filled inside it, under the caps first.

The check line says what was applied, with the arm split as built: `neutral: 6 games · main game rotated, sides 50/50 · team limit 63% · 75/75 ours/Stokastic`. `--neutral on|off|auto` and `--pool-adds on|off|auto` on the command line.

## Settings

Pool (type-ahead or pasted list; cores are no longer on the page — the sheet's pool already lists them), players allowed off the pool, a
per-player cap list (`Name 20` = at most 20 lineups, `Name 20%` = a percentage; one per line), a **minimum-lineups** list
(`Andre Drummond 2` — a number of lineups, not a percentage; the player is added to
the pool and weighted like a core in construction so the minimum is reachable), late scratches to remove, the
ownership lean (neutral by default), and the minimum projection for a roster spot.

**Lineups, ours and contest size are automatic on the page.** Left blank, lineups is
the biggest contest's entry count in the DK file, ours is half (11 of a 20-max, which
the build makes 15/5 on 5–7 games), and contest size is the contest's prize ÷ fee ÷ 0.84
(DK's real counts matched within 0.5% on 22 of 23 contests, Oct 20–27). Slot 3 shows
them as soon as the DK file is dropped; typing a number overrides. The command line
still takes `--n`, `--split` and `--field-cap` as typed.

**Rounding out the pool** (classic, on by default, `--no-pool-adds` turns it off).
It adds three groups of players who aren't on your sheet:
- **Cheap:** the 5 best points per $1,000 at $4,500 or less, projected 14+.
- **Mid:** the 5 best points per $1,000 from $4,600 to $7,500, projected 14+.
- **Studs:** the 3 highest projections above $7,500 among players under 10% projected ownership. Each stud gets a minimum of 2× his projected ownership, in lineups, at least 1 (Edwards at 3.3% in a 150-max: 10).

The cheap and mid adds become legal but aren't forced. Without the minimum, a stud in the pool reached only 1–3% of lineups on Oct 21. Cap a name at 0 and he's skipped (a stud is replaced by the next in line); a minimum you type wins over the automatic one.

**Late scratch** works the WNBA way: the player is zeroed and about 65% of his
production goes to teammates, weighted to his position. A fresh Stokastic pull
is better when there is time.

## Late swap (mid-slate, classic)

Drop a **fresh Stokastic projections** pull in slot 1, your **current DK entries
export** in slot 3 and the **live contest standings** in slot 5, then press **Late
swap** (or `python3 nba/app.py --lateswap --proj … --dk … --standings …`).

The WNBA tool's rules, NBA's files (`nba/lateswap.py` has the reasoning):

- Locked players never move, and stay in their exact slot. Cores are ordinary
  pool players.
- **Bad news reopens a lineup:** a player ruled out (or typed as a late scratch), a
  projection cut of 25% and 5+ points against your build, or a move to the bench
  where his projection or minutes moved **8%+**. Good news (projection up, moved
  into the lineup) is listed but reopens nothing. A move under 8% is a role
  change, not news. With no build to compare against, a held bench player with a
  cheaper starter at his position projecting 1.5x also counts.
- **A ruled-out player is always swapped out**, whatever the gain.
- **A news swap takes the news player out** and needs **+8** simulated points.
  Any other move is a **no-news swap**, even in a lineup holding the news player:
  it needs **+14**. Good news
  (moved into the lineup, projection up) comes in this way. No-news swaps are on
  by default; untick "allow no-news swaps" to stop them. Oct 28: Capela's bench
  news let 25 lineups keep Capela and sell Curry at +8.
- **Minutes cut:** a starter who stays a starter is news when his minutes drop
  25% and 5+.
- **No swap moves more than two players**, news or not. A ruled-out player still
  always goes, with at most one other player unless nothing that small is legal. Every saved swap night: two-player swaps +129 over 19, four-plus
  player rebuilds −163 over 14.
- **Neutral across the set:** a swap that adds a team already at the team limit,
  or tips its main game's sides past 50/50, is skipped for the next-best roster
  that fits. A ruled-out player is still always replaced.
- Every run saves its input files (`nba/logs/swap_inputs/<time>/`) so it can be
  replayed exactly.
  An off-pool player needs +17 of projection each; stranding $2,000+ needs +17.
- A lineup projecting top 1% is never moved without news.
- Ahead in the standings leans to safety, behind leans to ceiling; ownership is
  never faded. A small tiebreak keeps the next game to tip swappable.
- **Projected finals use the clock.** DK's standings carry each entry's player-minutes
  still to play, so a game in progress counts what is left in it — for your entries
  and the field alike — instead of reading a live score as a final.
- DK's standings are the final word on who has tipped; an entries file that
  disagrees is left alone and named.
- Every decision is logged in `nba/logs/nba_swaps.jsonl` with its gain.

The page lists every lineup with its live score, minutes left, projected final and
why it moved or held; untick any swap and the download keeps that entry as entered.
Pool and cores default to your last build of the slate.

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
