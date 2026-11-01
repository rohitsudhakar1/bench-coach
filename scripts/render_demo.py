"""Turn a recorded bench run into a finished 1080p demo video.

    python -m scripts.render_demo --video run.mp4 [--video2 cam2.mp4] --protocol protocol.txt --out out.mp4
    python -m scripts.render_demo --r2-latest --protocol protocol.txt --out out.mp4   (newest Pi session)

What it does, in four passes:
1. Vision (the same code the live demo runs: Grounding DINO tiny boxes, MediaPipe hands, the
   event rules) reads every camera and records boxes, finger points and events.
2. The coach's rules follow the protocol from those events: steps done, skipped or unsure,
   warnings, the lines it would say.
3. The note part builds the lab note from the same events.
4. The bench footage is drawn at full frame rate with every object outlined and named, both
   hands drawn, the protocol ticking off on the right, warnings as they happen, and the note at
   the end.

Pass 1 is cached next to the output (<out>-work/analysis.json); --reuse skips it on a re-render.
--labels FILE adds hand-labelled events (JSON lines: {"s": 12.5, "kind": "draw", "object": "tube A"});
anything they decide is marked "hand-labelled" on screen. Nothing makes sound.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import render_layout as L  # noqa: E402

OUT_FPS = 30
TITLE_SECONDS = 3.0
NOTE_SECONDS = 12.0

# colours, RGB
BG = (14, 18, 24)
PANEL = (21, 27, 35)
FEED = (27, 34, 44)
TEXT = (232, 237, 242)
MUTED = (134, 148, 163)
FAINT = (70, 82, 96)
GREEN = (63, 185, 127)
AMBER = (232, 168, 56)
RED = (232, 85, 75)
BLUE = (88, 166, 255)
OBJECT_COLOURS = {"pipette": (255, 196, 64), "pipette tip": (255, 140, 66), "tip box": (88, 166, 255),
                  "tube": (99, 214, 145), "tube rack": (160, 220, 90), "plate": (214, 120, 255),
                  "waste": (240, 110, 110), "hand": (200, 200, 200)}
HAND_COLOURS = {"left": (255, 170, 90), "right": (90, 205, 255)}
LINKS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]

FONT_FILES = [("/System/Library/Fonts/HelveticaNeue.ttc", {"regular": 0, "bold": 1, "medium": 10, "light": 7}),
              ("/Library/Fonts/Arial Unicode.ttf", {}),
              ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", {})]
_fonts: dict = {}


def font(size: int, weight: str = "regular"):
    key = (size, weight)
    if key not in _fonts:
        f = None
        for path, idx in FONT_FILES:
            if Path(path).is_file():
                try:
                    f = ImageFont.truetype(path, size, index=idx.get(weight, 0))
                    break
                except OSError:
                    continue
        _fonts[key] = f or ImageFont.load_default(size)
    return _fonts[key]


def bgr(rgb):
    return (rgb[2], rgb[1], rgb[0])


# ------------------------------------------------------------------ pass 1: vision ------------

def start_time(video) -> float:
    """When the recording began: the file's modification time minus its length (a phone or
    capture writes the file as it records). Used only for the date on the lab note."""
    cap = cv2.VideoCapture(str(video))
    length = cap.get(cv2.CAP_PROP_FRAME_COUNT) / (cap.get(cv2.CAP_PROP_FPS) or 30.0)
    cap.release()
    return round(Path(video).stat().st_mtime - length, 3)


def pretty_title(protocol: dict, path: Path) -> str:
    name = str(protocol.get("name") or "").strip()
    if not name or name == path.stem:
        name = path.stem.replace("_", " ").replace("-", " ")
        name = " ".join(w for w in name.split() if w.lower() != "protocol") or "Protocol"
    return name


def analyse(videos, fps, detect_every, detector_name, new_draw, seconds, log=print):
    from vision.detector import MotionGate, detect_batch
    from vision.hands import HandTracker
    from vision.pipeline import Vision
    from vision.sources import FileSource

    t0 = start_time(videos[0])
    sources = [FileSource(str(p), fps, 640, t0) for p in videos]
    n = len(sources)
    vis = Vision(n, new_draw)
    trackers = [HandTracker() for _ in range(n)]
    detect = None
    if detector_name != "none":
        from vision.zoo import make
        detect = make(detector_name)
        if new_draw:
            from vision.detector import WithContainers
            detect = WithContainers(detect, 1)
    motion = MotionGate()
    hands = [[] for _ in range(n)]
    snaps = [[] for _ in range(n)]
    events, last_detect, done = [], [-1e9] * n, [False] * n
    scale = [None] * n
    wall, frames = time.time(), 0
    while not all(done):
        batch = []
        for i, src in enumerate(sources):
            if done[i]:
                continue
            t, frame = src.read()
            if frame is None or (seconds and t - t0 > seconds):
                done[i] = True
                continue
            if scale[i] is None:
                scale[i] = (src.shape[1] / frame.shape[1], src.shape[0] / frame.shape[0])
            batch.append((i, t, frame))
        detected = {}
        if detect is not None:
            jobs = [(i, t, f) for i, t, f in batch
                    if t - last_detect[i] >= detect_every and motion.needs_detection(i, t, f)]
            if jobs:
                frames_ = [f for _, _, f in jobs]
                found = detect.batch(frames_, [i for i, _, _ in jobs]) if new_draw else detect_batch(detect, frames_)
                for (i, t, f), raw in zip(jobs, found):
                    last_detect[i] = t
                    motion.remember(i, t, f)
                    detected[i] = raw
        for i, t, frame in batch:
            h, w = frame.shape[:2]
            sx, sy = scale[i]
            raw = detected.get(i)
            evs = vis.step(i, t, w, h, trackers[i](frame, t), raw)
            s = round(t - t0, 3)
            hands[i].append((s, [{"side": hd.side,
                                  "pts": [(round(x * sx, 1), round(y * sy, 1)) for x, y in hd.pts] if hd.pts else None,
                                  "box": [round(v * (sx if k % 2 == 0 else sy), 1) for k, v in enumerate(hd.box)]
                                  if hd.box is not None and not hd.pts else None}
                                 for hd in vis.hands[i]]))
            if raw is not None:
                snaps[i].append((s, [{"label": d.label, "score": round(float(d.score), 3),
                                      "box": [round(v * (sx if k % 2 == 0 else sy), 1) for k, v in enumerate(d.box)]}
                                     for d in vis.dets[i] if d.label != "hand"]))
            for e in evs:
                if e["kind"] != "objects":
                    e["s"] = s
                    events.append(e)
            frames += 1
        if frames and frames % 200 < n:
            log(f"  vision: {frames} frames, {time.time() - wall:.0f}s")
    for e in vis.finish():
        e.setdefault("s", e["t"] - t0)
        events.append(e)
    for tr in trackers:
        tr.close()
    for s in sources:
        s.close()
    for e in events:
        e["t"] = t0 + e["s"]
    return {"t0": t0, "fps": fps, "detector": detector_name, "new_draw": new_draw,
            "hands": hands, "snaps": snaps, "events": events,
            "seconds_taken": round(time.time() - wall, 1)}


# ------------------------------------------------------------------ pass 2: coach -------------

def load_protocol(path: Path, use_llm: bool = False) -> dict:
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    from protocols.convert import convert_text
    data, _how = convert_text(path.read_text(), path.stem, use_llm=use_llm)
    return data


def labelled_events(path: Path, t0: float) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        s = float(row["s"])
        out.append({"t": t0 + s, "s": s, "source": "vision", "kind": row["kind"], "object": row.get("object"),
                    "hand": row.get("hand"), "step": None, "confidence": float(row.get("confidence", 0.9)),
                    "detail": {"cam": row.get("cam", 0), "labelled": True}})
    return out


def coach(protocol: dict, events: list[dict], t0: float, end_s: float, run_id: str) -> list[dict]:
    """Run the coach's rules over the events; returns the whole run's event log (run start and
    stop, the vision events, and the coach's step, warning and say events), each with "s"."""
    from coach.brain import Brain
    from coach.protocol import from_data
    now = [t0]
    brain = Brain(from_data(protocol), clock=lambda: now[0])
    start = {"t": t0, "s": 0.0, "source": "app", "kind": "run", "object": None, "hand": None, "step": None,
             "confidence": None, "detail": {"status": "start", "run": run_id, "protocol": protocol.get("name"),
                                            "protocol_data": protocol}}
    log = [start]

    def take(outs, labelled=False):
        for o in outs:
            if o.kind == "emit":
                e = dict(o.event)
            else:
                e = {"t": brain.now, "source": "coach", "kind": "say", "object": None, "hand": None,
                     "step": None, "confidence": None, "detail": {"text": o.text}}
                brain.said(o.text)
            e["detail"] = dict(e.get("detail") or {})
            if labelled and e["kind"] == "step" and e["detail"].get("status") in ("done", "unsure"):
                e["detail"]["labelled"] = True
            e["s"] = round(e["t"] - t0, 3)
            log.append(e)

    take(brain.handle(start))
    for e in sorted(events, key=lambda e: e["t"]):
        now[0] = e["t"]
        log.append(e)
        take(brain.handle(e), labelled=bool(e["detail"].get("labelled")))
    now[0] = t0 + end_s
    take(brain.tick(now[0]))
    log.append({"t": t0 + end_s, "s": end_s, "source": "app", "kind": "run", "object": None, "hand": None,
                "step": None, "confidence": None, "detail": {"status": "stop", "run": run_id}})
    return log


# ------------------------------------------------------------------ pass 3: note --------------

def build_note(work: Path, run_id: str, log: list[dict], videos: list[Path]) -> dict | None:
    from note.service import _make_note
    run_dir = work / run_id
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    with open(run_dir / "events.jsonl", "w") as fh:
        for e in log:
            fh.write(json.dumps({k: v for k, v in e.items() if k != "s"}) + "\n")
    for i, v in enumerate(videos):
        (run_dir / f"cam{i}.mp4").symlink_to(Path(v).resolve())
    try:
        return _make_note(run_dir, use_claude=None, frames=False)["note"]
    except Exception as exc:  # the video still renders without a note
        print(f"  note failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


# ------------------------------------------------------------------ pass 4: drawing -----------

class Sprites:
    """Text pills drawn once with PIL and alpha-blended onto frames."""

    def __init__(self):
        self.cache = {}

    def pill(self, text, colour, size=17):
        key = (text, colour, size)
        if key not in self.cache:
            f = font(size, "medium")
            l, t, r, b = f.getbbox(text)
            pad_x, pad_y = 8, 5
            w, h = r + pad_x * 2, (b - t) + pad_y * 2 + 2
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((0, 0, w - 1, h - 1), radius=6, fill=(*colour, 225))
            dark = sum(colour) > 420
            d.text((pad_x, pad_y - t + 1), text, font=f, fill=(15, 18, 22) if dark else (255, 255, 255))
            a = np.asarray(img)
            self.cache[key] = (a[..., 2::-1].astype(np.float32), a[..., 3:4].astype(np.float32) / 255.0)
        return self.cache[key]

    def blit(self, canvas, sprite, x, y, clip):
        rgb, alpha = sprite
        h, w = alpha.shape[:2]
        x = int(min(max(x, clip.x), clip.x2 - w))
        y = int(min(max(y, clip.y), clip.y2 - h))
        if x < 0 or y < 0 or x + w > canvas.shape[1] or y + h > canvas.shape[0]:
            return
        roi = canvas[y:y + h, x:x + w].astype(np.float32)
        canvas[y:y + h, x:x + w] = (roi * (1 - alpha) + rgb * alpha).astype(np.uint8)


def draw_objects(canvas, boxes, src_size, rect, sprites):
    for d in sorted(boxes, key=lambda d: -(d["box"][2] - d["box"][0]) * (d["box"][3] - d["box"][1])):
        c = OBJECT_COLOURS.get(d["label"], (200, 200, 200))
        x1, y1, x2, y2 = (int(round(v)) for v in L.scale_box(d["box"], *src_size, rect))
        cv2.rectangle(canvas, (x1, y1), (x2, y2), bgr(c), 2, cv2.LINE_AA)
        sp = sprites.pill(d["label"], c)
        sprites.blit(canvas, sp, x1, y1 - sp[1].shape[0] - 2, rect)


def draw_hands(canvas, hands, src_size, rect, sprites):
    sw, sh = src_size
    sx, sy = rect.w / sw, rect.h / sh
    for h in hands:
        c = HAND_COLOURS.get(h["side"], (220, 220, 220)) if h.get("pts") else (210, 210, 210)
        if h.get("pts"):
            pts = [(int(round(rect.x + x * sx)), int(round(rect.y + y * sy))) for x, y in h["pts"]]
            for a, b in LINKS:
                cv2.line(canvas, pts[a], pts[b], bgr(c), 2, cv2.LINE_AA)
            for p in pts:
                cv2.circle(canvas, p, 3, (255, 255, 255), -1, cv2.LINE_AA)
            anchor = pts[0]
        elif h.get("box"):
            x1, y1, x2, y2 = (int(round(v)) for v in L.scale_box(h["box"], sw, sh, rect))
            cv2.rectangle(canvas, (x1, y1), (x2, y2), bgr(c), 2, cv2.LINE_AA)
            anchor = ((x1 + x2) // 2, y2)
        else:
            continue
        sp = sprites.pill(f"{h['side']} hand" if h.get("pts") else "hand", c)
        sprites.blit(canvas, sp, anchor[0] - sp[1].shape[1] // 2, anchor[1] + 8, rect)


def to_bgr(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"))[..., ::-1].copy()


STATUS_WORD = {"done": "Done", "skipped": "Skipped", "unsure": "Not sure", "current": "Now", "pending": ""}
STATUS_COLOUR = {"done": GREEN, "skipped": RED, "unsure": AMBER, "current": BLUE, "pending": FAINT}


def status_icon(d: ImageDraw.ImageDraw, x, y, status, r=13):
    c = STATUS_COLOUR.get(status, FAINT)
    box = (x - r, y - r, x + r, y + r)
    if status == "done":
        d.ellipse(box, fill=c)
        d.line([(x - 6, y), (x - 2, y + 5), (x + 7, y - 5)], fill=(14, 18, 24), width=3)
    elif status == "skipped":
        d.ellipse(box, outline=c, width=3)
        d.line([(x - 5, y - 5), (x + 5, y + 5)], fill=c, width=3)
        d.line([(x - 5, y + 5), (x + 5, y - 5)], fill=c, width=3)
    elif status == "unsure":
        d.ellipse(box, outline=c, width=3)
        f = font(17, "bold")
        d.text((x, y + 1), "?", font=f, fill=c, anchor="mm")
    elif status == "current":
        d.ellipse(box, outline=c, width=3)
        d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=c)
    else:
        d.ellipse(box, outline=c, width=2)


def measure(f):
    return lambda s: f.getlength(s)


def draw_panel(state: L.PanelState, title: str, rect: L.Rect, credits: list[str]) -> np.ndarray:
    img = Image.new("RGB", (rect.w, rect.h), PANEL)
    d = ImageDraw.Draw(img)
    pad = 36
    width = rect.w - pad * 2
    d.text((pad, 34), "BENCH COACH", font=font(17, "bold"), fill=MUTED)
    d.text((rect.w - pad, 26), L.clock(state.elapsed), font=font(34, "light"), fill=TEXT, anchor="ra")
    y = 72
    for line in L.wrap(title, width - 10, measure(font(30, "bold")))[:2]:
        d.text((pad, y), line, font=font(30, "bold"), fill=TEXT)
        y += 38
    done = sum(s.status == "done" for s in state.steps)
    d.text((pad, y + 6), f"{done} of {len(state.steps)} steps done", font=font(19), fill=MUTED)
    y += 44
    d.line([(pad, y), (rect.w - pad, y)], fill=FAINT, width=1)
    y += 18

    # bottom area: warnings then credits, measured first so the steps know their space
    credit_f = font(15)
    credit_lines = [ln for c in credits for ln in L.wrap(c, width, measure(credit_f))]
    warn_f = font(19)
    warn_rows = []
    for at, text in state.warnings[-3:]:
        warn_rows.append((at, L.wrap(text, width - 70, measure(warn_f))[:2]))
    warn_h = (44 + sum(10 + 25 * len(r[1]) for r in warn_rows)) if warn_rows else 0
    bottom = rect.h - 26 - 21 * len(credit_lines) - warn_h - 20

    text_f, small_f = font(22), font(16)
    rows = []
    for st in state.steps:
        lines = L.wrap(st.text, width - 50, measure(text_f))[:3]
        meta = None
        if st.status in ("done", "skipped", "unsure") and st.at is not None:
            meta = f"{STATUS_WORD[st.status]} at {L.clock(st.at)}" + (f" · {st.how}" if st.how else "")
        rows.append((st, lines, meta, 28 * len(lines) + (24 if meta else 0) + 22))
    # keep the current step in view: drop finished rows from the top when the list is too long
    cur = next((i for i, (st, *_ ) in enumerate(rows) if st.status == "current"), len(rows) - 1)
    first = 0
    while first < cur and y + 30 + sum(r[3] for r in rows[first:cur + 2]) > bottom:
        first += 1
    if first:
        d.text((pad, y), f"{first} earlier step{'s' if first > 1 else ''} above", font=small_f, fill=MUTED)
        y += 30
    for st, lines, meta, h in rows[first:]:
        if y + h > bottom:
            left = len(state.steps) - st.number + 1
            d.text((pad, y), f"{left} more step{'s' if left > 1 else ''}", font=small_f, fill=MUTED)
            break
        if st.status == "current":
            d.rounded_rectangle((pad - 14, y - 10, rect.w - pad + 14, y + h - 12), radius=10, fill=(30, 40, 54))
        status_icon(d, pad + 13, y + 14, st.status)
        colour = MUTED if st.status == "pending" else TEXT
        for k, line in enumerate(lines):
            d.text((pad + 44, y + 28 * k), line, font=text_f, fill=colour)
        if meta:
            mc = STATUS_COLOUR[st.status] if st.how != "hand-labelled" else AMBER
            d.text((pad + 44, y + 28 * len(lines) + 2), meta, font=small_f, fill=mc)
        y += h

    yb = rect.h - 26 - 21 * len(credit_lines) - warn_h
    if warn_rows:
        d.line([(pad, yb - 4), (rect.w - pad, yb - 4)], fill=FAINT, width=1)
        d.text((pad, yb + 10), f"WARNINGS  {len(state.warnings)}", font=font(16, "bold"), fill=RED)
        yy = yb + 44
        for at, lines in warn_rows:
            d.text((pad, yy), L.clock(at), font=warn_f, fill=RED)
            for k, line in enumerate(lines):
                d.text((pad + 70, yy + 25 * k), line, font=warn_f, fill=TEXT)
            yy += 10 + 25 * len(lines)
    yc = rect.h - 22 - 21 * len(credit_lines)
    for line in credit_lines:
        d.text((pad, yc), line, font=credit_f, fill=AMBER if "labelled" in line.lower() else FAINT)
        yc += 21
    return to_bgr(img)


def draw_feed(state: L.PanelState, rect: L.Rect, current_text: str | None) -> np.ndarray:
    img = Image.new("RGB", (rect.w, rect.h), FEED)
    d = ImageDraw.Draw(img)
    pad = 34
    width = rect.w - pad * 2 - 12
    if state.live_warning:
        at, text = state.live_warning
        d.rectangle((0, 0, 8, rect.h), fill=RED)
        d.text((pad, 30), f"WARNING  ·  {L.clock(at)}", font=font(18, "bold"), fill=RED)
        f = font(34, "bold")
        lines = L.wrap(text, width, measure(f))[:4]
        for k, line in enumerate(lines):
            d.text((pad, 68 + 44 * k), line, font=f, fill=TEXT)
        if state.live_say:
            pass
    elif state.live_say:
        at, text = state.live_say
        d.rectangle((0, 0, 8, rect.h), fill=BLUE)
        d.text((pad, 30), f"COACH SAYS  ·  {L.clock(at)}", font=font(18, "bold"), fill=BLUE)
        f = font(32, "medium")
        for k, line in enumerate(L.wrap(text, width, measure(f))[:4]):
            d.text((pad, 68 + 42 * k), line, font=f, fill=TEXT)
    elif current_text:
        d.text((pad, 30), "NOW", font=font(18, "bold"), fill=MUTED)
        f = font(30)
        for k, line in enumerate(L.wrap(current_text, width, measure(f))[:4]):
            d.text((pad, 68 + 40 * k), line, font=f, fill=TEXT)
    else:
        d.text((pad, 30), "ALL STEPS FINISHED", font=font(18, "bold"), fill=GREEN)
    nxt = next((st for st in state.steps if st.status == "pending"), None)
    if nxt is not None and not state.live_warning:
        f = font(22)
        line = L.wrap(f"Next: {nxt.text}", width, measure(f))[0]
        d.text((pad, rect.h - 52), line, font=f, fill=MUTED)
    return to_bgr(img)


def cam_tag(i: int, name: str, sprites):
    return sprites.pill(f"Camera {i + 1}  ·  {name}", (30, 38, 48), 16)


def title_card(title: str, lines: list[str], brand: str = "BENCH COACH") -> np.ndarray:
    img = Image.new("RGB", (L.W, L.H), BG)
    d = ImageDraw.Draw(img)
    d.text((160, 380), brand.upper(), font=font(30, "bold"), fill=BLUE)
    y = 430
    for line in L.wrap(title, 1600, measure(font(76, "bold")))[:2]:
        d.text((160, y), line, font=font(76, "bold"), fill=TEXT)
        y += 92
    y += 20
    for line in lines:
        d.text((160, y), line, font=font(30), fill=MUTED)
        y += 44
    return to_bgr(img)


def end_card(lines: list[str]) -> np.ndarray:
    """Closing card: first line large, the rest smaller underneath."""
    img = Image.new("RGB", (L.W, L.H), BG)
    d = ImageDraw.Draw(img)
    y = 440
    for k, line in enumerate(lines):
        f = font(64, "bold") if k == 0 else font(30)
        d.text((L.W // 2, y), line, font=f, fill=TEXT if k == 0 else MUTED, anchor="ma")
        y += 100 if k == 0 else 46
    return to_bgr(img)


def note_card(note: dict, credits: list[str]) -> np.ndarray:
    img = Image.new("RGB", (L.W, L.H), (246, 247, 249))
    d = ImageDraw.Draw(img)
    ink, soft = (22, 27, 34), (96, 106, 118)
    d.text((120, 70), "LAB NOTE  ·  WRITTEN FROM THIS RUN", font=font(22, "bold"), fill=(40, 110, 200))
    d.text((120, 106), note.get("title") or "Run", font=font(54, "bold"), fill=ink)
    from note.render import clock as nclock, header_lines
    hdr = "   ·   ".join(f"{k} {v}" for k, v in header_lines(note) if k in ("Date", "Start", "Length"))
    d.text((120, 178), hdr, font=font(22), fill=soft)
    y = 226
    for line in L.wrap(note.get("summary") or "", 1680, measure(font(26)))[:3]:
        d.text((120, y), line, font=font(26), fill=ink)
        y += 36
    top = y + 30
    # left column: steps
    colw = 820
    d.text((120, top), "STEPS", font=font(20, "bold"), fill=soft)
    y = top + 40
    sf, tf = font(22, "bold"), font(23)
    word = {"done": "Done", "skipped": "Skipped", "unsure": "Not sure", "not finished": "Not finished",
            "not reached": "Not reached"}
    colours = {"done": (30, 150, 95), "skipped": (200, 60, 50), "unsure": (190, 130, 20)}
    for s in note.get("steps", [])[:12]:
        st = s.get("status", "")
        lab = f" ({s['label']})" if s.get("label") else ""
        at = f"  {nclock(s.get('at'))}" if s.get("at") is not None and st in ("done", "skipped", "unsure") else ""
        d.text((120, y), f"{s['number']}.", font=tf, fill=soft)
        colour = (190, 120, 10) if s.get("label") == "hand-labelled" else colours.get(st, soft)
        d.text((160, y), f"{word.get(st, st)}{lab}{at}", font=sf, fill=colour)
        y += 32
        for line in L.wrap(s.get("text", ""), colw - 60, measure(tf))[:2]:
            d.text((160, y), line, font=tf, fill=ink)
            y += 31
        y += 12
        if y > 960:
            break
    # right column: what went wrong, then what was said
    x = 1040
    y = top
    probs = note.get("problems") or []
    d.text((x, y), "WHAT WENT WRONG", font=font(20, "bold"), fill=soft)
    y += 40
    if not probs:
        d.text((x, y), "Nothing flagged.", font=tf, fill=ink)
        y += 40
    for p in probs[:5]:
        at = nclock(p.get("at")) if p.get("at") is not None else ""
        d.text((x, y), at, font=sf, fill=(200, 60, 50))
        for line in L.wrap(p.get("text", ""), 640, measure(tf))[:3]:
            d.text((x + 80, y), line, font=tf, fill=ink)
            y += 31
        y += 14
    said = note.get("said") or []
    if said and y < 820:
        y += 20
        d.text((x, y), "WHAT WAS SAID", font=font(20, "bold"), fill=soft)
        y += 40
        for item in said[:3]:
            text = item.get("text", "") if isinstance(item, dict) else str(item)
            for line in L.wrap(f"“{text}”", 720, measure(tf))[:2]:
                d.text((x, y), line, font=tf, fill=ink)
                y += 31
            y += 10
    yc = L.H - 40 - 24 * len(credits)
    for c in credits:
        d.text((120, yc), c, font=font(18), fill=soft)
        yc += 24
    return to_bgr(img)


class Encoder:
    def __init__(self, path: Path, fps: int, codec: str):
        enc = ["-c:v", "h264_videotoolbox", "-b:v", "10M", "-profile:v", "high"] if codec == "videotoolbox" else \
              ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{L.W}x{L.H}",
               "-r", str(fps), "-i", "-", *enc, "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(path)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        self.frames = 0

    def write(self, img):
        self.proc.stdin.write(img.tobytes())
        self.frames += 1

    def close(self):
        self.proc.stdin.close()
        if self.proc.wait() != 0:
            raise RuntimeError("ffmpeg failed")


def pick_codec(wanted: str) -> str:
    if wanted != "auto":
        return wanted
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return "videotoolbox" if "h264_videotoolbox" in enc else "x264"


def render(out: Path, videos, names, analysis, log, protocol, note, credits, codec, seconds=0.0,
           brand="BENCH COACH", title_seconds=TITLE_SECONDS, note_seconds=NOTE_SECONDS, end_lines=None,
           end_seconds=3.0):
    caps = [cv2.VideoCapture(str(v)) for v in videos]
    sizes = [(int(c.get(cv2.CAP_PROP_FRAME_WIDTH)), int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))) for c in caps]
    lengths = [c.get(cv2.CAP_PROP_FRAME_COUNT) / (c.get(cv2.CAP_PROP_FPS) or 30) for c in caps]
    vfps = [c.get(cv2.CAP_PROP_FPS) or 30.0 for c in caps]
    duration = max(lengths)
    if seconds:
        duration = min(duration, seconds)
    lay = L.layout(sizes)
    steps = [s["text"] for s in protocol.get("steps", [])]
    timeline = L.Timeline(steps, [e for e in log if e.get("source") == "coach" and e["kind"] in ("step", "warning", "say")])
    snaps = [[(t, b) for t, b in s] for s in analysis["snaps"]]
    hands = [[(t, h) for t, h in s] for s in analysis["hands"]]
    sprites = Sprites()
    enc = Encoder(out, OUT_FPS, codec)
    title = protocol.get("name") or "Protocol"

    card = title_card(title, [f"{len(videos)} camera{'s' if len(videos) > 1 else ''}, {L.clock(duration)} of bench footage, "
                              f"{len(steps)} protocol steps.", *credits], brand)
    for _ in range(int(title_seconds * OUT_FPS)):
        enc.write(card)

    base = np.zeros((L.H, L.W, 3), np.uint8)
    base[:] = bgr(BG)
    panel_key = feed_key = None
    panel_img = feed_img = None
    last = [None] * len(caps)
    next_pos = [0.0] * len(caps)
    n_frames = int(duration * OUT_FPS)
    for k in range(n_frames):
        s = k / OUT_FPS
        canvas = base.copy()
        for i, cap in enumerate(caps):
            # keep each camera at the frame nearest to s (sources may differ in frame rate)
            while next_pos[i] <= s + 1e-6:
                ok, f = cap.read()
                if not ok:
                    next_pos[i] = float("inf")
                    break
                last[i] = f
                next_pos[i] += 1.0 / vfps[i]
            r = lay.cams[i]
            # draw on the camera's own picture so nothing spills into the next one
            here = L.Rect(0, 0, r.w, r.h)
            if last[i] is not None:
                view = cv2.resize(last[i], (r.w, r.h), interpolation=cv2.INTER_AREA)
            else:
                view = np.zeros((r.h, r.w, 3), np.uint8)
            draw_objects(view, L.dedupe(L.boxes_at(snaps[i], s)), sizes[i], here, sprites)
            draw_hands(view, L.hands_at(hands[i], s), sizes[i], here, sprites)
            sprites.blit(view, cam_tag(i, names[i], sprites), 14, 14, here)
            canvas[r.y:r.y2, r.x:r.x2] = view
        state = timeline.at(s)
        key = state.key()
        if key != panel_key:
            panel_img, panel_key = draw_panel(state, title, lay.panel, credits), key
        cur = next((st.text for st in state.steps if st.status == "current"), None)
        fkey = (state.live_warning, state.live_say, cur)
        if fkey != feed_key:
            feed_img, feed_key = draw_feed(state, lay.feed, cur), fkey
        p, fd = lay.panel, lay.feed
        canvas[p.y:p.y2, p.x:p.x2] = panel_img
        canvas[fd.y:fd.y2, fd.x:fd.x2] = feed_img
        enc.write(canvas)
        if k and k % 600 == 0:
            print(f"  render: {k}/{n_frames} frames", flush=True)
    for c in caps:
        c.release()
    if note:
        nc = note_card(note, credits)
        for _ in range(int(note_seconds * OUT_FPS)):
            enc.write(nc)
    if end_lines:
        ec = end_card(end_lines)
        for _ in range(int(end_seconds * OUT_FPS)):
            enc.write(ec)
    enc.close()
    return {"frames": enc.frames, "duration": duration, "layout": [vars(r) for r in lay.cams]}


# ------------------------------------------------------------------ main ----------------------

def mark_labelled(note: dict, log: list[dict]) -> None:
    """The note part calls every camera-event step "seen"; steps decided by hand labels must not be."""
    hand = {e.get("step") for e in log if e.get("source") == "coach" and e["kind"] == "step"
            and (e.get("detail") or {}).get("labelled") and (e.get("detail") or {}).get("status") in ("done", "unsure")}
    for s in note.get("steps", []):
        if s.get("index") in hand:
            s["label"] = "hand-labelled"


def credits_for(analysis: dict, labelled: bool) -> list[str]:
    det = analysis.get("detector")
    seen = ("Objects outlined by Grounding DINO tiny" if det == "gdino-tiny" else
            f"Objects outlined by {det}" if det and det != "none" else "No object detector: hands only")
    out = [f"{seen}, hands by MediaPipe. Steps ticked by the coach's rules from what the cameras saw."]
    if labelled:
        out.append("Hand-labelled events were added; steps they decided say hand-labelled.")
    return out


def parse(argv=None):
    p = argparse.ArgumentParser(prog="python -m scripts.render_demo", description=__doc__.splitlines()[0])
    p.add_argument("--video", type=Path)
    p.add_argument("--video2", type=Path)
    p.add_argument("--audio", type=Path, help="sound to put under the footage (starts with the first frame)")
    p.add_argument("--r2-latest", action="store_true",
                   help="fetch the newest Pi session from R2 (usb and ribbon views, audio) instead of --video")
    p.add_argument("--r2-session", help="fetch this R2 session id instead of the newest")
    p.add_argument("--r2-device", help="only sessions from this Pi")
    p.add_argument("--r2-cache", type=Path, help="download folder (default $BENCH_R2_CACHE or ~/.cache/bench-coach/r2)")
    p.add_argument("--protocol", required=True, type=Path, help="numbered steps in a .txt, or a protocol .yaml")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--title", help="protocol title on screen (default: from the protocol)")
    p.add_argument("--labels", type=Path, help="hand-labelled events, JSON lines with s, kind, object")
    p.add_argument("--fps", type=float, default=10.0, help="vision frames per second per camera")
    p.add_argument("--detect-every", type=float, default=0.35)
    p.add_argument("--detector", default="gdino-tiny", help="gdino-tiny, gdino-base, florence2 or none")
    p.add_argument("--hints", action="store_true", help="vision --new-draw (tip-into-container hints)")
    p.add_argument("--seconds", type=float, default=0.0, help="only the first this many seconds")
    p.add_argument("--reuse", action="store_true", help="reuse the saved vision pass if there is one")
    p.add_argument("--no-note", action="store_true")
    p.add_argument("--llm-protocol", action="store_true", help="let Claude convert a .txt protocol (needs a key)")
    p.add_argument("--brand", default="Bench Coach", help="small line above the title on the title card")
    p.add_argument("--title-seconds", type=float, default=TITLE_SECONDS)
    p.add_argument("--note-seconds", type=float, default=NOTE_SECONDS)
    p.add_argument("--end", action="append", default=[], help="end card line (repeat; first is large)")
    p.add_argument("--codec", default="auto", choices=["auto", "videotoolbox", "x264"])
    return p.parse_args(argv)


def from_r2(a):
    """Fill --video, --video2 and --audio from an R2 session (scripts/r2_pull.py)."""
    from scripts import r2_pull
    try:
        got = r2_pull.fetch(a.r2_session, latest=not a.r2_session, device=a.r2_device, out=a.r2_cache)
    except r2_pull.PullError as e:
        sys.exit(f"r2: {e}")
    views = [got[n] for n in ("usb", "ribbon") if n in got]
    a.video, a.video2 = views[0], (views[1] if len(views) > 1 else None)
    if a.audio is None:
        a.audio = got.get("audio")
    return a


def mux_audio(out: Path, audio: Path, seconds: float = 0.0, title_seconds: float = TITLE_SECONDS):
    """Put the recording's sound under the footage: it starts after the title card and is cut at
    the end of the footage. The video stream is copied, not re-encoded."""
    tmp = out.with_name(out.stem + ".audio-tmp" + out.suffix)
    trim = ["-t", str(seconds)] if seconds else []
    delay = int(title_seconds * 1000)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), *trim, "-i", str(audio),
           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
           "-af", f"adelay={delay}:all=1", "-movflags", "+faststart", str(tmp)]
    subprocess.run(cmd, check=True)
    tmp.replace(out)


def main(argv=None):
    a = parse(argv)
    if a.r2_latest or a.r2_session:
        a = from_r2(a)
    if a.video is None:
        sys.exit("give --video (and optionally --video2), or --r2-latest / --r2-session")
    if a.audio is not None and not a.audio.is_file():
        sys.exit(f"not found: {a.audio}")
    videos = [a.video] + ([a.video2] if a.video2 else [])
    for v in videos + [a.protocol]:
        if not v.is_file():
            sys.exit(f"not found: {v}")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    work = a.out.with_name(a.out.stem + "-work")
    work.mkdir(exist_ok=True)
    timings = {}
    t = time.time()
    protocol = load_protocol(a.protocol, a.llm_protocol)
    protocol["name"] = a.title or pretty_title(protocol, a.protocol)
    cache = work / "analysis.json"
    key = {"videos": [str(v.resolve()) for v in videos], "fps": a.fps, "detect_every": a.detect_every,
           "detector": a.detector, "hints": a.hints, "seconds": a.seconds}
    analysis = None
    if a.reuse and cache.is_file():
        saved = json.loads(cache.read_text())
        if saved.get("key") == key:
            analysis = saved
            print("vision: reusing the saved pass")
    if analysis is None:
        print(f"vision: {len(videos)} camera(s), {a.fps:g} fps, detector {a.detector}", flush=True)
        analysis = analyse(videos, a.fps, a.detect_every, a.detector, a.hints, a.seconds)
        analysis["key"] = key
        cache.write_text(json.dumps(analysis))
    timings["vision"] = round(time.time() - t, 1)

    t = time.time()
    t0 = analysis["t0"]
    events = [dict(e) for e in analysis["events"]]
    labelled = False
    if a.labels:
        extra = labelled_events(a.labels, t0)
        labelled = bool(extra)
        events += extra
    caps = [cv2.VideoCapture(str(v)) for v in videos]
    end_s = max(c.get(cv2.CAP_PROP_FRAME_COUNT) / (c.get(cv2.CAP_PROP_FPS) or 30) for c in caps)
    for c in caps:
        c.release()
    if a.seconds:
        end_s = min(end_s, a.seconds)
    run_id = "render-" + a.out.stem
    log = coach(protocol, events, t0, end_s, run_id)
    with open(work / "events.jsonl", "w") as fh:
        for e in log:
            fh.write(json.dumps(e) + "\n")
    timings["coach"] = round(time.time() - t, 1)

    t = time.time()
    note = None if a.no_note else build_note(work, run_id, log, videos)
    if note is not None:
        mark_labelled(note, log)
        for s in note.get("steps", []):
            s["at"] = None if s.get("t") is None else s["t"] - t0
        for p in note.get("problems", []):
            p["at"] = None if p.get("t") is None else p["t"] - t0
    timings["note"] = round(time.time() - t, 1)

    t = time.time()
    names = [v.stem for v in videos]
    info = render(a.out, videos, names, analysis, log, protocol, note,
                   credits_for(analysis, labelled), pick_codec(a.codec), a.seconds,
                   brand=a.brand, title_seconds=a.title_seconds, note_seconds=a.note_seconds, end_lines=a.end)
    if a.audio is not None:
        mux_audio(a.out, a.audio, a.seconds, a.title_seconds)
    timings["render"] = round(time.time() - t, 1)
    timings["total"] = round(sum(timings.values()), 1)
    kinds = {}
    for e in log:
        if e.get("source") in ("vision", "coach") and e["kind"] in ("tip_on", "tip_off", "draw", "dispense",
                                                                   "hand_touch", "warning", "step"):
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    summary = {"out": a.out.name, "footage_seconds": round(info["duration"], 1), "frames": info["frames"],
               "seconds": timings, "events": kinds, "note": note is not None,
               "audio": a.audio is not None}
    (work / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
