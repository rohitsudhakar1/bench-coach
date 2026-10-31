from vision.core import Det
from vision.events import EventEngine, tip_end
from vision.tests.helpers import hand_at

RACK = Det("tube rack", (100, 100, 200, 200), 0.4)


def kinds(evs):
    return [e["kind"] for e in evs if e["kind"] != "objects"]


def test_touch_fires_once_and_needs_finger_points():
    eng = EventEngine(0)
    out = []
    for i in range(20):  # fingertip on the rack for 2 s
        out += eng.update(i * 0.1, [hand_at(150, 110)], [RACK])
    assert kinds(out) == ["hand_touch"]
    assert out[-1]["object"] == "tube rack" and out[-1]["source"] == "vision"
    from vision.core import Hand
    eng2 = EventEngine(0)
    out2 = []
    for i in range(20):
        out2 += eng2.update(i * 0.1, [Hand("left", None, (120, 120, 180, 180))], [RACK])
    assert kinds(out2) == []


def test_touch_rearms_after_three_seconds_away():
    eng = EventEngine(0)
    out, t = [], 0.0
    for on in [True] * 5 + [False] * 10 + [True] * 5 + [False] * 35 + [True] * 5:
        out += eng.update(t, [hand_at(150, 110) if on else hand_at(400, 400)], [RACK])
        t += 0.1
    assert kinds(out) == ["hand_touch", "hand_touch"]


def test_objects_rate_limited():
    eng = EventEngine(0)
    out = []
    for i in range(30):
        out += eng.update(i * 0.1, [], [RACK], fresh=True)
    assert sum(e["kind"] == "objects" for e in out) == 6  # 2 per second over 3 s


def run_pipette(eng, path, extra, dt=0.1):
    """Move a held pipette so its lower end follows path (list of (x, y))."""
    out = []
    for i, (x, y) in enumerate(path):
        pip = Det("pipette", (x - 10, y - 120, x + 10, y), 0.5)
        out += eng.update(i * dt, [hand_at(x, y - 110)], [pip] + extra, fresh=True)
    return out


def test_tip_on_after_dwell_in_tip_box_then_leaving():
    tipbox = Det("tip box", (300, 300, 400, 400), 0.4)
    path = [(350, 250)] * 3 + [(350, 350)] * 6 + [(350, 250)] * 3
    assert kinds(run_pipette(EventEngine(0), path, [tipbox])) == ["tip_on"]
    short = [(350, 250)] * 3 + [(350, 350)] * 2 + [(350, 250)] * 3
    assert kinds(run_pipette(EventEngine(0), short, [tipbox])) == []


def test_tip_off_over_waste():
    waste = Det("waste", (300, 300, 400, 400), 0.4)
    path = [(350, 250)] * 3 + [(350, 350)] * 6 + [(350, 250)] * 3
    assert kinds(run_pipette(EventEngine(0), path, [waste])) == ["tip_off"]


def test_conservative_rule_draw_then_dispense_by_thumb_press():
    eng = EventEngine(0)
    tube = Det("tube", (90, 280, 130, 330), 0.4)
    out = []
    t = 0.0
    for press in [False] * 10 + [True] * 3 + [False] * 10 + [True] * 3 + [False] * 3:
        h = hand_at(110, 190)
        if press:  # thumb tip onto the index knuckle
            h.pts[4] = h.pts[5]
        pip = Det("pipette", (100, 180, 120, 300), 0.5)
        out += eng.update(t, [h], [pip, tube], fresh=True)
        t += 0.1
    assert kinds(out) == ["draw", "dispense"]
    assert all(e["object"] == "tube" and "hint" not in e["detail"] for e in out if e["kind"] != "objects")


def test_tip_box_in_hand_counts_as_pipette():
    eng = EventEngine(0)
    tipbox = Det("tip box", (300, 300, 400, 400), 0.4)
    out = []
    path = [(350, 250)] * 3 + [(350, 350)] * 6 + [(350, 250)] * 3
    for i, (x, y) in enumerate(path):
        tip = Det("pipette tip", (x - 10, y - 120, x + 10, y), 0.5)
        out += eng.update(i * 0.1, [hand_at(x, y - 110)], [tip, tipbox], fresh=True)
    assert kinds(out) == ["tip_on"]


def test_tip_end_is_far_from_hand():
    pip = Det("pipette", (100, 100, 120, 300), 0.5)
    assert tip_end(pip, [], hand_at(110, 120)) == (110, 300)
    assert tip_end(pip, [], hand_at(110, 290)) == (110, 100)
