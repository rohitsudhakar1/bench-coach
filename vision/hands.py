"""Hand landmarks with MediaPipe Hand Landmarker (Apache 2.0), one tracker per camera.

MediaPipe was trained on bare hands and misses most blue nitrile gloves, so blue and purple pixels
are painted a skin colour before it looks (on the earlier draft's labelled frames this took hands
with finger points from 3 of 112 to 67 of 112).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .core import Hand

MODEL = Path(__file__).parent / "models" / "hand_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")


def ensure_model() -> Path:
    if not MODEL.exists():
        import urllib.request
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, MODEL)  # about 7.5 MB
    return MODEL


def glove_to_skin(bgr):
    """Blue and purple pixels (OpenCV hue 80-135) turned skin coloured; everything else kept."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = (hsv[..., 0] >= 80) & (hsv[..., 0] <= 135) & (hsv[..., 1] >= 15) & (hsv[..., 2] >= 60)
    hsv[..., 0] = np.where(m, 12, hsv[..., 0])
    hsv[..., 1] = np.where(m, 90, hsv[..., 1])
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def merge_hands(landmarked: list, boxes, frame_w: float) -> list:
    """Hands with finger points first, then detector hand boxes no landmarked hand explains.
    A box-only hand's side is a guess from the half of the frame it is in."""
    out = list(landmarked)
    for b in boxes:
        x1, y1, x2, y2 = b
        if any(x1 <= h.center[0] <= x2 and y1 <= h.center[1] <= y2 for h in out):
            continue
        out.append(Hand("left" if (x1 + x2) / 2 < frame_w / 2 else "right", None, tuple(b)))
    return out


class HandTracker:
    def __init__(self, mirrored: bool = False, recolour: bool = True):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision as mpv
        self.mp = mp
        opts = mpv.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(ensure_model())),
            running_mode=mpv.RunningMode.VIDEO, num_hands=2,
            min_hand_detection_confidence=0.3, min_hand_presence_confidence=0.3,
            min_tracking_confidence=0.3)
        self.lm = mpv.HandLandmarker.create_from_options(opts)
        self.last_ms = -1
        self.mirrored, self.recolour = mirrored, recolour

    def __call__(self, bgr, t: float) -> list:
        h, w = bgr.shape[:2]
        ms = max(int(t * 1000), self.last_ms + 1)  # MediaPipe wants strictly rising timestamps
        self.last_ms = ms
        img = glove_to_skin(bgr) if self.recolour else bgr
        res = self.lm.detect_for_video(
            self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), ms)
        hands = []
        for pts, hd in zip(res.hand_landmarks, res.handedness):
            # MediaPipe names sides as in a mirrored selfie; bench cameras are not mirrored.
            side = hd[0].category_name.lower()
            if not self.mirrored:
                side = "left" if side == "right" else "right"
            hands.append(Hand(side, [(p.x * w, p.y * h) for p in pts]))
        return hands

    def close(self):
        self.lm.close()
