# WNBA DraftKings Optimizer

A **GPP-first** lineup generator/optimizer for **DraftKings WNBA Classic** with
a local web GUI: run one command, drag in your DraftKings CSV, get lineups and a
DraftKings-ready download. No cloud, no base44, no Vercel — the balldontlie
calls happen inside the app (server-side), so there's no CORS and your API key
never leaves your machine.

## Easiest: double-click (Mac)

1. Download this repo (GitHub → green **Code** button → **Download ZIP**) and
   unzip it.
2. **Double-click `WNBA-Optimizer.command`.** The first time, right-click it →
   **Open** (macOS asks once about running a downloaded script). If Python
   isn't installed it opens the installer page — install the `.pkg`, then
   double-click again.
3. Your browser opens the app. Drop `DKSalaries.csv`, set your number of
   lineups, hit **Generate lineups**, then **Download DraftKings CSV**.

Paste your balldontlie GOAT key into the field in the app (stored in your
browser only) to get live props-based projections. There is **nothing to
`pip install`** — it runs on a stock Python 3.8+.

**If macOS blocks it** ("Apple could not verify… is free of malware") — that's
just Gatekeeper flagging a script downloaded in a ZIP. Either: **System
Settings → Privacy & Security → scroll to Security → Open Anyway** (one time),
or run this in Terminal once — type it with a trailing space, drag the
`dfs-proxy` folder in to fill the path, press Return:
`xattr -dr com.apple.quarantine ` . Then double-click the launcher again.

## Or from a terminal

```bash
cd wnba
python3 app.py            # opens http://localhost:8000
# headless, no GUI:
python3 optimizer.py --csv DKSalaries.csv --mode gpp --n 12 --out lineups.csv
```

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

1. **Generate** a large, diverse pool of valid lineups (randomized weighted
   construction under the cap + position rules), each forced to include a
   **game stack** (≥2 players from one game).
2. **Simulate** the slate thousands of times: each player drawn from a skewed
   Beta-PERT distribution over (floor, median, ceiling), with a shared per-game
   environment multiplier so teammates/opponents move together — *this is why
   stacking pays off, and the sim rewards it automatically.*
3. **Rank** by simulated ceiling (p85), then **leverage-adjust** by projected
   ownership — being right where the field is light is worth more.
4. **Select** the final N (your entry count) under a per-player **exposure
   cap** + uniqueness, so your 12 lineups are genuinely different.

## Layout

| File | Role |
|---|---|
| `dk.py` | DK WNBA ruleset, CSV parsing, DK scoring (incl. DD/TD), name normalization. |
| `projections.py` | Projection seam + `make_projector()`. `CsvProjector` = offline baseline; `_estimate_ownership` heuristic. |
| `bdl.py` | balldontlie WNBA client + `BalldontlieProjector` — props-first + recent-form + DvP + pace + injuries. Falls back to CSV if no key. |
| `engine.py` | Pure-Python GPP engine: randomized lineup construction + Beta-PERT Monte-Carlo sim with game correlation + exposure-capped selection. No deps. |
| `optimizer.py` | Headless CLI wrapper around the engine. |
| `app.py` + `gui.py` | The local web app: stdlib HTTP server + single-page GUI. |
| `../WNBA-Optimizer.command` | Double-click launcher (Mac). |

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

## Game-theory pool (optional)

If you follow a sharp who posts a player pool with core plays, paste it in:

- **Core plays** → every lineup includes at least *Min cores* of them (default 1),
  and cores get an **ownership bump** (they're the field's chalk) so the leverage
  engine pivots around them. Winners usually carry 1–2 of 3 cores — set *Min
  cores* to 2 to lean into that, 1 to keep more pivots.
- **His full pool** → an ownership *signal, not a filter*. Nobody is excluded:
  in-pool reads as chalk, **off-pool gets an ownership discount** so a rare sharp
  play outside his pool surfaces as leverage and still gets used if it projects.

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
