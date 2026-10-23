# NBA optimizer — change log

Newest first. One entry per change, in plain words: what changed and why.

## Oct 23, 2026

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

- **Trim the build's info section** (asked Oct 22, for Oct 23).
  - It's too much to read.
  - Lead with one short list: every player in your pool and the added pool (cheap, mid and stud adds) who is in **zero** lineups. Then you can decide whether to give anyone a few lineups with the minimum box before downloading.
  - Keep the rest, but shorter, and below that list.

## Tonight's lineups (Oct 21)

- Built from the command line, because the page still held this morning's file.
- Files: Stokastic Pre-Contest projections and lineups, plus the three DKEntries files.
- Pool: the 54-name sheet.
- Cores: Anthony Davis, Darius Garland, Ty Jerome.
- Outputs: `UPLOAD-NBA-1021-{miniMAX-150x050, AndOne-20x1, Sharpshooter-20x3}.csv`.
- Contest sizes were guessed: 70,000 for the mini-MAX and 30,000 for each 20-max. The real sizes were 71,343 / 29,726 / 15,854.
