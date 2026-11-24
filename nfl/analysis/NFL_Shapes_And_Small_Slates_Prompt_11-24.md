# NFL Shapes and Small Slates Prompt (11-24)

**Review only. Never build, edit, run for a live build or commit anything in the tool (`~/Desktop/dfs-proxy/nfl`). Every change goes on a build list for me to approve in the tool chat.**

This replaces `NFL_Small_Slate_Shapes_Prompt_11-24.md`. Use this one.

## 0. Why this prompt

The main-slate build bans three shapes outright:
- QB+3 and QB+4 (only QB+1 and QB+2 are built);
- the QB's own RB;
- more than $400 of salary left.

The bans came from the standings, where those shapes did worse than the field on average. But a ban means **if that shape wins on a slate, we cannot win it.** That is the Emmett Johnson lesson from your chalk report: being off what the field is on only pays when it fails, and costs everything when it hits. Being at the field's share was right; going far over did not help.

So the question is not "be different". It is: **should these shapes be carried at about the field's share instead of banned?** On main, and on small slates.

**Thursday (11-26) is a 3-game slate** (CHI @ DET 55.5, PHI @ DAL 47.5, KC @ BUF 47.5), about 300,000 entries in the 150-max. Stokastic's Thursday lineups file shows the field's likely shapes. Our build today puts 0% in the shapes they cover:

| Shape | Stokastic's Thursday file | Our build |
|---|---|---|
| QB+1 | 29% | 45% |
| QB+2 | 42% | 55% |
| QB+3 | 21% | 0% |
| QB+4 | 3.5% | 0% |
| QB's own RB | 35% | 0% |
| $1,000+ salary left | 2% | 0% |

The tool chat built Thursday's early files each way (pre-lock, no results). Allowing the big stacks and the QB's RB took exact copies of a Stokastic lineup from 4 to 13–16 of 150. Forcing $1,000+ left took them to 0, at 3–6 projected points a lineup.

## 1. Main slate: field share against a ban (11 slates)

Through your harness, graded split first, held out, with the cost shown. The 150 and the 20s.

For each banned shape (QB+3, QB+4, QB's RB, salary left over $400), test:
- banned (today);
- at the field's share on that slate, using the field's mix from the standings or from Stokastic's file before lock (say which; before lock is what the tool can use);
- at half the field's share;
- forced above it.

Then all of them together at field share, and only the stack mix at field share.

Answer directly:
1. On the slates where a banned shape won (the winner, or a big share of the top 1%), what did the ban cost?
2. On the slates where it failed, what did field share cost over the ban?
3. Net over 11 slates: is "field share" better, level or worse than "banned"?

## 2. Build your own 3-game slates from the main slates

There are no real 3-game NFL slates in the store, but there are 11 main slates with every player's actual points. Cut each main slate into 3-game slates:
- every combination of 3 games, or a sample if that is too many;
- the real pre-lock projections and salaries;
- the real points scored.

**What has to be modeled, and how to check it:**
- **Ownership on a 3-game slate is more concentrated than on the full slate.** Rescale each player's projected ownership inside the 3 games (total 900%, nobody over ~90%) or use a better method of your own. Say which, and why.
- **There is no real field for these slates.** Model one: draw field lineups the way the field drafts (by the rescaled ownership, with the field's shape mix), at the size of a real 3-game contest (~300,000 entries), copies included. Its top 1% is the line.
- **Calibrate the model before trusting it.** Run the same field model on:
  - the full main slates, against their real standings;
  - a few showdowns (1 game), against theirs.

  Does the modeled field's top 1% look like the real top 1%: shape shares, own sum, copies? If it doesn't, fix it or say plainly how far off it is.
- **The unit is the original slate (11), not the 3-game cut.** The cuts from one Sunday share the same games and are not independent.

**On the cuts, answer:**
1. What shapes do the top-scoring lineups take: QB+1 through QB+4, the QB's RB, bring-backs, salary left, own sum? Against the modeled field, counted and split.
2. Test builds, graded against the modeled field, counted and split, with the cost:
   - today's build;
   - shapes at the field's share;
   - shapes banned;
   - shapes forced;
   - salary left forced to $1,000–2,500 and $2,000–3,500;
   - the field's share plus a salary-left slice.
3. Does anything that loses on the 10+ game main slate win on 3 games, or the other way round?

## 3. Thursday itself: a real check

Before Thursday's lock, write down the sets to grade so the grade is honest. Each is built from the inputs the tool saves Thursday (`logs/inputs/2026-11-26/...`), seed 0, the same typed pool, 150 lineups and two 20s:

| Set | What changes from the live build |
|---|---|
| A | nothing (the live build) |
| B | shapes at the field's share from Stokastic's Thursday file (QB+1–4, the QB's RB allowed) |
| C | all big stacks: QB+3/QB+4 only, QB's RB allowed |
| D | salary left forced to $1,000–2,500 |
| E | lineups not in Stokastic's Thursday file |
| F | your best pick from sections 1–2, written down before lock |

After Thursday, with the standings (150-max and both 20-maxes) and the DK FINAL file, report:
- how copied the real field was;
- what the top 1% played;
- every set A–F, split first, with the cost.

Then say whether Thursday agrees with section 2's model. One slate is one slate: nothing goes on the build list from Thursday alone.

## 4. What I want back

- **Before Thursday:** a short note with section 1's answer if it's ready, the field model and its calibration from section 2, and the written-down sets for section 3.
- **After Thursday:** `NFL Shapes And Small Slates Report 11-27.md` (short, plain words, answers first), an appendix with every table, and ledger rows. Mark rows from the modeled slates as "modeled" and rows from Thursday as "one slate".

Every number gets its slates, how many were up and down, and split before counted. The cash rate goes in as the cost. Sharks are reference only.

End with:
- **A build list for me to approve,** or "nothing clears". For each item give:
  - what changes, and which step of the tool;
  - the numbers;
  - the cost;
  - the slates it hurts on;
  - its pass line, and when it gets decided;
  - whether it is for every main slate or small slates only.
- **A shadow list.**

**Review only: never build, edit or commit anything in the tool.**
