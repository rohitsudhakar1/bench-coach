#!/usr/bin/env python3
"""Measure how well each part of Bench Coach works and write SCORECARD.md at the repo root.

    $BENCH_PY scripts/scorecard.py                  # about half a minute
    $BENCH_PY scripts/scorecard.py --json out.json  # the raw numbers too
    $BENCH_PY scripts/scorecard.py --coach DIR      # score a coach checkout that is not merged yet

It reads only data kept in this repo, plus the sample videos and protocol texts in
$BENCH_SAMPLES when that is set. It runs the parts' own code on that data and never changes how a
part behaves. Nothing is played out loud, no server is started on a port, no key is needed.

Every row says what kind of data it rests on:
- held-out: real footage whose labels were not used to tune the part
- tuning: real footage the part was tuned on (flatters the part; shown for comparison)
- real: real footage with no split (nothing was tuned on it, but it is small)
- scripted: events or answers written by hand by the team
- synthetic: generated pictures or numbers

The coach rows fill in by themselves once coach/ is in the repo (merged to main).
"""
from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEMO = ROOT / "protocols" / "pipetting-demo.yaml"
ACTIONS = ROOT / "vision" / "eval" / "actions.json"
LABELS = ROOT / "vision" / "eval" / "labels.json"

# The written protocol that goes with the 414-445 clips (CP-414-445-protocol.txt), as evidence.
# Vision names things "bottle" or "tube", never the trypsin bottle or flask, so no objects.
CP414 = {"name": "CP 414-445", "steps": [
    {"text": "Attach a 1000 uL tip.", "expect": [{"kind": "tip_on"}]},
    {"text": "Collect the 1st 750 uL of trypsin.", "expect": [{"kind": "draw"}]},
    {"text": "Dispense into the flask.", "expect": [{"kind": "dispense"}]},
    {"text": "Collect the 2nd 750 uL of trypsin.", "expect": [{"kind": "draw"}]},
]}

# One reader's key (the scorecard builder's) for protocol texts in $BENCH_SAMPLES: per step, the
# pipetting actions a camera could see. An empty set means a camera cannot check that step.
CONVERT_KEY = {
    "lsv_mv/CP-12-27-protocol.txt": [set()],
    "lsv_mv/CP-249-269-protocol.txt": [{"tip_on"}, set()],
    "lsv_mv/CP-414-445-protocol.txt": [{"tip_on"}, {"draw"}, {"dispense"}, {"draw"}],
    "lsv_dji/Serial_Dilution_XM.txt": [set()] + [{"draw", "dispense"}] * 5,
    "lsv_dji/PCR_Reaction_Setup_XM.txt": [{"draw", "dispense"}] * 4 + [set(), set()],
    "lsv_dji/Lentivirus_infection_XM.txt": [{"draw", "dispense"}] * 2 + [set(), set()],
}

TOL = 1.5          # an action matches when the event is in [start - 1.5 s, end + 1.5 s]
REPLY_AFTER = 2.0  # simulated person answers a hint question this long after it is asked


# ------------------------------------------------------------------ helpers

def samples() -> Path | None:
    base = os.environ.get("BENCH_SAMPLES")
    return Path(base) if base and Path(base).is_dir() else None


def r2(x):
    return "n/a" if x is None else f"{x:.2f}"


def pct(a, b):
    return "n/a" if not b else f"{100 * a / b:.0f}%"


def row(part, data, amount, number, meaning, missing, chance="-"):
    return {"part": part, "data": data, "amount": amount, "number": number, "chance": chance,
            "meaning": meaning, "missing": missing}


def failed(part, err):
    return row(part, "-", "-", "could not run", f"The scorecard hit an error: {err}.",
               "Nothing measured in this run.")


def load_actions():
    return json.loads(ACTIONS.read_text())


def action_streams(labels, split):
    from vision.eval.score_actions import streams
    return streams(labels, split)


def vision_events(labels, names, new_draw):
    """Replay the cached model output through the live vision code. Returns events and duration."""
    from vision.eval.replay import frames, replay
    fs = [frames(labels["clips"][n]["path"], 0.0, labels["clips"][n]["seconds"]) for n in names]
    evs = [e for e in replay(fs, new_draw) if e["kind"] != "objects"]
    return evs, labels["clips"][names[0]]["seconds"]


def near_action(t, acts, kind):
    return any(a["kind"] == kind and a["start"] - TOL <= t <= a["end"] + TOL for a in acts)


# ------------------------------------------------------------------ vision

def vision_frames():
    from vision.eval import score as vs
    labels = json.loads(LABELS.read_text())
    out = {}
    for split in ("hold", "tune"):
        r = vs.score(labels, {}, split)
        clips = sorted(n for n, c in labels["clips"].items() if c["split"] == split)
        r["frames"] = sum(1 for f in labels["frames"] if labels["clips"][f["clip"]]["split"] == split)
        r["clips"] = clips
        out[split] = r
    return out


def vision_frame_rows(v):
    rows = []
    for split, data in (("hold", "held-out"), ("tune", "tuning")):
        r = v[split]
        amount = f"{r['frames']} labelled frames from {len(r['clips'])} clips, {r['seconds']:.0f} s"
        p = r["pipette_found"]
        rows.append(row("Vision: finding the pipette", data, amount, f"{p[0]} of {p[1]} ({pct(*p)})",
                        f"On {pct(*p)} of the frames where a pipette is labelled, the part's pipette box sat on it.",
                        "One person labelled the frames. Not tried on the demo bench or its lighting."))
        b = r["boxes"]
        wrong = sum(x["wrong"] for x in b.values())
        total = sum(x["wrong"] + x["right"] for x in b.values())
        rows.append(row("Vision: object boxes", data, amount,
                        f"{wrong} of {total} boxes on the wrong thing ({r2(r['wrong_box_rate'])})",
                        "Share of pipette, tip, tip box and waste boxes that were not on that thing. "
                        + ", ".join(f"{k} {x['wrong']} of {x['wrong'] + x['right']} wrong" for k, x in b.items()) + ".",
                        "These clips have no waste bin, so every waste box is wrong. Tube, rack and plate boxes are not scored."))
        ev = r["events"]
        parts = []
        chance = []
        for k in ("hand_touch", "tip_on", "tip_off"):
            e = ev[k]
            parts.append(f"{k} fired {e['fired']}, right {e['right']} of {e['scored']}, found {e['found']} of {e['runs']}")
            chance.append(f"{k} {r2(e['chance_precision'])}")
        rows.append(row("Vision: touches, tip on, tip off", data, amount, "; ".join(parts),
                        "Right: a labelled frame within 0.8 s shows that thing. Found: labelled moments that got an event.",
                        "Only a handful of real touches and tip changes are labelled, so each count is a few events.",
                        "precision by chance: " + ", ".join(chance)))
    return rows


