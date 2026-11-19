# NFL: Start From The Contests, Not From Us (11-19)

**Review only. Never build, edit, run for a live build or commit anything in the tool (`~/Desktop/dfs-proxy/nfl`). Every change goes on a build list for me to approve in the tool chat.**

## 0. Why, and how to read this prompt

Every projection review says the same thing: a player reaching his projection is about a coin flip. So roughly half of what decides a slate sits outside the projection, and that half is what this tool should be handling. Right now it isn't: it ranks on the projection and fills the rest with whatever fits.

**Set aside everything you already know. That includes:**
- our earlier reports;
- the rules ledger's conclusions;
- the NBA rules;
- our vocabulary (chalk, live piece, leverage, bring-back, value, punt, floor, ceiling);
- my theories.

**Look at the contests as if for the first time. Come up with your own definitions of what a winning lineup is and how it differs from a losing one.**
- **Our words may be wrong, and so may I.** I am not the one looking at the data, so anything I say is a guess. Do not let our framing decide which variables you build or which questions you ask.
- **Use our earlier work only at the end,** as a comparison: where your answers agree with it and where they don't.

Two things don't change:
- **Grading.** Split finishes: a finish on a lineup entered k times counts 1/k.
- **Before lock vs after.** Only information available before lock can become a rule.

## 1. Phase 1: discovery, with no theory going in

Data: 9 main slates and 27 showdown games. That's every contest's standings, Stokastic's projections and lineups files, LineStar, the DK files, and our build log.

1. **Describe what separates the top of each contest from the field, in your own terms.**
   - **Levels:** players, pairs and groups of players, and whole lineups.
   - **Features:** build whatever you think matters. Examples: how points are spread across a lineup, the lowest scorer, price against outcome, teams and games, positions, ownership, correlation, how many copies, and anything else you see.
   - **Rule:** don't start from our categories.
2. **Separate what is knowable before lock from what is only visible after.**
   - For every pattern from (1), find the before-lock information that comes closest to predicting it. That can be any column in any pre-lock file, or anything built from them.
   - Measure how much each one adds beyond the projection. Compare players of the same projection, and lineups of the same projected total.
   - **The question:** at the same projection, what tells you who ends up in the winning lineups?
3. **Define "a player who wins a slate" and "a lineup that wins a slate" yourself,** in whatever form the data supports, and show the definitions working on data they weren't fitted on.

## 2. Phase 2: turn what you found into ways to build

1. **From Phase 1, design the build or builds you would use to win main and showdown contests,** split finishes first. That can be a whole new design, not a tweak to ours.
2. **Test them in your harness,** against:
   - the live tool as it runs now;
   - the 11-17 candidate spec;
   - a plain baseline (Stokastic's file by projected points);
   - a random legal lineup set.

   Show the cost of each (mean lineup and cash rate), and whether its gains come from a few slates or many.

## 3. Phase 3: only now, our ideas, as claims to confirm or reject

These are guesses. Grade each one as true, false or can't tell. Don't use them to steer Phases 1–2.

- **Every showdown player needs a real shot at 10+.** The tool keeps fitting low-salary pieces (NFL and NBA) that have no path there.
  - **The example:** 11-16 LAC @ BAL, total 39.5. My lineup was five of the winner's six, same captain. Mine had the Chargers DST ($3,400, 7.0 points), the winner had Mark Andrews ($6,400, 16.2).
  - **My note:** the Chargers DST had reached 10+ once all season. Andrews had done it three times and had a new QB who throws.
- **A defense in a low-total game, or a bad team's defense.** Today the showdown tool leaves kickers and defenses uncapped when both QBs project at 37 or under, and holds them to 25% at 40+. Those thresholds came from four games and were never tested.
- **A player's own season hit rate** (how often he reached a big score) and **role changes** (a new QB, a teammate out) tell you something the projection doesn't.
- **Our earlier conclusions:** the 11-17 report, the rules ledger, Stage 1–3. Which ones does your fresh look support, and which does it contradict?

## 4. Guard rails

With 9 main slates and 27 showdowns it is easy to find patterns that aren't there.
- **Hold data out.** Fit any definition, threshold or band on part of the data and judge it on the rest: leave one slate out, or train on the earlier weeks and test on the later ones. Say which.
- **Say how many things you tried,** not just the ones that worked. Compare each finding to what chance would give: shuffle within the slate, or permute the labels.
- **Write a pass line before each test,** and keep it.
- **The slate (or game) is the unit.** Report how many were up and down, and give intervals over slates.
- **No "the projection said" as a reason.** Sharks are a reference only.
- **If the data says nothing can be seen before lock, say so plainly,** and say what that means for how the tool should build.

## 5. What I want back

- `NFL Fresh Look Report 11-19.md`:
  - short, plain words, answers first;
  - your definitions;
  - what is knowable before lock;
  - the builds you would use;
  - the grades on our claims.
- `NFL Fresh Look Appendix 11-19.md`: every table, and the list of everything tried.
- New and updated rows in `NFL Rules Ledger.md`.

End with:
- **A build list for me to approve.** For each item give:
  - what changes, and which step of the tool;
  - the numbers;
  - the cost;
  - the slates it hurts on;
  - its pass line, and when it gets decided;
  - whether it touches showdown.
- **A shadow list:** what to log from now on so each idea grades itself live.

**Review only: never build, edit or commit anything in the tool.**
