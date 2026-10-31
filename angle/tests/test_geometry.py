import json
import cv2
import numpy as np
import pytest

from angle.geometry import Calibration, angle_between, image_angle, stereo_angle
from angle.detection import Axis, detect_axis, ShaftTracker
from angle.evaluate import synthetic_cases
from angle.run import measure


def test_projection_and_endpoint_order():
    assert image_angle([[0, 0], [0, 10]]) == 0
    assert image_angle([[10, 10], [0, 0]]) == pytest.approx(45)
    assert image_angle([[0, 0], [10, 0]]) == 90
    with pytest.raises(ValueError):
        image_angle([[1, 1], [1, 1]])


def test_random_stereo_with_noise_and_rejections():
    clean, noisy = synthetic_cases(), synthetic_cases(.5)
    assert clean['accepted'] > 750 and clean['rejected'] > 100
    assert clean['max_deg'] < 1e-8
    assert noisy['mae_deg'] < 1 and noisy['p95_deg'] < 3


def test_bad_calibration_and_parallel_planes():
    k = np.array([[800., 0, 320], [0, 800, 240], [0, 0, 1]])
    cal = Calibration(k, np.zeros(5), k, np.zeros(5), np.eye(3), [100, 0, 0], [0, 1, 0], (640, 480))
    with pytest.raises(ValueError, match='coincide'):
        stereo_angle([[100, 10], [100, 100]], [[100, 10], [100, 100]], cal)
    for rotation in (np.diag([-1, 1, 1]), np.zeros((3, 3))):
        with pytest.raises(ValueError, match='rotation'):
            Calibration(k, np.zeros(5), k, np.zeros(5), rotation, [100, 0, 0], [0, 1, 0], (640, 480))
    with pytest.raises(ValueError):
        Calibration(k, np.zeros(5), k, np.zeros(5), np.eye(3), [0, 0, 0], [0, 1, 0], (640, 480))


def test_detector_and_best_view_not_average():
    frame = np.zeros((300, 300, 3), np.uint8)
    cv2.line(frame, (145, 40), (155, 260), (255, 255, 255), 3)
    axis = detect_axis(frame, (120, 20, 60, 260))
    assert axis is not None
    assert image_angle(axis.points) == pytest.approx(2.6, abs=.5)
    assert detect_axis(np.zeros_like(frame), (0, 0, 300, 300)) is None
    result = measure([Axis(np.array([[0, 0], [0, 10]]), .8), Axis(np.array([[0, 0], [10, 0]]), .6)])
    assert result[0] == 0 and result[1] == .4 and result[2]['rough']
    assert measure([None, None]) is None


def test_tracker_returns_home_after_loss(monkeypatch):
    class FakeTracker:
        def init(self, frame, box):
            self.initial = box
        def update(self, frame):
            return False, (0, 0, 0, 0)
    monkeypatch.setattr(cv2, 'TrackerCSRT_create', FakeTracker)
    tracker = ShaftTracker((10, 20, 30, 40))
    frame = np.zeros((100, 100, 3), np.uint8)
    assert tracker.update(frame) == tracker.home
    assert tracker.update(frame) is None
    assert tracker.losses == 1 and tracker.roi == tracker.home
    assert tracker.update(frame) == tracker.home


def test_rendered_stereo_shafts_and_sync_fallback(monkeypatch):
    import angle.run as runner
    from angle.sources import Frame
    monkeypatch.setattr(runner.time, 'time', lambda: 100.)
    k = np.array([[700., 0, 320], [0, 700, 240], [0, 0, 1]])
    rotation = cv2.Rodrigues(np.array([0., -.25, 0.]))[0]
    translation = np.array([-90., 0, 0])
    cal = Calibration(k, np.zeros(5), k, np.zeros(5), rotation, translation, [0, 1, 0], (640, 480))
    direction = np.array([.3, np.sqrt(.75), .4])
    ends = np.array([0, 0, 700]) + np.array([-100, 100])[:, None]*direction
    frames, crops = [], []
    for r, t in ((np.eye(3), np.zeros(3)), (rotation, translation)):
        points = (k @ (r @ ends.T+t[:, None])).T
        points = np.rint(points[:, :2]/points[:, 2:]).astype(int)
        image = np.zeros((480, 640, 3), np.uint8)
        cv2.line(image, tuple(points[0]), tuple(points[1]), (255, 255, 255), 3)
        low, high = points.min(axis=0)-10, points.max(axis=0)+10
        crops.append((*low, *(high-low)))
        frames.append(Frame(image, 99.99, True))
    result, *_ = runner.analyze(frames, [None, None], crops, True, cal, .1, [True, True])
    assert result[2]['rough'] is False and result[0] == pytest.approx(30, abs=2)
    frames[1].stamp = 99.5
    result, *_ = runner.analyze(frames, [None, None], crops, True, cal, .1, [True, True])
    assert result[2]['rough'] and 'skew' in result[2]['reason']
    frames[0].stamp = frames[1].stamp = 98
    result, *_ = runner.analyze(frames, [None, None], crops, True, cal, .1, [True, True])
    assert result is None
