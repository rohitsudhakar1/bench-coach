"""Replay the six CP clip caches through opt-in stops; no model or API calls.

$BENCH_PY -m vision.eval.stops --output vision/eval/stops-results.json
Use --sheets vision/eval/out/stops to write video-frame contact sheets for review.
Counts describe inferred dispense events, not ground-truth liquid transfers.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path

from ..core import sample
from .replay import frames, replay

SEGMENTS = ("12-27", "249-269", "414-445")


def measure(paths, new_draw):
    streams = [frames(p) for p in paths]
    if not all(streams):
        raise ValueError("missing model cache")
    counts = [Counter() for _ in paths]
    presses = {}
    last_vis = None

    def observe(cam, frame, vis):
        nonlocal last_vis
        last_vis = vis
        tracker = vis.stops.trackers[cam]
        counts[cam]["frames"] += 1
        counts[cam]["landmarked_frames"] += bool(frame["hands"])
        counts[cam]["usable_thumb_frames"] += bool(tracker.smooth)
        for p in tracker.presses:
            presses[(cam, p.start)] = {"cam": cam, **asdict(p)}

    events = replay(streams, new_draw=new_draw, stops=True, on_frame=observe)
    for cam, tracker in enumerate(last_vis.stops.trackers):
        for p in tracker.presses:
            presses[(cam, p.start)] = {"cam": cam, **asdict(p)}
    base = replay(streams, new_draw=new_draw)
    stripped = [{**e, "detail": {k: v for k, v in e["detail"].items() if k != "stop"}} for e in events]
    # Emission order may change, but no action, timestamp or old field may change.
    key = lambda e: json.dumps(e, sort_keys=True)
    assert sorted(base, key=key) == sorted(stripped, key=key)
    dispenses = [e for e in events if e["kind"] == "dispense"]
    labels = Counter(e["detail"]["stop"] for e in dispenses)
    return {"clips": paths, "mode": "new-draw" if new_draw else "legacy",
            "coverage": [dict(c) for c in counts], "dispenses": len(dispenses),
            "labels": {s: labels[s] for s in ("first", "second", "unknown")},
            "events_unchanged_except_stop_and_delivery_order": True,
            "presses": list(presses.values()), "dispense_events": dispenses}


def sheets(result, folder):
    import cv2
    import numpy as np
    folder.mkdir(parents=True, exist_ok=True)
    for i, e in enumerate(result["dispense_events"]):
        for cam in e["detail"].get("cams", [e["detail"]["cam"]]):
            path = sample(result["clips"][cam])
            cap = cv2.VideoCapture(str(path))
            start = max(0, e["detail"].get("start", e["t"]) - 0.4)
            end = min(cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS) - 0.05,
                      e["detail"].get("end", e["t"]) + 0.6)
            tiles = []
            for t in np.linspace(start, end, 12):
                cap.set(cv2.CAP_PROP_POS_MSEC, float(t * 1000))
                ok, frame = cap.read()
                if not ok:
                    continue
                frame = cv2.resize(frame, (640, round(frame.shape[0] * 640 / frame.shape[1])))
                cv2.putText(frame, f"{t:.2f}s", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
                tiles.append(frame)
            cap.release()
            if not tiles:
                continue
            while len(tiles) % 3:
                tiles.append(np.zeros_like(tiles[0]))
            sheet = cv2.vconcat([cv2.hconcat(tiles[j:j + 3]) for j in range(0, len(tiles), 3)])
            name = f"{Path(path).stem}-{result['mode']}-{i}-{e['t']:.2f}.jpg"
            cv2.imwrite(str(folder / name), sheet)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sheets", type=Path)
    args = parser.parse_args()
    results, skipped = [], []
    for segment in SEGMENTS:
        paths = [f"lsv_mv/CP-{side}-Front-{segment}.mp4" for side in ("Left", "Right")]
        if not all(sample(p) for p in paths):
            skipped.append(segment)
            continue
        for mode in (False, True):
            for group in ([paths[0]], [paths[1]], paths):
                result = measure(group, mode)
                results.append(result)
                if args.sheets and len(group) == 2:
                    sheets(result, args.sheets)
    output = {"method": "existing 10 Hz MediaPipe/Grounding DINO cache replay; no new inference",
              "accuracy": None, "manual_stop_ground_truth": "not supplied by this script",
              "skipped_missing_samples": skipped, "results": results}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2) + "\n")
    for r in results:
        print(f"{','.join(Path(p).stem for p in r['clips'])} {r['mode']}: "
              f"{r['dispenses']} dispenses, {r['labels']}, "
              f"{sum(c['usable_thumb_frames'] for c in r['coverage'])}/"
              f"{sum(c['frames'] for c in r['coverage'])} usable frames")
    if skipped:
        print("Skipped missing samples:", ", ".join(skipped))


if __name__ == "__main__":
    main()
