# Running the WNBA optimizer from base44

You don't run any Python. The whole optimizer is a single endpoint on your
Vercel proxy — base44 just uploads the DraftKings CSV to it and renders the
lineups it returns.

## Step 1 — get the endpoint live on Vercel

The code is on branch `claude/wnba-dfs-optimizer-gh8wl3`. To make it live at
`https://dfs-proxy.vercel.app/api/wnba-optimize`, **merge that branch into
`main`** (Vercel auto-deploys `main`, and the `BALLDONTLIE_API_KEY` already
set for your NBA endpoints is reused). No other config needed.

Quick check it's up (returns a 400 asking for a CSV, which means it's live):
```
curl https://dfs-proxy.vercel.app/api/wnba-optimize
```

## Step 2 — the API contract

**Request** — `POST /api/wnba-optimize`, JSON body:
```json
{ "csv": "<paste the full DKSalaries.csv text here>",
  "options": { "n": 20, "stack": 2, "maxExposure": 0.6, "leverage": 0.35 } }
```
You can also POST the raw CSV as the body with `Content-Type: text/csv`.

Options (all optional): `n` lineups (default 20), `stack` min game-stack size
(2), `maxPerTeam` (4), `maxExposure` 0–1 share of lineups one player can be in
(0.6), `leverage` how hard to fade chalk (0.35), `season`, `date`.

**Response**:
```json
{
  "slate": { "date": "2026-08-05", "season": 2026, "games": ["SEA@NYL", ...] },
  "source": "props-first",
  "warnings": [],
  "players": [ { "name","team","pos","salary","game","proj","floor","ceil","own","notes" } ],
  "lineups": [ {
    "rank": 1, "salary": 49900, "proj": 176.0, "simCeiling": 198.2, "totalOwn": 142.1,
    "stacks": ["PHX@ATL:3"],
    "players": [ { "slot":"G","name","team","pos","salary","proj" }, ... ],
    "upload": ["Kelsey Plum (43754736)", ... 6 strings in G,G,F,F,UTIL,UTIL order]
  } ]
}
```
The `upload` array is the DraftKings import order — join the 6 strings as a CSV
row under the header `G,G,F,F,UTIL,UTIL`.

## Step 3 — build the base44 screen (paste this prompt into base44)

> Add a page called **WNBA**. It has:
> 1. A file upload that accepts a DraftKings `DKSalaries.csv`.
> 2. Number inputs: "Lineups" (default 20), "Min game stack" (default 2),
>    "Max exposure %" (default 60), and a "Fade chalk" slider 0–1 (default 0.35).
> 3. A **Generate Lineups** button. On click, read the uploaded CSV file as
>    text and `POST` it to `https://dfs-proxy.vercel.app/api/wnba-optimize`
>    with JSON body `{ csv: <file text>, options: { n, stack, maxExposure,
>    leverage } }` (maxExposure as a 0–1 fraction).
> 4. Show a loading state, then render each returned lineup as a card: rank,
>    total salary, projection, sim ceiling, total ownership, and the stacks;
>    inside each card a 6-row table (Slot, Player, Team, Pos, Salary, Proj).
> 5. A **Download DK CSV** button that writes a file with header
>    `G,G,F,F,UTIL,UTIL` and one row per lineup from each lineup's `upload`
>    array (comma-joined), so it imports straight into DraftKings.
> 6. Also show a collapsible "Player projections" table from the `players`
>    array (Name, Team, Pos, Salary, Proj, Floor, Ceil, Own, Notes), sortable
>    by Proj.
> If the response has `warnings`, show them in a small muted note.

That's the entire app: upload tonight's CSV → Generate → review lineups →
Download DK CSV → upload to DraftKings.

## Notes

- `source` tells you what drove projections: `props-first` (best — real market
  lines), `season-stats` (props feed empty/off), or `csv-only` (couldn't reach
  balldontlie — check the key/env). If you ever see `csv-only` in production,
  the endpoint couldn't read the API.
- The props/odds JSON field names are read defensively. If `source` is stuck on
  `season-stats` when props should exist, paste me one `/wnba/v1/odds/player_props`
  record and I'll pin the field mapping exactly.
