"""How well each signal tells a draw from a dispense, on the labelled actions.

    $BENCH_PY -m vision.eval.dispense_signals [--split tune|hold|all] [--rows]

Replays the --new-draw tracker from the cache and keeps every group of entries the sequencer
names (before naming). Each group gets the kind of the labelled draw or dispense it overlaps
(1.5 s either side), or "none". For the groups that are a real draw or dispense it prints, per
signal, the draw and dispense medians and how often a threshold at the midpoint puts a group on
the right side (0.5 is no help).
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from ..core import TIP_BOX, dist
from ..pipeline import Vision
from .replay import frames, raw_boxes
from .score_actions import TOL, streams
from ..core import Hand

HERE = Path(__file__).parent


def truth(g0, g1, acts):
    best = None
    for a in acts:
        if a["kind"] not in ("draw", "dispense", "mix"):
            continue
        ov = min(g1, a["end"] + TOL) - max(g0, a["start"] - TOL)
        if ov >= 0 and (best is None or ov > best[0]):
            best = (ov, a["kind"])
    return best[1] if best else "none"


def groups_for(labels, names, rules=None):
    """Every named group on one stream, with the signals of its entries."""
    fs = [frames(labels["clips"][n]["path"], 0.0, labels["clips"][n]["seconds"]) for n in names]
    vis = Vision(len(fs), True, draw=rules)
    seen = []
    orig = vis.seq._name

    def spy(g):
        seen.append((g, vis.seq.loaded, vis.seq.since_tip if hasattr(vis.seq, "since_tip") else None))
        return orig(g)
    vis.seq._name = spy
    timeline = sorted((f["t"], cam, i) for cam, fl in enumerate(fs) for i, f in enumerate(fl))
    for t, cam, i in timeline:
        fr = fs[cam][i]
        hands = [Hand(h["side"], [tuple(p) for p in h["pts"]]) for h in fr["hands"]]
        vis.step(cam, t, fr["w"], fr["h"], hands, raw_boxes(fr, True))
    vis.finish()
    return seen


def rows(labels, split):
    out = []
    for sname, names in streams(labels, split) if split != "all" else streams(labels, "tune") + streams(labels, "hold"):
        acts = [a for a in labels["actions"] if a["clip"] == names[0]]
        prev = None  # previous non-tip-box group
        last_draw_xy = {}
        for g, loaded, _ in groups_for(labels, names):
            es = list(g.by_cam.values())
            if any(e.label == TIP_BOX for e in es):
                prev, last_draw_xy = None, {}
                continue
            e = max(es, key=lambda e: e.score)
            k = truth(g.t0, g.t1, acts)
            th = [x for x in es if x.thumb]
            r = {"stream": sname, "t0": round(g.t0, 2), "t1": round(g.t1, 2), "truth": k, "named": "dispense" if loaded else "draw",
                 "dwell": round(max(x.t1 - x.t0 for x in es), 2), "label": e.label, "cams": len(es),
                 "pressed": any(x.pressed for x in es), "rise": max(x.rise for x in es),
                 "lean": min(x.lean for x in es), "down": any(x.down for x in es),
                 "thumb_min": min((x.thumb_min for x in th), default=None),
                 "thumb_change": (th[0].thumb[1] - th[0].thumb[0]) if th else None,
                 "gap_prev": round(g.t0 - prev[0], 2) if prev else None,
                 # same place as the previous group (in container sizes), same camera
                 "moved_prev": (round(min(dist(x.xy, prev[1][x.cam].xy) / max(x.size, 1.0) for x in es if x.cam in prev[1]), 2)
                                if prev and any(x.cam in prev[1] for x in es) else None)}
            out.append(r)
            prev = (g.t1, {x.cam: x for x in es})
    return out


def separation(rs, key):
    d = [r[key] for r in rs if r["truth"] == "draw" and r[key] is not None]
    p = [r[key] for r in rs if r["truth"] == "dispense" and r[key] is not None]
    if len(d) < 2 or len(p) < 2:
        return None
    d, p = [float(x) for x in d], [float(x) for x in p]
    md, mp = statistics.median(d), statistics.median(p)
    cut = (md + mp) / 2
    hi_is_disp = mp > md
    right = sum((x > cut) == hi_is_disp for x in p) + sum((x > cut) != hi_is_disp for x in d)
    return {"n_draw": len(d), "n_dispense": len(p), "draw_median": round(md, 3), "dispense_median": round(mp, 3),
            "cut": round(cut, 3), "right_share": round(right / (len(d) + len(p)), 2)}


SIGNALS = ("dwell", "rise", "lean", "thumb_min", "thumb_change", "pressed", "down", "moved_prev", "gap_prev", "cams")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="tune")
    ap.add_argument("--rows", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    labels = json.loads((HERE / "actions.json").read_text())
    rs = rows(labels, a.split)
    if a.rows:
        for r in rs:
            print(r)
    kinds = {k: sum(r["truth"] == k for r in rs) for k in ("draw", "dispense", "mix", "none")}
    named_right = sum(r["named"] == r["truth"] for r in rs if r["truth"] in ("draw", "dispense"))
    print(f"[{a.split}] {len(rs)} groups: {kinds}; alternation names {named_right} of "
          f"{kinds['draw'] + kinds['dispense']} real ones right")
    res = {}
    for k in SIGNALS:
        res[k] = s = separation([{**r, k: (float(r[k]) if isinstance(r[k], bool) else r[k])} for r in rs], k)
        print(f"  {k:13s} {s}")
    if a.json:
        Path(a.json).write_text(json.dumps({"groups": rs, "signals": res}, indent=1))


if __name__ == "__main__":
    main()
