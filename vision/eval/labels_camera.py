"""Small reproducible camera OCR check; sample images never enter the repository.

Apple Vision is the default. --backend tesseract is an explicitly separate
optional diagnostic, never a production fallback. --deskew rotates the crop by
the supplied degrees before OCR (positive is counterclockwise).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from vision.labels import native_helper, recognize, OCRUnavailable, parse_crop


def tesseract_words(image, scale: int = 1) -> list[dict]:
    import cv2
    encoded = cv2.imencode('.png', image)[1].tobytes()
    result = subprocess.run(['tesseract', 'stdin', 'stdout', '-l', 'eng', '--psm', '11', 'tsv'],
                            input=encoded, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Tesseract failed')
    words = []
    for row in csv.DictReader(io.StringIO(result.stdout.decode('utf-8')), delimiter='\t'):
        if row['level'] != '5' or not row['text'].strip():
            continue
        words.append({'text': row['text'], 'confidence': float(row['conf']) / 100,
                      'box': [int(row[key]) / scale for key in ('left', 'top', 'width', 'height')]})
    return words


def evaluate(args, binary=None):
    import cv2
    cap = cv2.VideoCapture(str(args.source))
    if not cap.isOpened():
        raise ValueError('cannot open video')
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        with tempfile.TemporaryDirectory(prefix='bench-label-eval-') as directory:
            image = Path(directory) / 'frame.png'
            for t in args.times:
                index = round(t * fps)
                if not 0 <= index < count:
                    raise ValueError('sample time outside video')
                cap.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, frame = cap.read()
                if not ok:
                    raise ValueError('could not decode frame')
                fh, fw = frame.shape[:2]
                crop = args.crop or (0, 0, fw, fh)
                x, y, w, h = crop
                if x + w > fw or y + h > fh:
                    raise ValueError('crop outside frame')
                patch = frame[y:y+h, x:x+w]
                if args.deskew:
                    patch = cv2.warpAffine(
                        patch, cv2.getRotationMatrix2D((w / 2, h / 2), args.deskew, 1),
                        (w, h), borderValue=(255, 255, 255))
                if args.upscale != 1:
                    patch = cv2.resize(patch, (w * args.upscale, h * args.upscale),
                                       interpolation=cv2.INTER_CUBIC)
                started = time.perf_counter()
                record = {'source': args.source.name, 'backend': args.backend, 't': index / fps,
                          'frame_size': [fw, fh], 'crop': crop, 'upscale': args.upscale,
                          'deskew_degrees': args.deskew}
                try:
                    if args.backend == 'vision':
                        cv2.imwrite(str(image), patch)
                        words = recognize(binary, image, t=index / fps, crop=crop)
                        record['box_space'] = 'original-frame xywh'
                    else:
                        words = tesseract_words(patch, args.upscale)
                        record['box_space'] = 'crop after rotation, before enlargement, xywh'
                    record.update(status='ok', words=words)
                except OCRUnavailable as exc:
                    record.update(status='unavailable', reason=str(exc), words=[])
                record['seconds'] = round(time.perf_counter() - started, 4)
                print(json.dumps(record, ensure_ascii=False), flush=True)
    finally:
        cap.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--times', type=float, nargs='+', default=[0, 6, 12, 15, 18, 21])
    parser.add_argument('--crop', type=parse_crop)
    parser.add_argument('--upscale', type=int, choices=[1, 2], default=1)
    parser.add_argument('--deskew', type=float, default=0)
    parser.add_argument('--backend', choices=['vision', 'tesseract'], default='vision')
    args = parser.parse_args(argv)
    if args.backend == 'vision' and args.deskew:
        parser.error('deskew is diagnostic-only; Vision boxes must stay in original pixels')
    if args.backend == 'tesseract':
        if not shutil.which('tesseract'):
            parser.error('optional tesseract executable is not installed')
        evaluate(args)
    else:
        with native_helper() as binary:
            evaluate(args, binary)


if __name__ == '__main__':
    main()
