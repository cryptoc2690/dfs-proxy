# NFL Week 10 Review: Contest Lens Rework Prompt (11-19)

**Review only. Never build, edit, run for a live build or commit anything in the tool (`~/Desktop/dfs-proxy/nfl`). Read it, its logs and its files. Every change goes on a build list for me to approve in the tool chat.**

## 0. Why this prompt

Week 10 is done. That's the point we set for updating the system.

**The main slate.** Week 10's main slate was brutal. As it stands, the main-slate tool is dead to me. I want it reworked from the contest view, the way we just reworked the NBA tool. Nothing in the current main-slate spec is protected. If the contests say start over, start over.

**Showdown.** Showdown is not dead, but it needs the same review from the contest view.

**The pattern I keep seeing:** we are always one player off. The one player we are under is the one that costs us. I want to understand who that player is, and whether anything before lock could have shown him.

You already did a contest-first pass (Stage 1–3, Nov 8–9), and the Stage 3 spec went live from week 9. This prompt builds on that; it does not redo it. What's new:
- the grading (split finishes);
- the review method that came out of the NBA rework;
- the NBA rules themselves, as ideas to test on NFL;
- weeks 9 and 10, which the live Stage 3 spec built;
- the tool chat's notes on what the live build actually did those two weeks (section 1f). Read them before grading: several of the week 10 losses trace to specific rules firing.

Section 1 is the NBA rework in full: what we tested, what failed, what was built, and why. Sections 2–6 are the questions.

---

## 1. What the NBA rework did, and why

The NBA files are in `~/Downloads/NBA Results 1/`. Read these, in order:
- `NBA Rules Ledger.md`
- `NBA Win Not Cash Report 11-15.md`
- `NBA Juice The Live Piece Report 11-15.md`
- `NBA Contest Review Report 11-17.md`

The harness is in `Prototype/` (`test_all.py`, `proto/build.py`), and the tool is `~/Desktop/dfs-proxy/nba` (`newbuild.py`, README "The build"). Read them for the method and the rules. Don't redo their numbers.

### 1a. The method (port all of this)

