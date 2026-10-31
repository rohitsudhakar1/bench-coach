"""Other open-vocabulary detectors, behind `--detector`, all returning the same Det boxes with the
names in core.py. Measured against Grounding DINO tiny in DETECTOR2-REPORT.md.

- gdino-tiny: Grounding DINO tiny (IDEA-Research, Apache 2.0), the default.
- gdino-base: Grounding DINO base (IDEA-Research, Apache 2.0), same code, bigger model.
- owlv2: OWLv2 base patch16 ensemble (Google, Apache 2.0), several phrasings per thing.
- florence2: Florence-2 base (Microsoft, MIT), phrase grounding of one caption. It gives no
  scores, so every box gets FLORENCE_SCORE.
- ensemble:<a>+<b>: both detectors, boxes of the same name that overlap merged (score = the
  higher one, box = the score-weighted mean).
"""
from __future__ import annotations

import copy

import cv2

from .core import Det, iou
from .detector import CONTAINER_PROMPTS, PROMPTS, GroundingDino, WithContainers, canonical, clean, pick_device

NAMES = ("gdino-tiny", "gdino-base", "owlv2", "florence2")
GDINO_BASE = "IDEA-Research/grounding-dino-base"
OWL_ID = "google/owlv2-base-patch16-ensemble"
FLORENCE_ID = "florence-community/Florence-2-base"
FLORENCE_SCORE = 0.5

# OWLv2 reads each phrase on its own, so it gets several phrasings per name.
OWL_PHRASES = {
    "hand": ["a hand", "a gloved hand"],
    "pipette": ["a pipette", "a micropipette", "a handheld pipette"],
    "pipette tip": ["a pipette tip", "a plastic pipette tip"],
    "tip box": ["a box of pipette tips", "a pipette tip rack"],
    "tube": ["a plastic tube", "a microcentrifuge tube"],
    "tube rack": ["a tube rack"],
    "well plate": ["a well plate"],
    "waste bin": ["a waste bin", "a waste beaker"],
}
OWL_CONTAINERS = {
    "bottle": ["a bottle"], "flask": ["a flask"], "beaker": ["a beaker"], "tube": ["a plastic tube"],
    "petri dish": ["a petri dish"], "well plate": ["a well plate"], "tube rack": ["a tube rack"],
}


class Owl2:
    def __init__(self, device: str | None = None, threshold: float = 0.15, raw: bool = False,
                 prompts: dict | None = None, phrases: dict | None = None):
        import torch
        from transformers import Owlv2ForObjectDetection, Owlv2Processor
        self.torch = torch
        self.device = device or pick_device()
        self.proc = Owlv2Processor.from_pretrained(OWL_ID)
        self.model = Owlv2ForObjectDetection.from_pretrained(OWL_ID).to(self.device).eval()
        self.th, self.raw = threshold, raw
        self.set_prompts(prompts or PROMPTS, phrases or OWL_PHRASES)

    def set_prompts(self, prompts: dict, phrases: dict):
        self.prompts = dict(prompts)
        self.queries, self.names = [], []
        for key, name in self.prompts.items():
            for q in phrases.get(key, [key]):
                self.queries.append(q)
                self.names.append(name)
        self.text_inputs = None

    def with_prompts(self, prompts: dict) -> "Owl2":
        other = copy.copy(self)
        other.set_prompts(prompts, OWL_CONTAINERS if prompts is CONTAINER_PROMPTS else OWL_PHRASES)
        return other

    def __call__(self, bgr) -> list:
        h, w = bgr.shape[:2]
        inp = self.proc(text=[self.queries], images=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB),
                        return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            out = self.model(**inp)
        side = max(h, w)  # OWLv2 pads to a square before resizing
        res = self.proc.post_process_grounded_object_detection(
            out, threshold=self.th, target_sizes=[(side, side)])[0]
        dets = []
        for lab, box, score in zip(res["labels"].tolist(), res["boxes"], res["scores"]):
            x0, y0, x1, y1 = box.tolist()
            b = (max(0.0, x0), max(0.0, y0), min(float(w), x1), min(float(h), y1))
            if b[2] > b[0] and b[3] > b[1]:
                dets.append(Det(self.names[lab], b, float(score)))
        return dets if self.raw else clean(dets, w, h)


