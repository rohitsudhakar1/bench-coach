from vision.core import Det
from vision.draw import DipTracker, DrawRules, Sequencer
from vision.tests.helpers import hand_at

TUBE = Det("tube", (90, 280, 130, 330), 0.5)
BOTTLE = Det("bottle", (290, 280, 340, 340), 0.5)


def dip_path(x, n_in=6, start=0.0, dt=0.1):
    """Above, slowly into the container at (x, 300), stay, back up: (t, box) per frame."""
    out, t = [], start
    for y in [200, 210, 220, 230, 240, 250]:
        out.append((t, (x - 10, y - 120, x + 10, y)))
        t += dt
    for _ in range(n_in):
        out.append((t, (x - 10, 180, x + 10, 300)))
        t += dt
    for y in [250, 220, 200, 200, 200, 200, 200, 200]:
        out.append((t, (x - 10, y - 120, x + 10, y)))
        t += dt
    return out


def run(tracker, frames, conts):
    entries = []
    for t, box in frames:
        tracker.set_containers(t, conts, 640, 480)
        x = (box[0] + box[2]) / 2
        entries += tracker.update(t, box, hand_at(x, box[1] + 10))
    return entries + tracker.finish()


def test_draw_then_dispense_with_hint():
    trk, seq = DipTracker(0), Sequencer()
    frames = dip_path(110) + dip_path(315, start=3.0)
    for e in run(trk, frames, [TUBE, BOTTLE]):
        seq.push(e)
    evs = seq.flush(0, final=True)
    assert [(e["kind"], e["object"]) for e in evs] == [("draw", "tube"), ("dispense", "bottle")]
    assert all(e["detail"]["hint"] is True for e in evs)


def test_short_stay_is_not_an_entry():
    assert run(DipTracker(0, DrawRules(dip=False)), dip_path(110, n_in=2), [TUBE]) == []


def test_lying_pipette_dropped():
    frames = [(i * 0.1, (60, 270, 110, 300)) for i in range(10)]  # wide box, lower end in the tube
    trk = DipTracker(0)
    assert run(trk, frames, [TUBE]) == []
    assert len(run(DipTracker(0, DrawRules(min_upright=0)), frames, [TUBE])) == 1


def test_fast_swing_in_dropped():
    frames = [(i * 0.1, (600 - 100 * i, 180, 620 - 100 * i, 300)) for i in range(5)]  # 100 px per frame
    frames += [(0.5 + i * 0.1, (100, 180, 120, 300)) for i in range(6)]
    frames += [(1.1 + i * 0.1, (100, 60, 120, 180)) for i in range(6)]
    assert run(DipTracker(0, DrawRules(dip=False)), frames, [TUBE]) == []
    assert len(run(DipTracker(0, DrawRules(dip=False, max_pre_speed=0)), frames, [TUBE])) == 1


def test_two_cameras_fuse_into_one_event():
    seq = Sequencer()
    for cam in (0, 1):
        for e in run(DipTracker(cam), dip_path(110, start=0.05 * cam), [TUBE]):
            seq.push(e)
    evs = seq.flush(0, final=True)
    assert len(evs) == 1 and evs[0]["detail"]["cams"] == [0, 1]


def test_tip_box_resets_to_draw():
    seq = Sequencer()
    trk = DipTracker(0)
    tb = Det("tip box", (290, 280, 340, 340), 0.5)
    entries = []
    for t, box in dip_path(110) + dip_path(315, start=3.0) + dip_path(110, start=6.0):
        trk.set_containers(t, [TUBE], 640, 480, [tb])
        entries += trk.update(t, box, None)
    for e in entries + trk.finish():
        seq.push(e)
    assert [e["kind"] for e in seq.flush(0, final=True)] == ["draw", "draw"]


def test_still_tip_without_box_counts():
    entries = run(DipTracker(0), dip_path(500, n_in=10), [])
    assert len(entries) == 1 and entries[0].label == "spot"