def vision_tips():
    from vision.eval import score_tips as vt
    labels = json.loads((vt.HERE / "tips.json").read_text())
    out = {}
    for split in ("hold", "tune"):
        res, _ = vt.score(labels, {}, split)
        names = [n for n, c in labels["clips"].items() if c["split"] == split]
        out[split] = {"res": res, "clips": len(names),
                      "seconds": sum(labels["clips"][n]["seconds"] for n in names)}
    return out


def vision_tip_rows(v):
    rows = []
    names = {"tip_on": "tip pick-ups", "tip_off": "tip ejects", "hand_touch": "hand touches"}
    for split, data in (("hold", "held-out"), ("tune", "tuning")):
        s = v[split]
        res = s["res"]
        amount = (f"{s['clips']} clips, {s['seconds']:.0f} s; " + ", ".join(
            f"{res[k]['labelled']} {names[k]}" for k in names) + " labelled")
        parts, chance = [], []
        for k in names:
            x = res[k]
            parts.append(f"{k} precision {r2(x['precision'])} ({x['right']} of {x['fired']}), "
                         f"recall {r2(x['recall'])} ({x['found']} of {x['labelled']})")
            chance.append(f"{k} {r2(x['chance_precision'])} / {r2(x['chance_recall'])}")
        rows.append(row("Vision: tip on, tip off, touches against labelled moments", data, amount, "; ".join(parts),
                        "An event counts when a labelled moment of that kind is within 1 s; each moment once. "
                        "Touches are only scored on clips where touches were labelled.",
                        "One person labelled from contact sheets at 0.5 s steps. Most tip ejects in these clips "
                        "happen out of frame, so their times are guesses. tip_on uses the steady tip box rule; "
                        "touches and tip_off are the older rules.",
                        "precision / recall by chance: " + ", ".join(chance)))
    return rows


def vision_actions():
    from vision.eval import score_actions as va
    labels = load_actions()
    out = {}
    for split in ("hold", "tune"):
        res = va.score(labels, split, {})
        st = va.streams(labels, split)
        out[split] = {"res": res, "streams": [s for s, _ in st],
                      "seconds": sum(labels["clips"][n[0]]["seconds"] for _, n in st),
                      "actions": sum(1 for a in labels["actions"] if a["kind"] in va.KINDS
                                     and labels["clips"][a["clip"]]["split"] == split)}
    return out


def vision_action_rows(v):
    rows = []
    for mode, name in (("default", "default rule"), ("new-draw", "--new-draw hints")):
        for split, data in (("hold", "held-out"), ("tune", "tuning")):
            s = v[split]
            amount = f"{len(s['streams'])} clip streams, {s['seconds']:.0f} s, {s['actions']} labelled draws and dispenses"
            for kind in ("draw", "dispense"):
                x = s["res"][mode][kind]
                rows.append(row(
                    f"Vision: {kind}, {name}", data, amount,
                    f"precision {r2(x['precision'])} ({x['right']} of {x['fired']}); recall {r2(x['recall'])} ({x['right']} of {x['labelled']})",
                    ("Precision: share of events that match a labelled action. Recall: share of labelled actions found."
                     + (" These are hints and must never tick a step." if mode == "new-draw" else "")),
                    ("Says nothing about volume, liquid moved or which tube."
                     + (" The earlier draft looked at the held-out scores when choosing which filters to keep, so treat them as optimistic."
                        if mode == "new-draw" and split == "hold" else "")),
                    f"precision {r2(x['chance_precision'])}, recall {r2(x['chance_recall'])}"))
    return rows


# --- table mode on made-up overhead frames

TABLE_CAPS = {"tube A": (100, 60, (0, 0, 255)), "tube B": (220, 60, (255, 0, 0)), "tube C": (340, 60, (0, 255, 255))}
TABLE_TIP, TABLE_WASTE = (80, 280), (400, 280)


def table_plan(rng):
    """What the person means to do: a list of (kind, object) and the places to visit."""
    plan, visits = [], []
    tubes = list(TABLE_CAPS)
    for _ in range(rng.randint(1, 3)):
        a, b = rng.sample(tubes, 2)
        plan += [("tip_on", "tip box"), ("draw", a), ("dispense", b)]
        visits += ["tip box", a, b]
        for _ in range(rng.choice([0, 0, 1, 2])):
            plan.append(("mix", b))
            visits.append(b)
        plan.append(("tip_off", "waste"))
        visits.append("waste")
    return plan, visits


