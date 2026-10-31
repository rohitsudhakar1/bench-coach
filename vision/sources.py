"""Where frames come from: video files (testing) or the capture part's latest-frame endpoint."""
from __future__ import annotations

import time

import cv2
import numpy as np


def fit(frame, max_side: int):
    h, w = frame.shape[:2]
    s = max_side / max(h, w)
    if s >= 1:
        return frame
    return cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)


class FileSource:
    """One video file read at `fps` frames per second of video time. Frame times are the run's
    start time plus the position in the video, so files started together share one clock."""

    def __init__(self, path: str, fps: float, max_side: int, t0: float, realtime: bool = False, start: float = 0.0):
        self.cap = cv2.VideoCapture(str(path))
        if not self.cap.isOpened():
            raise SystemExit(f"cannot open {path}")
        self.vfps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.step = max(1, round(self.vfps / fps))
        self.max_side, self.t0, self.realtime, self.wall0 = max_side, t0, realtime, time.time()
        self.idx = int(start * self.vfps)
        self.start = start
        if self.idx:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.idx)
        self.shape = None

    def read(self):
        frame = None
        for _ in range(self.step):
            ok, f = self.cap.read()
            if not ok:
                return None, None
            frame, self.idx = f, self.idx + 1
        pos = self.idx / self.vfps
        if self.realtime:
            wait = self.wall0 + (pos - self.start) - time.time()
            if wait > 0:
                time.sleep(wait)
        self.shape = frame.shape
        return self.t0 + pos - self.start, fit(frame, self.max_side)

    def close(self):
        self.cap.release()


class HttpSource:
    """Polls a camera's latest JPEG (capture's /cam/{i}/frame.jpg) and keeps its X-Frame-Time."""

    def __init__(self, url: str, fps: float, max_side: int, client=None):
        import httpx
        self.client = client or httpx.Client(timeout=2.0)
        self.url, self.period, self.max_side = url, 1.0 / fps, max_side
        self.last_t, self.next_wall, self.shape = None, time.time(), None

    @classmethod
    def for_cam(cls, base: str, cam: int, fps: float, max_side: int):
        return cls(f"{base.rstrip('/')}/cam/{cam}/frame.jpg", fps, max_side)

    def read(self, give_up: float = 10.0):
        delay = self.next_wall - time.time()
        if delay > 0:
            time.sleep(delay)
        self.next_wall = max(self.next_wall + self.period, time.time())
        deadline = time.time() + give_up
        while time.time() < deadline:
            try:
                r = self.client.get(self.url)
                if r.status_code == 200:
                    t = float(r.headers.get("X-Frame-Time", time.time()))
                    if t != self.last_t:  # a repeated frame is not a new one
                        img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
                        if img is not None:
                            self.last_t, self.shape = t, img.shape
                            return t, fit(img, self.max_side)
            except Exception:
                pass
            time.sleep(0.03)
        return None, None

    def close(self):
        self.client.close()
