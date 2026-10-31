"""Score draw and dispense events against the labelled actions (vision/eval/actions.json).

    $BENCH_PY -m vision.eval.score_actions [--per-clip] [--set '{"min_upright": 0}']

Both rules are replayed from the cache: the default conservative rule and --new-draw. An event
is right when an action of that kind is labelled on the clip with the event inside
[start - 1.5 s, end + 1.5 s]; each action matches once. Events inside a labelled mixing stretch
count neither way. The two mv414 views run as one two-camera stream, scored on the left view's
labels. Chance: the same events at random times in the clip, 200 tries.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from ..draw import DrawRules
from .replay import frames, replay

HERE = Path(__file__).parent
TOL = 1.5
KINDS = ("draw", "dispense")
PAIRS = {"mv414": ("mv414L", "mv414R")}


def streams(labels, split):
    paired = {n for p in PAIRS.values() for n in p}
    out = [(n, [n]) for n, c in labels["clips"].items() if c["split"] == split and n not in paired]
    out += [(p, list(v)) for p, v in PAIRS.items() if labels["clips"][v[0]]["split"] == split]
    return out


def match(evs, acts, kind):
    want = [a for a in acts if a["kind"] == kind]
    mixes = [a for a in acts if a["kind"] == "mix"]
    used, tp, fp = set(), 0, 0
    for e in sorted((e for e in evs if e["kind"] == kind), key=lambda e: e["t"]):
        best = None
        for i, a in enumerate(want):
            if i not in used and a["start"] - TOL <= e["t"] <= a["end"] + TOL:
                d = 0.0 if a["start"] <= e["t"] <= a["end"] else min(abs(e["t"] - a["start"]), abs(e["t"] - a["end"]))
                if best is None or d < best[0]:
                    best = (d, i)
        if best is not None:
            used.add(best[1])
            tp += 1
        elif not any(m["start"] - TOL <= e["t"] <= m["end"] + TOL for m in mixes):
            fp += 1
    return tp, fp, len(want)


def run_stream(labels, names, new_draw, settings):
    fs = [frames(labels["clips"][n]["path"], 0.0, labels["clips"][n]["seconds"]) for n in names]
    draw = DrawRules(**settings) if new_draw else None
    evs = [{"t": e["t"], "kind": e["kind"], "object": e["object"]} for e in replay(fs, new_draw, draw=draw)
           if e["kind"] in KINDS]
    return evs, fs[0][-1]["t"] if fs[0] else 0.0


def score(labels, split, settings, tries=200, per_clip=None):
    res = {}
    rng = random.Random(11)
    for mode in ("default", "new-draw"):
        tot = {k: [0, 0, 0] for k in KINDS}
        ch = {k: [0, 0] for k in KINDS}
        for sname, names in streams(labels, split):
            acts = [a for a in labels["actions"] if a["clip"] == names[0]]
            evs, dur = run_stream(labels, names, mode == "new-draw", settings)
            for k in KINDS:
                tp, fp, n = match(evs, acts, k)
                tot[k][0] += tp
                tot[k][1] += tp + fp
                tot[k][2] += n
                if per_clip is not None:
                    per_clip.setdefault(sname, {}).setdefault(mode, {})[k] = f"{tp}/{tp + fp} right, {tp}/{n} found"
            for _ in range(tries):
                moved = [{**e, "t": rng.uniform(0, dur)} for e in evs]
                for k in KINDS:
                    tp, fp, _ = match(moved, acts, k)
                    ch[k][0] += tp
                    ch[k][1] += tp + fp
        res[mode] = {k: {"right": tp, "fired": f, "labelled": n,
                         "precision": round(tp / f, 2) if f else None, "recall": round(tp / n, 2) if n else None,
                         "chance_precision": round(ch[k][0] / ch[k][1], 2) if ch[k][1] else None,
                         "chance_recall": round(ch[k][0] / (tries * n), 2) if n else None}
                     for k, (tp, f, n) in tot.items()}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="{}", help="DrawRules settings to try, as JSON")
    ap.add_argument("--per-clip", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    labels = json.loads((HERE / "actions.json").read_text())
    out = {}
    for split in ("tune", "hold"):
        per = {} if a.per_clip else None
        out[split] = r = score(labels, split, json.loads(a.set), per_clip=per)
        print(f"[{split}]")
        for mode, v in r.items():
            print(f"  {mode:8s} " + "   ".join(
                f"{k}: precision {x['precision']} ({x['right']}/{x['fired']}, chance {x['chance_precision']}) "
                f"recall {x['recall']} ({x['right']}/{x['labelled']}, chance {x['chance_recall']})" for k, x in v.items()))
        for c, v in (per or {}).items():
            print(f"    {c:9s} {v}")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