def table_frames(visits, rng, drop=0.05, jitter=3.0, fps=10):
    import cv2
    where = {n: (x, y) for n, (x, y, _) in TABLE_CAPS.items()}
    where["tip box"], where["waste"] = TABLE_TIP, TABLE_WASTE
    rest = (240, 200)
    pts = [rest] * 3
    pos = rest
    for name in visits:
        goal = where[name]
        speed = rng.uniform(15, 45)     # pixels per frame
        steps = max(1, int(math.dist(pos, goal) / speed))
        pts += [(pos[0] + (goal[0] - pos[0]) * i / steps, pos[1] + (goal[1] - pos[1]) * i / steps)
                for i in range(1, steps + 1)]
        pts += [goal] * rng.randint(4, 8)
        # step just out of the region (an overhead camera barely sees the lift), then go on
        out = (goal[0] + rng.uniform(-15, 15), goal[1] + rng.choice([-1, 1]) * rng.uniform(32, 45))
        pts += [out] * rng.randint(2, 4)
        pos = out
    pts += [rest] * 3
    out = []
    for i, p in enumerate(pts):
        img = np.full((360, 480, 3), 90, np.uint8)
        for x, y, c in TABLE_CAPS.values():
            cv2.circle(img, (x, y), 14, c, -1)
        if rng.random() >= drop:
            q = (int(p[0] + rng.gauss(0, jitter)), int(p[1] + rng.gauss(0, jitter)))
            cv2.circle(img, q, 6, (255, 0, 255), -1)
        out.append((i / fps, img))
    return out


def table_mode(n=40):
    from vision.table import TableTracker, marker
    rng = random.Random(5)
    regions = {k: [x, y, 25] for k, (x, y, _) in TABLE_CAPS.items()}
    regions["tip box"], regions["waste"] = [*TABLE_TIP, 25], [*TABLE_WASTE, 25]
    exact, right, fired, planned, frames_total = 0, 0, 0, 0, 0
    chance_right = 0
    for _ in range(n):
        plan, visits = table_plan(rng)
        trk = TableTracker(regions)
        got = []
        frames = table_frames(visits, rng)
        frames_total += len(frames)
        for t, img in frames:
            for e in trk.update(t, marker(img)):
                kind = "mix" if e["detail"].get("action") == "mix" else e["kind"]
                got.append((kind, e["object"]))
        exact += got == plan
        sm = difflib.SequenceMatcher(a=plan, b=got, autojunk=False)
        hit = sum(b.size for b in sm.get_matching_blocks())
        right += hit
        fired += len(got)
        planned += len(plan)
        # chance: the same number of events with random kinds and places
        pool = sorted(set(plan) | {("draw", t) for t in TABLE_CAPS} | {("dispense", t) for t in TABLE_CAPS})
        fake = [rng.choice(pool) for _ in got]
        chance_right += sum(b.size for b in difflib.SequenceMatcher(a=plan, b=fake, autojunk=False).get_matching_blocks())
    return {"runs": n, "exact": exact, "right": right, "fired": fired, "planned": planned,
            "frames": frames_total, "chance_precision": chance_right / fired if fired else None}


def table_rows(t):
    return [row("Vision: table mode (overhead camera, coloured caps, magenta band)", "synthetic",
                f"{t['runs']} made-up overhead runs, {t['frames']} drawn frames, {t['planned']} planned actions; "
                "band jittered 3 px, 5% of frames lose it, it steps just outside each place and moves in straight lines, sometimes over other tubes",
                f"{t['exact']} of {t['runs']} runs exactly right; precision {r2(t['right'] / t['fired'] if t['fired'] else None)} "
                f"({t['right']} of {t['fired']}); recall {r2(t['right'] / t['planned'])} ({t['right']} of {t['planned']})",
                "Events matched to the planned tip on, draw, dispense, mix and tip off in order.",
                "Drawn pictures only. Never run on a real bench, camera, light or a real band. "
                "It knows where the band went, not that liquid moved.",
                f"precision {r2(t['chance_precision'])} (random events, same count)")]


# ------------------------------------------------------------------ angle

def angle_scores():
    import cv2
    from angle.detection import detect_axis
    from angle.evaluate import synthetic_cases
    from angle.geometry import image_angle
    base = ROOT / "angle" / "samples"
    items = json.loads((base / "labels.json").read_text())["samples"]
    truth, errors, missing = [], [], 0
    for it in items:
        img = cv2.imread(str(base / it["file"]))
        ends = np.array(it["axis"])
        lo, hi = ends.min(axis=0) - it["padding"], ends.max(axis=0) + it["padding"]
        found = detect_axis(img, [*lo.tolist(), *(hi - lo).tolist()])
        t = image_angle(ends)
        truth.append(t)
        if found is None:
            missing += 1
        else:
            errors.append(abs(image_angle(found.points) - t))
    # baseline: guess the average of the other stills' angles every time
    guess = [abs(t - np.mean([u for j, u in enumerate(truth) if j != i])) for i, t in enumerate(truth)]
    return {"stills": len(items), "found": len(errors), "missing": missing,
            "mae": float(np.mean(errors)), "max": float(np.max(errors)),
            "within2": sum(e <= 2 for e in errors), "guess_mae": float(np.mean(guess)),
            "clean": synthetic_cases(0.0), "noisy": synthetic_cases(0.5)}


def angle_rows(a):
    n, c = a["noisy"], a["clean"]
    return [
        row("Angle: shaft lean in one still", "tuning",
            f"{a['stills']} hand-labelled stills from the 414-445 and 249-269 clips",
            f"{a['found']} of {a['stills']} measured; mean error {a['mae']:.1f} deg, worst {a['max']:.1f} deg; "
            f"{a['within2']} within 2 deg",
            f"Where it found the shaft it was off by {a['mae']:.1f} deg on average. It gave no answer on {a['missing']}.",
            "Lean in the picture, not the real 3D tilt. The search box is drawn around each shaft by hand, "
            "and the method was tuned on these same stills.",
            f"guessing the average angle every time: off by {a['guess_mae']:.1f} deg"),
        row("Angle: 3D tilt from two cameras", "synthetic",
            f"{n['attempted']} made-up pipettes seen by two made-up cameras, 0.5 px noise on the shaft ends",
            f"{n['accepted']} of {n['attempted']} answered; mean error {n['mae_deg']:.2f} deg, 95% under "
            f"{n['p95_deg']:.2f} deg, worst {n['max_deg']:.2f} deg",
            f"With no noise the maths is exact ({c['accepted']} answered). About 1 in 5 poses is refused as badly placed.",
            "Perfect camera parameters and correct shaft lines are assumed. No real calibration or pencil check has been done.",
            "guessing 45 deg every time: off by about 22.5 deg"),
    ]


