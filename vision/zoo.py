"""Other open-vocabulary detectors, behind `--detector`, all returning the same Det boxes with the
names in core.py. Measured against Grounding DINO tiny in DETECTOR2-REPORT.md.

- gdino-tiny: Grounding DINO tiny (IDEA-Research, Apache 2.0), the default.
- gdino-base: Grounding DINO base (IDEA-Research, Apache 2.0), same code, bigger model.
- owlv2: OWLv2 base patch16 ensemble (Google, Apache 2.0), several phrasings per thing.
- florence2: Florence-2 base (Microsoft, MIT), phrase grounding of one caption. Its boxes have
  no score of their own, so each box's score is how sure the model was when it started writing
  that phrase (see phrase_scores); a box that repeats an earlier box of another name is marked
  down. Measured in FLORENCE-REPORT.md.
- yoloe: YOLOE-26 large (Ultralytics, AGPL-3.0: using it in a product means opening that
  product's source, or buying an Ultralytics licence) with text prompts. Weights are looked up in
  VISION_YOLO_DIR (yoloe-26l-seg.pt and its mobileclip2_b.ts text encoder), else downloaded there.
- ensemble:<a>+<b>: both detectors, boxes of the same name that overlap merged (score = the
  higher one, box = the score-weighted mean).
"""
from __future__ import annotations

import copy
import os
from contextlib import contextmanager
from pathlib import Path

import cv2

from .core import Det, iou
from .detector import CONTAINER_PROMPTS, PROMPTS, GroundingDino, WithContainers, canonical, clean, pick_device

NAMES = ("gdino-tiny", "gdino-base", "owlv2", "florence2", "yoloe")
GDINO_BASE = "IDEA-Research/grounding-dino-base"
OWL_ID = "google/owlv2-base-patch16-ensemble"
FLORENCE_ID = "florence-community/Florence-2-base"
FLORENCE_SCORE = 0.5  # used only when the token probabilities cannot be lined up with the boxes
# Per-name score cuts for the live (non-raw) output, picked on the tuning frames by
# vision.eval.florence_cut. Waste is cut hard because no labelled clip has a waste bin, so every
# waste box seen so far was wrong; on a bench that has one, check this first.
FLORENCE_CUTS = {"tip box": 0.3, "waste": 0.9}
FLORENCE_DUP = 0.1  # score multiplier for a box that copies an earlier box of another name

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
    batch_on_cpu = False

    def __init__(self, device: str | None = None, raw: bool = False, prompts: dict | None = None,
                 model_id: str = FLORENCE_ID, dtype: str = "float32"):
        import torch
        from transformers import AutoProcessor, Florence2ForConditionalGeneration
        self.torch = torch
        self.device = device or pick_device()
        self.proc = AutoProcessor.from_pretrained(model_id)
        if dtype not in ("float32", "float16"):
            raise ValueError("dtype must be float32 or float16")
        self.dtype = getattr(torch, dtype)
        self.model = Florence2ForConditionalGeneration.from_pretrained(model_id).to(
            device=self.device, dtype=self.dtype).eval()
        self.raw = raw
        self.last_parts: list = []  # per box of the last raw call: the numbers behind its score
        self.set_prompts(prompts or PROMPTS)

    def set_prompts(self, prompts: dict):
        self.prompts = dict(prompts)
        self.caption = ". ".join(self.prompts) + "."

    def with_prompts(self, prompts: dict) -> "Florence2":
        other = copy.copy(self)
        other.set_prompts(prompts)
        return other

    def __call__(self, bgr) -> list:
        return self.batch([bgr])[0]

    def batch(self, frames) -> list:
        """Detections for each camera's frame, in order."""
        if not frames:
            return []
        task = "<CAPTION_TO_PHRASE_GROUNDING>"
        # Florence-2 runs one frame at a time so each box keeps its own confidence score.
        return [self._one(bgr, task) for bgr in frames]

    def _one(self, bgr, task) -> list:
        h, w = bgr.shape[:2]
        inp = self.proc(text=task + self.caption, images=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB),
                        return_tensors="pt").to(self.device)
        inp["pixel_values"] = inp["pixel_values"].to(dtype=self.dtype)
        steps = []

        def keep(_ids, logits):  # every step's token probabilities, to score the boxes afterwards
            steps.append(self.torch.log_softmax(logits.float(), -1))
            return logits

        from transformers import LogitsProcessorList
        with self.torch.no_grad():
            ids = self.model.generate(input_ids=inp["input_ids"], pixel_values=inp["pixel_values"],
                                      max_new_tokens=256, num_beams=1, do_sample=False,
                                      logits_processor=LogitsProcessorList([keep]))
        seq = ids[0]
        # step k chose token seq[k + 1] (seq[0] is the decoder start)
        n = min(len(steps), len(seq) - 1)
        chosen = seq[1:n + 1]
        probs = self.torch.stack([steps[k][0, chosen[k]] for k in range(n)]).exp().tolist() if n else []
        toks = self.proc.tokenizer.convert_ids_to_tokens(chosen.tolist())
        text = self.proc.batch_decode(ids, skip_special_tokens=False)[0]
        res = self.proc.post_process_generation(text, task=task, image_size=(w, h))[task]
        labels, bxs = res.get("labels", []), res.get("bboxes", [])
        parts = phrase_scores(toks, probs)
        if len(parts) != len(bxs):
            parts = [None] * len(bxs)
        dets, self.last_parts = [], []
        for lab, box, part in zip(labels, bxs, parts):
            name = canonical(str(lab), self.prompts)
            if not name:
                continue
            box = tuple(float(v) for v in box)
            if part is None:
                score, part = FLORENCE_SCORE, {}
            else:
                dup = any(d.label != name and iou(d.box, box) > 0.9 for d in dets)
                part = {**part, "dup": dup}
                score = part["start"] * (FLORENCE_DUP if dup else 1.0)
            dets.append(Det(name, box, round(score, 3)))
            self.last_parts.append(part)
        if self.raw:
            return dets
        return clean(present(dets), w, h)


