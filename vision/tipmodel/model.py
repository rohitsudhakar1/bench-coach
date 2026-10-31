"""Run the trained tip model, alone or in place of Grounding DINO's pipette boxes.

`TipModel` returns `pipette` boxes only. `WithTipModel` is what `--detector tipmodel` uses: the
Grounding DINO boxes for everything else (hands, tip box, tubes, racks, plates, waste), with its
pipette and pipette tip boxes swapped for the tip model's.
"""
from __future__ import annotations

import torch

from ..core import PIPETTE, PIPETTE_TIP, Det
from ..detector import pick_device
from .dataset import OUT
from .train import build_model, to_tensor

WEIGHTS = OUT / "tipmodel.pt"


class TipModel:
    def __init__(self, device: str | None = None, threshold: float = 0.5, weights=WEIGHTS):
        if not weights.exists():
            raise SystemExit(f"no tip model weights at {weights}; build them with "
                             "`python -m vision.tipmodel.rebuild` (see vision/TIPMODEL-REPORT.md)")
        self.device = device or pick_device()
        self.model = build_model(pretrained=False)
        self.model.load_state_dict(torch.load(weights, map_location="cpu"))
        self.model.to(self.device).eval()
        self.threshold = threshold

    def __call__(self, bgr) -> list:
        with torch.no_grad():
            out = self.model([to_tensor(bgr).to(self.device)])[0]
        return [Det(PIPETTE, tuple(float(v) for v in b.tolist()), float(s))
                for b, s in zip(out["boxes"].cpu(), out["scores"].cpu()) if float(s) >= self.threshold]


class WithTipModel:
    """Grounding DINO (or WithContainers) for everything but pipettes, the tip model for pipettes."""

    def __init__(self, det, tip: TipModel):
        self.det, self.tip = det, tip

    def __call__(self, bgr, cam: int = 0) -> list:
        from ..detector import WithContainers
        base = self.det(bgr, cam) if isinstance(self.det, WithContainers) else self.det(bgr)
        return [d for d in base if d.label not in (PIPETTE, PIPETTE_TIP)] + self.tip(bgr)

