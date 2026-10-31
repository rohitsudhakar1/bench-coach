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
import numpy as np

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
                 prompts: dict | None = None, model_id: str = MODEL_ID):
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        self.torch = torch
        self.device = device or pick_device()
        self.proc = AutoProcessor.from_pretrained(model_id)
        self.proc.image_processor.size = {"shortest_edge": short_side, "longest_edge": long_side}
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(model_id).to(self.device).eval()
        self.set_prompts(prompts or PROMPTS)
        self.bt, self.tt, self.raw = box_threshold, text_threshold, raw

    def set_prompts(self, prompts: dict):
        self.prompts = dict(prompts)
        self.text = " . ".join(self.prompts) + " ."
        self._text_inputs = {}

    def with_prompts(self, prompts: dict) -> "GroundingDino":
        """The same loaded model asked for other things."""
        other = copy.copy(self)
        other.set_prompts(prompts)
        return other

    def __call__(self, bgr) -> list:
        return self.batch([bgr])[0]

    def batch(self, frames) -> list:
        if not frames:
            return []
        images = [cv2.cvtColor(f, cv2.COLOR_BGR2RGB) for f in frames]
        n = len(images)
        if n not in self._text_inputs:
            inp = self.proc(images=images, text=[self.text] * n, return_tensors="pt").to(self.device)
            self._text_inputs[n] = {k: v for k, v in inp.items() if k not in ("pixel_values", "pixel_mask")}
        else:
            inp = self.proc.image_processor(images=images, return_tensors="pt").to(self.device)
            inp.update(self._text_inputs[n])
        with self.torch.inference_mode():
            out = self.model(**inp)
        results = self.proc.post_process_grounded_object_detection(
            out, inp["input_ids"], threshold=self.bt, text_threshold=self.tt,
            target_sizes=[f.shape[:2] for f in frames])
        batch = []
        for frame, res in zip(frames, results):
            h, w = frame.shape[:2]
            dets = []
            for lab, box, score in zip(res["text_labels"], res["boxes"], res["scores"]):
                name = canonical(str(lab), self.prompts)
                if name:
                    dets.append(Det(name, tuple(float(v) for v in box.tolist()), float(score)))
            batch.append(dets if self.raw else clean(dets, w, h))
        return batch


class WithContainers:
    """Main prompt every call, container prompt every `every`-th call per camera
    (container boxes come back labelled "container:<name>")."""

    def __init__(self, det: GroundingDino, every: int = 1):
        self.det, self.cdet, self.every = det, det.with_prompts(CONTAINER_PROMPTS), max(1, every)
        self.calls: dict = {}

    def __call__(self, bgr, cam: int = 0) -> list:
        return self.batch([bgr], [cam])[0]

    def batch(self, frames, cams=None) -> list:
        cams = list(range(len(frames))) if cams is None else list(cams)
        if len(cams) != len(frames):
            raise ValueError("one camera ID per frame required")
        out = detect_batch(self.det, frames)
        if len(out) != len(frames):
            raise ValueError("detector returned the wrong batch length")
        selected = [i for i, cam in enumerate(cams) if self.calls.get(cam, 0) % self.every == 0]
        extra = detect_batch(self.cdet, [frames[i] for i in selected])
        if len(extra) != len(selected):
            raise ValueError("container detector returned the wrong batch length")
        for i, dets in zip(selected, extra):
            out[i] = out[i] + [Det(CONTAINER + d.label, d.box, d.score) for d in dets]
        for cam in cams:
            self.calls[cam] = self.calls.get(cam, 0) + 1
        return out


def detect_batch(detect, frames):
    """Adapters without batching retain their serial behavior."""
    if not frames:
        return []
    batch = getattr(detect, "batch", None)
    # Florence CPU batching was slower; other measured backends use one model call.
    use_batch = getattr(detect, "device", None) != "cpu" or getattr(detect, "batch_on_cpu", True)
    return batch(frames) if batch and use_batch else [detect(frame) for frame in frames]


class MotionGate:
    """Skip only pixel-identical frames, relative to the last successful model call.

    A full-resolution comparison avoids hiding slow tip movement or colour-only changes.
    Refresh at least once a second so scene persistence still receives observations.
    """
    def __init__(self, refresh: float = 1.0):
        self.refresh = refresh
        self.previous = {}

    def needs_detection(self, cam, t, frame):
        old = self.previous.get(cam)
        return (old is None or t < old[0] or t - old[0] >= self.refresh
                or old[1].shape != frame.shape or not np.array_equal(old[1], frame))

    def remember(self, cam, t, frame):
        self.previous[cam] = (t, frame.copy())


class AsyncDetector:
    """Live mode: one worker batches the newest pending frame from each camera,
    so hands and events keep the camera's frame rate."""

    def __init__(self, detect, n_cams: int):
        self.detect, self.n = detect, n_cams
        self.pending = [None] * n_cams
        self.results = [(None, [])] * n_cams
        self.version = [0] * n_cams
        self.lock, self.wake = threading.Lock(), threading.Event()
        self.stopping = False
        self.motion = MotionGate()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def submit(self, cam: int, t: float, frame):
        self.submit_batch([(cam, t, frame)])

    def submit_batch(self, jobs):
        """Publish available camera frames atomically, with no extra batch-fill delay."""
        with self.lock:
            for cam, t, frame in jobs:
                self.pending[cam] = (t, frame)
        self.wake.set()

    def latest(self, cam: int):
        with self.lock:
            return self.version[cam], self.results[cam]

    def _loop(self):
        while not self.stopping:
            self.wake.wait(0.2)
            if self.stopping:
                break
            with self.lock:
                jobs = [(cam, *job) for cam, job in enumerate(self.pending) if job is not None]
                self.pending = [None] * self.n
                self.wake.clear()
            jobs = [(c, t, f) for c, t, f in jobs if self.motion.needs_detection(c, t, f)]
            if not jobs:
                continue
            try:
                frames = [f for _, _, f in jobs]
                dets = (self.detect.batch(frames, [c for c, _, _ in jobs])
                        if isinstance(self.detect, WithContainers) else detect_batch(self.detect, frames))
                if len(dets) != len(jobs):
                    raise ValueError("detector returned the wrong batch length")
            except Exception as e:  # keep hands running if the detector fails once
                print(f"# detector error: {e}", flush=True)
                time.sleep(0.5)
                continue
            with self.lock:
                for (cam, t, frame), boxes in zip(jobs, dets):
                    self.motion.remember(cam, t, frame)
                    self.results[cam] = (t, boxes)
                    self.version[cam] += 1

    def close(self):
        self.stopping = True
        self.wake.set()
        self.thread.join()