# ------------------------------------------------------------------ capture

def capture_check(seconds=3.0):
    base = samples()
    srcs = [base / f"lsv_mv/CP-{s}-Front-414-445.mp4" for s in ("Left", "Right")] if base else []
    if not srcs or not all(p.is_file() for p in srcs):
        return None
    import cv2
    import wave
    from capture.service import Capture
    tmp = Path(tempfile.mkdtemp(prefix="scorecard-capture-"))
    cap = Capture([str(p) for p in srcs], tmp, fake_mic=True, loop=True)
    cap.start()
    try:
        until = time.time() + 10
        while not all(c.latest for c in cap.cameras) and time.time() < until:
            time.sleep(0.05)
        cap.recorder.start("score")
        t0 = time.time()
        time.sleep(seconds)
        result = cap.recorder.stop()
        wall = time.time() - t0
    finally:
        cap.close()
    folder = tmp / "score"
    rows_ = [json.loads(line) for line in (folder / "timing.jsonl").read_text().splitlines() if line.strip()]
    fps = []
    out = {"wall": wall, "error": (result or {}).get("error")}
    frames = {}
    for i in (0, 1):
        fr = [r for r in rows_ if r["kind"] == "frame" and r["cam"] == i]
        v = cv2.VideoCapture(str(folder / f"cam{i}.mp4"))
        decoded = int(v.get(cv2.CAP_PROP_FRAME_COUNT))
        fps.append(v.get(cv2.CAP_PROP_FPS))
        v.release()
        frames[i] = fr
        out[f"cam{i}"] = {"rows": len(fr), "decoded": decoded, "fps": fps[-1]}
    # at each camera-0 frame, the camera-1 frame nearest in clock time: how far apart are the clip moments
    t1 = np.array([r["t"] for r in frames[1]])
    m1 = np.array([r["media_time"] for r in frames[1]])
    gaps, clock = [], []
    for r in frames[0]:
        j = int(np.argmin(np.abs(t1 - r["t"])))
        clock.append(abs(t1[j] - r["t"]))
        gaps.append(abs(m1[j] - r["media_time"]))
    out["media_gap_ms"] = (1000 * float(np.median(gaps)), 1000 * float(np.max(gaps)))
    out["clock_gap_ms"] = (1000 * float(np.median(clock)), 1000 * float(np.max(clock)))
    audio = [r for r in rows_ if r["kind"] == "audio"]
    with wave.open(str(folder / "mic.wav")) as w:
        out["audio"] = {"samples": w.getnframes(), "rows": sum(r["n"] for r in audio)}
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def capture_rows(c):
    if c is None:
        return [row("Capture: recording two files and a mic", "-", "-", "skipped",
                    "Set BENCH_SAMPLES to the sample video folder to measure it.", "Nothing measured in this run.")]
    a, b = c["cam0"], c["cam1"]
    expect = c["wall"] * a["fps"]
    return [row("Capture: recording two files and a mic", "real",
                f"the two 414-445 sample clips served as cameras for {c['wall']:.1f} s, fake silent mic",
                f"camera 0 {a['rows']} frames, camera 1 {b['rows']} (about {expect:.0f} expected); "
                f"timing rows match the saved video {'on both' if a['rows'] == a['decoded'] and b['rows'] == b['decoded'] else 'NOT on both'}; "
                f"same-moment gap between the two views median {c['media_gap_ms'][0]:.0f} ms, worst {c['media_gap_ms'][1]:.0f} ms; "
                f"audio {c['audio']['samples']} samples, rows say {c['audio']['rows']}",
                "Both recorded views stay close to the same moment of the clip, and the timing file describes the "
                "saved files frame for frame." + (f" Recorder error: {c['error']}." if c["error"] else ""),
                "Files, not real cameras: no USB, phone or Pi camera, no real mic, no exposure sync, no long recording.")]


# ------------------------------------------------------------------ app

def app_checks():
    from fastapi.testclient import TestClient
    from app.hub import create_app
    events = [json.loads(line) for line in (ROOT / "nvidia/samples/run-real/events.jsonl").read_text().splitlines()
              if line.strip()]
    events = [e for e in events if e["kind"] != "run"]
    tmp = Path(tempfile.mkdtemp(prefix="scorecard-app-"))
    app = create_app(tmp / "runs", "http://127.0.0.1:1", "http://127.0.0.1:1", ROOT / "protocols")
    got = []
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            t0 = time.perf_counter()
            for e in events:
                assert client.post("/events", json=e).status_code == 200
            per = (time.perf_counter() - t0) / len(events) * 1000
            while len(got) < len(events):
                got.append(ws.receive_json())
        hist = client.get("/events").json()
    shutil.rmtree(tmp, ignore_errors=True)
    same = sum(1 for a, b in zip(events, got) if a["kind"] == b["kind"] and abs(a["t"] - b["t"]) < 1e-6)
    return {"sent": len(events), "ws": len(got), "same": same, "history": len(hist), "ms": per}


def convert_check():
    base = samples()
    if not base:
        return None
    from protocols.convert import rule_protocol
    out = {"files": 0, "steps": 0, "checkable_right": 0, "kinds_right": 0, "checkable_steps": 0,
           "naive_kinds": 0, "naive_checkable": 0, "missing": []}
    for rel, key in CONVERT_KEY.items():
        p = base / rel
        if not p.is_file():
            out["missing"].append(rel)
            continue
        proto = rule_protocol(p.read_text(), p.stem)
        steps = proto["steps"] if isinstance(proto, dict) else [s.model_dump() for s in proto.steps]
        out["files"] += 1
        for i, want in enumerate(key):
            out["steps"] += 1
            got = {e["kind"] for e in (steps[i]["expect"] if i < len(steps) else [])}
            out["checkable_right"] += bool(got) == bool(want)
            out["naive_checkable"] += bool(want)       # baseline: every step is camera-checkable
            if want:
                out["checkable_steps"] += 1
                out["kinds_right"] += bool(got) and got <= want
                out["naive_kinds"] += {"draw", "dispense"} <= want or want <= {"draw", "dispense"}
        if len(steps) != len(key):
            out.setdefault("count_off", []).append(rel)
    return out


