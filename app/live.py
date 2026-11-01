"""Live view: the camera picture with every detected object outlined and named, both hands drawn,
and the protocol steps and coach lines beside it.

    $BENCH_PY -m app.live --source 0                      # camera 0
    $BENCH_PY -m app.live --source clip.mp4 --realtime    # a video file played at real speed
    $BENCH_PY -m app.live --source 0 --fallback clip.mp4  # camera, or the clip if the camera fails

The page is at http://127.0.0.1:8706/ (the hub's /live sends you there). The picture is redrawn on
every camera frame with the newest boxes, outlines and hands; the box model (Grounding DINO tiny,
the same one vision uses) runs in its own thread as fast as it can and the picture never waits
for it. Steps and coach lines come from the hub's /state; without a hub the page still shows the
picture. --post-events also sends vision's events to the hub, for when the vision part is not
running on this camera.
"""
from __future__ import annotations

import argparse
import asyncio
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from app.live_draw import overlay

ROOT = Path(__file__).resolve().parent
HAND = "hand"


def fit(frame, max_side: int):
    h, w = frame.shape[:2]
    s = max_side / max(h, w)
    if s >= 1:
        return frame
    return cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)


class Rate:
    """Events per second over the last `window` seconds."""

    def __init__(self, window: float = 2.0):
        self.window, self.times = window, deque()

    def tick(self, t: float | None = None):
        t = time.time() if t is None else t
        self.times.append(t)
        while self.times and t - self.times[0] > self.window:
            self.times.popleft()

    def value(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        while self.times and now - self.times[0] > self.window:
            self.times.popleft()
        if len(self.times) < 2:
            return 0.0
        return round((len(self.times) - 1) / max(self.times[-1] - self.times[0], 1e-3), 1)


class Source:
    """A camera index, a stream URL or a video file. Files with realtime=True play at their own
    speed: frames the loop was too slow for are skipped, never shown late. loop=True starts a
    file again at its end."""

    def __init__(self, spec: str, realtime: bool = True, loop: bool = False):
        self.spec = str(spec)
        self.camera = self.spec.isdigit()
        self.file = not self.camera and "://" not in self.spec
        self.cap = cv2.VideoCapture(int(self.spec) if self.camera else self.spec)
        self.ok = self.cap.isOpened()
        self.fps = (self.cap.get(cv2.CAP_PROP_FPS) or 30.0) if self.ok else 30.0
        if not 1 <= self.fps <= 240:
            self.fps = 30.0
        self.realtime, self.loop = realtime and self.file, loop and self.file
        self.pos, self.wall0, self.skipped = 0, None, 0

    def read(self):
        if not self.ok:
            return None
        if self.wall0 is None:
            self.wall0 = time.time()  # the clock starts at the first frame, after the models load
        if self.realtime:
            target = int((time.time() - self.wall0) * self.fps)
            while self.pos < target:  # behind: drop frames to stay at real speed
                if not self.cap.grab():
                    break
                self.pos += 1
                self.skipped += 1
            wait = self.wall0 + self.pos / self.fps - time.time()
            if wait > 0:
                time.sleep(wait)
        ok, frame = self.cap.read()
        if not ok:
            if self.loop and self.pos > 0:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.pos, self.wall0 = 0, time.time()
                ok, frame = self.cap.read()
            if not ok:
                return None
        self.pos += 1
        return frame

    def close(self):
        self.cap.release()


def outline(frame, box, side: int = 96, iters: int = 2, min_fill: float = 0.15):
    """The object's outline inside its box, from OpenCV GrabCut on a small copy of the box.
    Returns a polygon in frame pixels, or None when GrabCut finds nothing sensible
    (then the box is drawn instead)."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    if bw < 8 or bh < 8:
        return None
    px, py = 0.08 * bw, 0.08 * bh  # a margin of background around the box
    cx1, cy1 = int(max(0, x1 - px)), int(max(0, y1 - py))
    cx2, cy2 = int(min(w, x2 + px)), int(min(h, y2 + py))
    crop = frame[cy1:cy2, cx1:cx2]
    if crop.size == 0:
        return None
    s = side / max(crop.shape[:2])
    small = cv2.resize(crop, (max(8, int(crop.shape[1] * s)), max(8, int(crop.shape[0] * s))),
                       interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    rx1, ry1 = int((x1 - cx1) * s), int((y1 - cy1) * s)
    rx2, ry2 = int(min(sw - 1, (x2 - cx1) * s)), int(min(sh - 1, (y2 - cy1) * s))
    if rx2 - rx1 < 4 or ry2 - ry1 < 4:
        return None
    mask = np.zeros((sh, sw), np.uint8)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(small, mask, (rx1, ry1, rx2 - rx1, ry2 - ry1), bgd, fgd, iters, cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return None
    fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < min_fill * (rx2 - rx1) * (ry2 - ry1):
        return None
    c = cv2.approxPolyDP(c, 1.0, True)
    if len(c) < 3:
        return None
    return [[round(cx1 + p[0][0] / s, 1), round(cy1 + p[0][1] / s, 1)] for p in c]


class DetectorThread:
    """Runs the box model on the newest frame whenever it is free; the picture never waits for it."""

    def __init__(self, detect, outlines: bool = True, segmenter=None):
        self.detect, self.outlines, self.segmenter = detect, outlines, segmenter
        self.pending, self.objects, self.version, self.t = None, [], 0, None
        self.ms, self.outline_ms, self.rate = 0.0, 0.0, Rate()
        self.lock, self.wake, self.stopping = threading.Lock(), threading.Event(), False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def submit(self, t, frame):
        with self.lock:
            self.pending = (t, frame)
        self.wake.set()

    def latest(self):
        with self.lock:
            return self.version, self.t, list(self.objects)

    def _loop(self):
        while not self.stopping:
            self.wake.wait(0.2)
            with self.lock:
                job, self.pending = self.pending, None
                self.wake.clear()
            if job is None:
                continue
            t, frame = job
            t0 = time.perf_counter()
            try:
                dets = self.detect(frame)
            except Exception as e:  # keep the picture going if the model fails once
                print(f"# detector error: {type(e).__name__}: {e}", flush=True)
                time.sleep(0.5)
                continue
            t1 = time.perf_counter()
            objs = []
            things = [d for d in dets if d.label != HAND]
            masked = {}
            if self.outlines and self.segmenter is not None and things:
                # true outlines from the segmenter, all boxes in one call; a box it rejects stays a box
                from vision.masks import outlines as seg_outlines
                masked = {id(d): p for d, p in zip(things, seg_outlines(self.segmenter, frame, [d.box for d in things]))}
            for d in dets:
                o = {"label": d.label, "box": [round(v, 1) for v in d.box], "score": round(d.score, 2),
                     "outline": None}
                if id(d) in masked:
                    p = masked[id(d)]
                    o["outline"] = [[round(x, 1), round(y, 1)] for x, y in p] if p else None
                elif self.outlines and d.label != HAND:
                    o["outline"] = outline(frame, d.box)
                objs.append(o)
            t2 = time.perf_counter()
            with self.lock:
                self.objects, self.t = objs, t
                self.version += 1
                self.ms, self.outline_ms = (t1 - t0) * 1000, (t2 - t1) * 1000
                self.rate.tick()

    def close(self):
        self.stopping = True
        self.wake.set()
        self.thread.join(timeout=5)


class HubState:
    """Polls the hub's /state for steps and coach lines. Empty while the hub is down."""

    def __init__(self, hub: str | None, every: float = 0.5, client=None):
        self.url = hub.rstrip("/") + "/state" if hub else None
        self.every, self.client = every, client
        self.data = summarise(None)
        self.stopping = False
        if self.url:
            if self.client is None:
                import httpx
                self.client = httpx.Client(timeout=1.0)
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def poll(self):
        try:
            r = self.client.get(self.url)
            self.data = summarise(r.json() if r.status_code == 200 else None)
        except Exception:
            self.data = summarise(None)

    def _loop(self):
        while not self.stopping:
            self.poll()
            time.sleep(self.every)

    def close(self):
        self.stopping = True


def summarise(state: dict | None) -> dict:
    """What the page shows beside the picture, from the hub's /state."""
    if not state:
        return {"ok": False, "run": None, "protocol": None, "steps": [], "current": None, "coach": [],
                "warnings": [], "question": None}
    proto = state.get("protocol") or {}
    status = state.get("steps") or {}
    steps = []
    for i, s in enumerate(proto.get("steps") or []):
        st = status.get(str(i)) or {}
        steps.append({"index": i, "text": s.get("text", ""), "status": st.get("status"),
                      "evidence": st.get("source") or st.get("evidence")})
    coach = [{"who": "coach" if e.get("kind") == "say" else "you", "text": (e.get("detail") or {}).get("text", ""),
              "t": e.get("t")} for e in (state.get("chat") or [])[-8:]]
    warnings = [(w.get("detail") or {}).get("text", "") for w in (state.get("warnings") or [])[-3:]]
    return {"ok": True, "run": state.get("run"), "protocol": proto.get("name") or proto.get("title"),
            "steps": steps, "current": state.get("current_step"), "coach": coach, "warnings": warnings,
            "question": state.get("question")}


class LiveView:
    """Reads frames, finds hands on every frame, draws the newest boxes and keeps the newest JPEG."""

    def __init__(self, source: Source, detect=None, hands=None, hub: HubState | None = None,
                 max_side: int = 960, outlines: bool = True, fallback: str | None = None,
                 vision=None, sink=None, quality: int = 80, segmenter=None,
                 filter_labels: bool = False, protocol=None):
        self.src, self.fallback, self.max_side, self.quality = source, fallback, max_side, quality
        self.det = DetectorThread(detect, outlines, segmenter) if detect is not None else None
        self.hands_fn, self.hub = hands, hub or HubState(None)
        self.vision, self.sink = vision, sink
        self.filter_labels, self.filter_protocol = filter_labels, protocol
        self.label_filter, self.filter_key = None, None
        self.hand_history = deque(maxlen=180)
        self.filter_start = -float("inf")
        self.jpeg, self.version, self.frame_t, self.size = None, 0, None, (0, 0)
        self.objects, self.hands = [], []
        self.rate, self.hand_ms, self.draw_ms = Rate(), 0.0, 0.0
        self.using_fallback, self.status = False, "starting"
        self.lock, self.cond = threading.Lock(), threading.Condition()
        self.stopping, self.seen_det = False, 0
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def _switch_to_fallback(self, why: str):
        print(f"# {why}; playing {Path(self.fallback).name} instead", flush=True)
        self.src.close()
        self.src = Source(self.fallback, realtime=True, loop=True)
        self.using_fallback = True
        if self.label_filter is not None:
            self.label_filter.reset()
            self.filter_start = time.time()
            self.objects = []
            self.hand_history.clear()

    def step(self, frame, t: float):
        """One frame: hands, newest boxes, overlay, JPEG. Returns the drawn picture."""
        frame = fit(frame, self.max_side)
        h, w = frame.shape[:2]
        raw = None
        if self.det is not None:
            self.det.submit(t, frame)
            v, detector_t, objs = self.det.latest()
            if v != self.seen_det:
                self.seen_det, self.objects, raw = v, objs, objs
        t0 = time.perf_counter()
        found = self.hands_fn(frame, t) if self.hands_fn else []
        t1 = time.perf_counter()
        if self.filter_labels:
            from vision.labelfilter import LabelFilter
            # Explicit file wins; otherwise use only the hub's current instruction lines.
            protocol = self.filter_protocol if self.filter_protocol is not None else {
                "steps": [{"text": step.get("text", "")}
                          for step in self.hub.data.get("steps", [])]}
            key = (repr(protocol), self.hub.data.get("run"), (w, h))
            if key != self.filter_key:
                self.label_filter, self.filter_key = LabelFilter(protocol), key
                self.filter_start = t
                self.hand_history.clear()
            self.hand_history.append((t, found))
            if raw is not None and detector_t is not None and detector_t >= self.filter_start:
                historical = next((hs for ht, hs in reversed(self.hand_history)
                                   if ht <= detector_t and detector_t - ht <= 0.25), [])
                self.label_filter.update(detector_t, raw, historical)
            self.objects = self.label_filter.visible(t)
        hands = [{"side": hd.side, "pts": [[round(x, 1), round(y, 1)] for x, y in hd.pts] if hd.pts else None,
                  "box": list(hd.box) if hd.box else None} for hd in found]
        # A detector hand box no tracked hand explains is still drawn, as a box.
        shown = [o for o in self.objects if o["label"] != HAND]
        for o in self.objects:
            if o["label"] == HAND:
                x1, y1, x2, y2 = o["box"]
                if not any(hd.pts and x1 <= sum(p[0] for p in hd.pts) / 21 <= x2
                           and y1 <= sum(p[1] for p in hd.pts) / 21 <= y2 for hd in found):
                    hands.append({"side": "left" if (x1 + x2) / 2 < w / 2 else "right", "pts": None, "box": o["box"]})
        img = overlay(frame, shown, hands)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        t2 = time.perf_counter()
        if self.vision is not None:
            self._events(t, w, h, found, raw)
        with self.cond:
            if ok:
                self.jpeg = buf.tobytes()
            self.version += 1
            self.frame_t, self.size, self.hands = t, (w, h), hands
            self.hand_ms, self.draw_ms = (t1 - t0) * 1000, (t2 - t1) * 1000
            self.rate.tick()
            self.cond.notify_all()
        return img

    def _events(self, t, w, h, found, raw):
        from vision.core import Det
        dets = [Det(o["label"], tuple(o["box"]), o["score"]) for o in raw] if raw is not None else None
        events = self.vision.step(0, t, w, h, found, dets)
        if self.label_filter is not None:
            events = self.label_filter.filter_events(events, t, found or None)
            for event in events:
                if event["kind"] == "objects":
                    from vision.core import iou
                    original = event["detail"]["boxes"]
                    boxes = [{k: v for k, v in o.items() if k != "outline"}
                             for o in self.label_filter.visible(t) if o["label"] != HAND]
                    for box in boxes:
                        if "held" in box:
                            # Never undo the pipeline's tripod/stationary rejection.
                            box["held"] = box["held"] and any(
                                old.get("held") is True and iou(old["box"], box["box"]) >= .25
                                for old in original if old["label"] == box["label"])
                    event["detail"]["boxes"] = boxes
        if self.sink is not None and events:
            self.sink.send(events)

    def run(self):
        self.status = "running"
        last_frame = time.time()
        if not self.src.ok and self.fallback:
            self._switch_to_fallback(f"cannot open source {self.src.spec}")
        while not self.stopping:
            frame = self.src.read()
            if frame is None:
                if self.src.camera and not self.using_fallback and time.time() - last_frame < 3:
                    time.sleep(0.05)  # cameras sometimes drop a frame
                    continue
                if self.fallback and not self.using_fallback:
                    self._switch_to_fallback(f"no frames from {self.src.spec}")
                    continue
                self.status = "ended"
                break
            last_frame = time.time()
            self.step(frame, last_frame)
        with self.cond:
            self.cond.notify_all()

    def wait_frame(self, after: int, timeout: float = 1.0):
        with self.cond:
            self.cond.wait_for(lambda: self.version != after or self.stopping, timeout)
            return self.version, self.jpeg

    def snapshot(self) -> dict:
        now = time.time()
        d = self.det
        with self.cond:
            snap = {
                "status": self.status,
                "source": {"name": Path(self.src.spec).name if self.src.file else self.src.spec,
                           "camera": self.src.camera, "fallback": self.using_fallback,
                           "skipped_frames": self.src.skipped},
                "frame": {"width": self.size[0], "height": self.size[1], "t": self.frame_t},
                "fps": {"picture": self.rate.value(now), "detections": d.rate.value(now) if d else 0.0},
                "ms": {"hands": round(self.hand_ms, 1), "draw": round(self.draw_ms, 1),
                       "detector": round(d.ms, 1) if d else None, "outlines": round(d.outline_ms, 1) if d else None},
                "objects": [dict(o) for o in self.objects],
                "hands": [{"side": hd["side"], "points": bool(hd["pts"])} for hd in self.hands],
                "detector_t": d.t if d else None,
            }
        snap["hub"] = self.hub.data
        return snap

    def close(self):
        self.stopping = True
        if self.thread:
            self.thread.join(timeout=5)
        if self.det:
            self.det.close()
        self.hub.close()
        self.src.close()


def create_app(view):
    """The page, the drawn picture as MJPEG and one JPEG, and the page's data as JSON.
    `view` needs snapshot(), wait_frame(after, timeout) and a jpeg attribute."""
    from fastapi import FastAPI, Response
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

    app = FastAPI()

    @app.get("/")
    @app.get("/live")
    async def page():
        return FileResponse(ROOT / "static/live.html", headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    async def health():
        return {"ok": True, "part": "live"}

    @app.get("/data")
    async def data():
        return JSONResponse(view.snapshot(), headers={"Cache-Control": "no-store",
                                                      "Access-Control-Allow-Origin": "*"})

    @app.get("/frame.jpg")
    async def frame():
        if view.jpeg is None:
            return Response(status_code=503)
        return Response(view.jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/stream.mjpeg")
    async def stream():
        async def parts():
            seen = -1
            while True:
                seen, jpg = await asyncio.to_thread(view.wait_frame, seen, 1.0)
                if jpg is None:
                    if getattr(view, "stopping", False):
                        return
                    continue
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n"
        return StreamingResponse(parts(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    app.mount("/static", __import__("fastapi.staticfiles", fromlist=["StaticFiles"]).StaticFiles(
        directory=ROOT / "static"), name="static")
    return app


def parse(argv=None):
    p = argparse.ArgumentParser(prog="python -m app.live", description=__doc__.split("\n")[0])
    p.add_argument("--source", required=True, help="camera index (0), video file, or stream URL")
    p.add_argument("--realtime", action="store_true", help="files: play at real speed (on by default for files)")
    p.add_argument("--fast", action="store_true", help="files: as fast as the machine goes (for timing)")
    p.add_argument("--loop", action="store_true", help="files: start again at the end")
    p.add_argument("--fallback", help="video file to play (looped) if the camera cannot be opened or stops")
    p.add_argument("--hub", default="http://127.0.0.1:8700", help="hub base URL for steps and coach lines, or 'none'")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8706)
    p.add_argument("--max-side", type=int, default=960, help="picture size (longest side)")
    p.add_argument("--no-outlines", action="store_true", help="boxes only")
    p.add_argument("--masks", action=argparse.BooleanOptionalAction, default=True,
                   help="true outlines from EdgeTAM (vision/masks.py); --no-masks uses the quick GrabCut cutout")
    p.add_argument("--no-detector", action="store_true", help="hands only")
    p.add_argument("--no-hands", action="store_true")
    p.add_argument("--mirrored", action="store_true", help="camera image is mirrored (selfie view)")
    p.add_argument("--filter-labels", action="store_true",
                   help="filter labels using --protocol or the hub's current protocol")
    p.add_argument("--protocol", type=Path, help="protocol .txt or .yaml for --filter-labels")
    p.add_argument("--post-events", action="store_true",
                   help="also send vision's events to the hub (only when the vision part is not running)")
    p.add_argument("--seconds", type=float, default=0.0, help="stop after this long and print the measured rates")
    return p.parse_args(argv)


def main(argv=None):
    a = parse(argv)
    import uvicorn

    protocol = None
    if a.filter_labels and a.protocol:
        from scripts.render_demo import load_protocol
        protocol = load_protocol(a.protocol)
    src = Source(a.source, realtime=not a.fast, loop=a.loop)
    if not src.ok and not a.fallback:
        raise SystemExit(f"cannot open source {a.source}; give --fallback clip.mp4 to play a video instead")
    detect = None
    if not a.no_detector:
        from vision.zoo import make
        detect = make("gdino-tiny")
    segmenter = None
    if detect is not None and a.masks and not a.no_outlines:
        from vision.masks import load_segmenter
        segmenter = load_segmenter()
    hands = None
    if not a.no_hands:
        from vision.hands import HandTracker
        hands = HandTracker(mirrored=a.mirrored)
    hub_url = None if a.hub == "none" else a.hub
    vision = sink = None
    if a.post_events:
        from vision.pipeline import Vision
        from vision.sink import Sink
        vision, sink = Vision(1), Sink(hub_url, quiet_objects=True)
    view = LiveView(src, detect, hands, HubState(hub_url), a.max_side, not a.no_outlines, a.fallback,
                    vision, sink, segmenter=segmenter, filter_labels=a.filter_labels,
                    protocol=protocol).start()
    server = uvicorn.Server(uvicorn.Config(create_app(view), host=a.host, port=a.port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    print(f"# live view at http://{a.host}:{a.port}/", flush=True)
    t0 = time.time()
    try:
        while view.thread.is_alive() and not (a.seconds and time.time() - t0 > a.seconds):
            time.sleep(2.0)
            if a.seconds:
                s = view.snapshot()
                print(f"# picture {s['fps']['picture']} fps, detections {s['fps']['detections']}/s, "
                      f"detector {s['ms']['detector']} ms, outlines {s['ms']['outlines']} ms, "
                      f"hands {s['ms']['hands']} ms, draw+jpeg {s['ms']['draw']} ms, "
                      f"skipped {s['source']['skipped_frames']}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        view.close()
        if hands:
            hands.close()
        if sink:
            sink.close()


if __name__ == "__main__":
    main()
