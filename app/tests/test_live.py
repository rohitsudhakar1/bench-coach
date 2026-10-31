import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.live import LiveView, Rate, Source, create_app, outline, summarise
from app.live_draw import COLORS, HAND_COLORS, overlay
from vision.core import Det, Hand


def frame(w=320, h=240, value=40):
    return np.full((h, w, 3), value, np.uint8)


def test_box_is_drawn_in_its_colour_and_the_input_is_untouched():
    f = frame()
    out = overlay(f, [{"label": "tube", "box": [50, 60, 150, 200], "score": 0.5, "outline": None}], [], scale=1.0)
    assert (f == 40).all()
    assert tuple(out[130, 50]) == COLORS["tube"]  # left edge of the box
    assert tuple(out[130, 100]) == (40, 40, 40)  # inside a box with no outline stays clear


def test_outline_is_filled_lightly_and_traced():
    f = frame()
    poly = [[100, 80], [200, 80], [200, 180], [100, 180]]
    out = overlay(f, [{"label": "plate", "box": [90, 70, 210, 190], "score": 0.9, "outline": poly}], [], scale=1.0)
    inside = out[130, 150].astype(int)
    assert inside.tolist() != [40, 40, 40]  # tinted
    assert abs(inside - 40).max() < 100  # but the picture still shows through
    assert tuple(out[130, 100]) == COLORS["plate"]  # the outline itself
    assert tuple(out[130, 92]) == (40, 40, 40)  # the box is not drawn when there is an outline


def test_label_stays_inside_the_picture():
    f = frame()
    out = overlay(f, [{"label": "pipette tip", "box": [300, 0, 319, 20], "score": 0.3, "outline": None}], [], scale=1.0)
    # the chip is pushed left and down so it is readable; its colour shows near the top right
    assert (out[0:20, 200:319] == COLORS["pipette tip"]).all(axis=2).any()


def test_hand_skeleton_and_box_only_hand():
    pts = [[100 + 3 * i, 100 + 2 * i] for i in range(21)]
    out = overlay(frame(), [], [{"side": "right", "pts": pts, "box": None},
                                {"side": "left", "pts": None, "box": [10, 150, 60, 220]}], scale=1.0)
    assert (out == HAND_COLORS["right"]).all(axis=2).sum() > 30
    assert tuple(out[185, 10]) == HAND_COLORS["left"]


def test_outline_finds_a_blob_inside_its_box():
    f = frame(400, 300, 90)
    cv2.circle(f, (200, 150), 50, (30, 200, 240), -1)
    poly = outline(f, (140, 90, 260, 210))
    assert poly is not None
    area = cv2.contourArea(np.asarray(poly, np.float32))
    assert 0.6 * np.pi * 50 ** 2 < area < 1.4 * np.pi * 50 ** 2
    assert outline(f, (10, 10, 14, 14)) is None  # too small to bother


def test_rate():
    r = Rate(window=10)
    for i in range(11):
        r.tick(100 + i * 0.1)
    assert r.value(101.0) == 10.0
    assert r.value(200) == 0.0


STATE = {"run": "r1", "current_step": 1, "question": "Did you draw from tube A?",
         "protocol": {"name": "Pipetting demo", "steps": [{"text": "Tip on"}, {"text": "Draw"}, {"text": "Dispense"}]},
         "steps": {"0": {"status": "done", "source": "seen"}, "1": {"status": "current"}},
         "chat": [{"kind": "speech", "t": 1, "detail": {"text": "done"}},
                  {"kind": "say", "t": 2, "detail": {"text": "Now draw from tube A."}}],
         "warnings": [{"kind": "warning", "detail": {"text": "Hold the pipette upright."}}]}


def test_summarise_hub_state():
    s = summarise(STATE)
    assert s["ok"] and s["current"] == 1 and s["protocol"] == "Pipetting demo"
    assert [st["status"] for st in s["steps"]] == ["done", "current", None]
    assert s["steps"][0]["evidence"] == "seen"
    assert s["coach"] == [{"who": "you", "text": "done", "t": 1}, {"who": "coach", "text": "Now draw from tube A.", "t": 2}]
    assert s["warnings"] == ["Hold the pipette upright."]
    assert summarise(None)["ok"] is False


class FakeHub:
    def __init__(self):
        self.data = summarise(STATE)

    def close(self):
        pass


def fake_detect(img):
    return [Det("tube", (40.0, 40.0, 100.0, 120.0), 0.6), Det("hand", (200.0, 50.0, 260.0, 120.0), 0.4)]


