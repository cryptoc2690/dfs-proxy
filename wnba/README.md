# WNBA DraftKings Optimizer

A local, GPP-first lineup optimizer for **DraftKings WNBA Classic**. Run one
command, drop in a LineStar projections CSV, get differentiated tournament
lineups. Pure Python standard library — nothing to install, no cloud, no keys.

## Run it (Mac)

Double-click **`WNBA-Optimizer.command`** (first time: right-click → Open).
Or from a terminal:

```bash
cd wnba && python3 app.py     # opens http://localhost:8000
```

Drop your LineStar CSV → set lineup count → **Generate** → **Download**.

## Source of truth — LineStar

One file carries everything the optimizer needs, so there's nothing to
reconcile and no external API:

| LineStar column | Used for |
|---|---|
| `Projected` | projection |
| `PPG` | season average |
| `Floor` / `Ceiling` | the simulator's outcome band (sanity-checked; falls back to a projection-anchored band if a value looks wrong) |
| `ProjOwn` | real projected ownership |
| `Scored` | 0 pre-lock; live/final actual points mid-slate → what late swap runs on |
| `StartingStatus` | starter (1) / bench (2); 0-proj / inactive (4) is excluded |
| `Position` | G/F mapping (PG/SG → G, SF/PF → F) |
| `Salary`, `Team`, `VersusStr` | cap, stacking game keys, home/away |
| `Vegas`, `VegasImplied` | spread and team implied total → which offences are worth stacking |

LineStar's own player IDs are **not** DraftKings IDs. For a re-uploadable file,
drop a **DK entries export** (`DKEntries*.csv`) as well — it carries the real DK
IDs alongside your Entry IDs.

## The ruleset

DK WNBA Classic: **$50,000 cap, 6 players — G, G, F, F, F, UTIL** (2 guards, 3
forwards, 1 flex), and a roster must span **at least two games**. Scoring is the
NBA formula plus double-double +1.5 / triple-double +3.

## How it builds lineups

1. **Blend the projection** — LineStar alone is the most accurate single point
   forecast but it runs optimistic and over-reacts to a player's season
   baseline, so the build runs on `0.5 × LineStar + 0.5 × season PPG`, with the
   daily file taking a third vote wherever it disagrees with LineStar by 2+
   points. Floor and ceiling ride the same ratio, so LineStar's outcome *shape*
   is kept and only its level moves.
2. **Clean the pool** — drop 0-projection players (out / inactive). A player you
   named in your pool whom LineStar prints at 0 is read off the daily file
   instead, provided the daily file gives him 14+ minutes.
3. **Build** a large pool of valid, salary-legal rosters. Two shape rules
   survive, and they apply at different slate sizes:
   - a block of **4+ from one game** must sit in the highest-**owned** game, at
     every slate size;
   - **3+ from one team** needs a bring-back (an opponent), **two-game slates
     only** — winners carry one 100% of the time at two games, 14% at three.

   A 3-3 game split is an ordinary shape and competes on score like any other.
   There is no cap on players per game beyond DK's own, and no exposure or
   overlap cap. Up to $2,000 of unspent salary is allowed; the floor is a junk
   filter, not a spending target.
4. **Simulate** thousands of slates with **independent** per-player noise —
   spread is `max(6.0, 0.40 × projection)` — and rank on half mean, half the
   85th percentile. There is deliberately no game or team correlation factor:
   measured on real results, residual correlation is −0.010 for teammates,
   −0.006 for opponents and +0.019 across games, on 2,179 / 2,500 / 9,436 pairs.
5. **Select** N distinct rosters, honouring any per-player caps you typed and a
   guaranteed exposure floor for each core. The core floor draws from every
   candidate built, not the filtered shortlist, so a low-owned core can never be
   filtered out before it reaches its floor.

Ownership is neutral by default — the lean slider exists, but zero was the
answer on all 23 held-out folds.

Every build appends one JSON line to `logs/builds.jsonl` — settings, projection
source, cores, pool, and the shape of each lineup — so a later review can read
what was actually done instead of reconstructing it from the DK export.

## What was removed, and why

Kept here because the deletions are the main finding, and because a rule that
was measured and dropped should not come back by accident. Each of these was
built in once:

