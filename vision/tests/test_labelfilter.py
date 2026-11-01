"""Synthetic streams: rules, causal evidence, multi-object/camera isolation."""
from copy import deepcopy

import pytest

from vision.core import Det, Hand
from vision.labelfilter import LabelFilter, LIMITS, filter_analysis, protocol_classes

HAND = Hand("left", box=(100, 100, 200, 200))
PIP = {"label": "pipette", "box": [140, 130, 160, 300], "score": .8}
BOX = {"label": "tip box", "box": [200, 180, 320, 280], "score": .8}
PROTOCOL = "Attach a fresh tip to a pipette from the tip box. Draw from tube A."


def run(f, ds, times=(0, .4, .8), hands=(HAND,)):
    out = []
    for t in times:
        out = f.update(t, ds, hands)
    return out


@pytest.mark.parametrize("text,expected", [
    ("Tube rack; tip boxes", {"tube rack", "tip box"}),
    ("Pipette tip, tube A, well plate and waste bin", {"pipette tip", "tube", "plate", "waste"}),
    ("Fit a fresh tip", {"pipette", "pipette tip", "tip box"}),
    ("Grab 1000uL pipet and attach 1000uL pipet tip", {"pipette", "pipette tip", "tip box"}),
    ("Check temperature", set()),
    ("Wash fingertips; no equipment names", set()),
])
def test_protocol_lines(text, expected):
    assert protocol_classes(text) == expected


def test_protocol_metadata_cannot_enable_classes():
    assert protocol_classes({"name": "waste plate", "description": "tip box", "steps": [
        {"text": "Continue", "expect": [{"kind": "draw", "object": "tube A"}]}]}) == {"tube"}
    assert protocol_classes({"steps": [{"text": "Continue", "expect": [{"kind": "tip_on"}]}]}) == {
        "pipette", "pipette tip", "tip box"}


def test_only_protocol_classes_but_hands_always_pass():
    f = LabelFilter("tube")
    hs = {"label": "hand", "box": list(HAND.box), "score": .9}
    assert run(f, [PIP, BOX, hs]) == [hs]
    assert f.rejected["protocol"] == 6


@pytest.mark.parametrize("label", list(LIMITS))
def test_each_class_has_relative_size_and_aspect_limits(label):
    f = LabelFilter("pipette pipette tip tip box tube tube rack plate waste")
    lo, hi, alo, ahi, slo, shi = LIMITS[label]
    assert not f.geometry(label, [0, 0, .001, .001], [HAND.box])
    assert not f.geometry(label, [0, 0, 10000, 10000], [HAND.box])
    assert not f.geometry(label, [0, 0, 100, .001], [HAND.box])
    assert f.geometry(label, [0, 0, 120, 80], [HAND.box])
    # Resizing both the camera and hand leaves every decision unchanged.
    assert f.geometry(label, [0, 0, 240, 160], [[v * 2 for v in HAND.box]])


def test_no_hand_does_not_guess_scale_and_invalid_boxes_are_dropped():
    assert run(LabelFilter(PROTOCOL), [PIP], hands=[]) == []
    for box in ([0, 0, 0, 0], [5, 5, 0, 0], [0, 0, float("nan"), 5]):
        assert run(LabelFilter(PROTOCOL), [{**PIP, "box": box}]) == []


def test_three_distinct_consistent_frames_not_duplicate_timestamps():
    f = LabelFilter(PROTOCOL)
    for _ in range(10):
        assert f.update(0, [PIP], [HAND]) == []
    assert f.update(.4, [PIP], [HAND]) == []
    assert f.update(.8, [PIP], [HAND])[0]["held"] is True
    assert f.visible(1) and not f.visible(1.81)


def test_dropout_resets_streak_and_does_not_draw_missing_object():
    f = LabelFilter(PROTOCOL)
    assert run(f, [PIP])
    assert f.update(1.2, [], [HAND]) == []
    assert f.update(1.6, [PIP], [HAND]) == []
    assert f.update(2.0, [PIP], [HAND]) == []
    assert f.update(2.4, [PIP], [HAND])


def test_gap_and_rewind_reset_evidence():
    f = LabelFilter(PROTOCOL)
    assert run(f, [PIP])
    assert f.update(5, [PIP], [HAND]) == []
    assert f.update(0, [PIP], [HAND]) == []
    assert f.update(.4, [PIP], [HAND]) == []


