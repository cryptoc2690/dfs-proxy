"""chalk_sheet.py: the chalk sheet. One call, a plain dict back.

    sheet = chalk_sheet(players, active, logs, history, games, linestar, pool_names)

    players     every Player in tonight's projections file (dk.Player), in_pool / removed set
    active      the buildable ones (newbuild.is_active)
    logs        role.load_logs(slate day): the ESPN box scores before tonight
    history     newbuild.load_history(): the saved nights
    games       number of games on the slate
    linestar    sources.read_linestar(text)[0] ({name key: {"own", ...}}), or {} / None
    pool_names  the typed pool (normalised names), empty when no pool is typed

    Optional, by keyword (the tool has all of them when it builds):
    today       the slate's date (datetime.date). Default: today.
    ceil        leverage.ceilings(proj_text). Without it the leverage vote loses one of its reasons.
    inj         newbuild.injury_tags(proj_text). Without it "out" means "in the file, not active".
    needs       the fitted table (a dict). Default: needs.json beside this file.
    notes       {team: [note, ...]} or the path of `NBA Team Notes (master).md`.
    left_off    {name key: how many earlier nights he was marked "left off on purpose"}.

Everything comes from what the tool has before lock. Nothing is fitted in the call: the
break-even and the big-game score are read from needs.json (fit_needs.py writes it). Nothing
here moves the build. A line moves only when it is ticked (tick_plan() turns ticks into the
caps and minimums the build already takes). No rule numbers in any text it returns.
"""
import collections
import datetime as dt
import json
import math
import os
import re
import statistics
import sys

if os.environ.get("NBA_TOOL"):                       # the review ran this against a copy of the tool
    sys.path.insert(0, os.environ["NBA_TOOL"])
import boxread as BR
import leverage as LV
import matchup as MU
import newbuild as NB
import role as RL
from dk import normalize_name

HERE = os.path.dirname(os.path.abspath(__file__))
NEEDS_PATH = os.path.join(HERE, "needs.json")

# --- who gets a line ---------------------------------------------------------
CHALK_OWN = 15.0          # every player at 15%+ projected own (Stokastic's)
TOP_CHEAP_SAL, TOP_CHEAP_N = 5000, 3   # and the three most-owned players under $5,000
#                           and anyone one of the old rules votes on

# --- the marks ---------------------------------------------------------------
CHEAP = 5500              # "cheap" = priced under $5,500
HEAVY_OWN, HEAVY_GAP = 25.0, -1.5        # heavily owned and under his line
CHEAP_UNDER_OWN, CHEAP_UNDER_GAP = 15.0, -0.5   # the same for a cheap player, from half a point under (post-hoc)
CLEAR_MOST_OWN, CLEAR_MOST_GAP = 25.0, 6.0
SHORT_GAP, CLEAR_GAP = 0.93, 2.85        # the thirds of the gap among chalk (Top 10 report); frozen
NIGHT_LINE = 5            # 5+ cheap chalk players with nobody out: the winners tend to come from under the field
FRINGE_MPG = 15.0         # a teammate under 15 minutes a game is never "back"
HEAVY_CROWD = 3           # more than 3 players with the heavy mark: the tick is suggested only for the cheap ones
SET_BIG, SET_SMALL = 150, 20
OFF_OWN, OFF_LS_OWN, OFF_PROJ = 5.0, 10.0, 10.0
OFF_STUD_SAL, OFF_STUD_PROJ, OFF_STUD_OWN, OFF_STUD_N = 7500, 14.0, 10.0, 3

PRICE_BANDS = (3000, 4500, 5500, 7000, 9000)
OWN_BANDS = (5, 15, 25, 40)


