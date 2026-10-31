"""Fit two fixed cameras from a level reference followed by varied checkerboard poses."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time

import cv2
import httpx
import numpy as np

from .geometry import Calibration, angle_between
from .sources import read_jpeg


def folder_pairs(left, right):
    folders = [[p for p in sorted(Path(folder).iterdir()) if p.suffix.lower() in ('.png', '.jpg', '.jpeg')]
               for folder in (left, right)]
    if not folders[0] or [p.name for p in folders[0]] != [p.name for p in folders[1]]:
        raise ValueError('Image filenames must match in both nonempty folders')
    for a, b in zip(*folders):
        yield a.name, cv2.imread(str(a)), cv2.imread(str(b))


def video_pairs(paths, interval, duration, offset):
    readers = []
    try:
        for path in paths:
            reader = cv2.VideoCapture(path)
            readers.append(reader)
            if not reader.isOpened():
                raise ValueError('Cannot open both calibration videos')
        for index, seconds in enumerate(np.arange(0, duration, interval)):
            frames = []
            for i, reader in enumerate(readers):
                reader.set(cv2.CAP_PROP_POS_MSEC, 1000*(seconds+(offset if i else 0)))
                found, image = reader.read()
                frames.append(image if found else None)
            if any(image is None for image in frames):
                break
            yield f'{index:04d}.png', *frames
    finally:
        for reader in readers:
            reader.release()


def validate_times(stamps, previous, now, max_skew):
    if any(not np.isfinite(t) or not 0 <= now-t <= 1 for t in stamps):
        raise ValueError('Capture frames are stale or future dated')
    if max(stamps)-min(stamps) > max_skew:
        raise ValueError('Capture timestamps are too far apart')
    if any(new <= old for new, old in zip(stamps, previous)):
        raise ValueError('Capture frames have not advanced')


def capture_pairs(base, interval, duration, max_skew):
    previous = [-np.inf, -np.inf]
    with httpx.Client(timeout=2) as client, ThreadPoolExecutor(max_workers=2) as pool:
        started = time.monotonic()
        index = 0
        while time.monotonic()-started < duration:
            futures = [pool.submit(read_jpeg, client, f'{base.rstrip("/")}/cam/{i}/frame.jpg') for i in (0, 1)]
            try:
                frames = [f.result() for f in futures]
                stamps = [f.stamp for f in frames]
                validate_times(stamps, previous, time.time(), max_skew)
            except (httpx.HTTPError, ValueError) as exc:
                if index == 0:
                    raise ValueError(f'First level reference unavailable: {exc}') from exc
                print(f'Skipped pair: {exc}', file=sys.stderr)
            else:
                previous = stamps
                yield f'{index:04d}.png', *(f.image for f in frames)
                index += 1
            time.sleep(max(0, min(interval, duration-(time.monotonic()-started))))


def collect(pairs, pattern, save=None):
    left, right, names, size = [], [], [], None
    for index, (name, a, b) in enumerate(pairs):
        corners = []
        for image in (a, b):
            if image is None:
                raise ValueError(f'Unreadable image in pair {name}')
            current = (image.shape[1], image.shape[0])
            if size is not None and size != current:
                raise ValueError('All images in both views must have the same resolution')
            size = current
            found, points = cv2.findChessboardCornersSB(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), pattern)
            corners.append(points if found else None)
        if any(points is None for points in corners):
            if index == 0:
                raise ValueError('The first pair must show the entire level board in both views')
            print(f'Skipped {name}: board not found in both views', file=sys.stderr)
            continue
        left.append(corners[0])
        right.append(corners[1])
        names.append(name)
        if save:
            for side, image in (('left', a), ('right', b)):
                folder = Path(save)/side
                folder.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(str(folder/name), image):
                    raise ValueError('Cannot save calibration pair')
        print(f'Accepted {len(names)}: {name}' + ('; level reference saved' if len(names) == 1 else ''), flush=True)
    return left, right, names, size


def fit(left, right, names, size, pattern=(9, 6), square_mm=25):
    if len(left) < 12 or len(left) != len(right) or len(names) != len(left):
        raise ValueError('Need at least 12 paired board poses')
    distinct = []
    for points in left:
        if not any(np.sqrt(np.mean((points-other)**2)) < 2 for other in distinct):
            distinct.append(points)
    if len(distinct) < 12:
        raise ValueError('Need at least 12 distinct board poses')
    model = np.zeros((pattern[0]*pattern[1], 3), np.float32)
    model[:, :2] = np.mgrid[:pattern[0], :pattern[1]].T.reshape(-1, 2)*square_mm
    objects = [model for _ in left]
    fits = [cv2.calibrateCamera(objects, views, size, None, None) for views in (left, right)]
    (error1, k1, d1, r1, t1), (error2, k2, d2, r2, t2) = fits
    normals = [cv2.Rodrigues(vector)[0][:, 2] for vector in r1]
    if max(angle_between(a, b) for a in normals for b in normals) < 15:
        raise ValueError('Tilt the board through at least 15 degrees in varied poses')
    stereo = cv2.stereoCalibrate(objects, left, right, k1, d1, k2, d2, size,
                                flags=cv2.CALIB_FIX_INTRINSIC,
                                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-7))
    rms, _, _, _, _, rotation, translation, _, _ = stereo
    per_pair = []
    for index, name in enumerate(names):
        row = {'pair': name}
        for side, views, intrinsic, distortion, rotations, translations in (
                ('left', left, k1, d1, r1, t1), ('right', right, k2, d2, r2, t2)):
            projected = cv2.projectPoints(model, rotations[index], translations[index], intrinsic, distortion)[0]
            residual = projected.reshape(-1, 2) - views[index].reshape(-1, 2)
            row[side+'_rms_px'] = float(np.sqrt(np.mean(np.sum(residual**2, axis=1))))
        per_pair.append(row)
    metrics = [error1, error2, rms] + [row[side+'_rms_px'] for row in per_pair for side in ('left', 'right')]
    if not np.isfinite(metrics).all() or max(metrics) > 2:
        raise ValueError(f'Fit exceeds 2 pixel limit: left {error1:.3f}, right {error2:.3f}, stereo {rms:.3f}; check blur and corner ordering')
    if np.linalg.norm(translation) < square_mm:
        raise ValueError('Camera baseline is smaller than one board square')
    cal = Calibration(k1, d1, k2, d2, rotation, translation, normals[0], size)
    result = {key: np.asarray(getattr(cal, key)).tolist() for key in cal.__dataclass_fields__}
    result.update(rms_px=float(rms), left_rms_px=float(error1), right_rms_px=float(error2),
                  per_pair=per_pair, pairs=len(left), gravity_reference=names[0], square_mm=square_mm,
                  pattern=list(pattern), translation_units='mm', physical_check='pending')
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--left')
    p.add_argument('--right')
    p.add_argument('--source', action='append')
    p.add_argument('--capture')
    p.add_argument('--save-pairs')
    p.add_argument('--interval', type=float, default=2)
    p.add_argument('--duration', type=float, default=60)
    p.add_argument('--right-offset', type=float, default=0)
    p.add_argument('--max-skew', type=float, default=.1)
    p.add_argument('--cols', type=int, default=9)
    p.add_argument('--rows', type=int, default=6)
    p.add_argument('--square-mm', type=float, default=25)
    p.add_argument('--output', default='angle/calibration.json')
    args = p.parse_args(argv)
    try:
        if sum(map(bool, (args.left or args.right, args.source, args.capture))) != 1:
            raise ValueError('Choose two folders, two video sources, or capture')
        values = (args.interval, args.duration, args.square_mm, args.max_skew, args.right_offset)
        if (not all(np.isfinite(v) for v in values) or min(values[:4]) <= 0
                or args.right_offset < 0 or min(args.cols, args.rows) < 3):
            raise ValueError('Sizes and times must be finite and positive; offset nonnegative; at least 3x3 corners')
        if args.left or args.right:
            if not args.left or not args.right:
                raise ValueError('Both --left and --right folders are required')
            pairs = folder_pairs(args.left, args.right)
        elif args.source:
            if len(args.source) != 2:
                raise ValueError('Exactly two --source videos are required')
            pairs = video_pairs(args.source, args.interval, args.duration, args.right_offset)
        else:
            pairs = capture_pairs(args.capture, args.interval, args.duration, args.max_skew)
        if args.save_pairs:
            Path(args.save_pairs).mkdir(parents=True, exist_ok=False)
        try:
            observations = collect(pairs, (args.cols, args.rows), args.save_pairs)
        finally:
            pairs.close()
        result = fit(*observations, (args.cols, args.rows), args.square_mm)
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
        temporary.replace(path)
        print(f'GOOD fit: left {result["left_rms_px"]:.3f}, right {result["right_rms_px"]:.3f}, '
              f'stereo {result["rms_px"]:.3f} pixels RMS. Saved {path}. Physical pencil check still required.')
        return 0
    except (ValueError, OSError, cv2.error, httpx.HTTPError) as exc:
        print(f'REDO: {exc}. Previous calibration file unchanged.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
