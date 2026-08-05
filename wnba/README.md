# WNBA DraftKings Optimizer

A **GPP-first** lineup generator/optimizer for **DraftKings WNBA Classic** with
a local web GUI: run one command, drag in your DraftKings CSV, get lineups and a
DraftKings-ready download. No cloud, no base44, no Vercel — the balldontlie
calls happen inside the app (server-side), so there's no CORS and your API key
never leaves your machine.

## Quick start

```bash
cd wnba
pip install -r requirements.txt
export BALLDONTLIE_API_KEY=your_goat_key   # optional; enables live props
python app.py                              # opens http://localhost:8000
```

Then: drop `DKSalaries.csv` → tune settings → **Generate lineups** → review →
**Download DraftKings CSV** → upload to DraftKings. You can also paste the key
into the GUI instead of the env var (it's stored in your browser only).

Prefer the command line? `python optimizer.py --csv DKSalaries.csv --mode gpp
--n 20 --out lineups.csv` does the same thing headless.

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
| `optimizer.py` | ILP + GPP pipeline + headless CLI. Only reads `player.proj/floor/ceil/ownership`. |
| `app.py` + `gui.py` | The local web app: a stdlib HTTP server + single-page GUI. No extra deps. |

## Settings (GUI sliders / CLI flags)

- **Lineups** (`--n`) — how many to build.
- **Min game stack** (`--stack`) — force ≥N players from one game per lineup.
- **Max exposure** (`--max-exposure`) — cap the share of lineups any one player
  can appear in (diversity across your entries).
- **Fade chalk / leverage** (`--leverage`) — 0 = pure ceiling, higher = punish
  high projected ownership harder.
- CLI-only: `--source csv|bdl|auto`, `--pool`, `--sims`, `--season`, `--out`.

## Projection sources

- **`csv-only`** — DK `AvgPointsPerGame` as the median, fixed variance band.
  Always works, no key. What you get if the app can't reach balldontlie.
- **`props-first`** (with a GOAT key) — builds DK points from market prop lines
  (pts/reb/ast/3pm), filling steals/blocks/TO from recent form. Because the
  market already prices minutes, matchup, pace and injuries, props-based
  projections are *not* re-scaled by DvP/pace (no double-count). Players with no
  props fall back to recent-form (weighted last-5 DK output blended with season
  avg), scaled by defense-vs-position (G/F), pace, and implied-total
  environment. OUT dropped, questionable haircut, via `player_injuries`.

Still heuristic (documented in code): **projected ownership** — real ownership
needs a feed WNBA doesn't expose, so it's a value+salary proxy, good enough to
*rank* leverage, not to trust as a number.

## Data source

The app talks to the balldontlie WNBA API directly (base
`api.balldontlie.io/wnba/v1`) from the local server process — no proxy needed,
because a server (not a browser) makes the calls. Endpoints used: `games`,
`odds`, `odds/player_props`, `player_injuries`, `player_season_stats`,
`team_season_advanced_stats`.

> The odds/props JSON field names are read defensively (a `_first()` over likely
> keys) because the dev sandbox couldn't reach balldontlie to confirm the exact
> live shape. If projections show `source: csv-only` or `season-stats` when
> props should exist, one real `player_props` record pins the mapping exactly.

## Hosting it later (optional)

`app.py` is a standard web server, so if you ever want a URL instead of
localhost, it runs as-is on any host that runs Python (Render, Railway, Fly, a
VPS) — set `BALLDONTLIE_API_KEY` in that host's env and point it at `app.py`.
No Vercel required.
