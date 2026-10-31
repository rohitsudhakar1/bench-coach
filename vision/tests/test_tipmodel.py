import random

import numpy as np

from vision.core import HAND, PIPETTE, PIPETTE_TIP, TUBE, Det
from vision.tipmodel import dataset
from vision.tipmodel.model import WithTipModel
from vision.tipmodel.train import augment


def rec(dets, hands):
    return {"w": 640, "h": 480, "dets": [{"label": lab, "box": list(b), "score": s} for lab, b, s in dets],
            "hands": [{"side": "right", "pts": pts} for pts in hands]}


def hand_at(x, y):
    # 21 points spread a little around (x, y); wrist and middle knuckle 30 px apart
    pts = [[x + (i % 5) * 4, y + (i // 5) * 4] for i in range(21)]
    pts[0], pts[9] = [x, y + 30], [x, y]
    return pts


def test_held_long_pipette_is_kept():
    r = rec([(PIPETTE, (300, 100, 340, 260), 0.5)], [hand_at(320, 120)])
    assert dataset.pick(r) == [(1, (300, 100, 340, 260))]


def test_pipette_tip_box_on_the_whole_pipette_counts_as_pipette():
    r = rec([(PIPETTE_TIP, (300, 100, 340, 260), 0.4)], [hand_at(320, 120)])
    assert dataset.pick(r) == [(1, (300, 100, 340, 260))]


def test_no_hand_on_it_no_label():
    r = rec([(PIPETTE, (300, 100, 340, 260), 0.5)], [hand_at(50, 400)])
    assert dataset.pick(r) is None


def test_small_square_and_weak_boxes_are_dropped():
    hands = [hand_at(320, 120)]
    assert dataset.pick(rec([(PIPETTE, (310, 110, 330, 140), 0.6)], hands)) is None  # small
    assert dataset.pick(rec([(PIPETTE, (280, 100, 380, 200), 0.6)], hands)) is None  # square
    assert dataset.pick(rec([(PIPETTE, (300, 100, 340, 260), 0.2)], hands)) is None  # weak


def test_detector_hand_box_stands_in_for_missing_points():
    r = rec([(PIPETTE, (300, 100, 340, 260), 0.5), (HAND, (290, 90, 350, 160), 0.5)], [])
    assert dataset.pick(r) == [(1, (300, 100, 340, 260))]


def test_no_held_out_video_in_training():
    held = dataset.held_out_videos()
    assert not held & set(dataset.CACHED_TRAIN + dataset.EXTRA_TRAIN)
    assert "lsv_mv/CP-Left-Front-414-445.mp4" in held


def test_augment_keeps_boxes_inside_the_image():
    rng = random.Random(3)
    img = np.zeros((480, 640, 3), np.uint8)
    for _ in range(50):
        out, b = augment(img, np.array([[300, 100, 340, 260]], np.float32), rng)
        h, w = out.shape[:2]
        assert (b[:, 0] < b[:, 2]).all() and (b[:, 1] < b[:, 3]).all()
        assert b.min() >= 0 and (b[:, 2] <= w).all() and (b[:, 3] <= h).all()


def test_with_tip_model_swaps_only_pipette_boxes():
    base = lambda img: [Det(PIPETTE, (0, 0, 5, 20), 0.9), Det(PIPETTE_TIP, (1, 1, 2, 2), 0.5),  # noqa: E731
                        Det(TUBE, (5, 5, 9, 9), 0.4)]
    tip = lambda img: [Det(PIPETTE, (10, 10, 15, 40), 0.8)]  # noqa: E731
    out = WithTipModel(base, tip)(None)
    assert [d.label for d in out] == [TUBE, PIPETTE]
    assert out[1].box == (10, 10, 15, 40)
