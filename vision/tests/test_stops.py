"""Synthetic mechanics and integration checks, not physical stop accuracy tests."""
import copy
import math

import pytest

from vision.core import Det, Hand, event
from vision.stops import Press, PressTracker, StopEstimator, classify, thumb_observation
from vision.tests.helpers import hand_at


def trace(depths, dt=0.1):
    return [(round(i * dt, 6), d) for i, d in enumerate(depths)]


def track(depths, dt=0.1):
    tracker = PressTracker()
    for t, depth in trace(depths, dt):
        tracker.update(t, None if depth is None else 1 - depth)
    tracker.finish()
    return list(tracker.presses)


@pytest.mark.parametrize("depths,expected", [
    ([0.3] * 6, "first"),
    ([0.3] * 5 + [0.45, 0.6] + [0.6] * 5, "second"),
    ([0.3, 0.31, 0.29, 0.3, 0.31, 0.3], "first"),
    ([0.3, 0.31, 0.29, 0.3, 0.31] + [0.48] + [0.6, 0.61, 0.59, 0.6, 0.61], "second"),
    ([0.1] * 10, "unknown"),
    ([0.6] * 10, "unknown"),  # depth alone is not a calibrated second stop
    ([0.2, 0.28, 0.36, 0.44, 0.52, 0.60], "unknown"),
    ([0.3] * 5 + [0.1] * 2 + [0.6] * 5, "unknown"),  # rebound, not continuation
    ([1.5] * 5, "unknown"),
])
def test_stop_patterns(depths, expected):
    assert classify(trace(depths))[0] == expected


@pytest.mark.parametrize("dt", [0.05, 0.1])
def test_press_depth_and_two_stage_release(dt):
    rest = [0] * round(0.8 / dt)
    first = [0.3] * round(0.5 / dt)
    second = [0.6] * round(0.5 / dt)
    presses = track(rest + first + second + rest, dt)
    assert len(presses) == 1
    assert presses[0].stop == "second"
    assert presses[0].depth == pytest.approx(0.6)


def test_separate_presses_and_rearm():
    presses = track([0] * 8 + [0.3] * 6 + [0] * 6 + [0.3] * 6 + [0] * 6)
    assert [p.stop for p in presses] == ["first", "first"]
    assert presses[0].end < presses[1].start


def test_one_frame_spike_is_not_a_press():
    assert track([0] * 8 + [0.8] + [0] * 8) == []


def test_missing_frames_during_press_make_unknown():
    p, = track([0] * 8 + [0.3] * 5 + [None] + [0.6] * 5 + [0] * 8)
    assert p.stop == "unknown"
    assert p.reason == "incomplete_or_occluded"


def test_missing_before_press_needs_new_baseline():
    p, = track([None] * 10 + [0] * 8 + [0.3] * 6 + [0] * 6)
    assert p.stop == "first"


def test_eof_does_not_call_first_before_second_is_visible():
    p, = track([0] * 8 + [0.3] * 6)
    assert p.stop == "unknown"


def test_timestamp_gap_is_missing_evidence():
    tracker = PressTracker()
    for t, d in trace([0] * 8 + [0.3] * 6):
        tracker.update(t, 1 - d)
    for t, d in trace([0.6] * 5 + [0] * 6):
        tracker.update(t + 2, 1 - d)
    assert tracker.presses[0].stop == "unknown"


def test_clock_restart_discards_old_press():
    tracker = PressTracker()
    for t, d in trace([0] * 8 + [0.3] * 6):
        tracker.update(t, 1 - d)
    tracker.update(0, 1)
    assert not tracker.presses and tracker.active is None


def test_hand_switch_does_not_combine_presses():
    tracker = PressTracker()
    for t, d in trace([0] * 8 + [0.3] * 6):
        tracker.update(t, 1 - d, "right")
    tracker.update(1.4, 0.4, "left")
    assert tracker.presses[0].stop == "unknown"
    assert tracker.active is None


@pytest.mark.parametrize("ratio", [None, math.nan, math.inf, -1, 5])
def test_invalid_observation(ratio):
    tracker = PressTracker()
    for t, d in trace([0] * 8 + [0.3] * 6):
        tracker.update(t, 1 - d)
    tracker.update(1.4, ratio)
    tracker.finish()
    assert tracker.presses[0].stop == "unknown"


