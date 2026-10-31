from vision.core import Det
from vision.scene import SceneFilter


def labels(dets):
    return [d.label for d in dets]


def test_still_pipette_dropped_after_three_seconds_moving_one_kept():
    sf = SceneFilter()
    still = Det("pipette", (10, 10, 30, 100), 0.4)
    out = []
    for i in range(12):  # every 0.35 s for about 4 s
        moving = Det("pipette tip", (200 + 15 * i, 10, 220 + 15 * i, 100), 0.4)
        out.append(labels(sf.update(i * 0.35, [still, moving], 640, 480)))
    assert out[0] == ["pipette", "pipette tip"]
    assert out[-1] == ["pipette tip"]


def test_big_rack_and_plate_dropped():
    sf = SceneFilter()
    big = Det("tube rack", (0, 0, 300, 200), 0.4)  # a fifth of the frame
    small = Det("plate", (0, 0, 100, 100), 0.4)
    assert labels(sf.update(0, [big, small], 640, 480)) == ["plate"]


def test_waste_must_be_low_small_and_stable():
    sf = SceneFilter()
    high = Det("waste", (100, 10, 150, 60), 0.4)
    low = Det("waste", (100, 400, 150, 450), 0.4)
    seen = [labels(sf.update(i * 0.35, [high, low], 640, 480)) for i in range(6)]
    assert seen[0] == [] and seen[3] == [] and seen[4] == ["waste"]
    huge = Det("waste", (0, 250, 400, 480), 0.4)
    assert all(labels(SceneFilter().update(i, [huge], 640, 480)) == [] for i in range(6))
