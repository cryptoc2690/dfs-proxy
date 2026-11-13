# NBA optimizer — change log

Newest first. One entry per change, in plain words: what changed and why.

## Nov 13, 2026

**Weak chalk is left out on 2-4 game slates.** From `NBA Weak Chalk Rebuild Report 11-13.md`. Weak chalk is a player at 20%+ projected own with no role change tonight (no takeover mark, no minutes lift of 5+ over his real last five games, no new starting job) who comes off the bench, has a teammate at his position back tonight, or is projected under 22 minutes. On 22 nights those 31 calls reached projection 19% of the time against 49% for the other chalk; leaving them out of the pick put more lineups in the top 1% on all 8 short slates (150: +2.9 a lineup, +1.4 top-1% lineups a contest), and it held with the "no role change" filter chosen held-out. Capping them or forcing a salary shape on the lineups without them did not.
- 2-4 games: the calls are left out of every lineup, the 150 and the 20s; the lineups refill on their own (they come out more mid-range). Settings shows the night's calls as soon as the projections are dropped; untick one to keep him. A typed minimum also keeps him.
- 5+ games: a shadow only. The build is unchanged; the 150 and 20s it would have built are saved in `logs/shadow/<date>/` for the review (it rests on one night there, Nov 9).
- Real minutes and starts come from ESPN's public box scores: `role.py`, one small file a day in `logs/espn/` (seeded with Oct 20 - Nov 12 from the review's own files), fetched in the background when the tool starts and before each build. A name that does not join falls back to the one player on his team with the same last name and first initial (Alexandre/Alex Sarr, Gregory/GG Jackson). With box scores missing the rule is not applied, and the build says so.
- Replayed on the 22 saved nights the tool's calls match the review's on 30 of 31 (Harden on Oct 31 is 19.7% own in the tool's copy of that night's file) plus Isaiah Jackson on Nov 1 (projected 21.8 minutes). Nov 12: Morez Johnson and Kevin Huerter, the sharp's two calls.

**On 5-7 games Stokastic's lineups are picked by projected sum.** From `NBA Shark Edge And Slate Triggers Report 11-13.md` (T2): on the nine 5-7 game nights, ahead on the mean on 8 (+1.9 a lineup), top-1% lineups +0.11 a contest, best rank 404 → 359. On 8+ games the Top 10% figure stays ahead and stays. The log keeps it as `sets.big.filePick`.

**Logged with every build, no effect on it** (`typed.json` → `settings`): the weak chalk calls and every chalk player's role marks (`weakChalk`, `roleMarks`); the Bones type (`bonesType`: cheap, under 24 projected minutes, 1.1+ DK points a minute, two earlier games of 6x his salary in under 24 minutes; half went over Stokastic's ceiling, 10 of 15 were off the pool); the sharp's **bad chalk** calls, a new Settings box, one name per line with a word of why (`badChalk`); the slate traits (`slateTraits`: share of teams tipping 30+ minutes after the first game, the gap between the top two studs' projections, studs projected 40+).

**`grade_night.py`** rebuilds a night with only the box scores before it, and a night built before today without the weak chalk rule and with the old file pick, so seed 0 still reproduces the played set (Nov 12: +7.3 and +8.9, as played).

## Nov 12, 2026

**The 150's takeover lean, added: it was in the tested spec and left out of the Nov 7 port.** The prototype's final spec (`takeover_k=1.5, takeover_cap=0.95`, the 150 only) floors takeover chalk — the takeover mark (projection 1.3x his normal and +5, a regular out leaving 10%+ of the team's normal projection open) at 15%+ projected own — at 1.5x his own, capped at 95%. The Nov 7 port kept the mark as a warning and applied nothing, so every ledger row has been graded against a spec the tool did not run. Found by `NBA Contest Review Report 11-12.md` (row T13).
- Over 21 nights in the harness: +12.4 / 2.48 top-1% lineups a contest with the lean against +12.2 / 2.29 without; it acts on 10 nights (ahead on the mean on 6, behind on 2); in the 20 it costs, so the 20 stays without it.
- Applied after the pick to the whole 150 (Stokastic and own lineups), with typed caps respected, before the 4-lineup minimum — as in the prototype. The build names the player and the target; the log keeps them under `sets.big.takeoverLean`.
- Rebuilt from the saved inputs: Nov 11 Wolf to 81 of 150 (played +8.0, 1 top-1%, best 579 → +9.8, 2, 327); Nov 7 Ayton to 65 of 150 (+19.8, 10, best 13 → +16.8, 9, 41 — he lost his start); Nov 9 no takeover chalk, identical.

## Nov 10, 2026

**`grade_night.py`: the seed rebuilds no longer see the night's own winners.** The 20-max rebuilt with seeds 0–5 reads the winners' ranges from every saved night, and once a night's standings are saved that included the night being graded — its own top 1%. The live build never did (it reads only nights before today). The rebuild now reads only the nights before the graded one, and seed 0 reproduces the set played exactly on Nov 7, 8 and 9 (before, Nov 9's seeds all read above the played set by up to 8 a lineup).

