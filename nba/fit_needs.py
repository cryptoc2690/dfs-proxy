"""fit_needs.py: fits the two tables the chalk sheet reads (needs.json).

    python fit_needs.py                       refit from every saved night, write needs.json beside this file
    python fit_needs.py --before 11-19        only nights before Nov 19 (what the tool had that morning)
    python fit_needs.py --out some/file.json  write somewhere else

It reads ONLY the tool's saved nights: the folder newbuild.HISTORY_DIR
(`NBA Stokastic Projections Main M-D.csv`, or the Data Hub export of the same
night, and `NBA Contest Standing Main mini-MAX M-D.csv`) and, when it is there,
the tool's own ESPN box-score folder (logs/espn) to tell who played. Nothing
from the harness. Plain Python. The first run takes about ten minutes (the search
for each night's 50 best lineups); every night's rows are then kept in
needs_cache.json, so a morning refit works out only the new night (10 seconds to
3 minutes) and the fit itself is instant.

The two tables, in plain words:

  break-even   The score at which holding a player stops costing the field
               money. For every earlier night each entry of the mini-MAX is
               paid by DK's ladder; for every player, the entries holding him
               are set against the entries without him. Inside a band of price
               and of projected ownership, the gap (with him less without him,
               per $1) is fitted as a straight line against his score in points
               per $1,000 of price. The break-even is where that line crosses
               zero. Saved as a multiple of his price: need = multiple x price
               / 1000.

  big game     The score a lineup at the top needs from him: the lowest multiple
               of his price at which a player of that price was in 10 or more
               of the night's 50 best possible lineups at least half the time.
               By price band only. big = multiple x price / 1000.

Bands: price $3,000-4,499 / 4,500-5,499 / 5,500-6,999 / 7,000-8,999 / 9,000+;
projected own 5-15% / 15-25% / 25-40% / 40%+ (a player under 5% reads the 5%
band). A band with fewer than 8 player-nights, or whose line slopes the wrong
way, takes the fit of every 15%+ player at that price; failing that 5.2.
"""
import csv, datetime as dt, glob, heapq, json, os, re, sys, collections

if os.environ.get("NBA_TOOL"):                      # the review ran this against a copy of the tool
    sys.path.insert(0, os.environ["NBA_TOOL"])
try:
    from dk import normalize_name
except ImportError:                                  # stand-alone: the same folding the tool does
    import unicodedata

    def normalize_name(name):
        n = unicodedata.normalize("NFKD", name or "")
        n = "".join(c for c in n if not unicodedata.combining(c))
        n = re.sub(r"[.'’\-,]", "", n.lower())
        n = re.sub(r"\s+", " ", n).strip()
        return re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n).strip()

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.environ.get("NBA_HISTORY") or os.path.expanduser("~/Downloads/NBA Results 1/Main")
ESPN_DIR = os.environ.get("NBA_ESPN") or os.path.join(os.environ.get("NBA_TOOL") or HERE, "logs", "espn")

PRICE_BANDS = (3000, 4500, 5500, 7000, 9000)
OWN_BANDS = (5, 15, 25, 40)
MIN_ROWS = 8                    # a band needs this many player-nights
DEFAULT_EVEN, DEFAULT_BIG = 5.2, 8.0
MIN_PROJ = 10.0                 # player-nights projected 10+ go into the fit
BIG_STEPS = (5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5, 9)
BEST_N, BEST_IN = 50, 10        # "in 10 or more of the night's 50 best possible lineups"
VERSION = 1                     # raise it to throw the cache away after a change to night_rows()

# DK's mini-MAX payout table of Oct 20 (71,343 entries, $30,000, $0.50): rank from, rank to, prize.
# Every night's ladder is this shape scaled to the night's field (see ladder()).
BASE = {"fee": 0.5, "pool": 30000.0, "entries": 71343, "paid": 14980, "ladder": [
    [1, 1, 3000], [2, 2, 1500], [3, 3, 750], [4, 4, 500], [5, 5, 250], [6, 6, 200], [7, 8, 150], [9, 10, 100],
    [11, 14, 75], [15, 19, 60], [20, 25, 50], [26, 35, 40], [36, 45, 30], [46, 55, 25], [56, 70, 20],
    [71, 90, 15], [91, 120, 10], [121, 160, 8], [161, 210, 6], [211, 345, 5], [346, 615, 4],
    [616, 1175, 3], [1176, 2545, 2], [2546, 5185, 1.5], [5186, 14980, 1]]}
