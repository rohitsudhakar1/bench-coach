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
    # Naming checks (vision/DISPENSE-REPORT.md). 0 turns each off.
    merge_gap: float = 0.0  # a group starting this soon after the last named one is the same action: no event
    merge_move: float = 0.0  # ...and only when it is within this many container sizes of it (0: anywhere)
    same_place: float = 0.0  # loaded and back at the draw's place, or empty and back at the dispense's: no event
    by_place: float = 0.0  # name by place, not by turn: within this many container sizes of the source is a draw
    follow_source: bool = False  # by_place: every draw moves the source (else it stays at the first draw after a tip)
    near_last: float = 0.0  # name by distance: within this many container sizes of the last entry since a tip is a dispense
    # Dispense checks: the turn still flips (so draws keep their names), the dispense hint is just not sent.
    disp_near: float = 0.0  # send a dispense only within this many container sizes of the entry before it, same camera
    disp_min_gap: float = 0.0  # ...and only when it starts at least this long after the entry before it
    disp_min_dwell: float = 0.0  # ...and only when the tip stayed in at least this long


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
    thumb: tuple = ()  # thumb ratio minus its rest value, first and last while in (empty: no finger points)
    thumb_min: float | None = None
    rise: float = 0.0  # how far the tip end went up in the gap after leaving, in frame heights
    lean: float = 0.0  # pipette box width / height while in (0 upright, bigger leaning)

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
        self.thumb_rel = None

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
            self.thumb_rel = None
            return False
        ratio = hand.thumb_ratio()
        rest = self.rest.get(hand.side, ratio)
        self.thumb_rel = ratio - rest
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
                            "pre": [q for s, q in self.hist if t - r.pre_win <= s <= t],
                            "thumb": [self.thumb_rel] if self.thumb_rel is not None else [],
                            "y_in": pt[1], "y_min_after": None}
            else:
                c["t1"] = t
                c["pressed"] = c["pressed"] or pressed
                c["shape"].append(upright(pip_box))
                c["y_in"] = pt[1]
                c["y_min_after"] = None
                if self.thumb_rel is not None:
                    c["thumb"].append(self.thumb_rel)
        elif c is not None and pt is not None:
            c["y_min_after"] = pt[1] if c["y_min_after"] is None else min(c["y_min_after"], pt[1])
        if c is not None and cont is None and self.cur is not None and t - c["t1"] > r.gap:
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
        th = c.get("thumb") or []
        ya = c.get("y_min_after")
        up = sh[len(sh) // 2]
        e = Entry(self.cam, c["t0"], c["t1"], c["label"], c["xy"], c["size"], c["down"], c["pressed"], c["hand"],
                  round(up, 2), round(speed, 3),
                  (round(th[0], 3), round(th[-1], 3)) if th else (), round(min(th), 3) if th else None,
                  round((c.get("y_in", 0) - ya) / self.frame_h, 3) if ya is not None else 0.0,
                  round(1.0 / max(up, 1e-3), 3))
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
        self.last = None  # (t1, {cam: entry}) of the last named group
        self.place = {}  # "draw" / "dispense": {cam: entry} where the last one of each was

    def tip_on(self):
        self.loaded = False
        self.last, self.place = None, {}

    @staticmethod
    def _moved(g, where) -> float | None:
        """Shortest distance from g's entries to the entries in `where` on the same camera, in container sizes."""
        d = [dist(e.xy, where[e.cam].xy) / max(e.size, where[e.cam].size, 1.0) for e in g.by_cam.values() if e.cam in where]
        return min(d) if d else None

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
        r = self.r
        if r.merge_gap and self.last is not None and g.t0 - self.last[0] <= r.merge_gap:
            m = self._moved(g, self.last[1])
            if not r.merge_move or (m is not None and m <= r.merge_move):
                self.last = (max(self.last[0], g.t1), {**self.last[1], **g.by_cam})
                return []  # one dip seen as two: keep the name it already has
        kind = "dispense" if self.loaded else "draw"
        if r.near_last:
            m = self._moved(g, self.last[1]) if self.last is not None else None
            kind = "dispense" if m is not None and m <= r.near_last else "draw"
            self.loaded = kind == "draw"
            self.last = (g.t1, dict(g.by_cam))
            return self._event(g, kind)
        if r.by_place:
            src = self.place.get("source")
            m = self._moved(g, src) if src else None
            kind = "draw" if src is None or (m is not None and m <= r.by_place) else "dispense"
            if src is None or (kind == "draw" and r.follow_source):
                self.place["source"] = {**(src or {}), **g.by_cam}
            self.loaded = kind == "draw"
            self.last = (g.t1, dict(g.by_cam))
            self.place[kind] = dict(g.by_cam)
            return self._event(g, kind)
        if r.same_place:
            m = self._moved(g, self.place.get("draw" if self.loaded else "dispense", {}))
            if m is not None and m <= r.same_place:
                self.last = (g.t1, dict(g.by_cam))
                return []  # back where the last draw (or dispense) was: more of that, not the next action
        prev = self.last
        self.loaded = not self.loaded
        self.last = (g.t1, dict(g.by_cam))
        self.place[kind] = dict(g.by_cam)
        if kind == "dispense" and not self._dispense_ok(g, prev):
            return []
        return self._event(g, kind)

    def _dispense_ok(self, g, prev) -> bool:
        r = self.r
        if r.disp_min_dwell and max(e.t1 - e.t0 for e in g.by_cam.values()) < r.disp_min_dwell:
            return False
        if prev is None:
            return not (r.disp_near or r.disp_min_gap)
        if r.disp_min_gap and g.t0 - prev[0] < r.disp_min_gap:
            return False
        if r.disp_near:
            m = self._moved(g, prev[1])
            return m is not None and m <= r.disp_near
        return True

    def _event(self, g, kind) -> list:
        best = max(g.by_cam.values(), key=lambda e: e.score)
        named = [e for e in g.by_cam.values() if e.label != "spot"]
        obj = max(named, key=lambda e: e.score).label if named else None
        conf = round(min(0.9, best.score + 0.1 * (len(g.by_cam) - 1)), 2)
        return [event(g.t1, kind, best.cam, obj, best.hand, conf,
                      {"cams": sorted(g.by_cam), "start": round(g.t0, 2), "end": round(g.t1, 2), "hint": True})]
