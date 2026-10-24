# NBA optimizer — change log

Newest first. One entry per change, in plain words: what changed and why.

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

- **Hunch to test once there's data (Oct 24):** chalk or core on an 11-game slate may behave completely differently from chalk on a 5–6 game slate. So far: on the 11-game Oct 21 the core hit and spreading cost about $60; on the 6-game Oct 23 it failed and spreading won. That's one slate each, so it can't be told apart from luck yet. Revisit by slate size as the 5–7 and 8+ slates pile up.

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
