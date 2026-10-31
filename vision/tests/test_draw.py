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


def _entry(cam, t0, t1, x, label="tube"):
    from vision.draw import Entry
    return Entry(cam, t0, t1, label, (x, 300), 40)


def _named(rules, entries):
    seq = Sequencer(rules)
    out = []
    for e in entries:
        seq.push(e)
        out += seq.flush(e.t1 + 5)
    return [ev["kind"] for ev in out + seq.flush(0, final=True)]


def test_short_dispense_not_sent_but_turn_still_flips():
    es = [_entry(0, 0, 1.0, 100), _entry(0, 3, 3.3, 200), _entry(0, 6, 7.0, 100), _entry(0, 9, 10.0, 200)]
    assert _named(DrawRules(), es) == ["draw", "dispense", "draw", "dispense"]
    assert _named(DrawRules(disp_min_dwell=0.4), es) == ["draw", "draw", "dispense"]


def test_dispense_near_previous_entry():
    es = [_entry(0, 0, 1.0, 100), _entry(0, 3, 4.0, 500)]  # 10 container sizes away
    assert _named(DrawRules(disp_near=3.0), es) == ["draw"]
    assert _named(DrawRules(disp_near=3.0), [es[0], _entry(0, 3, 4.0, 160)]) == ["draw", "dispense"]


def test_merge_gap_keeps_the_name():
    es = [_entry(0, 0, 1.0, 100), _entry(0, 1.2, 1.8, 105), _entry(0, 4, 5, 300)]
    assert _named(DrawRules(), es) == ["draw", "dispense", "draw"]
    assert _named(DrawRules(merge_gap=0.4), es) == ["draw", "dispense"]


def test_by_place_names_the_source_a_draw():
    es = [_entry(0, 0, 1, 100), _entry(0, 3, 4, 400), _entry(0, 6, 7, 102), _entry(0, 9, 10, 104)]
    assert _named(DrawRules(by_place=1.0), es) == ["draw", "dispense", "draw", "draw"]


def test_entries_record_thumb_and_lean():
    trk = DipTracker(0)
    es = run(trk, dip_path(110), [TUBE])
    assert len(es) == 1
    assert 0 < es[0].lean < 1  # 20 px wide, 120 px tall
    assert es[0].thumb_min is None or isinstance(es[0].thumb_min, float)