def app_rows(h, c):
    rows = [row("App: event hub", "scripted",
                f"{h['sent']} events from the earlier draft's rehearsal log, posted one at a time, in process",
                f"{h['ws']} of {h['sent']} reached a websocket listener, {h['same']} in the same order; "
                f"{h['history']} in the history; {h['ms']:.1f} ms per post",
                "Every event posted came back out to a listener and in the history.",
                "In-process test client, no network, one listener, no browser. The page itself was not opened.")]
    if c is None:
        rows.append(row("App: protocol text to steps (no key)", "-", "-", "skipped",
                        "Set BENCH_SAMPLES to measure it.", "Nothing measured in this run."))
    else:
        rows.append(row(
            "App: protocol text to steps (no key)", "real",
            f"{c['files']} protocol texts, {c['steps']} steps, answer key written by the scorecard builder",
            f"camera-checkable or not right on {c['checkable_right']} of {c['steps']} steps; "
            f"right kind of action on {c['kinds_right']} of {c['checkable_steps']} checkable steps",
            "Whether the converter knows a camera can check a step, and if so, which pipetting action to look for.",
            "One reader's key on six short texts. Tube names, volumes and timings are not scored. "
            "The Claude converter (with a key) was not run." + (f" Missing texts: {', '.join(c['missing'])}." if c["missing"] else ""),
            f"calling every step a draw or dispense: {c['naive_checkable']} of {c['steps']} on checkable-or-not"))
    return rows


# ------------------------------------------------------------------ note

def note_checks():
    from note import fakerun
    from note.build import build_note, steps_from_evidence
    from note.runfolder import load_run
    tmp = Path(tempfile.mkdtemp(prefix="scorecard-note-"))
    out = {}
    folder = fakerun.make(tmp / "fake")
    t0 = time.perf_counter()
    note = build_note(load_run(folder))
    out["seconds"] = time.perf_counter() - t0
    truth = ["done", "done", "done", "skipped", "skipped", "done", "skipped", "done"]
    got = [s["status"] for s in note["steps"]]
    out["coach"] = (sum(a == b for a, b in zip(got, truth)), len(truth))
    kinds = {p["kind"] for p in note["problems"]}
    out["problems"] = sorted(kinds)
    out["after_stop"] = any("smoke" in json.dumps(s) for s in note["steps"])
    # the note's own evidence walk, on real vision events from the 414-445 pair
    labels = load_actions()
    evs, _ = vision_events(labels, ["mv414L", "mv414R"], False)
    walked = steps_from_evidence(CP414["steps"], evs)
    out["real"] = {"done": sum(1 for v in walked.values() if v["status"] == "done"), "steps": len(CP414["steps"]),
                   "kinds": [e["kind"] for e in evs]}
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def note_rows(n):
    r = n["real"]
    return [
        row("Note: steps and problems from coach events", "scripted",
            "1 scripted 60 s run (note/fakerun.py): 8 steps, a tilt, a reused tip, 3 skipped steps, an event after stop",
            f"{n['coach'][0]} of {n['coach'][1]} step results match the script; problems listed: {', '.join(n['problems'])}; "
            f"event after stop {'leaked in' if n['after_stop'] else 'left out'}; built in {1000 * n['seconds']:.0f} ms",
            "The note copies what the coach decided and lists the tilt, the reused tip and the skips.",
            "Scripted by the note builder. The note is only as right as the coach's step events. "
            "The Claude wording pass has not run against the real API."),
        row("Note: walking the protocol itself on real vision events", "real",
            "vision's events on the 414-445 pair (31 s, both views), that clip's 4-step protocol",
            f"{r['done']} of {r['steps']} steps marked done (all 4 really happened)",
            f"With no coach events the note checks the protocol against vision's events ({', '.join(r['kinds']) or 'none'}).",
            "One short clip. The default vision rule fires no draw or dispense here, so the draw steps cannot be seen."),
    ]


# ------------------------------------------------------------------ nvidia

def nvidia_checks():
    from nvidia.export_tasks import export_run
    from nvidia.runner import score
    tmp = Path(tempfile.mkdtemp(prefix="scorecard-nvidia-"))
    out = {"tasks": 0}
    for run in ("run-real", "run-fake"):
        out["tasks"] += len(export_run(ROOT / "nvidia/samples" / run, tmp / run))

    def constant(task):
        return {"skipped_step": "none", "tip_reuse": "no"}.get(task["kind"], "")
    for name, how in (("reference", "reference"), ("blank", "blank"), ("rules", "rules"), ("constant", constant)):
        out[name] = score(tmp, how)
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def nvidia_rows(v):
    kinds = ", ".join(f"{k} {x['tasks']}" for k, x in v["rules"]["by_kind"].items())
    return [row("NVIDIA: run logs to verifier tasks", "scripted",
                f"2 sample runs (the earlier draft's rehearsal, scripted vision; and the note's scripted run), {v['tasks']} tasks ({kinds})",
                f"reference answers {v['reference']['mean']:.2f}, blank {v['blank']['mean']:.2f}, "
                f"offline rules solver {v['rules']['mean']:.2f}",
                "The checker gives full marks to the logged answer and none to a blank one. The rules solver, "
                "which sees only the protocol and events, gets the score shown.",
                "Two runs of one protocol. The answer key is what Bench Coach logged, so a vision miss makes the key wrong too. "
                "The rules solver was written by the same builder. No Harbor, Docker or model run.",
                f"always 'none' / 'no': {v['constant']['mean']:.2f}")]


# ------------------------------------------------------------------ coach

