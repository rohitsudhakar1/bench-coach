import json
from pathlib import Path

from vision.core import Det
from vision.eval.score_tips import match
from vision.pipeline import Vision
from vision.tips import TipRules, TipTouch

TIP_BOX = Det("tip box", (100, 200, 200, 260), 0.4)


def pip(y2):
    """An upright pipette box whose lower end is at (150, y2)."""
    return Det("pipette", (140, y2 - 150, 160, y2), 0.4)


def run(tt, frames):
    out = []
    for t, dets in frames:
        out += tt.update(t, [], dets, fresh=True)
    return [e for e in out if e["kind"] != "objects"]


def test_tip_on_fires_when_the_pipette_leaves_a_steady_tip_box():
    tt = TipTouch(0)
    frames = [(i * 0.35, [TIP_BOX]) for i in range(4)]  # tip box seen in the same place
    frames += [(1.4, [TIP_BOX, pip(230)]), (1.75, [TIP_BOX, pip(232)]), (2.1, [TIP_BOX, pip(120)])]
    out = run(tt, frames)
    assert [e["kind"] for e in out] == ["tip_on"]
    assert abs(out[0]["t"] - 2.1) < 1e-9 and out[0]["object"] == "tip box"


def test_no_tip_on_from_a_box_seen_once():
    tt = TipTouch(0)
    out = run(tt, [(0.0, [TIP_BOX, pip(230)]), (0.35, [pip(120)]), (0.7, [pip(110)])])
    assert out == []


def test_no_tip_on_from_a_flat_pipette():
    tt = TipTouch(0)
    flat = Det("pipette", (60, 220, 240, 240), 0.4)  # wider than tall: lying across the box
    frames = [(i * 0.35, [TIP_BOX]) for i in range(4)] + [(1.4, [TIP_BOX, flat]), (1.75, [TIP_BOX])]
    assert run(tt, frames) == []


def test_tip_on_rearm():
    tt = TipTouch(0, TipRules(tip_rearm=5.0))
    frames = [(i * 0.35, [TIP_BOX]) for i in range(4)]
    t = 1.4
    for _ in range(3):  # three dips 0.7 s apart
        frames += [(t, [TIP_BOX, pip(230)]), (t + 0.35, [TIP_BOX, pip(100)])]
        t += 0.7
    assert [e["kind"] for e in run(tt, frames)] == ["tip_on"]


def test_vision_sends_anchor_tip_on_and_keeps_engine_touches():
    v = Vision(1)
    assert v.tip_kinds == ("tip_on",)
    off = Vision(1, tips=None)
    assert off.tips is None


def test_match_is_one_to_one():
    assert match([1.0, 1.1, 5.0], [1.05], tol=1.0) == 1
    assert match([1.0, 3.0], [1.5, 2.5], tol=1.0) == 2
    assert match([], [1.0]) == 0


def test_tip_labels_have_the_promised_counts():
    d = json.loads((Path(__file__).parent.parent / "eval" / "tips.json").read_text())
    clips = d["clips"].values()
    assert sum(len(c["tip_on"]) for c in clips) >= 30
    assert sum(len(c["touch"]) for c in clips) >= 30
    for c in clips:
        assert c["split"] in ("tune", "hold") and not c["path"].startswith("/")