## Nov 9, 2026

**The 20-max is the typed pool only again; the Nov 8 add is out.** From `NBA 20-Max And Pool Add Report 11-09.md`: on 19 nights (38 20-max contests, six seeds) every version of adding Stokastic's off-pool players lowered the 20-max — pool only +12.3 / 1.60% top-1%, the Nov 8 version +11.9 / 1.56%, the every-night Stokastic 150's +11.0 / 1.51% (2–4 games: top-1% rate halved), the 50's or 20's +11.6 / 1.21% and +11.9 / 1.23%. Into the 150's own lineups the adds cost 1.1 a lineup for nothing at the top. The pool already held 113 of the 127 slate-breakers.
- Nov 8's 20s were a normal set drawn badly: Acuff (in the pool) at 5% and 20%, Giannis and Lopez under the field, Kuzma and Klay Thompson off every shark's 20. The same pool rebuilt with six seeds ran −2.8 to +10.7; the sets played were +0.4 and −4.1.
- The top-3 raise and the two different 20s stay as they are (no raise, a 1.5x raise and a lowest-value swap all read lower over 38 contests).
- The 150 is unchanged: its Stokastic lineups still carry whoever is in them.

**`grade_night.py`: the pick's tilt.** From `NBA Off-Pool Scenarios And Pool Weight Report 11-09.md` (build list 6): every pool player at 5%+ projected own, our share in each played set against the top 1% and the sharks' median, grouped by value (projection per $1,000: under 4.8, 4.8–5.5, 5.5–6.5, 6.5+) and by value rank among the pool players in his $500 salary band, then the players one by one. The review found the pick by projected sum runs heavy on the best value in a band, 35%+ chalk and punts, and light on low-value players (Acuff's type); every floor and cap that corrects it lost on 19 nights, so it is watched, not built. The same report: no news swap, no pool-weight rule, no pool add — nothing else changes.

**`grade_night.py`: two more blocks.**
- Where each played set lost and gained against each shark's set and the contest's top 1%: (our share − theirs) × his real points, the five biggest costs and three biggest gains. The lines sum to the gap between the two sets' average lineups.
- The 20-max rebuilt with seeds 0–5 from the night's saved inputs (temporary logs), each graded against the field with its best rank, and where the played set falls among them. About 25 seconds a night.

## Nov 8, 2026

**The 20-max pool takes in the Stokastic lineups' off-pool players.** The user's call: the 150's Stokastic lineups are not pool-bound, so a player they bring in should be open to the 20-max too.
- After the 150 is built, every player in its Stokastic lineups who is not in the typed pool is added to the 20-max's pool. He may or may not make a 20-max lineup; nothing forces him in.
- Only when a pool is typed (with no pool the 20-max already draws from the whole slate) and only from Stokastic lineups actually used in the 150. On 2–4 game slates the 150 has none, so nothing changes.
- The build names the added players, and the log keeps them under `sets.small.fromFile`.
- Nov 8 rebuilt from its saved inputs: eight added (Keyonte George, Ace Bailey, PJ Washington, Zion Williamson, Morez Johnson, Kyle Kuzma, Naji Marshall, Brice Sensabaugh); seven of them made at least one of the 40 20-max lineups.
- Not tested by the data chat; grade it after the fact with the rest.

## Nov 7, 2026

**Settings: tick the contests to fill.** The user hand-builds the contests that are not the 150 or the 20-maxes, and the tool must not overwrite them.
- Dropping the DK file lists its contests as tick boxes at the top of Settings (it opens when the file holds more than one). Contests of 20 or more entries start ticked.
- Only ticked contests are built and written to the upload file. An unticked contest's rows are left out entirely, so DK keeps those lineups exactly as entered; the build names the contests it left alone.
- Each ticked 20-max gets its own set. Nothing ticked stops the build and says so.
- Late swap is unchanged: it still checks every entry in the file, and only replaces players who are out.

**Two 20-max contests get two different sets.** From `NBA Slate By Slate Calls Report 11-07.md`, section 5C: on 17 nights with two 20-max contests, the next 20 in the pick order for the second contest gave 0.61 top-1% lineups a night (both contests) against 0.56 for the same 20, with a better best rank (396 against 428) and the mean within 0.2. Inside the noise on 17 nights; on the check list for 20 more.
- One DK file with several 20-max contests: the first gets the top 20, the next the next 20 in the pick order (picked together under the 90% cap), and so on. Each set gets its own top-3 raise. A 1-entry contest takes the first lineup of the first set.
- Nov 2's file (150 + Four Point Play + And-One + a 2-entry contest) built clean: the two 20-max sets share 0 of 20 lineups.

**Late swap: the out player plus one, by projection.** From `NBA Slate By Slate Calls Report 11-07.md`, section 5B: on the 26 of our lineups that held an in-night out (Jerome Oct 31; Toppin, Z. Williams, Poeltl Nov 1), swapping the out player plus one more by the fresh pull's projection scored 282.1 real points against 269.1 one-for-one and 271.6 for the old swap.
- A lineup with an out player (ruled out, removed, or over a typed cap) takes the roster with the most projected points from the fresh pull that drops him and moves at most one more unlocked player. No pool preference, no simulation. Typed caps still hold.
- The search pairs his slot with each other open slot over the top 20 by projection and the top 20 by value, so the second move can spend the freed salary.
- Oct 31 replayed: all six lineups holding Jerome swapped him and one more for Cam Spencer (40.2 real) and an upgrade.

**The new build.** From `NBA Before The Build Report 11-07.md` (the final spec) and the three reviews before it; the code is `newbuild.py`, ported from the prototype in `~/Downloads/NBA Results 1/Prototype/`.
- **The 150 by slate size:** 2–4 games all from your pool; 5–7 games all from Stokastic's lineups (every file dropped, as one set, picked by their Top 10% figure); 8+ games 75 from Stokastic and 75 from your pool, drawn the old way (projection^3, up to $2,000 left, 4,000 candidates).
- **Pool lineups:** projection^2.5, within $300 of the cap, kept inside the ranges the top-1% lineups fell in on earlier nights of the same slate size.
- **The 20-max** is its own set from your pool (not a slice of the 150), with the night's top three owned (15%+) raised to 2x their projected ownership, at most 95%.
- **One cap:** 85% of a 150, 90% of a 20. A 150 has nobody in fewer than 4 lineups.
- **Gone:** the simulation, the bar, the win-rate score and duplicate model, neutral rules, the tier cap, core floors, pool adds and stud floors. Removals no longer shift points to teammates (build and late swap).
- **Kept:** your pool, removals, caps (a cap at 0 is a removal) and minimums; the DK readers, checks and upload writer; the build log.
- **Saved every night:** each Stokastic projections file dropped, with its time, and what moved between pulls (`logs/pulls/`); the build's input files (`logs/inputs/`); the names the old add rule would have added; the late swap's risers it did not act on.
- **Tested on 16 saved nights** with each night's pool, one seed, graded on the real standings: the 150 +12.9 a lineup against the field with 1.71% top 1%; the 20-max +15.4 with 2.81% (the prototype's six seeds: +12.1 / 1.38% and +12.2 / 1.40%; our real sets +6.9 / 1.25% and +7.5 / 1.03%).
- Showdown still runs the old build. The old classic build is `run_build_old`, kept for after-the-fact replays.

