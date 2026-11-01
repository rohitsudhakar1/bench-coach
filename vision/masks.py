"""True object outlines from a promptable segmenter, with the detector's boxes as prompts.

    from vision.masks import load_segmenter, outlines
    seg = load_segmenter()              # None when torch, transformers or the weights are missing
    polys = outlines(seg, frame, boxes) # one polygon (list of [x, y]) or None per box

The default model is EdgeTAM (Meta, Apache-2.0, 56 MB), run through Hugging Face transformers.
SAM 2.1 tiny (Meta, Apache-2.0, 156 MB) is the other choice (VISION_MASKS_MODEL=sam2.1-tiny).
All boxes of one frame go through the model in one call: the picture is encoded once and each
box only costs the small mask decoder.

When there is no model, `outlines` returns None for every box and the caller draws boxes, which
is what the demo did before. Nothing here downloads anything; scripts/fetch_models.py does that.
"""
from __future__ import annotations

import os
import time

import cv2
import numpy as np

MODELS = {"edgetam": "yonigozlan/EdgeTAM-hf", "sam2.1-tiny": "facebook/sam2.1-hiera-tiny"}
DEFAULT = "edgetam"
FILES = ("config.json", "model.safetensors", "preprocessor_config.json", "processor_config.json")


def mask_to_polygon(mask, min_area: float = 30.0, eps: float = 1.0):
    """The biggest outer outline of a boolean mask as [[x, y], ...], or None if it is too small."""
    m = np.asarray(mask).astype(np.uint8)
    if m.ndim != 2 or not m.any():
        return None
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < min_area:
        return None
    c = cv2.approxPolyDP(c, eps, True)
    if len(c) < 3:
        return None
    return [[float(p[0][0]), float(p[0][1])] for p in c]


def sensible(poly, box, min_fill: float = 0.12, max_spill: float = 0.25) -> bool:
    """Reject masks that fill almost none of the box or spill far outside it (the model latched
    onto the table or the background). Then the box is drawn instead."""
    if not poly:
        return False
    x1, y1, x2, y2 = box
    area_box = max(1.0, (x2 - x1) * (y2 - y1))
    pts = np.asarray(poly, np.float32)
    area = cv2.contourArea(pts)
    if area < min_fill * area_box:
        return False
    px1, py1 = pts.min(0)
    px2, py2 = pts.max(0)
    mx, my = max_spill * (x2 - x1), max_spill * (y2 - y1)
    return px1 >= x1 - mx and py1 >= y1 - my and px2 <= x2 + mx and py2 <= y2 + my


def weights_here(name: str = DEFAULT) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    for f in FILES:
        p = try_to_load_from_cache(MODELS[name], f)
        if not isinstance(p, str) or not os.path.isfile(p):
            return False
    return True


class Segmenter:
    """Box-prompted masks from EdgeTAM or SAM 2.1 tiny. Call with a BGR frame and a list of boxes."""

    def __init__(self, name: str = DEFAULT, device: str | None = None):
        import torch
        import transformers
        self.name, self.torch = name, torch
        if device is None:
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.device = device
        # the image models, not the video trackers AutoModel would pick for these repos
        cls = transformers.EdgeTamModel if name == "edgetam" else transformers.Sam2Model
        self.proc = transformers.Sam2Processor.from_pretrained(MODELS[name], local_files_only=True)
        self.model = cls.from_pretrained(MODELS[name], local_files_only=True).to(device).eval()
        self.ms = 0.0

    def masks(self, bgr, boxes) -> list:
        """One boolean mask (frame size) per box."""
        if not len(boxes):
            return []
        from PIL import Image
        torch = self.torch
        t0 = time.perf_counter()
        img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        bx = [[[float(v) for v in b] for b in boxes]]
        inputs = self.proc(images=img, input_boxes=bx, return_tensors="pt").to(self.device)
        with torch.no_grad():
            out = self.model(**inputs, multimask_output=False)
        m = self.proc.post_process_masks(out.pred_masks.cpu(), inputs["original_sizes"].cpu())[0]
        m = m.numpy()  # (boxes, 1, H, W)
        self.ms = (time.perf_counter() - t0) * 1000
        return [m[i, 0] > 0 for i in range(m.shape[0])]

    def __call__(self, bgr, boxes) -> list:
        out = []
        for b, mk in zip(boxes, self.masks(bgr, boxes)):
            poly = mask_to_polygon(mk)
            out.append(poly if sensible(poly, b) else None)
        return out


def load_segmenter(name: str | None = None, log=print):
    """The segmenter, or None (with one line saying why) so callers fall back to boxes."""
    name = name or os.environ.get("VISION_MASKS_MODEL", DEFAULT)
    if name not in MODELS:
        log(f"# masks: unknown model {name!r}; boxes only")
        return None
    if not weights_here(name):
        log(f"# masks: {name} weights not downloaded (scripts/fetch_models.py --download --with {name}); boxes only")
        return None
    try:
        return Segmenter(name)
    except Exception as e:  # missing torch or a broken file must not stop the demo
        log(f"# masks: could not load {name}: {type(e).__name__}: {e}; boxes only")
        return None


def outlines(seg, bgr, boxes) -> list:
    """One polygon or None per box. With no segmenter, or if it fails, every entry is None."""
    if seg is None or not len(boxes):
        return [None] * len(boxes)
    try:
        return seg(bgr, boxes)
    except Exception as e:
        print(f"# masks: {type(e).__name__}: {e}; boxes for this frame", flush=True)
        return [None] * len(boxes)
