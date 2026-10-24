"""NBA late swap — the WNBA tool's rules, on NBA's files.

Three files, all pulled mid-slate:

  * a FRESH Stokastic projections export   (the news source: projection, Starting)
  * your CURRENT DK entries export          (what is entered, who is LOCKED, DK ids)
  * the CURRENT contest standings, optional (live points, your rank, the field)

Why it is built this way is in wnba/app.py's late-swap notes, and the numbers
there are the reason for every rule below. The short version: across the WNBA
review, swaps forced by NEWS gained 24.5 points an entry and were positive 15
times of 15, while re-optimising slots nobody had said anything about averaged
+3.4 and was a coin flip (15 of 29) — and on one night cost 125 points. What
separated the good moves from the churn was the SIZE of the simulated gain, so a
move with news clears a low bar and a move without it clears a high one. Both
Stokastic late-swap runs on NBA's first night (Oct 20) changed rosters with no
news behind them and both lost money.

What is NBA's own here, and unmeasured:
  * The gain bars are WNBA's scaled by 1.4x — an eight-man NBA lineup swings
    about 1.4 times a six-man WNBA one. Every decision is logged with its gain so
    the bars can be re-fit on NBA nights.
  * No per-team cap: the NBA build has none, so the swap does not invent one.
  * Positions overlap, so a roster is legal if SOME assignment of its open
    players fills the open slots; locked players never leave their slot.
  * Classic only. On a showdown every player locks at once.

Default policy is FREE (the user's call for the start of the season, when
projections move a lot, and again on Oct 21): for every lineup, build the best
lineup from ALL its open slots on the fresh projections, and swap if it beats
what is entered by +8 when the lineup holds a player with news, or by +14 when it
does not. News changes the slate, so the whole lineup is open, not just the news
player's slot. The narrow news-only policy (the news player plus one spare) is
evaluated on every run and logged beside it, so each night grades both.
"""

from __future__ import annotations

import bisect
import json
import os
import random
from datetime import datetime

import classic as C
import engine as E
from dk import CLASSIC_SLOTS, CLASSIC_SIZE, SALARY_CAP, normalize_name
from sources import (read_dk_entries, read_projections, read_sharp, read_standings,
                     stokastic_problem)

# --- the bars (WNBA x 1.4) ---------------------------------------------------
SWAP_NEWS_GAIN = 8.0        # a move that replaces or brings in a player with news
SWAP_DISC_GAIN = 14.0       # a move with no news behind it (WNBA: measured at 10)
SWAP_OFF_POOL_GAIN = 17.0   # projection each off-pool incoming player must add
SWAP_MAX_LEFTOVER = 2000    # stranding more than this needs a clearly better roster
SWAP_STRAND_GAIN = 17.0
# Where a projected finish sits in the field: projecting to win (top 1%) blocks
# any move without news. WNBA: refusing on that estimate was right 9 of 9 times.
SWAP_WIN_PCT = 1.0
SWAP_CASH_PCT = 20.0
SWAP_CHASE_PCT = 70.0
SWAP_EARLY_PENALTY = 0.5    # tiny tiebreak against filling from the next game to tip

# --- what counts as news (against the logged build of this slate) -------------
SWAP_NEWS_PROJ_SHARE = 0.25  # a raw projection moving this share...
SWAP_NEWS_MIN_PTS = 5.0      # ...and at least this many points (WNBA 4, x1.4 rounded)
SWAP_BENCH_EDGE = 1.5        # a cheaper starter at the position projecting this multiple

# --- breadth ------------------------------------------------------------------
SWAP_TOP_PER_SLOT = 12
SWAP_PAIR_TOP = 4
SWAP_RANDOM_FILLS = 250
SWAP_SIMS = 1500

SWAP_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "logs", "nba_swaps.jsonl")