**Late swap: outs only.** From `NBA Where The Points Come From Report 11-07.md` and `NBA Full System Review And Prototype Report 11-07.md`:
- The swap now replaces a player who is out (ruled out, removed by you, or over a typed cap) and moves nothing else. No news swaps at +8, no no-news swaps at +14.
- Why: over 34 contests the old swaps added about a point a lineup and gave back top finishes, 42 top-1% lineups down to 36. Where a pre-swap export exists, our 20-max sets ran 2.27% top 1% before the swap and 1.03% after.
- A typed cap still trims, as before. Each held lineup says "outs only" and names any news player who stayed.
- The "allow no-news swaps" box is greyed out while outs only is on.

## Nov 2, 2026

**Late swap: your player caps now carry into it.** The user: a starter listed on the bench, a DNP risk, in 28% of the mini-MAX — "limit that to 10-15%, eat the risk if he does play".
- The late swap reads the "Cap individual players" box, per contest, the same way the build does ("Name 20" = 20 lineups, "Name 12%" = a share).
- A capped player over his cap is taken out with no bar, as a late scratch is, until the contest is at the cap. The lineups where he costs least to replace go first. Still at most two players moved.
- No swap brings a capped player in past his cap. A locked player can't be taken out, so the page says if he stays over.
- The notes say each capped player's count before and after.

