"""Run the models once over a sample video and save what they saw, so the rules can be replayed
and scored many times with no model.

    $BENCH_PY -m vision.eval.cache lsv_dji/pcr_xm048.mp4 [--start 0 --seconds 60]
    $BENCH_PY -m vision.eval.cache --all      # every labelled video, in chunks of 60 s

Writes vision/eval/cache/<name>@<start>.json.gz. Per processed frame (10 a second, long side
640 px): MediaPipe hands (gloves recoloured). Every 0.35 s of video: raw Grounding DINO boxes
for the main prompt and for the container prompt. Video paths are relative to BENCH_SAMPLES.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import time
from pathlib import Path

import cv2

from ..core import sample

HERE = Path(__file__).parent
# VISION_EVAL_CACHE picks another detector's cache (a folder under vision/eval/, e.g. cache-owlv2,
# written by vision.eval.recache) for every scorer.
CACHE = HERE / os.environ.get("VISION_EVAL_CACHE", "cache")


def min_score(default: float = 0.25) -> float:
    """The box score the replay keeps from this cache: VISION_EVAL_MIN_SCORE, else the cache's
    meta.json, else default."""
    if os.environ.get("VISION_EVAL_MIN_SCORE"):
        return float(os.environ["VISION_EVAL_MIN_SCORE"])
    meta = CACHE / "meta.json"
    if meta.exists():
        return float(json.loads(meta.read_text()).get("min_score", default))
    return default
FPS, DETECT_EVERY, MAX_SIDE, CHUNK = 10.0, 0.35, 640, 60.0


def cache_name(rel: str, start: float) -> str:
    return f"{Path(rel).stem}@{int(start)}"


def boxes(dets):
    return [{"label": d.label, "box": [round(v, 1) for v in d.box], "score": round(d.score, 3)} for d in dets]


def videos() -> dict:
    """Every labelled video -> its length in seconds."""
    out = {}
    for f in ("labels.json", "actions.json"):
        for c in json.loads((HERE / f).read_text())["clips"].values():
            end = c.get("start", 0.0) + c["seconds"]
            out[c["path"]] = max(out.get(c["path"], 0.0), end)
    return out


def run(rel, start, seconds, det, cdet):
    from ..hands import HandTracker
    from ..sources import fit
    path = sample(rel)
    if path is None:
        raise SystemExit(f"missing {rel} (is BENCH_SAMPLES set?)")
    cap = cv2.VideoCapture(str(path))
    vfps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(vfps / FPS))
    idx = int(round(start * vfps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    trk = HandTracker()
    frames, last = [], -1e9
    while True:
        f = None
        for _ in range(step):
            ok, f2 = cap.read()
            if not ok:
                f = None
                break
            f, idx = f2, idx + 1
        if f is None:
            break
        t = idx / vfps
        if t - start > seconds:
            break
        img = fit(f, MAX_SIDE)
        rec = {"t": round(t, 3), "w": img.shape[1], "h": img.shape[0],
               "hands": [{"side": h.side, "pts": [[round(x, 1), round(y, 1)] for x, y in h.pts]} for h in trk(img, t)]}
        if t - last >= DETECT_EVERY:
            last = t
            rec["dets"] = boxes(det(img))
            rec["cont"] = boxes(cdet(img))
        frames.append(rec)
    trk.close()
    cap.release()
    CACHE.mkdir(parents=True, exist_ok=True)
    with gzip.open(CACHE / f"{cache_name(rel, start)}.json.gz", "wt") as fh:
        json.dump({"video": rel, "start": start, "frames": frames}, fh)
    return len(frames)


def load(rel: str) -> list:
    """All cached frames of a video, chunks joined, in time order."""
    frames = []
    for p in sorted(CACHE.glob(f"{Path(rel).stem}@*.json.gz"), key=lambda p: int(p.name.split("@")[1].split(".")[0])):
        with gzip.open(p, "rt") as fh:
            frames += json.load(fh)["frames"]
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="?")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--seconds", type=float, default=CHUNK)
    ap.add_argument("--all", action="store_true", help="every labelled video not cached yet")
    a = ap.parse_args()
    from ..detector import CONTAINER_PROMPTS, GroundingDino
    det = GroundingDino(box_threshold=0.2, text_threshold=0.2, raw=True)
    cdet = det.with_prompts(CONTAINER_PROMPTS)
    jobs = [(a.video, a.start, a.seconds)] if a.video else []
    if a.all:
        for rel, length in videos().items():
            s = 0.0
            while s < length:
                if not (CACHE / f"{cache_name(rel, s)}.json.gz").exists():
                    jobs.append((rel, s, CHUNK))
                s += CHUNK
    for rel, s, n in jobs:
        t0 = time.time()
        k = run(rel, s, n, det, cdet)
        print(f"{rel} @{s:.0f}: {k} frames in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