| removed | why |
|---|---|
| sub-10%-owned cap, stud requirement, ownership floor, team-share cap, stack seeding, exposure and overlap caps | held out across 23 contests, every one tested as no effect or worse; together they cost 23 cashes per 23 contests. The damage was a funnel — 120 candidates built, ~55 through the salary floor, ~43 through the ownership floor, 12 chosen |
| positive ownership lean | at +0.35 the tool sat at the 75th within-slate ownership percentile while the top-1% tier sits at the 63rd — leaning in walked past the winners into the crowd. Neutral won on all 23 folds; fading is worse still |
| shared per-game multiplier in the simulator | forced a +0.35 same-game correlation that does not exist in the data, and handed free variance to game stacks. Real rosters with 5 from one game have *lower* residual SD (22.9) than spread 2-2-2 rosters (24.5) |
| 3-3 ban on two-game slates | 3-3 without a 3-block reaches the top 1% at ~1.4% against a 1.55% field. The harm sat in the 3-block rosters, which the bring-back rule already catches |
| the $800 salary floor | set from where *winners'* leftover salary sits, then measured on our own builds: it cost 1.2 of mean and 5 cashes per 309 entries across 18 slates and bought nothing in the tail. Our builds already spend within $700 78% of the time, so it bound only where spending down was right |
| the +6% projection edge on cores | cores DID outperform (30.1 actual against 24.5 for the rest of the pool) but hit 1.01x their projection — they meet it, not beat it. The edge was double-counting a belief already priced into the number that got them cored |
| news-only as the late-swap default | see below |

## Settings & inputs (all optional except the file)

- **Lineups**, **ownership lean** (defaults to 0), **two-game shape rules**
  on/off, **max per team** (3), **salary floor** ($2,000 of leftover).
- **Per-player exposure caps**: one line per player, each carrying its own
  percentage — `A'ja Wilson 40`, `Emma Cannon 10`. One number for everybody is
  not how the decision is made. A bare name falls back to the slider. A cap the
  board cannot satisfy is *reported*, never silently abandoned.
- **Game-theory cores** (type-ahead): flag a sharp's core plays. No projection
  edge — a core earns its place through `minCores` (at least this many cores per
  lineup, default 1) and a guaranteed exposure floor of `ceil(n / (cores + 1))`.
  Measured across 22 slates coring is worth **+0.87 of mean per slate and
  nothing in the tail**; it is NOT a cash edge. Because `minCores` is a hard
  floor, a bad core night costs about a fifth of a set's cashes with no branch
  of the portfolio exempt.
- **His full pool** (type-ahead) + **off-pool allowed per lineup** (0 / 1 / 2):
  a hard build constraint. At 0 every player comes from the pool; at 1–2 the app
  may spend a slot off-pool, but only when it makes a genuinely better lineup.
  Off-pool lineups show a pool-legal alternative you can reveal and swap in.
- **Remove player** (type-ahead): zero someone out (late scratch, missed
  shootaround) and redistribute their minutes/usage to teammates.

## Late swap

Drop a **DK entries export** plus a **fresh mid-slate LineStar pull** (its
`Scored` column carries live actuals, so the tool knows how each lineup already
stands) and optionally a **contest standings export** for a real leaderboard
position instead of a projection-based estimate.

**Re-optimising is the default now, with a bar rather than a trigger.** The old
behaviour fired on news only — a player ruled out, or a projection cut against
what was logged at build time. That was the right instinct and the wrong
mechanism: the fix is what a move has to be worth, not whether somebody said
something. A swap nobody mentioned must clear **+10 simulated points**; one
forced by news clears **+6**. The 10 is measured — replayed across the archive,
realised gain separates from zero at 10-12, not at the 20 first shipped:

```
bar   moves   realised   mean    positive
  6      30       +359   +12.0     26/30
 10      22       +323   +14.7     20/22
 12      17       +284   +16.7     15/17
 20       8        +98   +12.2      6/8
```

The late file really is better information: across the archive, mean absolute
error against actuals runs 10.24 for the lock projection and 4.76 for the late
one, better on 9 of 10 slates. Upward moves too — 9.86 to 7.12 — which is why a
projection *rise* counts as news alongside a cut.

A lineup already projecting to finish in the top 1% is refused any discretionary
move; only news can touch it. Rivals' unplayed slots are modelled as a single
average with no variance, so a projected rank is softer than it looks — soft
enough to refuse a decision, not to make one.

