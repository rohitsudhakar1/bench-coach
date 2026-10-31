"""LSD finds a straight edge, not a pipette identity."""
from dataclasses import dataclass
import cv2
import numpy as np


@dataclass
class Axis:
    points: np.ndarray
    confidence: float


def clip_roi(roi, shape):
    x, y, width, height = roi
    left, top = max(0, int(np.floor(x))), max(0, int(np.floor(y)))
    right, bottom = min(shape[1], int(np.ceil(x + width))), min(shape[0], int(np.ceil(y + height)))
    return (left, top, right - left, bottom - top) if right-left >= 8 and bottom-top >= 8 else None


def detect_axis(frame, roi):
    roi = clip_roi(roi, frame.shape)
    if roi is None:
        return None
    x, y, width, height = roi
    gray = cv2.cvtColor(frame[y:y+height, x:x+width], cv2.COLOR_BGR2GRAY)
    segments = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(gray)[0]
    if segments is None:
        return None
    segments = segments.reshape(-1, 2, 2)
    delta = segments[:, 1] - segments[:, 0]
    lengths = np.linalg.norm(delta, axis=1)
    offset = np.array([width/2, height/2]) - segments[:, 0]
    distance = abs(delta[:, 0]*offset[:, 1] - delta[:, 1]*offset[:, 0]) / np.maximum(lengths, 1e-9)
    scale = max(6, .15*min(width, height))
    score = lengths * np.exp(-.5*(distance/scale)**2)
    best = int(score.argmax())
    if lengths[best] < max(15, .3*max(width, height)):
        return None
    return Axis(segments[best] + [x, y], min(.85, float(lengths[best]/np.hypot(width, height))))


class ShaftTracker:
    def __init__(self, roi, fixed=False):
        self.home = tuple(roi)
        self.roi = self.home
        self.fixed = fixed
        self.tracker = None
        self.losses = 0

    def update(self, frame):
        if self.fixed:
            return self.roi
        if self.tracker is None:
            self.roi = clip_roi(self.home, frame.shape)
            if self.roi is None:
                return None
            self.tracker = cv2.TrackerCSRT_create()
            self.tracker.init(frame, self.roi)
        else:
            found, box = self.tracker.update(frame)
            clipped = clip_roi(box, frame.shape) if found else None
            if clipped is None:
                self.losses += 1
                self.tracker = None
                self.roi = self.home
                return None
            self.roi = clipped
        return self.roi


def overlay_frame(frame, axis, crop, text):
    image = frame.copy()
    if crop is not None:
        x, y, w, h = map(int, crop)
        cv2.rectangle(image, (x, y), (x+w, y+h), (255, 220, 0), 1)
    if axis is not None:
        ends = np.rint(axis.points).astype(int)
        cv2.line(image, tuple(ends[0]), tuple(ends[1]), (0, 170, 255), 3)
    cv2.rectangle(image, (0, 0), (image.shape[1], 40), (20, 20, 20), -1)
    cv2.putText(image, text, (8, 27), cv2.FONT_HERSHEY_SIMPLEX, .55, (240, 240, 240), 1, cv2.LINE_AA)
    return image
