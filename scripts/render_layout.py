"""Layout and timeline logic for scripts/render_demo.py, with no video, model or font.

Everything here works on plain numbers and event dicts so it can be tested on its own:
where each camera and the side panel go on the 1920x1080 frame, what the panel shows at a
given second, and how boxes and hand points move smoothly between two samples.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

W, H = 1920, 1080
PANEL_W = 640
WARN_SECONDS = 7.0     # a warning stays in the bottom box this long
SAY_SECONDS = 5.0      # a coach line stays this long
HAND_GAP = 0.35        # interpolate hand points only between samples this close together
HAND_HOLD = 0.2        # show a lone hand sample this long around its time


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self):
        return self.x + self.w

    @property
    def y2(self):
        return self.y + self.h


@dataclass
class Layout:
    cams: list            # one Rect per camera, the video fitted inside it
    panel: Rect           # protocol steps on the right
    feed: Rect            # warnings and coach lines


def fit(src_w: int, src_h: int, box: Rect) -> Rect:
    """The largest rectangle of the source's shape that fits in box, centred."""
    s = min(box.w / src_w, box.h / src_h)
    w, h = int(round(src_w * s)), int(round(src_h * s))
    w -= w % 2
    h -= h % 2
    return Rect(box.x + (box.w - w) // 2, box.y + (box.h - h) // 2, w, h)


def layout(sizes: list[tuple[int, int]]) -> Layout:
    """sizes: (width, height) of each camera. One or two cameras.

    One camera: it fills the left 1280 columns at the top, the feed box goes under it.
    Two: the first is large at the top left, the second sits under it next to the feed box.
    """
    if not 1 <= len(sizes) <= 2:
        raise ValueError("one or two cameras")
    left = W - PANEL_W
    top = Rect(0, 0, left, 720)
    panel = Rect(left, 0, PANEL_W, H)
    cams = [fit(*sizes[0], top)]
    if len(sizes) == 1:
        feed = Rect(0, 720, left, H - 720)
    else:
        cams.append(fit(*sizes[1], Rect(0, 720, 640, H - 720)))
        feed = Rect(640, 720, left - 640, H - 720)
    return Layout(cams, panel, feed)


def scale_box(box, src_w: int, src_h: int, rect: Rect):
    """A box in source pixels to output pixels inside rect."""
    sx, sy = rect.w / src_w, rect.h / src_h
    x1, y1, x2, y2 = box
    return (rect.x + x1 * sx, rect.y + y1 * sy, rect.x + x2 * sx, rect.y + y2 * sy)


def clock(sec: float) -> str:
    s = max(0, int(sec))
    return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------- timeline --------------------

@dataclass
class StepView:
    number: int
    text: str
    status: str = "pending"          # pending, current, done, skipped, unsure
    at: float | None = None          # seconds into the video when it got that status
    how: str | None = None           # seen, said, seen+said, hand-labelled


@dataclass
class PanelState:
    steps: list
    warnings: list                   # (at, text) every warning so far
    live_warning: tuple | None       # (at, text) the one to show in the feed box now
    live_say: tuple | None           # (at, text) the coach's line to show now
    elapsed: float
    labelled: bool = False           # some evidence came from hand-labelled events

    def key(self):
        """Changes whenever anything drawn changes (the clock only by whole seconds)."""
        return (tuple((s.status, s.at, s.how) for s in self.steps), len(self.warnings),
                self.live_warning, self.live_say, int(self.elapsed), self.labelled)


@dataclass
class Timeline:
    """Coach events (step, warning, say) in video seconds, queried by time."""
    step_texts: list
    events: list = field(default_factory=list)   # sorted dicts with "s" = video seconds

    def __post_init__(self):
        self.events = sorted(self.events, key=lambda e: e["s"])
        self.times = [e["s"] for e in self.events]

    def at(self, s: float) -> PanelState:
        steps = [StepView(i + 1, t) for i, t in enumerate(self.step_texts)]
        if steps:
            steps[0].status = "current"
        warnings, say, labelled = [], None, False
        for e in self.events[:bisect.bisect_right(self.times, s)]:
            d = e.get("detail") or {}
            if e["kind"] == "step":
                i = d.get("index")
                if isinstance(i, int) and 0 <= i < len(steps):
                    st = steps[i]
                    st.status = d.get("status") or st.status
                    st.at = e["s"]
                    st.how = "hand-labelled" if d.get("labelled") else d.get("source")
                    labelled = labelled or bool(d.get("labelled"))
            elif e["kind"] == "warning" and d.get("text"):
                if not warnings or warnings[-1][1] != d["text"]:
                    warnings.append((e["s"], d["text"]))
            elif e["kind"] == "say" and d.get("text"):
                say = (e["s"], d["text"])
        live_w = warnings[-1] if warnings and s - warnings[-1][0] <= WARN_SECONDS else None
        live_s = say if say and s - say[0] <= SAY_SECONDS else None
        if live_s and live_w and live_s[1] == live_w[1]:
            live_s = None
        return PanelState(steps, warnings, live_w, live_s, s, labelled)


# ---------------------------------------------------------------- motion ----------------------

def lerp(a, b, f):
    return tuple(x + (y - x) * f for x, y in zip(a, b))


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def boxes_at(snaps: list, s: float) -> list:
    """snaps: [(t, [{"label", "box", "score"}])] sorted by t. The boxes at time s, each one moved
    part of the way to its best match (same label, overlapping) in the next snapshot."""
    if not snaps:
        return []
    times = [t for t, _ in snaps]
    i = bisect.bisect_right(times, s) - 1
    if i < 0:
        return []
    ta, a = snaps[i]
    if i + 1 >= len(snaps):
        return [dict(d) for d in a]
    tb, b = snaps[i + 1]
    f = (s - ta) / (tb - ta) if tb > ta else 0.0
    out = []
    for d in a:
        best, score = None, 0.3
        for e in b:
            if e["label"] == d["label"]:
                v = iou(d["box"], e["box"])
                if v > score:
                    best, score = e, v
        box = lerp(d["box"], best["box"], f) if best else tuple(d["box"])
        out.append({**d, "box": box})
    return out


def hands_at(samples: list, s: float) -> list:
    """samples: [(t, [{"side", "pts" or None, "box" or None}])] sorted by t. Hands at time s:
    finger points blended between the samples either side when the same hand is in both."""
    if not samples:
        return []
    times = [t for t, _ in samples]
    j = bisect.bisect_right(times, s)
    before = samples[j - 1] if j > 0 else None
    after = samples[j] if j < len(samples) else None
    if before and after and after[0] - before[0] <= HAND_GAP:
        f = (s - before[0]) / (after[0] - before[0]) if after[0] > before[0] else 0.0
        out = []
        for h in before[1]:
            m = next((g for g in after[1] if g["side"] == h["side"] and bool(g.get("pts")) == bool(h.get("pts"))), None)
            if m is None:
                if f < 0.5:
                    out.append(h)
                continue
            if h.get("pts"):
                out.append({**h, "pts": [lerp(p, q, f) for p, q in zip(h["pts"], m["pts"])]})
            else:
                out.append({**h, "box": lerp(h["box"], m["box"], f)})
        if f >= 0.5:
            out += [g for g in after[1] if not any(o["side"] == g["side"] for o in out)]
        return out
    near = min((x for x in (before, after) if x), key=lambda x: abs(x[0] - s))
    return near[1] if abs(near[0] - s) <= HAND_HOLD else []


def wrap(text: str, width: float, measure) -> list[str]:
    """Greedy word wrap; measure(str) gives a width in the same units as width."""
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and measure(trial) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines
