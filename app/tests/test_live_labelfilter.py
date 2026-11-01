"""Live integration without models, cameras, network, threads or a speaker."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np

from app.live import LiveView, parse
from vision.core import Hand

PIP = {"label": "pipette", "box": [140, 130, 160, 300], "score": .8, "outline": None}
BOX = {"label": "tip box", "box": [200, 180, 320, 280], "score": .8, "outline": None}
HAND = Hand("left", box=(100, 100, 200, 200))
FRAME = np.zeros((360, 640, 3), np.uint8)
PROTOCOL = "Attach a fresh tip to the pipette"


class Detector:
    def __init__(self):
        self.v, self.t, self.ds = 0, None, []

    def submit(self, t, frame):
        pass

    def latest(self):
        return self.v, self.t, deepcopy(self.ds)

    def result(self, t, ds):
        self.v += 1
        self.t, self.ds = t, ds


class Vision:
    def __init__(self):
        self.raw = []

    def step(self, cam, t, w, h, hands, raw):
        self.raw.append(raw)
        return [{"kind": "tip_on", "detail": {"cam": 0}},
                {"kind": "objects", "detail": {"cam": 0, "boxes": [{**PIP, "held": True}, BOX]}},
                {"kind": "draw", "detail": {"hint": True}}]


def make(enabled=True, protocol=PROTOCOL):
    hub = SimpleNamespace(data={"run": None, "steps": [{"text": PROTOCOL, "status": None}]})
    sink = SimpleNamespace(events=[])
    sink.send = lambda events: sink.events.extend(events)
    view = LiveView(None, hands=lambda frame, t: [HAND], hub=hub, vision=Vision(), sink=sink,
                    filter_labels=enabled, protocol=protocol)
    view.det = Detector()
    return view


def observe(view, t, ds):
    view.det.result(t, ds)
    view.step(FRAME, t)


def test_default_preserves_labels_and_events_on_first_frame():
    v = make(False)
    observe(v, 0, [PIP, BOX])
    assert v.objects == [PIP, BOX]
    assert [e["kind"] for e in v.sink.events] == ["tip_on", "objects", "draw"]
    assert v.label_filter is None
    assert not parse(["--source", "0"]).filter_labels


def test_filter_applies_to_overlay_and_posted_objects_and_tip_event():
    v = make()
    for t in (0, .4):
        observe(v, t, [PIP, BOX])
        assert v.objects == []
    observe(v, .8, [PIP, BOX])
    assert len(v.objects) == 2
    assert len([e for e in v.sink.events if e["kind"] == "tip_on"]) == 1
    emitted = [e["detail"]["boxes"] for e in v.sink.events if e["kind"] == "objects"]
    assert emitted[:2] == [[], []] and len(emitted[-1]) == 2
    assert emitted[-1][0]["held"] is True
    assert "outline" not in emitted[-1][0]
    # Event engine keeps its original input: this is a post-filter, not retuning.
    assert all(len(ds) == 2 for ds in v.vision.raw)


def test_camera_frames_cannot_count_as_new_detections_and_stale_results_expire():
    v = make()
    observe(v, 0, [PIP, BOX])
    for t in (.1, .2, .3):
        v.step(FRAME, t)
        assert not v.objects
    observe(v, .4, [PIP, BOX])
    observe(v, .8, [PIP, BOX])
    assert v.objects
    v.step(FRAME, 2)
    assert not v.objects
    assert v.sink.events[-2]["detail"]["boxes"] == []


def test_hub_protocol_and_run_changes_reset_but_step_status_does_not():
    v = make(protocol=None)
    observe(v, 0, [PIP, BOX])
    v.hub.data["steps"][0]["status"] = "current"
    observe(v, .4, [PIP, BOX])
    observe(v, .8, [PIP, BOX])
    assert v.objects
    v.hub.data["run"] = "new-run"
    observe(v, 1.2, [PIP, BOX])
    assert not v.objects
    v.hub.data["steps"] = [{"text": "Read temperature"}]
    for t in (1.6, 2, 2.4):
        observe(v, t, [PIP, BOX])
    assert not v.objects


def test_missing_protocol_fails_closed_and_old_async_results_cannot_cross_reset():
    v = make(protocol=None)
    v.hub.data["steps"] = []
    for t in (0, .4, .8):
        observe(v, t, [PIP, BOX])
    assert not v.objects
    v.hub.data["steps"] = [{"text": PROTOCOL}]
    v.step(FRAME, 1)
    v.det.result(.9, [PIP, BOX])
    v.step(FRAME, 1.1)
    assert v.label_filter.last_t is None


def test_async_geometry_uses_hands_from_detector_frame_not_future_hand():
    v = make()
    v.hands_fn = lambda frame, t: [HAND] if t <= .8 else [Hand("left", box=(0, 0, 5, 5))]
    for t in (0, .4, .8):
        v.step(FRAME, t)  # original hand saved before delayed result arrives
        v.det.result(t, [PIP, BOX])
        v.step(FRAME, t + .15)
    assert len(v.objects) == 2  # future tiny hand would reject both by size
    assert not any(e["kind"] == "tip_on" for e in v.sink.events)  # hand moved away now


def test_posted_held_metadata_cannot_override_pipeline_rejection():
    v = make()
    class Unheld(Vision):
        def step(self, *args):
            events = super().step(*args)
            events[1]["detail"]["boxes"][0]["held"] = False
            return events
    v.vision = Unheld()
    for t in (0, .4, .8):
        observe(v, t, [PIP, BOX])
    boxes = [e["detail"]["boxes"] for e in v.sink.events if e["kind"] == "objects"][-1]
    assert next(b for b in boxes if b["label"] == "pipette")["held"] is False