## Oct 29, 2026

**Late swap: no swap moves more than two players.** From `NBA Late Swap Parameters Note 10-29.md`, every saved swap night replayed and graded on what the players scored:
- Two-player swaps: 19 made, 13 gained, +129. Four or more: 14 made, 4 gained, −163, including all six of Oct 28's big losses (rebuilds around Capela).
- Capping every swap at two: −64 → +118 over the saved nights. Oct 28 goes −366 → −24; Oct 26 gives back some (+249 → +104).
- Applies to news and no-news swaps. A ruled-out player is still always swapped out.
- Not changed: the +8 / +14 bars, the team limit, the aggression from rank, 1,500 sims. Watch: bench-move news (Allen +90, Holland −36, Capela −366) — decide after three more swap nights.
- Oct 28 replayed: 2 swaps (both no-news, +14), Curry kept in every lineup.


**Late swap: a news swap has to take the news player out.**
- Before, bad news on one player opened the whole lineup at the +8 bar, so the tool could keep the news player and swap anyone else. Oct 28: Capela moved to the bench, and 25 of 27 swaps kept Capela and sold Curry, the night's top scorer.
- Now a news swap must take the news player out and clear +8; the rest of the open lineup can change with him. Every other move is a no-news swap at +14, even in a lineup holding the news player.
- No-news swaps are back ON by default (the user's call). Good news (bench to starting, projection up) comes in through them at +14. A lineup projecting top 1% still never takes one.
- A ruled-out player is still always replaced.
- New news: a starter who stays a starter but loses 25% and 5+ of his minutes.
- Oct 28 replayed: 27 swaps → 9 (5 take Capela out at +8, 4 no-news at +14). Curry sold 25 times → 3.

## Oct 28, 2026

**Settings page tidied.** The settings that run themselves (lineups, ours, contest size, neutral build, round-out, ownership lean, how full, minimum projection) sit under a closed "Advanced" fold. The main settings are now just the pool, players off the pool, late scratches, caps and minimums.

**Lineups, ours and contest size fill themselves in from the DK file**
- Lineups: the biggest contest's entry count in the DK file.
- Ours: half of a 150-max, 11 of a 20-max (still 15/5 on 5–7 games).
- Contest size: prize ÷ entry fee ÷ 0.84, read from the contest name and fee in the DK file. Against the real standings, Oct 20–27, it matched within 0.5% on 22 of 23 contests (e.g. $30K mini-MAX $0.50 → 71,343 real). The one miss was opening night's And-One, which didn't fill.
- Slot 3 says it when the file is dropped, e.g. "NBA $20K mini-MAX — 150 lineups, ~47,585 entries". The boxes are still there to override. The contest size is now in the build log too.

One build from three reviews: `NBA ETR Deep Dive Report 10-28.md`, `NBA Recency Ownership Report 10-28.md` and `NBA Bar Field And Ranges Report 10-28.md`.

**Even ranges for players projected 10+**
- The simulation drew every player from a right-skewed range (big nights stretch further than bad ones). Real nights don't look like that: across 811 players who played, Oct 20–27, they reached further down and slightly less far up.
- 14% landed under their simulated 10th percentile, against 10%. With an even (normal) range it's 10.9%, and the fix held on 8 of 9 nights left out of the fit. Studs went from 21% to 15%.
- Players projected under 10 keep the skewed range.
- Rebuilds: top 0.1% finishes 34 → 39 in the 150-max (a reseed gave 32). Who the build plays didn't change.
- Not done: lowering every projection (it needed twice the measured gap and broke the top of the range) and a "short night" chance (it pushed the bar up and did nothing).
- Still true: cheap chalk and players on the losing side of a 20+ blowout land low far too often (cheap chalk 27% → 15%). Their whole range sits 4–7 points high, which a range change can't fix.

**The bar's field leans to Stokastic's projected ownership where the two differ by 5+ points** (classic)
- The bar is built from Stokastic's lineups file, which runs about 5 points light on players coming off good games. Stokastic's projected ownership is closer on them.
- Where a player's share of the file is 5+ points off his projected ownership, the file is re-weighted so his share matches it. Everyone else is left alone. Example: Achiuwa, Oct 27, 35% of the file → 48% (real 52.5%).
- The page lists who was moved, under "All build details".
- Rebuilds: top 1% finishes 266 → 296 in the 150-max, mostly on 2–4 games (reseed 264), worst-10% slightly better. Matching everyone fully or halfway cost the 2–4 game tail, so only the 5+ gap version is in.
- Together with the even ranges: top 0.1% 41 against 34, top 1% 301 against 266.

**Each player's share of Stokastic's lineups file is logged** in the build log and `nba_slates.jsonl` (`file_share`). The Recency review needed it and it existed only inside the build.

**ETR removed** (subscription ended). Its slot, reader and log fields are gone; contest standings is now slot 5. Slot 1 still refuses an ETR file by name.
- Why, over seven nights: its projections were slightly better than Stokastic's (0.2 points a player, mostly bench players), its ownership was two to three times worse at the chalk end every night, and building on it alone lost at the top end on 5+ game slates. A 50/50 blend was inside the noise.

**20,000 simulations per build** (was 4,000)
- 4,000 came over from NFL and WNBA and was never tested on NBA.
- Oct 27, same candidate lineups, two different random draws: the 75 lineups chosen matched 37 of 75 at 4,000, 46 at 10,000 and 52 at 20,000. Player exposures were stable at all three, so this sharpens which exact lineups are picked; it doesn't change who is played.
- Costs about 25 seconds a build. Late swap keeps its own count.

**Logging for the next reviews** (`NBA Review Report 10-28.md`, items 2–4)
- **Each player's range against what he scored.** Every build logs each player's simulated p10 / median / p90, with salary, ownership and the game's spread (from LineStar). Grading writes it against the actual score, real ownership, favorite or underdog, starter and minutes to `nba/logs/nba_player_ranges.jsonl`.
  - Why: cheap chalk has landed under its p10 31% of the time, against 10% expected, and blowout games are rare. This is the record that will show whether it's a pattern.
- **The bar against the real lines.** Every build logs the bar it ranked against. Grading writes it next to the contest's real top-1%, top-0.1% and first-place scores in `nba/logs/nba_bar_lines.jsonl`.
  - Why: the bar ran about +21 over the real top-1% line on 2–4 games and −22 under it on 11.
- **Cores typed under the slate rule** now read back as "3 cores read, treated as pool players", not "0 cores".
- Builds before Oct 28 have no ranges or bar logged, so they grade without them.

**Cores removed from the page.** Since Oct 24 a core has been a plain pool player, and the sheet's pool already lists them. The build and late swap still read cores from the command line (and the old core floor with neutral set Off), but the page no longer asks.

## Oct 27, 2026

From `NBA Thorough Review Report 10-27.md` (8 slates, 23 contests, about 7,000 rebuilds on the same seeds).

**The slate decides: nothing to remember**
- **Neutral build:** on at 5–7 games, off at 2–4 and at 8+.
  - 2–4 games, 150-max: top 0.1% went 3 → 19 with it off, and lineups in the field's bottom 10% went 18.5 → 7.
  - 8+ games: money went $97 → $159 with it off.
- **Pool round-out adds:** off under 5 games, on at 5+. On 2–4 games they hurt, and they were behind most "nobody would do this" exposures. On 5+ they helped.
- **Slot 1 says what the slate gets**, e.g. "7 games … → neutral on, round-out on". The check line says it again after the build.
- **Late swap follows the same rule**, so the set is only held neutral when it was built neutral.
- Both settings are now **Auto** on the page. On and Off still override, and the build says "set by hand".
- **Cores stay plain pool players either way.** Only a hand-set Off brings back the old core floor.

**20-max: no lineup from past the 1,000th candidate**
- Oct 26's near-last And-One lineup (O'Neale) was candidate 3,966 of 4,000, taken by the "filled by score" fallback.
- A short slot is now filled inside the top 1,000: inside the caps first, then anything. It goes deeper only if nothing inside fits.
- Minimums may still go deeper.
- In tests this changed no scores; it removes that kind of lineup.

