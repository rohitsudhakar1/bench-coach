"""Compare original float32 and batched calls on labelled frames, offline and in short chunks.

Run each --offset (0, 16, ..., 112) separately. Half precision is a candidate, never a default.
"""
from __future__ import annotations
import argparse
import json
import os
import signal
import time
from pathlib import Path

import cv2

from ..core import iou, sample
from ..sources import fit
from .speed_pairs import check_memory, legacy

HERE = Path(__file__).parent


def agreement(before, after, tolerance=.99):
    """One-to-one label/box matches; extra or missing detections fail the check."""
    remaining = list(after)
    overlaps = []
    scores = []
    for d in before:
        candidates = [i for i, other in enumerate(remaining) if other.label == d.label]
        if not candidates:
            return False, 0.0, None
        index = max(candidates, key=lambda i: iou(d.box, remaining[i].box))
        other = remaining.pop(index)
        overlaps.append(iou(d.box, other.box))
        scores.append(abs(d.score - other.score))
    minimum = min(overlaps, default=1.0)
    delta = max(scores, default=0.0)
    return not remaining and minimum >= tolerance and delta <= .001, minimum, delta


def labelled_frames(offset, limit):
    labels = json.loads((HERE / 'labels.json').read_text())
    for label in labels['frames'][offset:offset + limit]:
        clip = labels['clips'][label['clip']]
        path = sample(clip['path'])
        if path is None:
            raise FileNotFoundError(f"Set BENCH_SAMPLES: missing {clip['path']}")
        cap = cv2.VideoCapture(str(path))
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, round(label['t'] * fps) - 1))
            ok, frame = cap.read()
            if not ok:
                raise ValueError('labelled frame could not be read')
            yield label, clip, fit(frame, 640)
        finally:
            cap.release()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--detector', choices=['gdino-tiny', 'florence2'], required=True)
    ap.add_argument('--dtype', choices=['float32', 'float16'], default='float32')
    ap.add_argument('--offset', type=int, default=0)
    ap.add_argument('--limit', type=int, default=16)
    ap.add_argument('--budget', type=int, default=165)
    ap.add_argument('--json', required=True)
    a = ap.parse_args()
    if a.limit < 1 or a.offset < 0 or not 1 <= a.budget <= 170:
        ap.error('limit must be positive, offset nonnegative, and budget 1..170 seconds')
    if a.detector != 'florence2' and a.dtype != 'float32':
        ap.error('float16 is a Florence-only candidate')
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('model budget exhausted')))
    signal.alarm(a.budget)
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import torch
    torch.set_num_threads(1)
    from ..zoo import make
    from ..detector import pick_device
    if a.dtype == 'float16' and pick_device() != 'mps':
        ap.error('Half-precision acceptance requires MPS')
    check_memory()
    from .cache import boxes
    rows = list(labelled_frames(a.offset, a.limit))
    if not rows:
        ap.error('no labelled frames in the requested range')
    det = make(a.detector)
    start = time.perf_counter()
    before = [legacy(det, frame, a.detector) for _, _, frame in rows]
    if a.dtype == 'float16':
        if det.device != 'mps':
            raise RuntimeError('Half-precision acceptance requires MPS')
        det.dtype = torch.float16
        det.model.to(dtype=det.dtype)
    after = []
    for i in range(0, len(rows), 2):
        after.extend(det.batch([f for _, _, f in rows[i:i+2]]))
    records = []
    for (label, clip, _), b, c in zip(rows, before, after):
        passed, minimum, delta = agreement(b, c)
        records.append(dict(clip=label['clip'], t=label['t'], split=clip['split'],
                            passed=passed, min_iou=minimum, max_score_delta=delta,
                            before=boxes(b), after=boxes(c)))
    result = dict(detector=a.detector, dtype=a.dtype, device=det.device, offset=a.offset,
                  seconds=time.perf_counter()-start, load_average=os.getloadavg(),
                  passed=sum(r['passed'] for r in records), frames=len(records), records=records)
    Path(a.json).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'records'}), flush=True)

if __name__ == '__main__':
    main()