def test_box_hand_stale_unheld_and_tiny_are_not_depth():
    pip = Det("pipette", (100, 180, 120, 300), 0.5)
    hand = hand_at(110, 190)
    assert thumb_observation(hand, pip, True, 0.1) == pytest.approx(0.8)
    assert thumb_observation(hand, pip, False, 0.1) is None
    assert thumb_observation(hand, pip, True, 0.7) is None
    assert thumb_observation(Hand("right", box=(100, 100, 150, 150)), pip, True, 0) is None
    assert thumb_observation(hand_at(110, 190, s=3), pip, True, 0) is None
    assert thumb_observation(hand_at(500, 500), pip, True, 0) is None


def dispense(t=1.2, cam=0, detail=None):
    return event(t, "dispense", cam, "tube", "right", 0.5, detail)


def test_delayed_event_preserves_fields_and_waits_for_second_stop():
    estimator = StopEstimator(1)
    e = dispense()
    original = copy.deepcopy(e)
    for t, d in trace([0] * 8 + [0.3] * 5 + [0.6] * 5 + [0] * 6):
        estimator.trackers[0].update(t, 1 - d)
        assert estimator.process([e] if t == 1.2 else [], t) == []
    result, = estimator.process([], 3.2)
    assert result["detail"].pop("stop") == "second"
    assert result == original


def test_pass_through_unknown_and_final_drain():
    estimator = StopEstimator(1)
    draw = event(1, "draw", 0)
    e = dispense()
    assert estimator.process([draw, e], 1.2) == [draw]
    result, = estimator.process([], 1.2, final=True)
    assert result["detail"]["stop"] == "unknown"
    assert estimator.process([], 5) == []


@pytest.mark.parametrize("labels,want", [(["first", "first"], "first"),
                                        (["first", "second"], "unknown"),
                                        (["second", "unknown"], "unknown")])
def test_camera_votes(labels, want):
    estimator = StopEstimator(2)
    for cam, label in enumerate(labels):
        estimator.trackers[cam].presses.append(Press(1, 2, "right", 0.3, label, "test"))
    result, = estimator.process([dispense(detail={"cams": [0, 1], "start": 0.8, "end": 2.1})], 5)
    assert result["detail"]["stop"] == want


def test_no_reuse_and_no_future_or_multiple_presses():
    estimator = StopEstimator(1)
    estimator.trackers[0].presses.append(Press(1, 2, "right", 0.3, "first", "test"))
    a, = estimator.process([dispense()], 5)
    assert a["detail"]["stop"] == "first"
    b, = estimator.process([dispense(1.3)], 5)
    assert b["detail"]["stop"] == "unknown"
    c, = estimator.process([dispense(0.5)], 5)
    assert c["detail"]["stop"] == "unknown"
    estimator.trackers[0].presses.append(Press(2.5, 3, "right", 0.3, "first", "test"))
    d, = estimator.process([dispense(3, detail={"start": 0.5, "end": 3})], 5)
    assert d["detail"]["stop"] == "unknown"


def test_pipeline_default_unchanged_and_opt_in():
    from vision.pipeline import Vision
    from vision.scene import SceneRules
    from vision.__main__ import parse
    assert not parse(["--source", "sample.mp4"]).stops
    assert parse(["--source", "sample.mp4", "--stops"]).stops
    baseline = Vision(1, tips=None, scene=SceneRules(static_secs=100))
    opt_in = Vision(1, tips=None, scene=SceneRules(static_secs=100), stops=True)
    outputs = [[], []]
    # A draw first, then a two-stage dispense. The first stop lasts long enough
    # that the original engine emits before the later second plateau.
    depths = [0] * 15 + [0.3] * 5 + [0] * 10 + [0.3] * 5 + [0.6] * 5 + [0] * 25
    for t, depth in trace(depths):
        hand = hand_at(110, 190)
        hand.pts[4] = (105, 190 + (0.8 - depth) * 40)
        raw = [Det("pipette", (100, 180, 120, 300), 0.5), Det("tube", (90, 280, 130, 330), 0.5)]
        for i, v in enumerate((baseline, opt_in)):
            outputs[i] += v.step(0, t, 640, 480, [hand], raw)
    for i, v in enumerate((baseline, opt_in)):
        outputs[i] += v.finish()
    dispenses = [e for e in outputs[1] if e["kind"] == "dispense"]
    assert len(dispenses) == 1
    assert dispenses[0]["detail"]["stop"] == "second"
    assert all("stop" not in e["detail"] for e in outputs[0])
    for e in outputs[1]:
        e["detail"].pop("stop", None)
    assert sorted(outputs[0], key=lambda e: (e["t"], e["kind"])) == sorted(outputs[1], key=lambda e: (e["t"], e["kind"]))


