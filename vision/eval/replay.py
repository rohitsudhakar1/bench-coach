"""Replay cached model output (vision/eval/cache/) through the same code the live part runs."""
from __future__ import annotations

from ..core import Det, Hand
from ..detector import CONTAINER
from ..pipeline import Vision
from .cache import load

MIN_SCORE = 0.25  # the live detector's box threshold; the cache keeps boxes from 0.2


def frames(rel: str, start: float = 0.0, seconds: float = 1e9) -> list:
    return [f for f in load(rel) if start - 1e-6 <= f["t"] <= start + seconds + 1e-6]


def raw_boxes(fr, containers: bool):
    if "dets" not in fr:
        return None
    out = [Det(d["label"], tuple(d["box"]), d["score"]) for d in fr["dets"] if d["score"] >= MIN_SCORE]
    if containers:
        out += [Det(CONTAINER + d["label"], tuple(d["box"]), d["score"]) for d in fr["cont"]]
    return out


def replay(streams: list, new_draw: bool = False, on_frame=None, **kw):
    """streams: one list of cached frames per camera, on one clock. Returns all events.
    on_frame(cam, frame record, vision) is called after each step."""
    vis = Vision(len(streams), new_draw, **kw)
    timeline = sorted((f["t"], cam, i) for cam, fs in enumerate(streams) for i, f in enumerate(fs))
    events = []
    for t, cam, i in timeline:
        fr = streams[cam][i]
        hands = [Hand(h["side"], [tuple(p) for p in h["pts"]]) for h in fr["hands"]]
        events += vis.step(cam, t, fr["w"], fr["h"], hands, raw_boxes(fr, new_draw))
        if on_frame:
            on_frame(cam, fr, vis)
    return events + vis.finish()