**Caps are lineups now**
- "Reed Sheppard 50" means at most 50 lineups, the same as the minimum box. Add % for a percentage ("Reed Sheppard 33%").
- Three nights of caps had been typed as counts while the box read percentages; for example, Booker "2" was 2%, 3 of 150.
- A count bigger than the set caps nothing and is named back. A percentage over 100 is refused rather than quietly set to 100%.

**The check line tells the truth**
- **Arm split:** shows the ours/Stokastic split as built, not as planned.
- **Tier cap:** the finished-set check now covers the 20-max tier cap too, and names any overrun.

**Bugs fixed**
- **Late swap:** a swap DK refused stayed in the set's team and side counts. Now it's only counted once DK's rules pass.
- **Rotation, short sets:** with fewer lineups than games (a 5-lineup Stokastic arm on 7 games), the spare main games went to the first games alphabetically, and the gap was counted as "could not fit". Now the spares go to whichever games reach them first.
- **Salary-left factor:** it was missing from the Stokastic half's duplicate estimate.
- **Game count:** only counts real "A@B" games.

**Logged per build:** the slate rules; each arm's actual size; how each lineup was taken (rules, minimum, fill, any); its place in the scored list; tier-cap overruns; and cap lines too big for the set.

**Not built, per the report:** a new generator for 5+ games, and the candidate limit on the 150-max. Both were tested and neither helped. Late swap stays as built.