def find_coach(extra: str | None):
    """The coach part, if it is in the repo (or a checkout given with --coach)."""
    where = Path(extra) if extra else ROOT
    if not (where / "coach" / "brain.py").is_file():
        return None
    if extra:
        sys.path.insert(0, str(where))
    from coach import brain, protocol
    return {"brain": brain, "protocol": protocol, "merged": extra is None}


class Session:
    """Drive the coach's Brain with events in time order, collecting what it posts and says."""

    def __init__(self, coach, proto, hints=False, t0=0.0):
        self.now = t0
        self.b = coach["brain"].Brain(coach["protocol"].from_data(proto), hints=hints, clock=lambda: self.now)
        self.warnings, self.says, self.questions = [], [], []
        self._take(self.b.start())

    def _take(self, outs):
        for o in outs or []:
            if o.kind == "emit" and o.event and o.event.get("kind") == "warning":
                self.warnings.append(o.event["detail"].get("text", ""))
            elif o.kind == "say":
                self.says.append(o.text)
        q = self.b.question
        if q is not None and (not self.questions or self.questions[-1]["t"] != q["t"]):
            self.questions.append(dict(q))

    def event(self, ev):
        self.now = ev["t"]
        self._take(self.b.handle(ev))

    def hear(self, text, t):
        self.now = t
        self._take(self.b.hear(text, t=t))

    def tick(self, t):
        self.now = t
        self._take(self.b.tick(t))

    def status(self):
        return list(self.b.status)


def ev(t, kind, obj=None, conf=0.9, source="vision", **detail):
    return {"t": t, "source": source, "kind": kind, "object": obj, "hand": "right", "step": None,
            "confidence": conf, "detail": detail}


def scripted_sessions(coach):
    import yaml
    proto = yaml.safe_load(DEMO.read_text())
    res = {}
    # mistakes: tilt on step 2, same tip into tube C (steps 4 and 5 never happen), step 7 skipped
    s = Session(coach, proto)
    for e in [ev(5, "tip_on"), ev(8, "angle", source="angle", deg=27.0, ok=False, limit=20),
              ev(10, "draw", "tube A"), ev(16, "dispense", "tube B"), ev(25, "draw", "tube C"),
              ev(35, "tip_off")]:
        s.event(e)
    s.tick(40)
    truth = ["done", "done", "done", "skipped", "skipped", "done", "skipped", "done"]
    w = " ".join(s.warnings).lower()
    res["mistakes"] = {"right": sum(a == b for a, b in zip(s.status(), truth)), "steps": len(truth),
                       "tilt": "tilt" in w, "tip": "tip" in w and ("same" in w or "twice" in w or "fresh" in w),
                       "skip": "skip" in w,
                       "praise": any("all steps done" in x.lower() for x in s.says)}
    # clean: one event per step, nothing wrong
    s = Session(coach, proto)
    t = 0.0
    for step in proto["steps"]:
        t += 6
        e = step["expect"][0]
        s.event(ev(t, e["kind"], e.get("object")))
        if step.get("technique"):
            s.event(ev(t + 0.5, "angle", source="angle", deg=8.0, ok=True, limit=20))
    s.tick(t + 5)
    res["clean"] = {"done": s.status().count("done"), "steps": len(proto["steps"]), "warnings": len(s.warnings),
                    "last": s.says[-1] if s.says else ""}
    return res


def coach_real(coach):
    labels = load_actions()
    evs, _ = vision_events(labels, ["mv414L", "mv414R"], False)
    s = Session(coach, CP414)
    for e in evs:
        s.event({**e, "confidence": e.get("confidence")})
    return {"done": s.status().count("done"), "steps": len(CP414["steps"]), "kinds": [e["kind"] for e in evs],
            "warnings": len(s.warnings)}


def hint_replay(coach, labels, names, evs, dur, seed=None):
    """Hint mode with a simulated person who only answers questions (yes if a matching action was
    really happening when asked, else no). Returns questions, real ones, labelled steps, ticked."""
    acts = [a for a in labels["actions"] if a["clip"] == names[0] and a["kind"] in ("draw", "dispense")]
    steps = [{"text": f"{a['kind']} {a.get('container', '')}".strip(), "expect": [{"kind": a["kind"]}]} for a in acts]
    proto = {"name": names[0], "steps": steps or [{"text": "draw", "expect": [{"kind": "draw"}]}]}
    evs = [dict(e) for e in evs]
    if seed is not None:
        rng = random.Random(seed)
        for e in evs:
            if (e.get("detail") or {}).get("hint"):
                e["t"] = rng.uniform(0, dur)
    evs.sort(key=lambda e: e["t"])
    s = Session(coach, proto, hints=True)
    pending = []
    answered = 0

    def flush(upto):
        nonlocal answered
        while answered < len(s.questions) and s.questions[answered]["t"] + REPLY_AFTER <= upto:
            q = s.questions[answered]
            answered += 1
            t = q["t"] + REPLY_AFTER
            s.tick(t)
            if s.b.question is not None and s.b.question["t"] == q["t"]:
                s.hear("yes" if near_action(q["t"], acts, q.get("kind")) else "no", t)
    for e in evs:
        flush(e["t"])
        s.event(e)
    flush(dur + 60)
    s.tick(dur + 60)
    real = sum(1 for q in s.questions if near_action(q["t"], acts, q.get("kind")))
    return {"questions": len(s.questions), "real": real, "steps": len(steps),
            "ticked": s.status().count("done") if steps else 0, "minutes": dur / 60}


def hint_scores(coach, tries=50):
    labels = load_actions()
    out = {}
    for split in ("hold", "tune"):
        streams = action_streams(labels, split)
        cached = {n: vision_events(labels, names, True) for n, names in streams}
        tot = {"questions": 0, "real": 0, "steps": 0, "ticked": 0, "minutes": 0.0}
        for n, names in streams:
            r = hint_replay(coach, labels, names, *cached[n])
            for k in tot:
                tot[k] += r[k]
        cq = cr = 0
        for seed in range(tries):
            for n, names in streams:
                r = hint_replay(coach, labels, names, *cached[n], seed=seed * 101 + 7)
                cq += r["questions"]
                cr += r["real"]
        tot["chance_real"] = cr / cq if cq else None
        tot["streams"] = len(streams)
        out[split] = tot
    return out


