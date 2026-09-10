# NFL lineup builder — showdown and main slate

Builds up to 150 DraftKings NFL lineups from Stokastic's exports and writes a
file you can upload straight back to DK. Pure Python, no installs.

It handles **both formats** — single-game Showdown (1 CPT + 5 FLEX) and the
Sunday main slate (QB/RB/RB/WR/WR/WR/TE/FLEX/DST) — and works out which one you
are building from the files themselves: the DK entries export spells the roster
slots out in its header, and failing that, a showdown board is one game while a
main slate is a dozen. The settings panel changes to match. You can force it
with `--format showdown|classic` if you ever need to.

## Run it

```
cd ~/Desktop/dfs-proxy && git pull && python3 nfl/app.py
```

That opens a page in your browser. Drop the three files in, press **Build
lineups**, then **Download DK file** and upload it to DraftKings. Same shape as
the WNBA tool, different program — `wnba/app.py` will never show NFL.

There is also a command-line path for scripted runs:

```
python3 nfl/app.py --proj proj.csv --field lineups.csv --dk DKEntries.csv \
  --n 150 --split 75 --field-cap 237812 --out upload.csv
```

## The four files

| Drop slot / flag | File | Needed? |
|---|---|---|
| `--proj` | Stokastic **projections** export (one row per player) | **Yes** |
| `--field` | Stokastic **lineups** export (~9,000 rows) | Strongly recommended |
| `--dk` | Your **DK entries export** for the contest | Yes, for an uploadable file |
| Settings panel | The sharp's pool and cores — type-ahead, click to add | Optional |

Pull the two Stokastic files **in the same session**. Their exports carry
separate ownership snapshots and the research brief measured them disagreeing by
up to 9.8 points on a single lineup when pulled hours apart.

Get the DK entries file by entering or reserving your 150 entries on DK first,
then downloading. It is the only file carrying your Entry IDs and DK's player
IDs, and without it there is nothing to upload.

Every column is matched by exact name. A column that only matched by prefix or
substring is reported as a **guess** so you can check it — a renamed vendor
column once bound both projection and ownership to the same numbers with no
warning anywhere. Non-numeric projections (“-”, “N/A”) and blank positions are
listed rather than silently becoming zero.

Each drop slot checks the file before accepting it and says what it found —
"150 entries, 68 players (showdown)" — or why it was rejected. A file in the
wrong slot is refused rather than silently taken, and a lineups export from the
wrong *slate* is ignored with a warning rather than quietly poisoning the
opponent model.

## Pool and cores

Once the projections file lands, the pool and core boxes turn into type-ahead
pickers over that slate's players: type a few letters, click or press Enter,
click a chip to remove. Cores count as in-pool automatically.

A pool becomes a real build constraint — by default every player must come from
it, and it is enforced while the lineup is being *built*, not as a filter
afterwards. On a main slate the odds of nine random picks all landing inside a
55-name sheet are effectively zero, so a post-filter returned nothing at all.

On a main slate the sheet also has to cover nine seats — a QB, two RBs, three
WRs, a TE, a flex and a defense. If it cannot, the tool says which seat is short
and treats the sheet as a shortlist rather than a filter, instead of failing.

Each core is guaranteed a share of the entries, applied *after* every other
filter, so a core can never be squeezed out by the tool's own preferences. Two
things back that up:

- A **core quarterback licences his own pass-catchers** past the pool filter. A
  main slate needs a stack, so a core QB whose receivers are not on the sheet
  could otherwise never be built at all.
- The build **checks afterwards that every floor actually held** and names the
  player and the shortfall if one did not. The top-up loop stops silently when
  no legal swap exists, and that silence is exactly how the WNBA tool once put a
  core in one entry out of ten while reporting a guarantee.

## What `--field` is actually for

Stokastic's lineup export is **not a list of recommendations**. It is a
simulated model of your opponents: the row count plus the Dupes column
reconstructs the configured field size exactly, and player exposure tracks
projected ownership at r = 0.996. It is built to reproduce the field.

So we do not play their lineups. We use the file as the opponent set to score
our own lineups against, and as the duplication model. That is the one component
that cannot be built from scratch with no results history, and it is the reason
to keep paying for the package.

Without `--field` the tool still runs, but it loses the win-rate ranking and the
duplication estimate and falls back to simulated score alone.

## `--split` runs the A/B

`--split 75` puts 75 lineups from our builder and 75 from their pool (re-ranked
on `Win% / (1 + Dupes)`, which beat their own default ordering in the brief's
measurements) into the same contest, tagged in the log.

A split inside **one** contest is the only design that removes slate luck from
the comparison. Compare the two arms on percentile finish, not on ROI — one
slate of ROI tells you nothing.

## What it does that the vendor pool does not