**What would change it:** two more 5–7 slates where neutral off beats neutral on (top 1% and the tail) moves 5–7 to off. A second 11-game slate where neutral wins reopens 8+.

## Oct 26, 2026

**Late swap: news swaps may add a team at its limit** (`NBA Review Report 10-26.md`)
- **What changed:** a swap forced by bad news no longer checks the team limit. The 50/50 sides still hold, and no-news swaps (off by default) keep the full check.
- **Why:** across the saved nights the team-limit check blocked 4 good news swaps (+85 real points) and 1 bad one. The chalk drift it was built for is already stopped by no-news swaps being off.
- **Replay of Oct 21 at 7:17:** 4 mini-MAX swaps, +79 real points, all gaining (before this change: 3, +39).
- **The rest of the review said keep:** the team limit, dropping the Stokastic half under 5 games, the 20-max tier cap (10 peers, 50%), the 8% bench line, bad news only, and no-news swaps off.
- **Watch:** neutral against the old build is 4 nights to 2 so far. The old build won Oct 21 (11 games) and Oct 25 (4 games); on Oct 25 it was through a heavy DET stack of Stokastic-file lineups that neutral can't make (#12 against #113).

## Oct 25, 2026

From `NBA Review Report 10-25.md` and `NBA Follow-up Report 10-25.md`.

**Late swap**
- **Only bad news reopens a lineup** (ruled out, projection cut, moved to the bench). Good news is listed but reopens nothing.
- **A move to or from the bench counts as news only when projection or minutes moved 8%+.** Every bench move is logged with its percentages so the number can be re-fit.
  - 8% keeps Oct 21's Grayson Allen swaps, which gained.
  - It drops Grant, Sasser and Risacher, whose swaps lost.
- **No-news swaps are off by default.** They went 4 for 15, −198 points, over five nights. A checkbox next to Late swap turns them back on.
- **Swaps respect the neutral team limit and sides across the set.** A ruled-out player is still always replaced.
- **Each run saves its input files** (projections, DK export, standings) for exact replays.
- **On the saved nights:**
  - Oct 24: 0 swaps instead of 18. The 18 had cost 278 points.
  - Oct 21 at 7:17: a handful of Allen-news swaps, all gaining in real points.
  - Oct 21 at 8:13: untouched.

**20-max (any build of 20 or fewer)**
- **On 3+ games:** a player with 10+ tier-mates (within $600, projected at least half his) is held to half the set. Typed caps win.
- **On 5+ games:** the split becomes 15 ours / 5 Stokastic.
- The check line shows both.
- On 5–7 games the median best rank went from 3.1% of the field to 0.3%. Low confidence: five contests.

**Tracking only:** grading writes each team's starter and bench residual to `nba/logs/nba_team_residuals.jsonl`, for the depth-chart pattern (PHI, HOU, BKN starters high, benches low).

**Not built:** a team limit sized by each team, or adjusting for a team's projection bias. Neither held up.

## Oct 24, 2026

