"""Pure-Python GPP engine — no third-party dependencies.

Replaces the pulp ILP + numpy simulation with a randomized-construction
lineup builder and a hand-rolled Monte-Carlo simulation, so the whole app
runs on a stock Python install with nothing to pip-install. This is the
standard GPP approach anyway: you want a diverse pool of strong, stacked
lineups, then rank them on simulated ceiling — not the single LP optimum.
"""

from __future__ import annotations

import math
import random

from dk import MIN_FORWARDS, MIN_GUARDS, ROSTER_SIZE, SALARY_CAP, Player

MIN_SALARY = 3000  # DK WNBA min; used so partial lineups stay completable

# Salary a lineup may leave unspent. Restored after being deleted in the strip,
# on far better evidence than removed it: 71,936 real rosters across 24 contests,
# with exact salaries from the pre-lock files.
#
#   leftover        share of field   top-1% rate   cash    $/entry
#   $0                        32%         1.42%   22.8%       3.87
#   $100-$300                 44%         1.23%   23.5%       4.53
#   $400-$700                 17%         1.39%   22.2%       5.65
#   $800-$1,000                4%         0.41%   19.2%       2.21
#   $1,600-$2,000            0.7%         0.19%   18.8%       3.80
#   over $2,000              0.7%         0.00%   15.1%       1.30
#
# Leaving up to $700 costs nothing — the $400-$700 band has the best dollars per
# entry on the board. The cliff is at $800, and past $2,000 the top-1% rate is
# literally zero across 71,936 rosters. Of 48 winners, one left more than $1,000
# and none left more than $2,000; their mean leftover was $306.
#
# The earlier hold-out run that justified deleting this measured it at 700 as a
# candidate-diversity cost and could not see the cliff, because at 12 entries on
# 23 slates almost nothing lands past $800 anyway. The field data can see it.
# Set high enough to bind only past the cliff, so it is a junk filter and not a
# diversity tax.
MAX_LEFTOVER = 800

# DraftKings Classic requires players from at least two different games, so a
# roster can never be more than ROSTER_SIZE-1 from one game. This is a contest
# rule, not a preference — an all-one-game lineup is rejected at upload.
MAX_PER_GAME = ROSTER_SIZE - 1

# --- what survived a leave-one-slate-out test on 23 real contests ---------
#
# An earlier 24-contest study produced a shelf of construction rules and all of
# them were built in as hard constraints. Every one had been fitted AND scored on
# the same slates. Held out slate by slate they did not pay: the sub-10%
# ownership cap, the stud requirement, the ownership floor, the team-share cap,
# the salary floor, stack seeding and the exposure/overlap caps each tested as no
# effect or worse, and together they were costing 23 lineups in the money per 23
# contests (67 -> 90 cashes at 12 entries, 18 slates better and 4 worse;
# 110 -> 144 at 20 entries).
#
# The damage was a funnel, not any single rule: 120 candidates built -> ~55
# through the salary floor -> ~43 through the ownership floor -> 12 chosen. Each
# filter looked harmless alone and the losses compounded. Measured directly
# against the real field, the stack of rules left 42% of actual top-1% lineups
# and a third of actual winners UNBUILDABLE — on four-game slates only 13% of the
# top-1% tier was reachable at all, because an absolute 10% ownership threshold
# does not travel to a board where ownership spreads across 60+ players.
#
# So they are gone. What remains is DK's ruleset, the handicapper's pool and
# cores, and the two-game shape rules below.

# Reporting thresholds only — these no longer constrain anything. They describe a
# lineup's shape in the build log so the next review can still ask the questions
# the last one asked.
SUB10_OWN = 10.0
STUD_SALARY = 10_000

