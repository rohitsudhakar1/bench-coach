"""Read the exact video frames the eval cache saw, at the same size (long side 640).

vision/eval/cache.py reads every `step`-th frame and stamps it t = (frame index + 1) / fps, so the
frame behind a cached time t is number round(t * fps) - 1.
"""
from __future__ import annotations

import cv2

from ..core import sample
from ..sources import fit

MAX_SIDE = 640


def read_at(rel: str, times: list) -> dict:
    """{t: BGR image} for each time (seconds) in a sample video, read in order."""
    path = sample(rel)
    if path is None:
        return {}
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    want = sorted({max(0, round(t * fps) - 1): t for t in times}.items())
    out, pos = {}, -1
    for idx, t in want:
        if idx > pos + 60 or idx <= pos:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            pos = idx - 1
        img = None
        while pos < idx:
            ok, img = cap.read()
            if not ok:
                img = None
                break
            pos += 1
        if img is None:
            break
        out[t] = fit(img, MAX_SIDE)
    cap.release()
    return out
