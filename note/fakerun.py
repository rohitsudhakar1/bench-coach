"""Write a scripted run folder to try the note: python -m note.fakerun <folder> [--samples].

Events follow the demo rehearsal: tip on, a tilt during step 2, a reused tip into tube C,
a skipped dispense. Capture's timing.jsonl and two short camera files are written too. The
cameras run at 10 fps and camera 1 drops frames, so a wrong offset rule shows up in tests.
--samples links two real bench clips from $BENCH_SAMPLES instead of drawing frames.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import yaml

from .runfolder import REPO_ROOT

T0 = 1793400000.0
LENGTH = 60.0
FPS = 10.0
SAMPLE_CLIPS = ("lsv_mv/CP-Left-Front-414-445.mp4", "lsv_mv/CP-Right-Front-414-445.mp4")


def ev(t, source, kind, obj=None, step=None, conf=None, **detail):
    return {"t": T0 + t, "source": source, "kind": kind, "object": obj, "hand": None,
            "step": step, "confidence": conf, "detail": detail}


def script(run_id: str, protocol: dict) -> list[dict]:
    steps = protocol["steps"]

    def step(t, i, status, evidence=None, source=None):
        d = {"index": i, "status": status, "text": steps[i]["text"], "evidence": evidence}
        if source:
            d["source"] = source
        e = ev(t, "coach", "step", step=i)
        e["detail"] = d
        return e

    out = [ev(0.0, "app", "run", status="start", run=run_id, protocol="protocols/pipetting-demo.yaml",
              protocol_data=protocol),
           step(0.0, 0, "current"),
           ev(2.0, "coach", "speech", text="Hi, this is Maya Lopez running the dye test."),
           ev(5.0, "vision", "hand_touch", "pipette", conf=0.8, cam=0),
           ev(6.0, "vision", "tip_on", "pipette", conf=0.9),
           step(6.2, 0, "done", "vision", "seen"), step(6.2, 1, "current"),
           ev(9.0, "angle", "angle", conf=0.7, deg=8.0, ok=True, limit=20),
           ev(12.0, "angle", "angle", conf=0.7, deg=27.4, ok=False, limit=20),
           ev(12.1, "coach", "warning", step=1, text="Pipette tilted 27 degrees. Keep it under 20 degrees."),
           ev(13.0, "angle", "angle", conf=0.7, deg=24.0, ok=False, limit=20),
           ev(14.0, "vision", "draw", "tube A", conf=0.8, cam=0),
           ev(14.5, "coach", "speech", text="Drawing 100 microlitres of water from tube A."),
           step(14.6, 1, "done", "vision", "seen"), step(14.6, 2, "current"),
           ev(20.0, "coach", "speech", text="What's next?"),
           ev(24.0, "vision", "dispense", "tube B", conf=0.6, cam=0, hint=True),
           ev(25.0, "coach", "speech", text="yes, done"),
           step(25.2, 2, "done", "hint+speech", "seen+said"), step(25.2, 3, "current"),
           ev(37.0, "vision", "draw", "tube C", conf=0.8, cam=1),
           ev(37.0, "coach", "warning", text="Same tip used twice. Change it before tube C."),
           step(37.1, 3, "skipped"), step(37.1, 4, "skipped"),
           ev(37.1, "coach", "warning", step=3, text="You skipped 2 steps. Check steps 4 to 5."),
           step(37.1, 5, "done", "vision", "seen"), step(37.1, 6, "current"),
           ev(41.0, "angle", "angle", conf=0.7, deg=11.0, ok=True, limit=20),
           ev(44.0, "coach", "speech", text="The dye looks a bit cloudy, lot 4471B."),
           ev(50.0, "vision", "tip_off", "pipette", conf=0.9),
           step(50.1, 6, "skipped"),
           ev(50.1, "coach", "warning", step=6, text="You skipped step 7: dispense it into tube B."),
           step(50.1, 7, "done", "vision", "seen"),
           ev(LENGTH, "app", "run", status="stop", run=run_id, protocol="protocols/pipetting-demo.yaml"),
           # after the stop: must not reach the note
           ev(LENGTH + 30, "vision", "draw", "smoke-tube", conf=0.5, cam=0)]
    return out


def write_video(path: Path, times: list[float], color) -> None:
    import cv2
    import numpy as np
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (320, 240))
    for i, t in enumerate(times):
        img = np.full((240, 320, 3), color, dtype=np.uint8)
        cv2.putText(img, f"{t - T0:5.1f}s", (30, 130), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
        cv2.rectangle(img, (0, 230), (int(320 * i / max(1, len(times) - 1)), 240), (255, 255, 255), -1)
        w.write(img)
    w.release()


def cam_times(cam: int) -> list[float]:
    n = int(LENGTH * FPS) + 1
    times = [T0 + 0.3 + i / FPS for i in range(n)]
    if cam == 1:  # camera 1 starts 0.5 s later and loses frames 100-149 (5 s)
        times = [t + 0.5 for i, t in enumerate(times) if not 100 <= i < 150]
    return times


def make(folder: str | os.PathLike, samples: bool = False, run_id: str | None = None) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    run_id = run_id or folder.name
    protocol = yaml.safe_load((REPO_ROOT / "protocols" / "pipetting-demo.yaml").read_text())
    with open(folder / "events.jsonl", "w") as fh:
        for e in script(run_id, protocol):
            fh.write(json.dumps(e) + "\n")
    rows = [{"kind": "start", "t": T0 + 0.3, "fps": [FPS, FPS], "audio_rate": 16000}]
    per_cam = {0: cam_times(0), 1: cam_times(1)}
    if samples:
        import cv2
        base = Path(os.environ["BENCH_SAMPLES"])
        for cam, rel in enumerate(SAMPLE_CLIPS):
            dst = folder / f"cam{cam}.mp4"
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            dst.symlink_to(base / rel)
            cap = cv2.VideoCapture(str(dst))
            fps, n = cap.get(cv2.CAP_PROP_FPS) or 30.0, int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()
            # The clips are about 31 s, so play them at half speed over the 60 s run.
            per_cam[cam] = [T0 + 0.3 + i * (LENGTH / n) for i in range(n)]
            rows[0]["fps"][cam] = fps
    else:
        write_video(folder / "cam0.mp4", per_cam[0], (90, 60, 30))
        write_video(folder / "cam1.mp4", per_cam[1], (30, 70, 90))
    for cam, times in per_cam.items():
        rows += [{"kind": "frame", "cam": cam, "frame": i, "t": t, "sequence": i, "media_time": t - T0}
                 for i, t in enumerate(times)]
    rows.sort(key=lambda r: r["t"])
    rows.append({"kind": "stop", "t": T0 + LENGTH + 0.2, "dropped": 50, "error": None})
    with open(folder / "timing.jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return folder


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--samples", action="store_true", help="link two real clips from $BENCH_SAMPLES")
    a = ap.parse_args()
    print(make(a.folder, samples=a.samples))
