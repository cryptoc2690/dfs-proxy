"""The field's ownership, predicted (Predicting The Field report, Nov 13).

Stokastic's projected own misses real own by 2.1 points a player (4.9 on chalk).
The review's predictor took about 15% off that on nights it was not fitted on,
and named, before lock, players the field would take 3+ points over projected
own. Those reached projection 35% of the time; a cap at half their own was the
closest build to passing (ledger G9). Here that predictor runs on every build:

  - logged: every player's predicted own and the night's named over-drafts
    (3+ over) and under-drafts (3+ under), with the inputs, for grade_night to
    join to real own after the night;
  - a shadow build: the night's set with every named over-draft capped at half
    his projected own. Saved under logs/shadow/, never entered.

Nothing here changes the lineups that are entered.

The predictor is the review's (`Prototype/pf114.py`): boosted one-split trees
on the miss (real own minus Stokastic's), 80 rounds, learning rate 0.1, 25
players a leaf, each input cut at its deciles with "missing" as its own bin.
The review's model leaned on six inputs (99% of its gain); only those are
here, all known before lock:
  - LineStar's own minus Stokastic's (55% of the gain)
  - his own against what his value rank usually draws on this slate size (16%)
  - his last game minus tonight's projection (13%)
  - his last five games' mean minus tonight's projection (13%)
  - the takeover mark (2%)
  - a new starting job (under 1%)
ETR's own (under 1%) is left out: the tool takes no ETR file.

The model is fitted once and frozen in field_model.json:
    python3 field.py fit ["~/Downloads/NBA Results 1/Prototype/out/pf112.json"]
    python3 field.py check   # leave-one-night-out on the same rows
"""

import collections
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "field_model.json")
DEFAULT_ROWS = os.path.expanduser(
    "~/Downloads/NBA Results 1/Prototype/out/pf112.json")

FEATURES = ("ls_diff", "own_resid", "last_diff", "five_diff", "takeover", "newstart")
LABELS = {"ls_diff": "LineStar own minus Stokastic own",
          "own_resid": "own against his value rank",
          "last_diff": "last game minus projection",
          "five_diff": "last-five scoring minus projection",
          "takeover": "takeover mark", "newstart": "new starting job"}
ROUNDS, LR, MINLEAF = 80, 0.1, 25
VRANK_MAX = 40               # own against value rank: ranks 1-40 only
CALL_OVER = 3.0              # named over-draft: predicted 3+ points over projected own
CALL_UNDER = -3.0            # named under-draft: 3+ under
CALL_MIN_OWN = 5.0           # ... among players at 5%+ projected own
CAP_K = 0.5                  # the shadow: capped at half his projected own


def group_of(games):
    return "2-4" if games <= 4 else "5-7" if games <= 7 else "8+"


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


