"""Layout, panel timeline and smoothing for scripts/render_demo.py. No video or model needed."""
import pytest

from scripts import render_layout as L


def inside(r, outer):
    return outer.x <= r.x and outer.y <= r.y and r.x2 <= outer.x2 and r.y2 <= outer.y2


def overlap(a, b):
    return not (a.x2 <= b.x or b.x2 <= a.x or a.y2 <= b.y or b.y2 <= a.y)


@pytest.mark.parametrize("sizes", [[(1280, 720)], [(1280, 720), (1280, 720)], [(1920, 1080), (1280, 720)],
                                   [(720, 1280)], [(1280, 720), (640, 480)]])
def test_layout_fills_1080p_without_overlap(sizes):
    lay = L.layout(sizes)
    frame = L.Rect(0, 0, L.W, L.H)
    rects = lay.cams + [lay.panel, lay.feed]
    for r in rects:
        assert inside(r, frame)
        assert r.w % 2 == 0 and r.h % 2 == 0 or r in (lay.panel, lay.feed)
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not overlap(a, b)


def test_cameras_keep_their_shape():
    lay = L.layout([(1280, 720), (1280, 720)])
    big, small = lay.cams
    assert (big.w, big.h) == (1280, 720)      # Rohit's Pi footage and the phone clips: no scaling up
    assert (small.w, small.h) == (640, 360)
    portrait = L.layout([(720, 1280)]).cams[0]
    assert abs(portrait.w / portrait.h - 720 / 1280) < 0.01


def test_layout_rejects_three_cameras():
    with pytest.raises(ValueError):
        L.layout([(1280, 720)] * 3)


def test_scale_box_maps_source_pixels_into_the_camera_rect():
    r = L.Rect(0, 720, 640, 360)
    assert L.scale_box((0, 0, 1280, 720), 1280, 720, r) == (0, 720, 640, 1080)
    assert L.scale_box((640, 360, 640, 360), 1280, 720, r) == (320, 900, 320, 900)


def ev(s, kind, **detail):
    return {"s": s, "kind": kind, "detail": detail}


STEPS = ["Attach a tip", "Draw from tube A", "Dispense into tube B"]


def test_steps_tick_off_at_their_time():
    tl = L.Timeline(STEPS, [ev(5.0, "step", index=0, status="done", source="seen"),
                            ev(5.0, "step", index=1, status="current"),
                            ev(12.0, "step", index=1, status="skipped"),
                            ev(12.0, "step", index=2, status="unsure")])
    at0 = tl.at(0.0)
    assert [s.status for s in at0.steps] == ["current", "pending", "pending"]
    at6 = tl.at(6.0)
    assert [s.status for s in at6.steps] == ["done", "current", "pending"]
    assert at6.steps[0].at == 5.0 and at6.steps[0].how == "seen"
    assert [s.status for s in tl.at(20).steps] == ["done", "skipped", "unsure"]


def test_events_out_of_order_are_sorted():
    tl = L.Timeline(STEPS, [ev(9.0, "step", index=0, status="skipped"), ev(3.0, "step", index=0, status="done")])
    assert tl.at(4).steps[0].status == "done"
    assert tl.at(10).steps[0].status == "skipped"


def test_warning_shows_for_a_while_then_stays_in_the_list():
    tl = L.Timeline(STEPS, [ev(10.0, "warning", text="Same tip used twice.")])
    assert tl.at(9.9).live_warning is None
    assert tl.at(10.0).live_warning == (10.0, "Same tip used twice.")
    later = tl.at(10.0 + L.WARN_SECONDS + 0.1)
    assert later.live_warning is None
    assert later.warnings == [(10.0, "Same tip used twice.")]


def test_repeated_warning_is_listed_once_and_say_of_same_text_is_hidden():
    tl = L.Timeline(STEPS, [ev(10.0, "warning", text="Tilted."), ev(10.0, "say", text="Tilted."),
                            ev(11.0, "warning", text="Tilted.")])
    st = tl.at(11.5)
    assert len(st.warnings) == 1
    assert st.live_say is None and st.live_warning is not None


def test_coach_line_expires():
    tl = L.Timeline(STEPS, [ev(1.0, "say", text="First: attach a tip.")])
    assert tl.at(2).live_say == (1.0, "First: attach a tip.")
    assert tl.at(1.0 + L.SAY_SECONDS + 0.1).live_say is None


