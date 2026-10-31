"""Compare detectors on the labelled frames, each through the same replay and scorer.

    $BENCH_PY -m vision.eval.compare gdino-tiny gdino-base owlv2 florence2 --json out.json
    $BENCH_PY -m vision.eval.compare --merge gdino-tiny owlv2     # build an ensemble cache, no model

"gdino-tiny" is the original cache (vision/eval/cache/). Every other name reads
vision/eval/cache-<name>/ (written by vision.eval.recache). Each detector's box score cut is
picked on the tuning clips only, from its CUTS list, by pipette found minus wrong-box rate; the
held-out clips are then scored once at that cut.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
CUTS = {"gdino-tiny": [0.25], "gdino-base": [0.1, 0.15, 0.2, 0.25], "owlv2": [0.1, 0.15, 0.2, 0.3],
        "florence2": [0.0], "yoloe": [0.05, 0.1, 0.15, 0.25]}
DEFAULT_CUTS = [0.1, 0.15, 0.2, 0.25]


def folder(name: str) -> str:
    return "cache" if name == "gdino-tiny" else "cache-" + name.replace(":", "-").replace("+", "-")


def score(name: str, cut: float) -> dict:
    env = {**os.environ, "VISION_EVAL_CACHE": folder(name), "VISION_EVAL_MIN_SCORE": str(cut)}
    out = HERE / f".score-{folder(name)}-{cut}.json"
    subprocess.run([sys.executable, "-m", "vision.eval.score", "--json", str(out)], env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    r = json.loads(out.read_text())
    out.unlink()
    return r


def merit(r: dict) -> float:
    found, n = r["pipette_found"]
    return found / n - (r["wrong_box_rate"] or 0.0)


def summary(r: dict) -> dict:
    b = r["boxes"]
    tb = b["tip box"]
    return {"pipette_found": r["pipette_found"], "wrong_box_rate": r["wrong_box_rate"],
            "wrong": {k: [v["wrong"], v["right"] + v["wrong"]] for k, v in b.items()},
            "tip_box_precision": round(tb["right"] / (tb["right"] + tb["wrong"]), 2) if tb["right"] + tb["wrong"] else None}


def speed(name: str) -> dict | None:
    """Mean ms per call (main prompt, container prompt) from the recache meta.json."""
    # gdino-tiny's times come from a recache run (cache-gdino-tiny/), its boxes from cache/
    meta = HERE / ("cache-gdino-tiny" if name == "gdino-tiny" else folder(name)) / "meta.json"
    if not meta.exists():
        return None
    files = json.loads(meta.read_text())["files"].values()
    n = sum(f["frames"] for f in files)
    if not n:
        return None
    return {"frames": n, "main_ms": round(sum(f["main_ms"] * f["frames"] for f in files) / n, 1),
            "containers_ms": round(sum(f["containers_ms"] * f["frames"] for f in files) / n, 1)}


def compare(name: str) -> dict:
    tries = []
    for cut in CUTS.get(name, DEFAULT_CUTS):
        r = score(name, cut)
        tries.append((merit(r["tune"]), cut, r))
    best = max(tries, key=lambda x: (x[0], -x[1]))
    _, cut, r = best
    return {"cut": cut, "tune": summary(r["tune"]), "hold": summary(r["hold"]),
            "tune_tries": {c: round(m, 3) for m, c, _ in tries}, "speed": speed(name)}


def build_merge(a: str, b: str):
    """Ensemble cache: each detector frame's boxes from both caches merged (zoo.merge)."""
    from ..core import Det
    from ..zoo import merge
    from .cache import boxes
    name = f"ensemble:{a}+{b}"
    out = HERE / folder(name)
    out.mkdir(exist_ok=True)
    cut = {x: json.loads((HERE / folder(x) / "meta.json").read_text())["min_score"]
           if (HERE / folder(x) / "meta.json").exists() else 0.25 for x in (a, b)}
    files_b = {p.name for p in (HERE / folder(b)).glob("*.json.gz")}
    n = 0
    for p in sorted((HERE / folder(a)).glob("*.json.gz")):
        if p.name not in files_b:
            continue
        da = json.load(gzip.open(p, "rt"))
        db = json.load(gzip.open(HERE / folder(b) / p.name, "rt"))
        for fa, fb in zip(da["frames"], db["frames"]):
            assert abs(fa["t"] - fb["t"]) < 1e-6
            if "dets" not in fa:
                continue
            for key in ("dets", "cont"):
                xa = [Det(d["label"], tuple(d["box"]), d["score"]) for d in fa[key] if d["score"] >= cut[a]]
                xb = [Det(d["label"], tuple(d["box"]), d["score"]) for d in fb[key] if d["score"] >= cut[b]]
                fa[key] = boxes(merge(xa, xb))
        with gzip.open(out / p.name, "wt") as fh:
            json.dump(da, fh)
        n += 1
    sa, sb = speed(a), speed(b)
    meta = {"detector": name, "min_score": 0.0, "files": {}}
    if sa and sb:  # an ensemble costs both models, one after the other
        meta["files"]["all"] = {"frames": min(sa["frames"], sb["frames"]),
                                "main_ms": sa["main_ms"] + sb["main_ms"],
                                "containers_ms": sa["containers_ms"] + sb["containers_ms"]}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"{name}: {n} files merged")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--merge", nargs=2, metavar=("A", "B"))
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.merge:
        build_merge(*a.merge)
        return
    res = {}
    for name in a.names:
        res[name] = r = compare(name)
        h, t = r["hold"], r["tune"]
        sp = r["speed"]
        print(f"{name:28s} cut {r['cut']:.2f}  held-out: pipette {h['pipette_found'][0]}/{h['pipette_found'][1]}, "
              f"wrong boxes {h['wrong_box_rate']}, tip box precision {h['tip_box_precision']}  | tuning: pipette "
              f"{t['pipette_found'][0]}/{t['pipette_found'][1]}, wrong {t['wrong_box_rate']}  | "
              + (f"{sp['main_ms']:.0f} ms + {sp['containers_ms']:.0f} ms" if sp else "speed n/a"), flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
