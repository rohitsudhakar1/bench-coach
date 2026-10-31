import json

import cv2
import numpy as np

from vision.table import TableTracker, find_caps, main, marker

CAPS = {"tube A": (100, 60, (0, 0, 255)), "tube B": (220, 60, (255, 0, 0)), "tube C": (340, 60, (0, 255, 255))}
TIP, WASTE = (80, 280), (400, 280)


def scene(pt=None):
    img = np.full((360, 480, 3), 90, np.uint8)
    for x, y, c in CAPS.values():
        cv2.circle(img, (x, y), 14, c, -1)
    if pt:
        cv2.circle(img, (int(pt[0]), int(pt[1])), 6, (255, 0, 255), -1)
    return img


def test_caps_and_marker_found():
    caps = find_caps(scene())
    for name, (x, y, _) in CAPS.items():
        assert abs(caps[name][0] - x) < 2 and abs(caps[name][1] - y) < 2
    assert marker(scene()) is None
    m = marker(scene((200, 200)))
    assert abs(m[0] - 200) < 2 and abs(m[1] - 200) < 2


def regions():
    r = {n: [x, y, 25] for n, (x, y, _) in CAPS.items()}
    r["tip box"], r["waste"] = [*TIP, 25], [*WASTE, 25]
    return r


def visits(seq, dt=0.1, stay=4, travel=(240, 200)):
    """seq of points to visit; between visits the marker sits in the travel lane."""
    pts = []
    for p in seq:
        pts += [travel] * 3 + [p] * stay
    return pts + [travel] * 3


def play(pts, trk=None, dt=0.1):
    trk = trk or TableTracker(regions())
    out = []
    for i, p in enumerate(pts):
        out += trk.update(i * dt, p)
    return [(e["kind"], e["object"], e["detail"].get("action")) for e in out]


A, B = CAPS["tube A"][:2], CAPS["tube B"][:2]


def test_demo_order_with_mixing():
    ev = play(visits([TIP, A, B, B, B, WASTE]))
    assert ev == [("tip_on", "tip box", None), ("draw", "tube A", None), ("dispense", "tube B", None),
                  ("hand_touch", "tube B", "mix"), ("hand_touch", "tube B", "mix"), ("tip_off", "waste", None)]


def test_no_tube_events_without_tip_and_reuse_not_hidden():
    assert play(visits([A, B])) == []
    ev = play(visits([TIP, A, B, TIP, A]))
    assert [k for k, *_ in ev] == ["tip_on", "draw", "dispense", "draw"]  # second tip box visit: no tip_on


def test_lost_marker_drops_visit():
    pts = [(240, 200)] * 3 + [TIP] * 4 + [(240, 200)] * 3 + [A] * 2 + [None] * 8 + [(240, 200)] * 3
    assert [k for k, *_ in play(pts)] == ["tip_on"]


def test_cli_setup_and_run_on_a_file(tmp_path, capsys):
    path = tmp_path / "table.avi"
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (480, 360))
    for p in visits([TIP, A, B, WASTE]):
        vw.write(scene(p))
    vw.release()
    cfg = tmp_path / "table.json"
    still = tmp_path / "still.avi"
    vw = cv2.VideoWriter(str(still), cv2.VideoWriter_fourcc(*"MJPG"), 10, (480, 360))
    vw.write(scene())
    vw.release()
    main(["setup", "--source", str(still), "--config", str(cfg), "--headless",
          "--tip-box", *map(str, (*TIP, 25)), "--waste", *map(str, (*WASTE, 25))])
    assert set(json.loads(cfg.read_text())["regions"]) == {"tube A", "tube B", "tube C", "tip box", "waste"}
    capsys.readouterr()
    main(["run", "--source", str(path), "--config", str(cfg), "--hub", "none"])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    assert [e["kind"] for e in lines] == ["tip_on", "draw", "dispense", "tip_off"]
    assert lines[1]["object"] == "tube A" and lines[0]["t"] > 1.7e9