# Two-game slates are the one place a shape rule survived, and only INSIDE the
# stripped build: with them on, 90 cashes held out against 86 with them off. They
# are cheap and specific, so they stay:
#   * a 3+ block from one team with NO player from its opponent went 2-for-3,376
#     on top-1% finishes, against 52 expected at the two-game field rate of 1.55%
#     (the figure recorded here was once "about 34", which used the all-slate
#     rate of 1.0% and understated the rule)
#   * putting the majority in the game with the higher projected-ownership sum
#     paid in 7 of 7 slates on cash: 30.0% vs 12.3%
#
# The bring-back clause was re-examined across 68,376 real rosters on 23 slates,
# 28,242 of them carrying a 3+ block, and three things came back:
#
#   IT IS A TWO-GAME FACT. On slates of three games or more a bring-backless
#   block BEATS the field — 1.90% top-1% against 1.28%, and against 1.17% for
#   blocks that do carry a bring-back. The clause already sits inside the
#   two-game branch, which is the only reason that is not a live bug.
#
#   CONCENTRATION IS NOT THE ESCAPE HATCH. The hypothesis was that stacking a
#   team whose scoring sits in its top three makes the bring-back redundant.
#   Pooled it looks that way, but pooled is a slate-size effect: inside two-game
#   slates the highest-concentration blocks went 0 of 1,066 on top-1%, and at or
#   above the concentration that prompted the question, 0 of 2,191.
#
#   IT IS NOW THE ONLY THING ENFORCING IT. With max_per_team at 3, a
#   bring-backless 3-block on a two-game slate IS a 3-3 roster, so while 3-3 was
#   banned this clause was unreachable dead code. Since the 3-3 ban was removed
#   (see below) it is live: deleting it admits 1,016 of 4,000 candidates on a
#   real two-game board. Removing both at once would have been a bad trade.
#
# `rules=None` used to mean "work them out", which made both the UI's off switch
# and the last rung of the relaxation ladder silent no-ops: each passed None and
# got the rules handed straight back. RULES_OFF is a sentinel that means off.
RULES_OFF = {"two_game": False, "major_game": None}

# The 3-3 ban is GONE. It is an ordinary shape now — neither blocked nor
# preferred, just one the simulator is allowed to rank on its merits.
#
# The cash numbers above are about which shape wins MORE OFTEN. They were
# enforced as though they were about which shape can win at all, and the
# difference showed up the hard way: on CHI@ATL / SEA@GSV the ban put 3-3 at 0 of
# 150, a 3-3 built by hand off lineup #1 took second, and left alone on that
# board a 3-3 is 40.1% of every legal candidate and 36.7% of the top sixty by
# simulated score. The rule was not trimming a fringe shape, it was deleting the
# most common one on the slate — and a construction that cashes 17% against 23%
# still wins often enough that its absence is a hole in the set, not a saving.
#
# Nothing replaced it. A quota tilting the set back toward 4-2 was written and
# then thrown away: the whole complaint was that the shape was being decided by a
# rule instead of by the board, and a share is the same mistake with a softer
# edge. The scorer already sees game totals, ownership and ceilings.
#
# Checked afterwards against 68,376 real rosters, and the ban was over-broad in
# exactly the way removing it assumed. On two-game slates the top-1% rate for a
# 3-3 roster carrying NO 3-block is about 1.4% (55 of roughly 3,900) against a
# two-game field rate of 1.55% — an ordinary shape, which is what it is now
# treated as. The whole of the old penalty was the subset that also held a
# bring-backless 3-block, at 2 of 2,409. That subset is still refused, by the
# bring-back clause above. The two rules were doing one job badly between them;
# they now do two jobs, separately.


class Lineup:
    def __init__(self, players: list[Player]):
        self.players = players
        self.metrics: dict[str, float] = {}
        self.alt: "Lineup | None" = None  # pool-legal alternative (P2), if any

    @property
    def salary(self) -> int:
        return sum(p.salary for p in self.players)

    @property
    def proj(self) -> float:
        return round(sum(p.proj for p in self.players), 1)

    @property
    def total_own(self) -> float:
        return round(sum(p.ownership for p in self.players), 1)

    def ids(self) -> list[str]:
        return [p.dk_id for p in self.players]

    def games(self) -> set[str]:
        return {p.game for p in self.players}

    def dk_slots(self) -> list[Player]:
        """DK upload order: G, G, F, F, F, UTIL — matching the DK entries file."""
        g = sorted((p for p in self.players if p.is_guard), key=lambda p: -p.proj)
        f = sorted((p for p in self.players if not p.is_guard), key=lambda p: -p.proj)
        slotted = g[:MIN_GUARDS] + f[:MIN_FORWARDS]          # 2 G, 3 F
        rest = sorted(g[MIN_GUARDS:] + f[MIN_FORWARDS:], key=lambda p: -p.proj)
        return slotted + rest[:ROSTER_SIZE - MIN_FORWARDS - MIN_GUARDS]  # + UTIL


