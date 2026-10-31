from vision.core import Hand


def hand_at(x, y, side="right", s=40.0):
    """A made-up MediaPipe hand: 21 points around (x, y), index fingertip at (x, y + s)."""
    pts = [(x, y)] * 21
    pts[0] = (x, y - s)  # wrist
    pts[9] = (x, y)  # middle knuckle: hand size = s
    pts[5] = (x - 5, y)  # index knuckle
    pts[4] = (x - 5, y + 0.8 * s)  # thumb tip
    pts[8] = (x, y + s)  # index tip
    pts[12] = (x + 3, y + s)  # middle tip
    return Hand(side, pts)