class Florence2:
    def __init__(self, device: str | None = None, raw: bool = False, prompts: dict | None = None,
                 model_id: str = FLORENCE_ID):
        import torch
        from transformers import AutoProcessor, Florence2ForConditionalGeneration
        self.torch = torch
        self.device = device or pick_device()
        self.proc = AutoProcessor.from_pretrained(model_id)
        self.model = Florence2ForConditionalGeneration.from_pretrained(model_id).to(self.device).eval()
        self.raw = raw
        self.set_prompts(prompts or PROMPTS)

    def set_prompts(self, prompts: dict):
        self.prompts = dict(prompts)
        self.caption = ". ".join(self.prompts) + "."

    def with_prompts(self, prompts: dict) -> "Florence2":
        other = copy.copy(self)
        other.set_prompts(prompts)
        return other

    def __call__(self, bgr) -> list:
        h, w = bgr.shape[:2]
        task = "<CAPTION_TO_PHRASE_GROUNDING>"
        inp = self.proc(text=task + self.caption, images=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB),
                        return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            ids = self.model.generate(input_ids=inp["input_ids"], pixel_values=inp["pixel_values"],
                                      max_new_tokens=256, num_beams=1, do_sample=False)
        text = self.proc.batch_decode(ids, skip_special_tokens=False)[0]
        res = self.proc.post_process_generation(text, task=task, image_size=(w, h))[task]
        dets = []
        for lab, box in zip(res.get("labels", []), res.get("bboxes", [])):
            name = canonical(str(lab), self.prompts)
            if name:
                dets.append(Det(name, tuple(float(v) for v in box), FLORENCE_SCORE))
        return dets if self.raw else clean(dets, w, h)


def merge(a: list, b: list, overlap: float = 0.5) -> list:
    """Boxes from two detectors: same name and IoU over `overlap` become one box."""
    out = [Det(d.label, d.box, d.score) for d in a]
    for d in b:
        same = [k for k, o in enumerate(out) if o.label == d.label and iou(o.box, d.box) > overlap]
        if not same:
            out.append(d)
            continue
        k = max(same, key=lambda k: iou(out[k].box, d.box))
        o = out[k]
        s = o.score + d.score
        box = tuple((o.box[i] * o.score + d.box[i] * d.score) / s for i in range(4))
        out[k] = Det(d.label, box, max(o.score, d.score))
    return out


class Ensemble:
    def __init__(self, a, b, raw: bool = False):
        self.a, self.b, self.raw = a, b, raw

    def with_prompts(self, prompts: dict) -> "Ensemble":
        return Ensemble(self.a.with_prompts(prompts), self.b.with_prompts(prompts), self.raw)

    def __call__(self, bgr) -> list:
        h, w = bgr.shape[:2]
        dets = merge(self.a(bgr), self.b(bgr))
        return dets if self.raw else clean(dets, w, h)


def make(name: str = "gdino-tiny", raw: bool = False, **kw):
    """A detector by name (see NAMES, or ensemble:<a>+<b>)."""
    if name.startswith("ensemble:"):
        a, b = name.split(":", 1)[1].split("+")
        return Ensemble(make(a, raw=True), make(b, raw=True), raw)
    if name == "gdino-tiny":
        return GroundingDino(raw=raw, **kw)
    if name == "gdino-base":
        return GroundingDino(raw=raw, model_id=GDINO_BASE, **kw)
    if name == "owlv2":
        return Owl2(raw=raw, **kw)
    if name == "florence2":
        return Florence2(raw=raw, **kw)
    raise ValueError(f"unknown detector {name!r}; one of {', '.join(NAMES)} or ensemble:<a>+<b>")


__all__ = ["NAMES", "make", "merge", "Ensemble", "Owl2", "Florence2", "WithContainers"]
