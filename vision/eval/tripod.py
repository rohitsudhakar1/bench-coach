"""Audit both 12-27 views from cached detections; save three paired annotated stills.

Run: $BENCH_PY -m vision.eval.tripod (set BENCH_SAMPLES for the stills).
Labels are used only to score the output, never to choose or reject a live box.
"""
import json
from pathlib import Path

import cv2
from angle.feed import FeedState

from ..annotate import draw
from ..core import sample, Det
from .replay import frames, replay
from .score import centre, inside

HERE = Path(__file__).parent
TIMES = (2.1, 7.3, 12.5)


def audit():
    labels = json.loads((HERE / 'labels.json').read_text())
    out = HERE / 'out'
    out.mkdir(exist_ok=True)
    stills, results = {}, {}
    for name in ('mv12L', 'mv12R'):
        clip = labels['clips'][name]
        fs = frames(clip['path'])
        if not fs:
            raise RuntimeError(f'No cached frames for {name}')
        counts = dict(frames=0, selected=0, selected_on_distractor=0, emitted=0, emitted_on_distractor=0, angle_crops=0, angle_on_distractor=0, before_angle_crops=0, before_angle_on_distractor=0)
        wanted = {min(fs, key=lambda f: abs(f['t'] - t))['t']: t for t in TIMES}
        path = sample(clip['path'])
        cap = cv2.VideoCapture(str(path)) if path else None

        def wrong(box):
            return any(inside(centre(box), b) for b in clip['scene']['not'])

        events = replay([fs], new_draw=True)
        object_events = {e['t']: e for e in events if e['kind'] == 'objects'}
        still_state = FeedState()

        def grab(cam, fr, vis):
            if fr['t'] in object_events:
                still_state.accept(object_events[fr['t']], now=fr['t'])

            eng = vis.engines[cam]
            counts['frames'] += 1
            if eng.pip:
                counts['selected'] += 1
                counts['selected_on_distractor'] += wrong(eng.pip.box)
            if fr['t'] not in wanted or cap is None:
                return
            # Cache timestamps are the end of a sampled source frame.
            fps = cap.get(cv2.CAP_PROP_FPS)
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, round(fr['t'] * fps) - 1))
            ok, image = cap.read()
            if not ok:
                raise RuntimeError(f'Cannot decode {name} at {fr["t"]}')
            image = cv2.resize(image, (fr['w'], fr['h']))
            lines = [f'{name}  {fr["t"]:.1f}s  cached model replay',
                     'Angle path only: held boxes; draw path is unchanged']
            crop = still_state.snapshot([0], [image.shape], now=fr['t'])[0][0]
            dets = []
            if crop:
                x, y, w, h = crop
                dets = [Det('pipette', (x, y, x+w, y+h), 1.0)]
            image = draw(image, 0 if name.endswith('L') else 1, [], dets, {}, [], 10)
            image = cv2.copyMakeBorder(image, 0, 50, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
            for i, line in enumerate(lines):
                cv2.putText(image, line, (8, fr['h'] + 18 + i * 21), cv2.FONT_HERSHEY_SIMPLEX,
                            0.45, (255, 255, 255), 1, cv2.LINE_AA)
            stills.setdefault(wanted[fr['t']], []).append(image)

        try:
            events = replay([fs], new_draw=True, on_frame=grab)
        finally:
            if cap is not None:
                cap.release()
        state = FeedState()
        before_state = FeedState()
        for e in events:
            state.accept(e, now=e['t'])
            # Before round 1 the same objects flowed to angle with no held check.
            before = {**e, 'detail': {**e['detail']}}
            if e['kind'] == 'objects':
                before['detail']['boxes'] = [{**b, 'held': True} for b in e['detail']['boxes']]
            before_state.accept(before, now=e['t'])
            if e['kind'] == 'objects':
                crop = state.snapshot([0], [(fs[0]['h'], fs[0]['w'], 3)], now=e['t'])[0][0]
                if crop:
                    x, y, w, h = crop
                    counts['angle_crops'] += 1
                    counts['angle_on_distractor'] += wrong((x, y, x+w, y+h))
                before_crop = before_state.snapshot([0], [(fs[0]['h'], fs[0]['w'], 3)], now=e['t'])[0][0]
                if before_crop:
                    x, y, w, h = before_crop
                    counts['before_angle_crops'] += 1
                    counts['before_angle_on_distractor'] += wrong((x, y, x+w, y+h))
                for b in e['detail']['boxes']:
                    if 'pipette' in b['label']:
                        counts['emitted'] += 1
                        counts['emitted_on_distractor'] += wrong(b['box'])
        counts['draw_hints'] = sum(e['kind'] == 'draw' for e in events)
        counts['dispense_hints'] = sum(e['kind'] == 'dispense' for e in events)
        results[name] = counts
    for t, images in stills.items():
        if len(images) == 2:
            cv2.imwrite(str(out / f'tripod-{t:.1f}s.jpg'), cv2.hconcat(images))
    (out / 'tripod-audit.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))
    if not stills:
        print('Stills skipped: set BENCH_SAMPLES to the folder containing lsv_mv/.')
    if any(v['angle_on_distractor'] for v in results.values()):
        raise SystemExit('Angle accepted a known distractor')
    return results


if __name__ == '__main__':
    audit()
