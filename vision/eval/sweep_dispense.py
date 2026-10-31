"""Try the naming checks one setting at a time and together, pick on the tuning clips only.

    $BENCH_PY -m vision.eval.sweep_dispense [--json out.json]

Score for picking: dispense F1 on the tuning clips, as long as draw F1 there does not drop by
more than 0.02 from the current default. The held-out numbers are printed next to each setting
but are not used to pick.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

from .score_actions import HERE, score


def f1(x):
    p, r = x["precision"] or 0.0, x["recall"] or 0.0
    return round(2 * p * r / (p + r), 3) if p + r else 0.0


def run(labels, settings):
    out = {}
    for split in ("tune", "hold"):
        nd = score(labels, split, settings, tries=50)["new-draw"]
        out[split] = {k: {**nd[k], "f1": f1(nd[k])} for k in ("draw", "dispense")}
    return out


GRID = [{}]
GRID += [{"merge_gap": g} for g in (0.2, 0.4, 0.6, 0.8, 1.0)]
GRID += [{"merge_gap": g, "merge_move": m} for g in (0.4, 0.6, 1.0) for m in (0.5, 1.0, 2.0)]
GRID += [{"same_place": m} for m in (0.25, 0.5, 0.75, 1.0)]
GRID += [{"merge_gap": g, "same_place": m} for g, m in itertools.product((0.4, 0.6), (0.25, 0.5))]
GRID += [{"by_place": m, "follow_source": f} for m in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0) for f in (False, True)]
GRID += [{"disp_near": m} for m in (0.75, 1.0, 1.5, 2.0, 3.0)]
GRID += [{"disp_min_gap": g} for g in (0.6, 1.0, 1.5, 2.0)]
GRID += [{"disp_min_dwell": d} for d in (0.4, 0.5, 0.7)]
GRID += [{"disp_near": m, "disp_min_gap": g} for m in (1.5, 2.0, 3.0) for g in (0.6, 1.0)]
GRID += [{"disp_near": m, "disp_min_dwell": d} for m in (1.5, 2.0, 3.0) for d in (0.4, 0.5)]
GRID += [{"near_last": m} for m in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)]


def line(s, r):
    t, h = r["tune"], r["hold"]
    return (f"{json.dumps(s):40s} tune draw {t['draw']['right']}/{t['draw']['fired']} f1 {t['draw']['f1']:.2f}  "
            f"disp {t['dispense']['right']}/{t['dispense']['fired']} of {t['dispense']['labelled']} f1 {t['dispense']['f1']:.2f}  | "
            f"hold draw {h['draw']['right']}/{h['draw']['fired']} f1 {h['draw']['f1']:.2f}  "
            f"disp {h['dispense']['right']}/{h['dispense']['fired']} of {h['dispense']['labelled']} f1 {h['dispense']['f1']:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    labels = json.loads((HERE / "actions.json").read_text())
    res = [(s, run(labels, s)) for s in GRID]
    base = res[0][1]["tune"]["draw"]["f1"]
    for s, r in res:
        print(line(s, r))
    ok = [(s, r) for s, r in res if r["tune"]["draw"]["f1"] >= base - 0.02]
    pick = max(ok, key=lambda sr: (sr[1]["tune"]["dispense"]["f1"], sr[1]["tune"]["draw"]["f1"]))
    print("picked on tuning:", json.dumps(pick[0]))
    if a.json:
        Path(a.json).write_text(json.dumps({"grid": [{"settings": s, **r} for s, r in res], "picked": pick[0]}, indent=1))


if __name__ == "__main__":
    main()