def coach_rows(coach, err=None):
    tag = "" if coach is None or coach["merged"] else " (unmerged checkout)"
    if coach is None or err:
        why = err or "coach/ is not in this repo yet. These rows fill in on their own once it is merged to main."
        return [row(f"Coach: {name}", "-", "-", "waiting for coach/" if not err else "could not run", why,
                    "Nothing measured in this run.")
                for name in ("step tracking, scripted mistakes", "clean scripted run", "real vision events",
                             "hint mode, held-out", "hint mode, tuning")]
    s = scripted_sessions(coach)
    m, c = s["mistakes"], s["clean"]
    rows = [
        row("Coach: step tracking, scripted mistakes" + tag, "scripted",
            "1 scripted session on the demo protocol: tilt, same tip into tube C, steps 4, 5 and 7 never done",
            f"{m['right']} of {m['steps']} step results right; warned on tilt {'yes' if m['tilt'] else 'no'}, "
            f"reused tip {'yes' if m['tip'] else 'no'}, skipped step {'yes' if m['skip'] else 'no'}; "
            f"said 'all steps done' {'yes (wrong)' if m['praise'] else 'no'}",
            "Given clean scripted events at confidence 0.9, does it mark each step right and warn on the three mistakes.",
            "Events written by hand, written by the scorecard builder who knows the protocol."),
        row("Coach: clean scripted run" + tag, "scripted",
            "1 scripted session, one event per step, angles under the limit",
            f"{c['done']} of {c['steps']} done, {c['warnings']} warnings; last line: \"{c['last']}\"",
            "A perfect run should tick every step with no false warning.",
            "Scripted. Missing or weak events and out-of-order noise are not scored here."),
    ]
    r = coach_real(coach)
    rows.append(row("Coach: real vision events" + tag, "real",
                    "vision's default-rule events on the 414-445 pair (31 s, both views), that clip's 4-step protocol",
                    f"{r['done']} of {r['steps']} steps done (all 4 really happened); {r['warnings']} warnings",
                    f"Vision sent {', '.join(r['kinds']) or 'nothing'}; the coach can only tick what vision sees.",
                    "One short clip. The vision rule here sends no draw or dispense at all."))
    h = hint_scores(coach)
    for split, data in (("hold", "held-out"), ("tune", "tuning")):
        x = h[split]
        rows.append(row(
            f"Coach: hint mode, person only answers{tag}", data,
            f"{x['streams']} clip streams, {x['minutes']:.1f} min, {x['steps']} labelled steps; --new-draw hints replayed, "
            f"replies simulated {REPLY_AFTER:.0f} s later",
            f"{x['questions']} questions ({x['questions'] / x['minutes']:.1f} a minute); about a real action "
            f"{x['real']} of {x['questions']} ({pct(x['real'], x['questions'])}); ticked {x['ticked']} of {x['steps']} steps",
            "How often the coach asks 'did you just draw?', how often that was right, and how many steps a yes ticked.",
            "Simulated replies, no live speech, no person. A person who also says 'done' would tick more; that is not scored.",
            f"about a real action {r2(x['chance_real'])} (hints at random times, 50 tries)"))
    return rows


# ------------------------------------------------------------------ output

COLS = ("Part", "Data", "Measured on", "Number", "Chance", "What it means", "Not measured")


def cell(s):
    return str(s).replace("|", "/").replace("\n", " ")


