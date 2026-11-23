# NFL Week 11 Main Review Prompt (11-23)

**Review only. Never build, edit, run for a live build or commit anything in the tool (`~/Desktop/dfs-proxy/nfl`). Every change goes on a build list for me to approve in the tool chat.**

## 0. Why this prompt

Week 11's main slate (11-22) went badly. I put in about $150 and got back $63. The winning lineup had an extremely low-owned piece, under 1%.

This was the first main slate built with the Nov 19 changes. I want to know four things:
- did the changes we made since week 9 hurt us, help us, or make no difference;
- what beat us on 11-22;
- was any of it reachable before lock;
- is there anything else to do.

Showdown is fine (a 1st, and a 1st split 16 ways, last night). It is **not** part of this review, apart from recording its results (section 6).

**One slate is one slate.** Judge every change on all the slates it has run on, never on 11-22 alone. The pass lines in `NFL Rules Ledger.md` still apply.

## 1. What is live, and what changed when

| Change | Live from | Where it is logged |
|---|---|---|
| Stage 3 main build (ranges, QB+1/QB+2 with a 55% QB+2 quota, bring-back 60%, no QB's RB, 55% cap, 15 QBs by proj², own 20s) | week 9 (11-8) | `settings.build` |
| DST drawn first, not last | week 10 (11-15) | `settings.dstRule = "first"` |
| Own-sum window measured against the slate (lineup sum less the field's expected sum, −26.5 to +36.9) in place of 86.4–145.0 | week 11 (11-22) | `settings.build = stage3-2026-11-19+relsum`, `settings.ranges`, `settings.fieldOwnSum` |
| No ceiling on the QB's game total (41.5 floor kept) | week 11 (11-22) | `settings.ranges.vt` |
| Every build saves its inputs | week 11 | `settings.inputsDir` → `logs/inputs/<date>/<time>/` (projections, DK, LineStar, Stokastic lineups, `typed.json` with the pool, scratches, caps, minimums and ticked contests) |

The tool's log is `~/Desktop/dfs-proxy/nfl/logs/nfl_builds.jsonl`. **Rebuild 11-22 from the saved inputs folder,** not from my Downloads. Use the folder of the build that was uploaded: the last one before lock that matches the entry ids in the DK FINAL file.

## 2. 11-22 from the standings

For each main contest (the 150-max and both 20-maxes):
- **The contest:** entries, first place, the top-1% line, how far first was over the line, the field mean and the cash line.
- **The winner and the top 1%:** what they played against the field and ours. Shapes too: stack size, bring-back, DST, salary left, summed own against the field's expected sum, and the fewest-owned player.
- **The under-1% piece:** who he was, his price, position, team and game, his projected and drafted own, his points, and how many of the top 1% held him. Was he on my typed sheet? Could our build have used him at all? The counts range allows at most one player under 2%, so check whether he was eligible, whether he was drawn, and where he dropped out.
- **Our sets:** mean against the field; top 1% and top 0.1%, split and counted; top 100; best rank; cash rate (as the cost); how many copies the field entered of each lineup. Put money in as a note: fees in, money back, by contest.
- **The post-mortem table:** each gap is (our share − the top 1%'s share) × points. Give each a "before lock" column (what the files showed) and a "what the rules say" column (which step put us there: pool, draw, ranges, cap, quota, stack rules, DST rule or the pick).
- **Which rules fired on 11-22:** the field's expected own sum and the window it gave; QBs with lineups; the top game total and whether its QBs got lineups; the most-owned player and our share of him; how much of the draw the ranges kept; any caps or minimums I typed.

## 3. Did the changes help or hurt?

Rebuild 11-22 from the saved inputs, three seeds, and add it to every slate you already have. Grade split first. For each change, show the record **with 11-22 in**: slates up and down, the interval, and whether it is still on track for its pass line.

1. **The own-sum window against the slate vs the fixed 86.4–145.0** (shadow S1). 11-22 is live slate 1 of the 8 it is decided at.
2. **No game-total ceiling vs the 53.5 ceiling.**
3. **DST first vs DST last.**
4. **The Stage 3 build as a whole vs our real sets before week 9, the field, and a random pick from the typed pool** (the Fresh Look's reference sets).
5. **The 20s:** each change on the 20-lineup sets.

If a change has been hurting us, say so plainly with its numbers, and put taking it out on the build list. If it is too early to tell, say how many more slates it needs.

## 4. The under-1% winner: a one-off or a pattern?

Your ledger says winners rarely hold players under 2% (F2, F3): the winner averaged 0.56 of them on nine slates.
1. With 11-22 in, how often did a winner or the top 1% hold someone under 1% and under 2%? Who were they, and what did they score?
2. Is there anything before lock that separates the low-owned players who break a slate from the hundreds who don't? Report precision and recall against the base rate. If nothing does, say so: then this is a slate we cannot be built for, and chasing it costs the other slates.
3. Would any rule that allows more low-owned pieces have won more over all ten slates, not just 11-22? Test it split, held out, with its cost.

## 5. Anything else

Look at 11-22 the way the Fresh Look did: fresh, with your own definitions. Then rebuild the shadows (S3, S12, S13, and any others you can rebuild from the saved inputs) for 11-22 and add them to their records. Say what, if anything, is now ahead of the live build across all slates, and what it would take to call it.

## 6. Showdown: record only

Add last night's showdown contests to the ledger and the showdown shadows (S8, S9, S10) without reviewing the build. Note the 1st split 16 ways as a split finish worth 1/16. No showdown changes.

## 7. What I want back

- `NFL Week 11 Main Review Report 11-23.md`: short, plain words, the answers first.
- `NFL Week 11 Main Review Appendix 11-23.md`: every table.
- `NFL Rules Ledger.md` updated with 11-22 (and the showdown rows).

Every number gets its slates, how many were up and down, and split before counted. The cash rate goes in as the cost. Sharks are reference only. No "the projection said" as a reason. Check all saved slates before calling anything dead.

End with:
- **A build list for me to approve.** For each item give:
  - what changes, and which step of the tool;
  - the numbers;
  - the cost;
  - the slates it hurts on;
  - its pass line, and when it gets decided.
- **The shadow list,** updated.

**Review only: never build, edit or commit anything in the tool.**