**Two late-swap bugs fixed** (found on the Oct 23 night slate: 3 cores, no pool, 0 of 20 changed)
1. **Cores alone are no longer a pool.** With no pool typed, every other player had counted as "off your pool". Each swap then needed +24 to +32, and nothing moved. Now "off your pool" only applies when a pool was typed or logged.
2. **The bench rule no longer floods the news.** A bench player beaten by a cheaper starter now counts as news only if you hold him and there's no logged build of him to compare against. That night it called 27 players "news"; the same files now show the 3 real changes (Gregory Jackson starting; Gui Santos and Jerami Grant to the bench). Rerun on the same 9:55 pm files, 8 of the 20 lineups would have moved instead of 0.

**Neutral build, on by default**
- Built like NFL showdown: field ownership is shown, but it never steers the build.
- **Main game rotated:** each lineup's main game is rotated evenly across the slate.
- **Sides 50/50:** inside that game, the two teams are held even.
- **Team limit, by slate size:** none at 2 games; 1.25× the even share at 3–7 games (63% at 6); a loose 1.5× at 8+ (47% at 11).
- **Stokastic's half:** dropped at 2–4 games; kept at 5+ and held to the same rules.
- **Cores are just pool players** (your call): they're the sharp's cash-game reads, not build-arounds. With no pool, they carry nothing. Late swap treats them the same way.
- **8+ games:** a quota-aware generator, so the team limit can actually be reached.
- **One line** in the check row says what was applied, and how close the set got to the limit.
- **Untick "Neutral build"** (`--no-neutral`) for the old build.
- From `NBA Neutral Build Report 10-24.md`, against the old build on the same seeds:
  - 2 games: best rank #327 → #137.
  - 3 games: #723 → #179, top 10% 9 → 22.
  - 6 games: top 1% 0 → 6, top 10% 6 → 25.
  - 11 games: limits cost money; that's why the 8+ limit is loose.
- Rebuilt here:
  - Oct 23: CHA 100% → 65%, IND 80% → 65%, Schroder 76% → out of the top four.
  - Oct 21: LAC 90% → 58%, Jerome 79% → 55%.
  
  One slate per group on 5–7 and 8+, so grade the next few.

## Oct 23, 2026

**New info section after a build**
- **One check line.** Everything that confirms the files and the build is right collapses into one green ✓ line. If anything is wrong, it turns red and lists only what failed.
- **In 0 lineups:** everyone from your pool, cores and added players who's in no lineup, so you can give them a minimum before downloading.
- **Overweights:** every player 15+ points over the field, with:
  - how much of the set's chance at the top 1% survives his floor night, and what his ceiling night adds;
  - his same-range peers (within $600, a 40–60% coin flip to outscore him), with our exposure and the field's.
  
  It shows the bet; it doesn't change the build.
- **Short lines** for the added players, the cores, and the most-used players against the field.
- **Every original message** is still under "All build details" at the bottom.
- The overweights and the zero list are logged each night, so 10–15 slates can say whether floor nights cost what the simulation says.

**Cheap tier gets a real range**
- Anyone projected under 10 now gets a spread of at least 6 points; Stokastic's file gives them 0.3–3.
- Why, from `NBA Range Of Outcomes Report 10-23.md`: across Oct 20–22, 54% of these players beat the tool's own 90th percentile, when it should be 10%. WNBA showed the same shape.
- Players projected 10+ were measured as right, so they're unchanged.
- **Not built, per the report:** no limit on going over the field and no peer spreading. On 3 slates a limit was free on the 2-game night but cost about $45 on the 11-game night.

**Late swap: see each lineup**
- Each row of the late-swap table has a **lineup** button, like WNBA's. It opens what you entered next to the proposed lineup, slot by slot, with the changed slots lit.
- Marks: 🔒 locked, ★ core, ◇ off your pool. Each player shows salary, projection and ownership.
- The swap table's change column now wraps, so the table fits the page.

## Oct 22, 2026

**Salary left counts in the duplicate estimate** (classic)
- Based on `NBA Salary Left Report 10-22.md`, which looked at every roster in six DK standings files.
- What it found:
  - Rosters that leave salary are duplicated far less, even at the same ownership.
  - The tool's estimate ran backwards on this.
  - The effect is much stronger on small slates: 27% of full-cap rosters were duplicated on the 3-game slate vs 7% on the 11-game slate.
- The estimate is now multiplied by ×1 at $0 left, ×0.5 at $100–300, ×0.15 at $400–700, ×0.05 at $800–1,200, and ×0.03 beyond that.
- Over 10 seeds on both slates it moved no money. It changes which lineups get picked on chalky, small slates. If it keeps moving nothing after five small slates, take it back out.
- Not changed, per the report:
  - The $2,000 limit on unspent salary, because it never binds.
  - Deliberately building lineups that leave salary, because the ranking discards them.
