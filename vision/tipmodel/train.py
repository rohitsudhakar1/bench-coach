"""Fine-tune torchvision's Faster R-CNN MobileNetV3-Large FPN (BSD-3-Clause, COCO weights) on the
auto-labelled bench frames from dataset.py. Runs on MPS, CUDA or CPU.

    $BENCH_PY -m vision.tipmodel.train --minutes 8      # run again to go on where it stopped
    $BENCH_PY -m vision.tipmodel.train --fresh --epochs 24

Writes vision/models/tipmodel/checkpoint.pt while training and tipmodel.pt (weights only) at the
end. Neither is committed: rebuild with scripts in this folder (see vision/TIPMODEL-REPORT.md).
"""
from __future__ import annotations

import argparse
import json
import random
import time

import cv2
import numpy as np
import torch

from ..detector import pick_device
from .dataset import OUT

MIN_SIZE, MAX_SIZE = 480, 640


def build_model(num_classes: int = 2, pretrained: bool = True):
    from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_fpn
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
    m = fasterrcnn_mobilenet_v3_large_fpn(weights="DEFAULT" if pretrained else None,
                                          min_size=MIN_SIZE, max_size=MAX_SIZE)
    m.roi_heads.box_predictor = FastRCNNPredictor(m.roi_heads.box_predictor.cls_score.in_features, num_classes)
    return m


def augment(img, boxes, rng):
    """Random crop (keeps boxes at least half inside), left-right flip, brightness, contrast and hue."""
    h, w = img.shape[:2]
    boxes = boxes.copy()
    if rng.random() < 0.7:
        s = rng.uniform(0.75, 1.0)
        cw, ch = int(w * s), int(h * s)
        x0, y0 = rng.randint(0, w - cw), rng.randint(0, h - ch)
        nb = boxes.copy()
        nb[:, [0, 2]] = np.clip(nb[:, [0, 2]] - x0, 0, cw)
        nb[:, [1, 3]] = np.clip(nb[:, [1, 3]] - y0, 0, ch)
        kept = ((nb[:, 2] - nb[:, 0]) * (nb[:, 3] - nb[:, 1])) / np.maximum(
            (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1]), 1)
        if (kept >= 0.5).all():
            img, boxes = img[y0:y0 + ch, x0:x0 + cw], nb
            h, w = ch, cw
    if rng.random() < 0.5:
        img = img[:, ::-1]
        boxes[:, [0, 2]] = w - boxes[:, [2, 0]]
    img = img.astype(np.float32)
    img = (img - 128) * rng.uniform(0.75, 1.25) + 128 + rng.uniform(-25, 25)
    if rng.random() < 0.5:
        hsv = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV).astype(np.int16)
        hsv[..., 0] = (hsv[..., 0] + rng.randint(-8, 8)) % 180
        img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
    return np.clip(img, 0, 255), boxes


def to_tensor(bgr):
    rgb = np.ascontiguousarray(bgr[:, :, ::-1], dtype=np.float32) / 255.0
    return torch.from_numpy(rgb).permute(2, 0, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=24)
    ap.add_argument("--minutes", type=float, default=8.0, help="stop after this long; run again to go on")
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--lr", type=float, default=0.01)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    torch.manual_seed(a.seed)
    rng = random.Random(a.seed)
    dev = pick_device()
    items = json.loads((OUT / "dataset.json").read_text())["items"]
    imgs = {it["image"]: cv2.imread(str(OUT / "images" / it["image"])) for it in items}
    model = build_model().to(dev)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.SGD(params, lr=a.lr, momentum=0.9, weight_decay=1e-4)
    steps_per_epoch = (len(items) + a.batch - 1) // a.batch
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=a.epochs * steps_per_epoch, pct_start=0.1)
    ck = OUT / "checkpoint.pt"
    epoch = 0
    if ck.exists() and not a.fresh:
        state = torch.load(ck, map_location="cpu")
        model.load_state_dict(state["model"])
        opt.load_state_dict(state["opt"])
        sched.load_state_dict(state["sched"])
        epoch = state["epoch"]
        rng.setstate(tuple(state["rng"][0:1]) + (tuple(state["rng"][1]),) + tuple(state["rng"][2:]))
    t0 = time.time()
    model.train()
    while epoch < a.epochs:
        order = list(range(len(items)))
        rng.shuffle(order)
        losses = []
        for k in range(0, len(order), a.batch):
            batch_imgs, targets = [], []
            for i in order[k:k + a.batch]:
                it = items[i]
                im, bx = augment(imgs[it["image"]], np.array(it["boxes"], dtype=np.float32), rng)
                batch_imgs.append(to_tensor(im).to(dev))
                targets.append({"boxes": torch.tensor(bx, dtype=torch.float32, device=dev),
                                "labels": torch.tensor(it["labels"], dtype=torch.int64, device=dev)})
            loss = sum(model(batch_imgs, targets).values())
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            losses.append(float(loss))
        epoch += 1
        print(f"epoch {epoch}/{a.epochs} loss {np.mean(losses):.3f} {time.time() - t0:.0f}s", flush=True)
        torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "sched": sched.state_dict(),
                    "epoch": epoch, "rng": rng.getstate()}, ck)
        if time.time() - t0 > a.minutes * 60 and epoch < a.epochs:
            print("time used; run again to go on")
            return
    torch.save(model.state_dict(), OUT / "tipmodel.pt")
    print(f"saved {OUT / 'tipmodel.pt'}")


if __name__ == "__main__":
    main()