def test_hand_labelled_steps_are_marked():
    tl = L.Timeline(STEPS, [ev(4.0, "step", index=0, status="done", source="seen", labelled=True)])
    st = tl.at(5)
    assert st.steps[0].how == "hand-labelled"
    assert st.labelled


def test_bad_step_index_is_ignored():
    tl = L.Timeline(STEPS, [ev(1.0, "step", index=7, status="done"), ev(1.0, "step", index=None, status="done")])
    assert [s.status for s in tl.at(2).steps] == ["current", "pending", "pending"]


def test_panel_key_changes_only_when_something_drawn_changes():
    tl = L.Timeline(STEPS, [ev(5.0, "step", index=0, status="done")])
    assert tl.at(1.1).key() == tl.at(1.9).key()
    assert tl.at(1.9).key() != tl.at(2.1).key()      # the clock ticked
    assert tl.at(4.99).key() != tl.at(5.0).key()


def test_boxes_move_smoothly_between_detector_runs():
    snaps = [(0.0, [{"label": "tube", "box": (0, 0, 10, 10), "score": 0.5}]),
             (1.0, [{"label": "tube", "box": (4, 0, 14, 10), "score": 0.5},
                    {"label": "pipette", "box": (50, 50, 60, 90), "score": 0.4}])]
    mid = L.boxes_at(snaps, 0.5)
    assert len(mid) == 1 and mid[0]["box"] == pytest.approx((2, 0, 12, 10))
    assert L.boxes_at(snaps, -1) == []
    assert len(L.boxes_at(snaps, 1.5)) == 2


def test_boxes_do_not_jump_to_another_label_or_far_box():
    snaps = [(0.0, [{"label": "tube", "box": (0, 0, 10, 10), "score": 0.5}]),
             (1.0, [{"label": "plate", "box": (0, 0, 10, 10), "score": 0.5},
                    {"label": "tube", "box": (100, 100, 110, 110), "score": 0.5}])]
    assert L.boxes_at(snaps, 0.5)[0]["box"] == (0, 0, 10, 10)


def hand(side, x):
    return {"side": side, "pts": [(x, 0.0)] * 21, "box": None}


def test_hand_points_are_blended_between_close_samples():
    samples = [(0.0, [hand("left", 0.0)]), (0.1, [hand("left", 10.0)])]
    got = L.hands_at(samples, 0.05)
    assert len(got) == 1 and got[0]["pts"][0][0] == pytest.approx(5.0)


def test_a_hand_seen_once_does_not_linger():
    samples = [(0.0, [hand("left", 0.0)]), (2.0, [])]
    assert L.hands_at(samples, 0.1)
    assert L.hands_at(samples, 1.0) == []


def test_hand_that_disappears_fades_at_the_midpoint():
    samples = [(0.0, [hand("left", 0.0), hand("right", 50.0)]), (0.1, [hand("left", 1.0)])]
    assert {h["side"] for h in L.hands_at(samples, 0.02)} == {"left", "right"}
    assert {h["side"] for h in L.hands_at(samples, 0.08)} == {"left"}


def test_wrap_keeps_words_whole_and_within_width():
    lines = L.wrap("Dispense into flask without touching any solid or liquid objects", 20, len)
    assert all(len(x) <= 20 for x in lines)
    assert " ".join(lines) == "Dispense into flask without touching any solid or liquid objects"
    assert L.wrap("", 10, len) == []
    assert L.wrap("averyveryverylongword", 5, len) == ["averyveryverylongword"]


def test_clock():
    assert L.clock(0) == "0:00" and L.clock(75.9) == "1:15" and L.clock(-3) == "0:00"


def test_dedupe_drops_doubled_boxes_of_one_label_only():
    boxes = [{"label": "pipette tip", "box": (0, 0, 10, 10), "score": 0.3},
             {"label": "pipette tip", "box": (1, 1, 11, 11), "score": 0.5},
             {"label": "tube", "box": (1, 1, 11, 11), "score": 0.2},
             {"label": "pipette tip", "box": (50, 50, 60, 60), "score": 0.1}]
    got = L.dedupe(boxes)
    assert len(got) == 3
    assert {"label": "pipette tip", "box": (1, 1, 11, 11), "score": 0.5} in got