# ---------------- the logged build: what we believed at lock ----------------
def news_baseline(log_path, games):
    """{norm name: {"proj", "starting"}} from the LAST build of this game set.

    Matched on the slate's game set, never a date: a 10:30 tip rolls past
    midnight and a date match then finds nothing (WNBA hit exactly that).
    """
    want = sorted(games or [])
    builds = {}
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if sorted(r.get("games") or []) == want:
                    builds.setdefault(r.get("build_id") or r.get("ts"), []).append(r)
    except OSError:
        return {}, None
    if not builds:
        return {}, None
    last = builds[max(builds)]
    out = {}
    for r in last:
        for p in r.get("players") or []:
            out[normalize_name(p["name"])] = {"proj": p.get("proj"),
                                              "starting": p.get("starting")}
    return out, last[0]


def news_names(players, baseline, locked, held=()):
    """{norm name: reason} for every player something has changed about.

    `held` is everyone in your entries: a held player now projected 0 is news
    even with no logged build to compare against."""
    news = {}
    held = set(held)
    live = [p for p in players if p.proj > 0 and p.starting
            and normalize_name(p.name) not in locked]
    for p in players:
        nm = normalize_name(p.name)
        if nm in locked:
            continue
        b = baseline.get(nm)
        if p.proj <= 0 or p.removed:
            if (b and (b.get("proj") or 0) > 0) or nm in held:
                news[nm] = "ruled out" if not p.removed else "removed by you (late scratch)"
            continue
        if b and (b.get("proj") or 0) > 0:
            was, now = b["proj"], p.proj
            if was - now >= SWAP_NEWS_MIN_PTS and was - now >= was * SWAP_NEWS_PROJ_SHARE:
                news[nm] = f"projection cut {was:.0f} to {now:.0f}"
                continue
            if now - was >= SWAP_NEWS_MIN_PTS and now - was >= was * SWAP_NEWS_PROJ_SHARE:
                news[nm] = f"projection up {was:.0f} to {now:.0f}"
                continue
            if b.get("starting") is True and p.starting is False:
                news[nm] = "was starting at lock, now on the bench"
                continue
            if b.get("starting") is False and p.starting is True:
                news[nm] = "was on the bench at lock, now starting"
                continue
        if p.starting is False:
            alt = [q for q in live if q.eligible & p.eligible and q.salary <= p.salary
                   and q.proj >= p.proj * SWAP_BENCH_EDGE and q is not p]
            if alt:
                best = max(alt, key=lambda q: q.proj)
                news[nm] = (f"on the bench — {best.name} is starting at ${best.salary:,} "
                            f"and projects {best.proj:.0f} against {p.proj:.0f}")
    return news


# ---------------- slotting with locked players pinned ----------------
def _fill(open_slots, players, start_of=None):
    """Assign players to the open slot labels -> [player per open slot] or None.

    Prefers the latest tips in UTIL, then G and F, as the build's upload does."""
    best, best_key = None, None
    n = len(open_slots)
    if len(players) != n:
        return None
    order = sorted(range(n), key=lambda i: sum(1 for p in players
                                                if open_slots[i] in p.eligible))
    cur, used = [None] * n, set()

    def key(a):
        if not start_of:
            return ()
        at = dict(zip(open_slots, a))
        return tuple((start_of(at[s]) if s in at else "") for s in ("UTIL", "G", "F"))

    found = [0]

    def go(k):
        nonlocal best, best_key
        if found[0] > 400:
            return
        if k == n:
            found[0] += 1
            kk = key(cur)
            if best is None or kk > best_key:
                best, best_key = list(cur), kk
            return
        i = order[k]
        for j, p in enumerate(players):
            if j in used or open_slots[i] not in p.eligible:
                continue
            used.add(j)
            cur[i] = p
            go(k + 1)
            used.discard(j)
            cur[i] = None

    go(0)
    return best


# ---------------- field and standing ----------------
def _avg_open_slot(players, locked):
    live = [p for p in players if p.proj > 0 and normalize_name(p.name) not in locked]
    wt = sum(p.ownership for p in live) or 1.0
    return sum(p.proj * p.ownership for p in live) / wt if live else 0.0