def present(dets: list, cuts: dict = FLORENCE_CUTS) -> list:
    """Florence-2 boxes whose name's cut they reach (names with no cut are all kept)."""
    return [d for d in dets if d.score >= cuts.get(d.label, 0.0)]


def phrase_scores(toks: list, probs: list) -> list:
    """One entry per box in Florence-2's output order: the probability of the first token of the
    box's phrase ("start", the model's choice to name that thing at all), the product over the
    phrase's tokens ("phrase") and the mean over the box's four location tokens ("loc"). A phrase
    with several boxes gives each box the same phrase numbers."""
    out, phrase, k = [], None, 0
    while k < len(toks):
        t = toks[k]
        if t.startswith("<loc_"):
            if phrase is None or k + 4 > len(toks):
                return []
            loc = probs[k:k + 4]
            out.append({"start": round(phrase[0], 3), "phrase": round(_prod(phrase), 3),
                        "loc": round(sum(loc) / 4, 3)})
            k += 4
            continue
        if t in ("<s>", "</s>", "<pad>"):
            phrase = None if t == "</s>" else phrase
            k += 1
            continue
        if k == 0 or toks[k - 1].startswith("<loc_") or toks[k - 1] == "<s>" or phrase is None:
            phrase = []
        phrase.append(probs[k])
        k += 1
    return out


def _prod(xs):
    r = 1.0
    for x in xs:
        r *= x
    return r


YOLOE_WEIGHTS = "yoloe-26l-seg.pt"
YOLO_PHRASES = {"hand": "hand", "pipette": "pipette", "pipette tip": "pipette tip", "tip box": "pipette tip box",
                "tube": "plastic tube", "tube rack": "tube rack", "well plate": "well plate", "waste bin": "waste bin",
                "bottle": "bottle", "flask": "flask", "beaker": "beaker", "petri dish": "petri dish"}


@contextmanager
def _in_yolo_dir():
    """Ultralytics finds and downloads weights in the working folder, so work in VISION_YOLO_DIR."""
    d = os.environ.get("VISION_YOLO_DIR")
    old = os.getcwd()
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        os.chdir(d)
    try:
        yield
    finally:
        os.chdir(old)


class YoloE:
    def __init__(self, device: str | None = None, threshold: float = 0.1, raw: bool = False,
                 prompts: dict | None = None, imgsz: int = 640):
        from ultralytics import YOLOE
        self.device = device or pick_device()
        with _in_yolo_dir():
            self.model = YOLOE(YOLOE_WEIGHTS)
        self.th, self.raw, self.imgsz = threshold, raw, imgsz
        self.set_prompts(prompts or PROMPTS)

    def set_prompts(self, prompts: dict):
        self.prompts = dict(prompts)
        self.keys = list(self.prompts)
        texts = [YOLO_PHRASES.get(k, k) for k in self.keys]
        with _in_yolo_dir():
            self.pe = self.model.get_text_pe(texts)
        self.texts = texts

    def with_prompts(self, prompts: dict) -> "YoloE":
        other = copy.copy(self)
        other.set_prompts(prompts)
        return other

    def __call__(self, bgr) -> list:
        h, w = bgr.shape[:2]
        self.model.set_classes(self.texts, self.pe)
        r = self.model.predict(bgr, conf=self.th, imgsz=self.imgsz, device=self.device, verbose=False)[0]
        dets = []
        for box, score, k in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist()):
            dets.append(Det(self.prompts[self.keys[int(k)]], tuple(float(v) for v in box), float(score)))
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
    if name == "yoloe":
        return YoloE(raw=raw, **kw)
    raise ValueError(f"unknown detector {name!r}; one of {', '.join(NAMES)} or ensemble:<a>+<b>")


__all__ = ["NAMES", "make", "merge", "Ensemble", "Owl2", "Florence2", "WithContainers", "phrase_scores"]