def test_async_old_detector_frame_is_not_fresh_stop_evidence():
    from vision.pipeline import Vision
    vis = Vision(1, stops=True)
    hand = hand_at(110, 190)
    pip = Det("pipette", (100, 180, 120, 300), 0.5)
    for t, _ in trace([0] * 20):
        vis.step(0, t, 640, 480, [hand], [pip], raw_t=t - 1)
    assert vis.stops.trackers[0].rest is None
    assert not vis.stops.trackers[0].smooth


def test_depth_is_invariant_to_translation_and_scale():
    pip = Det("pipette", (100, 180, 120, 300), 0.5)
    hand = hand_at(110, 190)
    expected = thumb_observation(hand, pip, True, 0)
    scaled = Hand("right", [(x * 2 + 300, y * 2 + 100) for x, y in hand.pts])
    moved_pip = Det("pipette", (500, 460, 540, 700), 0.5)
    assert thumb_observation(scaled, moved_pip, True, 0) == pytest.approx(expected)


def test_stalled_press_times_out_unknown():
    p, = track([0] * 8 + [0.3] * 80)
    assert p.stop == "unknown"


def test_cli_source_to_stdout_with_synthetic_model_outputs(tmp_path, monkeypatch, capsys):
    """Real video decode + CLI + rules + JSON sink; model outputs are fabricated."""
    import cv2
    import json
    import numpy as np
    import signal
    from vision import hands, zoo
    from vision.__main__ import main

    depths = [0] * 15 + [0.3] * 5 + [0] * 10 + [0.3] * 5 + [0.6] * 5 + [0] * 25
    path = tmp_path / "trace.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (640, 480))
    assert writer.isOpened()
    for i in range(len(depths)):
        writer.write(np.full((480, 640, 3), i, dtype=np.uint8))
    writer.release()

    class Hands:
        def __init__(self, **kw):
            self.closed = False
            self.i = 0
            created.append(self)

        def __call__(self, frame, t):
            i = self.i
            self.i += 1
            h = hand_at(110 + 2 * i, 190)
            h.pts[4] = (105 + 2 * i, 190 + (0.8 - depths[i]) * 40)
            return [h]

        def close(self):
            self.closed = True

    class Detector:
        def __call__(self, frame):
            x = 110 + 2 * round(float(frame.mean()))
            return [Det("pipette", (x - 10, 180, x + 10, 300), 0.5),
                    Det("tube", (x - 20, 280, x + 20, 330), 0.5)]

    created = []
    monkeypatch.setattr(hands, "HandTracker", Hands)
    monkeypatch.setattr(zoo, "make", lambda name: Detector())
    old_signals = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        main(["--source", str(path), "--stops", "--hub", "none", "--port", "0", "--no-verify"])
    finally:
        for s, handler in old_signals.items():
            signal.signal(s, handler)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    dispenses = [e for e in events if e["kind"] == "dispense"]
    assert len(dispenses) == 1
    assert dispenses[0]["detail"]["stop"] == "second"
    assert dispenses[0]["t"] > 1_000_000_000
    assert all(h.closed for h in created)


@pytest.mark.parametrize("segment", ["12-27", "249-269", "414-445"])
def test_sample_replay_preserves_events(segment):
    from vision.core import sample
    from vision.eval.stops import measure
    paths = [f"lsv_mv/CP-{side}-Front-{segment}.mp4" for side in ("Left", "Right")]
    if not all(sample(p) for p in paths):
        pytest.skip("BENCH_SAMPLES or CP clips missing")
    result = measure(paths, new_draw=True)
    assert result["events_unchanged_except_stop_and_delivery_order"]
    assert sum(result["labels"].values()) == result["dispenses"]
