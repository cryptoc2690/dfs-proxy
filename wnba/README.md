# WNBA DraftKings Optimizer

A local, GPP-first lineup optimizer for **DraftKings WNBA Classic**. Run one
command, drop in a projection file, get differentiated tournament lineups.
Pure Python standard library — nothing to install, no cloud, no API keys.

## Run it (Mac)

Double-click **`WNBA-Optimizer.command`** (first time: right-click → Open).
Or from a terminal:

```bash
cd wnba && python3 app.py     # opens http://localhost:8000
```

Drop your files → set lineup count → **Generate** → **Download DraftKings CSV**.

## Sources of truth

| File | Provides | Required? |
|---|---|---|
| **DFF cheatsheet** (DailyFantasyFuel) | projections, recent form (L5/L10/season), salary, positions, injury status, ownership (if present) | this **or** the DK CSV |
| **DK salary CSV** | DraftKings player **IDs** (for one-click bulk upload), salary, positions | optional — add it for uploadable lineups |

Run on the **DFF alone** and lineups export by name (manual entry). Add the DK
CSV to get the `Name (ID)` format DraftKings needs for bulk upload. If you drop
both, DK is the pool/IDs and DFF overlays its (better) projections.

## The ruleset

DK WNBA Classic: **$50,000 cap, 6 players — F, F, F, G, G, UTIL** (2 guards, 3
forwards, 1 flex). Scoring is the NBA formula plus double-double +1.5 /
triple-double +3.

## How it builds lineups

1. **Clean the pool** — drop OUT players, players with ~0 recent minutes
   (out of rotation, no matter the season average), and, dynamically, the
   minutes-punts: keep players by *upside*, with the cutoff scaling to slate
   depth (deep slate → higher floor; thin slate → reaches down only if forced).
2. **Build** a large pool of valid, salary-legal, game-stacked lineups.
3. **Simulate** thousands of slates (Beta-PERT player outcomes + a shared
   per-game multiplier, so stacking pays off) and rank by ceiling.
4. **Leverage-adjust** by projected ownership (value-based, ~0.63 rank
   correlation with real %Drafted), so the set fades the obvious chalk.
5. **Select** N under a per-player exposure cap **and** a pairwise-overlap cap,
   so your lineups are genuinely different — the whole game in WNBA, where
   everyone runs the same projections. The A'ja-type on/off split emerges here.

## Settings & inputs (all optional except the file)

- **Lineups**, **min game stack**, **max exposure**, **fade chalk (leverage)**.
- **Game-theory cores** (type-ahead): flag a sharp's core plays — the app
  gives them a small edge, treats them as chalk for leverage, and diversifies
  them across your set (no forced count — the data decides).
- **His full pool** (type-ahead): an ownership *signal*, never a filter —
  in-pool reads as chalk, off-pool gets a leverage discount, nobody is excluded.

## Files

| File | Role |
|---|---|
| `app.py` | Local web server + the projection/pool logic (DFF + CSV). |
| `gui.py` | The single-page UI (drag-drop, pickers, results, exposure, download). |
| `engine.py` | Pure-Python GPP engine: pool filter, construction, Monte-Carlo sim, selection. |
| `dk.py` | DK ruleset, CSV parsing, scoring, name normalization. |
| `projections.py` | CSV-only projector + value-based ownership model. |
| `../WNBA-Optimizer.command` | Double-click launcher (Mac). |

## Building your own ownership model (later)

Ownership can't be computed from math alone — it's *learnable* from your past
contests. Each `(DFF cheatsheet + contest standings)` pair from a day you
played is one training slate; ~10–15 pairs are enough for a fitted model to
beat the value-only default. Until then, value-based ownership + your sharp's
cores are the read.
