"""Draw and dispense hints from where the pipette's tip goes (--new-draw).

DipTracker, one per camera, finds entries: the lower end of the pipette box sits in a container
box (bottle, flask, beaker, tube from a second detector prompt) for `min_dwell` s, or, with no box
under it, comes down and holds still for `still_win` s. Two filters drop most false entries:
the pipette box must be about upright while in (height / width at least `min_upright`), and the
tip must not have swung in fast just before (`max_pre_speed` frame heights per second).

Sequencer, one for all cameras, merges entries two cameras saw within `fuse_gap` s and names
them: the first after a fresh tip or a dispense is a draw, the next a dispense. Every event has
detail.hint = true; no part may treat it as proof (docs/EVENTS.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .core import TIP_BOX, Det, area, center, dist, event, inside, iou


@dataclass
class DrawRules:
    min_dwell: float = 0.3  # tip end this long in a container makes an entry
    gap: float = 0.4  # tip end may leave or be lost this long without ending the entry
    pad: float = 0.1  # container boxes grown by this share
    cont_keep: float = 1.0  # a container box is kept this long after the detector saw it
    cont_min_score: float = 0.25
    cont_max_frac: float = 0.25  # bigger container boxes (half the bench) are dropped
    pip_iou: float = 0.3  # a container box this like the pipette box is the pipette
    labels: tuple = ("bottle", "flask", "beaker", "tube")  # dish, plate, rack boxes cover the bench
    same_px: float = 0.6  # same container if centres are this many container sizes apart
    dip: bool = True  # still tip after coming down counts as an entry where no box is
    down_px: float = 6.0
    still_px: float = 12.0
    still_win: float = 0.4
    thumb_drop: float = 0.2  # thumb press only raises confidence
    min_upright: float = 1.0  # 0 turns the filter off
    max_pre_speed: float = 1.5  # 0 turns the filter off
    pre_win: float = 0.5
    fuse_gap: float = 1.0


@dataclass
class Entry:
    cam: int
    t0: float
    t1: float
    label: str  # container name, "spot" (no box) or "tip box"
    xy: tuple
    size: float
    down: bool = False
    pressed: bool = False
    hand: str | None = None
    upright: float = 99.0
    pre_speed: float = 0.0

    @property
    def score(self) -> float:
        return 0.3 + 0.2 * self.down + 0.3 * self.pressed


def tip_point(box, hand):
    """Lower end of the pipette box; on a slanted (wide) box, the lower corner away from the hand."""
    x1, y1, x2, y2 = box
    if hand is None or (x2 - x1) < 0.4 * (y2 - y1):
        return ((x1 + x2) / 2, y2)
    return (x1, y2) if hand.center[0] > (x1 + x2) / 2 else (x2, y2)


def upright(box) -> float:
    return (box[3] - box[1]) / max(box[2] - box[0], 1.0) if box else 0.0


class DipTracker:
    def __init__(self, cam: int, rules: DrawRules | None = None):
        self.cam, self.r = cam, rules or DrawRules()
        self.conts: list = []  # (last seen, Det)
        self.tipboxes: list = []
        self.cur: dict | None = None
        self.hist: list = []  # (t, tip point), last 2 s
        self.rest: dict = {}
        self.frame_h = 1.0
        self.debug: dict = {}

    def set_containers(self, t, dets, w, h, tipboxes=()):
        r = self.r
        self.tipboxes, self.frame_h = list(tipboxes), float(h)
        keep = [(s, d) for s, d in self.conts if t - s <= r.cont_keep]
        for d in dets:
            if d.label not in r.labels or d.score < r.cont_min_score or area(d.box) > r.cont_max_frac * w * h:
                continue
            keep = [(s, k) for s, k in keep if not (k.label == d.label and iou(k.box, d.box) > 0.3)]
            keep.append((t, d))
        self.conts = keep

    def _container(self, pt, pip_box):
        if any(inside(pt, tb.box) for tb in self.tipboxes):
            return Det(TIP_BOX, next(tb.box for tb in self.tipboxes if inside(pt, tb.box)), 1.0)
        cands = [d for _, d in self.conts
                 if not (pip_box is not None and iou(d.box, pip_box) > self.r.pip_iou)
                 and not any(inside(center(d.box), tb.box) for tb in self.tipboxes)
                 and inside(pt, d.box, self.r.pad)]
        return min(cands, key=lambda d: area(d.box)) if cands else None

    def _still_dip(self, t, pt):
        r = self.r
        recent = [(s, q) for s, q in self.hist if t - s <= r.still_win]
        if len(recent) < 3 or t - recent[0][0] < 0.75 * r.still_win:
            return None
        if max(dist(q, pt) for _, q in recent) > r.still_px:
            return None
        if self.cur is None and pt[1] - min(q[1] for s, q in self.hist if t - s <= 1.0) < r.down_px:
            return None
        return Det("spot", (pt[0] - 20, pt[1] - 20, pt[0] + 20, pt[1] + 20), 0.2)

    def _pressed(self, hand) -> bool:
        if hand is None or not hand.pts:
            return False
        ratio = hand.thumb_ratio()
        rest = self.rest.get(hand.side, ratio)
        if ratio < rest - self.r.thumb_drop:
            return True
        self.rest[hand.side] = rest + 0.05 * (ratio - rest)
        return False

    def update(self, t, pip_box, hand) -> list:
        r, out = self.r, []
        pt = tip_point(pip_box, hand) if pip_box is not None else None
        cont = self._container(pt, pip_box) if pt is not None else None
        pressed = self._pressed(hand)
        if pt is not None:
            self.hist = [(s, q) for s, q in self.hist if t - s <= max(2.0, r.pre_win + 0.5)] + [(t, pt)]
            if cont is None and r.dip:
                cont = self._still_dip(t, pt)
        self.debug = {"tip_point": pt, "container": cont.label if cont else None}
        c = self.cur
        if c is not None and cont is not None and dist(center(cont.box), c["xy"]) > r.same_px * max(c["size"], 1.0):
            out += self._close(c)  # moved straight into another container
            c = self.cur = None
        if cont is not None:
            if c is None:
                recent_y = [q[1] for s, q in self.hist if t - s <= 1.0]
                self.cur = {"t0": t, "t1": t, "label": cont.label, "xy": center(cont.box),
                            "size": max(cont.box[2] - cont.box[0], cont.box[3] - cont.box[1]),
                            "down": bool(recent_y) and pt[1] - min(recent_y) >= r.down_px,
                            "pressed": pressed, "hand": hand.side if hand else None,
                            "shape": [upright(pip_box)],
                            "pre": [q for s, q in self.hist if t - r.pre_win <= s <= t]}
            else:
                c["t1"] = t
                c["pressed"] = c["pressed"] or pressed
                c["shape"].append(upright(pip_box))
        elif c is not None and t - c["t1"] > r.gap:
            out += self._close(c)
            self.cur = None
        return out

    def _close(self, c) -> list:
        r = self.r
        if c["t1"] - c["t0"] < r.min_dwell:
            return []
        sh = sorted(c["shape"])
        pre = c["pre"]
        speed = sum(dist(a, b) for a, b in zip(pre, pre[1:])) / max(r.pre_win, 1e-3) / self.frame_h if len(pre) > 1 else 0.0
        e = Entry(self.cam, c["t0"], c["t1"], c["label"], c["xy"], c["size"], c["down"], c["pressed"], c["hand"],
                  round(sh[len(sh) // 2], 2), round(speed, 3))
        if e.label != TIP_BOX:
            if r.min_upright and e.upright < r.min_upright:
                return []  # pipette lying down, or the whole hand side on
            if r.max_pre_speed and e.pre_speed > r.max_pre_speed:
                return []  # a hand passing by that happened to stop
        return [e]

    def finish(self) -> list:
        out = self._close(self.cur) if self.cur else []
        self.cur = None
        return out


@dataclass
class _Group:
    t0: float
    t1: float
    by_cam: dict = field(default_factory=dict)


class Sequencer:
    """All cameras: push() entries as they close, flush(now) every frame returns events."""

    def __init__(self, rules: DrawRules | None = None):
        self.r = rules or DrawRules()
        self.pending: list = []
        self.loaded = False

    def tip_on(self):
        self.loaded = False

    def push(self, e: Entry):
        for g in self.pending:
            if e.cam not in g.by_cam and e.t0 <= g.t1 + self.r.fuse_gap and g.t0 <= e.t1 + self.r.fuse_gap:
                g.by_cam[e.cam] = e
                g.t0, g.t1 = min(g.t0, e.t0), max(g.t1, e.t1)
                return
        self.pending.append(_Group(e.t0, e.t1, {e.cam: e}))

    def flush(self, now: float, final: bool = False) -> list:
        out = []
        for g in sorted((g for g in self.pending if final or now - g.t1 >= self.r.fuse_gap), key=lambda g: g.t0):
            self.pending.remove(g)
            out += self._name(g)
        return out

    def _name(self, g) -> list:
        if any(e.label == TIP_BOX for e in g.by_cam.values()):
            self.tip_on()  # tip end in the tip box: a fresh tip, so the next entry is a draw
            return []
        kind = "dispense" if self.loaded else "draw"
        self.loaded = not self.loaded
        best = max(g.by_cam.values(), key=lambda e: e.score)
        named = [e for e in g.by_cam.values() if e.label != "spot"]
        obj = max(named, key=lambda e: e.score).label if named else None
        conf = round(min(0.9, best.score + 0.1 * (len(g.by_cam) - 1)), 2)
        return [event(g.t1, kind, best.cam, obj, best.hand, conf,
                      {"cams": sorted(g.by_cam), "start": round(g.t0, 2), "end": round(g.t1, 2), "hint": True})]
