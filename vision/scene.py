"""Clean detector boxes with what a bench looks like over time.

Grounding DINO names things by look, frame by frame: wall tubing becomes a "pipette tip", a beaker
"waste", the bench edge a "tube rack". These rules use where and how long a box has been seen.
Defaults are the ones that helped on the earlier draft's held-back labelled frames.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .core import PIPETTE, PIPETTE_TIP, PLATE, RACK, TIP_BOX, TUBE, WASTE, area, center, iou


@dataclass
class SceneRules:
    static_secs: float = 3.0  # a pipette / tip box sitting still this long is on a wall or rack: dropped
    static_iou: float = 0.7  # "the same place"
    # Rack and plate boxes over a tenth of the frame span the bench edge. Half the frame (the brief's
    # wording) let through more false touches on the held-back clips (REPORT.md).
    max_frac: dict = field(default_factory=lambda: {RACK: 0.1, PLATE: 0.1, TUBE: 0.03})  # share of the frame
    waste_min_y: float = 0.5  # waste centre must be in the lower part of the frame
    waste_max_frac: float = 0.12  # and small
    waste_hits: int = 5  # and seen in one place this many detector runs
    forget: float = 4.0  # tracks not seen this long are forgotten


@dataclass
class _Track:
    box: tuple
    first: float
    last: float
    hits: int = 1


class SceneFilter:
    def __init__(self, rules: SceneRules | None = None):
        self.r = rules or SceneRules()
        self.tracks: dict = {}

    def _track(self, t, d):
        tracks = self.tracks.setdefault(d.label, [])
        best = max(tracks, key=lambda k: iou(k.box, d.box), default=None)
        if best is not None and iou(best.box, d.box) >= self.r.static_iou:
            best.box, best.last, best.hits = d.box, t, best.hits + 1
            return best
        tr = _Track(d.box, t, t)
        tracks.append(tr)
        return tr

    def update(self, t: float, dets: list, w: int, h: int) -> list:
        r = self.r
        for tracks in self.tracks.values():
            tracks[:] = [k for k in tracks if t - k.last <= r.forget]
        keep = []
        for d in dets:
            if area(d.box) > r.max_frac.get(d.label, 1.0) * w * h:
                continue
            if d.label in (PIPETTE, PIPETTE_TIP):
                k = self._track(t, d)
                if t - k.first >= r.static_secs and k.hits >= 3:
                    continue  # a pipette in use moves
            if d.label == WASTE:
                k = self._track(t, d)
                if center(d.box)[1] < r.waste_min_y * h or area(d.box) > r.waste_max_frac * w * h or k.hits < r.waste_hits:
                    continue
            keep.append(d)
        return keep