def make_view(tmp_path, detect=fake_detect):
    path = tmp_path / "clip.mp4"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (320, 240))
    for i in range(30):
        f = frame()
        cv2.circle(f, (70, 80), 25, (0, 200, 255), -1)
        w.write(f)
    w.release()
    hands = lambda img, t: [Hand("right", [[150 + i, 150 + i] for i in range(21)])]
    return LiveView(Source(str(path), realtime=True), detect, hands, FakeHub(), outlines=True)


def test_data_endpoint_and_picture(tmp_path):
    view = make_view(tmp_path)
    client = TestClient(create_app(view))
    assert client.get("/frame.jpg").status_code == 503  # nothing drawn yet
    assert "live.js" in client.get("/live").text
    view.start()
    deadline = time.time() + 10
    while time.time() < deadline and view.det.version == 0:
        time.sleep(0.05)
    time.sleep(0.2)
    r = client.get("/data")
    view.close()
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*"
    d = r.json()
    assert d["frame"]["width"] == 320 and d["frame"]["height"] == 240
    assert d["fps"]["picture"] > 0 and d["ms"]["detector"] is not None
    labels = sorted(o["label"] for o in d["objects"])
    assert labels == ["hand", "tube"]
    tube = next(o for o in d["objects"] if o["label"] == "tube")
    assert tube["outline"] and len(tube["outline"]) >= 3  # the drawn disc was outlined
    # MediaPipe's hand with points, plus the detector hand box no tracked hand explains (right half: right)
    assert sorted((h["side"], h["points"]) for h in d["hands"]) == [("right", False), ("right", True)]
    assert d["hub"]["steps"][1]["status"] == "current"
    jpg = client.get("/frame.jpg")
    assert jpg.status_code == 200 and cv2.imdecode(np.frombuffer(jpg.content, np.uint8), 1).shape == (240, 320, 3)


def test_file_plays_in_real_time_and_skips_when_slow(tmp_path):
    view = make_view(tmp_path, detect=None)
    src = view.src
    t0 = time.time()
    n = 0
    while src.read() is not None:
        n += 1
        if n == 5:
            time.sleep(0.3)  # a slow moment: about nine frames must be dropped, not shown late
    took = time.time() - t0
    assert 0.9 < took < 1.4  # 30 frames at 30 fps
    assert src.skipped >= 6 and n + src.skipped == 30
    src.close()


def test_missing_camera_falls_back_to_the_clip(tmp_path):
    good = make_view(tmp_path, detect=None)
    clip = good.src.spec
    good.src.close()
    view = LiveView(Source(str(tmp_path / "no-such.mp4")), None, None, FakeHub(), fallback=clip)
    assert not view.src.ok
    view.start()
    deadline = time.time() + 5
    while time.time() < deadline and view.version == 0:
        time.sleep(0.05)
    snap = view.snapshot()
    view.close()
    assert snap["source"]["fallback"] and snap["frame"]["width"] == 320


def test_hub_live_route_points_at_the_live_view(tmp_path, monkeypatch):
    from app.hub import create_app as hub_app
    monkeypatch.setenv("LIVE_URL", "http://127.0.0.1:8899/")
    r = TestClient(hub_app(tmp_path)).get("/live", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "http://127.0.0.1:8899/"


SAMPLE = Path(os.environ.get("BENCH_SAMPLES", "/nonexistent")) / "lsv_mv/CP-Left-Front-414-445.mp4"


@pytest.mark.skipif(not SAMPLE.exists(), reason="BENCH_SAMPLES clip missing")
def test_sample_clip_keeps_full_frame_rate_with_a_slow_detector():
    def slow(img):
        time.sleep(0.2)  # slower than the real model
        return [Det("tube", (10.0, 10.0, 80.0, 90.0), 0.5)]
    view = LiveView(Source(str(SAMPLE), realtime=True), slow, None, FakeHub()).start()
    time.sleep(4)
    snap = view.snapshot()
    view.close()
    assert snap["fps"]["picture"] >= 24  # the clip is 30 fps; the picture does not wait for the model
    assert 2 <= snap["fps"]["detections"] <= 6


def test_overlapping_labels_are_moved_apart():
    objs = [{"label": "tube", "box": [50, 60, 150, 200], "score": 0.5, "outline": None},
            {"label": "plate", "box": [55, 62, 150, 200], "score": 0.5, "outline": None}]
    out = overlay(frame(), objs, [], scale=1.0)
    # both chips are visible: each colour fills a run of pixels above or just inside the boxes
    assert (out[40:100, 50:150] == COLORS["tube"]).all(axis=2).sum() > 200
    assert (out[40:100, 50:150] == COLORS["plate"]).all(axis=2).sum() > 200