# ---------------- lineup construction ----------------
def _weighted_pick(cands, rng):
    # Weight by projection; reliability is handled structurally by the risk-body
    # cap (see _build_one), not by nudging weights here — a weight nudge always
    # loses to a real projection gap, so keep this clean.
    weights = [max(p.proj, 0.1) ** 3 * (0.6 + 0.8 * rng.random()) for p in cands]
    total = sum(weights)
    x = rng.random() * total
    for p, w in zip(cands, weights):
        x -= w
        if x <= 0:
            return p
    return cands[-1]


# Stack seeding used to live here: a share of lineups was built AROUND a 3-man
# high-implied team block or a 5-man game block, on the strength of top-1% rates
# measured in-sample (3-stack of a >=84-total team 2.65%, 5-man game stack 7.03%).
# The cells shrank every time the sample grew — the game stack went 7.03% -> 2.76%
# on 4x the data — and held out across 23 slates, seeding at 50% cost 6 cashes
# against seeding at 0. It is gone. The field agrees with the deletion for a
# reason the old note missed: real rosters with 5 from one game have LOWER
# residual variance (22.9) than spread 2-2-2 rosters (24.5), so a game stack was
# never buying the upside it was credited with.
def _slate_rules(pool):
    """The slate-shape facts the two-game rules key off, worked out once.

    major_game is the game with the higher summed projected ownership — on all
    seven two-game slates in the study that was also the game the field ended up
    more heavily on, and the side the winners loaded."""
    games = {}
    for p in pool:
        if p.game:
            games[p.game] = games.get(p.game, 0.0) + p.ownership
    two_game = len(games) == 2
    return {
        "two_game": two_game,
        "major_game": max(games, key=lambda g: games[g]) if two_game else None,
    }


def _rules_ok(picked, rules):
    """Final check against the two-game shape rules. Runs on a complete roster,
    so it can see the whole shape."""
    if rules["two_game"]:
        games = {}
        for p in picked:
            games[p.game] = games.get(p.game, 0) + 1
        big_game, big_ct = max(games.items(), key=lambda kv: kv[1])
        # 3-3 is legal, and nothing downstream trims it either — see the note by
        # RULES_OFF. It is an ordinary shape competing on simulated score.
        #
        # The major-game test only applies when there IS a majority. On a 3-3 the
        # max() above picks a "biggest" game arbitrarily between two equal counts,
        # so running the test there would reject or accept the same shape
        # depending on dictionary order.
        if big_ct >= 4 and rules["major_game"] and big_game != rules["major_game"]:
            return False
        teams = {}
        for p in picked:
            teams[p.team] = teams.get(p.team, 0) + 1
        for t, ct in teams.items():             # 3+ from a team needs a bring-back
            if ct >= 3:
                opp = next((q.opponent for q in picked if q.team == t), "")
                if not any(q.team == opp for q in picked):
                    return False
    return True


