"""Score tip_on, tip_off and hand_touch events against labelled moments (vision/eval/tips.json).

    $BENCH_PY -m vision.eval.score_tips [--set '{"rules": {"touch_dwell": 0.5}}'] [--per-clip] [--json out.json]

Each clip is replayed from the cached model output (vision/eval/cache/) through the live code.
An event matches a labelled moment of the same kind within TOL seconds, each moment once.
hand_touch matches any labelled touch (the object name is reported separately: tube and rack
are hard to tell apart when a tube sits in a rack). Chance: the same events at random times in
the clip, 200 tries.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from ..events import Rules
from ..scene import SceneRules
from .replay import frames, replay

HERE = Path(__file__).parent
TOL = 1.0
KINDS = ("tip_on", "tip_off", "hand_touch")


def label_times(clip, kind):
    if kind == "hand_touch":
        return [t for t, _ in clip["touch"]]
    return list(clip[kind])


def match(ev_times, lab_times, tol=TOL):
    """Greedy one-to-one matching in time order. Returns matched pairs count."""
    used, hit = set(), 0
    for t in sorted(ev_times):
        best = None
        for i, lt in enumerate(lab_times):
            if i not in used and abs(lt - t) <= tol and (best is None or abs(lt - t) < abs(lab_times[best] - t)):
                best = i
        if best is not None:
            used.add(best)
            hit += 1
    return hit


def clip_events(clip, settings):
    fs = frames(clip["path"])
    if not fs:
        return None, (0, 0)
    kw = {}
    if "rules" in settings:
        kw["rules"] = Rules(**settings["rules"])
    if "scene" in settings:
        kw["scene"] = SceneRules(**settings["scene"])
    evs = [e for e in replay([fs], **kw) if e["kind"] in KINDS]
    return evs, (fs[0]["t"], fs[-1]["t"])


def score(labels, settings, split, tries=200, per_clip=False):
    rng = random.Random(7)
    tot = {k: {"fired": 0, "right": 0, "labelled": 0, "found": 0, "ch_right": 0, "ch_found": 0, "obj_right": 0}
           for k in KINDS}
    rows = []
    for name, clip in labels["clips"].items():
        if clip["split"] != split:
            continue
        evs, (a, b) = clip_events(clip, settings)
        if evs is None:
            continue
        for k in KINDS:
            et = [e["t"] for e in evs if e["kind"] == k]
            lt = label_times(clip, k)
            m = match(et, lt)
            v = tot[k]
            v["fired"] += len(et)
            v["labelled"] += len(lt)
            v["right"] += m
            v["found"] += m
            if k == "hand_touch":
                objs = [(t, o) for t, o in clip["touch"]]
                v["obj_right"] += sum(1 for e in evs if e["kind"] == k and any(
                    abs(t - e["t"]) <= TOL and o == e["object"] for t, o in objs))
            for _ in range(tries):
                mc = match([rng.uniform(a, b) for _ in et], lt)
                v["ch_right"] += mc
                v["ch_found"] += mc
            if per_clip and (et or lt):
                rows.append(f"  {name:9s} {k:10s} fired {len(et):3d} labelled {len(lt):3d} matched {m}")
    out = {}
    for k, v in tot.items():
        out[k] = {"fired": v["fired"], "right": v["right"], "labelled": v["labelled"], "found": v["found"],
                  "precision": round(v["right"] / v["fired"], 2) if v["fired"] else None,
                  "recall": round(v["found"] / v["labelled"], 2) if v["labelled"] else None,
                  "chance_precision": round(v["ch_right"] / (tries * v["fired"]), 2) if v["fired"] else None,
                  "chance_recall": round(v["ch_found"] / (tries * v["labelled"]), 2) if v["labelled"] else None}
        if k == "hand_touch":
            out[k]["same_object"] = v["obj_right"]
    return out, rows


def show(split, res):
    print(f"[{split}]")
    for k, v in res.items():
        extra = f"  same object {v['same_object']}" if "same_object" in v else ""
        print(f"  {k:10s} precision {v['precision']} ({v['right']}/{v['fired']}, chance {v['chance_precision']})"
              f"  recall {v['recall']} ({v['found']}/{v['labelled']}, chance {v['chance_recall']}){extra}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="{}")
    ap.add_argument("--json")
    ap.add_argument("--per-clip", action="store_true")
    ap.add_argument("--labels", default=str(HERE / "tips.json"))
    a = ap.parse_args()
    labels = json.loads(Path(a.labels).read_text())
    res = {}
    for s in ("tune", "hold"):
        res[s], rows = score(labels, json.loads(a.set), s, per_clip=a.per_clip)
        show(s, res[s])
        for r in rows:
            print(r)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
