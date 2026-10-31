"""Opt-in stop estimates from a complete thumb press, never from vessel dwell.

Depth is the fall in thumb-to-index-knuckle distance divided by palm length. It is
an image-plane proxy, not a measurement of plunger travel or liquid delivery.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from statistics import median

from .core import INDEX_MCP, MIDDLE_MCP, THUMB_TIP, WRIST, dist, inside


@dataclass(frozen=True)
class StopRules:
    press_depth: float = 0.18
    release_depth: float = 0.06
    plateau_seconds: float = 0.15
    plateau_noise: float = 0.04
    deeper_by: float = 0.12
    first_max: float = 0.45
    max_depth: float = 1.2
    max_gap: float = 0.25
    max_press: float = 6.0
    wait: float = 2.0  # extra media seconds before publishing a dispense


@dataclass(frozen=True)
class Press:
    start: float
    end: float
    hand: str
    depth: float
    stop: str
    reason: str


def classify(trace, complete=True, rules=None):
    """Return (label, reason) for timestamped positive depths of ONE press.

    Both levels must pause: a smooth ramp is not evidence of two mechanical stops.
    A single shallow plateau is only a first-stop estimate; it is not calibrated.
    """
    r = rules or StopRules()
    if not complete or not trace:
        return "unknown", "incomplete_or_occluded"
    peak = max(d for _, d in trace)
    if peak > r.max_depth:
        return "unknown", "implausible_depth"
    plateaus = []
    for i, (t, d) in enumerate(trace):
        window = [(s, x) for s, x in trace[:i + 1] if t - s <= r.plateau_seconds + 0.11]
        if (len(window) >= 3 and t - window[0][0] >= r.plateau_seconds - 1e-6
                and max(x for _, x in window) - min(x for _, x in window) <= r.plateau_noise):
            level = median(x for _, x in window)
            if level >= r.press_depth:
                plateaus.append((window[0][0], t, level))
    for a0, a1, a in plateaus:
        for b0, b1, b in plateaus:
            if b0 > a1 and b - a >= r.deeper_by:
                between = [d for t, d in trace if a1 <= t <= b0]
                if between and min(between) >= a - r.release_depth:
                    return "second", "pause_then_deeper"
    if (r.press_depth <= peak <= r.first_max
            and any(peak - d <= r.plateau_noise for _, _, d in plateaus)):
        return "first", "single_shallow_plateau"
    return "unknown", "no_distinct_stop_pattern"


class PressTracker:
    """One hand/pipette stream. None breaks evidence; it is never interpolated."""
    def __init__(self, rules=None):
        self.r = rules or StopRules()
        self.presses = deque()
        self.rest = None
        self.side = None
        self.last_t = None
        self.smooth = deque(maxlen=3)
        self.baseline = deque(maxlen=5)
        self.active = None
        self.release_since = None

    def _close(self, t, complete):
        c = self.active
        label, reason = classify(c["trace"], complete and not c["bad"], self.r)
        p = Press(c["start"], t, self.side, max(d for _, d in c["trace"]), label, reason)
        self.presses.append(p)
        self.active = None
        self.release_since = None
        return p

    def update(self, t, ratio, side="right"):
        r = self.r
        if not math.isfinite(t):
            raise ValueError("frame time must be finite")
        if self.last_t is not None and t <= self.last_t:
            # A restarted video must not reuse evidence from its previous clock.
            self.__init__(r)
        gap = self.last_t is not None and t - self.last_t > r.max_gap
        self.last_t = t
        while self.presses and self.presses[0].end < t - 30:
            self.presses.popleft()
        valid = ratio is not None and math.isfinite(ratio) and 0 <= ratio <= 4
        if self.side is not None and side is not None and self.side != side:
            if self.active:
                self._close(t, False)
            self.rest = None
            self.smooth.clear()
            self.baseline.clear()
        if side is not None:
            self.side = side
        if gap or not valid:
            if self.active:
                self.active["bad"] = True
            self.smooth.clear()
            self.baseline.clear()
            self.release_since = None
            if not self.active:
                self.rest = None
        if not valid:
            if self.active and t - self.active["start"] > r.max_press:
                self._close(t, False)
                self.rest = None
            return
        self.smooth.append(ratio)
        if len(self.smooth) < 3:
            return
        value = median(self.smooth)
        if self.rest is None:
            self.baseline.append((t, value))
            if (len(self.baseline) >= 3 and t - self.baseline[0][0] >= 0.2 - 1e-6
                    and max(x for _, x in self.baseline) - min(x for _, x in self.baseline) <= r.plateau_noise):
                self.rest = median(x for _, x in self.baseline)
            return
        depth = self.rest - value
        if self.active is None:
            if depth >= r.press_depth:
                self.active = {"start": t, "trace": [(t, depth)], "bad": gap}
            elif abs(depth) <= r.release_depth:
                self.rest += 0.05 * (value - self.rest)
            elif depth < -r.release_depth:
                # A new grip/rest position needs a fresh stable baseline.
                self.rest = None
                self.baseline.clear()
            return
        self.active["trace"].append((t, max(0, depth)))
        if depth <= r.release_depth:
            self.release_since = t if self.release_since is None else self.release_since
            if t - self.release_since >= r.plateau_seconds - 1e-6:
                self._close(t, True)
        else:
            self.release_since = None
        if self.active and t - self.active["start"] > r.max_press:
            self._close(t, False)
            self.rest = None

    def finish(self):
        if self.active:
            self._close(self.last_t, False)


def thumb_observation(hand, pip, qualified, age):
    """Reject box-only hands, stale/unheld pipettes, tiny or off-box landmarks."""
    if (hand is None or not hand.pts or pip is None or not qualified
            or not math.isfinite(age) or not 0 <= age <= 0.6):
        return None
    if len(hand.pts) != 21 or any(not math.isfinite(v) for p in hand.pts for v in p):
        return None
    if dist(hand.pts[WRIST], hand.pts[MIDDLE_MCP]) < 8:
        return None
    x1, y1, x2, y2 = pip.box
    pad = hand.size
    box = (x1 - pad, y1 - pad, x2 + pad, y2 + pad)
    if not all(inside(hand.pts[i], box) for i in (THUMB_TIP, INDEX_MCP)):
        return None
    return hand.thumb_ratio()


class StopEstimator:
    """Annotate existing dispenses after a bounded wait, preserving kind and time.

    Multiple presses in one visit or conflicting camera estimates are unknown.
    No press is reused for a later event. All other events pass through immediately.
    """
    def __init__(self, n_cams, rules=None):
        self.r = rules or StopRules()
        self.trackers = [PressTracker(self.r) for _ in range(n_cams)]
        self.pending = []
        self.used = set()
        self.previous = [None] * n_cams

    def observe(self, cam, t, hand, pip, qualified, age):
        ratio = thumb_observation(hand, pip, qualified, age)
        prev = self.previous[cam]
        if ratio is not None:
            if prev is not None:
                _, center, size, old_ratio, old_pip = prev
                relative_move = dist((pip.box[0] - hand.center[0], pip.box[1] - hand.center[1]), old_pip)
                if (dist(hand.center, center) > hand.size or not 0.7 <= hand.size / size <= 1.4
                        or relative_move > hand.size
                        or abs(ratio - old_ratio) > 0.6 + 1e-6):
                    ratio = None
            self.previous[cam] = (t, hand.center, hand.size, hand.thumb_ratio(),
                                  (pip.box[0] - hand.center[0], pip.box[1] - hand.center[1]))
        else:
            self.previous[cam] = None
        self.trackers[cam].update(t, ratio, hand.side if hand else None)

    def _label(self, e):
        detail = e["detail"]
        start, end = detail.get("start", e["t"]), detail.get("end", e["t"])
        labels = []
        ambiguous = False
        for cam in detail.get("cams", [detail["cam"]]):
            tracker = self.trackers[cam]
            matches = [p for p in tracker.presses if p.start <= end + 1e-6 and p.end >= start
                       and (e["hand"] is None or p.hand == e["hand"])]
            if tracker.active and tracker.active["start"] <= end:
                ambiguous = True
            if len(matches) > 1:
                ambiguous = True
            for p in matches:
                key = (cam, p.start, p.end)
                if key in self.used or p.end > end + self.r.wait:
                    ambiguous = True
                self.used.add(key)
                labels.append(p.stop)
        known = {s for s in labels if s != "unknown"}
        # A camera with no measurable press contributes no vote. A broken press does.
        return next(iter(known)) if not ambiguous and len(known) == 1 and "unknown" not in labels else "unknown"

    def process(self, events, now, final=False):
        out = []
        for e in events:
            if e["kind"] == "dispense":
                self.pending.append(e)
            else:
                out.append(e)
        if final:
            for tracker in self.trackers:
                tracker.finish()
        for e in list(self.pending):
            if final or now >= e["detail"].get("end", e["t"]) + self.r.wait:
                e["detail"]["stop"] = self._label(e)
                out.append(e)
                self.pending.remove(e)
        # Keep the ownership cache bounded for long-running cameras.
        self.used = {key for key in self.used if key[2] >= now - 30}
        return out
