"""Contact sheets for labelling actions by eye: a grid of small frames with their times.

    python -m vision.eval.sheet video.mp4 out.jpg --start 10 --end 40 --every 0.5 [--cols 6] [--width 213]
    python -m vision.eval.sheet video.mp4 out.jpg --start 10 --end 14 --every 0.2 --crop x1,y1,x2,y2
"""
from __future__ import annotations

import argparse

import cv2
import numpy as np


def sheet(path, start, end, every, cols=6, width=213, crop=None):
    cap = cv2.VideoCapture(path)
    tiles = []
    t = start
    while t <= end + 1e-6:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, f = cap.read()
        if not ok:
            break
        if f.shape[1] > 640:
            f = cv2.resize(f, (640, round(f.shape[0] * 640 / f.shape[1])))
        if crop:
            x1, y1, x2, y2 = crop
            f = f[y1:y2, x1:x2]
        h = round(f.shape[0] * width / f.shape[1])
        f = cv2.resize(f, (width, h))
        cv2.putText(f, f"{t:.1f}", (3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3)
        cv2.putText(f, f"{t:.1f}", (3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        tiles.append(f)
        t = round(t + every, 3)
    cap.release()
    if not tiles:
        raise SystemExit("no frames")
    blank = np.zeros_like(tiles[0])
    while len(tiles) % cols:
        tiles.append(blank)
    rows = [cv2.hconcat(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    return cv2.vconcat(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("out")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=30.0)
    ap.add_argument("--every", type=float, default=0.5)
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--width", type=int, default=213)
    ap.add_argument("--crop")
    a = ap.parse_args()
    crop = tuple(int(v) for v in a.crop.split(",")) if a.crop else None
    cv2.imwrite(a.out, sheet(a.video, a.start, a.end, a.every, a.cols, a.width, crop), [cv2.IMWRITE_JPEG_QUALITY, 80])


if __name__ == "__main__":
    main()
