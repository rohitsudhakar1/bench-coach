"""Turn one camera's hands and boxes into bench events.

- objects: the latest boxes, at most `objects_per_sec` per second per camera.
- hand_touch: a fingertip of a hand with finger points stays on an object `touch_dwell` s;
  the same hand on the same thing again within `touch_rearm` s is the same touch.
- tip_on: the pipette's tip end stays in the tip box `tip_dwell` s, then leaves it.
- tip_off: the tip end stays over the waste `waste_dwell` s.
- draw / dispense, the conservative rule (default): the thumb presses the plunger while the tip
  end is in a tube, rack or plate; without finger points, `vessel_dwell` s in a vessel counts as
  one press. Presses alternate draw, dispense after a fresh tip. It fires rarely. The tracker in
  draw.py (--new-draw) replaces it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .core import (PIPETTE, PIPETTE_TIP, TIP_BOX, TOUCHABLE, VESSELS, WASTE, Det, area, center, dist,
                   event, inside, overlaps)


@dataclass
class Rules:
    objects_per_sec: float = 2.0
    touch_dwell: float = 0.3
    touch_rearm: float = 3.0
    tip_dwell: float = 0.4
    waste_dwell: float = 0.3
    press_drop: float = 0.25  # thumb ratio this far under its resting level is a press
    press_min: float = 0.12  # held this long
    rest_alpha: float = 0.05  # how fast the resting thumb level follows
    box_pad: float = 0.08  # vessel and waste boxes grown by this share for the tip end
    vessel_dwell: float = 0.8  # no finger points: this long in a vessel is one press
    pipette_keep: float = 4.0  # carry a pipette box with its hand this long after the detector saw it
    press_events: bool = True  # off when draw.py does draw/dispense


def holding_hand(pip: Det, hands: list):
    """The hand whose centre is nearest the pipette box, within one hand size."""
    best, best_d = None, math.inf
    for h in hands:
        c = h.center
        x1, y1, x2, y2 = pip.box
        d = math.hypot(max(x1 - c[0], 0, c[0] - x2), max(y1 - c[1], 0, c[1] - y2)) / h.size
        if d < best_d:
            best, best_d = h, d
    return best if best_d < 1.0 else None


def tip_end(pip: Det, tips: list, hand):
    """Where the pipette's tip is: the far end of a detected tip touching the pipette box, else
    the end of the pipette box farther from the holding hand (bottom end with no hand)."""
    x1, y1, x2, y2 = pip.box
    ref = hand.center if hand else center(pip.box)
    for t in tips:
        if t.box != pip.box and overlaps(t.box, pip.box):
            a, b, c, d = t.box
            return max([((a + c) / 2, d), ((a + c) / 2, b), (a, (b + d) / 2), (c, (b + d) / 2)],
                       key=lambda p: dist(p, ref))
    if (y2 - y1) >= (x2 - x1):
        ends = [((x1 + x2) / 2, y1), ((x1 + x2) / 2, y2)]
    else:
        ends = [(x1, (y1 + y2) / 2), (x2, (y1 + y2) / 2)]
    return ends[1] if hand is None else max(ends, key=lambda p: dist(p, hand.center))


@dataclass
class _State:
    last_objects: float = -1e9
    touch_since: dict = field(default_factory=dict)
    touch_fired: dict = field(default_factory=dict)
    tipbox_since: float | None = None
    tipbox_done: bool = False
    waste_since: float | None = None
    waste_done: bool = False
    loaded: bool = False
    rest: dict = field(default_factory=dict)
    press_since: float | None = None
    press_vessel: str | None = None
    press_fired: bool = False
    pip_box: tuple | None = None
    pip_hand_c: tuple | None = None
    pip_t: float = -1e9


class EventEngine:
    """One per camera; call update() once per processed frame."""

    def __init__(self, cam: int, rules: Rules | None = None):
        self.cam, self.r, self.s = cam, rules or Rules(), _State()
        self.pip = self.pip_hand = None
        self.debug: dict = {}

    def update(self, t: float, hands: list, dets: list, fresh: bool = False) -> list:
        r, s, out = self.r, self.s, []
        by: dict = {}
        for d in dets:
            by.setdefault(d.label, []).append(d)

        if fresh and t - s.last_objects >= 1.0 / r.objects_per_sec:
            s.last_objects = t
            out.append(event(t, "objects", self.cam, detail={"boxes": [
                {"label": d.label, "box": [round(v, 1) for v in d.box], "score": round(d.score, 3)} for d in dets]}))

        out += self._touches(t, hands, by)

        pips = by.get(PIPETTE, [])
        if not pips:  # the detector often calls the whole pipette "pipette tip"; one in a hand is the pipette
            pips = [Det(PIPETTE, d.box, d.score) for d in by.get(PIPETTE_TIP, []) if holding_hand(d, hands)]
        pip, hand = self._pipette(t, hands, pips, fresh)
        self.pip, self.pip_hand = pip, hand
        end = tip_end(pip, by.get(PIPETTE_TIP, []), hand) if pip else None
        side = hand.side if hand else None
        self.debug = {"tip_end": end, "pipette_box": pip.box if pip else None, "loaded": s.loaded}

        # tip_on: dwell in the tip box, fire on leaving
        if end is not None and any(inside(end, d.box) for d in by.get(TIP_BOX, [])):
            s.tipbox_since = t if s.tipbox_since is None else s.tipbox_since
            s.tipbox_done = s.tipbox_done or t - s.tipbox_since >= r.tip_dwell
        elif end is not None:  # a pipette missed for a frame does not reset the dwell
            if s.tipbox_done:
                out.append(event(t, "tip_on", self.cam, TIP_BOX, side, 0.6))
                s.loaded = False
            s.tipbox_since, s.tipbox_done = None, False

        # tip_off: dwell over the waste
        if end is not None and any(inside(end, d.box, r.box_pad) for d in by.get(WASTE, [])):
            s.waste_since = t if s.waste_since is None else s.waste_since
            if not s.waste_done and t - s.waste_since >= r.waste_dwell:
                out.append(event(t, "tip_off", self.cam, WASTE, side, 0.6))
                s.waste_done, s.loaded = True, False
        elif end is not None:
            s.waste_since, s.waste_done = None, False

        out += self._press(t, end, hand, by)
        return out

    def _touches(self, t, hands, by) -> list:
        r, s, out, seen = self.r, self.s, [], set()
        for h in hands:
            if not h.pts:
                continue  # box-only hands are guesses; touches need finger points
            for label in TOUCHABLE:
                hit = next((d for d in by.get(label, []) if any(inside(p, d.box) for p in h.fingertips())), None)
                if hit is None:
                    continue
                key = (h.side, label)
                seen.add(key)
                if key in s.touch_fired:
                    s.touch_fired[key] = t
                elif t - s.touch_since.setdefault(key, t) >= r.touch_dwell:
                    out.append(event(t, "hand_touch", self.cam, label, h.side, round(hit.score, 3)))
                    s.touch_fired[key] = t
        for key in [k for k in s.touch_since if k not in seen]:
            del s.touch_since[key]
        for key in [k for k, last in s.touch_fired.items() if k not in seen and t - last >= r.touch_rearm]:
            del s.touch_fired[key]
        return out

    def _press(self, t, end, hand, by) -> list:
        r, s = self.r, self.s
        vessel = None
        if end is not None:
            cands = [d for lab in VESSELS for d in by.get(lab, []) if inside(end, d.box, r.box_pad)]
            vessel = min(cands, key=lambda d: area(d.box)) if cands else None  # a tube beats its rack
        ratio = hand.thumb_ratio() if hand is not None else None
        if ratio is not None:
            rest = s.rest.get(hand.side, ratio)
            pressed, need = ratio < rest - r.press_drop, r.press_min
            if not pressed:
                s.rest[hand.side] = rest + r.rest_alpha * (ratio - rest)
        else:
            pressed, need = vessel is not None, r.vessel_dwell
        self.debug.update(vessel=vessel.label if vessel else None, pressed=pressed and ratio is not None)
        out = []
        if pressed and vessel is not None:
            if s.press_since is None:
                s.press_since, s.press_vessel, s.press_fired = t, vessel.label, False
            if not s.press_fired and t - s.press_since >= need:
                if r.press_events:
                    out.append(event(t, "dispense" if s.loaded else "draw", self.cam, s.press_vessel,
                                     hand.side if hand else None, 0.5 if ratio is not None else 0.3))
                s.loaded, s.press_fired = not s.loaded, True
        elif end is not None or ratio is not None:
            s.press_since, s.press_vessel, s.press_fired = None, None, False
        return out

    def _pipette(self, t, hands, pips, fresh):
        """Best pipette box now; between detections the last one moves with its hand."""
        s = self.s
        if pips and fresh:
            pip = max(pips, key=lambda d: d.score)
            hand = holding_hand(pip, hands)
            s.pip_box, s.pip_hand_c, s.pip_t = pip.box, (hand.center if hand else None), t
            return pip, hand
        if s.pip_box is None or not hands or t - s.pip_t > self.r.pipette_keep:
            return None, None
        if s.pip_hand_c is None:
            return Det(PIPETTE, s.pip_box, 0.0), None
        hand = min(hands, key=lambda h: dist(h.center, s.pip_hand_c))
        if dist(hand.center, s.pip_hand_c) > 2.0 * hand.size:
            return None, None
        dx, dy = hand.center[0] - s.pip_hand_c[0], hand.center[1] - s.pip_hand_c[1]
        x1, y1, x2, y2 = s.pip_box
        return Det(PIPETTE, (x1 + dx, y1 + dy, x2 + dx, y2 + dy), 0.0), hand
