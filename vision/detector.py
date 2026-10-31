"""Open-vocabulary boxes with Grounding DINO tiny (IDEA-Research, Apache 2.0) via Hugging Face transformers.

The earlier draft timed three detectors on bench frames: Grounding DINO tiny at 480x640 found the
pipette, racks, plates and hands in about 150 ms on MPS; OWLv2 gave many false boxes; YOLO-World
found almost nothing (and is AGPL). So this is the one used.
"""
from __future__ import annotations

import copy
import os
import threading
import time

import cv2

from .core import HAND, PIPETTE, PIPETTE_TIP, PLATE, RACK, TIP_BOX, TUBE, WASTE, Det, area, iou

MODEL_ID = "IDEA-Research/grounding-dino-tiny"
# prompt phrase -> name used in events
PROMPTS = {"hand": HAND, "pipette": PIPETTE, "pipette tip": PIPETTE_TIP, "tip box": TIP_BOX,
           "tube": TUBE, "tube rack": RACK, "well plate": PLATE, "waste bin": WASTE}
# Second prompt for --new-draw: things a tip goes into. Kept apart so the main boxes stay as tuned.
CONTAINER_PROMPTS = {"bottle": "bottle", "flask": "flask", "beaker": "beaker", "tube": "tube",
                     "petri dish": "dish", "well plate": "plate", "tube rack": "tube rack"}
CONTAINER = "container:"  # label prefix for container boxes


def pick_device() -> str:
    """VISION_DEVICE (cuda, mps or cpu) if set, else the best one this machine has."""
    env = os.environ.get("VISION_DEVICE")
    if env:
        return env
    import torch
    if torch.cuda.is_available():
        return "cuda"
    return "mps" if torch.backends.mps.is_available() else "cpu"


def canonical(phrase: str, prompts: dict) -> str | None:
    """Map whatever phrase the model returned to one of our names (longest matching prompt wins)."""
    t = phrase.strip().lower()
    if t in prompts:
        return prompts[t]
    words = set(t.split())
    best, best_n = None, 0
    for p in prompts:
        n = len(words & set(p.split()))
        if n > best_n or (n == best_n and n and len(p) > len(best)):
            best, best_n = p, n
    return prompts[best] if best else None


def clean(dets: list, w: int, h: int, max_frac: float = 0.5) -> list:
    """Drop boxes covering most of the frame and same-label duplicates."""
    keep = []
    for d in sorted(dets, key=lambda d: -d.score):
        if area(d.box) > max_frac * w * h:
            continue
        if any(k.label == d.label and iou(k.box, d.box) > 0.6 for k in keep):
            continue
        keep.append(d)
    return keep


class GroundingDino:
    def __init__(self, device: str | None = None, short_side: int = 480, long_side: int = 640,
                 box_threshold: float = 0.25, text_threshold: float = 0.2, raw: bool = False,
                 prompts: dict | None = None):
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        self.torch = torch
        self.device = device or pick_device()
        self.proc = AutoProcessor.from_pretrained(MODEL_ID)
        self.proc.image_processor.size = {"shortest_edge": short_side, "longest_edge": long_side}
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(MODEL_ID).to(self.device).eval()
        self.set_prompts(prompts or PROMPTS)
        self.bt, self.tt, self.raw = box_threshold, text_threshold, raw

    def set_prompts(self, prompts: dict):
        self.prompts = dict(prompts)
        self.text = " . ".join(self.prompts) + " ."

    def with_prompts(self, prompts: dict) -> "GroundingDino":
        """The same loaded model asked for other things."""
        other = copy.copy(self)
        other.set_prompts(prompts)
        return other

    def __call__(self, bgr) -> list:
        h, w = bgr.shape[:2]
        inp = self.proc(images=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), text=self.text, return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            out = self.model(**inp)
        res = self.proc.post_process_grounded_object_detection(
            out, inp.input_ids, threshold=self.bt, text_threshold=self.tt, target_sizes=[(h, w)])[0]
        dets = []
        for lab, box, score in zip(res["text_labels"], res["boxes"], res["scores"]):
            name = canonical(str(lab), self.prompts)
            if name:
                dets.append(Det(name, tuple(float(v) for v in box.tolist()), float(score)))
        return dets if self.raw else clean(dets, w, h)


class WithContainers:
    """Main prompt every call, container prompt every `every`-th call per camera
    (container boxes come back labelled "container:<name>")."""

    def __init__(self, det: GroundingDino, every: int = 1):
        self.det, self.cdet, self.every = det, det.with_prompts(CONTAINER_PROMPTS), max(1, every)
        self.calls: dict = {}

    def __call__(self, bgr, cam: int = 0) -> list:
        out = self.det(bgr)
        n = self.calls.get(cam, 0)
        self.calls[cam] = n + 1
        if n % self.every == 0:
            out += [Det(CONTAINER + d.label, d.box, d.score) for d in self.cdet(bgr)]
        return out


class AsyncDetector:
    """Live mode: the detector runs in its own thread on the newest frame of each camera in turn,
    so hands and events keep the camera's frame rate."""

    def __init__(self, detect, n_cams: int):
        self.detect, self.n = detect, n_cams
        self.pending = [None] * n_cams
        self.results = [(None, [])] * n_cams
        self.version = [0] * n_cams
        self.lock, self.wake = threading.Lock(), threading.Event()
        self.stopping, self.next_cam = False, 0
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def submit(self, cam: int, t: float, frame):
        with self.lock:
            self.pending[cam] = (t, frame)
        self.wake.set()

    def latest(self, cam: int):
        with self.lock:
            return self.version[cam], self.results[cam]

    def _loop(self):
        while not self.stopping:
            self.wake.wait(0.2)
            job = None
            with self.lock:
                for k in range(self.n):
                    c = (self.next_cam + k) % self.n
                    if self.pending[c] is not None:
                        job, self.pending[c] = (c, *self.pending[c]), None
                        self.next_cam = (c + 1) % self.n
                        break
                if job is None:
                    self.wake.clear()
            if job is None:
                continue
            cam, t, frame = job
            try:
                dets = self.detect(frame, cam) if isinstance(self.detect, WithContainers) else self.detect(frame)
            except Exception as e:  # keep hands running if the detector fails once
                print(f"# detector error: {e}", flush=True)
                time.sleep(0.5)
                continue
            with self.lock:
                self.results[cam] = (t, dets)
                self.version[cam] += 1

    def close(self):
        self.stopping = True
        self.wake.set()
        self.thread.join(timeout=2)
