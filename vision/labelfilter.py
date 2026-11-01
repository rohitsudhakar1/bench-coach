"""CPU-only, causal label filtering. One instance per camera; no model imports.

Call update once per *new detector frame*, never once per displayed frame. Boxes
and hands must use the same pixel coordinates. Missing hands fail closed; hand
boxes themselves always pass through as context rather than protocol equipment.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
import math
import re

from .core import Det, Hand, center, dist, iou
from .held import hand_box

# Long side / hand bounding-box span, elongation (long / short), area / span².
# Broad bounds from the tuning split; not physical dimensions or calibrated sizes.
LIMITS = {
    "pipette": (0.25, 3.5, 1.2, 30.0, 0.02, 6.0),
    "pipette tip": (0.08, 3.0, 1.05, 30.0, 0.003, 5.5),
    "tip box": (0.7, 5.0, 1.0, 2.2, 0.4, 20.0),
    "tube": (0.08, 2.5, 1.0, 15.0, 0.005, 4.0),
    "tube rack": (0.5, 5.0, 1.0, 5.0, 0.2, 20.0),
    "plate": (0.3, 4.0, 1.0, 4.0, 0.1, 12.0),
    "waste": (0.3, 4.0, 1.0, 4.0, 0.1, 12.0),
}
PATTERNS = (
    ("tip box", r"\b(?:tip[ -]?(?:box(?:es)?|racks?)|box(?:es)? of tips)\b"),
    ("tube rack", r"\b(?:tube[ -]?racks?|racks? of tubes)\b"),
    ("pipette tip", r"\b(?:pipett?e?[ -]?)?tips?\b"),
    ("pipette", r"\bpipett?(?:e[sd]?|ing|ors?)?\b"),
    ("tube", r"\b(?:tubes?|microtubes?|eppendorfs?)\b"),
    ("plate", r"\b(?:plates?|wells?|petri dishes)\b"),
    ("waste", r"\b(?:waste|trash|discard bin)\b"),
)


def protocol_classes(protocol) -> frozenset[str]:
    """Read instruction lines, not YAML titles, descriptions or bibliography.

    Tip attachment also permits its required pipette and tip-box context. Plain
    tip mentions alone do not imply a box. Unknown vocabulary permits no objects.
    """
    if isinstance(protocol, dict):
        lines = [s.get("text", "") for s in protocol.get("steps", [])]
        for step in protocol.get("steps", []):
            for expected in step.get("expect", []):
                lines.append(expected.get("object") or "")
                if expected.get("kind") == "tip_on":
                    lines.append("attach tip to pipette at tip box")
    elif isinstance(protocol, str):
        lines = protocol.splitlines()
    else:
        lines = list(protocol or [])
    allowed = set()
    for line in lines:
        text = str(line).lower()
        if re.search(r"\btip(?:s)?\b", text) and re.search(
                r"\b(?:attach\w*|fit\w*|put|pick\w*|load\w*|fresh|new)\b", text):
            allowed.update(("pipette", "pipette tip", "tip box"))
        for label, pattern in PATTERNS:
            if re.search(pattern, text):
                allowed.add(label)
                text = re.sub(pattern, " ", text)
    return frozenset(allowed)


def objects(dets):
    """Copy dict detections (preserving outlines/metadata) or Det records."""
    return [dict(d) if isinstance(d, dict) else
            {"label": d.label, "box": list(d.box), "score": d.score} for d in dets]


def valid_box(box):
    return (box is not None and len(box) == 4 and all(math.isfinite(v) for v in box)
            and box[2] > box[0] and box[3] > box[1])


def hand_bounds(hands):
    result = []
    for h in hands:
        if isinstance(h, dict):
            h = Hand(h.get("side", "unknown"), h.get("pts"), h.get("box"))
        b = hand_box(h)
        if valid_box(b):
            result.append(b)
    return result


def gap(a, b):
    return math.hypot(max(a[0] - b[2], b[0] - a[2], 0),
                      max(a[1] - b[3], b[1] - a[3], 0))


def span(b):
    return max(b[2] - b[0], b[3] - b[1])


@dataclass
class Track:
    box: list
    last: float
    streak: int = 0
    votes: deque = field(default_factory=deque)
    label: str | None = None
    held: int = 0


class LabelFilter:
    def __init__(self, protocol, min_frames=3, window=5, max_gap=1.0,
                 match_iou=0.25, limits=None):
        if min_frames < 1 or window < min_frames or max_gap <= 0 or not 0 < match_iou <= 1:
            raise ValueError("invalid label filter settings")
        self.allowed = protocol_classes(protocol)
        self.min_frames, self.window, self.max_gap = min_frames, window, max_gap
        self.match_iou, self.limits = match_iou, dict(LIMITS if limits is None else limits)
        self.reset()

    def reset(self):
        self.tracks, self.current = [], []
        self.last_t = None
        self.hands = []
        self.rejected = Counter()

    def geometry(self, label, box, hands):
        if label not in self.limits or not hands:
            return False
        hb = min(hands, key=lambda b: dist(center(b), center(box)))
        scale = span(hb)
        w, h = box[2] - box[0], box[3] - box[1]
        length, aspect, area = max(w, h) / scale, max(w, h) / min(w, h), w * h / scale**2
        lo, hi, alo, ahi, slo, shi = self.limits[label]
        return lo <= length <= hi and alo <= aspect <= ahi and slo <= area <= shi

    def update(self, t, dets, hands=()):
        """Return copied dicts, with smoothed labels and qualified held metadata.

        A missing detection breaks its consecutive streak. A missing frame is not
        a vote. Matching is one-to-one, by IoU across labels. Duplicate timestamps
        cannot accumulate evidence. Backward clocks reset the entire camera.
        """
        if not math.isfinite(t):
            raise ValueError("non-finite frame time")
        if self.last_t is not None and t < self.last_t:
            self.reset()
        if self.last_t == t:
            return self.visible(t)
        ds = [d for d in objects(dets) if valid_box(d.get("box"))]
        self.hands = hand_bounds(hands)
        # Use detector hands only when no landmark hands were supplied.
        if not self.hands:
            self.hands = [d["box"] for d in ds if d["label"] == "hand"]
        self.tracks = [tr for tr in self.tracks if t - tr.last <= self.max_gap]
        candidates = []
        for d in ds:
            label = d["label"]
            if label == "hand":
                continue
            if label not in self.allowed:
                self.rejected["protocol"] += 1
            elif not self.geometry(label, d["box"], self.hands):
                self.rejected["geometry_or_no_hand"] += 1
            else:
                candidates.append(d)
        pairs = sorted(((iou(tr.box, d["box"]), ti, di)
                        for ti, tr in enumerate(self.tracks) for di, d in enumerate(candidates)), reverse=True)
        matched, used = {}, set()
        for overlap, ti, di in pairs:
            if overlap >= self.match_iou and ti not in used and di not in matched:
                used.add(ti)
                matched[di] = self.tracks[ti]
        for ti, tr in enumerate(self.tracks):
            if ti not in used:
                tr.streak = tr.held = 0
                tr.votes.clear()
                tr.label = None
        result = [dict(d) for d in ds if d["label"] == "hand"]
        for di, d in enumerate(candidates):
            tr = matched.get(di)
            if tr is None:
                tr = Track(list(d["box"]), t)
                self.tracks.append(tr)
            tr.box, tr.last = list(d["box"]), t
            tr.streak += 1
            tr.votes.append(d["label"])
            while len(tr.votes) > self.window:
                tr.votes.popleft()
            counts = Counter(tr.votes)
            winner, count = counts.most_common(1)[0]
            if tr.label is None or count > len(tr.votes) / 2:
                tr.label = winner
            near = any(gap(tr.box, hb) <= 0.15 * span(hb) for hb in self.hands)
            tr.held = tr.held + 1 if near else 0
            if (tr.streak < self.min_frames or counts[tr.label] < self.min_frames
                    or not self.geometry(tr.label, tr.box, self.hands)):
                self.rejected["persistence"] += 1
                continue
            out = {**d, "label": tr.label}
            if tr.label in ("pipette", "pipette tip"):
                out["held"] = tr.held >= self.min_frames and d.get("held", True) is True
            result.append(out)
        self.last_t, self.current = t, result
        return self.visible(t)

    def visible(self, t):
        if self.last_t is None or not 0 <= t - self.last_t <= self.max_gap:
            return []
        return [dict(d) for d in self.current]

    def allows_tip_attach(self, t, hands=None):
        """Require a held *pipette body* and tip box near the SAME current hand.

        A pipette-tip detection alone is insufficient: bottles often get that
        label. This gate rejects events; it never invents an attachment event.
        """
        ds = self.visible(t)
        hs = self.hands if hands is None else hand_bounds(hands)
        pips = [d for d in ds if d["label"] == "pipette" and d.get("held") is True]
        boxes = [d for d in ds if d["label"] == "tip box"]
        return any(any(gap(p["box"], hb) <= 0.15 * span(hb) for p in pips)
                   and any(gap(b["box"], hb) <= 0.5 * span(hb) for b in boxes) for hb in hs)

    def filter_events(self, events, t, hands=None):
        return [e for e in events if e.get("kind") != "tip_on" or self.allows_tip_attach(t, hands)]


def filter_analysis(analysis, protocol):
    """Filter renderer snapshots and tip events without changing its reusable cache.

    Saved snapshots are already scene-filtered, so they cannot recover raw boxes.
    Hand-labelled events are added by the renderer *after* this operation.
    """
    filters = [LabelFilter(protocol) for _ in analysis["snaps"]]
    snapshots = [[] for _ in filters]
    latest_hands = [[] for _ in filters]
    hand_times = [-float("inf")] * len(filters)
    events = []
    timeline = []
    for cam, rows in enumerate(analysis["hands"]):
        timeline += [(t, 0, cam, hs) for t, hs in rows]
    for cam, rows in enumerate(analysis["snaps"]):
        timeline += [(t, 1, cam, ds) for t, ds in rows]
    for index, e in enumerate(analysis["events"]):
        timeline.append((e.get("s", e["t"] - analysis["t0"]), 2, index, e))
    for t, kind, key, data in sorted(timeline, key=lambda row: row[:3]):
        if kind == 0:
            latest_hands[key] = data
            hand_times[key] = t
        elif kind == 1:
            hs = latest_hands[key] if t - hand_times[key] <= 0.25 else []
            ds = filters[key].update(t, data, hs)
            snapshots[key].append((t, ds))
        else:
            cam = (data.get("detail") or {}).get("cam")
            if data.get("kind") != "tip_on" or (
                    isinstance(cam, int) and 0 <= cam < len(filters)
                    and filters[cam].allows_tip_attach(
                        t, latest_hands[cam] if t - hand_times[cam] <= 0.25 else [])):
                events.append(dict(data))
    # The renderer otherwise holds the last snapshot forever, including after EOF.
    for cam, rows in enumerate(snapshots):
        expired = []
        for index, (t, ds) in enumerate(rows):
            expired.append((t, ds))
            expiry = t + filters[cam].max_gap + 1e-6
            if index + 1 == len(rows) or rows[index + 1][0] > expiry:
                expired.append((expiry, []))
        snapshots[cam] = expired
    return {**analysis, "snaps": snapshots, "events": events, "filter_labels": True}