# --- the fitted table ----------------------------------------------------------
def load_needs(path=None):
    with open(path or NEEDS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _pband(sal):
    return max([b for b in PRICE_BANDS if sal >= b] or [PRICE_BANDS[0]])


def _oband(own):
    return max([b for b in OWN_BANDS if own >= b] or [OWN_BANDS[0]])


def need(tables, salary, own):
    """(break-even points, big-game points) at this price and projected own."""
    e = tables["break_even_multiple"].get(f"{_oband(own)}|{_pband(salary)}", tables.get("default_break_even", 5.2))
    b = tables["big_game_multiple"].get(str(_pband(salary)), tables.get("default_big_game", 8.0))
    return e * salary / 1000.0, b * salary / 1000.0


def chance(line, proj, sd):
    """The chance he scores `line` or more: a normal curve around Stokastic's projection with
    Stokastic's Std Dev."""
    if not sd or sd <= 0:
        return 1.0 if proj >= line else 0.0
    return 1.0 - 0.5 * (1.0 + math.erf((line - proj) / sd / math.sqrt(2.0)))


# --- small reads from his own games ---------------------------------------------
def role_type(H):
    """His role from his games for this team (the Role Rules Audit's lines): regular starter =
    started 60%+ of his last ten; sixth man = 4+ of his last five on record, one start at most,
    24+ minutes a game and never under 18; part-time starter = started 20% to 60%; else rotation
    bench at 15+ minutes a game over his last ten, fringe under that."""
    L = H[-10:]
    if not L:
        return "no game on record"
    rate = sum(1 for x in L if x["starter"]) / len(L)
    m = sum(x["mins"] for x in L) / len(L)
    if rate >= 0.6:
        return "regular starter"
    l5 = H[-5:]
    if (len(l5) >= 4 and sum(1 for x in l5 if x["starter"]) <= 1
            and sum(x["mins"] for x in l5) / len(l5) >= 24.0 and min(x["mins"] for x in l5) >= 18.0):
        return "sixth man"
    if rate >= 0.2:
        return "part-time starter"
    return "rotation bench" if m >= FRINGE_MPG else "fringe"


def steadiness(H):
    """The spread of his DK points over their average, 5+ games: steady at 0.28 or under, hit or
    miss at 0.39 or over."""
    dk = [x["dk"] for x in H]
    if len(dk) < 5 or not sum(dk):
        return None
    cv = statistics.pstdev(dk) / (sum(dk) / len(dk))
    return "steady" if cv <= 0.28 else "hit or miss" if cv >= 0.39 else "in between"


def read_notes(path):
    """{team: [note, ...]} from `NBA Team Notes (master).md`: every table row above the section
    "Notes that no longer hold"."""
    out = collections.defaultdict(list)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    for ln in text.split("## Notes that no longer hold")[0].split("\n"):
        c = [x.strip() for x in ln.strip().strip("|").split("|")]
        if ln.startswith("|") and len(c) >= 3 and c[0].isdigit() and re.fullmatch(r"[A-Z]{2,4}", c[1]):
            out[c[1]].append(c[2])
    return dict(out)


# --- the marks and the ticks (pure functions: the tests call them) ---------------------
def player_marks(own, price, gap, starting, nobody_out, short_gap=SHORT_GAP, clear_gap=CLEAR_GAP):
    """The marks on one line. own: Stokastic's projected own (%). gap: his projection less his
    break-even. starting: Stokastic's flag (True / False / None). nobody_out: no regular of his
    team is out tonight (boxread.team_outs is empty for his team)."""
    marks = []
    chalk, cheap, bench = own >= CHALK_OWN, price < CHEAP, starting is False
    if own >= HEAVY_OWN and gap <= HEAVY_GAP:
        marks.append({"mark": "heavily owned and under what he needs",
                      "rule": "25%+ projected own and projected 1.5 or more under his break-even",
                      "record": "outside one two-game night: 7 players, 2 reached the break-even, none had a big game",
                      "post_hoc": False, "suggested_tick": "cap at half"})
    elif cheap and own >= CHEAP_UNDER_OWN and gap <= CHEAP_UNDER_GAP:
        marks.append({"mark": "cheap and under what he needs (found after the fact)",
                      "rule": "under $5,500, 15%+ projected own, projected half a point or more under his break-even",
                      "record": "29 players, 1 big game", "post_hoc": True, "suggested_tick": None})
    if chalk and gap < 0 and bench:
        marks.append({"mark": "under his line and on the bench",
                      "rule": "projected under his break-even and Stokastic has him on the bench",
                      "record": "1 big game in 22", "post_hoc": False, "suggested_tick": None})
    if chalk and cheap and nobody_out and gap <= short_gap:
        marks.append({"mark": "fails both checks" + (": under his line, cheap, nobody out" if gap < 0 else ""),
                      "rule": f"under $5,500, nobody out on his team, projected no more than {short_gap:.1f} over his break-even",
                      "record": "1 big game in 26" if gap < 0 else "1 big game in 34",
                      "post_hoc": False, "suggested_tick": None})
    if chalk and cheap and gap >= clear_gap:
        marks.append({"mark": "cheap and clears",
                      "rule": f"under $5,500 and projected {clear_gap:.1f} or more over his break-even",
                      "record": "43% big games in 49", "post_hoc": False, "suggested_tick": None})
    if own >= CLEAR_MOST_OWN and gap >= CLEAR_MOST_GAP:
        marks.append({"mark": "clears by the most",
                      "rule": "25%+ projected own and projected 6 or more over his break-even",
                      "record": "52% big games in 29; leave him alone", "post_hoc": False, "suggested_tick": None})
    return marks


def cap_of(pct):
    """A cap as the build's cap box takes it (a percent of the set) and as lineups."""
    return {"cap_percent": pct, "lineups_of_150": int(round(pct / 100.0 * SET_BIG)),
            "lineups_of_20": int(round(pct / 100.0 * SET_SMALL))}


def player_ticks(own, suggest_half=False):
    """The ticks offered on a chalk line. Nothing starts ticked."""
    return [dict(tick="cap at half", suggested=bool(suggest_half), **cap_of(round(0.5 * own, 1))),
            dict(tick="cap at three-quarters", suggested=False, **cap_of(round(0.75 * own, 1))),
            dict(tick="out", suggested=False, **cap_of(0.0))]


def minimum_of(own):
    """The off-pool tick: held at his projected own = this many of the 150 (and of a 20)."""
    return max(1, int(round(own / 100.0 * SET_BIG))), max(1, int(round(own / 100.0 * SET_SMALL)))


# --- the old rules, as votes ------------------------------------------------------
def _box_view(side, r, outs_team):
    """Does the box score agree with a vote? side: 'against' (hold him down) or 'for'."""
    if not r:
        return "no game on record"
    over = BR.any_over(r)
    if side == "against":
        if over:
            return "box score disagrees: a minutes read is 3+ over Stokastic's"
        if outs_team and r.get("rE") is None:
            return "box score cannot tell: no earlier game with these teammates out"
        return "box score agrees"
    if over or (r.get("rD") is not None and r["rD"] >= r["mins"] - BR.OVER):
        return "box score agrees"
    if r.get("rA") is not None and r["mins"] - r["rA"] >= BR.OVER:
        return "box score disagrees: Stokastic has him 3+ minutes over his last three"
    return "box score has nothing either way"


def _votes(players, active, logs, history, games, today, ceil, inj, jk, BX):
    """{dk_id: [vote, ...]} and the takeover ids. A vote: {rule, side, word, box}."""
    out = collections.defaultdict(list)
    R, outs = BX["reads"], BX["outs"]
    NM = NB.normals(history, players)
    hits = NB.takeover(players, active, NM, inj)
    if not logs.get("days"):
        return out, set(hits), "box scores missing: the old rules that read them are not shown"
    M = RL.marks(active, set(hits), logs)
    by_name = {q.name: q for q in active}
    G = logs.get("games") or {}

    def _rotation(name, team):
        """15+ minutes a game over his last ten games for this team."""
        q = by_name.get(name)
        H = [x for x in G.get(jk(q), []) if x["team"] == team][-10:] if q else []
        return bool(H) and sum(x["mins"] for x in H) / len(H) >= FRINGE_MPG
    for p in active:
        m = M.get(normalize_name(p.name))
        if not m or p.ownership < RL.CHALK_OWN or m["lift"] or m["newStart"]:
            continue
        r = R.get(p.dk_id)
        why = []
        if m["bench"] and not (r and BR.any_over(r)):              # fix: no bench call with a minutes read over
            why.append("bench")
        back = [n for n in m.get("back") or [] if _rotation(n, p.team)]     # fix: a fringe teammate is never "back"
        if back:
            why.append("teammate back: " + ", ".join(back))
        if m["under22"]:
            why.append(f"projected {m['mins']:.0f} minutes")
        if why:
            out[p.dk_id].append({"rule": "weak chalk", "side": "against", "word": "; ".join(why),
                                 "box": _box_view("against", r, outs.get(p.team))})
    for p in BX["g21"]:
        out[p.dk_id].append({"rule": "owned bench player, role unchanged", "side": "against", "word": "bench",
                             "box": _box_view("against", R.get(p.dk_id), outs.get(p.team))})
    for p in BX["g20"]:
        out[p.dk_id].append({"rule": "part-time starter, starting tonight", "side": "for", "word": "starts",
                             "box": _box_view("for", R.get(p.dk_id), outs.get(p.team))})
    try:
        mu = MU.tonight(active, logs, history, today)
    except Exception:                                    # a vote is never worth losing the sheet over
        mu = {"ready": False, "calls": []}
    for c in mu.get("calls") or []:
        out[c["id"]].append({"rule": "easy matchup", "side": "for", "word": f"against {c['opp']}",
                             "box": "not a box-score call"})
    try:
        lev = LV.named_studs(players, active, logs, history, today, ceil or {}, games, set(hits))
    except Exception:
        lev = {"named": [], "pick": None}
    pick = (lev.get("pick") or {}).get("id")
    for c in lev.get("named") or []:
        out[c["id"]].append({"rule": "leverage stud", "side": "for",
                             "word": ("the pick: " if c["id"] == pick else "") + LV.why(c),
                             "box": "its own reasons are box-score reads" if any(k in c["reasons"] for k in ("avg", "hot", "beat", "ceil")) else "not a box-score call"})
    return out, set(hits), ""


# --- the sheet ---------------------------------------------------------------------
def chalk_sheet(players, active, logs, history, games, linestar, pool_names,
                today=None, ceil=None, inj=None, needs=None, notes=None, left_off=None):
    today = today or dt.date.today()
    T = needs if isinstance(needs, dict) else load_needs(needs)
    short_gap, clear_gap = SHORT_GAP, CLEAR_GAP      # frozen: the cuts the marks' records were counted at
    ls = linestar or {}
    pool = {normalize_name(x) for x in (pool_names or [])}
    in_pool = lambda p: bool(p.in_pool or normalize_name(p.name) in pool)
    has_pool = bool(pool) or any(p.in_pool for p in players)
    act_ids = {p.dk_id for p in active}
    if inj is None:
        inj = {normalize_name(p.name): "O" for p in players if p.dk_id not in act_ids}
    if isinstance(notes, str):
        notes = read_notes(notes)
    notes = notes or {}
    left_off = left_off or {}

    G = logs.get("games") or {}
    jk = RL._joiner(G)
    BX = BR.night(players, active, logs, jk)
    R, outs = BX["reads"], BX["outs"]
    votes, hits, vote_note = _votes(players, active, logs, history, games, today, ceil, inj, jk, BX)
    have_box = bool(logs.get("days"))

    cheap3 = {p.dk_id for p in sorted((q for q in active if q.salary < TOP_CHEAP_SAL and q.proj > 0),
                                      key=lambda q: (-q.ownership, q.name))[:TOP_CHEAP_N]}
    on_sheet = [p for p in active if p.ownership >= CHALK_OWN or votes.get(p.dk_id) or p.dk_id in cheap3]
    on_sheet.sort(key=lambda p: (-p.ownership, p.name))
    last_names = {p.dk_id: normalize_name(p.name).split()[-1] for p in on_sheet}

    lines = []
    for p in on_sheet:
        k = normalize_name(p.name)
        H = G.get(jk(p), [])
        r = R.get(p.dk_id)
        team_out = outs.get(p.team) or []
        nobody_out = have_box and not team_out
        nd, big = need(T, p.salary, p.ownership)
        sd = p.sd if (p.sd or 0) > 0 else 0.25 * p.proj
        gap = p.proj - nd
        chalk = p.ownership >= CHALK_OWN
        cheap = p.salary < CHEAP
        bench = p.starting is False
        lsown = (ls.get(k) or {}).get("own")

        marks = player_marks(p.ownership, p.salary, gap, p.starting, nobody_out, short_gap, clear_gap)
        heavy = any(m["suggested_tick"] for m in marks)
        ticks = player_ticks(p.ownership, heavy) if chalk else []

        if not have_box:
            box = "box scores missing"
        else:
            box = ("Out on his team: " + ", ".join(f"{o['name']} ({o['avg10']:.0f} min)" for o in team_out)
                   if team_out else "Nobody out on his team") + ". " + BR.line(p, r)
        nm_notes = [n for n in notes.get(p.team, []) if last_names[p.dk_id] in normalize_name(n).split()]
        n_games = len(H)
        lines.append({
            "name": p.name, "team": p.team, "opponent": p.opponent, "price": p.salary,
            "starting": "starting" if p.starting is True else "bench" if bench else "not said",
            "minutes": round(float(p.minutes or 0), 1), "in_pool": in_pool(p) if has_pool else None,
            "own": round(p.ownership, 1), "linestar_own": None if lsown is None else round(lsown, 1),
            "projection": round(p.proj, 1), "break_even": round(nd, 1), "gap": round(gap, 1),
            "big_game": round(big, 1), "std_dev": round(sd, 1),
            "std_dev_from": "Stokastic" if (p.sd or 0) > 0 else "a quarter of his projection (none in the file)",
            "chance_break_even": round(chance(nd, p.proj, sd), 2), "chance_big_game": round(chance(big, p.proj, sd), 2),
            "games_on_record": n_games,
            "reached_break_even": (f"{sum(1 for x in H if x['dk'] >= nd)} of {n_games} games" if n_games >= 5
                                   else "no game on record" if not n_games else f"only {n_games} game{'s' if n_games != 1 else ''} on record"),
            "reached_big_game": (f"{sum(1 for x in H if x['dk'] >= big)} of {n_games} games" if n_games >= 5 else None),
            "role": role_type([x for x in H if x["team"] == p.team]), "steady": steadiness([x for x in H if x["team"] == p.team]),
            "job_opened": p.dk_id in hits,
            "nobody_out": nobody_out if have_box else None, "out_on_team": [o["name"] for o in team_out],
            "box": box, "notes": nm_notes,
            "marks": marks, "votes": votes.get(p.dk_id, []),
            "votes_conflict": len({v["side"] for v in votes.get(p.dk_id, [])}) > 1,
            "ticks": ticks,
            "why_on_sheet": ("15%+ projected own" if chalk else "an old rule votes on him" if votes.get(p.dk_id)
                             else "one of the three most-owned under $5,000"),
        })

    # a small slate where nearly everyone is heavily owned (Nov 5: 12 players carried the heavy
    # mark on a two-game night). Past three players the mark stops separating, so the tick stays
    # suggested only for the cheap ones, the half of the kind that cost.
    crowd = [x for x in lines if any(m["suggested_tick"] for m in x["marks"])]
    crowded = len(crowd) > HEAVY_CROWD
    if crowded:
        for x in crowd:
            if x["price"] >= CHEAP:
                for m in x["marks"]:
                    if m["suggested_tick"]:
                        m["suggested_tick"] = None
                        m["record"] += f"; {len(crowd)} players carry this mark tonight, so the tick is suggested only for the cheap ones"
                for t in x["ticks"]:
                    t["suggested"] = False

    # the kind of night
    n_cheap = sum(1 for p in active if p.ownership >= CHALK_OWN and p.salary < CHEAP and have_box and not outs.get(p.team))
    low = have_box and n_cheap >= NIGHT_LINE
    night = {"cheap_chalk_nobody_out": n_cheap if have_box else None, "line": NIGHT_LINE, "low_owned_night": bool(low),
             "record": "right on 21 of 29 nights; always calling a chalk night is right on 19",
             "text": ("Box scores missing: the night read cannot be made." if not have_box else
                      f"{n_cheap} cheap chalk player{'s' if n_cheap != 1 else ''} with nobody out on their team. "
                      + ("5 or more: the winners have tended to come from UNDER the field's ownership on nights like this."
                         if low else "Under 5: a chalk night is the usual call.")
                      + " (Right on 21 of 29 nights; always calling a chalk night is right on 19.)")}
    n_chalk = sum(1 for x in lines if x["own"] >= CHALK_OWN)
    n_marked = sum(1 for x in lines if x["marks"])
    warn = [x for x in lines if any(m["mark"] != "cheap and clears" and m["mark"] != "clears by the most" for m in x["marks"])]
    slate = {"games": games, "chalk_players": n_chalk, "with_a_mark": n_marked, "with_a_warning_mark": len(warn),
             "suggested_ticks": sum(1 for x in lines for t in x["ticks"] if t["suggested"]),
             "text": f"{games} games. {n_chalk} chalk players (15%+ projected own). {n_marked} with a mark"
                     + (f", {len(warn)} of them a warning" if n_marked else "") + "."}

    # the off-pool list
    off = []
    if has_pool:
        offp = [p for p in active if not in_pool(p) and p.proj >= OFF_PROJ]
        A = sorted((p for p in offp if p.ownership >= OFF_OWN), key=lambda p: (-p.ownership, p.name))
        B = sorted((p for p in offp if p.ownership < OFF_OWN and ((ls.get(normalize_name(p.name)) or {}).get("own") or 0) >= OFF_LS_OWN),
                   key=lambda p: (-((ls.get(normalize_name(p.name)) or {}).get("own") or 0), p.name))
        studs = sorted((p for p in active if not in_pool(p) and p.proj >= OFF_STUD_PROJ and p.salary > OFF_STUD_SAL
                        and p.ownership < OFF_STUD_OWN), key=lambda p: (-p.proj, p.name))[:OFF_STUD_N]
        C = [p for p in studs if p.ownership < OFF_OWN]
        seen = set()
        for lst, which, why in ((A, "A", "not in your pool at 5%+ projected own"),
                                (B, "B", "under 5% by Stokastic and 10%+ by LineStar"),
                                (C, "C", "an off-pool stud (over $7,500) under 5% own, one of the three highest by projection")):
            for p in lst:
                if p.dk_id in seen:
                    continue
                seen.add(p.dk_id)
                k = normalize_name(p.name)
                lo = (ls.get(k) or {}).get("own")
                off.append({"name": p.name, "team": p.team, "price": p.salary, "list": which, "why": why,
                            "own": round(p.ownership, 1), "linestar_own": None if lo is None else round(lo, 1),
                            "projection": round(p.proj, 1),
                            "starting": "starting" if p.starting is True else "bench" if p.starting is False else "not said",
                            "box": BR.line(p, R.get(p.dk_id)) if have_box else "box scores missing",
                            "tick": "hold at his projected own",
                            "minimum_lineups_of_150": minimum_of(p.ownership)[0], "minimum_lineups_of_20": minimum_of(p.ownership)[1],
                            "left_off_on_purpose_before": int(left_off.get(k, 0))})
    tot = sum(r["minimum_lineups_of_150"] for r in off)
    off_pool = {"rows": off, "names": len(off),
                "all_ticked_lineup_spots": tot, "all_ticked_share_of_spots": round(tot / (SET_BIG * 8.0), 3),
                "biggest_minimum": max((r["minimum_lineups_of_150"] for r in off), default=0),
                "text": ("No pool typed: no off-pool list." if not has_pool else "Nobody on the off-pool list tonight." if not off else
                         f"{len(off)} off-pool names. A tick holds a player at his projected own in the 150 (a minimum, in lineups). "
                         f"All ticked: {tot} of the 150's 1,200 lineup spots.")}

    team_notes = {t: [n for n in ns if not any(n in x["notes"] for x in lines)]
                  for t, ns in notes.items() if t in {x["team"] for x in lines}}
    alerts = []
    if crowded:
        alerts.append(f"{len(crowd)} players are heavily owned and under what they need. On a slate this small the mark stops separating: the tick is suggested only for the ones under $5,500.")
    if not ls:
        alerts.append("No LineStar file: LineStar's projected own is blank and off-pool list B is empty.")
    if not have_box:
        alerts.append("Box scores missing: nobody-out, the box line, the old rules and the night read are blank.")
    if vote_note and have_box:
        alerts.append(vote_note)
    if ceil is None:
        alerts.append("Stokastic's ceilings were not passed: the leverage vote runs without its ceiling reason.")
    age = None
    try:
        age = (today - dt.date.fromisoformat(T.get("fitted_on"))).days
    except (TypeError, ValueError):
        pass
    if age is not None and age > 7:
        alerts.append(f"The break-even table was last fitted {age} days ago.")
    return {
        "version": 1, "date": today.isoformat(),
        "top": {"slate": slate, "night": night, "alerts": alerts,
                "needs_table": {"fitted_on": T.get("fitted_on"), "nights": T.get("nights"), "last_night": T.get("last_night")}},
        "lines": lines, "team_notes": {t: n for t, n in team_notes.items() if n},
        "off_pool": off_pool,
        "ticks_offered": {
            "none": "The default. Nothing starts ticked. No tick, no move.",
            "cap at half": "A cap at half his projected own, as a percent of the set. Suggested only on the mark 'heavily owned and under what he needs'. It applies to the 150, and on a 2 to 4 game slate to each 20 as well at the same percent (on 5+ games the 20s are taken from the 150).",
            "cap at three-quarters": "A cap at three-quarters of his projected own. Never suggested. Same sets as the cap at half.",
            "out": "A cap of zero. Never suggested.",
            "hold at his projected own": "Off-pool list only: a minimum of round(own x 150 / 100) lineups in the 150, at least 1. On a 2 to 4 game slate the minimum box is the same NUMBER in each 20, so the sheet shows it and the tool should scale it (round(own x 20 / 100), at least 1).",
        },
    }


# --- ticks -> the build's own boxes ---------------------------------------------------
def tick_plan(sheet, ticked, games=None):
    """ticked: {player name: tick}. -> the caps (percent of the set) and minimums (lineups) to put
    in the build's cap and minimum boxes, and how much of the 150 the ticks move, estimated
    from projected own before the build runs."""
    games = games if games is not None else sheet["top"]["slate"]["games"]
    by = {normalize_name(x["name"]): x for x in sheet["lines"]}
    offby = {normalize_name(x["name"]): x for x in sheet["off_pool"]["rows"]}
    caps, mins, small_mins, unknown, freed, added = {}, {}, {}, [], 0.0, 0
    for name, tick in (ticked or {}).items():
        k = normalize_name(name)
        if tick == "hold at his projected own" and k in offby:
            r = offby[k]
            mins[r["name"]] = r["minimum_lineups_of_150"]
            small_mins[r["name"]] = r["minimum_lineups_of_20"]
            added += r["minimum_lineups_of_150"]
            continue
        x = by.get(k)
        t = next((t for t in (x or {}).get("ticks", []) if t["tick"] == tick), None)
        if not t:
            unknown.append(f"{name}: {tick}")
            continue
        caps[x["name"]] = t["cap_percent"]
        freed += max(0.0, x["own"] - t["cap_percent"]) / 100.0 * SET_BIG
    moved = int(round(freed)) + added
    return {"caps_percent": caps, "caps_apply_to": "the 150" + (" and each 20" if games <= 4 else " (the 20s are taken from it)"),
            "minimums_lineups_150": mins, "minimums_lineups_20": small_mins if games <= 4 else {},
            "not_understood": unknown,
            "lineup_spots_freed_by_caps": int(round(freed)), "lineup_spots_held_by_minimums": added,
            "lineup_spots_moved": moved, "share_of_the_150s_spots": round(moved / (SET_BIG * 8.0), 3),
            "text": ("No ticks: the build is untouched." if not (caps or mins) else
                     f"Your ticks move about {moved} of the 150's 1,200 lineup spots "
                     f"({int(round(freed))} freed by caps, {added} held by minimums). "
                     "This is from projected own; the build shows the real before and after.")}


def tick_effect(before, after):
    """before / after: the 150 without and with the ticks, as lists of rosters (player names or
    Players). -> how many lineups changed, and each player's lineups before and after."""
    nm = lambda r: tuple(sorted(getattr(q, "name", q) for q in r))
    a, b = collections.Counter(nm(r) for r in before), collections.Counter(nm(r) for r in after)
    ca = collections.Counter(q for r in a.elements() for q in r)
    cb = collections.Counter(q for r in b.elements() for q in r)
    moves = sorted(((q, ca.get(q, 0), cb.get(q, 0)) for q in set(ca) | set(cb) if ca.get(q, 0) != cb.get(q, 0)),
                   key=lambda t: (-(t[2] - t[1]), t[0]))
    return {"lineups_changed": sum((a - b).values()), "of": sum(a.values()),
            "gained": [{"name": q, "before": x, "after": y} for q, x, y in moves if y > x][:8],
            "lost": [{"name": q, "before": x, "after": y} for q, x, y in reversed(moves) if y < x][:8]}


def saved_record(sheet, ticked=None, left_off_tonight=None, plan=None):
    """What the build should save with the night so the sheet can be graded."""
    ticked = ticked or {}
    tk = {normalize_name(k): v for k, v in ticked.items()}
    return {"sheet": sheet, "ticked": ticked, "plan": plan or tick_plan(sheet, ticked),
            "left_off_on_purpose": sorted(left_off_tonight or []),
            "suggested": [{"name": x["name"], "tick": t["tick"], "cap_percent": t["cap_percent"],
                           "taken": tk.get(normalize_name(x["name"])) == t["tick"]}
                          for x in sheet["lines"] for t in x["ticks"] if t["suggested"]],
            "marks": [{"name": x["name"], "marks": [m["mark"] for m in x["marks"]],
                       "ticked": tk.get(normalize_name(x["name"]))} for x in sheet["lines"] if x["marks"]]}


# --- plain text ------------------------------------------------------------------------
def sheet_text(sheet):
    L = [f"CHALK SHEET  {sheet['date']}", sheet["top"]["slate"]["text"], sheet["top"]["night"]["text"]]
    nt = sheet["top"]["needs_table"]
    L.append(f"Break-even table: fitted {nt['fitted_on']} on {nt['nights']} nights (through {nt['last_night']}).")
    L += ["! " + a for a in sheet["top"]["alerts"]]
    L.append("")
    for x in sheet["lines"]:
        pool = "" if x["in_pool"] is None else "" if x["in_pool"] else "  [not in your pool]"
        ls = "none" if x["linestar_own"] is None else f"{x['linestar_own']:.0f}%"
        L.append(f"{x['name']}  {x['team']}  ${x['price']:,}  {x['starting']}, {x['minutes']:.0f} min{pool}")
        L.append(f"   own {x['own']:.0f}% (LineStar {ls})   projected {x['projection']:.1f}   needs {x['break_even']:.1f} "
                 f"({x['gap']:+.1f})   big game {x['big_game']:.1f}")
        L.append(f"   chance of the break-even {100 * x['chance_break_even']:.0f}%, of a big game {100 * x['chance_big_game']:.0f}%"
                 f"   his games: {x['reached_break_even']}"
                 + (f" reached {x['break_even']:.1f}, {x['reached_big_game']} reached {x['big_game']:.1f}" if x["reached_big_game"] else ""))
        for m in x["marks"]:
            L.append(f"   MARK: {m['mark']} ({m['record']})" + ("  [found after the fact]" if m["post_hoc"] else "")
                     + (f"  -> suggested tick: {m['suggested_tick']}" if m["suggested_tick"] else ""))
        for v in x["votes"]:
            L.append(f"   old rule, shown only: {v['rule']} ({'for' if v['side'] == 'for' else 'against'}): {v['word']}. {v['box']}.")
        if x["votes_conflict"]:
            L.append("   the old rules point both ways on him")
        L.append(f"   {x['role']}" + (f", {x['steady']}" if x["steady"] else "") + (", job opened tonight" if x["job_opened"] else "") + f". {x['box']}")
        for n in x["notes"]:
            L.append(f"   note: {n}")
        if x["ticks"]:
            L.append("   ticks: " + "  |  ".join(f"[ ] {t['tick']}" + (f" = {t['cap_percent']:.1f}% ({t['lineups_of_150']} of 150)" if t["tick"] != "out" else "")
                                                 + (" (suggested)" if t["suggested"] else "") for t in x["ticks"]))
        L.append("")
    for t, ns in sheet["team_notes"].items():
        for n in ns:
            L.append(f"{t} note: {n}")
    L += ["", "OFF-POOL LIST", sheet["off_pool"]["text"]]
    for r in sheet["off_pool"]["rows"]:
        ls = "none" if r["linestar_own"] is None else f"{r['linestar_own']:.0f}%"
        L.append(f"[{r['list']}] {r['name']}  {r['team']}  ${r['price']:,}  own {r['own']:.0f}% (LineStar {ls})  projected {r['projection']:.1f}"
                 f"   [ ] hold at his projected own = {r['minimum_lineups_of_150']} of 150   [ ] left off on purpose"
                 + (f" (marked {r['left_off_on_purpose_before']} time{'s' if r['left_off_on_purpose_before'] != 1 else ''} before)" if r["left_off_on_purpose_before"] else ""))
        L.append(f"      {r['box']}")
    return "\n".join(L) + "\n"
