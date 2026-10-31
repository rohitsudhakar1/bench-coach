import pytest

from vision.eval.cache import CACHE
from vision.eval.score import event_score
from vision.eval.score_actions import match

ACTS = [{"kind": "draw", "start": 10, "end": 12}, {"kind": "dispense", "start": 15, "end": 16},
        {"kind": "mix", "start": 20, "end": 25}]


def test_action_matching_tolerance_and_once_each():
    evs = [{"t": 9.0, "kind": "draw"}, {"t": 11.0, "kind": "draw"}, {"t": 30.0, "kind": "draw"},
           {"t": 22.0, "kind": "draw"}]
    tp, fp, n = match(evs, ACTS, "draw")
    assert (tp, fp, n) == (1, 2, 1)  # 9.0 matches (within 1.5 s), 11.0 has nothing left, 22.0 is in a mix


def test_frame_events_precision_and_recall():
    labs = [{"t": 1.0, "act": None, "touch": []}, {"t": 2.0, "act": "tip_on", "touch": []},
            {"t": 3.0, "act": None, "touch": ["tube"]}]
    r = event_score([{"t": 2.3, "kind": "tip_on", "object": "tip box"},
                     {"t": 3.1, "kind": "hand_touch", "object": "tube rack"}], labs)
    assert (r["tip_on"]["tp"], r["tip_on"]["hit"]) == (1, 1)
    assert (r["hand_touch"]["fp"], r["hand_touch"]["hit"]) == (1, 0)


@pytest.mark.skipif(not any(CACHE.glob("CP-Left-Front-414-445@*.json.gz")), reason="no cached model output")
def test_replay_of_cache_gives_events():
    from vision.eval.replay import frames, replay
    evs = replay([frames("lsv_mv/CP-Left-Front-414-445.mp4")], new_draw=True)
    assert any(e["kind"] == "draw" and e["detail"]["hint"] for e in evs)
