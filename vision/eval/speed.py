"""Time each detector on the same frames, one after the other, on this machine.

    $BENCH_PY -m vision.eval.speed gdino-tiny owlv2 --frames 30

Frames are taken evenly from the 414-445 left clip at the size vision works at (long side 640).
Prints the median ms per call for the main prompt, and the detector runs per second each camera
gets when two cameras share one detector (main prompt only, and with the container prompt too).
"""
from __future__ import annotations

import argparse
import json
import statistics
import time

import cv2

from ..core import sample
from ..sources import fit

CLIP = "lsv_mv/CP-Left-Front-414-445.mp4"


def frames(n: int) -> list:
    path = sample(CLIP)
    if path is None:
        raise SystemExit(f"missing {CLIP} (is BENCH_SAMPLES set?)")
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * total / n))
        ok, f = cap.read()
        if ok:
            out.append(fit(f, 640))
    cap.release()
    return out


def time_one(name: str, imgs: list) -> dict:
    from ..zoo import make
    det = make(name)
    for im in imgs[:3]:
        det(im)
    ms = []
    for im in imgs:
        t = time.perf_counter()
        det(im)
        ms.append(1000 * (time.perf_counter() - t))
    med = statistics.median(ms)
    return {"median_ms": round(med, 1), "per_cam_two_cams": round(1000 / (2 * med), 1),
            "per_cam_two_cams_with_containers": round(1000 / (4 * med), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+")
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--json")
    a = ap.parse_args()
    imgs = frames(a.frames)
    res = {}
    for name in a.names:
        res[name] = r = time_one(name, imgs)
        print(f"{name:28s} {r['median_ms']:6.0f} ms a call; two cameras: {r['per_cam_two_cams']} a second each, "
              f"{r['per_cam_two_cams_with_containers']} with the container prompt", flush=True)
    if a.json:
        from pathlib import Path
        Path(a.json).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