1. **Start at the contest, not the tool.** Every review starts with the standings of each contest:
   - the top-1% line, first place and how far over the line first was;
   - what won;
   - what the top 1% played, against the field and against our set;
   - our set and the sharks' sets, side by side.

   Then comes the post-mortem. Each gap is (our share − the top 1%'s share) × the player's real points. Each gap has a "before lock" column (what the tool or the news showed) and a "what the rules say" column (which rule put us there).
2. **Split finishes.** A finish on a lineup entered k times by the field counts 1/k. This changed the whole NBA picture:
   - Counted the old way, our 150s were a third over the field in the top 1%. Split, they were a tenth over.
   - The new tool's 150 on 5–7 game slates was 59% over the field counted and 32% under it split.
   - The reason: it was picking the top of Stokastic's order, and the field enters those lineups 20–40 times each.

   Every rule is now graded on split finishes first: split top 1%, split top 0.1% and split top 100. Counted finishes come second. The cash rate is shown only as the cost. **The goal is 1st–10th and top finishes, not cashing.** One finish in the top 10 is worth more than a lot of lineups cashing.
3. **A rules ledger.** Every claim gets a row:
   - F: what the field and the winners do.
   - T: what the tool does.
   - G: a triggered rule, one that fires only on some slates.
   - S: a shadow or log.

   Each review answers one question per row: does it still hold with the new nights in? Each row shows its main number before and after the new nights, its state (holds / weakened / fails / dropped), and the nights behind it.
4. **Triggered rules are judged only on the nights they fire.** Each one has a pass line written before it is built. For example: ahead by 0.3+ split top-1% lineups a contest, with more nights up than down, at 20 fires. Each one also has the number of fires at which it gets decided. A trigger chosen after the fact doesn't count. Any threshold is chosen leave-one-night-out, and a trigger has to beat "always on" in a nested test.
5. **Shadows before building.** A new idea runs as a shadow set saved every night (in the NBA tool, `logs/shadow/<date>/`). It is graded against the standings on live nights before it changes a live build.
6. **Check every saved night before removing anything.** Never decide off one night. A rule stays until its row says it fails over all its nights.
7. **Ranges of outcomes, not projections.** Never "the projection said" or "the field did" as a reason. A projection is a coin flip; ownership and price are what you know.
8. **Sharks are a reference point, never a reason.** Use them to say whether a night was winnable, not to copy.
9. **The leverage idea:** hold the chalk at about the field's share, and fund low-owned plays that have a reason (a role change, a teammate out, a matchup). Adding a low-owned piece beat removing chalk every time we tested it.

### 1b. What we found about winning lineups (NBA field rows that may port)

- **F1:** winners are a little chalkier than the field. Players under 2% are rare in them (2.3% of slots against 5.6%).
- **F5:** ride the field's favourite. It paid on 19 of 26 nights.
- **F10:** reaching projection is a 44% event for everyone, chalk or not.
- **F13, F14:** the field builds the same shapes and pairs as the winners. The winning lineup was a single copy in 23 of 26 mini-MAXes. Winners hold the chalk that hit and none that failed (0 of 71).
- **F22, F23:** our counted lead over the field is mostly shared finishes. The copies sit at the top of Stokastic's order.
- **F24: a winning lineup is chalk with one live piece.** Most of it is chalk, plus one or two players at about 5–15% own, nobody under 2%, and often a pair the field rarely plays together.
- **F18, F21:** about 5 to 5.5 points per $1,000 are needed at every price. A pricier player has to outscore a cheaper one by about 4.3 points per $1,000 of price difference ("the gap").
- **F25:** before lock, cheap value is knowable; whether the studs pay is not.
- **F4, F11:** a role takeover (a regular out, so the player's projection is 1.3× his normal) is the one chalk marker that held. Weak chalk can be told from the role: off the bench, a teammate at his position back, or under 22 minutes. It reached projection 26% of the time against 50% for other chalk.
- **F15:** the opponent is already in the projection. A fitted matchup adjustment added nothing.

### 1c. What was built and holds (live NBA rules, 26 nights, split top-1% lineups a contest)

| Rule | What it does | Result |
|---|---|---|
| G14, one live piece | 5–7 game slates, the 150: pick only from Stokastic's lineups whose fewest-owned player is projected at 2–10% (no all-chalk lineup, nobody under 2%) | **+1.10, up 10 nights / down 2.** Cost: mean −1.67 a lineup, cash rate −3.1 points. The budget I set was a mean of −2.5 to −3 and a cash rate of about 30%. |
| G7, weak chalk out | 2–4 game slates: weak chalk left out | +1.67, up 8 / down 0 |
| G10, easy-matchup floor | Pool players at 5–20% own facing a team that gives up the most are floored at 2× their own, five at most | +0.62, up 9 / down 3; mean +1.65 |
| G11, single-stud block | 30 of the 150 built around the low-owned stud with the most reasons (regulars out, carries the offence, over his projection) | +0.54, up 6 / down 4 |
| G1 | 5–7 games: Stokastic's lineups picked by projected sum | +0.44, up 8 / down 3 |

Also live:
- the slate-size split (T1);
- the winners' ranges for pool lineups (T3);
- one cap and a 4-lineup minimum (T4);
- the takeover lean (T13);
- late swap outs only (T7);
- removals shift nothing to teammates (T8).

### 1d. What was tested and failed (don't re-test these on NFL without a new reason; port the lesson)

- **G12, the low-owned stud floor.** Studs at 5–20% were floored at 1.5× own. It was built Nov 15, ahead of its own pass line, because it halved G14's cost. It lost on both live nights (−1.9 and −1.5 split top-1% lineups) and is back to shadow. **Lesson: never build ahead of the pass line to save cost.**
- **Nov 16, the stud load.** The top 1% held almost no players at $9,000+ (0.03 a lineup). The field held 0.49 and Stokastic's best lineups 0.32. We held 0.75, because the block and the floors stacked on top of each other. Rules that each pass alone can pile onto one type together. **The other side of it: Jaylen Brown ($8,000, 14% own) was in 43% of the top 1% and 1% of ours.** The tool's raises elsewhere squeezed him from 7% to 1%. That is the "one player off" mechanism in NBA: a floor on one player is paid for by someone else.
- **Copies limits, winning-score ranking, starting lower in Stokastic's order, a swapped-in live piece** (T21). Each one gains at the top once split, but costs 4–10 points of mean. G14's band does the same job for a third of the cost.
- **Holding all chalk at the field to pay for leverage** (T18): mean −7.1 a lineup, worse at the top.
- **Each of these lost or did nothing:**
  - a stud cap, floor or spread (T12);
  - a cheap-chalk fade (T14);
  - a lineup-level ownership lean (T16);
  - a matchup adjustment (T17);
  - taking share from the high-owned stud (T20);
  - capping low-ceiling cheap starters (T24);
  - an over-draft cap (G9);
  - a mid-tier starter floor (S1);
  - a forced shape or separate block (T15);
  - pool adds (T9).
- **Slate-type triggers** (stars/scrubs, value-thin, and so on): every trigger lost to "always on" in a nested test (T23). Cheap value is knowable before lock; whether the studs pay is not.
- **Leaning on one team** more than the field: a coin flip (the top 1% agreed 12 nights, disagreed 14).
- **A team or player history adjustment.** You already showed this for NFL (r 0.01).

### 1e. Still open in NBA (watch the NFL version)

- G16, the rare pair: the two fewest-owned players' ownerships multiplied, under 0.75%. It was ahead on both live nights; shadow until 20 fires.
- G8: weak chalk out on bigger slates (shadow).
- The stud-load log.
- Late news inside the last 15 minutes rebuilding most of the set: on Nov 16, 5 of 150 lineups survived, and the new team lost by 18.

### 1f. What the live NFL tool did in weeks 9 and 10 (tool chat notes)

These are from the tool and its log, not from the standings. Check each one against the log before using it.

**What is live** (commits 7ad8d34, da9e1f6, 85e5312; README "Nov 9: the new main-slate build"):
- **Main slate, Stage 3 spec, ported line for line** from `hbuild.py` (10-4 worked example reproduced exactly, both with the DST last and the DST first). Ranges: own sum 86.4–145.0, at most 3 players under 5% and 1 under 2%, $400 or less left, QB game total 41.5–53.5 (LineStar). QB+1/QB+2 with a 55% QB+2 quota, bring-back drawn 60%, no QB's RB, DST never facing the lineup, 55% cap on every player, 15 QBs by proj² (7 for a 20-max, with the graded trim rule). Pool = the typed sheet plus every DST. No Stokastic half, no cores, no adds.
- **Each main 20-max gets its own 20** (seed 0 for the first, 1 for the second).
- **The DST rule changed between the two weeks.** Week 9 (11-8) was DST-last; from Nov 9 it is DST-first (`settings.dstRule = "first"`, `build = stage3-2026-11-09+dstfirst`). Week 9 rows carry neither.
- **Showdown is the old build, unchanged**: 75 ours / 75 Stokastic on the 150, captain cap 28%, split targets 5-1 45% / 4-2 40% / 3-3 15%. Since Nov 9 each showdown 20-max takes its own block of OUR ranking: the first 20-max lineups 1–20, the second 21–40. Contest size is read from the contest name; on showdown it changes no lineup (DUPE_EXP 0), only the logged duplicate estimate.
- **Log defect, fixed Nov 9 (85e5312).** The 11-8 showdown rows all carry the Play-Action contest id and pair the 150 lineups with the first 150 entry ids; place them by entry id. From the next build on, one row per entry with its own contest id and `metrics.contestSize`.

**What the user did by hand (it is in `settings.capPlayers`, `settings.floorPlayers`, `settings.pool`):**
- Week 9 main: Cardinals DST capped at 40% (the build had them at 82 of 150, 55%, against 8% projected own).
- From week 10: Tampa Bay players cut from the pool by hand, after the check-in found no team carry-over. Count it as a pool choice, not a tool rule.
- Week 10 main: Gavin Bartholomew (TE $2,500, 6% own) capped at 5%. Check the log for anything else typed (an Emmett Johnson minimum?).

**What the tool chat traced on week 10 main (11-15), from the pre-lock files only:**
- **Emmett Johnson** (KC RB, $4,700, 72% projected own, a new role) was in 18 of 150 (12%). Before the filter 43% of the random lineups held him; after it 13%. Lineups with him had a median own sum of 164 and 84% were over the 145 ceiling. He is also never beside Mahomes (no QB's RB). The ranges were fitted on slates with no 70%+ player, so this was never tested.
- **SF@DAL had a 55.5 total, over the 53.5 game-total ceiling**, so Purdy and Dak got no lineups. Only 7 QBs qualified.
- **With 7 QBs the first selection pass could not fill 150**, so the relaxed passes let **Bijan Robinson reach 93 of 150 (62%)**, over the 55% cap.
- **Cheap low-owned players filled the room under the 145 ceiling**: Chimere Dike $3,000, Colby Parkinson $3,000, Bartholomew $2,500 (37 of 150 before the hand cap).

That was the build on the tool chat's machine from the user's pool image. The live build may differ (seed, caps, the exact pool), so grade the logged set.

**Open items from earlier reports, to close or carry:**
- DST-first: top-1% +0.43 (4 of 7), top-0.1% +0.10, shadow on both; top-10% and the tail tested. Add weeks 9–10.
- The cheap high-value RB at the cap (Coleman / Hurst / Singletary type): 1–1 on 7 slates.
- The 11-8 showdown call: if the next four showdown 20-max contests cash in the 25–45% band, 11-8 was the game; two more zero-cash would point at the build, with the 20-max coming from our half the first suspect. Grade the Stokastic 1–20 block beside ours each time.
- A team or player history adjustment is dead (r 0.01, every version lost about a point of top-1%).

---

## 2. First: weeks 9 and 10 from the standings

The files are in `Main/` (11-8, 11-15) and `Showdown /` (11-8, 11-9, 11-12, 11-15, 11-16). Note the trailing space in `Showdown `. They include:
- the standings for each contest;
- the DK FINAL entries;
- Stokastic's projections and lineups (main 11-15 has a lineups file);
- LineStar;
- the Pool:Core images.

The tool's log is `~/Desktop/dfs-proxy/nfl/logs/nfl_builds.jsonl`. Week 9's main was built with the defense last; week 10's with the defense first (`dstRule`).

For each slate, main first, then each showdown:
- **The contests:** the entries, first place, the top-1% line, how far first was over the line, the field mean and the cash line.
- **What won, and what the top 1% played** against the field and ours. Shapes too: stack size, bring-back, the DST, salary left, the summed own and the fewest-owned player.
- **Our sets:**
  - the mean against the field;
  - the top 1% and top 0.1%, counted and split;
  - the top 100;
  - the top 10%;
  - the best rank;
  - the cash rate (as the cost);
  - how many copies the field entered of each of our lineups.

  Put the sharks' sets beside ours, as reference only.
- **The post-mortem table** (section 1a.1), with "before lock" and "what the rules say".
- **Check the build against section 1f first:** which rules fired (ranges, game-total cut, relaxed cap passes, hand caps and minimums, the TB cut), and what each one did to that night's set.
- **Main 11-15:** what made it brutal? Was it the build or the slate? Could any set have won it? Did the Stage 3 spec do what it promised in the harness (2.17 top-1% a contest on 7 slates), and what is its live record now, split?

## 3. Re-grade on split finishes

Re-grade everything you have with duplicates split:
- the Stage 1–3 rows;
- the final spec against our real sets;
- the vendor arm against ours;
- each leave-one-out piece (the ranges, the QB+2 quota, the bring-back, the DST first, no file).

Does any verdict change? Showdown matters most here: in Stage 1, 74–87% of all showdown entries were exact copies of Stokastic's lineups. Show both arms split:
- how much of our top-1% and top-0.1% count disappears;
- how much of the vendor arm's disappears;
- how much of the field's disappears.

Then start an **NFL rules ledger**, in the same layout as `NBA Rules Ledger.md`. Give every claim you've made since week 1 a row, stated as a number with its nights. Rows that hold, rows that are weakened, rows that should come out.

## 4. The main slate, reworked from the contest view

Treat the current spec as one candidate, not the base. Answer from the standings, all slates:

1. **What is a winning NFL main lineup made of, split?** Look at:
   - the chalk share;
   - the fewest-owned player;
   - how many players at 5–15%;
   - nobody under 2%?;
   - the summed own;
   - stack size and the bring-back;
   - the rare pair (stack pieces or a bring-back the field rarely builds together).

   Does "chalk with one live piece" (F24) hold for NFL? If not, what is the NFL version?
2. **Are our main lineups the field's lineups?** How many copies did the field enter of each of ours? Does a chalk QB stack built the obvious way get copied?
3. **Test the NBA rules that might port**, through your harness, graded split, with a pass line written first:
   - **One live piece** (G14): only lineups whose fewest-owned player is in a band (try 2–10% and a band you pick leave-one-slate-out), from our own build and from Stokastic's lineups.
   - **The rare pair** (G16): the two fewest-owned players' ownerships multiplied, under a line.
   - **A leverage block** (G11): N of the 150 around one low-owned stack or player with a reason. NFL reasons could be a target share going up with a teammate out, or a backup RB starting.
   - **A takeover lean** (T13): a player whose projection jumped because a regular is out, at 15%+ own, floored above the field.
   - **Weak chalk** (G7): chalk with no role reason. Test this one only if you can define it before lock.
   - **An easy-matchup floor** (G10), with F15 in mind (the opponent may already be in the projection).

   Also show the cost of each one (mean and cash rate), and whether rules that each pass stack onto one type together (the Nov 16 lesson).
4. **The ranges against mega-chalk and big totals (week 10's mechanism).**
   - On each main slate, the most-owned player: how much of the top 1% held him, against the field and against us? On slates with a 40%+ player, where did the winners' own sum sit? Does a fixed 145 ceiling cut the lineups that won those nights?
   - Should the own-sum range move with the slate (for example, relative to the field's own sum, or with the top player's own taken out)? Test it split, leave one slate out.
   - The QB game-total range: how often did the slate's top-total game win, and what did cutting it cost? Same for the QB count: when fewer than 10 QBs qualify, what happens to the cap, and should the relaxed passes ever let a player past 55%?
5. **What doesn't port.**
   - NBA's slate-size split: NFL main is always a big slate.
   - Minutes-based marks.
   - The 20s' top-three raise.

   NFL has correlation (stacks) at the centre, and NBA has none. NFL also has far fewer slates (about 10 mains), so say what pass line is honest at this sample. Showdown gives about 30.
6. **The 20-max.** Same questions for the 20s. In NBA the 20s were already built to win once split. Are ours?
7. **If you rebuilt it from scratch today, what would the main-slate 150 and 20 be?** Give the spec with each piece's evidence, cost and pass line. Mark what is tested and what is a shadow.

## 5. Showdown, the same review

1. **The whole showdown record on split finishes:** our arm, the vendor arm and the field, minis and 20-maxes. Is our showdown set built to cash or to win?
2. **The captain.** For each game: who captained the top 1%, against the field's captains and ours. How often was the winning captain someone we held under the field? Is there a before-lock read: the price, own, team, game script and role of the captains who win, against the ones the field picks?
3. **The shape** (5-1 / 4-2 / 3-3, the captain cap, the own-lean toward the field, the 75/75 split with Stokastic's lineups), graded split.
4. **Port the NBA ideas to showdown:**
   - one live piece in the five flex spots;
   - a rare pair;
   - picking from Stokastic's lineups by projected sum against our own ranking;
   - how duplicated the top of Stokastic's showdown order is.
5. **The 20-max blocks.** Grade every showdown 20-max since 11-8 against the call rule in section 1f: our block, the old top-20, and Stokastic's 1–20, split.

## 6. "We are always one player off"

I want this answered from the data, main and showdown.

1. **Is it true?** On each slate, take our best few lineups. How many player swaps from a top-1% lineup (or the winner) were they? How often is it one player (or the captain in showdown)? Compare with the field's and the sharks' near-misses, so we know whether one-off is just what close looks like.
2. **Who is the one player?** Start with Emmett Johnson on 11-15 (section 1f) if he was the biggest gap. On each slate, find the player whose gap (our share − the top 1%'s share) × points cost us the most. Profile every one of them:
   - price and position;
   - projected own and drafted own;
   - team, favourite or underdog, and game total;
   - role (a teammate out, target or snap share trend, a backup starting);
   - projection against ceiling;
   - any news, and when it came;
   - was he in my pool?;
   - where the tool held him, and why. Name the step: the pool, the draw, the ranges, a cap, a quota, the stack rules, the DST rule or the pick.
3. **Is there a type?** Does he keep being the same kind of player? For example:
   - the mid-priced WR at 5–15%;
   - the bring-back receiver;
   - the RB2 or backup with a starter out;
   - the cheap TE;
   - the low-owned QB;
   - the captain in showdown.

   Compare him with every player of the same price and own who didn't break the slate, so the mark has a base rate. Precision and recall, like your Stage 2 marks.
4. **Is it coverage or selection?** Was he in the pool and squeezed out by a rule (the NBA Jaylen Brown case), or never in the pool? Test a coverage rule for each: every pooled player in at least X% of the 150, or every plausible captain at least Y% in showdown. Show it through the harness, split, with its cost.
5. **What can be seen before lock?** If nothing marks him, say so plainly and say what that means for the build. For example, a wider spread over the players who could be the one, rather than a call on which one.

## 7. What I want back

- `NFL Contest Lens Rework Report 11-17.md`: short, plain words, the answers first.
- `NFL Contest Lens Rework Appendix 11-17.md`: every table.
- `NFL Rules Ledger.md`: the new ledger.

Every number gets its slates, how many were up and down, and split before counted. The cash rate goes in as the cost. Sharks are reference only. No "the projection said" as a reason. Check all saved slates before calling anything dead.

End the report with a **build list for me to approve.** For each item give:
- what changes;
- the numbers behind it;
- its cost;
- the slates it hurts on;
- its pass line, and the fires at which it gets decided.

Each build item must also say which step of the live tool it changes (section 1f) and whether it changes showdown. The user does not want showdown touched without a clear reason.

Separately, give a **shadow list**: what to log from now on, so each idea grades itself live before it is built.

**Review only: never build, edit or commit anything in the tool.**
