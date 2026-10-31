"""Pick a per-name score cut for Florence-2's boxes on the tuning clips, with no model run.

    $BENCH_PY -m vision.eval.florence_cut tune            # search on tune frames, print held-out once
    $BENCH_PY -m vision.eval.florence_cut build '{"waste": 0.85}' --out cache-florence2-cut
    $BENCH_PY -m vision.eval.florence_cut mix Left --out cache-mix-left   # Florence-2 on one camera

Florence-2 names a box for nearly every phrase on every frame. vision/zoo.py scores each box by
how sure the model was when it started that phrase. This reads the scored cache
(vision/eval/cache-florence2s/, written by `recache --detector florence2 --out cache-florence2s`),
drops boxes under each name's cut, and writes a cache the normal scorers replay at min_score 0,
which is exactly what the live part does with the same cuts (zoo.FLORENCE_CUTS).

The search is coordinate-wise over CHOICES, scored on the tune split of labels.json by pipette
found minus wrong-box rate (the same merit compare.py uses). The held-out split is scored once.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import sys

from .cache import HERE

SRC = HERE / "cache-florence2s"
CHOICES = {"pipette": [0.0, 0.1, 0.2, 0.3, 0.4], "tip box": [0.0, 0.3, 0.4, 0.5, 0.6],
           "waste": [0.0, 0.3, 0.5, 0.7, 0.8, 0.9], "pipette tip": [0.0, 0.1, 0.3, 1.01]}


def build(cuts: dict, out_name: str, src=SRC) -> None:
    out = HERE / out_name
    out.mkdir(exist_ok=True)
    for p in sorted(src.glob("*.json.gz")):
        data = json.load(gzip.open(p, "rt"))
        for f in data["frames"]:
            for key in ("dets", "cont"):
                if key in f:
                    f[key] = [d for d in f[key] if d["score"] >= cuts.get(d["label"], 0.0)]
        with gzip.open(out / p.name, "wt") as fh:
            json.dump(data, fh)
    meta = json.loads((src / "meta.json").read_text())
    meta.update({"min_score": 0.0, "cuts": cuts})
    (out / "meta.json").write_text(json.dumps(meta, indent=1))


def mix(florence_on: str, florence_folder: str, out_name: str, tiny_cut: float = 0.25) -> None:
    """Florence-2 on one camera of the two-view clips, tiny on the other: Left-view files from
    `florence_folder` when florence_on is "Left" (else Right), the other view from cache/ (tiny,
    boxes from tiny_cut up). One-camera clips keep Florence-2 when it is on the left camera, so
    that each setup is one fixed choice per camera."""
    out = HERE / out_name
    out.mkdir(exist_ok=True)
    fl = HERE / florence_folder
    for p in sorted((HERE / "cache").glob("*.json.gz")):
        two_view = p.name.startswith("CP-")
        use_florence = (f"-{florence_on}-" in p.name) if two_view else florence_on == "Left"
        if use_florence and (fl / p.name).exists():
            data = json.load(gzip.open(fl / p.name, "rt"))
        else:
            data = json.load(gzip.open(p, "rt"))
            for f in data["frames"]:
                for key in ("dets", "cont"):
                    if key in f:
                        f[key] = [d for d in f[key] if d["score"] >= tiny_cut]
        with gzip.open(out / p.name, "wt") as fh:
            json.dump(data, fh)
    (out / "meta.json").write_text(json.dumps({"detector": f"florence2 on {florence_on}, tiny on the other",
                                               "min_score": 0.0, "files": {}}, indent=1))


def run_score(folder: str) -> dict:
    env = {**os.environ, "VISION_EVAL_CACHE": folder, "VISION_EVAL_MIN_SCORE": "0"}
    tmp = HERE / f".score-{folder}.json"
    subprocess.run([sys.executable, "-m", "vision.eval.score", "--json", str(tmp)], env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    r = json.loads(tmp.read_text())
    tmp.unlink()
    return r


def merit(r: dict) -> float:
    found, n = r["pipette_found"]
    return found / n - (r["wrong_box_rate"] or 0.0)


def tune(rounds: int = 2) -> dict:
    tmp = ".cache-florence-try"
    cuts = {k: 0.0 for k in CHOICES}
    build(cuts, tmp)
    best = merit(run_score(tmp)["tune"])
    for _ in range(rounds):
        moved = False
        for name, opts in CHOICES.items():
            for v in opts:
                if v == cuts[name]:
                    continue
                trial = {**cuts, name: v}
                build(trial, tmp)
                m = merit(run_score(tmp)["tune"])
                print(f"  {name} {v}: tune merit {m:.3f}", flush=True)
                if m > best + 1e-9:
                    best, cuts, moved = m, trial, True
        if not moved:
            break
    import shutil
    shutil.rmtree(HERE / tmp)
    return {"cuts": cuts, "tune_merit": round(best, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("tune", "build", "mix"))
    ap.add_argument("cuts", nargs="?", default="{}")
    ap.add_argument("--out", default="cache-florence2-cut")
    a = ap.parse_args()
    if a.mode == "tune":
        r = tune()
        print("picked on tune:", r)
        build(r["cuts"], a.out)
    elif a.mode == "mix":  # cuts here is Left or Right, the camera Florence-2 runs on
        mix(a.cuts, "cache-florence2-cut", a.out)
    else:
        build(json.loads(a.cuts), a.out)
    for name in ("cache-florence2s", a.out):
        env_r = run_score(name)
        for split in ("tune", "hold"):
            s = env_r[split]
            print(f"{name:20s} {split}: pipette {s['pipette_found']}, wrong boxes {s['wrong_box_rate']}, "
                  f"merit {merit(s):.3f}")


if __name__ == "__main__":
    main()
