"""Replay cached detections, no models/network. Counts boxes, not unique objects.

    $BENCH_PY -m vision.eval.labelfilter --output vision/eval/labelfilter-results.json
    # Optional renderer caches (outside repo); BENCH_SAMPLES supplies CP protocols:
    $BENCH_PY -m vision.eval.labelfilter --render-dir "$BENCH_RENDER_OUT"
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
import json
import os
from pathlib import Path
import time

from ..core import Det, Hand
from ..labelfilter import LIMITS, LabelFilter, filter_analysis, objects
from ..pipeline import Vision
from .replay import frames, raw_boxes
from .score import judge

HERE = Path(__file__).parent
# Deliberately permits every detector class: labelled benchmark measures size and
# temporal filtering without credit for protocol exclusion. Not a real protocol.
ALL_CLASSES = "pipette, pipette tip, tip box, tube, tube rack, plate, waste"


def at(rows, t):
    index = bisect_right([row[0] for row in rows], t + 1e-6) - 1
    return rows[index][1] if index >= 0 else []


def count_boxes(rows, labels, clip):
    counts = {label: {"right": 0, "wrong": 0, "unknown": 0}
              for label in ("pipette", "pipette tip", "tip box", "waste")}
    found, possible = 0, 0
    for lab in labels:
        ds = at(rows, lab["t"])
        hit = False
        for d in ds:
            if d["label"] not in counts:
                continue
            verdict = judge(Det(d["label"], d["box"], d["score"]), lab, clip["scene"])
            counts[d["label"]]["unknown" if verdict is None else "right" if verdict else "wrong"] += 1
            if d["label"] in ("pipette", "pipette tip") and isinstance(lab["pipette"], list):
                from .score import centre, inside
                hit |= inside(centre(d["box"]), lab["pipette"], 30)
        if isinstance(lab["pipette"], list):
            possible += 1
            found += bool(hit)
    return {"boxes": counts, "pipette_frames": {"found": found, "labelled": possible}}


def labelled_benchmark():
    labels = json.loads((HERE / "labels.json").read_text())
    clips, elapsed, observations = {}, 0.0, 0
    for name, clip in labels["clips"].items():
        baseline = Vision(1)
        filt = LabelFilter(ALL_CLASSES)
        before, after = [], []
        events_before, events_after = [], []
        for fr in frames(clip["path"], clip["start"], clip["seconds"]):
            hs = [Hand(h["side"], h["pts"]) for h in fr["hands"]]
            raw = raw_boxes(fr, False)
            evs = baseline.step(0, fr["t"], fr["w"], fr["h"], hs, raw)
            if raw is not None:
                ds = [d for d in baseline.dets[0] if d.label != "hand"]
                before.append((fr["t"], objects(ds)))
                start = time.perf_counter()
                out = filt.update(fr["t"], ds, baseline.hands[0])
                elapsed += time.perf_counter() - start
                observations += 1
                after.append((fr["t"], out))
            events_before.extend(evs)
            events_after.extend(filt.filter_events(evs, fr["t"], baseline.hands[0]))
        labs = [lab for lab in labels["frames"] if lab["clip"] == name]
        clips[name] = {"split": clip["split"], "labelled_frames": len(labs),
                       "before": count_boxes(before, labs, clip), "after": count_boxes(after, labs, clip),
                       "tip_on_before": sum(e["kind"] == "tip_on" for e in events_before),
                       "tip_on_after": sum(e["kind"] == "tip_on" for e in events_after)}
    totals = {}
    for split in ("tune", "hold"):
        totals[split] = {}
        for mode in ("before", "after"):
            records = [v[mode] for v in clips.values() if v["split"] == split]
            counts = {label: {k: sum(r["boxes"][label][k] for r in records)
                              for k in ("right", "wrong", "unknown")} for label in records[0]["boxes"]}
            totals[split][mode] = {"boxes": counts,
                "right": sum(c["right"] for c in counts.values()),
                "wrong": sum(c["wrong"] for c in counts.values()),
                "pipette_frames": {k: sum(r["pipette_frames"][k] for r in records)
                                   for k in ("found", "labelled")}}
    return {"protocol": ALL_CLASSES, "clips": clips, "totals": totals,
            "filter_observations": observations, "filter_seconds": round(elapsed, 6),
            "filter_ms_per_observation": round(elapsed * 1000 / observations, 4)}


def render_benchmark(directory):
    out = {}
    samples = Path(os.environ["BENCH_SAMPLES"]) if os.environ.get("BENCH_SAMPLES") else None
    for path in sorted(directory.glob("*-work/analysis.json")):
        name = path.parent.name.removesuffix("-work")
        protocol_path = None
        if name.startswith("cp-") and samples:
            segment = name.removeprefix("cp-").removesuffix("-labelled")
            protocol_path = samples / "lsv_mv" / f"CP-{segment}-protocol.txt"
        elif name.startswith("pi-"):
            protocol_path = directory / "pi-protocol.txt"
        elif name.startswith("pcr-"):
            protocol_path = directory / "pcr-protocol.txt"
        if protocol_path is None or not protocol_path.is_file():
            out[name] = {"skipped": "matching protocol file unavailable"}
            continue
        protocol = protocol_path.read_text()
        analysis = json.loads(path.read_text())
        start = time.perf_counter()
        filtered = filter_analysis(analysis, protocol)
        duration = time.perf_counter() - start
        def tally(a):
            return {"labels": dict(Counter(d["label"] for rows in a["snaps"] for _, ds in rows for d in ds)),
                    "events": dict(Counter(e["kind"] for e in a["events"]))}
        out[name] = {"before": tally(analysis), "after": tally(filtered),
                     "filter_seconds": round(duration, 6),
                     "detector_observations": sum(len(rows) for rows in analysis["snaps"]),
                     "allowed_classes": sorted(LabelFilter(protocol).allowed)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--render-dir", type=Path)
    args = ap.parse_args(argv)
    result = {"limits": LIMITS, "labelled": labelled_benchmark(),
              "render": render_benchmark(args.render_dir) if args.render_dir else {}}
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"totals": result["labelled"]["totals"],
                      "filter_ms": result["labelled"]["filter_ms_per_observation"],
                      "render": result["render"]}, indent=2))


if __name__ == "__main__":
    main()
