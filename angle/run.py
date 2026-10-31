"""Run angle measurement; use --always for a standalone file check."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import math
from pathlib import Path
import sys
import threading
import time

import cv2
from fastapi import FastAPI
import httpx
import numpy as np
import uvicorn

from .detection import ShaftTracker, detect_axis, overlay_frame
from .events import EventGate, Publisher
from .feed import FeedState, HubFeed
from .geometry import Calibration, image_angle, stereo_angle
from .sources import Reader


def measure(axes, calibration=None, reason=None):
    available = [(i, axis) for i, axis in enumerate(axes) if axis is not None]
    if not available:
        return None
    if calibration is not None and len(available) == 2:
        try:
            degrees, support = stereo_angle(axes[0].points, axes[1].points, calibration)
            return degrees, min(a.confidence for a in axes)*support, dict(
                rough=False, method='stereo_planes', views=[0, 1])
        except ValueError as exc:
            reason = str(exc)
    index, best = max(available, key=lambda item: item[1].confidence)
    return image_angle(best.points), min(.4, best.confidence), dict(
        rough=True, method='image_vertical', views=[index],
        projected_deg={str(i): round(image_angle(a.points), 3) for i, a in available},
        reason=reason or ('Second shaft missing' if calibration else 'No calibration'))


def analyze(frames, trackers, crops, active, calibration, max_skew, live):
    axes, origins, used = [], [], []
    for frame, tracker, crop, is_live in zip(frames, trackers, crops, live):
        # Selection or a slow read can make an initially fresh JPEG stale before use.
        if is_live and frame.verified and not 0 <= time.time()-frame.stamp <= 1:
            frame.fresh = False
        fallback = tracker.update(frame.image) if tracker and frame.fresh else None
        region = crop if crop is not None else fallback
        origin = 'vision' if crop else 'manual' if tracker else 'local'
        if region is None and tracker is None:
            region = (0, 0, frame.image.shape[1], frame.image.shape[0])
        axis = detect_axis(frame.image, region) if active and frame.fresh and region else None
        axes.append(axis)
        origins.append(origin)
        used.append(region)
    reason = None
    if calibration:
        if not all(f.verified for f in frames):
            reason = 'Sources lack verified synchronization'
        elif len(set(live)) > 1:
            reason = 'Live and recorded sources cannot form a stereo pair'
        elif all(live) and abs(frames[0].stamp-frames[-1].stamp) > max_skew:
            reason = 'Capture timestamps exceed maximum skew'
        elif any(origin == 'local' and axis is not None for origin, axis in zip(origins, axes)):
            reason = 'Full-frame line fallback has no pipette identity'
    result = measure(axes, None if reason else calibration, reason) if active else None
    return result, axes, origins, used


def roi_arg(text):
    try:
        values = tuple(map(int, text.split(',')))
        if len(values) != 4 or min(values[2:]) < 8 or min(values[:2]) < 0:
            raise ValueError()
        return values
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Use x,y,width,height; dimensions at least 8 pixels') from exc


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', action='append', required=True)
    p.add_argument('--roi', action='append', type=roi_arg)
    p.add_argument('--select-roi', action='store_true')
    p.add_argument('--fixed-roi', action='store_true')
    p.add_argument('--always', action='store_true')
    p.add_argument('--cam', action='append', type=int)
    p.add_argument('--calibration', help='JSON from python -m angle.calibrate')
    p.add_argument('--timing', help='capture timing.jsonl; also discovered beside input videos')
    p.add_argument('--limit', type=float, default=20)
    p.add_argument('--hz', type=float, default=2)
    p.add_argument('--fps', type=float, default=10)
    p.add_argument('--start', type=float, default=0)
    p.add_argument('--right-offset', type=float, default=0)
    p.add_argument('--max-skew', type=float, default=.1)
    p.add_argument('--duration', type=float)
    p.add_argument('--hub', default='http://127.0.0.1:8700')
    p.add_argument('--port', type=int, default=8703)
    p.add_argument('--no-server', action='store_true')
    p.add_argument('--overlay')
    p.add_argument('--preview', action='store_true')
    p.add_argument('--fast', action='store_true')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    count = len(args.source)
    numeric = [args.limit, args.hz, args.fps, args.start, args.right_offset, args.max_skew]
    if args.duration is not None:
        numeric.append(args.duration)
    if not all(math.isfinite(v) for v in numeric):
        raise ValueError('Numeric options must be finite')
    if count not in (1, 2) or args.roi and len(args.roi) != count:
        raise ValueError('Use one or two sources and one manual ROI per source')
    if args.cam and (len(args.cam) != count or min(args.cam) < 0 or len(set(args.cam)) != count):
        raise ValueError('Use one distinct nonnegative camera ID per source')
    if (not 0 < args.fps <= 60 or min(args.start, args.start+args.right_offset) < 0
            or not 0 < args.max_skew <= 1 or args.duration is not None and args.duration <= 0):
        raise ValueError('Invalid frame rate, start, offset, skew or duration')
    gate = EventGate(args.limit, args.hz)
    calibration = Calibration.load(args.calibration) if args.calibration else None
    cameras = args.cam or list(range(count))
    feed_state = FeedState()
    feed = HubFeed(args.hub, feed_state)
    publisher = Publisher('' if args.fast else args.hub)
    readers, writer, server, thread = [], None, None, None
    state = dict(part='angle', status='starting', frames=0, rough=True, last_event=None)
    app = FastAPI()

    @app.get('/health')
    def health():
        return dict(state)

    try:
        for source, cam in zip(args.source, cameras):
            readers.append(Reader(source, cam, args.timing))
        live = [reader.live for reader in readers]
        if args.fast and any(live) or args.right_offset and any(live):
            raise ValueError('--fast and --right-offset require video files')
        if args.right_offset and any(r.timing for r in readers):
            raise ValueError('Timing ledger already aligns the views; do not add an offset')
        if count == 2 and bool(readers[0].timing) != bool(readers[1].timing):
            raise ValueError('Both recordings need the same timing ledger')
        if count == 2 and readers[0].timing and readers[0].timing.origin != readers[1].timing.origin:
            raise ValueError('Recording ledgers must have the same clock origin')
        feed.start()
        if not args.no_server:
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port, log_level='warning'))
            thread = threading.Thread(target=server.run, name='angle-health', daemon=True)
            thread.start()
            for _ in range(100):
                if server.started or not thread.is_alive():
                    break
                time.sleep(.01)
            if not server.started:
                raise RuntimeError('Health endpoint did not start')
        trackers = None
        start = time.monotonic()
        n = 0
        with ThreadPoolExecutor(max_workers=count) as pool:
            while args.duration is None or (n/args.fps if args.fast else time.monotonic()-start) < args.duration:
                media = args.start+n/args.fps
                futures = [pool.submit(reader.read, media+(args.right_offset if i else 0))
                           for i, reader in enumerate(readers)]
                try:
                    frames = [future.result() for future in futures]
                except (httpx.HTTPError, ValueError) as exc:
                    if not any(live):
                        raise
                    state.update(status='source_unavailable', error=str(exc))
                    n += 1
                    time.sleep(.1)
                    continue
                if any(frame is None for frame in frames):
                    break
                if any(frame.image.size == 0 for frame in frames):
                    state.update(status='recording_gap', frames=n+1)
                    n += 1
                    if not args.fast:
                        time.sleep(max(0, start+n/args.fps-time.monotonic()))
                    continue
                if calibration and any((f.image.shape[1], f.image.shape[0]) != calibration.size for f in frames):
                    raise ValueError('Frame size differs from calibration')
                if trackers is None:
                    rois = args.roi
                    if args.select_roi:
                        rois = [tuple(cv2.selectROI(f'Shaft camera {cam}', f.image, False))
                                for cam, f in zip(cameras, frames)]
                        cv2.destroyAllWindows()
                    if rois:
                        for roi, f in zip(rois, frames):
                            x, y, w, h = roi
                            if min(w, h) < 8 or x+w > f.image.shape[1] or y+h > f.image.shape[0]:
                                raise ValueError('Manual ROI must fit inside the frame')
                    trackers = [ShaftTracker(roi, args.fixed_roi) for roi in rois] if rois else [None]*count
                    # Mouse selection is setup time, not a timed part of the run.
                    start = time.monotonic()
                crops, activity, step = feed_state.snapshot(cameras, [f.image.shape for f in frames])
                active = args.always or activity is not None
                usable = calibration
                sync_reason = None
                if calibration and count == 2 and readers[0].timing and abs(frames[0].stamp-frames[1].stamp) > args.max_skew:
                    usable, sync_reason = None, 'Recorded timestamps exceed maximum skew'
                result, axes, origins, used = analyze(frames, trackers, crops, active, usable, args.max_skew, live)
                state.update(frames=n+1, status='tracking' if result else 'axis_missing' if active else 'waiting_activity')
                state.pop('error', None)
                label = 'NO SHAFT' if active else 'WAITING FOR DRAW / DISPENSE'
                if result:
                    degrees, confidence, detail = result
                    detail.update(cams=cameras, crop_sources=origins, activity=activity[1] if activity else 'unknown',
                                  activity_hint=bool(activity and activity[2]))
                    if sync_reason:
                        detail['reason'] = sync_reason
                    if not any(live):
                        detail['media_time'] = media
                    if all(f.verified for f in frames) and all(live):
                        detail['frame_times'] = [f.stamp for f in frames]
                    if any(r.timing for r in readers):
                        detail['recorded_frame_times'] = [f.stamp+r.timing.origin for f, r in zip(frames, readers)]
                    events = gate.update(result, step)
                    publisher.send(events)
                    state['rough'] = detail['rough']
                    if events:
                        state['last_event'] = events[0]
                    label = f'{degrees:.1f} deg / {args.limit:g} limit ' + ('ROUGH' if detail['rough'] else 'STEREO')
                if args.overlay or args.preview:
                    tiles = [overlay_frame(f.image, a, crop, f'cam {cam}: {label}')
                             for f, a, crop, cam in zip(frames, axes, used, cameras)]
                    height = min(tile.shape[0] for tile in tiles)
                    canvas = np.hstack([cv2.resize(tile, (round(tile.shape[1]*height/tile.shape[0]), height)) for tile in tiles])
                    if args.overlay:
                        if writer is None:
                            Path(args.overlay).parent.mkdir(parents=True, exist_ok=True)
                            writer = cv2.VideoWriter(args.overlay, cv2.VideoWriter_fourcc(*'mp4v'), args.fps,
                                                     (canvas.shape[1], canvas.shape[0]))
                            if not writer.isOpened():
                                raise ValueError('Cannot open overlay output')
                        writer.write(canvas)
                    if args.preview:
                        cv2.imshow('Bench Coach angle', canvas)
                        if cv2.waitKey(1) & 255 == ord('q'):
                            break
                n += 1
                if not args.fast:
                    time.sleep(max(0, start+n/args.fps-time.monotonic()))
    finally:
        state['status'] = 'stopped'
        feed.close()
        for reader in readers:
            reader.close()
        if writer:
            writer.release()
        publisher.close()
        if server:
            server.should_exit = True
        if thread:
            thread.join(timeout=3)
        if args.preview or args.select_roi:
            cv2.destroyAllWindows()
    return 0


def cli():
    try:
        return main()
    except KeyboardInterrupt:
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, cv2.error, httpx.HTTPError) as exc:
        print(f'Angle stopped: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(cli())