def test_majority_vote_changes_after_three_new_votes_not_one():
    f = LabelFilter("pipette and tube")
    assert run(f, [PIP])[0]["label"] == "pipette"
    tube = {**PIP, "label": "tube"}
    assert f.update(1.2, [tube], [HAND])[0]["label"] == "pipette"
    assert f.update(1.6, [tube], [HAND])[0]["label"] == "pipette"
    assert f.update(2, [tube], [HAND])[0]["label"] == "tube"


def test_tracks_match_by_overlap_not_input_order_and_do_not_share_votes():
    f = LabelFilter("pipette and tube")
    tube = {**PIP, "label": "tube", "box": [250, 130, 270, 300]}
    for i in range(3):
        out = f.update(i * .4, [PIP, tube] if i % 2 else [tube, PIP], [HAND])
    assert {o["label"]: o["box"] for o in out} == {"pipette": PIP["box"], "tube": tube["box"]}
    assert LabelFilter(PROTOCOL).update(.8, [PIP], [HAND]) == []


def test_two_boxes_in_one_frame_cannot_count_as_two_frames():
    assert run(LabelFilter(PROTOCOL), [PIP, PIP], times=(0, .4)) == []


def test_smoothing_rechecks_geometry_under_winning_label():
    f = LabelFilter("pipette and tube")
    assert run(f, [PIP])
    # Broader aspect is legal for tube, illegal for the prior pipette label.
    box = [100, 130, 230, 260]
    f.match_iou = .1
    assert f.update(1.2, [{**PIP, "label": "tube", "box": box}], [HAND]) == []


def test_tip_attach_needs_body_and_box_by_same_hand():
    f = LabelFilter(PROTOCOL)
    run(f, [PIP, BOX])
    assert f.allows_tip_attach(.8)
    assert not f.allows_tip_attach(2)
    assert not f.allows_tip_attach(.8, [])
    for ds in ([PIP], [BOX], [{**PIP, "label": "pipette tip"}, BOX]):
        f = LabelFilter(PROTOCOL)
        run(f, ds)
        assert not f.allows_tip_attach(.8)
    f = LabelFilter(PROTOCOL)
    far = {**BOX, "box": [500, 180, 620, 280]}
    right = Hand("right", box=(500, 100, 600, 200))
    run(f, [PIP, far], hands=[HAND, right])
    assert not f.allows_tip_attach(.8)


def test_unheld_body_and_one_frame_hand_pass_cannot_attach():
    f = LabelFilter(PROTOCOL)
    far_hand = Hand("left", box=(0, 0, 100, 100))
    run(f, [PIP, BOX], hands=[far_hand])
    assert not f.allows_tip_attach(.8)
    f.update(1.2, [PIP, BOX], [HAND])
    assert not f.allows_tip_attach(1.2)


def test_input_metadata_and_events_are_not_mutated():
    f = LabelFilter(PROTOCOL)
    ds = [PIP, BOX]
    before = deepcopy(ds)
    run(f, ds)
    assert ds == before
    events = [{"kind": "tip_on", "detail": {}}, {"kind": "draw", "detail": {"hint": True}}]
    assert f.filter_events(events, .8) == events
    assert f.filter_events(events, 5) == events[1:]
    assert run(LabelFilter(PROTOCOL), [Det("pipette", tuple(PIP["box"]), .8)])


def test_render_filter_is_causal_per_camera_and_keeps_raw_cache():
    ds = [PIP, BOX]
    hs = [{"side": "left", "box": list(HAND.box)}]
    event = lambda s, cam: {"kind": "tip_on", "t": 100 + s, "s": s, "detail": {"cam": cam}}
    analysis = {"t0": 100, "snaps": [[(t, ds) for t in (0, .4, .8)], [(0, ds)]],
                "hands": [[(t, hs) for t in (0, .4, .8)], [(0, hs)]],
                "events": [event(.4, 0), event(.8, 0), event(.8, 1), event(3, 0)]}
    before = deepcopy(analysis)
    out = filter_analysis(analysis, PROTOCOL)
    assert analysis == before
    assert out["events"] == [event(.8, 0)]
    assert out["snaps"][0][0][1] == []
    assert len(out["snaps"][0][2][1]) == 2
    assert out["snaps"][0][-1][1] == []
    assert filter_analysis(analysis, PROTOCOL) == out


def test_existing_unheld_qualification_is_never_overridden():
    f = LabelFilter(PROTOCOL)
    out = run(f, [{**PIP, "held": False}, BOX])
    assert next(d for d in out if d["label"] == "pipette")["held"] is False
    assert not f.allows_tip_attach(.8)