- Separate, not fixed yet: the counts taken from Stokastic's lineups file run 2–4× too high.

**Stud adds in the pool round-out**
- Adds the 3 highest-projected players above $7,500 who are under 10% projected owned and not on your sheet.
- Each gets a minimum of 2× his projected ownership, in lineups, at least 1. Oct 21 would have been Edwards 10, Banchero 7, Sengun 4 of 150.
- Why: on Oct 21 Edwards ($8,800, 3.3% projected) scored 72 and was in 8 of the field's top 10, and we had none. In the pool alone he reached only 1–3% of lineups, because the ranker doesn't see ownership at this contest size.
- Evidence so far, from `NBA Stud Report 10-22.md`, rebuilt over 10 seeds:
  - Oct 21: median mini-MAX net went from +$105 to +$132.
  - Oct 20: the rule adds nobody.
- Two slates aren't proof. Keep the rule if, on at least 8 of the next 12 mini-MAX nights, it beats the same build without it.
- Cap a player at 0 to skip him (e.g. Booker), and the next stud takes his place. A minimum you type wins.

## Oct 21, 2026

**Old projections are refused** (commit 3f1c387)
- What happened: the morning Data Hub file still held Oct 20's games (BOS, DET, NYK, OKC, PHI, SAS). Slot 1 gave it a green tick, so tonight's pasted pool matched nobody. Lineups had to be built by hand from the command line minutes before lock.
- Slot 1 now refuses a projections file whose games match a build from an earlier day ("OLD SLATE — … built on 2026-10-20").
- Slot 1 now lists the games in the file, not just how many.
- If the projections and the DK entries are for different nights, both slots turn red and Build and Late swap stay off.
- A refused file clears the player list instead of leaving the old players in place. Picked names that aren't on the new file are dropped.
- Pasted names that don't match show in red under the pool box.
- The page is never served from the browser's cache.
- Late swap refuses an old night's projections too.

**Pool round-out** (commit 299b927)
- Classic only. Adds the 5 best points-per-dollar players at $4,500 or less, and the 5 best from $4,600 to $7,500. Every add must project 14 or more.
- The added players are named in a note and logged.
- To turn it off, untick "Round out the pool automatically" (`--no-pool-adds` on the command line).

**Late swap** (commits 2e5218b, 300d038, f31ae57)
- Classic only. WNBA's rules, run on NBA's files.
- Locked players stay in their slots.
- A player who is ruled out or removed is always swapped. No bar, no extra message.
- News on a player reopens the whole lineup. The best lineup from the open slots is used if it beats the current one by 8 points.
- With no news, the bar is 14 points.
- The projected final for every lineup counts time left on the clock (DK's TimeRemaining).
- You can untick any swap before downloading.
- Both policies, free and news-only, are logged to `nba/logs/nba_swaps.jsonl`.

**Build checks and logging** (commit 9a734a4)
- A projections file that isn't Stokastic's is refused. On Oct 20 ETR's file ran in slot 1 unnoticed.
- Every build logs each file's name and fingerprint.
- Minimum lineups per player are set as a count, not a percentage.
- ETR slot added. It is logged only and not used in the build.

## Wanted, not built yet

- **Teams that fail together (asked Oct 24).**
  - Oct 23 main: CHA was in 100% of our lineups and IND in 80%, against the field's 79% and 64%. Both got blown out, so the set was dead.
  - The simulator draws every player independently (`GAME_SD = 0`), so it can't see a blowout.
  - `NBA Team Blowout Report 10-24.md`: teammates don't correlate on average, but collapses happen far more often than the ranges allow. That's about 20 slates from being sized.
  - The neutral team limit (Oct 24) now caps the exposure.
  - **Still open:** blowout nights in the simulator, once there's data to size them.

## Tonight's lineups (Oct 21)

- Built from the command line, because the page still held this morning's file.
- Files: Stokastic Pre-Contest projections and lineups, plus the three DKEntries files.
- Pool: the 54-name sheet.
- Cores: Anthony Davis, Darius Garland, Ty Jerome.
- Outputs: `UPLOAD-NBA-1021-{miniMAX-150x050, AndOne-20x1, Sharpshooter-20x3}.csv`.
- Contest sizes were guessed: 70,000 for the mini-MAX and 30,000 for each 20-max. The real sizes were 71,343 / 29,726 / 15,854.