# --- the trees (pf114.py, one feature list) ----------------------------------
def _cuts(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return []
    qs = sorted({v[int(q * (len(v) - 1))] for q in [i / 9 for i in range(1, 9)]})
    return qs[:-1] if len(qs) > 1 and qs[-1] == v[-1] else qs


def _bin(x, cuts):
    return 0 if x is None else 1 + sum(1 for c in cuts if x > c)


def fit(rows, y):
    """rows: [{feature: value or None}], y: misses -> model dict."""
    cuts = {f: _cuts([r.get(f) for r in rows]) for f in FEATURES}
    B = [[_bin(r.get(f), cuts[f]) for f in FEATURES] for r in rows]
    NB_ = [len(cuts[f]) + 2 for f in FEATURES]
    idx = list(range(len(rows)))
    b0 = sum(y) / len(y)
    res = {i: y[i] - b0 for i in idx}
    trees, gain = [], collections.Counter()
    for _ in range(ROUNDS):
        best = None
        tot = sum(res.values())
        n = len(idx)
        for j in range(len(FEATURES)):
            s = [0.0] * NB_[j]
            c = [0] * NB_[j]
            for i in idx:
                b = B[i][j]
                s[b] += res[i]
                c[b] += 1
            sm, cm = s[0], c[0]
            sl = cl = 0.0
            for t in range(1, NB_[j] - 1):
                sl += s[t]
                cl += c[t]
                sr = tot - sm - sl
                cr = n - cm - cl
                if cl < MINLEAF or cr < MINLEAF:
                    continue
                g = sl * sl / cl + sr * sr / cr + (sm * sm / cm if cm else 0.0)
                if best is None or g > best[0]:
                    best = (g, j, t, sl / cl, sr / cr, (sm / cm) if cm >= MINLEAF else 0.0)
        if best is None:
            break
        g, j, t, vl, vr, vm = best
        trees.append([FEATURES[j], t, LR * vl, LR * vr, LR * vm])
        gain[FEATURES[j]] += g - tot * tot / n
        for i in idx:
            b = B[i][j]
            res[i] -= LR * (vm if b == 0 else vl if b <= t else vr)
    tg = sum(gain.values()) or 1.0
    return {"b0": b0, "cuts": cuts, "trees": trees,
            "gain": {f: round(100 * g / tg, 1) for f, g in gain.most_common()}}


def predict_miss(model, feats):
    p = model["b0"]
    for f, t, vl, vr, vm in model["trees"]:
        b = _bin(feats.get(f), model["cuts"][f])
        p += vm if b == 0 else vl if b <= t else vr
    return p


# --- the review's rows -> training rows --------------------------------------
def _review_rows(path):
    """pf112.json rows, filtered as pf114.py filters them."""
    with open(path, encoding="utf-8") as fh:
        R = json.load(fh)
    return [r for r in R if r.get("in_file") and r.get("sal")
            and (r["own"] >= 0.5 or r["real_mm"] >= 0.5)]


def value_rank_table(R, skip_date=None):
    """{group: {vrank: mean projected own}} over the rows' nights."""
    acc = collections.defaultdict(list)
    for r in R:
        if r["date"] != skip_date and r.get("vrank") and r["vrank"] <= VRANK_MAX:
            acc[(r["group"], r["vrank"])].append(r["own"])
    out = collections.defaultdict(dict)
    for (g, v), L in acc.items():
        out[g][str(v)] = sum(L) / len(L)
    return dict(out)


def _review_feats(r, table):
    ex = (table.get(r["group"]) or {}).get(str(r.get("vrank"))) \
        if r.get("vrank") and r["vrank"] <= VRANK_MAX else None
    return {"ls_diff": (r["ls_own"] - r["own"]) if r.get("ls_own") is not None else None,
            "own_resid": (r["own"] - ex) if ex is not None else None,
            "last_diff": (r["dk_last"] - r["proj"]) if r.get("dk_last") is not None else None,
            "five_diff": (r["dk5"] - r["proj"]) if r.get("dk5") is not None else None,
            "takeover": 1.0 if r.get("takeover") else 0.0,
            "newstart": 1.0 if (r.get("starting") and (r.get("n5") or 0) >= 3
                                and (r.get("starts5") or 0) <= 1) else 0.0}


def _train_feats(R):
    """pf114 reads each row's value-rank expectation from the OTHER nights."""
    tabs = {d: value_rank_table(R, d) for d in {r["date"] for r in R}}
    return [_review_feats(r, tabs[r["date"]]) for r in R]


def fit_from_review(path=DEFAULT_ROWS):
    R = _review_rows(path)
    table = value_rank_table(R)
    model = fit(_train_feats(R), [r["real_mm"] - r["own"] for r in R])
    order = lambda d: int(d.split("-")[0]) * 100 + int(d.split("-")[1])
    model.update(version="pf114-six-inputs", features=list(FEATURES),
                 nights=sorted({r["date"] for r in R}, key=order), rows=len(R),
                 ownByValueRank=table, callOver=CALL_OVER, callUnder=CALL_UNDER,
                 callMinOwn=CALL_MIN_OWN, capK=CAP_K)
    return model


def check(path=DEFAULT_ROWS):
    """Leave-one-night-out on the review's rows: the average miss, and the 3+
    over calls against the review's own (out/badchalk.json 'overdraft 3')."""
    R = _review_rows(path)
    dates = sorted({r["date"] for r in R})
    tabs = {d: value_rank_table(R, d) for d in dates}
    pm = {}
    for d in dates:
        tr = [r for r in R if r["date"] != d]
        m = fit(_train_feats(tr), [r["real_mm"] - r["own"] for r in tr])
        for r in R:
            if r["date"] == d:
                pm[id(r)] = predict_miss(m, _review_feats(r, tabs[d]))
        print(d, "fold done", flush=True)
    mae = lambda rs, f: _mean(abs(r["real_mm"] - f(r)) for r in rs)
    for lab, rs in (("all", R), ("chalk 20%+", [r for r in R if r["own"] >= 20]),
                    ("5-20%", [r for r in R if 5 <= r["own"] < 20])):
        print(f"{lab:11s} n {len(rs):4d} | Stokastic {mae(rs, lambda r: r['own']):.2f} | "
              f"predictor {mae(rs, lambda r: r['own'] + pm[id(r)]):.2f}")
    calls = {(r["date"], r["key"]) for r in R if pm[id(r)] >= CALL_OVER and r["own"] >= CALL_MIN_OWN}
    bc = os.path.join(os.path.dirname(path), "badchalk.json")
    theirs = set()
    if os.path.exists(bc):
        with open(bc, encoding="utf-8") as fh:
            for d, ks in (json.load(fh).get("overdraft 3") or {}).items():
                theirs |= {(d, k) for k in ks}
    print(f"3+ over calls: {len(calls)} here, {len(theirs)} in the review, "
          f"{len(calls & theirs)} the same")
    for d, k in sorted(calls ^ theirs):
        print("   ", d, k, "here only" if (d, k) in calls else "review only")


# --- tonight ------------------------------------------------------------------
_MODEL = None


def load_model():
    global _MODEL
    if _MODEL is None:
        try:
            with open(MODEL_PATH, encoding="utf-8") as fh:
                _MODEL = json.load(fh)
        except (OSError, ValueError):
            _MODEL = {}
    return _MODEL or None


def tonight(active, marks, linestar, takeover_ids, n_games, model=None):
    """active: tonight's buildable players. marks: role.marks() (dk5, dkLast,
    newStart by normalised name). linestar: read_linestar's dict (or {}).
    -> {"players": [...], "over": [...], "under": [...], "lineStar": bool}
    or None with no model. Every number is projected own in points."""
    from dk import normalize_name
    model = model or load_model()
    if not model:
        return None
    grp = group_of(n_games)
    table = (model.get("ownByValueRank") or {}).get(grp) or {}
    ranked = sorted((p for p in active if p.salary > 0 and p.proj > 0),
                    key=lambda p: -p.proj / p.salary)
    vrank = {p.dk_id: i + 1 for i, p in enumerate(ranked)}
    out = []
    for p in active:
        k = normalize_name(p.name)
        m = marks.get(k) or {}
        ls = (linestar.get(k) or {}).get("own") if linestar else None
        vr = vrank.get(p.dk_id)
        ex = table.get(str(vr)) if vr and vr <= VRANK_MAX else None
        f = {"ls_diff": (ls - p.ownership) if ls is not None else None,
             "own_resid": (p.ownership - ex) if ex is not None else None,
             "last_diff": (m["dkLast"] - p.proj) if m.get("dkLast") is not None else None,
             "five_diff": (m["dk5"] - p.proj) if m.get("dk5") is not None else None,
             "takeover": 1.0 if p.dk_id in takeover_ids else 0.0,
             "newstart": 1.0 if m.get("newStart") else 0.0}
        miss = predict_miss(model, f)
        out.append({"name": p.name, "id": p.dk_id, "team": p.team, "salary": p.salary,
                    "proj": round(p.proj, 2), "own": round(p.ownership, 2),
                    "lsOwn": ls, "vrank": vr,
                    "inputs": {a: (None if b is None else round(b, 2)) for a, b in f.items()},
                    "miss": round(miss, 2), "_raw": max(0.0, p.ownership + miss)})
    # the night's predicted own, rescaled to what Stokastic's own adds up to
    s = sum(r["_raw"] for r in out)
    s0 = sum(r["own"] for r in out)
    for r in out:
        r["pred"] = round(r.pop("_raw") * (s0 / s if s else 1.0), 2)
    out.sort(key=lambda r: -r["own"])
    mo = model.get("callMinOwn", CALL_MIN_OWN)
    over = [r for r in out if r["own"] >= mo and r["miss"] >= model.get("callOver", CALL_OVER)]
    under = [r for r in out if r["own"] >= mo and r["miss"] <= model.get("callUnder", CALL_UNDER)]
    return {"model": model.get("version"), "lineStar": bool(linestar),
            "players": out, "over": over, "under": under}


def shadow_caps(calls, n, k=None):
    """{dk_id: lineups} — each named over-draft capped at k x his projected own."""
    k = CAP_K if k is None else k
    return {r["id"]: int(round(k * r["own"] / 100.0 * n)) for r in calls}


def main(argv):
    if not argv or argv[0] not in ("fit", "check"):
        print(__doc__)
        return 2
    path = os.path.expanduser(argv[1]) if len(argv) > 1 else DEFAULT_ROWS
    if argv[0] == "check":
        check(path)
        return 0
    model = fit_from_review(path)
    with open(MODEL_PATH, "w", encoding="utf-8") as fh:
        json.dump(model, fh, indent=1)
    print(f"{MODEL_PATH}: {len(model['trees'])} trees on {model['rows']} players, "
          f"{len(model['nights'])} nights; gain {model['gain']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
