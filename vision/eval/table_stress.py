"""Deterministic rendered stress clips; no recordings or recognition models needed.

python -m vision.eval.table_stress --output vision/eval/table_stress_results.json
Use --videos DIR to retain the MJPEG fixtures for inspection.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import types

import cv2
import numpy as np

from vision import table
from vision.table_colour import BandTracker, learn, quality

BASELINE = '98840791ea5e6150eacd3a39e7bdd3fdd6674062'
CONDITIONS = ('clean', 'warm', 'cool', 'dim', 'glare', 'hand_shadow', 'hue_plus15',
              'hue_minus15', 'low_saturation', 'glove', 'tilt10', 'tilt20', 'shake',
              'clutter', 'combined')
POINTS = {'tube A': (70, 45), 'tube B': (160, 45), 'tube C': (250, 45),
          'tip box': (55, 195), 'waste': (265, 195)}
SEQUENCE = ('tip box', 'tube A', 'tube B', 'tube B', 'tube C', 'waste')
KINDS = ('tip_on', 'draw', 'dispense', 'hand_touch', 'draw', 'tip_off')


def fixture(condition, seed):
    rng = np.random.default_rng(seed)
    background = np.full((240, 320, 3), 100, np.uint8)
    shift = 15 if condition == 'hue_plus15' else -15 if condition in ('hue_minus15', 'combined') else 0
    saturation = 85 if condition == 'low_saturation' else 170 if condition == 'combined' else 240
    for name, hue in zip(table.TUBES, (0, 120, 30)):
        colour = cv2.cvtColor(np.uint8([[[(hue+shift)%180, saturation, 220]]]), cv2.COLOR_HSV2BGR)[0, 0]
        cv2.circle(background, POINTS[name], 10, tuple(map(int, colour)), -1)
    cv2.rectangle(background, (35, 175), (75, 215), (140, 140, 140), -1)
    cv2.circle(background, POINTS['waste'], 20, (40, 40, 40), -1)
    if condition in ('clutter', 'combined'):
        cv2.circle(background, (290, 120), 13, (240, 0, 240), -1)
        cv2.rectangle(background, (10, 90), (40, 120), (0, 230, 0), -1)
        cv2.circle(background, (300, 25), 12, (230, 230, 0), -1)
    angle = 10 if condition == 'tilt10' else 20 if condition in ('tilt20', 'combined') else 0
    # Project a plane viewed about its horizontal axis; top edge is farther away.
    corners = np.float32([[0, 0], [319, 0], [319, 239], [0, 239]])
    centred = corners - [160, 120]
    radians = math.radians(angle)
    depth = 400 + centred[:, 1]*math.sin(radians)
    projected = np.column_stack((centred[:, 0]*400/depth+160,
                                 centred[:, 1]*math.cos(radians)*400/depth+120))
    transform = cv2.getPerspectiveTransform(corners, projected.astype(np.float32))
    def project(point):
        return cv2.perspectiveTransform(np.float32([[point]]), transform)[0, 0].tolist()
    gains = np.array([.65, .85, 1.25]) if condition in ('warm', 'combined') else np.array([1.3, .9, .65]) if condition == 'cool' else np.ones(3)
    if condition in ('dim', 'combined'):
        gains *= .28 if condition == 'dim' else .5
    def render(point, index):
        frame = background.copy()
        if point is not None:
            p = tuple(np.round(point).astype(int))
            cv2.circle(frame, p, 6, (230, 0, 230), -1)
            if condition in ('glove', 'combined'):
                cv2.rectangle(frame, (p[0]-8, p[1]-8), (p[0]-1, p[1]+8), (190, 140, 90), -1)
            if condition == 'hand_shadow':
                x, y = p
                frame[max(0,y-20):y+20, max(0,x-25):x+25] = (frame[max(0,y-20):y+20, max(0,x-25):x+25]*.27).astype(np.uint8)
            if condition == 'glare':
                cv2.circle(frame, (p[0]-2, p[1]-2), 3, (255, 255, 255), -1)
        if condition == 'glare':
            for x, y in [POINTS[n] for n in table.TUBES]:
                cv2.circle(frame, (x-3, y-3), 4, (255, 255, 255), -1)
        frame = cv2.warpPerspective(frame, transform, (320, 240), borderMode=cv2.BORDER_REPLICATE)
        if index >= 0 and condition in ('shake', 'combined'):
            dx, dy = rng.integers(-3, 4, 2)
            frame = cv2.warpAffine(frame, np.float32([[1, 0, dx], [0, 1, dy]]), (320, 240), borderMode=cv2.BORDER_REPLICATE)
        noise = rng.normal(0, 1.2, frame.shape)
        frame = np.clip(frame*gains + noise, 0, 255).astype(np.uint8)
        # Every measured frame goes through a lossy camera-like JPEG round trip.
        return cv2.imdecode(cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 88])[1], cv2.IMREAD_COLOR)
    setup = render(None, -1)
    frames, expected = [], []
    centre = np.array([160., 125.])
    for name, kind in zip(SEQUENCE, KINDS):
        target = np.array(POINTS[name], dtype=float)
        # Edge visits exercise enlarged cap regions without changing ground-truth identity.
        if condition == 'shake' and name in table.TUBES:
            target[0] += 14
        for point in np.linspace(centre, target, 8):
            frames.append(render(point, len(frames)))
        entered = len(frames)-1
        for _ in range(6):
            frames.append(render(target, len(frames)))
        exit_frame = len(frames)
        for point in np.linspace(target, centre, 8):
            frames.append(render(point, len(frames)))
        # Match within this visit, including motion into/out of the region.
        expected.append(dict(kind=kind, object=name, start=(entered-3)/10, end=(exit_frame+5)/10))
    frames += [render(centre, len(frames)+i) for i in range(4)]
    return setup, frames, expected, {n: [*project(POINTS[n]), 20] for n in ('tip box', 'waste')}


def measure(module, setup, frames, expected, fixed, robust):
    regs = module.find_caps(setup)
    regs.update(fixed)
    if any(n not in regs for n in table.NAMES):
        return dict(right=0, emitted=0, expected=len(expected), exact=False, setup=False)
    try:
        cfg = module.validate(dict(size=[320, 240], regions=regs))
    except ValueError:
        return dict(right=0, emitted=0, expected=len(expected), exact=False, setup=False)
    if robust:
        cfg['colour_calibration'] = learn(setup, regs)
        band = BandTracker(cfg['colour_calibration'])
    tracker = module.TableTracker(regs)
    events = []
    for i, frame in enumerate(frames):
        point = band.update(frame, i/10) if robust else module.marker(frame)
        events += tracker.update(i/10, point)
    unmatched = list(expected)
    right = 0
    for event in events:
        match = next((e for e in unmatched if (e['kind'], e['object']) == (event['kind'], event['object'])
                      and e['start'] <= event['t'] <= e['end']), None)
        if match:
            right += 1
            unmatched.remove(match)
    return dict(right=right, emitted=len(events), expected=len(expected),
                exact=right == len(expected) == len(events), setup=True,
                quality=quality(setup, cfg)[0] if robust else None)


def run(seeds=4, videos=None):
    baseline = types.ModuleType('vision._table_baseline')
    baseline.__file__ = str(Path(table.__file__))
    exec(subprocess.check_output(['git', 'show', BASELINE+':vision/table.py'], text=True), baseline.__dict__)
    rows = []
    for condition in CONDITIONS:
        totals = {key: dict(right=0, emitted=0, expected=0, exact=0, setup=0) for key in ('before', 'after')}
        for seed in range(seeds):
            setup, frames, expected, fixed = fixture(condition, seed)
            if videos and seed == 0:
                videos.mkdir(parents=True, exist_ok=True)
                writer = cv2.VideoWriter(str(videos / (condition+'.avi')), cv2.VideoWriter_fourcc(*'MJPG'), 10, (320, 240))
                for frame in [setup, *frames]:
                    writer.write(frame)
                writer.release()
            for key, module in [('before', baseline), ('after', table)]:
                result = measure(module, setup, frames, expected, fixed, key == 'after')
                for field in totals[key]:
                    totals[key][field] += result[field]
        rows.append(dict(condition=condition, runs=seeds, **totals))
        print(json.dumps(rows[-1]), flush=True)
    return dict(baseline=BASELINE, seeds=list(range(seeds)), rows=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--videos', type=Path)
    parser.add_argument('--seeds', type=int, default=4)
    args = parser.parse_args()
    results = run(args.seeds, args.videos)
    if args.output:
        args.output.write_text(json.dumps(results, indent=2)+'\n')
