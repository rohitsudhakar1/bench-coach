"""python -m vision --source a.mp4 [b.mp4] | --capture http://localhost:8701 --cams 2

Events go to the hub (default http://localhost:8700/events); while it is down they are printed as
JSON lines and the hub is tried again every 5 s.
"""
from __future__ import annotations

import argparse
import signal
import threading
import time
from collections import deque


def parse(argv=None):
    p = argparse.ArgumentParser(prog="python -m vision")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--source", nargs="+", help="one video file per camera")
    src.add_argument("--capture", help="capture part base URL, e.g. http://localhost:8701")
    p.add_argument("--cams", type=int, default=2, help="cameras to read from capture")
    p.add_argument("--hub", default="http://localhost:8700", help="hub base URL, or 'none' to print only")
    p.add_argument("--port", type=int, default=8702, help="health endpoint port, 0 for none")
    p.add_argument("--fps", type=float, default=10.0, help="frames per second per camera")
    p.add_argument("--max-side", type=int, default=640)
    p.add_argument("--start", type=float, default=0.0, help="files: seconds in to start")
    p.add_argument("--seconds", type=float, default=0.0, help="files: stop after this much video")
    p.add_argument("--realtime", action="store_true", help="files: play at real speed")
    p.add_argument("--detect-every", type=float, default=0.35, help="files: detector every this many video seconds")
    p.add_argument("--no-detector", action="store_true", help="hands only")
    p.add_argument("--debug-video", help="write an annotated side-by-side video here")
    p.add_argument("--log", help="also append every event to this JSON lines file")
    p.add_argument("--mirrored", action="store_true")
    p.add_argument("--quiet-objects", action="store_true", help="do not print objects events")
    p.add_argument("--new-draw", action="store_true",
                   help="tip-into-container draw/dispense tracker; its events carry detail.hint = true")
    p.add_argument("--dispense-min-dwell", type=float, default=0.0,
                   help="--new-draw: send a dispense hint only when the tip stayed in this long (0.4 tried; off by default)")
    p.add_argument("--containers-every", type=int, default=1, help="container prompt every n-th detector run")
    p.add_argument("--no-verify", action="store_true", help="never ask Claude about hints")
    p.add_argument("--verify-per-minute", type=int, default=20)
    return p.parse_args(argv)


def serve_health(port: int, stats: dict):
    import uvicorn
    from fastapi import FastAPI
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True, "part": "vision", **stats}

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    return server


def main(argv=None):
    a = parse(argv)
    from .annotate import DebugVideo, draw
    from .hands import HandTracker
    from .pipeline import Vision, to_source_pixels
    from .sink import Sink
    from .sources import FileSource, HttpSource

    t0 = time.time()
    if a.source:
        live = a.realtime
        sources = [FileSource(p, a.fps, a.max_side, t0, a.realtime, a.start) for p in a.source]
    else:
        live = True
        sources = [HttpSource.for_cam(a.capture, i, a.fps, a.max_side) for i in range(a.cams)]
    n = len(sources)
    new_draw = a.new_draw and not a.no_detector
    from .draw import DrawRules
    vis = Vision(n, new_draw, draw=DrawRules(disp_min_dwell=a.dispense_min_dwell))
    trackers = [HandTracker(mirrored=a.mirrored) for _ in range(n)]
    sink = Sink(None if a.hub == "none" else a.hub, a.log, a.quiet_objects)

    detect = adet = None
    if not a.no_detector:
        from .detector import GroundingDino, WithContainers
        detect = GroundingDino()
        if new_draw:
            detect = WithContainers(detect, a.containers_every)
        if live:
            from .detector import AsyncDetector
            adet = AsyncDetector(detect, n)

    verifier = worker = rings = None
    if new_draw and not a.no_verify:
        from .verify import FrameRing, Verifier, VerifyWorker, default_client
        verifier = Verifier(default_client(), a.verify_per_minute)
        vis.on_entry = verifier.note
        rings = [FrameRing() for _ in range(n)]
        if verifier.client is not None:
            worker = VerifyWorker(verifier, sink.send)

    def checked(events):
        """Hints go through the Claude check when there is a key; otherwise verified = null."""
        if verifier is None:
            return events
        out = []
        for e in events:
            if e["kind"] not in ("draw", "dispense"):
                out.append(e)
            elif worker is None:
                e["detail"].update(verified=None, vlm={"skipped": "no key"})
                out.append(e)
            else:
                worker.submit(e, verifier.pictures(e, lambda c, tt: rings[c].at(tt)))
        return out

    stats = {"cams": n, "fps": [0.0] * n, "frames": [0] * n, "detections": [0] * n, "events": 0}
    server = serve_health(a.port, stats) if a.port else None
    dv = DebugVideo(a.debug_video, a.fps) if a.debug_video else None
    seen_version, last_detect, det_t = [0] * n, [-1e9] * n, [None] * n
    recent = [deque(maxlen=20) for _ in range(n)]
    done, stop = [False] * n, []
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.append(1))
    wall0 = time.time()
    try:
        while not all(done) and not stop:
            for i, src in enumerate(sources):
                if done[i]:
                    continue
                t, frame = src.read()
                if frame is None or (a.source and a.seconds and t - t0 > a.seconds):
                    done[i] = True
                    continue
                if rings:
                    rings[i].add(t, frame)
                raw = None
                if adet:
                    adet.submit(i, t, frame)
                    v, (dt, d) = adet.latest(i)
                    if v != seen_version[i]:
                        seen_version[i], raw, det_t[i] = v, d, dt
                elif detect and t - last_detect[i] >= a.detect_every:
                    last_detect[i], det_t[i] = t, t
                    raw = detect(frame, i) if new_draw else detect(frame)
                if raw is not None:
                    stats["detections"][i] += 1
                h, w = frame.shape[:2]
                events = checked(vis.step(i, t, w, h, trackers[i](frame, t), raw))
                to_source_pixels(events, frame.shape, src.shape, det_t[i])
                sink.send(events)
                stats["events"] += len(events)
                stats["frames"][i] += 1
                stats["fps"][i] = round(stats["frames"][i] / max(time.time() - wall0, 1e-3), 2)
                for e in events:
                    if e["kind"] != "objects":
                        recent[i].append(f"{t - t0:6.1f}s {e['kind']} {e['object'] or ''}")
                if dv:
                    dbg = dict(vis.engines[i].debug)
                    if new_draw:
                        dbg.update(tip_end=vis.dips[i].debug.get("tip_point"), vessel=vis.dips[i].debug.get("container"))
                    dv.add(i, draw(frame, i, vis.hands[i], vis.dets[i], dbg, list(recent[i]), stats["fps"][i]))
            if dv:
                dv.flush()
        last = checked(vis.finish())
        sink.send(last)
        stats["events"] += len(last)
    finally:
        if worker:
            worker.close()
        if adet:
            adet.close()
        for s in sources:
            s.close()
        for tr in trackers:
            tr.close()
        if dv:
            dv.close()
        sink.close()
        if server:
            server.should_exit = True
    summary = ", ".join(f"cam {i}: {stats['frames'][i]} frames at {stats['fps'][i]} fps, "
                        f"{stats['detections'][i]} detector runs" for i in range(n))
    print(f"# vision done in {time.time() - wall0:.1f}s: {summary}, {stats['events']} events", flush=True)


if __name__ == "__main__":
    main()
