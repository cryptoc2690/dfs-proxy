# WNBA DraftKings Optimizer

A lineup generator/optimizer for **DraftKings WNBA Classic**, sibling to the
NBA `dfs-proxy`. This directory is the *optimizer* (the part your NBA setup
does in base44). The *data proxy* (balldontlie WNBA endpoints) is Phase 2.

## The contest, from first principles

DraftKings WNBA Classic: **$50,000 cap, 6 players — G, G, F, F, UTIL, UTIL.**
DK only splits the league into Guards and Forwards (no centers), so every
player is G- or F-eligible and UTIL takes either. Scoring is the NBA formula
(`pt 1, 3PM .5, reb 1.25, ast 1.5, stl 2, blk 2, TO -.5`) **plus double-double
+1.5 / triple-double +3** — the bonuses are why high-usage forwards
(Stewart, Alyssa Thomas, Angel Reese) carry the ceiling.

Fantasy points reduce to **Minutes × Usage**, scaled by matchup, pace, and
game total. WNBA specifics that shape strategy:

- **Tiny slates** (tonight = 5 games). Small pool → ownership concentrates,
  correlation matters more, being contrarian is harder.
- **Short rotations, huge starter minutes** → the top of the board is
  *predictable*; your edge lives at the **bottom** (cheap players with a real
  minutes path).
- **Injuries are the game.** One star OUT redistributes ~30 min + usage to a
  knowable replacement. That replacement is the slate's leverage.

Two objectives, one engine:

- **Cash** (50/50, double-up): beat the median. Maximize **floor**.
- **GPP** (tournaments): beat ~99%. Maximize **ceiling × low ownership ×
  correlation** — a different, right-tail problem.

## Layout

| File | Role |
|---|---|
| `dk.py` | DK WNBA ruleset + CSV parsing → `Player` records. One place owns the cap/slots/scoring. |
| `projections.py` | The **swappable seam**. `CsvProjector` (v1) projects from the CSV alone. `BalldontlieProjector` (Phase 2 stub) will use live data. |
| `optimizer.py` | ILP solver (PuLP/CBC). Cash + GPP modes. Only ever reads `player.proj/floor/ceil/ownership`, so swapping projectors changes nothing here. |

## Usage

```bash
pip install pulp
# Cash: single highest-floor lineup
python optimizer.py --csv DKSalaries.csv --mode cash --out cash.csv
# GPP: N diverse ceiling lineups (DK-uploadable CSV)
python optimizer.py --csv DKSalaries.csv --mode gpp --n 20 --out lineups.csv
```

GPP knobs: `--min-diff` (min differing players between lineups),
`--max-per-team` (over-stack cap), `--seed`. The `--out` CSV uploads straight
to DraftKings (`G,G,F,F,UTIL,UTIL` header, `Name (ID)` cells).

## v1 honesty (what's rough today)

- **Projection = `AvgPointsPerGame`** from the CSV. Blunt but a real median.
- **Floor/ceiling** = fixed variance band, not a per-player model yet.
- **Ownership** = a heuristic proxy (value + salary tier), good enough to
  *rank* leverage, not to trust as a number.
- **GPP ceilings** are randomized draws around the ceiling band — a stand-in
  for a real Monte-Carlo sim.

## Phase 2 — live balldontlie enrichment (proxy endpoints to build)

Mirror the NBA proxy for WNBA (base URL `api.balldontlie.io`, WNBA scope):

- `wnba-slate` — games + odds → implied team totals, pace (which game to stack)
- `wnba-injuries` — `player_injuries` (drives minutes redistribution)
- `wnba-lineups` — confirmed starters (highest-leverage info, ~1hr pre-tip)
- `wnba-recent-stats` — minutes/usage trend + absent-teammate spike detector
  (port `nba-recent-stats.js` — this is the injury-value engine)
- `wnba-dvp` — defense vs position from box scores
- `wnba-props` — player points props → market-implied projections (best single
  input **if** your balldontlie tier exposes WNBA props)

Then `BalldontlieProjector.project()` blends props + recent form + DvP + pace,
redistributes minutes for confirmed inactives, and models floor/ceiling and
ownership properly. The solver stays exactly as-is.

## Notes carried over from the NBA proxy (fix on its next upgrade)

- `getSlateDate()` hard-codes ET as `-5` (EST). WNBA plays in **summer = EDT
  (-4)** — wrong slate date for evening games. Fixed from the start in WNBA code.
- `nba-dvp.js` fallback table is frozen static data; `season=2025` hard-coded
  in pace; fantasy formula omits the DD/TD bonus; no response caching (rate limits).