POOL_PER_ENTRY = BASE["pool"] / BASE["entries"]     # $0.4205: the pool is the field x this, to the nearest $5,000


def pband(sal):
    return max([b for b in PRICE_BANDS if sal >= b] or [PRICE_BANDS[0]])


def oband(own):
    return max([b for b in OWN_BANDS if own >= b] or [OWN_BANDS[0]])


def _f(v, d=0.0):
    try:
        return float(str(v).replace("%", "").replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return d


def _season_key(month, day):
    return (month if month >= 7 else month + 12, day)


_DATE = re.compile(r"(\d{1,2})-(\d{1,2})(?:\D|$)")


def nights(folder=None, before=None):
    """[(season key, 'M-D', projections path, mini-MAX standings path)], oldest first: every night
    with both files. before: 'M-D', only earlier nights. Which projections file when a night has
    several: the plain export named for the date, else its latest numbered pull (' 2' before
    ' 1'), else the Data Hub export of that night."""
    folder = folder or HISTORY_DIR
    cut = _season_key(*map(int, before.split("-"))) if before else None
    found = {}
    for pat, rank in (("NBA Stokastic Projections Main *.csv", 0), ("NBA Stokastic Data Hub Projections Main *.csv", 1)):
        for path in sorted(glob.glob(os.path.join(folder, pat))):
            tail = os.path.basename(path).split("Main", 1)[1]
            m = _DATE.search(tail)
            if not m:
                continue
            date = f"{int(m.group(1))}-{int(m.group(2))}"
            key = _season_key(int(m.group(1)), int(m.group(2)))
            if cut and key >= cut:
                continue
            rest = tail[m.end(2):].replace(".csv", "").strip()
            pull = int(rest) if rest.isdigit() else 0 if not rest else -1      # '' exact, '2', '1', or an odd name
            pref = (rank, 0 if not rest else 1 if pull > 0 else 2, -pull)
            st = os.path.join(folder, f"NBA Contest Standing Main mini-MAX {date}.csv")
            if os.path.exists(st) and (date not in found or pref < found[date][4]):
                found[date] = (key, date, path, st, pref)
    return [v[:4] for v in sorted(found.values())]


def read_proj(path):
    """{name key: {name, sal, pos, proj, own}} from a Stokastic projections file."""
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            k = normalize_name(r.get("Player") or r.get("Name") or "")
            if not k:
                continue
            pc = "Projected FP" if "Projected FP" in r else "Projection"
            out[k] = {"name": r.get("Player") or r.get("Name"), "sal": _f(r.get("Salary")),
                      "pos": (r.get("Position") or "").strip(), "proj": _f(r.get(pc)),
                      "own": _f(r.get("Ownership %")), "team": (r.get("Team") or "").strip()}
    return out


_LU = re.compile(r"(?:^|\s)(?:PG|SG|SF|PF|UTIL|C|G|F)\s")


def read_standings(path):
    """-> (number of entries, [(points, [name keys])] for entries with a lineup, {name key: DK points})"""
    n, ents, pts = 0, [], {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))[1:]
    for r in rows:
        if len(r) > 10 and r[7].strip():
            pts.setdefault(normalize_name(r[7]), _f(r[10]))
        if len(r) > 5 and r[1].strip():
            n += 1
            if r[5].strip():
                names = [normalize_name(x) for x in (p.strip() for p in _LU.split(" " + r[5].strip() + " ")) if x]
                ents.append((_f(r[4]), names))
    return n, ents, pts


