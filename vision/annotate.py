"""Draw what vision sees on a frame, for --debug-video."""
from __future__ import annotations

import cv2

COLORS = {"pipette": (0, 200, 255), "pipette tip": (0, 120, 255), "tip box": (255, 120, 0), "tube": (0, 255, 0),
          "tube rack": (120, 255, 0), "plate": (255, 0, 200), "waste": (80, 80, 255), "hand": (255, 255, 255)}
LINKS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


def draw(frame, cam, hands, dets, debug, recent, fps):
    img = frame.copy()
    for d in dets:
        x1, y1, x2, y2 = map(int, d.box)
        c = COLORS.get(d.label, (200, 200, 200))
        cv2.rectangle(img, (x1, y1), (x2, y2), c, 2)
        cv2.putText(img, f"{d.label} {d.score:.2f}", (x1, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, c, 1)
    for h in hands:
        if not h.pts:
            x1, y1, x2, y2 = map(int, h.box)
            cv2.rectangle(img, (x1, y1), (x2, y2), (255, 255, 255), 1)
            cv2.putText(img, h.side + "?", (x1, y2 + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            continue
        for a, b in LINKS:
            cv2.line(img, tuple(map(int, h.pts[a])), tuple(map(int, h.pts[b])), (255, 255, 255), 1)
        cv2.putText(img, h.side, tuple(map(int, h.pts[0])), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    if debug.get("pipette_box"):
        x1, y1, x2, y2 = map(int, debug["pipette_box"])
        cv2.rectangle(img, (x1, y1), (x2, y2), COLORS["pipette"], 1)
    if debug.get("tip_end"):
        cv2.circle(img, tuple(map(int, debug["tip_end"])), 7, (0, 0, 255) if debug.get("pressed") else (0, 255, 255), 2)
    cv2.rectangle(img, (0, 0), (img.shape[1], 20), (0, 0, 0), -1)
    cv2.putText(img, f"cam {cam}  {fps:.1f} fps  in: {debug.get('vessel') or '-'}", (4, 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
    for i, line in enumerate(recent[-4:]):
        y = img.shape[0] - 8 - 18 * (len(recent[-4:]) - 1 - i)
        cv2.putText(img, line, (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
        cv2.putText(img, line, (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
    return img


class DebugVideo:
    """All cameras side by side in one mp4."""

    def __init__(self, path, fps, height=360):
        self.path, self.fps, self.h = path, fps, height
        self.writer, self.size, self.last = None, None, {}

    def add(self, cam, img):
        h, w = img.shape[:2]
        self.last[cam] = cv2.resize(img, (int(w * self.h / h), self.h))

    def flush(self):
        if not self.last:
            return
        row = cv2.hconcat([self.last[c] for c in sorted(self.last)])
        if self.writer is None:
            self.size = (row.shape[1], row.shape[0])
            self.writer = cv2.VideoWriter(self.path, cv2.VideoWriter_fourcc(*"mp4v"), self.fps, self.size)
        if (row.shape[1], row.shape[0]) != self.size:
            row = cv2.resize(row, self.size)
        self.writer.write(row)

    def close(self):
        if self.writer:
            self.writer.release()