def _aggression_from_rank(pct):
    """0.15 protect when projecting to win, 0.5 neutral, 0.85 chase (WNBA)."""
    if pct <= SWAP_WIN_PCT:
        return 0.15
    if pct <= SWAP_CASH_PCT:
        return 0.15 + (pct - SWAP_WIN_PCT) / (SWAP_CASH_PCT - SWAP_WIN_PCT) * 0.35
    return 0.5 + min(1.0, (pct - SWAP_CASH_PCT) / (SWAP_CHASE_PCT - SWAP_CASH_PCT)) * 0.35


# ---------------- the run ----------------
def run_late_swap(proj_text, dk_text, standings_text="", options=None, log_path=None):
    o = options or {}
    notes = []

    def say(kind, text):
        notes.append({"type": kind, "text": text})

    players, rep = read_projections(proj_text or "")
    if rep.get("error"):
        return {"error": f"Projections file: {rep['error']}"}
    why_not = stokastic_problem(rep)
    if why_not and not o.get("allowNonStokastic"):
        return {"error": f"Not swapping: the projections file is not Stokastic's — "
                         f"{why_not}. Pull a fresh Stokastic projections export."}
    import app
    old_day = None if o.get("allowOldSlate") else app._old_slate(
        players, log_path or _build_log())
    if old_day:
        return {"error": "Not swapping: " + app._old_slate_msg(old_day, players)}
    if not (dk_text or "").strip():
        return {"error": "Drop your CURRENT DK entries export — it is the only file "
                         "that says what is entered and who is locked."}
    dk = read_dk_entries(dk_text)
    if not dk["pool"]:
        return {"error": "Couldn't read that as a DK entries export (no player pool)."}
    slots = list(dk["slots"])
    if tuple(slots) != CLASSIC_SLOTS:
        return {"error": f"Late swap is classic only; this file's slots are "
                         f"{', '.join(slots)}. A showdown locks all at once."}

    # DK ids, eligibility, tip times and locks from DK's own file.
    by_norm = {}
    for p in players:
        rec = dk["pool"].get(normalize_name(p.name))
        if rec and rec.get("dk_id"):
            p.dk_id = rec["dk_id"]
            p.eligible = set(rec.get("eligible") or p.eligible)
            p.start = rec.get("start") or ""
        by_norm[normalize_name(p.name)] = p
    locked = {n for n, rec in dk["pool"].items() if rec.get("locked")}
    games = sorted({p.game for p in players if p.game and p.proj > 0})

    # Late scratches typed on the page: zero them (WNBA removal), same as a build.
    from app import apply_removals          # one removal, shared with the build
    removed, rem_missing = apply_removals(players, read_sharp(o.get("remove") or ""))
    if rem_missing:
        say("warn", "Not on this slate, so NOT removed: " + ", ".join(rem_missing))

    baseline, base_row = news_baseline(log_path or _build_log(), games)
    pool_names = read_sharp(o.get("pool") or "")
    core_names = read_sharp(o.get("cores") or "")
    if base_row and not pool_names and not core_names:
        st = base_row.get("settings") or {}
        pool_names = {normalize_name(n) for n in st.get("pool") or []}
        core_names = {normalize_name(n) for n in st.get("cores") or []}
        if pool_names or core_names:
            say("info", f"Pool and cores taken from your build at "
                        f"{(base_row.get('ts') or '')[11:16]}: {len(pool_names | core_names)} "
                        f"names, {len(core_names)} cores.")
    # Neutral by default, as the build: a core is a pool player (when there is a
    # pool) and is opened for swaps like anyone else.
    if str(o.get("neutral", "on")) != "off":
        if pool_names:
            pool_names |= core_names
        core_names = set()
    pool_names |= core_names
    for p in players:
        n = normalize_name(p.name)
        p.core = n in core_names
        p.in_pool = p.core or (n in pool_names)
    if baseline:
        say("info", f"News is judged against your build at "
                    f"{(base_row.get('ts') or '')[11:16]} ({len(baseline)} players logged).")
    else:
        say("warn", "No logged build of this slate, so news is only players ruled out "
                    "or benched — a projection cut cannot be seen without the build.")
    held = {normalize_name(c["name"]) for e in dk["entries"] for c in e.get("cells") or []
            if c}
    news = news_names(players, baseline, locked, held)
    if news:
        say("info", f"News on {len(news)} player(s): " + "; ".join(
            f"{by_norm[n].name if n in by_norm else n} — {why}" for n, why in
            sorted(news.items())[:12]))
    else:
        say("info", "No news found on any unlocked player.")

    # Standings: live points, rank, the field.
    contest = None
    if (standings_text or "").strip():
        stand, srep = read_standings(standings_text)
        if srep.get("error"):
            say("warn", f"Standings file: {srep['error']}")
        else:
            contest = {"rows": stand, "rep": srep}
            for p in players:                      # real ownership where revealed
                real = srep["ownership"].get(normalize_name(p.name))
                if real:
                    p.ownership = real
    else:
        say("warn", "No contest standings, so there is no live score and no rank — "
                    "every lineup is judged neutrally (neither protecting nor chasing).")

    entries = [e for e in dk["entries"]
               if e.get("cells") and all(c for c in e["cells"])]
    if not entries:
        return {"error": "That DK file has no filled-in lineups.", "notes": notes}
    sims = int(o.get("sims") or SWAP_SIMS)
    mat = E.simulate([p for p in players if p.proj > 0], sims=sims,
                     seed=int(o.get("seed") or 0))
    zero = [0.0] * sims
    row = lambda p: mat.get(p.dk_id, zero)
    starts = sorted({p.start for p in players
                     if p.start and normalize_name(p.name) not in locked})
    early = {normalize_name(p.name) for p in players
             if len(starts) > 1 and p.start == starts[0]
             and normalize_name(p.name) not in locked}
    start_of = lambda p: p.start or ""

    mine = {e["entry_id"] for e in entries}
    # Projected FINAL for every entry, not its score right now. DK's
    # TimeRemaining is the player-minutes still to play on a roster: 48 for each
    # player yet to tip, and whatever is left for a player whose game is in
    # progress. Points + minutes left x the slate's points per player-minute is
    # the same yardstick for the field and for us (the user, Oct 21: a live
    # "score to win" read as far away when a game still had a quarter to go).
    field_finals, by_entry, rate = [], {}, 0.0
    if contest:
        avg = _avg_open_slot(players, locked)
        rate = avg / 48.0
        for e in contest["rows"]:
            by_entry[e["entry_id"]] = e
            if e["entry_id"] not in mine:
                if e.get("time_left"):
                    left = e["time_left"] * rate
                else:                       # no clock column: fall back to slots
                    left = sum(1 for n in e["names"] if n == "locked") * avg
                field_finals.append(e["points"] + left)
        field_finals.sort()

    cache = {}

    def metrics(roster):
        key = frozenset(p.dk_id for p in roster)
        if key not in cache:
            open_rows = [row(p) for p in roster if normalize_name(p.name) not in locked]
            tot = sorted(sum(t) for t in zip(*open_rows)) if open_rows else zero
            cache[key] = (sum(tot) / sims, tot[int(sims * 0.85)], tot[int(sims * 0.95)])
        return cache[key]

    def swap_score(roster, aggr):
        mean, p85, p95 = metrics(roster)
        if aggr <= 0.5:
            base = mean + (aggr / 0.5) * (p85 - mean)
        else:
            base = p85 + ((aggr - 0.5) / 0.5) * (p95 - p85)
        soon = sum(1 for p in roster if normalize_name(p.name) in early)
        return base - SWAP_EARLY_PENALTY * soon

    def view(roster, locks):
        """Slot by slot, for the page's before / after drawdown (WNBA's)."""
        return [{"slot": sl, "name": p.name, "team": p.team, "salary": p.salary,
                 "proj": round(p.proj, 1), "own": round(p.ownership, 1),
                 "locked": bool(lk), "core": bool(p.core),
                 "offPool": bool(pool_names or core_names) and not p.in_pool}
                for sl, p, lk in zip(slots, roster, locks)]

    rng = random.Random(int(o.get("seed") or 0))
    results, lines = [], ["Entry ID,Contest Name,Contest ID,Entry Fee," + ",".join(slots)]
    stale, changed = [], 0
    for e in entries:
        lineup = [by_norm.get(normalize_name(c["name"])) for c in e["cells"]]
        rec = {"entryId": e["entry_id"], "contest": e["contest"]}
        if any(p is None for p in lineup):
            miss = [c["name"] for c, p in zip(e["cells"], lineup) if p is None]
            rec.update(error="not in the projections file: " + ", ".join(miss),
                       keep=True, rowBefore=_dk_line(e, e["cells"]),
                       rowAfter=_dk_line(e, e["cells"]))
            results.append(rec)
            lines.append(_dk_line(e, e["cells"]))
            continue
        is_locked = [c["locked"] or normalize_name(p.name) in locked
                     for c, p in zip(e["cells"], lineup)]
        # DK's standings say who has tipped for THIS entry. If it shows a tipped
        # player the entries file does not, the file is stale — never guess.
        live = by_entry.get(e["entry_id"])
        if live:
            shown = {n for n in live["names"] if n != "locked"}
            ours = {normalize_name(p.name) for p in lineup}
            if shown - ours:
                stale.append(e["entry_id"])
                rec.update(keep=True, hold="your DK entries file is out of date for this "
                                           "entry — re-download it",
                           rowBefore=_dk_line(e, e["cells"]), rowAfter=_dk_line(e, e["cells"]))
                results.append(rec)
                lines.append(_dk_line(e, e["cells"]))
                continue
        news_here = {i for i, p in enumerate(lineup)
                     if not is_locked[i] and normalize_name(p.name) in news}
        open_idx = [i for i, p in enumerate(lineup) if not is_locked[i]
                    and not (p.core and p.proj > 0 and i not in news_here)]
        # Where this lineup stands.
        pct = None
        aggr = 0.5
        banked = live["points"] if live else None
        proj_final = None
        if live and field_finals:
            # Our unstarted players at their own projections; anything else left
            # on the clock (a game in progress) at the slate rate, like the field.
            unstarted = [p for i, p in enumerate(lineup) if not is_locked[i]]
            in_play = max(0.0, (live.get("time_left") or 0.0) - 48.0 * len(unstarted))
            proj_final = (live["points"] + sum(p.proj for p in unstarted)
                          + in_play * rate)
            ahead = len(field_finals) - bisect.bisect_left(field_finals, proj_final)
            pct = 100.0 * ahead / len(field_finals)
            aggr = _aggression_from_rank(pct)
        fixed = [p for i, p in enumerate(lineup) if i not in open_idx]
        open_slots = [slots[i] for i in open_idx]
        budget = SALARY_CAP - sum(p.salary for p in fixed)
        used = {p.dk_id for p in fixed}
        cand = [p for p in players if p.proj > 0 and not p.removed and p.dk_id not in used
                and normalize_name(p.name) not in locked and p.salary > 0]
        # Breadth by projection AND by value. Top-by-projection alone is all
        # studs, and none of them fits the salary a ruled-out $3,700 player
        # frees on a lineup already at the cap — that is the swap that matters.
        cur_sal = sum(lineup[i].salary for i in open_idx)
        afford = budget - cur_sal + max((lineup[i].salary for i in open_idx), default=0)
        per_slot = {}
        for sl in set(open_slots):
            el = [p for p in cand if sl in p.eligible]
            by_proj = sorted(el, key=lambda p: -p.proj)[:SWAP_TOP_PER_SLOT]
            by_val = sorted((p for p in el if p.salary <= afford),
                            key=lambda p: -p.proj / max(p.salary, 1))[:SWAP_TOP_PER_SLOT]
            per_slot[sl] = list({p.dk_id: p for p in by_proj + by_val}.values())
        cur_open = [lineup[i] for i in open_idx]
        rosters = {frozenset(p.dk_id for p in lineup): list(lineup)}

        def consider(open_players):
            if len({p.dk_id for p in open_players}) != len(open_players):
                return
            if sum(p.salary for p in open_players) > budget:
                return
            filled = _fill(open_slots, open_players)
            if filled is None:
                return
            full = fixed + filled
            if len({p.game for p in full}) < 2:
                return
            rosters.setdefault(frozenset(p.dk_id for p in full), full)

        if open_idx:
            pool_c = sorted({q.dk_id: q for qs in per_slot.values() for q in qs}.values(),
                            key=lambda p: -p.proj)
            for i in range(len(cur_open)):                  # every single swap,
                room = budget - cur_sal + cur_open[i].salary  # every affordable player
                for q in cand:
                    if q.salary > room:
                        continue
                    if q in cur_open:
                        continue
                    consider(cur_open[:i] + [q] + cur_open[i + 1:])
            top = list({p.dk_id: p for p in (
                pool_c[:SWAP_PAIR_TOP * 2]
                + sorted((p for p in pool_c if p.salary <= afford),
                         key=lambda p: -p.proj / max(p.salary, 1))[:SWAP_PAIR_TOP * 2])
                }.values())
            for i in range(len(cur_open)):                  # pairs of swaps
                for j in range(i + 1, len(cur_open)):
                    for a in top:
                        for b in top:
                            if a is b or a in cur_open or b in cur_open:
                                continue
                            nxt = list(cur_open)
                            nxt[i], nxt[j] = a, b
                            consider(nxt)
            weight = {p.dk_id: max(p.proj, 0.1) ** E.FILL_EXP for p in pool_c}
            for _ in range(SWAP_RANDOM_FILLS):              # whole re-fills
                pick, left = [], budget
                for s in open_slots:
                    el = [q for q in per_slot[s] if q not in pick
                          and q.salary <= left - 3000 * (len(open_slots) - len(pick) - 1)]
                    if not el:
                        break
                    q = E._pick(el, [weight[x.dk_id] for x in el], rng)
                    pick.append(q)
                    left -= q.salary
                if len(pick) == len(open_slots):
                    consider(pick)

        here = {normalize_name(p.name) for p in lineup}
        cur_score = swap_score(lineup, aggr)
        cur_proj = sum(p.proj for p in lineup)
        winning = pct is not None and pct <= SWAP_WIN_PCT
        scored = sorted(((swap_score(r, aggr), r) for r in rosters.values()),
                        key=lambda t: -t[0])

        # News in this lineup reopens the WHOLE lineup at the low bar: the
        # user's call (Oct 21) — news changes the slate, so the structure built
        # before it may not be the best one now, and nothing in it is sacred.
        # Without news, the best lineup from the open slots must beat what is
        # there by the high bar. Either way it is the best lineup that wins, not
        # the smallest change.
        lineup_news = bool(news_here)
        # A player ruled out is a guaranteed 0. He is swapped EVERY time, with no
        # bar at all (the user, Oct 21: "why would I willingly take a 0?"): the
        # best lineup without him wins, whatever its gain, salary left or pool.
        dead_here = {normalize_name(lineup[i].name) for i in news_here
                     if news[normalize_name(lineup[i].name)] in
                     ("ruled out", "removed by you (late scratch)")}

        def choose(news_only):
            blocked = None
            if dead_here:
                alive = [(sc, r) for sc, r in scored
                         if not ({normalize_name(p.name) for p in r} & dead_here)]
                for sc, r in alive:                 # best one inside the pool first
                    inn = {normalize_name(p.name) for p in r} - here
                    if not pool_names or all(n in pool_names for n in inn):
                        return r, sc - cur_score, True, None
                if alive:                           # else the best one at all
                    return alive[0][1], alive[0][0] - cur_score, True, None
            for sc, r in scored:
                names = {normalize_name(p.name) for p in r}
                if names == here:
                    continue
                out, inn = here - names, names - here
                forced = any(n in news for n in out | inn)
                if news_only:
                    out_news = [n for n in out if n in news]
                    if not out_news or len([n for n in out if n not in news]) > 1:
                        continue
                    need = SWAP_NEWS_GAIN
                else:
                    forced = lineup_news
                    need = SWAP_NEWS_GAIN if lineup_news else (
                        float("inf") if winning else SWAP_DISC_GAIN)
                gain = sc - cur_score
                if gain < need:
                    continue
                sal = sum(p.salary for p in r)
                if SALARY_CAP - sal > SWAP_MAX_LEFTOVER and gain < SWAP_STRAND_GAIN:
                    continue
                if pool_names:
                    off = [p for p in r if normalize_name(p.name) in inn
                           and normalize_name(p.name) not in pool_names]
                    bar = SWAP_NEWS_GAIN if forced else SWAP_OFF_POOL_GAIN
                    gain_proj = sum(p.proj for p in r) - cur_proj
                    if off and gain_proj < bar * len(off):
                        if blocked is None:
                            blocked = (f"{', '.join(p.name for p in off)} would add "
                                       f"+{gain_proj:.1f} proj but is off your pool; "
                                       f"that needs +{bar * len(off):.0f}")
                        continue
                return r, gain, forced, blocked
            return None, None, False, blocked

        free_pick, free_gain, free_forced, free_blocked = choose(False)
        news_pick, news_gain, _nf, _nb = choose(True)
        news_mode = str(o.get("swapMode") or "free") == "news"
        pick, gain, forced, blocked = ((news_pick, news_gain, True, _nb) if news_mode
                                       else (free_pick, free_gain, free_forced, free_blocked))
        final = pick if pick else list(lineup)
        # Keep each locked player in his exact slot; re-slot the open ones.
        out_cells = []
        if pick:
            open_now = [p for p in final if p not in fixed]
            filled = _fill(open_slots, open_now, start_of) or open_now
            slotted = list(lineup)
            for i, p in zip(open_idx, filled):
                slotted[i] = p
            out_cells = [{"name": p.name, "dk_id": p.dk_id} for p in slotted]
            changed += 1
        else:
            out_cells = [{"name": c["name"], "dk_id": c["dk_id"]} for c in e["cells"]]
            slotted = list(lineup)
        bad = _check(slotted, lineup, is_locked, slots) if pick else ""
        if bad:
            say("warn", f"Entry {e['entry_id']}: the swap would break a DK rule ({bad}), "
                        f"so it is left as entered.")
            out_cells = [{"name": c["name"], "dk_id": c["dk_id"]} for c in e["cells"]]
            slotted, pick = list(lineup), None
            changed -= 1
        why_news = [f"{p.name}: {news[normalize_name(p.name)]}" for i, p in enumerate(lineup)
                    if i in news_here]
        rec.update({
            "keep": pick is None, "open": len(open_idx), "banked": banked,
            "timeLeft": live.get("time_left") if live else None,
            "projFinal": None if proj_final is None else round(proj_final, 1),
            "pct": None if pct is None else round(pct, 2), "aggression": round(aggr, 2),
            "news": why_news, "gain": round(gain, 1) if pick else None,
            "forced": bool(pick and forced),
            "out": [p.name for p in lineup if pick and p not in slotted],
            "in": [p.name for p in slotted if pick and p not in lineup],
            "hold": None if pick else _hold_reason(blocked, open_idx, winning, why_news),
            "shadow": {"acted": "news" if news_mode else "free",
                       "freeGain": None if free_pick is None else round(free_gain, 1),
                       "newsGain": None if news_pick is None else round(news_gain, 1),
                       "freeRoster": [p.name for p in free_pick] if free_pick else None,
                       "newsRoster": [p.name for p in news_pick] if news_pick else None},
            "rowBefore": _dk_line(e, e["cells"]),
            "rowAfter": _dk_line(e, out_cells),
            "before": view(lineup, is_locked),
            "after": view(slotted, is_locked),
        })
        results.append(rec)
        lines.append(rec["rowAfter"])

    if stale:
        say("warn", f"{len(stale)} entr(ies) left alone because DK's standings show a "
                    f"different tipped player than your entries file. Re-download the "
                    f"DK entries export and run again.")
    say("good" if changed else "info",
        f"{changed} of {len(entries)} lineups would change; the rest are left exactly "
        f"as entered.")
    win_line = (round(field_finals[int(len(field_finals) * 0.99)], 1)
                if field_finals else None)
    if win_line:
        say("info", f"Projected FINAL top-1% line: {win_line:.0f} — today's points plus "
                    f"what is still on the clock, the same way for your entries and the "
                    f"field. A live score alone would read far lower.")
    out = {"notes": notes, "entries": len(entries), "changed": changed,
           "winLine": win_line,
           "mode": "news" if str(o.get("swapMode") or "free") == "news" else "free",
           "bars": {"news": SWAP_NEWS_GAIN, "noNews": SWAP_DISC_GAIN,
                    "offPool": SWAP_OFF_POOL_GAIN},
           "news": {k: v for k, v in news.items()},
           "swaps": results, "csvHeader": lines[0],
           "dkCsv": "\n".join(lines) + "\n"}
    _log_swap(out, players, baseline, games, o)
    return out


