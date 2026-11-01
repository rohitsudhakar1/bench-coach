"""Mask outlines: polygon from a mask, the sanity check, drawing, and the fall back to boxes."""
import os
from pathlib import Path

import numpy as np
import pytest

from vision import masks as M
from app.live_draw import draw_objects as live_draw
from scripts import render_layout as L
from scripts.render_demo import Sprites, draw_objects as render_draw

BOX = [20, 30, 80, 90]


def disc_mask(h=120, w=120, c=(50, 60), r=25):
    yy, xx = np.mgrid[:h, :w]
    return (xx - c[0]) ** 2 + (yy - c[1]) ** 2 <= r * r


def test_mask_to_polygon_follows_the_shape():
    poly = M.mask_to_polygon(disc_mask())
    pts = np.asarray(poly)
    assert len(poly) > 8  # a round outline, not a box
    assert abs(pts[:, 0].min() - 25) <= 1 and abs(pts[:, 0].max() - 75) <= 1
    assert abs(pts[:, 1].min() - 35) <= 1 and abs(pts[:, 1].max() - 85) <= 1


def test_empty_or_tiny_mask_gives_none():
    assert M.mask_to_polygon(np.zeros((50, 50), bool)) is None
    tiny = np.zeros((50, 50), bool)
    tiny[10:12, 10:12] = True
    assert M.mask_to_polygon(tiny) is None


def test_sensible_rejects_spill_and_slivers():
    good = M.mask_to_polygon(disc_mask())
    assert M.sensible(good, BOX)
    whole = M.mask_to_polygon(np.ones((120, 120), bool))  # model grabbed the whole table
    assert not M.sensible(whole, BOX)
    sliver = np.zeros((120, 120), bool)
    sliver[55:60, 40:60] = True
    assert not M.sensible(M.mask_to_polygon(sliver), BOX)


class FakeSeg:
    def __init__(self, fail=False):
        self.fail = fail

    def __call__(self, bgr, boxes):
        if self.fail:
            raise RuntimeError("mps fell over")
        return [M.mask_to_polygon(disc_mask()) for _ in boxes]


def test_outlines_falls_back_to_boxes():
    frame = np.zeros((120, 120, 3), np.uint8)
    assert M.outlines(None, frame, [BOX, BOX]) == [None, None]
    assert M.outlines(FakeSeg(fail=True), frame, [BOX]) == [None]
    assert M.outlines(FakeSeg(), frame, []) == []
    assert M.outlines(FakeSeg(), frame, [BOX])[0] is not None


def test_load_segmenter_without_weights_says_why_and_returns_none(monkeypatch):
    monkeypatch.setattr(M, "weights_here", lambda name=M.DEFAULT: False)
    said = []
    assert M.load_segmenter(log=said.append) is None
    assert "boxes only" in said[0] and "fetch_models" in said[0]
    said.clear()
    assert M.load_segmenter("nope", log=said.append) is None and "unknown" in said[0]


def test_render_draws_the_outline_not_the_box():
    poly = M.mask_to_polygon(disc_mask())
    rect = L.Rect(0, 0, 120, 120)
    with_mask = np.zeros((120, 120, 3), np.uint8)
    render_draw(with_mask, [{"label": "tube", "box": BOX, "score": 0.9, "outline": poly}], (120, 120), rect, Sprites())
    boxed = np.zeros((120, 120, 3), np.uint8)
    render_draw(boxed, [{"label": "tube", "box": BOX, "score": 0.9}], (120, 120), rect, Sprites())
    # the box's bottom-right corner is painted only when there is no outline
    assert boxed[89, 79].any() and not with_mask[89, 79].any()
    # the disc's centre is lightly filled only with the outline
    assert with_mask[60, 50].any() and not boxed[60, 50].any()


def test_live_draws_the_outline_not_the_box():
    poly = M.mask_to_polygon(disc_mask())
    a = np.zeros((120, 120, 3), np.uint8)
    live_draw(a, [{"label": "tube", "box": BOX, "score": None, "outline": poly}])
    b = np.zeros((120, 120, 3), np.uint8)
    live_draw(b, [{"label": "tube", "box": BOX, "score": None, "outline": None}])
    assert b[89, 79].any() and not a[89, 79].any()


def test_outline_follows_its_box_between_detections():
    poly = [[20, 30], [80, 30], [80, 90]]
    snaps = [(0.0, [{"label": "tube", "box": [20, 30, 80, 90], "score": 1, "outline": poly}]),
             (1.0, [{"label": "tube", "box": [30, 30, 90, 90], "score": 1, "outline": poly}])]
    mid = L.boxes_at(snaps, 0.5)[0]
    assert mid["box"][0] == pytest.approx(25)
    assert mid["outline"][0] == pytest.approx([25, 30])


SAMPLE = Path(os.environ.get("BENCH_SAMPLES", "/nonexistent")) / "lsv_mv" / "CP-Left-Front-249-269.mp4"


@pytest.mark.models
@pytest.mark.skipif(not SAMPLE.is_file() or not M.weights_here(), reason="needs BENCH_SAMPLES and EdgeTAM weights")
def test_edgetam_outlines_a_real_frame():
    import cv2
    cap = cv2.VideoCapture(str(SAMPLE))
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) * 0.7))
    ok, f = cap.read()
    f = cv2.resize(f, (640, 360))
    seg = M.load_segmenter()
    polys = seg(f, [[35, 265, 148, 358]])  # the tip box on the left
    assert seg.ms > 0
    assert polys[0] is not None and len(polys[0]) > 4