- **Targets lopsided team splits.** 5-1 splits win at nearly double the rate of
  a balanced 3-3 at matched projection, and the field only builds them about
  15.6% of the time. We target 45%. This is enforced as a quota rather than left
  to ranking, because forcing five players from one team costs a couple of
  projected points and a straight ranking quietly discards the shape.
- **Caps the captain much tighter than the flex.** The captain is the
  highest-dispersion decision in the format. Stokastic's own field puts 20.6% of
  its captaincies on one player and uses only 24 distinct captains across 9,061
  lineups. Default cap is 28% with a floor of 10 distinct captains.
- **Refuses a defense against an offense it is stacking.** Points allowed *is*
  the opponent's scoring, so the two cancel. Hard rule, not a penalty.
- **Leans toward the field, not away from it.** At matched projection chalk wins
  in showdown by 106%.

### ...and on a main slate (`nfl/classic.py`)

Different game, and the brief is explicit that three things point the other way:

- **Stacking is the lever**, in the place the team split occupies in showdown.
  Each extra pass-catcher rostered with your QB is worth about +31% relative win
  probability at matched projection. The real Sunday field builds QB+3 **4.6%**
  of the time; the default targets 45%, as a quota for the same reason the
  showdown split is one.
- **Bring-backs are insurance, not construction.** A player from your QB's
  opponent hedges game script: it lifts the floor and cuts the tail, which is
  wrong when first place is the objective (-12.7% win equity). Kept at 15% by
  deliberate choice, and the QB's opponent is closed off in the fill so a random
  draw cannot turn that into 34%.
- **Ownership lean is neutral, not negative.** Ranking on win probability
  already fades chalk hard by itself — on the real main slate these lineups land
  near the field's 12th ownership percentile with the lean at zero, and adding a
  negative lean moved that by one point. A lever that does nothing does not get
  to act on the report's least-trusted finding. The build prints where your
  lineups sit against the actual field so the size of the bet is visible.
- **A defense is refused against two or more of your own offensive players** —
  tighter than the showdown rule, because there is a whole slate of defenses to
  choose from instead of two.

Duplication is a much smaller number here: nine slots out of 700+ players, and
only 0.3% of the vendor's modelled pool carries a dupe at all against 70% in
showdown. An average of ~0 expected duplicates on a main slate is right, not
broken.

### How a lineup is ranked, and why that changed

A lineup's score is how often it clears **the field's 99th-percentile score**
in the same simulated world, divided by its expected duplicates. It used to be
"how often it beats the field's single best score", and that was too coarse a
target on a main slate: most candidates cleared it in zero simulations, the
rest in a handful, and the order among them was noise — change the random
seed and only 3–11 of the chosen 75 survived. The field sample is drawn
**weighted by Dupes**, because a roster the field holds forty times is forty
opponents, not one.

**Measured.** Build under one simulation seed, judge under a different one
the ranking never saw, three build seeds, against the vendor's own 75
re-ranked from their pool. First-place share of our arm as a multiple of
theirs:

| | judged by the old simulator | judged by the calibrated one |
|---|---|---|
| code before these changes | 1.11× | **0.77×** (behind) |
| ranking fix only | 1.25× | 0.91× |
| simulator fix only | 1.08× | 1.07× |
| both, plus stack quotas 45/55 | **1.25×** | **1.33×** |

The full set is also the most stable across seeds (0.062 / 0.062 / 0.061)
and the only one whose top-1% share clears the vendor arm's. On a second,
independent judge seed the same comparison came out 1.12× → **1.31×** (old
judge) and 1.03× → **1.62×** (calibrated judge). Bars at the 95th or 99.9th
percentile were both worse than the 99th. The judge is still a simulator —
real graded slates are what settle it — but the improvement holds under both
versions of it and both seeds, which is the check the review asked for.

### The simulator lands on the vendor's numbers now

Every player's simulated row is calibrated so its mean equals the projection
and its spread equals the stated Std Dev — exactly, not approximately. Before
this, defenses ran 6–8% high, workhorse backs 2–6% low, and every position was
17–31% wider than stated; that excess was all uncorrelated noise, which diluted
the stack correlation the whole build exists to exploit (QB→WR1 was 0.27; it is
0.46 calibrated). The Boom-mixture that caused most of the excess is gone.

### Cores and caps

A core's guaranteed share is filled **inside** the selection sweeps, under the
same exposure caps and overlap rules as everything else, rather than swapped in
afterwards past every cap. If a floor cannot be met within the rules the build
says so by name. Cores also get a construction weight, so a core the sharp
likes and the projections do not can still reach its floor.

The two arms of a split check overlap against each other, so the vendor half
cannot hand you near-copies of your own half.

## Settings worth knowing