def render(rows, seconds, coach):
    stamp = time.strftime("%Y-%m-%d %H:%M %Z")
    head = [
        "# Bench Coach scorecard", "",
        f"Written by `scripts/scorecard.py` on {stamp} in {seconds:.0f} s. Every number comes from data in this repo"
        " (and the sample videos in `$BENCH_SAMPLES`) run through the parts' own code.", "",
        "The Data column says what each number rests on. **held-out**: real footage nobody tuned on. "
        "**tuning**: real footage the part was tuned on, so it flatters the part; it is there to compare. "
        "**real**: real footage with no split. **scripted**: events written by hand. **synthetic**: made-up "
        "pictures or numbers. The Chance column is what you get from the same events at random times, or from "
        "the simple guess named there.", "",
    ]
    if coach is None:
        head += ["The coach rows are waiting for `coach/` to be merged; rerun this script after that.", ""]
    head += ["| " + " | ".join(COLS) + " |", "| " + " | ".join("---" for _ in COLS) + " |"]
    for r in rows:
        head.append("| " + " | ".join(cell(r[k]) for k in
                                       ("part", "data", "amount", "number", "chance", "meaning", "missing")) + " |")
    head += ["", "## From the part reports (not rerun by this script)", "",
             "These come from the October 30 and 31 part reports, quoted as written. This script does not rerun "
             "them, so check the linked report before quoting one.", "",
             "| Check | Data | Number | Chance | Report |",
             "| --- | --- | --- | --- | --- |",
             "| Detectors on the labelled frames, 64 frames from 4 clips | held-out | Grounding DINO tiny (default) "
             "finds the pipette on 38 of 58, 0.65 of boxes wrong, tip box right 0.26. Florence-2 base finds 42 of 58, "
             "0.45 wrong (0.28 after dropping unsure waste and tip box boxes), tip box right 0.67 | - | "
             "[detectors](vision/DETECTOR2-REPORT.md), [Florence-2](vision/FLORENCE-REPORT.md) |",
             "| Draws and dispenses found with Florence-2, `--new-draw` | held-out | draws found 7 of 18 (tiny 15 of 18); "
             "dispenses found 4 of 22 (tiny 10 of 22) | draws 0.36, dispenses 0.26 (tiny's chance 0.43 and 0.32) | "
             "[Florence-2](vision/FLORENCE-REPORT.md) |",
             "| Detector speed on the Mac's GPU, both cameras sharing one detector, after batching | real | tiny 4.04 "
             "runs a second per camera (3.69 before). Florence-2 2.15 in float32, 2.76 in float16 with its accuracy "
             "unchecked. The target is 3 | - | [speed](vision/SPEED-REPORT.md) |",
             "| Small trained pipette model (Faster R-CNN MobileNetV3), not adopted | held-out | pipette found by the "
             "live code 30 of 58 (tiny 38 of 58); 17 ms a frame against 178 ms | - | "
             "[tip model](vision/TIPMODEL-REPORT.md) |",
             "| Table mode under 15 lighting and placement conditions, 60 rendered runs | synthetic | before 142 of "
             "360 events found, 20 of 60 runs exact; after 360 of 360, 60 of 60 exact. The same fixtures were used "
             "while making the changes | - | [table robustness](vision/TABLEROBUST-REPORT.md) |",
             "| Spoken questions, 3 recordings at 2 speeds over bench noise at 3 levels | tuning | 12 of 18 right "
             "before, 18 of 18 after | - | [voice](coach/VOICEROBUST-REPORT.md) |",
             "| Spoken questions, made with 8 computer voices | synthetic | before 81 of 99 "
             "right, after 90 of 107. Scoring stopped after about 100 clips, almost all one voice "
             "(Samantha), so other voices are unmeasured | - | [voice](coach/VOICEROBUST-REPORT.md) |",
             "| Every launcher mode end to end (scripted, real vision, hints, table, three benches, replay) | "
             "scripted and real video | 6 of 6 modes started, ran, stopped and served their pages and downloads; "
             "3 problems found and fixed | - | [sweep](SWEEP-REPORT.md) |",
             "| Three benches at once on one hub, scripted vision | scripted | each bench reached 8 step outcomes "
             "and its event log held only its own bench id | - | [classroom](app/CLASSROOM-REPORT.md) |",
             "",
             "The detector and trained-model studies changed no default, so the main table still uses Grounding DINO "
             "tiny. The table and voice changes are in the default code now.", ""]
    head += ["## The weakest part", "",
             "Seeing lab actions in real video. The default draw and dispense rule rarely fires (recall about 0.1). "
             "The --new-draw hints find most draws, but about half of them are false, and dispense hints are only a "
             "little better than chance. That is why hints never tick a step on their own. Touch and tip events on "
             "the held-out frames are close to useless, and vision never names a specific tube outside table mode, "
             "which has only been tried on drawn pictures.", "",
             "## Not in the table", "",
             "- Speech, beyond the two report rows above. This script does not load Whisper. No real person has "
             "been scored speaking to a real microphone at a bench.",
             "- The whole demo end to end with every part running, and how fast events reach the page live.",
             "- Anything with a key: the Claude check on hints, the Claude note wording, the Claude protocol converter, "
             "Gemini Live, Nemotron. Every row here is the no-key behaviour.",
             "- Real cameras, a real bench, real calibration, and any real user.", "",
             "## How the numbers are made", "",
             "Action events match a labelled action of the same kind within 1.5 s of its start and end, each action once; "
             "events during labelled mixing count neither way. The two 414-445 views run as one two-camera stream. "
             "Frame events count as right when a labelled frame within 0.8 s shows them. Chance moves the same events to "
             "random times in the clip (200 tries for vision, 50 for hint questions). These are small samples, not "
             "estimates of live accuracy.", "",
             "## How to rerun", "",
             "```",
             "BENCH_SAMPLES=<sample video folder> $BENCH_PY scripts/scorecard.py",
             "```", ""]
    return "\n".join(head)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(ROOT / "SCORECARD.md"))
    ap.add_argument("--json", help="also write the raw numbers here")
    ap.add_argument("--coach", help="score the coach part from this checkout instead of the repo's own")
    ap.add_argument("--skip", nargs="*", default=[], help="parts to skip, e.g. capture")
    a = ap.parse_args(argv)
    os.environ.pop("ANTHROPIC_API_KEY", None)   # the scorecard measures the no-key behaviour only
    t0 = time.time()
    rows, raw = [], {}
    coach = None
    try:
        coach = find_coach(a.coach)
    except Exception as e:  # coach present but not importable
        coach_err = f"coach/ is there but could not be imported ({type(e).__name__}: {e})"
    else:
        coach_err = None

    jobs = [
        ("capture", lambda: capture_rows(raw.setdefault("capture", capture_check()))),
        ("vision", lambda: vision_frame_rows(raw.setdefault("vision_frames", vision_frames()))),
        ("vision", lambda: vision_tip_rows(raw.setdefault("vision_tips", vision_tips()))),
        ("vision", lambda: vision_action_rows(raw.setdefault("vision_actions", vision_actions()))),
        ("vision", lambda: table_rows(raw.setdefault("table", table_mode()))),
        ("angle", lambda: angle_rows(raw.setdefault("angle", angle_scores()))),
        ("coach", lambda: coach_rows(coach, coach_err)),
        ("note", lambda: note_rows(raw.setdefault("note", note_checks()))),
        ("app", lambda: app_rows(raw.setdefault("app", app_checks()), raw.setdefault("convert", convert_check()))),
        ("nvidia", lambda: nvidia_rows(raw.setdefault("nvidia", nvidia_checks()))),
    ]
    for part, job in jobs:
        if part in a.skip:
            continue
        t = time.time()
        try:
            rows += job()
        except Exception as e:
            traceback.print_exc()
            rows.append(failed(part, f"{type(e).__name__}: {e}"))
        print(f"{part}: {time.time() - t:.1f} s", file=sys.stderr)
    seconds = time.time() - t0
    Path(a.out).write_text(render(rows, seconds, coach))
    if a.json:
        Path(a.json).write_text(json.dumps({"rows": rows, "raw": raw}, indent=1, default=str))
    print(f"wrote {Path(a.out).name} in {seconds:.0f} s, {len(rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
