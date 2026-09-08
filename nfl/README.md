# NFL Showdown builder

Builds 150 DraftKings Showdown lineups from Stokastic's exports and writes a
file you can upload straight back to DK. Pure Python, no installs.

## Run it

```
python3 nfl/app.py \
  --proj  Stokastic_Projections_NE_SEA.csv \
  --field Stokastic_NE_@_SEA_Lineups.csv \
  --dk    DKEntries.csv \
  --n 150 --split 75 \
  --entries-at-build 56000 --field-cap 237812 \
  --out upload.csv
```

Then upload `upload.csv` to DraftKings.

## The four files

| Flag | File | Needed? |
|---|---|---|
| `--proj` | Stokastic **projections** export (one row per player) | **Yes** |
| `--field` | Stokastic **lineups** export (~9,000 rows) | Strongly recommended |
| `--dk` | Your **DK entries export** for the contest | Yes, for an uploadable file |
| `--pool` / `--cores` | The sharp's pool and cores, one name per line | Optional |

Pull the two Stokastic files **in the same session**. Their exports carry
separate ownership snapshots and the research brief measured them disagreeing by
up to 9.8 points on a single lineup when pulled hours apart.

Get the DK entries file by entering or reserving your 150 entries on DK first,
then downloading. It is the only file carrying your Entry IDs and DK's player
IDs, and without it there is nothing to upload.

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
  in showdown by 106%. This is the opposite of the right setting on a main
  slate, which is why showdown and classic will stay separate tools.

## Settings worth knowing

| Flag | Default | Note |
|---|---|---|
| `--own-lean` | `0.35` | Positive leans toward the field. Negative fades. |
| `--captain-cap` | `0.28` | Share of entries one captain may hold. |
| `--min-captains` | `10` | Floor on distinct captains. |
| `--player-cap` | `0.65` | Must run high — six of ~68 players fill every lineup. |
| `--max-leftover` | `2500` | Junk filter, not a lever. |
| `--max-off-pool` | off | Max non-pool players per lineup. Needs `--pool`. |
| `--sims` | `4000` | Monte-Carlo runs. |

## Log contest fill — it may be the biggest edge here

`--entries-at-build` and `--field-cap` are worth filling in every time. Both
contests are **guaranteed** prize pools, so EV per entry is `pool / entries` and
break-even fill is **84.1%**. At the fill levels observed in the brief, EV per
entry ran $1.78 against a $0.50 entry. That swing is far larger than any
construction decision in this tool and it costs nothing to observe.

The tool prints whether you are in overlay and logs it.

## The log

Every run appends one JSON line **per entry** to `nfl/logs/nfl_builds.jsonl`:
the source arm, captain, all six players with their inputs, salary, split,
ownership sum, the settings in force and the contest fill.

This file is the point of the whole exercise right now. There is no NFL results
history, so every setting in this tool is a hypothesis. The log is what lets a
later review join these entries to real standings and find out which ones were
right.

## Known limits

- The correlation strengths in `engine.py` are judgement, not measurement. They
  reproduce the direction of the lopsided-split finding but not its magnitude.
  First thing to re-fit once real results exist.
- Duplication for lineups absent from the vendor pool is an ownership-driven
  estimate, not a measurement.
- Showdown has **no late swap**. Every player locks together. The only lever is
  the inactive report about 90 minutes before kickoff, which has to be acted on
  *before* lock — so rebuild and re-upload then, rather than planning to swap.
