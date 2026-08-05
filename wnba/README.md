# WNBA DraftKings Optimizer

A **GPP-first** lineup generator/optimizer for **DraftKings WNBA Classic**,
sibling to the NBA `dfs-proxy`. This directory is the *optimizer*; the
browser-facing *data proxy* is the new `api/wnba-*.js` endpoints one level up.

## The contest, from first principles

DraftKings WNBA Classic: **$50,000 cap, 6 players — G, G, F, F, UTIL, UTIL.**
DK only splits the league into Guards and Forwards (no centers), so every
player is G- or F-eligible and UTIL takes either. Scoring is the NBA formula
(`pt 1, 3PM .5, reb 1.25, ast 1.5, stl 2, blk 2, TO -.5`) **plus double-double
+1.5 / triple-double +3** — those bonuses are why high-usage forwards
(Stewart, Alyssa Thomas, Angel Reese) carry the ceiling.

Fantasy points reduce to **Minutes × Usage**, scaled by matchup and pace.
WNBA specifics that shape strategy:

- **Tiny slates** → ownership concentrates, correlation matters more, being
  contrarian is harder. Edge lives at the **bottom** of the board (cheap
  players with a real minutes path), which is almost always **injury-driven**.
- **Player props ARE available** (`/wnba/v1/odds/player_props`) — the market's
  points/rebounds/assists/threes lines are the strongest projection input, so
  they're the *primary* source. **Betting odds** (`/wnba/v1/odds`) give implied
  team totals for game-environment scaling. What's missing is a
  **confirmed-lineups** feed, so "who starts" is inferred from recent minutes +
  injury status (`/wnba/v1/player_injuries`).

**GPP is a ceiling game, not an average game.** You need a lineup in the right
tail of outcomes *and* differentiated from the field. So the engine optimizes
**simulated ceiling × low ownership × correlation**, not raw projection.

## How GPP mode works

1. **Generate** a large, diverse pool of valid lineups (ILP via PuLP/CBC),
   each forced to include a **game stack** (≥2 players from one game).
2. **Simulate** the slate 10k times (`simulate.py`): each player drawn from a
   skewed Beta-PERT distribution over (floor, median, ceiling), with a shared
   per-game environment multiplier so teammates/opponents move together —
   *this is why stacking pays off, and the sim rewards it automatically.*
3. **Rank** by simulated ceiling (p85), then **leverage-adjust** by projected
   ownership — being right where the field is light is worth more.
4. **Select** the final N under a per-player **exposure cap** + uniqueness.

## Layout

| File | Role |
|---|---|
| `dk.py` | DK WNBA ruleset, CSV parsing, DK scoring (incl. DD/TD), name normalization. |
| `projections.py` | Projection seam + `make_projector()`. `CsvProjector` = offline baseline; `_estimate_ownership` heuristic. |
| `bdl.py` | balldontlie WNBA client + `BalldontlieProjector` — live recent-form + DvP + pace + injuries. Falls back to CSV if no key. |
| `simulate.py` | Monte-Carlo engine: Beta-PERT player outcomes + game correlation. |
| `optimizer.py` | ILP + GPP pipeline + CLI. Only reads `player.proj/floor/ceil/ownership`. |

## Usage

```bash
pip install -r requirements.txt
export BALLDONTLIE_API_KEY=your_goat_key      # enables live projections

# GPP: 20 lineups, live data, min 2-player game stacks, 60% max exposure
python optimizer.py --csv DKSalaries.csv --mode gpp --n 20 --out lineups.csv

# Offline (CSV only), or force a source:
python optimizer.py --csv DKSalaries.csv --source csv --mode gpp --n 20
```

Key flags: `--source csv|bdl|auto` (auto = bdl if key set), `--stack N`
(min game-stack size), `--max-exposure 0.0..1.0`, `--leverage` (0 = pure
ceiling, higher = fade chalk harder), `--pool`, `--sims`, `--season`.
The `--out` CSV uploads straight to DraftKings (`G,G,F,F,UTIL,UTIL` header,
`Name (ID)` cells).

## Projection sources

- **`csv`** — DK `AvgPointsPerGame` as the median, fixed variance band. Always
  works, no key. Good for a dry run.
- **`bdl`** (recommended) — **props-first**: builds DK points from market prop
  lines (pts/reb/ast/3pm), filling steals/blocks/TO from recent form. Because
  the market already prices minutes, matchup, pace and injuries, props-based
  projections are *not* re-scaled by DvP/pace (no double-count). Players with
  no props fall back to a recent-form projection (weighted last-5 DK output
  blended with season avg), scaled by defense-vs-position (G/F), pace, and
  implied-total environment. OUT dropped, questionable haircut, via
  `player_injuries`.

Still heuristic (documented in code): **projected ownership** — real ownership
needs a feed WNBA doesn't expose, so it's a value+salary proxy, good enough to
*rank* leverage, not to trust as a number.

## The WNBA data proxy (`../api/wnba-*.js`)

Vercel functions mirroring the NBA proxy, for a future base44-style frontend:
`wnba-slate` (games + pace/ratings), `wnba-odds` (implied team totals),
`wnba-props` (market stat lines per player), `wnba-injuries`,
`wnba-recent-stats` (minutes/usage trend + absent-teammate detector),
`wnba-dvp` (G/F defense vs position). Base path `api.balldontlie.io/wnba/v1`,
key via `BALLDONTLIE_API_KEY`.

> The odds/props JSON field names are read defensively (a `_first()` over
> likely keys) because this sandbox's network policy blocks balldontlie, so
> the exact live shape couldn't be confirmed here. One real `player_props` and
> one `odds` record will let us pin them exactly.

## Notes carried from the NBA proxy (fix on its next upgrade)

- `getSlateDate()` hard-codes ET as `-5` (EST). WNBA plays in **summer = EDT
  (-4)** — the WNBA endpoints here use `-4`. The NBA proxy should switch to a
  DST-aware offset.
- `nba-dvp.js` fallback table is frozen static data; `season=2025` hard-coded
  in pace; NBA fantasy formula omits the DD/TD bonus (WNBA code includes it);
  no response caching (rate-limit risk on repeated calls).