def _build_one(pool, max_per_team, rng, cores=None, min_cores=0, reserve=MIN_SALARY,
               max_off_pool=None, rules=None):
    # Seed the lineup with the required number of cores, then fill the rest with
    # a position-aware greedy that always keeps the G/F minimums reachable.
    # (Extra cores can still land in the fill — min_cores is a floor.)
    picked = []
    if cores and min_cores > 0:
        avail = [c for c in cores if c.proj > 0]
        k = min(min_cores, len(avail))
        if k > 0:
            picked = list(rng.sample(avail, k))
    used = {p.dk_id for p in picked}
    salary = sum(p.salary for p in picked)
    team_count, game_count = {}, {}
    for p in picked:
        team_count[p.team] = team_count.get(p.team, 0) + 1
        game_count[p.game] = game_count.get(p.game, 0) + 1
    if salary > SALARY_CAP or any(v > max_per_team for v in team_count.values()):
        return None
    if any(v > MAX_PER_GAME for v in game_count.values()):
        return None
    if max_off_pool is not None and sum(1 for p in picked if not p.in_pool) > max_off_pool:
        return None

    while len(picked) < ROSTER_SIZE:
        remaining = ROSTER_SIZE - len(picked)
        g = sum(1 for p in picked if p.is_guard)
        f = len(picked) - g
        need_g, need_f = max(0, MIN_GUARDS - g), max(0, MIN_FORWARDS - f)
        must_guard = need_g >= remaining
        must_forward = need_f >= remaining
        budget = SALARY_CAP - salary - reserve * (remaining - 1)
        off_pool_used = (sum(1 for p in picked if not p.in_pool)
                         if max_off_pool is not None else 0)
        elig = []
        for p in pool:
            if p.dk_id in used or p.salary > budget:
                continue
            if team_count.get(p.team, 0) >= max_per_team:
                continue
            if game_count.get(p.game, 0) >= MAX_PER_GAME:
                continue
            if max_off_pool is not None and not p.in_pool and off_pool_used >= max_off_pool:
                continue
            if (must_guard and not p.is_guard) or (must_forward and p.is_guard):
                continue
            ng = need_g - (1 if p.is_guard and need_g else 0)
            nf = need_f - (1 if not p.is_guard and need_f else 0)
            if max(0, ng) + max(0, nf) > remaining - 1:
                continue
            elig.append(p)
        if not elig:
            return None
        p = _weighted_pick(elig, rng)
        picked.append(p)
        used.add(p.dk_id)
        salary += p.salary
        team_count[p.team] = team_count.get(p.team, 0) + 1
        game_count[p.game] = game_count.get(p.game, 0) + 1

    g = sum(1 for p in picked if p.is_guard)
    if g < MIN_GUARDS or (len(picked) - g) < MIN_FORWARDS or salary > SALARY_CAP:
        return None
    if len({p.game for p in picked}) < 2:   # DK contest rule, never relaxed
        return None
    if rules and not _rules_ok(picked, rules):
        return None
    return picked


def build_candidates(pool, count, *, max_per_team, seed=0, cores=None,
                     min_cores=0, reserve=MIN_SALARY, max_off_pool=None,
                     rules=None):
    """Build up to `count` distinct legal rosters.

    `rules` is the two-game shape dict, or RULES_OFF to switch it off. Passing
    None means "work them out from the pool" — which is why callers that mean OFF
    must pass RULES_OFF and never None. That confusion is what made the UI switch
    and the last rung of the relaxation ladder silent no-ops.

    The old `stack` argument (minimum players from one game) is gone: with six
    roster slots and at most five games on any WNBA slate, some game always holds
    two, so it could never bind.
    """
    rng = random.Random(seed)
    if rules is None:
        rules = _slate_rules(pool)
    out, seen = [], set()
    tries = 0
    while len(out) < count and tries < count * 15:
        tries += 1
        lu = _build_one(pool, max_per_team, rng, cores, min_cores, reserve,
                        max_off_pool, rules)
        if not lu:
            continue
        key = frozenset(p.dk_id for p in lu)
        if key in seen:
            continue
        seen.add(key)
        out.append(Lineup(lu))
    return out


# ---------------- simulation ----------------
# The PERT / gamma draw that used to live here is gone with the game multiplier:
# it sampled between LineStar's floor and ceiling, a band about a third as wide
# as the real residual spread. See simulate_and_score.
# How much of the ranking is upside vs production. Half simulated mean, half
# simulated 85th percentile. Under the OLD simulator this weight was inert —
# 0 and 1 produced byte-identical lineup sets on all 23 slates, because a PERT
# draw scaled by a shared per-game multiplier makes the 85th percentile very
# nearly a monotone transform of the mean. With independent, correctly-sized
# noise the percentile carries real information about a roster's shape, and the
# configuration that held out best (stripped build, honest simulator, 90 cashes
# against 67, 19 slates better and 4 worse) ran this at 0.5. It stays at 0.5.
CEILING_WEIGHT = 0.5

# Residual spread per player, measured on 23 slates of real results: actual minus
# blended projection has an SD of 9.0-11.0 points for rostered rotation players,
# which is 2.8-3.8x the band LineStar's floor/ceiling implies. The old PERT draw
# between floor and ceiling was modelling about a third of the real spread — at
# lineup level the sim produced SD 14.4 against a real 20.5, too narrow on 21 of
# 23 slates — so a "ceiling" was never a ceiling.
SIM_SD_FLOOR = 6.0
SIM_SD_SHARE = 0.40