Reaching outside your pool costs more: an off-pool replacement must add **+12**
rather than +10, because off-pool swaps underperform in-pool ones by 10–14
realised points at matched simulated gain.

A **core** is held through a swap unless the news is about the core itself, in
which case it is released: holding a player nobody expects to play is not
respecting a conviction pick.

Both policies are evaluated on every run and both are written to
`logs/swaps.jsonl` with every projection that moved, so the bar can be
recalibrated from live nights rather than from the replay that set it.

### What the contest file can and cannot tell you

DK writes a standings roster **grouped by position — F F F G G UTIL** — not the
`G G F F F UTIL` its own entries export asks you to fill in, with the revealed
players first by salary inside each group and `LOCKED` for the rest. Measured
unanimously across all 5,945 rosters of one contest. So the file says *who has
tipped*; it cannot say which slot anyone sits in, and it is read as a set.

Reconciling it with your entries file is a question about membership: a player
DK shows that you do not have means your file is stale, and a player you have
whose game has tipped that DK does *not* show is provably not entered. Where
those balance, the roster is repaired. Where they do not, the replaced player
had not tipped yet, nothing in the file names them, and the run says so and
leaves the lineup alone.

## Files

| File | Role |
|---|---|
| `app.py` | Local web server + LineStar parsing / pool logic. |
| `gui.py` | The single-page UI (drop, pickers, results, exposure, download). |
| `engine.py` | Pure-Python GPP engine: pool filter, construction, Monte-Carlo sim, selection, pool-legal alternatives. |
| `dk.py` | DK ruleset, name normalization, the Player record. |
| `smoke.py` | End-to-end suite. Run before every commit. |
| `swap_repro.py` | Late-swap fixtures the suite imports. |
| `analysis/` | One-off harnesses kept for their method (see below). |
| `../WNBA-Optimizer.command` | Double-click launcher (Mac). |

## Tests

```bash
python3 wnba/smoke.py        # 126 checks, ~3 min, exits non-zero on failure
```

Not a unit-test file. Nearly every check exists because something shipped
broken, and the check names say which thing — a builder that abandoned exposure
caps when the board ran thin, a late-swap baseline that could never fire on a
real night, a standings parser that corrupted 22 of 50 rosters, a salary floor
whose three copies drifted apart. Several checks pin a value a measurement set,
so a future edit has to argue with the run that set it.

**The summary and `sys.exit` must stay at the end of `smoke.py`.** A section
appended after them is silently skipped and the suite still reports a clean
pass. That has happened twice.

`analysis/` holds harnesses written to answer one question each. They are kept
for the method rather than the numbers:

| | |
|---|---|
| `standings_parse.py` | Standalone reproduction of the 2026-09-22 standings corruption. Needs no data; run it directly. |
| `core_test.py` | Core vs no-core on one slate, scored against real actuals and ranked against the real field. |
| `real_standings.py` | Runs a real entries + standings pair through the reconciler and reports agree / repaired / refused. |

The last two read slate files that were uploaded to a chat session and are not
in the repo. Point `WNBA_SLATE_DIR` at a directory holding them to re-run; they
exit with a clear message otherwise.

## Build log

Every build appends one JSON line to `logs/builds.jsonl` (gitignored). It records
only what is known *before* the slate runs: the settings used, **which projection
produced the build** (source and blend weights — without this a change in results
can be described but never attributed), the core and pool sets in force, and for
each lineup its salary and leftover, projection, ceiling, total ownership, count
of sub-10%-owned players and of $10k+ players, stack shape (biggest team stack,
that team's implied total and whether it cleared the slate median, biggest game
stack and the game's combined total), which cores it held, and every rostered
player with salary, projection, ownership, minutes and implied total.

The late-swap tool reads this file back: it is the only record of what we
believed at lock time, which is what makes "has anything changed?" answerable.

Finishes, actual points and real ownership are not in here — they come from the
post-slate LineStar pull and the DK standings export, joined on the slate date.
Rebuilding during the day appends a second record rather than replacing the
first, so a mid-day core or pool change is visible as a timestamped edit.

## Building your own ownership model (later)

LineStar already gives projected ownership. If you ever want a model fitted to
*your* contests, each `(projections + contest standings)` pair from a day you
played is one training slate; ~10–15 pairs are enough to beat a generic number.
Until then, LineStar's `ProjOwn` is the read.
