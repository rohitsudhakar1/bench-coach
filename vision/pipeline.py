"""The per-frame logic, with no camera and no model: the live part and the scorers run this same code.

Feed it each camera's MediaPipe hands every frame and the raw detector boxes when there are new
ones; it returns events.
"""
from __future__ import annotations

from .core import HAND, PIPETTE, PIPETTE_TIP, TIP_BOX, Det, iou
from .detector import CONTAINER, clean
from .draw import DipTracker, DrawRules, Sequencer
from .events import EventEngine, Rules
from .hands import merge_hands
from .held import HeldPipette
from .scene import SceneFilter, SceneRules
from .tips import TipRules, TipTouch

# Kinds the anchor rules in tips.py send. On the held-out clips they found 5 of 16 tip pick-ups
# where the engine's own rule found none; their touches and tip ejects were no better than the
# engine's, so those stay with the engine (vision/TIP-REPORT.md).
TIP_KINDS = ("tip_on",)
_DEFAULT = object()


class Vision:
    def __init__(self, n_cams: int, new_draw: bool = False, rules: Rules | None = None,
                 scene: SceneRules | None = None, draw: DrawRules | None = None,
                 tips=_DEFAULT, tip_kinds=TIP_KINDS, stops: bool = False):
        self.new_draw = new_draw
        # tips: the anchor rules in tips.py replace the engine's own events of tip_kinds.
        # None turns them off (the engine's own rules for every kind).
        if tips is _DEFAULT:
            tips = TipRules()
        self.tips = [TipTouch(i, tips) for i in range(n_cams)] if tips is not None else None
        self.tip_kinds = tuple(tip_kinds)
        base = rules or Rules()
        self.engines = [EventEngine(i, Rules(**{**base.__dict__, "press_events": not new_draw})) for i in range(n_cams)]
        self.scenes = [SceneFilter(scene) for _ in range(n_cams)]
        self.dips = [DipTracker(i, draw) for i in range(n_cams)]
        self.seq = Sequencer(draw)
        self.dets = [[] for _ in range(n_cams)]
        self.held = [HeldPipette() for _ in range(n_cams)]
        self.detector_times = [-float("inf")] * n_cams
        self.hands = [[] for _ in range(n_cams)]
        self.on_entry = None  # called with every draw-tracker entry (the Claude check uses it)
        from .stops import StopEstimator
        self.stops = StopEstimator(n_cams) if stops else None
        self.stop_pip_times = [-float("inf")] * n_cams
        self.last_t = 0.0

    def step(self, cam: int, t: float, w: int, h: int, landmarked: list, raw: list | None = None,
             raw_t: float | None = None) -> list:
        """raw: new detector boxes, or None; raw_t: their source-frame time when asynchronous."""
        fresh = raw is not None
        self.last_t = max(self.last_t, t)
        if fresh:
            self.detector_times[cam] = t
            conts = [Det(d.label[len(CONTAINER):], d.box, d.score) for d in raw if d.label.startswith(CONTAINER)]
            main = clean([d for d in raw if not d.label.startswith(CONTAINER)], w, h)
            self.dets[cam] = self.scenes[cam].update(t, main, w, h)
            if self.new_draw:
                self.dips[cam].set_containers(t, clean(conts, w, h), w, h,
                                              [d for d in self.dets[cam] if d.label == TIP_BOX])
        dets = self.dets[cam]
        hands = merge_hands(landmarked, [d.box for d in dets if d.label == HAND], w)
        self.hands[cam] = hands
        eng = self.engines[cam]
        events = eng.update(t, hands, [d for d in dets if d.label != HAND], fresh)
        # Draw and dispense naming keeps following the engine's own tip_on (it was tuned on that;
        # the anchor rule's extra pick-ups made held-out draw naming worse).
        engine_tip_on = any(e["kind"] == "tip_on" for e in events)
        if self.tips is not None:
            new = [e for e in self.tips[cam].update(t, hands, [d for d in dets if d.label != HAND], fresh)
                   if e["kind"] in self.tip_kinds]
            events = [e for e in events if e["kind"] not in self.tip_kinds] + new
        # Qualification annotates objects only. Draw selection and carry stay unchanged.
        gate = self.held[cam]
        gate_hands = merge_hands(landmarked, [d.box for d in dets if d.label == HAND]
                                 if t - self.detector_times[cam] <= 1.0 else [], w)
        gate.update(t, gate_hands)
        gate.observe(t, eng.pip.box if eng.pip else None, gate_hands)
        if self.stops is not None:
            if fresh and eng.pip is not None and eng.s.pip_t == t:
                self.stop_pip_times[cam] = t if raw_t is None else raw_t
            qualified = eng.pip is not None and gate.allows(eng.pip.box) and not any(
                iou(eng.pip.box, stand) >= 0.3 for stand in self.scenes[cam].stands)
            self.stops.observe(cam, t, eng.pip_hand, eng.pip, qualified, t - self.stop_pip_times[cam])
        for e in events:
            if e["kind"] == "objects":
                for box in e["detail"]["boxes"]:
                    if box["label"] in (PIPETTE, PIPETTE_TIP):
                        box["held"] = gate.allows(box["box"]) and not any(
                            iou(box["box"], stand) >= 0.3 for stand in self.scenes[cam].stands)
        if self.new_draw:
            if engine_tip_on:
                events += self.seq.flush(t, final=True)
                self.seq.tip_on()
            for en in self.dips[cam].update(t, eng.pip.box if eng.pip is not None else None, eng.pip_hand):
                self._push(en)
            events += self.seq.flush(t)
        return self.stops.process(events, t) if self.stops is not None else events

    def _push(self, en):
        self.seq.push(en)
        if self.on_entry:
            self.on_entry(en)

    def finish(self) -> list:
        events = []
        if self.new_draw:
            for d in self.dips:
                for en in d.finish():
                    self._push(en)
            events = self.seq.flush(0.0, final=True)
        return self.stops.process(events, self.last_t, final=True) if self.stops is not None else events


def to_source_pixels(events: list, processed_shape, source_shape, detector_t=None):
    """Objects events give boxes in each camera's own frame size (width/height in detail)."""
    h, w = processed_shape[:2]
    sh, sw = source_shape[:2]
    for e in events:
        if e["kind"] == "objects":
            d = e["detail"]
            d.update(width=sw, height=sh, detector_frame_t=detector_t)
            for b in d["boxes"]:
                b["box"] = [round(v * (sw / w if i % 2 == 0 else sh / h), 1) for i, v in enumerate(b["box"])]
    return events