def simulate_and_score(cands, pool, *, sims, own_lean=0.0, seed=0):
    """Score every candidate on an independent-noise Monte Carlo.

    There is deliberately NO game or team correlation factor. The old simulator
    multiplied every player in a game by a shared draw (sigma 0.10), which forces
    a same-game correlation of roughly +0.35. Measured on the real results the
    correlation of residuals is: teammates -0.010, opponents -0.006, players in
    different games +0.019 — nothing, on 2,179 / 2,500 / 9,436 pairs. Realised
    game totals over projected totals have SD 0.085, exactly what independent
    players produce, so there is no game-level factor left to model.

    That wrong sign had a cost: the sim handed extra variance to game stacks, so
    the 85th percentile it ranked on was a stack-shaped artefact. In the real
    field, rosters with 5 players from one game have LOWER residual SD (22.9)
    than spread 2-2-2 rosters (24.5) — the opposite of what was being rewarded.
    """
    rng = random.Random(seed + 7)
    id_row, mat = {}, []
    for row, p in enumerate(pool):
        sd = max(SIM_SD_FLOOR, SIM_SD_SHARE * p.proj)
        mat.append([max(0.0, p.proj + sd * rng.gauss(0, 1)) for _ in range(sims)])
        id_row[p.dk_id] = row
    for c in cands:
        rows = [id_row[i] for i in c.ids() if i in id_row]
        totals = [sum(mat[r][s] for r in rows) for s in range(sims)]
        totals.sort()
        c.metrics = {
            "mean": sum(totals) / sims,
            "ceiling": totals[int(sims * 0.85)],
            "p95": totals[int(sims * 0.95)],
        }
    # Ownership lean on RAW lineup ownership. POSITIVE leans toward the field's
    # consensus, negative fades it. It now defaults to ZERO, and that was the
    # single biggest finding of the 23-slate held-out test: at +0.35 the tool sat
    # at the 75th within-slate ownership percentile while the top-1% tier sits at
    # the 63rd. Setting it to 0 was picked on all 23 held-out folds on both
    # objectives (cash 73 against 67; +$580 on the seed-median dollar view) and
    # it cut identical-twin rosters from 44% to 41%.
    #
    # Fading is worse than leaning, not better: -0.15 costs 11 cashes and -0.35
    # costs 14, and neither buys first places. The slider survives only because
    # its SIGN was worth measuring; neutral is the answer.
    owns = [c.total_own for c in cands] or [0]
    lo, hi = min(owns), max(owns)
    span = (hi - lo) or 1.0
    for c in cands:
        on = (c.total_own - lo) / span
        base = ((1 - CEILING_WEIGHT) * c.metrics["mean"]
                + CEILING_WEIGHT * c.metrics["ceiling"])
        c.metrics["base"] = round(base, 2)
        c.metrics["score"] = base * (1 + own_lean * (2 * on - 1))
    cands.sort(key=lambda c: -c.metrics["score"])


