import json
from pathlib import Path

import pytest

from vision.core import Det, Hand
from vision.events import EventEngine
from vision.held import HeldPipette
from vision.pipeline import Vision
from vision.scene import SceneFilter

PIP = Det('pipette', (100, 100, 120, 250), 0.8)
HAND = Hand('left', box=(90, 90, 130, 140))


def pip_boxes(events):
    return [b for e in events if e['kind'] == 'objects'
            for b in e['detail']['boxes'] if 'pipette' in b['label']]


@pytest.mark.parametrize('label', ['pipette', 'pipette tip'])
def test_unheld_boxes_keep_flowing_but_are_marked_false(label):
    v = Vision(1, new_draw=True)
    events = v.step(0, 0, 640, 480, [], [Det(label, PIP.box, .9)])
    assert len(v.dets[0]) == 1
    assert pip_boxes(events)[0]['held'] is False
    if label == 'pipette':
        assert v.engines[0].pip is not None


def test_brief_hand_pass_and_many_fast_frames_do_not_open_gate():
    gate = HeldPipette()
    for i in range(100):
        gate.update(i / 1000, [HAND])
    assert not gate.allows(PIP.box)
    gate.update(0.8, [HAND])  # a long frame gap is not continuous hand evidence
    assert not gate.allows(PIP.box)
    for i in range(9, 16):
        gate.update(i / 10, [HAND])
    assert gate.allows(PIP.box)
    gate.update(1.6, [])
    assert not gate.allows(PIP.box)
    gate.update(0, [HAND])  # clock reset cannot reuse the old window
    assert not gate.allows(PIP.box)


def test_each_pipette_and_tip_gets_its_own_held_flag():
    v = Vision(1)
    raw = [PIP, Det('pipette tip', PIP.box, .7),
           Det('pipette', (450, 10, 480, 300), .99)]
    for i in range(11):
        events = v.step(0, i / 10, 640, 480, [HAND], raw)
    boxes = pip_boxes(events)
    assert len(boxes) == 3
    assert all(b['held'] == (b['box'][0] == 100) for b in boxes)


@pytest.mark.parametrize('bench', [False, True])
def test_stand_rejected_at_two_seconds_even_with_a_hand(bench):
    v = Vision(1)
    box = (100, 100, 120, 300) if bench else (0, 100, 20, 300)
    hand = Hand('left', box=(box[0] - 10, 90, box[2] + 10, 140))
    raw = [Det('pipette', box, .9)]
    if bench:
        raw.append(Det('tube rack', (0, 300, 640, 380), .8))
    for i in range(20):
        v.step(0, i / 10, 640, 480, [hand], raw)
    assert v.engines[0].pip is not None
    events = v.step(0, 2.0, 640, 480, [hand], raw)
    assert pip_boxes(events)[0]['held'] is False
    assert v.engines[0].pip is not None  # the draw path remains unchanged


def test_moving_edge_box_is_not_a_two_second_stand():
    sf = SceneFilter()
    for i in range(22):
        d = Det('pipette', (0, 10 + 8 * i, 20, 100 + 8 * i), .8)
        sf.update(i / 10, [d], 640, 480)
        assert not sf.stands


def test_hand_history_is_per_camera_and_cached_hands_expire():
    v = Vision(2)
    raw = [PIP, Det('hand', HAND.box, .9)]
    for i in range(12):
        v.step(0, i / 10, 640, 480, [], raw)
        v.step(1, i / 10, 640, 480, [], [PIP])
    assert v.held[0].allows(PIP.box)
    assert not v.held[1].allows(PIP.box)
    v.step(0, 2.2, 640, 480, [], None)
    assert not v.held[0].allows(PIP.box)


def test_cached_12_27_angle_has_no_tripod():
    from angle.feed import FeedState
    from vision.eval.replay import frames, replay
    from vision.eval.score import centre, inside
    labels = json.loads(Path('vision/eval/labels.json').read_text())
    for name in ('mv12L', 'mv12R'):
        clip = labels['clips'][name]
        fs = frames(clip['path'])
        assert fs
        for mode in (False, True):
            state = FeedState()
            for e in replay([fs], new_draw=mode):
                state.accept(e, now=e['t'])
                crops = state.snapshot([0], [(fs[0]['h'], fs[0]['w'], 3)], now=e['t'])[0]
                if crops[0]:
                    x, y, w, h = crops[0]
                    assert not any(inside((x+w/2, y+h/2), region) for region in clip['scene']['not'])


def test_held_out_actions_restored():
    from vision.eval.score_actions import score
    labels = json.loads(Path('vision/eval/actions.json').read_text())
    result = score(labels, 'hold', {}, tries=1)['new-draw']
    assert [(result[k]['right'], result[k]['fired'], result[k]['labelled'])
            for k in ('draw', 'dispense')] == [(15, 29, 18), (10, 24, 22)]


def test_held_out_frame_acceptance():
    from vision.eval.score import score
    root = Path('vision/eval')
    labels = json.loads((root / 'labels.json').read_text())
    before = json.loads((root / 'tripod-before.json').read_text())['hold']
    after = score(labels, {}, 'hold', chance_tries=1)
    assert after['pipette_found'][0] >= before['pipette_found'][0]
    def wrong(r):
        return sum(v['wrong'] for v in r['boxes'].values()) / sum(sum(v.values()) for v in r['boxes'].values())
    assert wrong(after) <= wrong(before)
