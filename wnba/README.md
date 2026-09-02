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
2. **Clean the pool** — drop 0-projection players (out / inactive) and,
   dynamically, the minutes-punts: keep players by *upside* (ceiling), with the
   cutoff scaling to slate depth.
3. **Build** a large pool of valid, salary-legal lineups under the construction
   rules: at most one sub-10%-owned player (cores exempt), at least one $10k+
   player, and on a two-game slate no 3-3 split, no team block without a
   bring-back, and the majority in the higher-owned game. A share of lineups are
   *seeded* with a correlation stack rather than hoping one falls out of
   projection weighting. Every rule relaxes automatically if the slate can't
   support it, and each is switchable.
4. **Simulate** thousands of slates (Beta-PERT outcomes + a shared per-game
   multiplier, so stacking pays off) and rank on half production, half upside.
5. **Lean toward the consensus** — the ownership slider now defaults *positive*.
   The bottom ownership fifth of our own candidates is dropped outright; that is
   the one part of the ownership curve that is reliable slate after slate.
6. **Select** N under a per-player exposure cap, a pairwise-overlap cap, a
   pool-level team cap, and a per-core exposure floor, so the lineups are
   genuinely different *and* still built around your conviction plays. The core
   floor draws from every candidate built, not the filtered shortlist, so a
   low-owned core can never be filtered out before it reaches its floor.

Every build appends one JSON line to `logs/builds.jsonl` — settings, projection
source, cores, pool, and the shape of each lineup — so a later review can read
what was actually done instead of reconstructing it from the DK export.

## Settings & inputs (all optional except the file)

- **Lineups**, **min game stack**, **stack seeking**, **max exposure**,
  **ownership lean**, **sub-10%-owned allowed**, **two-game shape rules**.
- **Game-theory cores** (type-ahead): flag a sharp's core plays. No projection
  edge — a core earns its place through a guaranteed exposure floor, so a
  conviction pick can never get squeezed to 1-of-N.
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

It fires **on news only** by default: a player ruled out, or a projection cut
against what was logged at build time. When news lands, only that player moves —
plus at most one spare slot, so the replacement can be afforded under the cap.
Everything else is left exactly as entered.

That is narrower than a re-optimizer on purpose. Across the reviewed month,
swaps forced by news gained 24.5 points per entry and were positive 15 times out
of 15; re-optimizing slots nobody had said anything about averaged 3.4 with 15
of 29 positive, and on one night cost 125 points across 8 entries. The old
behavior is still available — switch *Late swap fires on* to "anything that
scores better".

A **core** is held through a swap unless the news is about the core itself, in
which case it is released: holding a player nobody expects to play is not
respecting a conviction pick.

## Files

| File | Role |
|---|---|
| `app.py` | Local web server + LineStar parsing / pool logic. |
| `gui.py` | The single-page UI (drop, pickers, results, exposure, download). |
| `engine.py` | Pure-Python GPP engine: pool filter, construction, Monte-Carlo sim, selection, pool-legal alternatives. |
| `dk.py` | DK ruleset, name normalization, the Player record. |
| `../WNBA-Optimizer.command` | Double-click launcher (Mac). |

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
