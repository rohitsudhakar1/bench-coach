"""Offline label OCR. JSONL boxes use original-frame pixels and a top-left origin.

`t` is video time in seconds, plus --time-origin (default 0). These analysis rows
are not hub events. No model, key, network, detector or other service is needed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


class OCRUnavailable(RuntimeError):
    """The native OCR backend cannot run on this host."""


def parse_crop(value: str) -> tuple[int, int, int, int]:
    """Parse an original-frame pixel rectangle: x,y,width,height."""
    try:
        parts = tuple(int(v.strip()) for v in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("crop must be x,y,width,height integers") from exc
    if len(parts) != 4 or min(parts[:2]) < 0 or min(parts[2:]) <= 0:
        raise argparse.ArgumentTypeError("crop needs nonnegative x,y and positive width,height")
    return parts


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def parse_observations(payload: str, *, t: float,
                       crop: tuple[int, int, int, int]) -> list[dict]:
    """Validate Swift output and undo crop/scale and Vision's vertical origin.

    Swift reports normalized boxes, so upscaling does not change this mapping.
    Reject a malformed response rather than emitting partially trusted results.
    """
    rows = json.loads(payload)
    if not isinstance(rows, list):
        raise ValueError("OCR response must be a list")
    result = []
    ox, oy, width, height = crop
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            raise ValueError("OCR observation needs text")
        confidence, box = row.get("confidence"), row.get("box")
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError("OCR confidence must be between zero and one")
        if not isinstance(box, list) or len(box) != 4 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in box
        ):
            raise ValueError("OCR box must contain four finite numbers")
        x, y, w, h = box
        if min(x, y) < -1e-6 or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001:
            raise ValueError("OCR box lies outside image")
        result.append({"t": t, "text": row["text"], "confidence": confidence,
                       "box": [round(ox + max(0, x) * width, 3),
                               round(oy + max(0, 1 - y - h) * height, 3),
                               round(w * width, 3), round(h * height, 3)]})
    return result


@contextmanager
def native_helper():
    """Compile in a private temporary directory; clean it up even after failures."""
    if platform.system() != "Darwin":
        raise OCRUnavailable("Apple Vision requires macOS")
    compiler = shutil.which("swiftc")
    if not compiler:
        raise OCRUnavailable("swiftc is not installed")
    with tempfile.TemporaryDirectory(prefix="bench-labels-") as directory:
        work = Path(directory)
        binary = work / "labels"
        try:
            completed = subprocess.run(
                [compiler, str(Path(__file__).with_suffix(".swift")), "-o", str(binary),
                 "-module-cache-path", str(work / "modules")],
                capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OCRUnavailable("could not compile Vision helper") from exc
        if completed.returncode:
            # Compiler diagnostics can contain private host paths; don't expose them.
            raise OCRUnavailable("Vision helper compilation failed; check the macOS Swift SDK")
        yield binary


def recognize(binary: Path, image: Path, *, t: float,
              crop: tuple[int, int, int, int]) -> list[dict]:
    try:
        completed = subprocess.run([str(binary), str(image)], capture_output=True,
                                   text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OCRUnavailable("Vision helper failed or timed out") from exc
    if completed.returncode:
        raise OCRUnavailable("Vision text recognition failed")
    try:
        return parse_observations(completed.stdout, t=t, crop=crop)
    except (ValueError, TypeError) as exc:
        raise OCRUnavailable("Vision helper returned invalid data") from exc


def video_rows(source: str, binary: Path, *, sample_rate: float = 1,
               crop: tuple[int, int, int, int] | None = None, upscale: int = 1,
               time_origin: float = 0, start: float = 0, seconds: float | None = None):
    import cv2  # Non-Mac fallback needs only the Python standard library.

    cap = cv2.VideoCapture(source)
    try:
        if not cap.isOpened():
            raise ValueError("cannot open source video")
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("video has no usable frame rate")
        if sample_rate > fps:
            raise ValueError("sample rate must not exceed the video's frame rate")
        frame_index = 0
        sample_index = 0
        target = math.ceil(start * fps - 1e-9)
        with tempfile.TemporaryDirectory(prefix="bench-label-frames-") as directory:
            image_path = Path(directory) / "frame.png"
            while cap.grab():
                current = frame_index
                frame_index += 1
                if current < target:
                    continue
                t = current / fps
                if seconds is not None and t >= start + seconds:
                    break
                ok, frame = cap.retrieve()
                if not ok:
                    raise ValueError("cannot decode sampled video frame")
                height, width = frame.shape[:2]
                roi = crop or (0, 0, width, height)
                x, y, w, h = roi
                if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
                    raise ValueError("crop must fit within the video frame")
                patch = frame[y:y+h, x:x+w]
                if upscale != 1:
                    patch = cv2.resize(patch, (w * upscale, h * upscale), interpolation=cv2.INTER_CUBIC)
                if not cv2.imwrite(str(image_path), patch):
                    raise ValueError("cannot write temporary OCR image")
                rows = recognize(binary, image_path, t=time_origin + t, crop=roi)
                yield from rows or [{"t": time_origin + t, "text": "", "confidence": None,
                                    "box": None, "status": "no_text"}]
                sample_index += 1
                target = math.ceil((start + sample_index / sample_rate) * fps - 1e-9)
    finally:
        cap.release()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="video file path")
    parser.add_argument("--sample-rate", type=positive_float, default=1, help="samples/second (default 1)")
    parser.add_argument("--crop", type=parse_crop, help="x,y,width,height in original pixels")
    parser.add_argument("--upscale", type=int, choices=(1, 2), default=1, help="bicubic crop enlargement")
    parser.add_argument("--time-origin", type=float, default=0, help="add to media seconds; use capture epoch for alignment")
    parser.add_argument("--start", type=float, default=0, help="first media second")
    parser.add_argument("--seconds", type=positive_float, help="maximum media duration to inspect")
    args = parser.parse_args(argv)
    if not math.isfinite(args.start) or args.start < 0 or not math.isfinite(args.time_origin):
        parser.error("start must be nonnegative and times must be finite")
    try:
        with native_helper() as binary:
            for row in video_rows(args.source, binary, sample_rate=args.sample_rate, crop=args.crop,
                                  upscale=args.upscale, time_origin=args.time_origin,
                                  start=args.start, seconds=args.seconds):
                print(json.dumps(row, ensure_ascii=False, allow_nan=False), flush=True)
    except OCRUnavailable as exc:
        print(json.dumps({"t": None, "text": None, "confidence": None, "box": None,
                          "status": "unavailable", "reason": str(exc)}), flush=True)
        return 2
    except (ValueError, ImportError) as exc:
        print(f"labels: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