def ladder(n):
    """The night's prize for each place, from the Oct 20 table: the same share of the field is paid,
    the tail keeps its multiple of the fee, the top keeps its share of the pool, and the part above
    the smallest prize is scaled so the table adds up to the pool. The pool is the field x $0.4205
    to the nearest $5,000 (true on all 29 saved nights). -> cumulative sums by place."""
    N0, X0, P0 = BASE["entries"], BASE["pool"], BASE["paid"]
    X = 5000.0 * round(n * POOL_PER_ENTRY / 5000.0)

    def base(r):
        for a, b, p in BASE["ladder"]:
            if a <= r <= b:
                return float(p)
        return 0.0
    P = int(round(n * P0 / N0))
    mn = float(BASE["ladder"][-1][2])
    pr = [max(base(min(P0, max(1, -(-r * N0 // n)))), base(r) * X / X0, mn) for r in range(1, P + 1)]
    T = sum(pr)
    k = (X - P * mn) / (T - P * mn) if T > P * mn else 1.0
    cs = [0.0]
    for p in pr:
        cs.append(cs[-1] + mn + (p - mn) * k)
    return cs


def pay(cs, points):
    """Each entry's prize, ties splitting the places they cover."""
    paid = len(cs) - 1
    order = sorted(range(len(points)), key=lambda i: -points[i])
    out = [0.0] * len(points)
    i, n = 0, len(points)
    while i < n:
        j = i
        while j + 1 < n and points[order[j + 1]] == points[order[i]]:
            j += 1
        a, b = i + 1, j + 1
        v = 0.0 if a > paid else (cs[min(b, paid)] - cs[a - 1]) / (b - a + 1)
        for t in range(i, j + 1):
            out[order[t]] = v
        i = j + 1
    return out


_ELIG = {"PG": {"PG"}, "SG": {"SG"}, "SF": {"SF"}, "PF": {"PF"}, "C": {"C"}, "G": {"PG", "SG"},
         "F": {"SF", "PF"}, "UTIL": {"PG", "SG", "SF", "PF", "C"}}


def best_lineups(players, N=BEST_N, cap=50000):
    """players: [(key, salary, points, {positions})] -> the N best possible rosters of the night
    under DK's cap and slots, [(points, (keys))]. Depth-first with a points bound; each slot is
    limited to its best scorers and best values."""
    order = ["C", "PG", "SG", "SF", "PF", "G", "F", "UTIL"]
    cand = {}
    for s in order:
        el = sorted([p for p in players if p[3] & _ELIG[s]], key=lambda p: -p[2])
        K = 14 if s in ("G", "F") else 18 if s == "UTIL" else 12
        val = sorted(el, key=lambda p: -p[2] / p[1])[:K]
        cand[s] = sorted({p[0]: p for p in el[:K] + val}.values(), key=lambda p: -p[2])
    minsal = min(p[1] for p in players)
    best, seen = [], set()

    def bound(i, used):
        b = 0.0
        for s in order[i:]:
            for p in cand[s]:
                if p[0] not in used:
                    b += p[2]
                    break
        return b

    def dfs(i, used, sal, pts, roster):
        if i == 8:
            fs = frozenset(roster)
            if fs in seen:
                return
            seen.add(fs)
            if len(best) < N:
                heapq.heappush(best, (pts, tuple(sorted(roster))))
            elif pts > best[0][0]:
                heapq.heapreplace(best, (pts, tuple(sorted(roster))))
            return
        thr = best[0][0] if len(best) >= N else -1.0
        if pts + bound(i, used) <= thr:
            return
        s = order[i]
        left = 7 - i
        for p in cand[s]:
            if p[0] in used or sal + p[1] + left * minsal > cap:
                continue
            used.add(p[0])
            roster.append(p[0])
            dfs(i + 1, used, sal + p[1], pts + p[2], roster)
            roster.pop()
            used.discard(p[0])
    dfs(0, set(), 0, 0.0, [])
    return sorted(best, reverse=True)


def _played(date_md, year_of):
    """{name key} of everyone ESPN has with minutes that night, or None when the tool has no
    box-score file for it (then 'played' is 'scored more than 0')."""
    m, d = map(int, date_md.split("-"))
    path = os.path.join(ESPN_DIR, f"day_{year_of(m)}{m:02d}{d:02d}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    out = set()
    for g in rec.get("games") or []:
        for r in g.get("rows") or []:
            if not r.get("dnp") and (r.get("mins") or 0) > 0:
                out.add(normalize_name(r.get("name") or r.get("key") or ""))
                out.add(r.get("key") or "")
    return out


def night_rows(date, ppath, spath, year_of):
    """One night's player rows for the fit: projected 10+, played, held by some entries and not by all."""
    P = read_proj(ppath)
    n_all, ents, pts = read_standings(spath)
    if not ents:
        return []
    prizes = pay(ladder(n_all), [e[0] for e in ents])
    n = len(ents)
    fee = BASE["fee"]
    hold, won = collections.Counter(), collections.Counter()
    for (_p, names), z in zip(ents, prizes):
        for x in names:
            hold[x] += 1
            won[x] += z
    tot = sum(prizes)
    sol = [(k, int(v["sal"]), pts[k], set(v["pos"].split("/")) - {""}) for k, v in P.items()
           if v["sal"] and pts.get(k)]
    sol = [q for q in sol if q[2] >= 8]
    in50 = collections.Counter(k for _pts, ros in best_lineups(sol) for k in ros) if len(sol) >= 8 else {}
    box = _played(date, year_of)
    rows = []
    for k, v in P.items():
        if v["proj"] < MIN_PROJ or not v["sal"] or k not in pts:
            continue
        real = pts[k]
        played = (k in box) if box is not None else real > 0
        if box is not None and not played and real > 0:      # a name ESPN spells another way
            played = True
        h, w = hold.get(k, 0), won.get(k, 0.0)
        if not played or not h or n <= h:
            continue
        rows.append({"date": date, "key": k, "sal": v["sal"], "own": v["own"], "proj": v["proj"],
                     "real": real, "mult": real / v["sal"] * 1000.0,
                     "gap": w / h / fee - (tot - w) / (n - h) / fee, "in50": in50.get(k, 0)})
    return rows


def _line(S):
    """Least squares of the with-less-without gap on the score multiple -> (intercept, slope) or None."""
    if len(S) < MIN_ROWS:
        return None
    mx = sum(r["mult"] for r in S) / len(S)
    my = sum(r["gap"] for r in S) / len(S)
    sxx = sum((r["mult"] - mx) ** 2 for r in S)
    b1 = sum((r["mult"] - mx) * (r["gap"] - my) for r in S) / sxx if sxx else 0.0
    return (my - b1 * mx, b1) if b1 > 0 else None


def fit_tables(rows):
    even, src, cnt, big, bign = {}, {}, {}, {}, {}
    for o in OWN_BANDS:
        for p in PRICE_BANDS:
            cell = [r for r in rows if oband(r["own"]) == o and pband(r["sal"]) == p and r["own"] >= 5]
            f1 = _line(cell)
            f2 = None if f1 else _line([r for r in rows if pband(r["sal"]) == p and r["own"] >= 15])
            f = f1 or f2
            key = f"{o}|{p}"
            even[key] = round(-f[0] / f[1], 4) if f else DEFAULT_EVEN
            src[key] = "this band" if f1 else "every 15%+ player at this price" if f2 else "default"
            cnt[key] = len(cell)
    for p in PRICE_BANDS:
        Q = [r for r in rows if pband(r["sal"]) == p]
        got = None
        for a in BIG_STEPS:
            W = [r for r in Q if a <= r["mult"] < a + 1]
            if len(W) >= MIN_ROWS and sum(1 for r in W if r["in50"] >= BEST_IN) / len(W) >= 0.5:
                got = a
                break
        big[str(p)] = got or DEFAULT_BIG
        bign[str(p)] = len(Q)
    return even, src, cnt, big, bign


def fit(folder=None, before=None, today=None, verbose=False):
    today = today or dt.date.today()
    # a night's year: Oct-Dec belong to the season's first year, Jan-Jun to the next
    first = today.year if today.month >= 7 else today.year - 1
    year_of = lambda m: first if m >= 7 else first + 1
    ns = nights(folder, before)
    # A finished night never changes, and the 50-best-lineups search takes up to three minutes a
    # night, so each night's rows are kept in needs_cache.json and only a new night is worked out.
    cpath = os.environ.get("NBA_NEEDS_CACHE") or os.path.join(HERE, "needs_cache.json")
    try:
        with open(cpath, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    rows, dirty = [], False
    for _key, date, ppath, spath in ns:
        stamp = [os.path.basename(ppath), os.path.getsize(ppath), os.path.basename(spath), os.path.getsize(spath), VERSION]
        rec = cache.get(date)
        if not rec or rec.get("stamp") != stamp:
            rec = {"stamp": stamp, "rows": night_rows(date, ppath, spath, year_of)}
            cache[date] = rec
            dirty = True
            if verbose:
                print(f"  {date}: {len(rec['rows'])} player-nights", flush=True)
        rows += rec["rows"]
    if dirty:
        try:
            with open(cpath, "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
        except OSError:
            pass
    even, src, cnt, big, bign = fit_tables(rows)
    # the thirds of the gap (projection less break-even) among chalk, 15%+ projected own and
    # projected 10+: "short" is the bottom third, "clears" the top third. Pre-lock numbers only.
    tb = {"break_even_multiple": even, "big_game_multiple": big}
    gaps = sorted(v["proj"] - need(tb, v["sal"], v["own"])[0]
                  for _key, _date, ppath, _spath in ns for v in read_proj(ppath).values()
                  if v["own"] >= 15 and v["proj"] >= MIN_PROJ and v["sal"])
    thirds = [round(gaps[len(gaps) // 3], 2), round(gaps[2 * len(gaps) // 3], 2)] if len(gaps) >= 30 else [0.93, 2.85]
    return {
        "gap_thirds_refit": thirds, "chalk_player_nights": len(gaps),
        "what": {
            "break_even": "The score at which holding him stops costing the field money, as a multiple of his price: need = multiple x price / 1000. Read by price band and projected-ownership band.",
            "big_game": "The score a lineup at the top needs from him, as a multiple of his price: the lowest multiple at which a player of that price was in 10+ of the night's 50 best possible lineups at least half the time. Read by price band.",
            "gap_thirds_refit": "For watching only. Among chalk (15%+ projected own), the gap between projection and break-even at the bottom third and the top third on the nights fitted. The sheet's own cuts for 'short' and 'clears' are frozen at 0.93 and 2.85, the cuts its records were counted at.",
            "refit": "Every morning after the standings are saved. It reads only the saved nights.",
        },
        "fitted_on": today.isoformat(), "nights": len(ns), "first_night": ns[0][1] if ns else None,
        "last_night": ns[-1][1] if ns else None, "before": before, "player_nights": len(rows),
        "price_bands": list(PRICE_BANDS), "own_bands": list(OWN_BANDS),
        "break_even_multiple": even, "break_even_from": src, "break_even_player_nights": cnt,
        "big_game_multiple": big, "big_game_player_nights": bign,
        "default_break_even": DEFAULT_EVEN, "default_big_game": DEFAULT_BIG,
    }


def need(tables, salary, own):
    """(break-even points, big-game points) for a player at this price and projected own."""
    e = tables["break_even_multiple"].get(f"{oband(own)}|{pband(salary)}", tables.get("default_break_even", DEFAULT_EVEN))
    b = tables["big_game_multiple"].get(str(pband(salary)), tables.get("default_big_game", DEFAULT_BIG))
    return e * salary / 1000.0, b * salary / 1000.0


if __name__ == "__main__":
    a = sys.argv[1:]
    before = a[a.index("--before") + 1] if "--before" in a else None
    out = a[a.index("--out") + 1] if "--out" in a else os.path.join(HERE, "needs.json")
    today = dt.date.fromisoformat(a[a.index("--today") + 1]) if "--today" in a else None
    T = fit(before=before, today=today, verbose=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(T, fh, indent=1)
    print(f"{T['nights']} nights ({T['first_night']} to {T['last_night']}), {T['player_nights']} player-nights -> {out}")
