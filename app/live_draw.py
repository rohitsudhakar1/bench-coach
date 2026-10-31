"""Draw detected objects and hands on a frame for the live view. No models here, so it is easy to test.

Objects are dicts: {"label", "box": [x1, y1, x2, y2], "score", "outline": [[x, y], ...] or None}
in the frame's own pixels. Hands are dicts: {"side", "pts": 21 [x, y] points or None, "box"}.
"""
from __future__ import annotations

import cv2
import numpy as np

# BGR. One colour per object name so the same thing keeps its colour across frames.
COLORS = {"pipette": (0, 200, 255), "pipette tip": (0, 120, 255), "tip box": (255, 140, 0),
          "tube": (60, 220, 60), "tube rack": (160, 255, 0), "plate": (255, 0, 200),
          "waste": (80, 80, 255), "hand": (255, 255, 255)}
OTHER = (200, 200, 200)
HAND_COLORS = {"left": (255, 200, 80), "right": (80, 255, 255)}
LINKS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]
FONT = cv2.FONT_HERSHEY_SIMPLEX


def color(label: str):
    return COLORS.get(label, OTHER)


def _label(img, text, x, y, c, scale, taken=None):
    """A filled chip with dark text, kept inside the picture. With `taken` (a list of chips already
    drawn), a chip that would cover another is moved down until it does not (at most 4 times)."""
    fs, th = 0.42 * scale, max(1, round(scale))
    (tw, tht), base = cv2.getTextSize(text, FONT, fs, th)
    h, w = img.shape[:2]
    ch = tht + base + 4
    x = int(min(max(0, x), max(0, w - tw - 8)))
    y = int(min(max(ch, y), h))
    for _ in range(4 if taken is not None else 0):
        if not any(x < bx2 and bx1 < x + tw + 8 and y - ch < by2 and by1 < y for bx1, by1, bx2, by2 in taken):
            break
        y = int(min(y + ch + 2, h))
    cv2.rectangle(img, (x, y - ch), (x + tw + 8, y), c, -1)
    cv2.putText(img, text, (x + 4, y - base - 2), FONT, fs, (20, 20, 20), th, cv2.LINE_AA)
    if taken is not None:
        taken.append((x, y - ch, x + tw + 8, y))


def draw_objects(img, objects, scale: float = 1.0, fill: float = 0.25):
    """Outline (or box) and name every object. Outlines get a light fill so the shape reads at a glance."""
    lw = max(2, round(2 * scale))
    if fill > 0 and any(o.get("outline") for o in objects):
        layer = img.copy()
        for o in objects:
            if o.get("outline"):
                cv2.fillPoly(layer, [np.asarray(o["outline"], np.int32)], color(o["label"]))
        cv2.addWeighted(layer, fill, img, 1 - fill, 0, dst=img)
    taken = []
    for o in objects:
        c = color(o["label"])
        x1, y1, x2, y2 = (int(round(v)) for v in o["box"])
        if o.get("outline"):
            cv2.polylines(img, [np.asarray(o["outline"], np.int32)], True, c, lw, cv2.LINE_AA)
        else:
            cv2.rectangle(img, (x1, y1), (x2, y2), c, lw, cv2.LINE_AA)
    for o in objects:  # labels after every shape, so no outline is drawn across a label
        score = o.get("score")
        _label(img, o["label"] if score is None else f"{o['label']} {score:.2f}", int(o["box"][0]), int(o["box"][1]),
               color(o["label"]), scale, taken)
    return img


def draw_hands(img, hands, scale: float = 1.0):
    lw = max(2, round(2 * scale))
    for hd in hands:
        c = HAND_COLORS.get(hd.get("side"), (255, 255, 255))
        pts = hd.get("pts")
        if pts:
            p = [(int(round(x)), int(round(y))) for x, y in pts]
            for a, b in LINKS:
                cv2.line(img, p[a], p[b], c, lw, cv2.LINE_AA)
            for q in p:
                cv2.circle(img, q, lw + 1, (30, 30, 30), -1, cv2.LINE_AA)
                cv2.circle(img, q, lw, c, -1, cv2.LINE_AA)
            x, y = min(q[0] for q in p), min(q[1] for q in p)
            _label(img, f"{hd.get('side', '')} hand", x, y - 4, c, scale)
        elif hd.get("box"):
            x1, y1, x2, y2 = (int(round(v)) for v in hd["box"])
            cv2.rectangle(img, (x1, y1), (x2, y2), c, lw, cv2.LINE_AA)
            _label(img, f"{hd.get('side', '')} hand?", x1, y1, c, scale)
    return img


def overlay(frame, objects, hands, scale: float | None = None):
    """A copy of frame with objects and hands drawn on it. scale defaults to the frame size over 640."""
    img = frame.copy()
    if scale is None:
        scale = max(img.shape[:2]) / 640
    draw_objects(img, objects, scale)
    draw_hands(img, hands, scale)
    return img