def select_final(cands, n, player_caps=None, core_floors=None, backfill=None,
                 report=None):
    """Pick the best N distinct rosters, score-first.

    What used to be here: a global per-player exposure cap, a pairwise-overlap
    cap and a pool-level team-slot cap, each with its own relaxation pass and
    then a "fill to N" pass underneath them all. Instrumented across 69 builds
    at 12 entries, the exposure pass rejected 1,911 candidates and the fill-to-N
    pass then added 150 lineups that broke the very cap it had just enforced —
    about 2 in every 12 — so realised peak exposure averaged 76% against a 60%
    setting, and nothing anywhere said so. The team cap fired 187 times and was
    relaxed away in 28 of 69 builds. The overlap cap rejected 1.4 candidates per
    build and 2.5% of the final pairs shared five players regardless.

    Held out over 23 slates none of the three changed outcomes, so they are gone
    rather than fixed. The diversity survives without them: mean pairwise overlap
    is 2.62 of 6 against 2.65 before, because a projection-weighted random
    builder already spreads. What is left is a genuine distinct-roster guarantee,
    the per-player caps the USER sets by hand (their instruction, not a data
    preference), and the core floors.
    """
    player_caps = player_caps or {}
    counts, final, seen = {}, [], set()

    def add(c):
        final.append(c)
        seen.add(frozenset(c.ids()))
        for p in c.players:
            counts[p.dk_id] = counts.get(p.dk_id, 0) + 1

    def capped(c):
        return any(counts.get(i, 0) >= player_caps[i]
                   for i in c.ids() if i in player_caps)

    for c in cands:  # score-sorted; honour the user's own caps, dedupe rosters
        if len(final) >= n:
            break
        if frozenset(c.ids()) in seen or capped(c):
            continue
        add(c)
    if len(final) < n:
        # The caps cannot all be met off this board. This pass used to abandon
        # them outright and fill to N from anywhere, silently — the same bug the
        # note above describes, still live for the one kind of cap that is not a
        # data preference but a thing you typed. So relax by one lineup at a
        # time, stop the moment N is reached, and say what it cost.
        slack = 1
        while len(final) < n and slack <= n:
            for c in cands:
                if len(final) >= n:
                    break
                if frozenset(c.ids()) in seen:
                    continue
                if any(counts.get(i, 0) >= player_caps[i] + slack
                       for i in c.ids() if i in player_caps):
                    continue
                add(c)
            slack += 1
        if len(final) < n:                     # still short: distinct rosters win
            for c in cands:
                if len(final) >= n:
                    break
                if frozenset(c.ids()) not in seen:
                    add(c)
    final = final[:n]
    if report is not None and player_caps:
        over = {i: counts.get(i, 0) for i in player_caps
                if counts.get(i, 0) > player_caps[i]}
        if over:
            name = {p.dk_id: p.name for c in final for p in c.players}
            report.setdefault("relaxed", []).append(
                "the board could not fill the set under your caps, so these ran "
                "over: " + ", ".join(
                    f"{name.get(i, i)} {v} of {n} against {player_caps[i]}"
                    for i, v in sorted(over.items(), key=lambda kv: -kv[1])))
    if core_floors:
        # Guarantee each core its minimum presence. This draws from `backfill` —
        # every candidate we built, not the filtered shortlist — because a core is
        # the user's own conviction and must not be squeezed out by a preference
        # upstream. That is precisely the bug that once buried a cored player at
        # 1-of-N. Instrumented at 51 of 51 builds honoured, 0.16 swaps per build.
        final = _enforce_core_floors(final, backfill or cands, core_floors)
    final.sort(key=lambda c: -c.metrics.get("score", 0))
    return final


def _enforce_core_floors(final, cands, core_floors):
    """Top up under-exposed cores to their floor. For each core below its target,
    pull the best-scoring candidate that features it (cands is score-sorted) and
    drop the weakest chosen lineup that lacks it — but never one whose removal
    would knock another core back under its own floor. Best-effort: stops when no
    swap is available rather than looping forever."""
    final = list(final)

    def count(core_id):
        return sum(1 for lu in final if core_id in lu.ids())

    for core_id, need in core_floors.items():
        while count(core_id) < need:
            cand = next((c for c in cands
                         if core_id in c.ids() and c not in final), None)
            if cand is None:
                break
            drop = None
            for lu in reversed(final):  # weakest-last -> reversed hits it first
                if core_id in lu.ids():
                    continue
                safe = True
                for oid, oneed in core_floors.items():
                    if oid != core_id and oid in lu.ids() and count(oid) - 1 < oneed:
                        safe = False
                        break
                if safe:
                    drop = lu
                    break
            if drop is None:
                break
            final.remove(drop)
            final.append(cand)
    return final


