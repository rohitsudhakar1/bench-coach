"""Shapes shared by every part of vision: boxes, hands, small geometry, and the event dict.

Nothing here touches a model or a camera, so it can be tested on hand-made inputs.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

# Object names used in events.
HAND = "hand"
PIPETTE = "pipette"
PIPETTE_TIP = "pipette tip"
TIP_BOX = "tip box"
TUBE = "tube"
RACK = "tube rack"
PLATE = "plate"
WASTE = "waste"
VESSELS = (TUBE, RACK, PLATE)
TOUCHABLE = (TIP_BOX, TUBE, RACK, PLATE, WASTE)

# MediaPipe hand landmark indices.
WRIST, THUMB_TIP, INDEX_MCP, INDEX_TIP, MIDDLE_MCP, MIDDLE_TIP = 0, 4, 5, 8, 9, 12


def samples_dir() -> Path | None:
    """Folder with the sample videos (BENCH_SAMPLES), or None when it is not set."""
    p = os.environ.get("BENCH_SAMPLES")
    return Path(p) if p else None


def sample(rel: str) -> Path | None:
    """A sample video by its path inside BENCH_SAMPLES, or None when missing."""
    root = samples_dir()
    if root is None:
        return None
    p = root / rel
    return p if p.exists() else None


@dataclass
class Det:
    label: str
    box: tuple  # x1, y1, x2, y2 in pixels
    score: float


@dataclass
class Hand:
    side: str  # "left" or "right" (the person's hand)
    pts: list | None = None  # 21 landmarks in pixels, when MediaPipe found them
    box: tuple | None = None  # detector box, when only that is known

    @property
    def center(self):
        if self.pts:
            return (sum(p[0] for p in self.pts) / len(self.pts), sum(p[1] for p in self.pts) / len(self.pts))
        return center(self.box)

    @property
    def size(self) -> float:
        if self.pts:
            return max(1.0, dist(self.pts[WRIST], self.pts[MIDDLE_MCP]))
        x1, y1, x2, y2 = self.box
        return max(1.0, max(x2 - x1, y2 - y1) / 2.5)

    def fingertips(self):
        return [self.pts[i] for i in (INDEX_TIP, THUMB_TIP, MIDDLE_TIP)] if self.pts else []

    def thumb_ratio(self) -> float | None:
        """Thumb tip to index knuckle, in hand sizes. Falls when the thumb pushes the plunger."""
        if not self.pts:
            return None
        return dist(self.pts[THUMB_TIP], self.pts[INDEX_MCP]) / self.size


def dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def inside(pt, box, pad: float = 0.0) -> bool:
    x1, y1, x2, y2 = box
    px, py = (x2 - x1) * pad, (y2 - y1) * pad
    return x1 - px <= pt[0] <= x2 + px and y1 - py <= pt[1] <= y2 + py


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def overlaps(a, b) -> bool:
    return not (a[0] > b[2] or a[2] < b[0] or a[1] > b[3] or a[3] < b[1])


def event(t, kind, cam, obj=None, hand=None, confidence=None, detail=None) -> dict:
    """One event in the shape docs/EVENTS.md gives."""
    d = {"cam": cam}
    if detail:
        d.update(detail)
    return {"t": float(t), "source": "vision", "kind": kind, "object": obj, "hand": hand,
            "step": None, "confidence": confidence, "detail": d}
