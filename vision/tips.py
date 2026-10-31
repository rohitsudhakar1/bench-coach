"""Tip on, tip off and hand touches from boxes that keep turning up in the same place.

The detector's single-frame boxes are often wrong (a phone called a tip box, a beaker called
waste), but the real tip box and racks sit still and get boxed again and again. So every rule
here only uses "anchors": tip box, rack, tube, plate and waste boxes seen at least `anchor_min`
times in about the same place.

- tip_on: the lower end of a pipette box (pipette or pipette tip, taller than wide) sits in a
  tip box anchor on `tip_hits` detector runs in a row, then a later run sees it out of the
  anchor or higher up. Fires on leaving.
- tip_off: the lower end of an upright pipette box sits in a waste anchor on `waste_hits` runs.
- hand_touch: a hand's fingertips (or, with no finger points, its box centre) stay inside a
  touchable anchor for `touch_dwell` s after the hand was away from it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .core import PIPETTE, PIPETTE_TIP, TIP_BOX, TOUCHABLE, WASTE, event, inside, iou

TIP_CONFIDENCE = 0.5


@dataclass
class TipRules:
    anchor_iou: float = 0.3
    anchor_min: int = 3  # detector runs that boxed the same place
    anchor_keep: float = 10.0  # forget an anchor not seen for this long
    tip_pad: float = 0.0  # anchor grown by this share for the pipette's lower end
    tip_down: float = 0.5  # tip box anchor also reaches this share of its height further down
    tip_hits: int = 1  # detector runs in a row with the lower end in the tip box
    tip_rise: float = 0.15  # leaving: lower end this share of the anchor height higher
    tip_rearm: float = 5.0
    upright: float = 1.2  # pipette box height / width at least this
    waste_hits: int = 2
    waste_rearm: float = 3.0
    touch_dwell: float = 0.3
    touch_rearm: float = 3.0
    touch_pad: float = 0.0
    touch_box_hands: bool = True  # let hands with no finger points touch, by their box centre


@dataclass
class _Anchor:
    label: str
    box: tuple
    hits: int
    last: float


@dataclass
class _S:
    anchors: list = field(default_factory=list)
    in_tip: int = 0
    tip_low: float | None = None
    tip_anchor: tuple | None = None
    last_tip_on: float = -1e9
    in_waste: int = 0
    last_tip_off: float = -1e9
    touch_since: dict = field(default_factory=dict)
    touch_fired: dict = field(default_factory=dict)


class TipTouch:
    """One per camera. update() every frame; dets are the cleaned detector boxes in force."""

    def __init__(self, cam: int, rules: TipRules | None = None):
        self.cam, self.r, self.s = cam, rules or TipRules(), _S()

    def anchors(self, label):
        return [a for a in self.s.anchors if a.label == label and a.hits >= self.r.anchor_min]

    def _learn(self, t, dets):
        r, s = self.r, self.s
        for d in dets:
            if d.label not in TOUCHABLE:
                continue
            best = max((a for a in s.anchors if a.label == d.label), key=lambda a: iou(a.box, d.box), default=None)
            if best is not None and iou(best.box, d.box) >= r.anchor_iou:
                best.box = tuple(0.7 * b + 0.3 * n for b, n in zip(best.box, d.box))
                best.hits += 1
                best.last = t
            else:
                s.anchors.append(_Anchor(d.label, tuple(d.box), 1, t))
        s.anchors = [a for a in s.anchors if t - a.last <= r.anchor_keep]

    def _lower_ends(self, dets):
        out = []
        for d in dets:
            if d.label not in (PIPETTE, PIPETTE_TIP):
                continue
            x1, y1, x2, y2 = d.box
            if (y2 - y1) >= self.r.upright * (x2 - x1):
                out.append(((x1 + x2) / 2, y2))
        return out

    def update(self, t, hands, dets, fresh) -> list:
        out = []
        if fresh:
            self._learn(t, dets)
            out += self._tips(t, dets)
        out += self._touches(t, hands)
        return out

    def _tips(self, t, dets):
        r, s, out = self.r, self.s, []
        ends = self._lower_ends(dets)
        boxes = self.anchors(TIP_BOX)
        hit = None
        for e in ends:
            for a in boxes:
                if inside(e, self._tip_region(a.box), r.tip_pad):
                    hit = (e, a)
                    break
            if hit:
                break
        if hit:
            s.in_tip += 1
            s.tip_low = hit[0][1] if s.tip_low is None else max(s.tip_low, hit[0][1])
            s.tip_anchor = hit[1].box
        elif s.in_tip:
            if s.in_tip >= r.tip_hits and t - s.last_tip_on >= r.tip_rearm:
                a = s.tip_anchor
                rose = not ends or all(not inside(e, self._tip_region(a), r.tip_pad) or e[1] < s.tip_low - r.tip_rise * (a[3] - a[1])
                                       for e in ends)
                if rose:
                    out.append(event(t, "tip_on", self.cam, TIP_BOX, None, TIP_CONFIDENCE))
                    s.last_tip_on = t
            s.in_tip, s.tip_low, s.tip_anchor = 0, None, None
        wastes = self.anchors(WASTE)
        if any(inside(e, a.box, r.tip_pad) for e in ends for a in wastes):
            s.in_waste += 1
            if s.in_waste == r.waste_hits and t - s.last_tip_off >= r.waste_rearm:
                out.append(event(t, "tip_off", self.cam, WASTE, None, TIP_CONFIDENCE))
                s.last_tip_off = t
        else:
            s.in_waste = 0
        return out

    def _tip_region(self, box):
        x1, y1, x2, y2 = box
        return (x1, y1, x2, y2 + self.r.tip_down * (y2 - y1))

    def _touches(self, t, hands):
        r, s, out, seen = self.r, self.s, [], set()
        for h in hands:
            pts = h.fingertips() if h.pts else ([h.center] if r.touch_box_hands and h.box else [])
            if not pts:
                continue
            for label in TOUCHABLE:
                if label == WASTE:
                    continue
                hit = next((a for a in self.anchors(label) if any(inside(p, a.box, r.touch_pad) for p in pts)), None)
                if hit is None:
                    continue
                key = (h.side, label)
                seen.add(key)
                if key in s.touch_fired:
                    s.touch_fired[key] = t
                elif t - s.touch_since.setdefault(key, t) >= r.touch_dwell:
                    out.append(event(t, "hand_touch", self.cam, label, h.side, 0.5))
                    s.touch_fired[key] = t
        for key in [k for k in s.touch_since if k not in seen]:
            del s.touch_since[key]
        for key in [k for k, last in s.touch_fired.items() if k not in seen and t - last >= r.touch_rearm]:
            del s.touch_fired[key]
        return out
