"""Times into each recording, and key frames for the moments that went wrong."""
from __future__ import annotations

from pathlib import Path

from .runfolder import Run

PRIORITY = {"angle": 0, "tip": 1, "skipped": 2, "warning": 3, "unsure": 4, "stopped": 5}
FRAME_DIR = "note_frames"
HEIGHT = 360


def attach_offsets(note: dict, run: Run) -> None:
    """Every timed line gets `at` (seconds since the run start) and `offsets` ({file: seconds})."""
    for key in ("steps", "technique", "said", "problems"):
        for item in note[key]:
            t = item.get("t")
            if t is None:
                item["at"], item["offsets"] = None, {}
                continue
            item["at"] = round(t - run.start, 2) if run.start is not None else None
            offs = {}
            for v in run.videos:
                off = v.offset(t)
                if off is not None:
                    offs[v.name] = off
            item["offsets"] = offs


def grab(path: Path, sec: float):
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        idx = int(round(sec * fps))
        if n:
            idx = min(idx, n - 1)
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, idx))
        ok, frame = cap.read()
        if not ok and n:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, n - 1))
            ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def save_key_frames(note: dict, run: Run, out_dir: Path, limit: int = 3) -> int:
    """Up to `limit` problems, most serious first and at least 2 s apart, get one still per
    camera: note_frames/problem-<n>-<video stem>.jpg. Stored on the problem as `frames`."""
    for p in note["problems"]:
        p["frames"] = []
    if not run.videos:
        return 0
    try:
        import cv2
    except ImportError:
        return 0
    timed = [p for p in note["problems"] if p.get("t") is not None and p["kind"] in PRIORITY]
    timed.sort(key=lambda p: (PRIORITY[p["kind"]], p["t"]))
    chosen: list[dict] = []
    for p in timed:
        if all(abs(p["t"] - q["t"]) >= 2 for q in chosen):
            chosen.append(p)
        if len(chosen) >= limit:
            break
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("problem-*.jpg"):
        old.unlink()
    saved = 0
    for p in chosen:
        n = note["problems"].index(p) + 1
        for v in run.videos:
            off = p["offsets"].get(v.name)
            if off is None:
                continue
            img = grab(v.path, off)
            if img is None:
                continue
            img = cv2.resize(img, (max(1, int(img.shape[1] * HEIGHT / img.shape[0])), HEIGHT))
            name = f"problem-{n}-{Path(v.name).stem}.jpg"
            cv2.imwrite(str(out_dir / name), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            p["frames"].append({"file": f"{out_dir.name}/{name}", "video": v.name, "offset": off})
            saved += 1
    return saved
