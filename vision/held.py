"""Require sustained hand proximity before a box can be used as a pipette."""
from collections import deque

from .core import center, dist, iou, overlaps


def hand_box(hand):
    if hand.pts:
        xs, ys = zip(*hand.pts)
        return min(xs), min(ys), max(xs), max(ys)
    return hand.box


def adjacent(box, hand):
    x1, y1, x2, y2 = hand_box(hand)
    pad = max(x2 - x1, y2 - y1) * 0.4
    return overlaps(box, (x1 - pad, y1 - pad, x2 + pad, y2 + pad))


class HeldPipette:
    """A rolling second of hand boxes, weighted by elapsed time, not frame count.

    Fixed candidates use the hand boxes at their location; a moving candidate uses
    its continuous observed path. Adjacency permits 0.4 of the hand box's longest
    side, matching the existing box-only hand size. More than 0.5 s must be held.
    Missing history counts as unheld. A single frame cannot open the gate; stale hands
    cannot fill a gap in the stream. Each camera owns a separate instance.
    """
    def __init__(self):
        self.history = deque()
        self.trajectory = deque()

    def update(self, t, hands):
        if self.history and t <= self.history[-1][0]:
            self.history.clear()
            self.trajectory.clear()
        self.history.append((t, list(hands)))
        while len(self.history) > 1 and self.history[1][0] <= t - 1.0:
            self.history.popleft()

    def observe(self, t, box, hands):
        if self.trajectory:
            last_t, last_box, _ = self.trajectory[-1]
            if (box is None or last_box is None or t <= last_t or t - last_t > 0.25
                    or (iou(box, last_box) < 0.1 and dist(center(box), center(last_box)) >
                        0.5 * max(box[2] - box[0], box[3] - box[1]))):
                self.trajectory.clear()
        self.trajectory.append((t, box, box is not None and any(adjacent(box, h) for h in hands)))
        while len(self.trajectory) > 1 and self.trajectory[1][0] <= t - 1.0:
            self.trajectory.popleft()

    def allows(self, box):
        if not self.history:
            return False
        now, hands = self.history[-1]
        if not any(adjacent(box, h) for h in hands):
            return False
        held = 0.0
        rows = list(self.history)
        for (t, hs), (end, _) in zip(rows, rows[1:]):
            if any(adjacent(box, h) for h in hs):
                held += max(0.0, min(end, t + 0.25) - max(t, now - 1.0))
        moving_held = 0.0
        trajectory = list(self.trajectory)
        if not trajectory or trajectory[-1][1] is None or iou(box, trajectory[-1][1]) < 0.1:
            trajectory = []
        for (t, _, near), (end, _, _) in zip(trajectory, trajectory[1:]):
            if near:
                moving_held += max(0.0, min(end, t + 0.25) - max(t, now - 1.0))
        return max(held, moving_held) > 0.5
