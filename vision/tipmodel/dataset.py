"""Build the tip model's training set: auto-labels from the current detector, kept only when a hand
holds the box, on clips that no held-out set uses.

    $BENCH_PY -m vision.tipmodel.dataset autolabel --budget 150   # run the models on extra clips
    $BENCH_PY -m vision.tipmodel.dataset build                    # pick boxes, write images
    $BENCH_PY -m vision.tipmodel.dataset sheet out.jpg            # look at a sample of what was kept

Everything goes to vision/models/tipmodel/ (ignored by git). `autolabel` resumes where it stopped
and stops after --budget seconds, so it runs in short pieces.
"""
from __future__ import annotations

import argparse
import gzip
import json
import random
import time
from pathlib import Path

import cv2

from ..core import HAND, PIPETTE, PIPETTE_TIP, Det, Hand, area, iou
from ..events import holding_hand
from ..eval.cache import load
from .frames import read_at

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent / "eval"
OUT = HERE.parent / "models" / "tipmodel"
CLASSES = (PIPETTE,)  # model label 1; tip boxes from the detector were too often wrong to learn

# Clips held out in labels.json, actions.json or tips.json never go into training, nor does any
# other camera of the same moment.
CACHED_TRAIN = ["lsv_mv/CP-Left-Front-12-27.mp4", "lsv_dji/pcr_105.mp4", "lsv_dji/pcr_xm048.mp4",
                "lsv_dji/pcr_xm050.mp4", "lsv_dji/serial_xm052.mp4", "lsv_hood/cas9_hood_035.mp4"]
EXTRA_TRAIN = ["lsv_dji/cas9_037.mp4", "lsv_dji/cas9_038.mp4", "lsv_dji/cas9_039.mp4", "lsv_dji/cas9_040.mp4",
               "lsv_dji/cas9_correct.mp4", "lsv_dji/cas9demo_014.mp4", "lsv_dji/pcr_106.mp4",
               "lsv_dji/pcr_107.mp4", "lsv_dji/pcr_108.mp4", "lsv_dji/pcr_109.mp4", "lsv_dji/pcr_110.mp4",
               "lsv_dji/transform_correct.mp4", "lsv_dji/transform_skip3.mp4",
               "lsv_hood/cas9_hood_036.mp4", "lsv_hood/cytokine_096.mp4", "lsv_hood/cytokine_099.mp4",
               "lsv_hood/lenti_xm079.mp4", "lsv_hood/lenti_xm080.mp4", "lsv_hood/split_025.mp4",
               "lsv_hood/split_026.mp4", "multicam_test/camA.mp4"]
HELD_OUT = {"lsv_mv/CP-Right-Front-12-27.mp4", "lsv_mv/CP-Left-Front-249-269.mp4",
            "lsv_mv/CP-Right-Front-249-269.mp4", "lsv_mv/CP-Left-Front-414-445.mp4",
            "lsv_mv/CP-Right-Front-414-445.mp4", "lsv_dji/serial_xm055.mp4", "lsv_dji/pcr_xm049.mp4",
            "lsv_dji/pcr_xm051.mp4", "lsv_hood/cas9_hood_033.mp4", "lsv_hood/cas9_hood_034.mp4"}
EXTRA_EVERY = 2.0  # seconds between auto-labelled frames on the extra clips
CACHED_EVERY = 0.7

MIN_SCORE = 0.25 # detector score a pipette box needs to become a label
MAX_AREA = 0.2  # share of the frame
MIN_LONG = 1.5  # long side over short side
MIN_SIZE = 0.12  # long side of the box over the frame's long side


def held_out_videos() -> set:
    out = set(HELD_OUT)
    for f in ("labels.json", "actions.json", "tips.json"):
        for c in json.loads((EVAL / f).read_text())["clips"].values():
            if c["split"] == "hold":
                out.add(c["path"])
    return out


def pick(rec: dict) -> list | None:
    """(label index, box) for one model record, or None when the frame is not worth keeping.
    A pipette is a long thin "pipette" box (or "pipette tip" box, which the detector often puts on
    the whole pipette) with a hand's points on it."""
    w, h = rec["w"], rec["h"]
    dets = [Det(d["label"], tuple(d["box"]), d["score"]) for d in rec["dets"]]
    hands = [Hand(x["side"], [tuple(p) for p in x["pts"]]) for x in rec["hands"]]
    hands += [Hand("left", None, d.box) for d in dets if d.label == HAND and d.score >= 0.3
              and not any(iou(d.box, hh.box or _ptbox(hh.pts)) > 0.3 for hh in hands)]
    if not hands:
        return None
    pips = []
    for d in sorted(dets, key=lambda d: -d.score):
        if d.label not in (PIPETTE, PIPETTE_TIP) or d.score < MIN_SCORE or area(d.box) > MAX_AREA * w * h:
            continue
        x1, y1, x2, y2 = d.box
        if max(x2 - x1, y2 - y1) < MIN_LONG * min(x2 - x1, y2 - y1):  # a pipette is long and thin
            continue
        if max(x2 - x1, y2 - y1) < MIN_SIZE * max(w, h):  # small boxes were mostly something else
            continue
        if not any(_grips(hh, d.box) for hh in hands):
            continue
        if any(iou(d.box, p) > 0.3 or _overlap(d.box, p) > 0.6 for p in pips):
            continue
        pips.append(d.box)
    return [(1, b) for b in pips] or None