| Flag | Default | Note |
|---|---|---|
| `--format` | auto | `showdown` or `classic`. Only needed to override. |
| `--own-lean` | `0.35` sd / `0` classic | Positive leans toward the field. Negative fades. |
| `--player-cap` | `0.65` sd / `0.55` classic | Share of entries one player may hold. |
| `--max-leftover` | `5000` sd / `2000` classic | Junk filter, not a lever. |
| `--min-proj` | `2.0` sd / `3.0` classic | Floor on a roster spot having any path to a score. |
| `--max-off-pool` | `0` with a pool | Max non-pool players per lineup. Needs `--pool`. |
| `--field-cap` | — | Contest max entries. Assumed to fill; `--fill-pct` marks it down. |
| `--sims` | `4000` | Monte-Carlo runs. |

Showdown only:

| Flag | Default | Note |
|---|---|---|
| `--captain-cap` | `0.28` | Share of entries one captain may hold. |
| `--min-captains` | `10` | Floor on distinct captains. |

Main slate only:

| Flag | Default | Note |
|---|---|---|
| `--stack-targets` | `3:45,2:55` | Share of lineups at each stack depth. |
| `--bring-back` | `0.15` | Share carrying a player from the QB's opponent. |
| `--qb-cap` | `0.35` | QB exposure *is* stack exposure, so it binds tighter. |
| `--dst-cap` | `0.30` | Share of entries one defense may hold. |

## Put the contest size in

`--field-cap` (the page's "Contest size") is the contest's max entries, and it
is assumed to fill because these do. Stokastic models only 50,000 opponents on
showdown and 10,000 on a main slate; without the real size, every duplication
figure is measured against a field several times too small. Mark it down with
`--fill-pct` only when entering days early into a contest that may stay short.

## The log

Every run appends one JSON line **per entry** to `nfl/logs/nfl_builds.jsonl`:
the format, the source arm, every player with their inputs, salary, ownership
sum, the settings in force and the contest fill — plus the shape fields for
whichever game it was. Showdown logs the captain, the team split and the major
team; a main slate logs the QB, the stack depth and the bring-back count. Those
are what let a later review test the *mechanism* rather than just the outcome.

This file is the point of the whole exercise right now. There is no NFL results
history, so every setting in this tool is a hypothesis. The log is what lets a
later review join these entries to real standings and find out which ones were
right.

## Grading a slate — the only thing that settles any of this

```
python3 nfl/app.py --grade LineStar_post_game.csv
```

Every construction belief in this tool — the 5-1 split, QB+3, the bring-back
share, the ownership lean — was measured inside **the vendor's own simulation**,
which is a model of the field and not the field. `--grade` scores your logged
entries against real results and breaks them down by arm and by shape.

The LineStar export doubles as the results file: its `Scored` column is actual
fantasy points, and nothing in the Stokastic exports carries them after the
fact. Pull it after the games, run `--grade`, and the numbers accumulate.

**Do not act on one slate.** Six to ten is a signal.

## LineStar — tested, and it is not a projection source

Measured against actual results on NE @ SEA:

| | LineStar | Stokastic |
|---|---|---|
| correlation with actual | 0.618 | 0.630 |
| mean absolute error | 3.95 | 3.99 |
| RMSE | 5.48 | 5.48 |
| head-to-head, closer on | 13 of 30 | 17 of 30 |

Identical. And every LineStar-only column — Ceiling, Safety, Consensus, PPG,
Consistency — correlates **negatively** with what Stokastic's projection got
wrong, which is regression to the mean rather than information. Pre-game the
two projection sets agree at r = 0.974 with a median gap of 0.8 points and no
player differing by 5.

So none of it is read into the build. Two things in the file are still worth
having, and neither is a projection:

- **Vegas** — spread, total, per-team implied points. Stokastic's showdown
  export carries none of it. Pass `--linestar` and it is logged against the
  build without touching it. The team split is a bet on game script, so this is
  the natural thing to condition it on — once there is enough of it to test.
- **Scored** — actual points, which is what `--grade` runs on.

## Known limits

- The correlation strengths in `engine.py` are judgement, not measurement. They
  reproduce the direction of the lopsided-split finding but not its magnitude.
  First thing to re-fit once real results exist.
- Duplication for lineups absent from the vendor pool is an ownership-driven
  estimate, not a measurement.
- Showdown has **no late swap**. Every player locks together. The only lever is
  the inactive report about 90 minutes before kickoff, which has to be acted on
  *before* lock — so rebuild and re-upload then, rather than planning to swap.
- The main slate **does** have late swap, and the tool does not do it yet. The
  WNBA tool's news-gated swap is the model to copy across when there is time.
- The vendor's own "Stack Type" column counts running backs as stack partners.
  This tool counts pass-catchers only, which is what the +31% finding was
  measured on, so the two numbers will not match. That is deliberate.
