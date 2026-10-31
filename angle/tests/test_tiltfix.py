"""Regression checks for the no-pipetting hint sweep and publication boundary."""
import json
from pathlib import Path

import numpy as np
import pytest

from angle.events import EventGate
from angle.feed import FeedState
from angle.run import analyze
from angle.sources import Frame
from angle.tests.test_feed_events import event, objects


@pytest.mark.parametrize('rough', [True, False])
@pytest.mark.parametrize('deg', [45.0001, 55.49, 88.75, 90])
def test_improbable_tilt_never_reaches_consumers(rough, deg, caplog):
    gate = EventGate()
    assert gate.update((deg, .4, {'rough': rough}), now=0) == []
    assert 'Probable mis-detection' in caplog.text
    # Rejection does not consume the publication or warning cooldown.
    assert len(gate.update((45, .4, {'rough': rough}), now=0)) == 2


@pytest.mark.parametrize('hint', [True, False])
@pytest.mark.parametrize('kind', ['draw', 'dispense'])
def test_activity_requires_current_held_crop(monkeypatch, hint, kind):
    import angle.run as runner
    calls = []
    monkeypatch.setattr(runner, 'detect_axis', lambda image, crop: calls.append(crop))
    frame = Frame(np.zeros((100, 100, 3), np.uint8), 100, True)
    class Tracker:
        def update(self, image):
            return (1, 1, 20, 20)
    state = FeedState()
    state.accept(event(kind, {'hint': hint}), now=100)
    for now, update in [(100, None), (100.1, objects(t=100.1)),
                        (100.2, objects([], t=100.2)), (100.3, objects(t=100.3)),
                        (101.3, None), (102.1, objects(t=102.1))]:
        if update:
            state.accept(update, now=now)
        crops, activity, _ = state.snapshot([0], [frame.image.shape], now=now)
        analyze([frame], [Tracker()], crops, activity is not None, None, .1, [False])
    assert calls == [(0, 10, 40, 80)] * 2


def test_rejected_measurement_is_not_displayed(monkeypatch, caplog):
    import angle.run as runner
    monkeypatch.setattr(runner, 'detect_axis', lambda *args: object())
    monkeypatch.setattr(runner, 'measure', lambda *args: (55, .4, {'rough': True}))
    frame = Frame(np.zeros((100, 100, 3), np.uint8), 100, True)
    result, *_ = analyze([frame], [None], [(1, 1, 20, 20)], True, None, .1, [False])
    assert result is None
    assert 'Probable mis-detection' in caplog.text


def test_stop_blocks_new_vision_until_restart():
    state = FeedState()
    state.accept(event('run', {'status': 'stop'}, source='app'), now=100)
    state.accept(objects(t=101), now=101)
    state.accept(event('draw', {'hint': True}, t=101), now=101)
    assert state.snapshot([0], [(100, 100, 3)], now=101) == ([None], None, None)
    state.accept(event('run', {'status': 'start'}, t=102, source='app'), now=102)
    state.accept(objects(t=102), now=102)
    state.accept(event('draw', {'hint': True}, t=102), now=102)
    crops, activity, _ = state.snapshot([0], [(100, 100, 3)], now=102)
    assert crops[0] and activity


def test_recorded_sweep_replay(caplog):
    # Original event order, relative timestamps, only geometry/activity fields retained.
    rows = [json.loads(line) for line in Path(__file__).with_name('sweep_hints.jsonl').read_text().splitlines()]
    state, gate = FeedState(), EventGate()
    published, rejected, after_stop = [], [], []
    for row in rows:
        now = row['t']
        state.accept(row, now=now)
        if row['source'] != 'angle':
            continue
        crops, activity, _ = state.snapshot([0, 1], [(1080, 1920, 3)] * 2, now=now)
        if not state.running:
            assert activity is None and crops == [None, None]
            after_stop.append(row)
            continue
        assert activity is not None and all(crops[i] for i in row['detail']['views'])
        detail = row['detail']
        events = gate.update((detail['deg'], .4, detail), now=now, stamp=now)
        (published if events else rejected).append(detail['deg'])
    assert published == [3.34, 11.92]
    assert rejected == [55.49, 88.75]
    assert len(after_stop) == 5
    assert caplog.text.count('Probable mis-detection') == 2
