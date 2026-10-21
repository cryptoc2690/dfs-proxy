# NBA optimizer — change log

Newest first. One entry per change, in plain words: what changed and why.

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

## Tonight's lineups (Oct 21)

- Built from the command line, because the page still held this morning's file.
- Files: Stokastic Pre-Contest projections and lineups, plus the three DKEntries files.
- Pool: the 54-name sheet.
- Cores: Anthony Davis, Darius Garland, Ty Jerome.
- Outputs: `UPLOAD-NBA-1021-{miniMAX-150x050, AndOne-20x1, Sharpshooter-20x3}.csv`.
- Contest sizes were guessed: 70,000 for the mini-MAX and 30,000 for each 20-max. The real sizes were 71,343 / 29,726 / 15,854.