# ---------------- pool-legal alternative (P2) ----------------
def _complete_in_pool(kept, pool, max_per_team):
    """Hold the in-pool players of an off-pool lineup and re-fill the vacated
    slots using ONLY in-pool players — a minimal-change, pool-legal version of
    the same lineup. Greedy by projection, keeping the roster legal and under
    cap. Returns 6 players or None if no legal all-in-pool repair exists."""
    picked = list(kept)
    used = {p.dk_id for p in picked}
    inpool = sorted((p for p in pool if p.in_pool and p.dk_id not in used),
                    key=lambda p: -p.proj)
    while len(picked) < ROSTER_SIZE:
        remaining = ROSTER_SIZE - len(picked)
        g = sum(1 for p in picked if p.is_guard)
        f = len(picked) - g
        need_g, need_f = max(0, MIN_GUARDS - g), max(0, MIN_FORWARDS - f)
        must_guard = need_g >= remaining
        must_forward = need_f >= remaining
        salary = sum(p.salary for p in picked)
        budget = SALARY_CAP - salary - MIN_SALARY * (remaining - 1)
        team_count = {}
        for p in picked:
            team_count[p.team] = team_count.get(p.team, 0) + 1
        pick = None
        for p in inpool:
            if p.dk_id in used or p.salary > budget:
                continue
            if team_count.get(p.team, 0) >= max_per_team:
                continue
            if (must_guard and not p.is_guard) or (must_forward and p.is_guard):
                continue
            ng = need_g - (1 if p.is_guard and need_g else 0)
            nf = need_f - (1 if not p.is_guard and need_f else 0)
            if max(0, ng) + max(0, nf) > remaining - 1:
                continue
            pick = p
            break
        if pick is None:
            return None
        picked.append(pick)
        used.add(pick.dk_id)
    g = sum(1 for p in picked if p.is_guard)
    if g < MIN_GUARDS or (len(picked) - g) < MIN_FORWARDS or sum(p.salary for p in picked) > SALARY_CAP:
        return None
    return picked


def _attach_pool_alternatives(lineups, pool, max_per_team, n_sims, own_lean, seed):
    """For every final lineup that spent an off-pool slot, attach the best
    pool-legal alternative (simulated on the same footing so its ceiling is
    comparable) for the UI to reveal/swap in."""
    alts = []
    for lu in lineups:
        if all(p.in_pool for p in lu.players):
            continue
        repaired = _complete_in_pool([p for p in lu.players if p.in_pool],
                                     pool, max_per_team)
        if repaired:
            lu.alt = Lineup(repaired)
            alts.append(lu.alt)
    if alts:
        simulate_and_score(alts, pool, sims=n_sims, own_lean=own_lean, seed=seed)


