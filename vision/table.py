"""Table mode: one fixed overhead camera, coloured tube caps and a magenta band on the pipette.

    $BENCH_PY -m vision.table setup --source 0            # or a video file, or a capture frame URL
    $BENCH_PY -m vision.table run --source http://localhost:8701/cam/0/frame.jpg

Setup finds the red, blue and yellow caps (tubes A, B and C) by colour in one still and asks for
the tip box and waste (clicks, or --tip-box X Y R --waste X Y R). Run follows the magenta band
and names visits by their order:
- tip box visit with no tip on -> tip_on; waste visit with a tip on -> tip_off
- first tube visit after a fresh tip (or after a dispense) -> draw; the next visit to a
  different tube -> dispense
- back into the same tube within `mix_gap` s -> mixing, sent as hand_touch with detail.action = "mix"
  (the event list in docs/EVENTS.md has no mix kind)
A visit needs the band seen going in, `dwell` s inside, and seen coming out; losing the band for
more than `lost` s drops the visit rather than guessing.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from .core import event
from .sink import Sink
from .table_colour import BandTracker, learn, propose_caps, quality

CONFIG = Path(__file__).with_name("table.json")
TUBES = ("tube A", "tube B", "tube C")
NAMES = TUBES + ("tip box", "waste")
# OpenCV hue 0-179; saturation and value floors keep the grey bench out.
HUES = {"tube A": [(0, 10), (170, 179)], "tube B": [(100, 130)], "tube C": [(20, 35)], "marker": [(140, 165)]}
SAT_MIN, VAL_MIN = 110, 80


def colour_blob(frame, name, min_area=30):
    """(x, y, radius) of the largest blob of that colour, or None."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = np.zeros(frame.shape[:2], np.uint8)
    for lo, hi in HUES[name]:
        mask |= cv2.inRange(hsv, (lo, SAT_MIN, VAL_MIN), (hi, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cs = [c for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0] if cv2.contourArea(c) >= min_area]
    if not cs:
        return None
    (x, y), r = cv2.minEnclosingCircle(max(cs, key=cv2.contourArea))
    return [float(x), float(y), float(r)]


def find_caps(frame) -> dict:
    return propose_caps(frame)


def marker(frame):
    return BandTracker().update(frame, 0.)


# Compatibility with preflight's public detector boundary.
marker_tip = marker


def validate(cfg: dict) -> dict:
    w, h = cfg["size"]
    regs = cfg["regions"]
    missing = [n for n in NAMES if n not in regs]
    if missing:
        raise ValueError("missing regions: " + ", ".join(missing))
    for n in NAMES:
        x, y, r = regs[n]
        if not (0 <= x < w and 0 <= y < h and r > 0):
            raise ValueError(f"region outside the picture: {n}")
    for i, a in enumerate(NAMES):
        for b in NAMES[i + 1:]:
            if math.dist(regs[a][:2], regs[b][:2]) <= regs[a][2] + regs[b][2]:
                raise ValueError(f"regions overlap: {a} and {b}; use smaller radii")
    return cfg


class TableTracker:
    def __init__(self, regions: dict, dwell: float = 0.15, lost: float = 0.5, mix_gap: float = 4.0):
        self.regions, self.dwell, self.lost, self.mix_gap = regions, dwell, lost, mix_gap
        self.active, self.entered, self.fired = None, 0.0, False
        self.last_seen = None
        self.tip = False
        self.loaded = False
        self.last_tube, self.last_exit = None, -math.inf

    def _ev(self, t, kind, obj, **detail):
        return event(t, kind, 0, obj, None, 0.75, {"mode": "table", **detail})

    def region(self, pt):
        return next((n for n, (x, y, r) in self.regions.items() if math.dist(pt, (x, y)) <= r), None)

    def update(self, t: float, pt) -> list:
        out = []
        if self.last_seen is not None and t - self.last_seen > self.lost:
            self.active = None  # band hidden too long: drop the visit, never invent its exit
        if pt is None:
            return out
        self.last_seen = t
        reg = self.region(pt)
        if reg != self.active:
            if self.active in TUBES and t - self.entered >= self.dwell and self.tip:
                out += self._tube_visit(t, self.active)
            self.active, self.entered, self.fired = reg, t, False
        if reg in ("tip box", "waste") and not self.fired and t - self.entered >= self.dwell:
            self.fired = True
            if reg == "tip box" and not self.tip:
                out.append(self._ev(t, "tip_on", "tip box"))
                self.tip = True
                self._fresh()
            elif reg == "waste" and self.tip:
                out.append(self._ev(t, "tip_off", "waste"))
                self.tip = False
                self._fresh()
        return out

    def _fresh(self):
        self.loaded, self.last_tube, self.last_exit = False, None, -math.inf

    def _tube_visit(self, t, tube) -> list:
        out = []
        if tube == self.last_tube and self.entered - self.last_exit <= self.mix_gap:
            out.append(self._ev(t, "hand_touch", tube, action="mix"))
        elif not self.loaded:
            out.append(self._ev(t, "draw", tube))
            self.loaded = True
        elif tube != self.last_tube:
            out.append(self._ev(t, "dispense", tube))
            self.loaded = False
        self.last_tube, self.last_exit = tube, t
        return out


class Source:
    """A camera index, a video file, or a capture frame URL (keeps its X-Frame-Time)."""

    def __init__(self, spec: str):
        self.http = self.cap = None
        self.fps = 30.0
        if spec.startswith(("http://", "https://")):
            from .sources import HttpSource
            self.http = HttpSource(spec, 30.0, 100000)
            self.live = True
        else:
            self.cap = cv2.VideoCapture(int(spec) if spec.isdecimal() else spec)
            self.live = spec.isdecimal()
            f = self.cap.get(cv2.CAP_PROP_FPS)
            if f and math.isfinite(f) and f > 0:
                self.fps = f

    def read(self):
        if self.http:
            return self.http.read()
        ok, frame = self.cap.read()
        return time.time(), (frame if ok else None)

    def close(self):
        if self.http:
            self.http.close()
        if self.cap:
            self.cap.release()


def draw_regions(frame, regions):
    out = frame.copy()
    for n, (x, y, r) in regions.items():
        cv2.circle(out, (round(x), round(y)), round(r), (0, 255, 0), 2)
        cv2.putText(out, n, (round(x - r), round(y - r - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    return out


def setup(a):
    src = Source(a.source)
    try:
        _, frame = src.read()
    finally:
        src.close()  # never hold the camera while waiting for clicks
    if frame is None:
        raise ValueError("cannot read a frame from the source")
    regs = {} if a.manual else find_caps(frame)
    if a.tip_box:
        regs["tip box"] = list(a.tip_box)
    if a.waste:
        regs["waste"] = list(a.waste)
    missing = [n for n in NAMES if n not in regs]
    if missing and a.headless:
        raise ValueError("not found: " + ", ".join(missing))
    if missing or not a.headless:
        win = "table setup: click the centre asked for, Enter saves, Esc cancels"
        cv2.namedWindow(win)
        cv2.setMouseCallback(win, lambda ev, x, y, *_: ev == cv2.EVENT_LBUTTONDOWN and missing
                             and regs.__setitem__(missing.pop(0), [x, y, a.radius]))
        try:
            while True:
                img = draw_regions(frame, regs)
                cv2.putText(img, f"click {missing[0]}" if missing else "Enter saves", (10, 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                cv2.imshow(win, img)
                k = cv2.waitKey(30) & 255
                if k == 27:
                    raise ValueError("setup cancelled")
                if k in (10, 13) and not missing:
                    break
        finally:
            cv2.destroyAllWindows()
    cfg = validate({"size": [frame.shape[1], frame.shape[0]], "regions": regs})
    cfg["colour_calibration"] = learn(frame, regs)
    score, issues = quality(frame, cfg)
    cfg["setup_quality"] = score
    print(f"# setup quality {score}/100" + (": " + "; ".join(issues) if issues else ""), file=sys.stderr)
    Path(a.config).write_text(json.dumps(cfg, indent=1) + "\n")
    print(f"# saved {a.config}", file=sys.stderr)


def run(a):
    cfg = validate(json.loads(Path(a.config).read_text()))
    if a.follow_runs and a.hub == "none":
        raise ValueError("--follow-runs needs a hub")
    from .__main__ import serve_health
    import httpx
    stats = {"mode": "table", "frames": 0, "run": None}
    server = serve_health(a.port, stats) if a.port else None
    control = httpx.Client(timeout=2.0) if a.follow_runs else None
    src = None if a.follow_runs else Source(a.source)
    sink = Sink(None if a.hub == "none" else a.hub, a.log)
    trk = TableTracker(cfg["regions"])
    band = BandTracker(cfg.get("colour_calibration"))
    start, i = time.time(), 0
    active_run, finished_run, next_poll = None, None, 0.0
    try:
        while True:
            if control and time.monotonic() >= next_poll:
                response = control.get(a.hub.rstrip("/") + "/state")
                response.raise_for_status()
                state = response.json()
                run_id = state.get("run") if state.get("recording") else None
                if run_id != active_run:
                    if src:
                        src.close()
                    src = None
                    active_run = run_id
                    stats["run"] = run_id
                    if run_id and run_id != finished_run:
                        src = Source(a.source)
                        trk = TableTracker(cfg["regions"])
                        band = BandTracker(cfg.get("colour_calibration"))
                        start, i = time.time(), 0
                next_poll = time.monotonic() + 0.1
            if src is None:
                time.sleep(0.03)
                continue
            stamp, frame = src.read()
            if frame is None:
                if i == 0:
                    raise ValueError("cannot read the source")
                if control:
                    src.close()
                    src = None
                    finished_run = active_run
                    continue
                break
            if [frame.shape[1], frame.shape[0]] != cfg["size"]:
                raise ValueError("frame size differs from setup; run setup again")
            t = stamp if src.live else start + i / src.fps
            if a.seconds and t - start > a.seconds:
                break
            if a.realtime and not src.live:
                time.sleep(max(0.0, t - time.time()))
            pt = band.update(frame, t)
            sink.send(trk.update(t, pt))
            i += 1
            stats["frames"] += 1
            if a.preview:
                img = draw_regions(frame, cfg["regions"])
                if pt:
                    cv2.circle(img, tuple(round(v) for v in pt), 5, (255, 255, 255), 2)
                cv2.imshow("table (Esc quits)", img)
                if cv2.waitKey(1) & 255 == 27:
                    break
    finally:
        if src:
            src.close()
        sink.close()
        if control:
            control.close()
        if server:
            server.should_exit = True
        if a.preview:
            cv2.destroyAllWindows()


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m vision.table")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("setup", "run"):
        s = sub.add_parser(name)
        s.add_argument("--source", default="0", help="camera index, video file or capture frame URL")
        s.add_argument("--config", default=str(CONFIG))
        if name == "setup":
            s.add_argument("--manual", action="store_true", help="click all five centres")
            s.add_argument("--headless", action="store_true", help="no window; fail if anything is missing")
            s.add_argument("--radius", type=float, default=25.0)
            s.add_argument("--tip-box", type=float, nargs=3, metavar=("X", "Y", "R"))
            s.add_argument("--waste", type=float, nargs=3, metavar=("X", "Y", "R"))
        else:
            s.add_argument("--port", type=int, default=0, help="health port; 0 disables it")
            s.add_argument("--follow-runs", action="store_true", help="reset on hub Start; release source on Stop")
            s.add_argument("--hub", default="http://localhost:8700")
            s.add_argument("--log")
            s.add_argument("--seconds", type=float, default=0.0)
            s.add_argument("--realtime", action="store_true")
            s.add_argument("--preview", action="store_true")
    a = p.parse_args(argv)
    try:
        (setup if a.cmd == "setup" else run)(a)
    except KeyboardInterrupt:
        pass
    except (ValueError, KeyError, OSError) as e:
        p.exit(2, f"table: {e}\n")


if __name__ == "__main__":
    main()
