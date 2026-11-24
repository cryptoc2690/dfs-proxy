# NFL Small Slate Shapes Prompt (11-24)

**Review only. Never build, edit, run for a live build or commit anything in the tool (`~/Desktop/dfs-proxy/nfl`). Every change goes on a build list for me to approve in the tool chat.**

## 0. Why this prompt

Thursday (11-26) is a 3-game main slate: CHI @ DET (total 55.5), PHI @ DAL (47.5) and KC @ BUF (47.5). The 150-max will have close to 300,000 entries. On 3 games I expect most of the field to build the same lineups, so the way to win may be to be different, not to be right on the obvious build.

I want to know whether some of what we dropped, or never built, works better on a small slate:
- **Bigger stacks:** QB + 3 or QB + 4 catchers, or QB + his RB + 3 catchers (the old tool built QB+3 and allowed the QB's RB; the Stage 3 build allows neither).
- **More salary left** than our $400 limit: $1,000, $2,000, $3,000+.
- **Lineups not in Stokastic's file.**

This is a one-off: Thanksgiving, and maybe Christmas. I am **not** asking you to build the tool around 3-game slates. I want a look, and an honest grade after Thursday.

The tool tested Thursday's early files (tool chat, 11-24): the live build makes 150 and two 20s with no problems. All 6 QBs get lineups, about 3,000–3,900 lineups pass the ranges, and 4–5 chalk players end up at 57–65% (Emmett Johnson, CeeDee Lamb, Javonte Williams, Colston Loveland, Chiefs DST).

## 1. Before Thursday: what the data you have already says

There are no 3-game NFL slates in the store. Use what is closest, and say plainly what does and doesn't carry over:
1. **How copied is the field as the slate shrinks?** Main (10+ games: 88% single copies) against showdown (1 game: 6% single). Where would a 3-game slate sit? If Stokastic's Thursday lineups file is in hand (`DK_NFL_Thu_Pre_Contest_Lineups.csv`), how concentrated is it: lineups per player, the same core repeated, its top lineups?
2. **Big stacks on main, split:** QB+3 (lift 1.72 on 9 slates, F7), QB+4, and QB + RB + 2–3 catchers. Do they help more on the slates where the field was most concentrated (fewest single copies, highest expected own sum)?
3. **Salary left:** on showdown, more salary left meant fewer copies and better split finishes (F38: $2,000–4,000 best). On main, over $1,000 left lost (F11). Is there any sign that main slates with a concentrated field behave more like showdown?
4. **Lineups not in Stokastic's file:** the same question.

## 2. Write the Thursday shadow sets down before Thursday

So the grade is honest, define them now, before lock, and don't change them after. Each one is built from the inputs the tool saves Thursday (`logs/inputs/2026-11-26/...`), seed 0, the same typed pool, 150 lineups and two 20s:

| Set | What changes from the live build |
|---|---|
| A. Live build | nothing (it is the comparison) |
| B. Big stacks | QB+3 and QB+4 allowed (you pick the mix); QB's RB allowed |
| C. QB + RB + 3 | every lineup QB + his RB + 3 catchers |
| D. Salary left | the $400 limit replaced by $1,000–3,000 left (you pick the band) |
| E. Off the file | only lineups not in Stokastic's Thursday lineups file |
| F. Your best combination | of B–E, chosen from section 1, written down now |

Send me (the tool chat) the exact definitions before Thursday's lock if you want the tool to save these sets live. Otherwise rebuild them afterwards from the saved inputs.

## 3. After Thursday: grade them

With Thursday's standings (150-max and both 20-maxes) and the DK FINAL file:
1. **The contest:** entries, first, the top-1% line, how copied the field was (single-copy share, copies of the winning lineup, copies of the top 1%), what the top 1% played (stack sizes, QB's RB, salary left, own sum, the fewest-owned player).
2. **Every set from section 2, split first:**
   - top 1%, top 0.1%, top 100, best rank;
   - copies per lineup;
   - mean against the field and cash rate (as the cost).
3. **Was it winnable by being different?** Where did the lineups that were different finish, against the lineups everyone had?

One slate is one slate. Report it as a look, not a result. Nothing goes on a build list from one slate. Say what Christmas (if it is a small slate) would need to confirm it.

## 4. What I want back

- **Before Thursday:** a short note with section 1 and the written-down definitions for section 2.
- **After Thursday:** `NFL Thursday Small Slate Look 11-27.md` with sections 3 and 4, plus ledger rows marked "one slate".

Every number gets split before counted. The cash rate goes in as the cost. Sharks are reference only.

**Review only: never build, edit or commit anything in the tool.**
