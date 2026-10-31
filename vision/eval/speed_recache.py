"""Rebuild held-out main detections with the runtime batch policy, in short offline chunks.

Hands and container boxes stay copied from the old cache; score only default-mode boxes.
Repeat a detector command until it reports complete, then use --score (no model).
"""
from __future__ import annotations
import argparse
import gzip
import json
import os
import signal
import time
from pathlib import Path

import cv2

from ..core import sample
from ..detector import detect_batch
from ..sources import fit
from .speed_pairs import check_memory, legacy

HERE = Path(__file__).parent


def read(path):
    with gzip.open(path, 'rt') as fh:
        return json.load(fh)


def write(path, data):
    with gzip.open(path, 'wt') as fh:
        json.dump(data, fh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--detector', choices=['gdino-tiny', 'florence2'], required=True)
    ap.add_argument('--limit', type=int, default=64)
    ap.add_argument('--score', action='store_true')
    ap.add_argument('--mode', choices=['auto', 'legacy'], default='auto')
    a = ap.parse_args()
    if a.limit < 1 or a.limit > 64:
        ap.error('limit must be 1..64')
    variant = a.detector if a.mode == 'auto' else f'legacy-{a.detector}'
    out = HERE / f'cache-speed-{variant}'
    source = HERE / ('cache' if a.detector == 'gdino-tiny' else 'cache-florence2')
    labels = json.loads((HERE / 'labels.json').read_text())
    clips = [c for c in labels['clips'].values() if c['split'] == 'hold']
    stems = [Path(c['path']).stem + '@0' for c in clips]
    ranges = {stem: (clip['start'], clip['start'] + clip['seconds']) for stem, clip in zip(stems, clips)}
    out.mkdir(exist_ok=True)
    meta_path = out / 'meta.json'
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {
        'detector': a.detector, 'mode': a.mode, 'min_score': .25 if a.detector == 'gdino-tiny' else 0,
        'main_only': True, 'scope': 'held-out label windows', 'completed': {}, 'chunks': []}
    jobs, data = [], {}
    for stem in stems:
        target = out / (stem + '.json.gz')
        data[stem] = read(target if target.exists() else source / target.name)
        done = set(meta['completed'].get(stem, []))
        lo, hi = ranges[stem]
        jobs += [(stem, i) for i, fr in enumerate(data[stem]['frames'])
                 if 'dets' in fr and i not in done and lo - 1e-6 <= fr['t'] <= hi + 1e-6]
    if a.score:
        if jobs:
            raise SystemExit(f'{len(jobs)} model frames still pending; refusing partial-cache score')
        os.environ['VISION_EVAL_CACHE'] = out.name
        os.environ['VISION_EVAL_MIN_SCORE'] = str(meta['min_score'])
        from .score import score
        result = score(labels, {}, 'hold')
        (HERE / f'speed-heldout-{variant}.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result), flush=True)
        return
    if not jobs:
        print('complete', flush=True)
        return
    check_memory()
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('model budget exhausted')))
    signal.alarm(165)
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import torch
    torch.set_num_threads(1)
    from ..zoo import make
    from .cache import boxes
    det = make(a.detector, raw=True, **({'box_threshold': .2} if a.detector == 'gdino-tiny' else {}))
    caps = {}
    touched = set()
    start = time.perf_counter()
    selected = jobs[:a.limit]
    try:
        for k in range(0, len(selected), 2):
            group = selected[k:k+2]
            images = []
            for stem, index in group:
                record = data[stem]
                if stem not in caps:
                    path = sample(record['video'])
                    if path is None:
                        raise FileNotFoundError('Set BENCH_SAMPLES: missing '+record['video'])
                    caps[stem] = cv2.VideoCapture(str(path))
                cap = caps[stem]
                fr = record['frames'][index]
                # Cache time is one past the decoded frame, as in eval.recache.
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, round(fr['t']*cap.get(cv2.CAP_PROP_FPS))-1))
                ok, image = cap.read()
                if not ok:
                    raise ValueError('could not decode cached frame')
                image = fit(image, 640)
                if image.shape[:2] != (fr['h'], fr['w']):
                    raise ValueError('cache/frame geometry mismatch')
                images.append(image)
            results = ([legacy(det, image, a.detector) for image in images] if a.mode == 'legacy'
                       else detect_batch(det, images))
            for (stem, index), dets in zip(group, results):
                data[stem]['frames'][index]['dets'] = boxes(dets)
                meta['completed'].setdefault(stem, []).append(index)
                touched.add(stem)
    finally:
        for cap in caps.values():
            cap.release()
        for stem in touched:
            write(out / (stem + '.json.gz'), data[stem])
        meta['chunks'].append({'completed_total': sum(len(meta['completed'].get(s, [])) for s in stems),
                               'seconds': time.perf_counter()-start, 'device': det.device,
                               'load_average': os.getloadavg()})
        meta_path.write_text(json.dumps(meta, indent=2)+'\n')
    print(f'{a.detector}: {len(selected)} frames completed; {len(jobs)-len(selected)} pending', flush=True)

if __name__ == '__main__':
    main()
