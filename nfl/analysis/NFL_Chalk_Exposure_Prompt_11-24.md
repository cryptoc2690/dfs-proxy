# NFL Chalk Exposure Prompt (11-24)

**Review only. Never build, edit, run for a live build or commit anything in the tool (`~/Desktop/dfs-proxy/nfl`). Every change goes on a build list for me to approve in the tool chat.**

## 0. Why this prompt

Most players I want at 60% or lower. But some slates have a player so well projected for his salary that the real choice is close to 0% or 100%, and the sharps sit near 100%.

**Last week (11-22):** Emmett Johnson (75% projected own) and Jonah Coleman (35%) were both in the winning lineup.
- **The top 1%:** Johnson in 92%, Coleman in 95%.
- **Us:** Johnson 65%, Coleman 56%.
- **YouTube:** everyone was talking about 80%+ on both.

Your ledger has a different conclusion: holding the most-owned player is a coin flip by slate (F13), and a floor at the field's share lost (G4). I think that conclusion is slightly wrong, or at least incomplete. Test it fresh, and don't let me or YouTube steer the answer. The question is when a player should be over 60%, up to 100%, and whether that can be known before lock.

**How the tool sets exposure today (main slate, Stage 3 build):**
- **The cap:** every player is held to 55% of the set.
- **Relaxed passes:** when the first selection pass can't fill the set inside the cap, the QB quotas and the stack quota, later passes let players go to 65%, then 70%, then no limit. That is how Johnson reached 65% on 11-22.
- **What the user can type:** a lower cap ("Name 20") or a minimum ("Name 40" lineups, the Minimum lineups box).
- **Nothing in the build decides on its own that a player should be at 80–100%.**

## 1. The players this is about

On all 11 main slates (9-13 to 11-22), list every player who before lock looked like a "must play":
- high projected ownership (try 30%+, 40%+, 50%+);
- **or** projected points per $1,000 far above his position on that slate (define it yourself, chosen leave-one-slate-out);
- **or** a role change (projected well over his season average).

For each one show:
- price, position, projected own, drafted own, projection and points;
- top 1% share (split), the field's share, our real share, the live build's share in the harness;
- whether he was in the winning lineup;
- the sharks' share, as reference only.

## 2. What happened when they hit and when they failed

1. How often did each kind of "must play" end up in 80%+ of the top 1%? How often under 30%?
2. Is there anything before lock that separates the ones winners held near 100% from the ones that failed? Look at:
   - value against the position;
   - projected own;
   - salary;
   - role;
   - game total and team total;
   - how many other "must plays" were on the slate.

   Give precision and recall against the base rate. If nothing separates them, say so plainly.
3. For the ones that hit, how much did being at 55–65% (not 90–100%) cost us in split top-1% lineups? For the ones that failed, how much did 55–65% save us over 90–100%?

## 3. Test exposure rules in the harness

Write the pass line first (the ledger's main-slate line). Graded split, held out, with the cost shown (mean and cash rate). The 150 and the 20s separately.

1. **Fixed exposure on the slate's top "must play":** 0%, 30%, 55% (today), 70%, 85%, 100%.
2. **A rule that fires only on the marks from section 2**, if any survive: hold him at 80–100%, and everyone else stays at 55%. Judge it only on the slates it fires on, against "always on" and against today's build.
3. **Two "must plays" on one slate** (11-22: Johnson and Coleman). Both at 80–100%, one at 100% and one at 55%, or neither.
4. **The cap itself:** today's 55% with relaxed passes, against 55% held strictly, 60%, 65% and 70%.
5. **Stacking:** if a floor at 80–100% passes, what does it cost the other players? The NBA Nov 16 lesson: a floor on one player is paid for by someone else.

Also answer directly: the analysis said the most-owned player is a coin flip by slate. Does that still hold when he is also the best value on the slate, or is that the case where 100% wins?

## 4. What I want back

- `NFL Chalk Exposure Report 11-24.md`: short, plain words, the answers first.
- An appendix with every table.
- Ledger rows updated (F13, G4, T9 and any new ones).

Every number gets its slates, how many were up and down, and split before counted. The cash rate goes in as the cost. Sharks are reference only. No "the projection said" as a reason.

End with:
- **A build list for me to approve,** or "nothing clears". For each item give:
  - what changes, and which step of the tool;
  - the numbers;
  - the cost;
  - the slates it hurts on;
  - its pass line, and when it gets decided.
- **A shadow list** for anything ahead but not proven.

**Review only: never build, edit or commit anything in the tool.**
