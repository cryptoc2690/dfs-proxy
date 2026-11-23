# NFL Build Item Check Prompt (11-23)

**Review only. Never build, edit, run for a live build or commit anything in the tool (`~/Desktop/dfs-proxy/nfl`).**

## 0. Why this prompt

Your Week 11 report put two items on the build list:
1. Every lineup QB + 2 catchers + an opponent, picked by the chance of reaching the line.
2. Take the own-sum window out.

I'm not sold on item 1. Making **every** lineup one shape, QB + 2 + a bring-back, sounds like the kind of forced rule that has hurt us before. It seems strange that one shape would win across all ten slates. I don't have the data, so show me. I also need a worked example from you before the tool chat ports anything.

Answer section 1 first. Only do the worked examples (section 3) for the items that still stand after section 1.

## 1. Is "every lineup QB + 2 + an opponent" real, or the best of 70 tries?

1. **Define the rule exactly.**
   - Is it exactly one opponent, or at least one?
   - Exactly two catchers (WR/TE), or two or more?
   - Can the opponent be an RB?
   - What happens to the QB+2 quota, the bring-back draw, QB+1 lineups and QB+3 lineups?
2. **Slate by slate, all ten (eleven if 11-22's 20-maxes count separately).** For each slate show:
   - the share of the top 1% (split) that has this exact shape, and the field's share;
   - what the winner was;
   - the share of the top 1% the rule would have thrown out, and what those lineups were (QB+1, QB+3, no opponent, two opponents, no stack);
   - item 1 against the live build on that slate.

   On how many slates was this shape the majority of the top 1%? Where it wasn't, how did item 1 still do?
3. **Which half carries the gain?** Split it cleanly:
   - the shape alone, picked by projected points (S13: +0.41);
   - the pick alone, with today's stack mix (S12: +0.28);
   - both (+1.76).

   Is the gain from the shape, from the pick, or only from the two together? If it is only together, explain why in plain words.
4. **Softer versions of the shape,** each with the reach-the-line pick:
   - QB+2 + opponent as a share of the set: 50%, 70%, 85%, with the rest built as now;
   - QB+2 with the opponent optional;
   - QB+1 or QB+2 with at least one opponent;
   - today's 55% QB+2 quota with the bring-back raised to 80% or 100%.

   Does any of them keep most of the gain without forcing every lineup into one shape?
5. **The selection problem.** You found item 1 while looking at nine of these ten slates, and it was the best of about 70 builds.
   - Run it the honest way: on each slate, pick the shape rule with the procedure (choose among the shape and pick options using only the other slates), then grade the slate it didn't see.
   - What does it make held out like that?
   - How often would the procedure have picked "every lineup QB+2 + opponent"?
   - Also: by chance, how far ahead should the best of 70 builds be? Compare the +1.76 to that.
6. **The three slates that carry it** (11-1, 11-15, 10-25). What made them? Would a build without the forced shape but with the reach-the-line pick have caught them?

**Plain verdict:** forced on every lineup, as a share, or not at all. If the honest held-out number does not clear the ledger line, say so. Then item 1 becomes a shadow, not a build.

## 2. Item 2: the own-sum window out

1. Confirm the comparison the tool chat should rely on: held out, with the window 1.54, without it 1.62. On 11-22, 0.00 with it and 2.42 without.
2. The 11-17 report had the window at +0.44 held out. Was that against the old fixed window (86.4–145.0), not against no window? Say plainly that this is why the two reports differ.
3. With no window, what keeps a set from piling into the chalkiest lineups on a normal slate? Show the own sum, copies and top exposure with and without it, slate by slate.

## 3. Worked examples (for the port)

For each item that still stands after sections 1 and 2:
- **The exact code change** to `hbuild.generate` / `select` (or your `hw.py` on top of it), written so it can be ported line for line. Include:
  - the line guess, 1.452 × the field's expected projected total;
  - the spread, 1.13 × Std Dev, or 0.35 × projection when Std Dev is empty;
  - the pair correlation table;
  - the score (line guess − projected total) / lineup spread, lowest first;
  - how ties are broken;
  - whether the cap and QB quotas are unchanged;
  - the constants as numbers, not refit at run time (or say exactly how the tool should refit them).
- **A worked example on 11-22,** from the saved inputs folder the tool wrote that night, seed 0. Give:
  - the window if any;
  - the QB quotas;
  - the candidate count;
  - the first five lineups in pick order, each with its score, projected total, salary, own sum and shape;
  - the set totals: QBs used, top five exposures, the shape mix, mean own sum and salary left.

  The tool chat will rebuild 11-22 from the same folder and must match these five lineups exactly before anything goes live.
- **The 20-max version,** with the same detail, for the first 20-max set (seed 0, 7 QBs).

## 4. What I want back

- `NFL Build Item Check Report 11-23.md`: short, plain words, the answers first.
- An appendix with every table, the code change and the worked examples.
- Ledger rows updated.

Every number gets its slates, how many were up and down, and split before counted. The cash rate goes in as the cost.

**Review only: never build, edit or commit anything in the tool.**