def _grips(hand: Hand, box) -> bool:
    """A hand landmark inside the box (padded by 0.3 hand sizes); for a box-only hand,
    the boxes overlap by a third of the smaller one."""
    if hand.pts:
        pad = 0.3 * hand.size
        n = sum(box[0] - pad <= x <= box[2] + pad and box[1] - pad <= y <= box[3] + pad for x, y in hand.pts)
        return n >= 1
    a = hand.box
    inter = max(0, min(a[2], box[2]) - max(a[0], box[0])) * max(0, min(a[3], box[3]) - max(a[1], box[1]))
    return inter >= min(area(a), area(box)) / 3


def _ptbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _overlap(a, b):
    """Share of box a inside box b."""
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return max(0, x2 - x1) * max(0, y2 - y1) / max(area(a), 1e-6)


def autolabel(budget: float):
    """Grounding DINO and MediaPipe hands on every EXTRA_EVERY s of the extra clips."""
    from ..detector import GroundingDino
    from ..eval.cache import boxes
    from ..hands import HandTracker
    from ..core import sample
    auto = OUT / "auto"
    auto.mkdir(parents=True, exist_ok=True)
    det, t0 = None, time.time()
    for rel in EXTRA_TRAIN:
        assert rel not in held_out_videos()
        dst = auto / (Path(rel).stem + ".json.gz")
        if dst.exists() or sample(rel) is None:
            continue
        if time.time() - t0 > budget:
            print("budget used; run again to go on")
            return False
        det = det or GroundingDino(box_threshold=0.2, text_threshold=0.2, raw=True)
        cap = cv2.VideoCapture(str(sample(rel)))
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) / (cap.get(cv2.CAP_PROP_FPS) or 30.0)
        cap.release()
        times = [round(EXTRA_EVERY * (k + 0.5), 3) for k in range(int(n / EXTRA_EVERY))]
        recs = []
        trk = HandTracker()
        for t, img in sorted(read_at(rel, times).items()):
            recs.append({"t": t, "w": img.shape[1], "h": img.shape[0], "dets": boxes(det(img)),
                         "hands": [{"side": hh.side, "pts": [[round(x, 1), round(y, 1)] for x, y in hh.pts]}
                                   for hh in trk(img, t)]})
        trk.close()
        with gzip.open(dst, "wt") as fh:
            json.dump({"video": rel, "frames": recs}, fh)
        print(f"{rel}: {len(recs)} frames, {time.time() - t0:.0f}s so far", flush=True)
    return True


def records():
    """(video, record) for every candidate training frame."""
    held = held_out_videos()
    for rel in CACHED_TRAIN:
        assert rel not in held
        last = -1e9
        for r in load(rel):
            if "dets" in r and r["t"] - last >= CACHED_EVERY - 1e-6:
                last = r["t"]
                yield rel, r
    for p in sorted((OUT / "auto").glob("*.json.gz")):
        with gzip.open(p, "rt") as fh:
            d = json.load(fh)
        assert d["video"] not in held
        for r in d["frames"]:
            yield d["video"], r


def build():
    img_dir = OUT / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    by_video: dict = {}
    for rel, r in records():
        p = pick(r)
        if p:
            by_video.setdefault(rel, []).append((r["t"], p))
    items = []
    for rel, rows in by_video.items():
        imgs = read_at(rel, [t for t, _ in rows])
        for t, p in rows:
            if t not in imgs:
                continue
            name = f"{Path(rel).stem}_{t:08.3f}.jpg"
            cv2.imwrite(str(img_dir / name), imgs[t], [cv2.IMWRITE_JPEG_QUALITY, 92])
            items.append({"image": name, "video": rel, "t": t,
                          "boxes": [list(map(float, b)) for _, b in p], "labels": [k for k, _ in p]})
    (OUT / "dataset.json").write_text(json.dumps({"classes": CLASSES, "items": items}))
    print(f"{len(items)} images from {len(by_video)} videos")
    for rel, rows in sorted(by_video.items()):
        print(f"  {rel}: {len(rows)}")


def sheet(path: str, n: int = 40, seed: int = 1):
    import numpy as np
    d = json.loads((OUT / "dataset.json").read_text())
    rng = random.Random(seed)
    tiles = []
    for it in rng.sample(d["items"], min(n, len(d["items"]))):
        img = cv2.imread(str(OUT / "images" / it["image"]))
        for b, k in zip(it["boxes"], it["labels"]):
            cv2.rectangle(img, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (0, 255, 0) if k == 1 else (0, 0, 255), 2)
        img = cv2.resize(img, (320, int(320 * img.shape[0] / img.shape[1])))
        img = cv2.copyMakeBorder(img, 0, 240 - img.shape[0], 0, 0, cv2.BORDER_CONSTANT) if img.shape[0] < 240 else img[:240]
        tiles.append(img)
    rows = [np.hstack(tiles[i:i + 5]) for i in range(0, len(tiles) - len(tiles) % 5, 5)]
    cv2.imwrite(path, np.vstack(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("autolabel", "build", "sheet"))
    ap.add_argument("out", nargs="?")
    ap.add_argument("--budget", type=float, default=150)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    if a.cmd == "autolabel":
        autolabel(a.budget)
    elif a.cmd == "build":
        build()
    else:
        sheet(a.out, seed=a.seed)


if __name__ == "__main__":
    main()
