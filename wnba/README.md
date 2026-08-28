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
| `Floor` / `Ceiling` | outcome band (sanity-checked; falls back to a projection-anchored band if a value looks wrong) |
| `ProjOwn` | real projected ownership → GPP leverage |
| `StartingStatus` | starter (1) gets a small construction lean; 0-proj / inactive is excluded |
| `Position` | G/F mapping (PG/SG → G, SF/PF → F) |
| `Salary`, `Team`, `VersusStr` | cap, stacking game keys, home/away |

LineStar's own player IDs are **not** DraftKings IDs, so lineups export **by
name** for manual entry.

## The ruleset

DK WNBA Classic: **$50,000 cap, 6 players — F, F, F, G, G, UTIL** (2 guards, 3
forwards, 1 flex). Scoring is the NBA formula plus double-double +1.5 /
triple-double +3.

## How it builds lineups

1. **Clean the pool** — drop 0-projection players (out / inactive) and,
   dynamically, the minutes-punts: keep players by *upside* (ceiling), with the
   cutoff scaling to slate depth.
2. **Build** a large pool of valid, salary-legal, game-stacked lineups, with a
   small lean toward confirmed starters and a salary-reserve that adapts to
   whether the slate is stars-and-scrubs or balanced.
3. **Simulate** thousands of slates (Beta-PERT outcomes + a shared per-game
   multiplier, so stacking pays off) and rank by ceiling.
4. **Leverage-adjust** on *over-ownership* (ownership per point of ceiling)
   using LineStar's projected ownership, so the set fades unbacked chalk without
   fading good chalk.
5. **Select** N under a per-player exposure cap **and** a pairwise-overlap cap,
   so the lineups are genuinely different.

## Settings & inputs (all optional except the file)

- **Lineups**, **min game stack**, **max exposure**, **fade chalk (leverage)**.
- **Game-theory cores** (type-ahead): flag a sharp's core plays — small edge,
  treated as chalk for leverage, diversified across the set, counted in-pool.
- **His full pool** (type-ahead) + **off-pool allowed per lineup** (0 / 1 / 2):
  a hard build constraint. At 0 every player comes from the pool; at 1–2 the app
  may spend a slot off-pool, but only when it makes a genuinely better lineup.
  Off-pool lineups show a pool-legal alternative you can reveal and swap in.
- **Remove player** (type-ahead): zero someone out (late scratch, missed
  shootaround) and redistribute their minutes/usage to teammates.

## Files

| File | Role |
|---|---|
| `app.py` | Local web server + LineStar parsing / pool logic. |
| `gui.py` | The single-page UI (drop, pickers, results, exposure, download). |
| `engine.py` | Pure-Python GPP engine: pool filter, construction, Monte-Carlo sim, selection, pool-legal alternatives. |
| `dk.py` | DK ruleset, scoring, name normalization, the Player record. |
| `../WNBA-Optimizer.command` | Double-click launcher (Mac). |

## Build log

Every build appends one JSON line to `logs/builds.jsonl` (gitignored). It records
only what is known *before* the slate runs: the settings used, the core and pool
sets in force, and for each lineup its salary and leftover, projection, ceiling,
total ownership, stack shape (biggest team stack, that team's implied total and
whether it cleared the slate median, biggest game stack and the game's combined
total), which cores it held, and every rostered player with salary, projection,
ownership, minutes and implied total.

Finishes, actual points and real ownership are not in here — they come from the
post-slate LineStar pull and the DK standings export, joined on the slate date.
Rebuilding during the day appends a second record rather than replacing the
first, so a mid-day core or pool change is visible as a timestamped edit.

## Building your own ownership model (later)

LineStar already gives projected ownership. If you ever want a model fitted to
*your* contests, each `(projections + contest standings)` pair from a day you
played is one training slate; ~10–15 pairs are enough to beat a generic number.
Until then, LineStar's `ProjOwn` is the read.
