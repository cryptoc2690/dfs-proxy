# NFL Range of Outcomes Prompt (11-19)

**Review only. Never build, edit, run for a live build or commit anything in the tool (`~/Desktop/dfs-proxy/nfl`). Every change goes on a build list for me to approve in the tool chat.**

## 0. Why this prompt

Every projection review we have done says the same thing: a player reaching his projection is a coin flip, about 50/50 (NBA F10 had it at 44%). So half of what decides a slate is outside the projection. That half is what the tool is supposed to be doing, and right now it isn't. Both tools rank on the projection (main: projected points; showdown: a simulation built on the projection) and then fill the leftover salary with whatever cheap piece fits.

**The example that started this: showdown 11-16, LAC @ BAL** (total 39.5). My lineup was five of the winner's six with the same captain (Hampton). The sixth was the Chargers DST ($3,400, 7.0 points) where the winner had Mark Andrews ($6,400, 16.2, 18% drafted). 1,960th against 1st.
- Before lock, the Chargers DST had reached 10+ once all season. Andrews had reached 10+ three times, and that night he had a new QB who throws.
- The tool reads none of that. It saw a cheap DST with a projection that fit the leftover salary, in a game its showdown slate read calls a grind (both QBs projected at 37 or under), where it leaves kickers and defenses uncapped. Those thresholds (37 / 40) came from four showdowns and were never tested.

**My read:** in showdown every player needs a real shot at 10+ to win. The tool keeps fitting in low-salary pieces, in NBA and NFL, that have no path there. Test that.

Already tested and dead, so do not redo them: a team or player history of projection misses (r 0.01, every version lost), a matchup adjustment (already in the projection), "weak chalk" marks with no before-lock definition. This prompt is not about whether the projection is biased. It is about **the range around it**: who can actually get to the number.

## 1. Is the 10-point floor real? (showdown first, then main)

All 27 showdown games, split finishes first (a finish on a lineup entered k times counts 1/k), counted second.
1. For the winner, the top 10, the top 1%, the field and our sets: the lowest-scoring player in the lineup (flex and captain base points). How often does a top-1% lineup hold anyone under 10? Under 8? Under 5? By position and price (K, DST, sub-$4,000, sub-$2,000).
2. Split top-1% rate per 1,000 entries by the lineup's lowest scorer, and by how many players finished under 10. Is there a line below which lineups stop winning?
3. **Main slate version.** The same for the 9 main minis, with the line expressed as points per $1,000 (NBA found about 5–5.5 a $1,000 at every price) or a per-position line you pick leave-one-slate-out. Which players in winning main lineups were the low scorers, and what did they cost?

If the floor is not real (winners hold sub-10 pieces as often as the field), say so plainly and stop there.

## 2. What before lock tells you who can get there

The question is not "who will beat his projection". It is: **at the same projection, who has the better chance of reaching the line?** Every mark is judged against players of the same projection band, so it shows what it adds beyond the projection.

Marks to test (everything must be known before lock; any history uses only games before the slate):
- **Stokastic's range columns:** Ceiling, Floor, Std Dev, Boom, Bust.
- **LineStar's:** Ceiling, Floor, Consistency, PPG, Safety, StartingStatus, Depth, AlertScore.
- **The player's own hit rate this season:** share of games at 10+ (showdown) or at the main line, to that date. If the store does not have weekly game logs for every player, say what is missing and what file I need to pull.
- **Role change:** a new QB (and whether he throws), a teammate out, a backup starting, projection well over his season average.
- **Game context:** total, spread, implied team total, favourite or underdog.
- **Position and price:** K, DST, punts.
- **Kickers and defenses specifically:** points allowed by the opponent, the opponent's offence, sacks and turnovers forced.

For each mark: precision and recall for "reached the line" against the base rate, inside each projection band. Which ones add anything over the projection?

Then answer the 11-16 case directly: before lock, what did the best marks say about the Chargers DST against Andrews?

## 3. Turn it into rules and test them

Write the pass line before running anything: the one in `NFL Rules Ledger.md` (main: +0.3 split top-1% lineups a contest, more slates up than down, the interval clear or 7 of 9 up; showdown: +0.10 split over our real 150 and the plain file pick, more games up than down, the interval clear). Any line or threshold is chosen leave-one-out.

Through your harness, graded split, with the cost (mean and cash rate) shown:
1. **Showdown floor:** every player must have a real shot at 10+ by the best mark from section 2. No piece below the line, whatever salary is left.
2. **Showdown kicker and defense read:** the 37 / 40 QB thresholds as live; kickers and defenses capped always; only when the mark says they can reach 10; never. Low totals and bad teams' defenses called out separately.
3. **Rank on the chance to reach the line, not the projection:** showdown and main. (The old tool's ceiling rank was a trial graded in dollars only; regrade the idea split.)
4. **Main slate floor:** every player must have a real shot at the main line, on top of the candidate spec from the 11-17 report (own sum against the slate, no game-total ceiling).
5. **Role change lean:** a player with a role change by section 2's definition, held over the field. (The 11-17 report has the new-role lean at +0.30 on the live spec and +0.10 on the candidate. Re-test it with the role marks from this prompt.)

Also show whether any of these stack onto one type together (the NBA Nov 16 lesson: a floor on one player is paid for by someone else).

## 4. What I want back

- `NFL Range Of Outcomes Report 11-19.md`: short, plain words, the answers first.
- `NFL Range Of Outcomes Appendix 11-19.md`: every table.
- New and updated rows in `NFL Rules Ledger.md`.

Every number gets its slates or games, how many were up and down, and split before counted. The cash rate goes in as the cost. No "the projection said" as a reason.

End with a **build list for me to approve** (what changes, the step of the tool it changes, the numbers, the cost, the slates it hurts on, the pass line and when it gets decided, and whether it touches showdown) and a **shadow list** (what to log from now on so each idea grades itself live).

If nothing before lock separates who gets to 10 from who doesn't at the same projection, say that plainly too, and say what it means for how the tool should fill the last slot.

**Review only: never build, edit or commit anything in the tool.**
