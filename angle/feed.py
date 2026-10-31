"""Thread-safe cache of short-lived vision observations and current coach step."""
import json
import math
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from websockets.sync.client import connect
from websockets.exceptions import WebSocketException
from .detection import clip_roi


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class FeedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.boxes = {}
        self.activity = None
        self.step = None
        self.step_time = -math.inf
        self.run_time = -math.inf
        self.running = None

    def accept(self, event, now=None):
        now = time.time() if now is None else now
        if not isinstance(event, dict):
            return
        stamp, detail = event.get('t'), event.get('detail')
        if not finite(stamp) or stamp > now or not isinstance(detail, dict):
            return
        source, kind = event.get('source'), event.get('kind')
        with self.lock:
            if stamp < self.run_time:
                return
            if source == 'app' and kind == 'run' and detail.get('status') in ('start', 'stop'):
                self.running = detail['status'] == 'start'
                self.boxes.clear()
                self.activity = None
                self.step = None
                self.step_time = self.run_time = stamp
            elif source == 'vision' and self.running is False:
                return
            elif source == 'vision' and kind == 'objects':
                cam, boxes = detail.get('cam'), detail.get('boxes')
                if type(cam) is not int or cam < 0 or not isinstance(boxes, list):
                    return
                if stamp < self.boxes.get(cam, (-math.inf, []))[0]:
                    return
                valid = []
                for box in boxes:
                    if not isinstance(box, dict) or not isinstance(box.get('label'), str):
                        continue
                    coords, score = box.get('box'), box.get('score')
                    if (box.get('held') is not True
                            or 'pipette' not in box['label'].lower() or not isinstance(coords, list)
                            or len(coords) != 4 or not all(finite(v) for v in coords)
                            or not finite(score) or not 0 <= score <= 1):
                        continue
                    x1, y1, x2, y2 = coords
                    if x2 > x1 and y2 > y1:
                        valid.append((score, (x1, y1, x2-x1, y2-y1)))
                self.boxes[cam] = (stamp, sorted(valid, reverse=True))
            elif source == 'vision' and kind in ('draw', 'dispense'):
                if self.activity is None or stamp >= self.activity[0]:
                    # A hint permits a technique check but never becomes proof of transfer.
                    self.activity = (stamp, kind, detail.get('hint') is True)
            elif source == 'coach' and kind == 'step' and stamp >= self.step_time:
                index = detail.get('index')
                if type(index) is not int or index < 0:
                    return
                if detail.get('status') == 'current':
                    self.step, self.step_time = index, stamp
                elif detail.get('status') in ('done', 'skipped') and index == self.step:
                    self.step, self.step_time = None, stamp

    def snapshot(self, cams, shapes, now=None):
        now = time.time() if now is None else now
        with self.lock:
            crops = []
            for cam, shape in zip(cams, shapes):
                stamp, boxes = self.boxes.get(cam, (-math.inf, []))
                crop = None
                if 0 <= now-stamp < 1:
                    crop = next((clipped for _, box in boxes if (clipped := clip_roi(box, shape))), None)
                crops.append(crop)
            activity = self.activity if self.activity and 0 <= now-self.activity[0] <= 2 else None
            return crops, activity, self.step


class HubFeed:
    def __init__(self, hub, state):
        url = urlsplit(hub)
        self.url = urlunsplit(('wss' if url.scheme == 'https' else 'ws', url.netloc,
                              url.path.rstrip('/')+'/ws', '', '')) if hub else None
        self.state = state
        self.stopping = threading.Event()
        self.thread = None

    def start(self):
        if self.url:
            self.thread = threading.Thread(target=self._listen, name='angle-feed', daemon=True)
            self.thread.start()

    def _listen(self):
        while not self.stopping.is_set():
            try:
                with connect(self.url, open_timeout=.5, close_timeout=.2, proxy=None) as socket:
                    while not self.stopping.is_set():
                        try:
                            payload = socket.recv(timeout=.1)
                        except TimeoutError:
                            continue
                        try:
                            self.state.accept(json.loads(payload))
                        except (ValueError, TypeError):
                            pass
            except (OSError, TimeoutError, WebSocketException):
                self.stopping.wait(.5)

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=2)