def _hold_reason(blocked, open_idx, winning, why_news):
    """Why a lineup is left alone — never a silent shrug."""
    if blocked:
        return blocked
    if not open_idx:
        return "nothing left to move"
    if winning and not why_news:
        return "projecting to win — only news moves it"
    if why_news:
        return f"news, but no move clears +{SWAP_NEWS_GAIN:.0f}"
    return f"no news, and nothing clears +{SWAP_DISC_GAIN:.0f}"


def _dk_line(e, cells):
    cname = (e["contest"] or "").replace('"', '""')
    return (f'{e["entry_id"]},"{cname}",{e["contest_id"]},{e["fee"]},'
            + ",".join(f'"{c["name"]} ({c["dk_id"]})"' for c in cells))


def _check(slotted, before, is_locked, slots):
    """Why DK would refuse this row, or ""."""
    for i, (p, b) in enumerate(zip(slotted, before)):
        if is_locked[i] and p is not b:
            return f"it moves {b.name}, who is locked"
    if any(s not in p.eligible for s, p in zip(slots, slotted)):
        return "a player in a slot he is not eligible for"
    why = C.legal(slotted)
    return why


def _build_log():
    import app
    return app.LOG_PATH


def _log_swap(out, players, baseline, games, options):
    """One line per run, BOTH policies per entry, so each night grades both."""
    try:
        moved = []
        for p in players:
            b = baseline.get(normalize_name(p.name))
            if b and b.get("proj") is not None and abs((b["proj"] or 0) - p.proj) >= 1.5:
                moved.append({"name": p.name, "lockProj": b["proj"], "nowProj": p.proj,
                              "starting": p.starting})
        rec = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
               "games": games, "mode": out["mode"], "bars": out["bars"],
               "hadBaseline": bool(baseline), "news": out["news"],
               "projectionsMoved": moved,
               "swaps": [{k: v for k, v in s.items() if k not in ("rowBefore", "rowAfter", "before", "after")}
                         for s in out["swaps"]]}
        os.makedirs(os.path.dirname(SWAP_LOG_PATH), exist_ok=True)
        with open(SWAP_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception as exc:                   # never break a swap over logging
        out["notes"].append({"type": "warn",
                             "text": f"Swap log not written ({exc}). This night will "
                                     f"not be gradable later."})
