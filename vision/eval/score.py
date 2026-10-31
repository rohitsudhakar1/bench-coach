"""Score boxes and events against the 128 hand-labelled frames (vision/eval/labels.json).

    $BENCH_PY -m vision.eval.score [--set '{"scene": {"max_frac": {"tube rack": 0.1}}}']

Measured per split ("tune" was used to pick settings in the earlier draft, "hold" was not):
- wrong boxes: pipette, pipette tip, tip box and waste boxes in force at a labelled frame whose
  centre is not on the labelled thing (rules from the earlier draft's scorer).
- pipette found: frames with a labelled pipette where the part's pipette box centre is on it.
- events: an event is right if a labelled frame within 0.8 s has that kind (hand_touch: that
  object; draw/dispense: the tip in a vessel or a labelled draw). Recall: runs of labelled
  frames with the kind that got an event. Chance: the same events at random times, 200 tries.
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
NEAR = 0.8
KINDS = ("hand_touch", "tip_on", "tip_off", "press")
PRESS = ("draw", "dispense", "vessel")


def centre(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def inside(pt, box, pad=0.0):
    return box[0] - pad <= pt[0] <= box[2] + pad and box[1] - pad <= pt[1] <= box[3] + pad


def judge(d, lab, scene):
    """True right, False wrong, None cannot tell."""
    c = centre(d.box)
    if d.label in ("pipette", "pipette tip"):
        p = lab["pipette"]
        if isinstance(p, list) and inside(c, p, 30):
            return True
        if d.label == "pipette tip" and any(inside(c, b, 10) for b in scene["tip box"]):
            return True
        if p == "?":
            return False if any(inside(c, b) for b in scene["not"]) else None
        return False
    if d.label == "tip box":
        return any(inside(c, b, 20) for b in scene["tip box"])
    if d.label == "waste":
        return any(inside(c, b, 20) for b in scene["waste"])
    return None


def kind_of(e):
    return "press" if e["kind"] in ("draw", "dispense") else e["kind"]


def has(lab, k, obj=None):
    if k == "hand_touch":
        return obj in lab["touch"] if obj else bool(lab["touch"])
    if k == "press":
        return lab["act"] in PRESS
    return lab["act"] == k


def run_clip(clip, settings):
    """(per labelled time: boxes in force and pipette box) and the events."""
    fs = frames(clip["path"], clip["start"], clip["seconds"])
    state = {}

    def grab(cam, fr, vis):
        state[round(fr["t"], 1)] = (list(vis.dets[0]), vis.engines[0].debug.get("pipette_box"))
    kw = {}
    if "rules" in settings:
        kw["rules"] = Rules(**settings["rules"])
    if "scene" in settings:
        kw["scene"] = SceneRules(**settings["scene"])
    evs = [{"t": e["t"], "kind": e["kind"], "object": e["object"]}
           for e in replay([fs], on_frame=grab, **kw) if e["kind"] != "objects"]
    return state, evs, (fs[0]["t"], fs[-1]["t"]) if fs else (0, 0)


def event_score(evs, labs):
    res = {k: {"tp": 0, "fp": 0, "runs": 0, "hit": 0, "n": 0} for k in KINDS}
    for e in evs:
        k = kind_of(e)
        if k not in res:
            continue
        res[k]["n"] += 1
        close = [lab for lab in labs if abs(lab["t"] - e["t"]) <= NEAR]
        known = [lab for lab in close if lab["act"] != "?" or k == "hand_touch"]
        if not known:
            continue
        ok = any(has(lab, k, e["object"] if k == "hand_touch" else None) for lab in known)
        res[k]["tp" if ok else "fp"] += 1
    for k in KINDS:
        objs = sorted({o for lab in labs for o in lab["touch"]}) if k == "hand_touch" else [None]
        for obj in objs:
            run = []
            for lab in labs + [None]:
                if lab is not None and has(lab, k, obj):
                    run.append(lab)
                    continue
                if run:
                    res[k]["runs"] += 1
                    t0, t1 = run[0]["t"] - NEAR, run[-1]["t"] + NEAR
                    if any(t0 <= e["t"] <= t1 and kind_of(e) == k and (obj is None or e["object"] == obj) for e in evs):
                        res[k]["hit"] += 1
                    run = []
    return res


def score(labels, settings, split, chance_tries=200):
    boxes = {k: [0, 0] for k in ("pipette", "pipette tip", "tip box", "waste")}
    pip = [0, 0]
    tot = {k: {"tp": 0, "fp": 0, "runs": 0, "hit": 0, "n": 0} for k in KINDS}
    ch = {k: [0, 0, 0] for k in KINDS}  # chance: tp, scored, hit
    seconds = 0.0
    rng = random.Random(7)
    for name, clip in labels["clips"].items():
        if clip["split"] != split:
            continue
        labs = sorted((f for f in labels["frames"] if f["clip"] == name), key=lambda f: f["t"])
        state, evs, (a, b) = run_clip(clip, settings)
        seconds += clip["seconds"]
        for lab in labs:
            key = min(state, key=lambda k: abs(k - lab["t"]))
            dets, pbox = state[key]
            for d in dets:
                if d.label in boxes and (j := judge(d, lab, clip["scene"])) is not None:
                    boxes[d.label][0 if j else 1] += 1
            if isinstance(lab["pipette"], list):
                pip[1] += 1
                pip[0] += bool(pbox) and inside(centre(pbox), lab["pipette"], 30)
        for k, v in event_score(evs, labs).items():
            for f in v:
                tot[k][f] += v[f]
        for _ in range(chance_tries):
            moved = [{**e, "t": rng.uniform(a, b)} for e in evs]
            for k, v in event_score(moved, labs).items():
                ch[k][0] += v["tp"]
                ch[k][1] += v["tp"] + v["fp"]
                ch[k][2] += v["hit"]
    out = {"seconds": seconds, "boxes": {k: {"right": r, "wrong": w} for k, (r, w) in boxes.items()},
           "pipette_found": pip, "events": {}}
    r_all = sum(r for r, _ in boxes.values())
    w_all = sum(w for _, w in boxes.values())
    out["wrong_box_rate"] = round(w_all / (r_all + w_all), 2) if r_all + w_all else None
    for k, v in tot.items():
        sc = v["tp"] + v["fp"]
        out["events"][k] = {
            "fired": v["n"], "right": v["tp"], "scored": sc, "runs": v["runs"], "found": v["hit"],
            "precision": round(v["tp"] / sc, 2) if sc else None,
            "recall": round(v["hit"] / v["runs"], 2) if v["runs"] else None,
            "chance_precision": round(ch[k][0] / ch[k][1], 2) if ch[k][1] else None,
            "chance_recall": round(ch[k][2] / (chance_tries * v["runs"]), 2) if v["runs"] else None}
    return out


def show(split, r):
    print(f"[{split}] {r['seconds']:.0f} s of video")
    print(f"  wrong boxes {r['wrong_box_rate']}  " + "  ".join(
        f"{k} {v['wrong']}/{v['right'] + v['wrong']} wrong" for k, v in r["boxes"].items()))
    print(f"  pipette found {r['pipette_found'][0]} of {r['pipette_found'][1]} labelled frames")
    for k, v in r["events"].items():
        print(f"  {k:10s} fired {v['fired']:3d}  precision {v['precision']} ({v['right']}/{v['scored']}, chance "
              f"{v['chance_precision']})  recall {v['recall']} ({v['found']}/{v['runs']}, chance {v['chance_recall']})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="{}")
    ap.add_argument("--json")
    a = ap.parse_args()
    labels = json.loads((HERE / "labels.json").read_text())
    res = {s: score(labels, json.loads(a.set), s) for s in ("tune", "hold")}
    for s, r in res.items():
        show(s, r)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
