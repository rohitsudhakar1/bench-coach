"""Video sampling and capture JPEGs; recorded timing rows retain dropped-frame timing."""
from bisect import bisect_left
from dataclasses import dataclass
import json
from pathlib import Path
import time

import cv2
import httpx
import numpy as np


@dataclass
class Frame:
    image: np.ndarray
    stamp: float
    verified: bool
    fresh: bool = True


def read_jpeg(client, url):
    response = client.get(url)
    response.raise_for_status()
    try:
        stamp = float(response.headers['X-Frame-Time'])
    except (ValueError, KeyError) as exc:
        raise ValueError('Capture needs a numeric X-Frame-Time header') from exc
    image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
    if image is None or not np.isfinite(stamp):
        raise ValueError('Invalid capture JPEG or timestamp')
    return Frame(image, stamp, True)


class Timing:
    def __init__(self, path, cam):
        rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        all_frames = [r for r in rows if r.get('kind') == 'frame']
        self.rows = sorted((r for r in all_frames if r.get('cam') == cam), key=lambda r: r['t'])
        if not self.rows:
            raise ValueError(f'No timing rows for camera {cam}')
        if any(not np.isfinite(r['t']) or type(r['frame']) is not int or r['frame'] < 0 for r in all_frames):
            raise ValueError('Invalid timing row')
        self.origin = min(r['t'] for r in all_frames)
        self.times = [r['t'] - self.origin for r in self.rows]

    def at(self, seconds):
        if seconds > self.times[-1] + .1:
            return None
        index = bisect_left(self.times, seconds)
        choices = [i for i in (index-1, index) if 0 <= i < len(self.rows)]
        best = min(choices, key=lambda i: abs(self.times[i]-seconds))
        if abs(self.times[best]-seconds) > .1:
            return None
        return self.rows[best]


class Reader:
    def __init__(self, source, cam=0, timing=None):
        self.live = source.isdecimal() or '://' in source
        self.jpeg = source.startswith(('http://', 'https://')) and '/frame.jpg' in source
        self.source = source
        self.client = httpx.Client(timeout=1) if self.jpeg else None
        self.cap = None
        self.previous = -np.inf
        self.timing = None
        if not self.live:
            automatic = Path(source).parent/'timing.jsonl'
            if timing or automatic.exists():
                self.timing = Timing(timing or automatic, cam)
        if not self.jpeg:
            self.cap = cv2.VideoCapture(int(source) if source.isdecimal() else source)
            if not self.cap.isOpened():
                self.close()
                raise ValueError(f'Cannot open {source}')

    def read(self, seconds):
        if self.jpeg:
            frame = read_jpeg(self.client, self.source)
            frame.fresh = frame.stamp > self.previous and 0 <= time.time()-frame.stamp <= 1
            if frame.fresh:
                self.previous = frame.stamp
            return frame
        stamp = seconds
        if not self.live:
            if self.timing:
                row = self.timing.at(seconds)
                if row is None:
                    # A gap is distinct from EOF: the caller can continue to the next time.
                    if seconds <= self.timing.times[-1]:
                        return Frame(np.empty((0, 0, 3), np.uint8), seconds, True, False)
                    return None
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, row['frame'])
                stamp = row['t'] - self.timing.origin
            else:
                self.cap.set(cv2.CAP_PROP_POS_MSEC, seconds*1000)
        found, image = self.cap.read()
        if not found:
            return None
        if self.live:
            stamp = time.time()
        fresh = stamp > self.previous
        self.previous = max(self.previous, stamp)
        return Frame(image, stamp, not self.live, fresh)

    def close(self):
        if self.cap is not None:
            self.cap.release()
        if self.client is not None:
            self.client.close()
