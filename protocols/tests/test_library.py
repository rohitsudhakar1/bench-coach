from pathlib import Path

import pytest
import yaml

from coach.brain import Brain
from coach.protocol import load
from protocols.convert import validate_protocol
from scripts.preflight import protocol as preflight_protocol

ROOT = Path(__file__).resolve().parents[1]
FILES = ['serial-dilution', 'pcr-master-mix', 'heat-shock-transformation', 'loading-gel', 'plating-cells']


@pytest.mark.parametrize('name', FILES)
def test_library_loads_and_confirms_in_coach(name):
    path = ROOT / f'{name}.yaml'
    raw = path.read_text()
    assert '# Source: https://' in raw
    data = validate_protocol(yaml.safe_load(raw))
    loaded = load(path)
    assert len(loaded.steps) == len(data['steps'])
    assert preflight_protocol(path).startswith(f'{len(loaded.steps)} steps load')
    brain = Brain(loaded, clock=lambda: 1000)
    brain.start()
    clock = 1000
    for index, step in enumerate(loaded.steps):
        assert step.expect
        assert all(e.kind in {'tip_on', 'tip_off', 'draw', 'dispense', 'hand_touch', 'speech'} for e in step.expect)
        if any(e.kind in {'draw', 'dispense'} for e in step.expect):
            assert step.angle_max_deg == 20
        clock += max(step.min_seconds or 0, 1)
        outs = brain.handle({'t': clock, 'kind': 'speech', 'source': 'app', 'step': index, 'detail': {'text': 'done'}})
        assert brain.status[index] == 'done'
        assert brain.evidence[index] == 'speech'
        assert not [o for o in outs if o.kind == 'emit' and o.event['kind'] == 'warning']
    assert brain.finished()


def test_four_tenfold_transfers():
    steps = load(ROOT / 'serial-dilution.yaml').steps
    draws = [s for s in steps if any(e.kind == 'draw' for e in s.expect)]
    assert [s.objects()[0] for s in draws] == ['stock dye', 'tube D1', 'tube D2', 'tube D3']
    assert all('100 microlitres' in s.text for s in draws)
    assert '900 microlitres' in steps[0].text
    assert [s.objects()[0] for s in steps if any(e.kind == 'dispense' for e in s.expect)] == [f'tube D{i}' for i in range(1, 5)]


def test_timing_and_manual_evidence():
    steps = load(ROOT / 'heat-shock-transformation.yaml').steps
    assert [s.min_seconds for s in steps if s.min_seconds] == [1800, 1800, 45, 120, 2700]
    assert load(ROOT / 'plating-cells.yaml').steps[-2].min_seconds == 57600
    step = next(s for s in steps if s.min_seconds == 45)
    from coach.protocol import Protocol
    brain = Brain(Protocol('Wait', [step]), clock=lambda: 1000)
    # A one-step protocol needs a zero-based step index.
    step.index = 0
    brain.start()
    brain.handle({'t': 1001, 'kind': 'speech', 'source': 'app', 'detail': {'text': 'what next?'}})
    assert not brain.finished()
    outs = brain.handle({'t': 1002, 'kind': 'speech', 'source': 'app', 'detail': {'text': 'done'}})
    assert any(o.kind == 'emit' and o.event['kind'] == 'warning' for o in outs)
