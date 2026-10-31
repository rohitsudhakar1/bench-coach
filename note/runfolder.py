"""Load one run folder: events, protocol, recordings and capture's frame timing.

A run folder is runs/<run>/ with events.jsonl (the hub), cam0.mp4, cam1.mp4, mic.wav and
timing.jsonl (capture). See docs/EVENTS.md.
"""
from __future__ import annotations

import bisect
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = Path(os.environ.get("BENCH_RUNS", REPO_ROOT / "runs"))
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")

# Events this long before the run start or after the stop still count (clock jitter between parts).
EDGE = 2.0


@dataclass
class Video:
    name: str                 # how the note links to it (file name inside the run folder)
    path: Path
    cam: int | None
    start: float | None       # epoch time of the first frame
    fps: float = 30.0
    frames: int = 0
    frame_times: list[float] = field(default_factory=list)   # epoch time of frame i, in file order

    @property
    def duration(self) -> float:
        return self.frames / self.fps if self.fps and self.frames else 0.0

    def offset(self, t: float) -> float | None:
        """Seconds into this file for epoch time t, or None when the file has no timing."""
        if self.frame_times:
            ft = self.frame_times
            i = bisect.bisect_left(ft, t)
            if i >= len(ft):
                i = len(ft) - 1
            elif i > 0 and t - ft[i - 1] < ft[i] - t:
                i -= 1
            off = i / self.fps if self.fps else 0.0
        elif self.start is not None:
            off = t - self.start
        else:
            return None
        if self.duration:
            off = min(off, max(0.0, self.duration - 1.0 / (self.fps or 30.0)))
        return round(max(0.0, off), 2)


@dataclass
class Run:
    run_id: str
    folder: Path
    events: list[dict]        # only this run's events, in time order
    protocol: dict
    protocol_file: str | None
    videos: list[Video]
    start: float | None
    stop: float | None
    timing_done: bool = False  # capture wrote its stop row


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # half-written last line while another part is still writing
            if isinstance(row, dict):
                rows.append(row)
    return rows


def read_events(path: Path) -> list[dict]:
    out = []
    for ev in read_jsonl(path):
        if not isinstance(ev.get("t"), (int, float)):
            continue
        if not isinstance(ev.get("detail"), dict):
            ev["detail"] = {}
        out.append(ev)
    out.sort(key=lambda e: e["t"])
    return out


def run_window(events: list[dict], run_id: str | None) -> tuple[list[dict], str | None, dict | None, float | None, float | None]:
    """Pick this run's start and stop events and keep only what falls between them.
    The hub's log can hold more than one run, and parts keep sending after a stop."""
    starts = [e for e in events if e.get("kind") == "run" and e["detail"].get("status") == "start"]
    if run_id:
        mine = [e for e in starts if e["detail"].get("run") == run_id]
        starts = mine or starts
    if not starts:
        return events, run_id, None, (events[0]["t"] if events else None), (events[-1]["t"] if events else None)
    start_ev = starts[-1]
    rid = start_ev["detail"].get("run") or run_id
    t0 = start_ev["t"]
    stop_t = None
    for e in events:
        if e["t"] < t0 or e.get("kind") != "run":
            continue
        d = e["detail"]
        if d.get("status") == "stop" and (d.get("run") in (None, rid)):
            stop_t = e["t"]
            break
        if d.get("status") == "start" and e is not start_ev and d.get("run") != rid:
            stop_t = e["t"]  # a new run began without a stop
            break
    hi = (stop_t + EDGE) if stop_t is not None else float("inf")
    kept = [e for e in events if t0 - EDGE <= e["t"] <= hi and e.get("kind") != "note"]
    return kept, rid, start_ev, t0, (stop_t if stop_t is not None else (kept[-1]["t"] if kept else t0))


def find_protocol(folder: Path, start_ev: dict | None) -> tuple[dict, str | None]:
    d = (start_ev or {}).get("detail") or {}
    name = d.get("protocol")
    data = d.get("protocol_data")
    if isinstance(data, dict) and isinstance(data.get("steps"), list):
        return data, name
    candidates = []
    if name:
        p = Path(name)
        candidates += [folder / p, REPO_ROOT / p, REPO_ROOT / "protocols" / p.name]
        if p.is_absolute():
            candidates.insert(0, p)
    candidates += [folder / "protocol.yaml", folder / "protocol.yml"]
    for c in candidates:
        if c.is_file():
            with open(c, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            if isinstance(loaded, dict):
                return loaded, name or c.name
    return {}, name


def load_timing(folder: Path) -> tuple[dict[int, list[float]], dict[int, float], bool]:
    """capture's timing.jsonl -> ({cam: frame times in frame order}, {cam: fps}, stop row seen)."""
    p = folder / "timing.jsonl"
    if not p.is_file():
        return {}, {}, False
    frames: dict[int, dict[int, float]] = {}
    fps: dict[int, float] = {}
    done = False
    for row in read_jsonl(p):
        kind = row.get("kind")
        if kind == "frame":
            cam, t = row.get("cam"), row.get("t")
            if isinstance(cam, int) and isinstance(t, (int, float)):
                per = frames.setdefault(cam, {})
                n = row.get("frame")
                per[int(n) if isinstance(n, int) else len(per)] = float(t)
        elif kind == "start":
            for i, f in enumerate(row.get("fps") or []):
                if isinstance(f, (int, float)) and f > 0:
                    fps[i] = float(f)
        elif kind == "stop":
            done = True
    return {c: [rows[k] for k in sorted(rows)] for c, rows in frames.items() if rows}, fps, done


def video_info(path: Path) -> tuple[float, int]:
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            return fps, n
        finally:
            cap.release()
    except Exception:
        return 0.0, 0


def _cam_of(path: Path) -> int | None:
    stem = path.stem.lower()
    if stem.startswith("cam") and stem[3:].isdigit():
        return int(stem[3:])
    return None


def load_run(folder: str | os.PathLike, sources: list[str] | None = None,
             events_file: str | None = None) -> Run:
    """sources: extra video files (for a loose folder without capture's recordings).
    They are taken to start at the run start and are numbered after any cam files."""
    folder = Path(folder)
    ev_path = Path(events_file) if events_file else folder / "events.jsonl"
    events = read_events(ev_path) if ev_path.is_file() else []
    events, rid, start_ev, start, stop = run_window(events, folder.name)
    protocol, proto_name = find_protocol(folder, start_ev)

    frame_times, fps_hint, done = load_timing(folder)
    paths = sorted((p for p in folder.glob("*") if p.suffix.lower() in VIDEO_EXT), key=lambda p: p.name) \
        if folder.is_dir() else []
    videos = []
    for p in paths + [Path(s) for s in (sources or [])]:
        cam = _cam_of(p) if p.parent == folder else None
        fps, n = video_info(p)
        ft = frame_times.get(cam, []) if cam is not None else []
        fps = fps or fps_hint.get(cam, 0.0) or 30.0
        inside = p.parent.resolve() == folder.resolve()
        videos.append(Video(name=p.name if inside else str(p.resolve()), path=p, cam=cam,
                            start=ft[0] if ft else start, fps=fps, frames=n or len(ft), frame_times=ft))
    return Run(run_id=rid or folder.name, folder=folder, events=events, protocol=protocol,
               protocol_file=proto_name, videos=videos, start=start, stop=stop, timing_done=done)
