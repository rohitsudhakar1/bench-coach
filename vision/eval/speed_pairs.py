"""Offline two-view detector benchmark. No downloads; one detector per invocation.

BENCH_SAMPLES=... $BENCH_PY -m vision.eval.speed_pairs --detector florence2
"""
from __future__ import annotations
import argparse
import json
import os
import statistics
import signal
import re
import subprocess
import sys
import time
from pathlib import Path

import cv2

from ..core import sample
from ..sources import fit


def pairs(n=12, segment="414-445"):
    caps = []
    try:
        for side in ('Left', 'Right'):
            name = f'lsv_mv/CP-{side}-Front-{segment}.mp4'
            path = sample(name)
            if path is None:
                raise FileNotFoundError(f'Set BENCH_SAMPLES: missing {name}')
            caps.append(cv2.VideoCapture(str(path)))
        total = min(int(c.get(cv2.CAP_PROP_FRAME_COUNT)) for c in caps)
        for i in range(n):
            frames = []
            for cap in caps:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * total / n))
                ok, frame = cap.read()
                if not ok:
                    raise ValueError('Could not decode sample pair')
                frames.append(fit(frame, 640))
            yield frames
    finally:
        for cap in caps:
            cap.release()


def check_memory():
    if sys.platform == 'darwin':
        result = subprocess.check_output(['memory_pressure', '-Q'], text=True)
        free = re.search(r'free percentage: (\d+)', result)
        if free is None or int(free.group(1)) < 40:
            raise RuntimeError('Pause model runs: free memory is below 40% or could not be checked')


def sync(det):
    if det.device == 'mps':
        det.torch.mps.synchronize()
    elif det.device.startswith('cuda'):
        det.torch.cuda.synchronize()


def legacy(det, bgr, name):
    """Original single-image calls retained to reproduce the before measurement."""
    from ..core import Det
    from ..detector import canonical, clean
    from ..zoo import FLORENCE_SCORE
    h, w = bgr.shape[:2]
    if name == 'florence2':
        task = '<CAPTION_TO_PHRASE_GROUNDING>'
        inp = det.proc(text=task + det.caption, images=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB),
                       return_tensors='pt').to(det.device)
        with det.torch.no_grad():
            ids = det.model.generate(input_ids=inp['input_ids'], pixel_values=inp['pixel_values'],
                                     max_new_tokens=256, num_beams=1, do_sample=False)
        text = det.proc.batch_decode(ids, skip_special_tokens=False)[0]
        res = det.proc.post_process_generation(text, task=task, image_size=(w, h))[task]
        rows = zip(res.get('labels', []), res.get('bboxes', []),
                   [FLORENCE_SCORE] * len(res.get('labels', [])))
    else:
        inp = det.proc(images=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), text=det.text,
                       return_tensors='pt').to(det.device)
        with det.torch.no_grad():
            out = det.model(**inp)
        res = det.proc.post_process_grounded_object_detection(
            out, inp.input_ids, threshold=det.bt, text_threshold=det.tt, target_sizes=[(h, w)])[0]
        rows = zip(res['text_labels'], res['boxes'].tolist(), res['scores'].tolist())
    dets = []
    for lab, box, score in rows:
        label = canonical(str(lab), det.prompts)
        if label:
            dets.append(Det(label, tuple(float(v) for v in box), float(score)))
    return dets if det.raw else clean(dets, w, h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--detector', choices=['gdino-tiny', 'florence2'], required=True)
    ap.add_argument('--mode', choices=['legacy', 'serial', 'batch', 'auto'], default='auto')
    ap.add_argument('--dtype', choices=['float32', 'float16'], default='float32')
    ap.add_argument('--pairs', type=int, default=4, help='pairs per segment')
    ap.add_argument('--segments', nargs='+', choices=['12-27', '249-269', '414-445'],
                    default=['12-27', '249-269', '414-445'])
    ap.add_argument('--json', required=True)
    ap.add_argument('--threads', type=int, default=1)
    ap.add_argument('--budget', type=int, default=165)
    a = ap.parse_args()
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('model budget exhausted')))
    signal.alarm(a.budget)
    import torch
    torch.set_num_threads(a.threads)
    if a.pairs < 1 or a.threads < 1 or not 1 <= a.budget <= 170:
        ap.error('pairs/threads must be positive and budget must be 1..170 seconds')
    if a.detector != 'florence2' and a.dtype != 'float32':
        ap.error('float16 is a Florence-only candidate')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    from ..zoo import make
    from ..detector import pick_device
    if a.dtype == 'float16' and (a.mode == 'legacy' or pick_device() != 'mps'):
        ap.error('float16 requires MPS and an updated call mode')
    check_memory()
    imgs = [pair for segment in a.segments for pair in pairs(a.pairs, segment)]
    kw = {} if a.dtype == 'float32' else {'dtype': a.dtype}
    det = make(a.detector, **kw)
    from ..detector import detect_batch
    call = (lambda fs: detect_batch(det, fs)) if a.mode == 'auto' else det.batch if a.mode == 'batch' else (
        lambda fs: [legacy(det, f, a.detector) if a.mode == 'legacy' else det(f) for f in fs])
    for fs in imgs[:1]:
        call(fs)
    durations = []
    load = os.getloadavg()
    for fs in imgs:
        sync(det)
        start = time.perf_counter()
        call(fs)
        sync(det)
        durations.append(time.perf_counter() - start)
    result = dict(detector=a.detector, mode=a.mode, dtype=a.dtype, pairs=len(imgs), segments=a.segments,
                  device=det.device, threads=a.threads, load_average=load, seconds=sum(durations),
                  processor_size=str(det.proc.image_processor.size),
                  per_camera_hz=len(imgs)/sum(durations),
                  median_pair_ms=1000*statistics.median(durations),
                  pair_seconds=durations)
    Path(a.json).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)

if __name__ == '__main__':
    main()
