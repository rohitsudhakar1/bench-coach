"""Run another detector over the frames already in vision/eval/cache/ and write its own cache, so
every scorer can replay it (VISION_EVAL_CACHE=cache-<name>). Hands are copied as they are; only
the detector boxes change, on exactly the same frames.

    $BENCH_PY -m vision.eval.recache --detector owlv2 --labels      # the 128 labelled frames' clips
    $BENCH_PY -m vision.eval.recache --detector owlv2 --all --budget 170

Each run stops starting new files after --budget seconds; run it again to carry on (files already
written are skipped). Per call times go to cache-<name>/meta.json.
"""
from __future__ import annotations

import argparse
import gzip
import json
import time
from pathlib import Path

import cv2

from ..core import sample
from .cache import HERE, MAX_SIDE, boxes

SRC = HERE / "cache"
# Cache files holding the labels.json clips (vision.eval.score reads only these).
LABEL_FILES = ("CP-Left-Front-12-27@0", "CP-Right-Front-12-27@0", "CP-Left-Front-249-269@0",
               "CP-Right-Front-249-269@0", "CP-Left-Front-414-445@0", "CP-Right-Front-414-445@0",
               "cas9_hood_033@0", "pcr_105@0")
# Lowest score kept per detector; scorers keep boxes from meta.json's min_score up.
KEEP = {"gdino-tiny": 0.2, "gdino-base": 0.1, "owlv2": 0.1, "florence2": 0.0}
USE = {"gdino-tiny": 0.25, "gdino-base": 0.15, "owlv2": 0.15, "florence2": 0.0}


def build(name: str):
    from ..detector import CONTAINER_PROMPTS
    from ..zoo import make
    kw = {}
    if name.startswith("gdino"):
        kw = {"box_threshold": KEEP[name], "text_threshold": min(0.2, KEEP[name])}
    elif name == "owlv2":
        kw = {"threshold": KEEP[name]}
    det = make(name, raw=True, **kw)
    return det, det.with_prompts(CONTAINER_PROMPTS)


def redo(stem: str, det, cdet, out: Path) -> list:
    """Rerun the detectors on one cache file's detector frames; returns per-frame seconds."""
    from ..sources import fit
    with gzip.open(SRC / f"{stem}.json.gz", "rt") as fh:
        data = json.load(fh)
    path = sample(data["video"])
    if path is None:
        raise SystemExit(f"missing {data['video']} (is BENCH_SAMPLES set?)")
    cap = cv2.VideoCapture(str(path))
    vfps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    want = {int(round(f["t"] * vfps)): f for f in data["frames"] if "dets" in f}
    first = min(want)
    cap.set(cv2.CAP_PROP_POS_FRAMES, first - 1)
    idx, times = first - 1, []
    while want and idx < max(want):
        ok, f = cap.read()
        if not ok:
            break
        idx += 1
        rec = want.pop(idx, None)
        if rec is None:
            continue
        img = fit(f, MAX_SIDE)
        assert img.shape[1] == rec["w"] and img.shape[0] == rec["h"], (img.shape, rec["w"], rec["h"])
        t0 = time.perf_counter()
        rec["dets"] = boxes(det(img))
        t1 = time.perf_counter()
        rec["cont"] = boxes(cdet(img))
        times.append((t1 - t0, time.perf_counter() - t1))
    cap.release()
    if want:
        raise SystemExit(f"{stem}: {len(want)} frames not found in the video")
    out.mkdir(parents=True, exist_ok=True)
    with gzip.open(out / f"{stem}.json.gz", "wt") as fh:
        json.dump(data, fh)
    return times


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detector", required=True)
    ap.add_argument("--labels", action="store_true", help="only the labels.json clips")
    ap.add_argument("--all", action="store_true", help="every cached file")
    ap.add_argument("files", nargs="*", help="cache file stems, e.g. pcr_105@0")
    ap.add_argument("--budget", type=float, default=170.0, help="stop starting files after this many seconds")
    a = ap.parse_args()
    stems = list(a.files)
    if a.labels:
        stems += LABEL_FILES
    if a.all:
        stems += sorted(p.name[:-len(".json.gz")] for p in SRC.glob("*.json.gz"))
    out = HERE / f"cache-{a.detector.replace(':', '-').replace('+', '-')}"
    stems = [s for s in dict.fromkeys(stems) if not (out / f"{s}.json.gz").exists()]
    if not stems:
        print("nothing to do")
        return
    start = time.time()
    det, cdet = build(a.detector)
    meta_p = out / "meta.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {
        "detector": a.detector, "min_score": USE.get(a.detector, 0.25), "files": {}}
    # one warm-up call so the first file's times are not the model loading
    import numpy as np
    det(np.zeros((360, 640, 3), np.uint8))
    for s in stems:
        if time.time() - start > a.budget:
            print(f"budget used; {len(stems) - stems.index(s)} files left, run again", flush=True)
            break
        t = redo(s, det, cdet, out)
        main_ms = 1000 * sum(x for x, _ in t) / len(t)
        cont_ms = 1000 * sum(y for _, y in t) / len(t)
        meta["files"][s] = {"frames": len(t), "main_ms": round(main_ms, 1), "containers_ms": round(cont_ms, 1)}
        meta_p.write_text(json.dumps(meta, indent=1))
        print(f"{s}: {len(t)} frames, main {main_ms:.0f} ms, containers {cont_ms:.0f} ms", flush=True)


if __name__ == "__main__":
    main()