# ---------------- public API ----------------
def build_gpp(players, *, n=20, pool_size=None, max_per_team=3, own_lean=0.0,
              n_sims=5000, seed=0, cores=None, min_cores=0, max_off_pool=None,
              stars_and_scrubs=None, player_caps=None, slate_rules=True,
              max_leftover=MAX_LEFTOVER, report=None):
    """Build and rank n lineups.

    `report`, if given, is filled in with what actually happened — how many
    candidates were asked for and built, which constraints had to be relaxed, and
    how many lineups came back. Nothing in here may narrow the build silently:
    a thin slate used to return 3 lineups for a requested 12 with no error and no
    note anywhere in the UI.
    """
    rep = report if report is not None else {}
    rep.setdefault("relaxed", [])

    # Minutes gate (not a grade). Back-testing showed minutes and stat-stuffer
    # have ZERO correlation with bust rate, so this only ever flagged genuine
    # non-rotation risk. It is now OFF by default (GATE_MINUTES = 0 in app.py):
    # held out, gating cost cashes on 0 slates and gained them on 10, and on one
    # slate it removed a player who was in that night's winning lineup because
    # the daily file said she would play zero minutes. The mechanism stays so the
    # threshold can be raised deliberately; nothing is gated at 0.
    full = [p for p in players if p.proj > 0]
    gated = [p for p in full if not (p.risk and not p.core)]
    pool = gated if _can_field(gated) else full
    if len(pool) < ROSTER_SIZE:
        rep["returned"] = 0
        return []
    cores = [c for c in (cores or []) if c.proj > 0]
    pool_size = pool_size or max(120, n * 8)
    rep["requested"] = pool_size

    # Salary-aware construction: stars-and-scrubs is only right when a CHEAP
    # player actually projects. If cheap value exists, reserve less per slot so
    # the build can pay up + use it; if not, reserve more so it spreads into
    # mid-range instead of punting two slots into 9-point dead weight. The caller
    # passes the slate read so the engine's reserve and the UI's badge are the
    # SAME determination; None -> decide it here (direct/standalone calls).
    if stars_and_scrubs is None:
        cheap_best = max((p.proj for p in pool if p.salary <= 5500), default=0.0)
        stars_and_scrubs = cheap_best >= 16
    reserve = 4200 if stars_and_scrubs else 6000

    kw = dict(max_per_team=max_per_team, seed=seed, cores=cores,
              min_cores=min_cores, reserve=reserve, max_off_pool=max_off_pool)
    rules = _slate_rules(pool) if slate_rules else RULES_OFF
    cands = build_candidates(pool, pool_size, rules=rules, **kw)
    if len(cands) < n and rules["two_game"]:
        # A two-game board can be thin enough that the shape rules starve it.
        # Drop them rather than return fewer lineups than asked for — and SAY so,
        # which the old ladder could not do because its "off" step passed None
        # and got the rules straight back.
        more = build_candidates(pool, pool_size, rules=RULES_OFF, **kw)
        if len(more) > len(cands):
            cands = more
            rep["relaxed"].append("two-game shape rules dropped — too few legal "
                                  "lineups on this board with them on")
    if not cands and max_off_pool is not None:
        # Pool too thin to field legal lineups at this cap — loosen it one at a
        # time (0 -> 1 -> ... -> unconstrained) rather than return nothing.
        for relaxed in range(max_off_pool + 1, ROSTER_SIZE + 1):
            kw2 = dict(kw, max_off_pool=(None if relaxed >= ROSTER_SIZE else relaxed))
            cands = build_candidates(pool, pool_size, rules=RULES_OFF, **kw2)
            if cands:
                rep["relaxed"].append(
                    f"pool limit raised from {max_off_pool} to "
                    f"{'unlimited' if relaxed >= ROSTER_SIZE else relaxed} off-pool "
                    f"players — the pool alone could not field a legal lineup")
                break
    rep["built"] = len(cands)
    if not cands:
        rep["returned"] = 0
        return []

    # Salary floor. Only bites past the cliff the field data shows at $800, and
    # only when enough lineups survive to still fill the set — a thin board that
    # cannot spend is a fact about the slate, not a lineup to throw away.
    if max_leftover is not None:
        floor = SALARY_CAP - max_leftover
        spent = [c for c in cands if c.salary >= floor]
        if len(spent) >= n:
            cands = spent
        elif spent:
            rep["relaxed"].append(
                f"salary floor relaxed — only {len(spent)} of {len(cands)} "
                f"candidates spent within ${max_leftover} of the cap")

    simulate_and_score(cands, pool, sims=n_sims, own_lean=own_lean, seed=seed)

    # Core-exposure floor: every core the sharp set is guaranteed at least this
    # many lineups so a conviction play can't get squeezed to 1 of N. Data-driven
    # from slate shape — more cores spread the floor thinner, more lineups raise
    # it — never a hardcoded number.
    core_floors = None
    if cores and min_cores > 0:
        floor_ct = int(math.ceil(n / (len(cores) + 1)))
        if floor_ct >= 1:
            core_floors = {c.dk_id: floor_ct for c in cores}
    final = select_final(cands, n, player_caps, core_floors=core_floors,
                         backfill=cands, report=rep)
    rep["returned"] = len(final)
    if max_off_pool:  # 0 or None -> every lineup is already all-in-pool
        _attach_pool_alternatives(final, pool, max_per_team, n_sims, own_lean, seed)
    return final


# _viable_pool used to sit here: it kept the top 60% of the board by ceiling
# (minimum 18 players) on the theory that every slot needs a real path to a
# useful score. On a two-game slate that removed the entire sub-$6,600 tier. On
# 8-10 it cut the pool to 18 players, 99.75% of construction attempts then failed
# and the tool returned 3 lineups for a requested 12 — and three of the six
# players in that night's WINNING lineup had been deleted before construction
# began. Held out it changed nothing on its own and was the root of the one
# starvation bug in the data, so the board is no longer pre-filtered at all.


def _can_field(pool):
    """Cheapest legal roster (2 G + 3 F + 1 flex) fits under the cap?"""
    g = sorted((p.salary for p in pool if p.is_guard))
    f = sorted((p.salary for p in pool if not p.is_guard))
    if len(g) < MIN_GUARDS or len(f) < MIN_FORWARDS:
        return False
    rest = sorted(g[MIN_GUARDS:] + f[MIN_FORWARDS:])
    need = g[:MIN_GUARDS] + f[:MIN_FORWARDS] + rest[:ROSTER_SIZE - MIN_GUARDS - MIN_FORWARDS]
    return len(need) == ROSTER_SIZE and sum(need) <= SALARY_CAP
