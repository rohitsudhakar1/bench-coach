import json

import cv2
import numpy as np
import pytest

from scripts.preflight import caps
from vision.table import TableTracker, validate
from vision.table_colour import BandTracker, learn, quality
from vision.eval.table_stress import CONDITIONS, fixture, measure
from vision import table


@pytest.mark.parametrize('condition', CONDITIONS)
def test_stress_event_identity_and_no_extra_events(condition):
    setup, frames, expected, fixed = fixture(condition, 7)
    result = measure(table, setup, frames, expected, fixed, True)
    assert result['exact'], (condition, result)


def config(frame):
    regs = table.find_caps(frame)
    regs.update({'tip box': [55, 195, 20], 'waste': [265, 195, 20]})
    cfg = validate(dict(size=[320, 240], regions=regs))
    cfg['colour_calibration'] = learn(frame, regs)
    return cfg


def test_learned_colours_preflight_and_bad_setup(tmp_path):
    frame, *_ = fixture('hue_plus15', 2)
    cfg = config(frame)
    path = tmp_path/'table.json'
    path.write_text(json.dumps(cfg))
    assert 'setup quality' in caps(frame, path)
    changed = frame.copy()
    cv2.circle(changed, (70, 45), 18, (100, 100, 100), -1)
    with pytest.raises(ValueError, match='setup quality'):
        caps(changed, path)
    with pytest.raises(ValueError, match='resolution'):
        caps(frame[:100], path)
    dark = (frame*.08).astype(np.uint8)
    assert quality(dark, cfg)[0] < 70


def test_partial_band_and_motion_prior_never_invent_hidden_points():
    frame, *_ = fixture('clean', 1)
    cfg = config(frame)
    band = BandTracker(cfg['colour_calibration'])
    def image(point=None, distractor=False):
        out = frame.copy()
        if point:
            cv2.circle(out, point, 6, (230, 0, 230), -1)
        if distractor:
            cv2.circle(out, (290, 130), 15, (230, 0, 230), -1)
        return out
    assert band.update(image((120, 130)), 0) == pytest.approx((120, 130), abs=1)
    assert band.update(image((125, 130), True), .1) == pytest.approx((125, 130), abs=1)
    assert band.update(image(), .2) is None
    assert band.update(image((130, 130), True), 1) is None  # expired, ambiguous
    assert BandTracker().update(image((130, 130), True), 0) is None


def test_static_magenta_is_not_tip_and_old_config_still_works():
    frame, *_ = fixture('clutter', 3)
    cfg = config(frame)
    assert BandTracker(cfg['colour_calibration']).update(frame, 0) is None
    old = dict(size=cfg['size'], regions=cfg['regions'])
    assert validate(old) == old
    assert quality(frame, old)[0] == 0


def test_missing_band_cannot_complete_a_visit():
    tracker = TableTracker({'tip box': [10, 10, 5], 'tube A': [30, 10, 5]})
    tracker.update(0, (10, 10))
    assert tracker.update(.2, (10, 10))[0]['kind'] == 'tip_on'
    tracker.update(.3, (30, 10))
    assert tracker.update(.6, None) == []
    assert tracker.update(1., (60, 10)) == []


def test_duplicate_cap_requires_manual_identity_review():
    frame, *_ = fixture('clean', 2)
    cv2.circle(frame, (110, 95), 10, (10, 10, 220), -1)
    assert 'tube A' not in table.find_caps(frame)


def test_preflight_rejects_swapped_caps(tmp_path):
    frame, *_ = fixture('clean', 2)
    cfg = config(frame)
    path = tmp_path/'table.json'
    path.write_text(json.dumps(cfg))
    swapped = frame.copy()
    swapped[30:61, 55:86] = frame[30:61, 145:176]
    swapped[30:61, 145:176] = frame[30:61, 55:86]
    with pytest.raises(ValueError, match='setup quality'):
        caps(swapped, path)
