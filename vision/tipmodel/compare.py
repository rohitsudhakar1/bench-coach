"""Compare the tip model with Grounding DINO's pipette boxes on held-out clips only.

    $BENCH_PY -m vision.tipmodel.compare [--json vision/eval/tipmodel-compare.json]

1. Frames: the held-out frames of vision/eval/labels.json, both detectors run on the same image.
   - found: a pipette box whose centre is on the labelled pipette (30 px of slack, as score.py).
   - wrong boxes: pipette and pipette tip boxes judged by score.py's rule.
   - tip end error: pixels from vision/eval/tipends.json to the tip end the live code would take
     (events.tip_end, holding hand from the cached MediaPipe points) on the box on the pipette.
   - speed: milliseconds a frame on this machine, after a warm-up.
2. Live code: the cached held-out clips replayed through `Vision` with Grounding DINO's pipette
   boxes swapped for the tip model's (vision.eval.score with both).
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path

from ..core import PIPETTE, PIPETTE_TIP, Det, Hand
from ..eval import score as S
from ..eval.cache import load
from ..events import holding_hand, tip_end
from .frames import read_at

EVAL = Path(__file__).resolve().parent.parent / "eval"


def nearest(recs, t, key=None):
    pool = [r for r in recs if key is None or key in r]
    return min(pool, key=lambda r: abs(r["t"] - t))


def pick_box(dets, lab_box):
    """The box the comparison credits: the highest scoring one on the labelled pipette, else the
    highest scoring one."""
    pips = sorted([d for d in dets if d.label in (PIPETTE, PIPETTE_TIP)], key=lambda d: -d.score)
    on = [d for d in pips if isinstance(lab_box, list) and S.inside(S.centre(d.box), lab_box, 30)]
    return (on or pips or [None])[0], bool(on)


def frame_compare(gd, tm):
    labels = json.loads((EVAL / "labels.json").read_text())
    tips = json.loads((EVAL / "tipends.json").read_text())["points"]
    res = {k: {"found": 0, "frames": 0, "right": 0, "wrong": 0, "tip_err": [], "tip_missing": 0, "ms": []}
           for k in ("dino", "tipmodel")}
    for name, clip in labels["clips"].items():
        if clip["split"] != "hold":
            continue
        labs = [f for f in labels["frames"] if f["clip"] == name]
        imgs = read_at(clip["path"], [f["t"] for f in labs])
        recs = load(clip["path"])
        for lab in labs:
            img = imgs[lab["t"]]
            hands = [Hand(h["side"], [tuple(p) for p in h["pts"]]) for h in nearest(recs, lab["t"])["hands"]]
            for k, det in (("dino", gd), ("tipmodel", tm)):
                t0 = time.perf_counter()
                dets = det(img)
                res[k]["ms"].append(1000 * (time.perf_counter() - t0))
                for d in dets:
                    if d.label in (PIPETTE, PIPETTE_TIP) and (j := S.judge(d, lab, clip["scene"])) is not None:
                        res[k]["right" if j else "wrong"] += 1
                if not isinstance(lab["pipette"], list):
                    continue
                res[k]["frames"] += 1
                box, on = pick_box(dets, lab["pipette"])
                res[k]["found"] += on
                if lab["id"] in tips:
                    if box is None:
                        res[k]["tip_missing"] += 1
                        continue
                    pip = Det(PIPETTE, box.box, box.score)
                    tipboxes = [d for d in dets if d.label == PIPETTE_TIP and d.box != box.box]
                    end = tip_end(pip, tipboxes, holding_hand(pip, hands))
                    res[k]["tip_err"].append(math.dist(end, tips[lab["id"]]))
    out = {}
    for k, v in res.items():
        e = v["tip_err"]
        out[k] = {"pipette_found": [v["found"], v["frames"]],
                  "wrong_boxes": [v["wrong"], v["right"] + v["wrong"]],
                  "tip_end_median_px": round(statistics.median(e), 1) if e else None,
                  "tip_end_within_20px": [sum(x <= 20 for x in e), len(e) + v["tip_missing"]],
                  "tip_end_no_box": v["tip_missing"],
                  "ms_median": round(statistics.median(v["ms"][3:]), 1)}
    return out


def swapped_cache(tm):
    """vision.eval.replay.frames, with every held-out detector run's pipette boxes from the tip model."""
    from ..eval import replay
    labels = json.loads((EVAL / "labels.json").read_text())
    held = {c["path"] for c in labels["clips"].values() if c["split"] == "hold"}
    memo = {}

    def frames(rel, start=0.0, seconds=1e9):
        fs = replay.frames(rel, start, seconds)
        if rel not in held:
            return fs
        if (rel, start, seconds) not in memo:
            imgs = read_at(rel, [f["t"] for f in fs if "dets" in f])
            new = []
            for f in fs:
                if "dets" in f:
                    keep = [d for d in f["dets"] if d["label"] not in (PIPETTE, PIPETTE_TIP)]
                    keep += [{"label": d.label, "box": list(d.box), "score": d.score} for d in tm(imgs[f["t"]])]
                    f = {**f, "dets": keep}
                new.append(f)
            memo[(rel, start, seconds)] = new
        return memo[(rel, start, seconds)]
    return frames


def live_compare(tm):
    labels = json.loads((EVAL / "labels.json").read_text())
    before = S.score(labels, {}, "hold")
    orig = S.frames
    S.frames = swapped_cache(tm)
    try:
        after = S.score(labels, {}, "hold")
    finally:
        S.frames = orig
    pick = lambda r: {"pipette_found": r["pipette_found"],  # noqa: E731
                      "pipette_wrong": [r["boxes"]["pipette"]["wrong"],
                                        r["boxes"]["pipette"]["right"] + r["boxes"]["pipette"]["wrong"]],
                      "pipette_tip_wrong": [r["boxes"]["pipette tip"]["wrong"],
                                            r["boxes"]["pipette tip"]["right"] + r["boxes"]["pipette tip"]["wrong"]],
                      "press_events": r["events"]["press"]}
    return {"dino": pick(before), "tipmodel": pick(after)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--threshold", type=float, default=0.5)
    a = ap.parse_args()
    from ..detector import GroundingDino
    from .model import TipModel
    gd, tm = GroundingDino(), TipModel(threshold=a.threshold)
    res = {"frames": frame_compare(gd, tm), "live": live_compare(tm), "threshold": a.threshold}
    print(json.dumps(res, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1) + "\n")


if __name__ == "__main__":
    main()
