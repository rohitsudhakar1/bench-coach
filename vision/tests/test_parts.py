import io
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from vision.core import Det, sample
from vision.detector import canonical, PROMPTS, pick_device
from vision.hands import glove_to_skin, merge_hands
from vision.pipeline import Vision, to_source_pixels
from vision.sink import Sink

ROOT = Path(__file__).resolve().parents[2]


class Hub:
    def __init__(self, ok=True):
        self.ok, self.posts = ok, []

    def post(self, url, json):
        if not self.ok:
            raise ConnectionError
        self.posts.append(json)
        return type("R", (), {"status_code": 200})()

    def close(self):
        pass


def test_sink_posts_or_prints():
    out = io.StringIO()
    hub = Hub()
    Sink("http://x", client=hub, out=out).send([{"kind": "tip_on"}])
    assert hub.posts == [[{"kind": "tip_on"}]] and out.getvalue() == ""
    s = Sink("http://x", client=Hub(ok=False), out=out)
    s.send([{"kind": "tip_on"}])
    s.send([{"kind": "objects"}])  # hub marked down: printed without trying
    assert [json.loads(x)["kind"] for x in out.getvalue().splitlines()] == ["tip_on", "objects"]


def test_labels_map_to_names():
    assert canonical("pipette tip", PROMPTS) == "pipette tip"
    assert canonical("tip", PROMPTS) == "pipette tip"
    assert canonical("waste bin", PROMPTS) == "waste"
    assert canonical("banana", PROMPTS) is None


def test_device_from_env(monkeypatch):
    monkeypatch.setenv("VISION_DEVICE", "cpu")
    assert pick_device() == "cpu"


def test_blue_glove_painted_skin():
    img = np.zeros((4, 4, 3), np.uint8)
    img[:] = (200, 80, 40)  # BGR blue glove
    b, g, r = glove_to_skin(img)[0, 0]
    assert r > b


def test_box_only_hands_fill_gaps():
    hs = merge_hands([], [(0, 0, 50, 50), (500, 0, 550, 50)], 640)
    assert [h.side for h in hs] == ["left", "right"] and hs[0].pts is None


def test_boxes_scaled_to_camera_size():
    ev = [{"kind": "objects", "detail": {"boxes": [{"label": "tube", "box": [10, 20, 30, 40], "score": 0.5}]}}]
    to_source_pixels(ev, (360, 640, 3), (720, 1280, 3), 5.0)
    assert ev[0]["detail"]["boxes"][0]["box"] == [20, 40, 60, 80]
    assert ev[0]["detail"]["width"] == 1280 and ev[0]["detail"]["height"] == 720


def test_pipeline_new_draw_marks_hints():
    v = Vision(1, new_draw=True)
    assert v.engines[0].r.press_events is False
    assert Vision(1).engines[0].r.press_events is True
    raw = [Det("container:tube", (90, 280, 130, 330), 0.5)]
    out = []
    from vision.tests.test_draw import dip_path
    from vision.tests.helpers import hand_at
    for t, box in dip_path(110):
        x = (box[0] + box[2]) / 2
        out += v.step(0, t, 640, 480, [hand_at(x, box[1] + 10)], raw + [Det("pipette", box, 0.5)])
    out += v.finish()
    hints = [e for e in out if e["kind"] in ("draw", "dispense")]
    assert len(hints) == 1 and hints[0]["detail"]["hint"] is True


@pytest.mark.skipif(sample("lsv_mv/CP-Left-Front-414-445.mp4") is None, reason="BENCH_SAMPLES not set")
def test_cli_runs_alone_on_two_files():
    a = sample("lsv_mv/CP-Left-Front-414-445.mp4")
    b = sample("lsv_mv/CP-Right-Front-414-445.mp4")
    r = subprocess.run([sys.executable, "-m", "vision", "--source", str(a), str(b), "--hub", "none", "--port", "0",
                        "--no-detector", "--seconds", "3"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "# vision done" in r.stdout and "cam 1:" in r.stdout
