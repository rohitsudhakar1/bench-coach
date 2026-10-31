"""Rerun inherited hand-labelled development stills and seeded synthetic stereo."""
import json
from pathlib import Path

import cv2
import numpy as np
from .detection import detect_axis, overlay_frame
from .geometry import Calibration, image_angle, stereo_angle

ROOT = Path(__file__).parent


def synthetic_cases(noise=0., count=1000):
    rng = np.random.default_rng(42)
    k = np.array([[900., 0, 640], [0, 900, 360], [0, 0, 1]])
    rotation = cv2.Rodrigues(np.array([0., -.65, .03]))[0]
    translation = np.array([-220., 10., 40.])
    calibration = Calibration(k, np.zeros(5), k, np.zeros(5), rotation, translation, [0, 1, 0], (1280, 720))
    errors = []
    for _ in range(count):
        tilt, azimuth = rng.uniform(0, np.pi/2), rng.uniform(-np.pi, np.pi)
        direction = np.array([np.sin(tilt)*np.cos(azimuth), np.cos(tilt), np.sin(tilt)*np.sin(azimuth)])
        center = np.array([rng.uniform(-80, 80), rng.uniform(-80, 80), rng.uniform(700, 1200)])
        ends = center + np.array([-100, 100])[:, None]*direction
        axes = []
        for r, t in ((np.eye(3), np.zeros(3)), (rotation, translation)):
            pixels = (k @ (r @ ends.T + t[:, None])).T
            axes.append(pixels[:, :2]/pixels[:, 2:] + rng.normal(0, noise, (2, 2)))
        try:
            actual, _ = stereo_angle(*axes, calibration)
        except ValueError:
            continue
        errors.append(abs(actual-np.degrees(tilt)))
    return dict(attempted=count, accepted=len(errors), rejected=count-len(errors),
                mae_deg=float(np.mean(errors)), p95_deg=float(np.percentile(errors, 95)),
                max_deg=float(max(errors)), pixel_noise_sigma=noise)


def main():
    data = json.loads((ROOT/'samples/labels.json').read_text())
    rows, tiles = [], []
    for item in data['samples']:
        image = cv2.imread(str(ROOT/'samples'/item['file']))
        if image is None:
            raise ValueError(f'Missing sample {item["file"]}')
        ends = np.array(item['axis'])
        lo, hi = ends.min(axis=0)-item['padding'], ends.max(axis=0)+item['padding']
        crop = [*lo.tolist(), *(hi-lo).tolist()]
        axis = detect_axis(image, crop)
        truth = image_angle(ends)
        predicted = image_angle(axis.points) if axis else None
        error = abs(truth-predicted) if axis else None
        rows.append(dict(file=item['file'], roi=crop, reference_deg=truth, predicted_deg=predicted, error_deg=error))
        label = f'{item["file"]}: {error:.2f} deg error' if axis else f'{item["file"]}: NO SHAFT'
        tile = overlay_frame(image, axis, crop, label)
        cv2.line(tile, tuple(ends[0]), tuple(ends[1]), (255, 0, 255), 2)
        tiles.append(cv2.resize(tile, (640, 360)))
    errors = [r['error_deg'] for r in rows if r['error_deg'] is not None]
    result = dict(sample_count=len(rows), detected=len(errors), missing=len(rows)-len(errors),
                  mae_deg=float(np.mean(errors)), max_deg=float(max(errors)), rows=rows,
                  meaning='Projected angle on inherited hand-cropped development stills, not physical tilt accuracy.',
                  synthetic_clean=synthetic_cases(), synthetic_noisy=synthetic_cases(.5))
    (ROOT/'samples/results.json').write_text(json.dumps(result, indent=2)+'\n')
    cv2.imwrite(str(ROOT/'samples/evaluation.jpg'), np.vstack([np.hstack(tiles[i:i+2]) for i in range(0, len(tiles), 2)]))
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, indent=2))


if __name__ == '__main__':
    main()
