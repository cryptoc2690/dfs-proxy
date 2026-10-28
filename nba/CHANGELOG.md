# NBA optimizer — change log

Newest first. One entry per change, in plain words: what changed and why.

## Oct 28, 2026

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

**ETR removed** (subscription ended). Its slot, reader and log fields are gone; contest standings is now slot 5. The only measured slate (Oct 20, `NBA Review Report 10-20.md`) found its point projections indistinguishable from Stokastic's and its ownership worse (r 0.915 vs 0.986). Slot 1 still refuses an ETR file by name.

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
