import json
import socket
import threading
import time

import httpx
import numpy as np
import pytest
from websockets.sync.server import serve

from angle.events import EventGate, Publisher
from angle.feed import FeedState, HubFeed
from angle.run import analyze
from angle.sources import Frame


def event(kind, detail, t=100, source='vision'):
    return dict(t=t, kind=kind, source=source, detail=detail)


def objects(boxes=None, cam=0, t=100):
    return event('objects', dict(cam=cam, boxes=boxes if boxes is not None else [
        dict(label='pipette', box=[-5, 10, 40, 90], score=.8)]), t)


def test_per_camera_freshness_empty_and_order():
    state = FeedState()
    state.accept(objects(), now=100)
    state.accept(objects(cam=1), now=100)
    state.accept(objects([], t=99), now=100)
    crops, _, _ = state.snapshot([0, 1], [(100, 100, 3)]*2, now=100.5)
    assert crops == [(0, 10, 40, 80)]*2
    state.accept(objects([], t=100.6), now=100.6)
    assert state.snapshot([0, 1], [(100, 100, 3)]*2, now=100.6)[0] == [None, (0, 10, 40, 80)]
    assert state.snapshot([1], [(100, 100, 3)], now=101)[0] == [None]
    state.accept(objects(t=200), now=101)
    assert state.snapshot([0], [(100, 100, 3)], now=101)[0] == [None]


def test_invalid_boxes_and_scores():
    state = FeedState()
    bad = [dict(label='pipette', box=[0, 0, 40, 90], score=float('nan')),
           dict(label='tube', box=[0, 0, 40, 90], score=1),
           dict(label='pipette', box=[40, 90, 0, 0], score=1),
           dict(label='pipette', box=[0, 0, 40, float('inf')], score=1)]
    state.accept(objects(bad), now=100)
    assert state.snapshot([0], [(100, 100, 3)], now=100)[0] == [None]


def test_activity_hint_current_step_and_run_reset():
    state = FeedState()
    state.accept(event('draw', {'hint': True}), now=100)
    state.accept(event('step', {'index': 2, 'status': 'current'}, source='coach'), now=100)
    state.accept(event('step', {'index': 1, 'status': 'done'}, source='coach'), now=100)
    _, activity, step = state.snapshot([], [], now=102)
    assert activity == (100, 'draw', True) and step == 2
    assert state.snapshot([], [], now=102.001)[1] is None
    state.accept(event('step', {'index': 2, 'status': 'done'}, t=103, source='coach'), now=103)
    assert state.snapshot([], [], now=103)[2] is None
    state.accept(event('run', {'status': 'start'}, t=104, source='app'), now=104)
    state.accept(event('step', {'index': 2, 'status': 'current'}, source='coach'), now=104)
    assert state.snapshot([], [], now=104) == ([], None, None)


def test_threshold_rate_and_warning_cooldown():
    gate = EventGate()
    def reading(deg, now):
        return gate.update((deg, .4, {'rough': True}), step=3, now=now, stamp=1793400000.)
    assert [e['kind'] for e in reading(20, 0)] == ['angle']
    assert reading(30, .1) == []
    events = reading(20.0001, .5)
    assert len(events) == 2 and events[0]['detail']['ok'] is False
    assert all(e['step'] == 3 and e['source'] == 'angle' for e in events)
    assert 'Rough' in events[1]['detail']['text']
    assert len(reading(30, 1)) == 1
    assert len(reading(30, 5.5)) == 2
    assert gate.update(None) == []
    with pytest.raises(ValueError):
        reading(float('nan'), 10)


def test_publisher_http_error_falls_back(capsys):
    publisher = Publisher('http://hub')
    publisher.client.close()
    publisher.client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503)))
    try:
        publisher.send([{'kind': 'angle', 't': 100}])
        assert json.loads(capsys.readouterr().out) == {'kind': 'angle', 't': 100}
    finally:
        publisher.close()


def test_vision_priority_idle_and_stale(monkeypatch):
    import angle.run as runner
    monkeypatch.setattr(runner.time, 'time', lambda: 100)
    calls = []
    monkeypatch.setattr(runner, 'detect_axis', lambda image, crop: calls.append(crop))
    frame = Frame(np.zeros((100, 100, 3), np.uint8), 100, True)
    class Tracker:
        def update(self, image):
            return (1, 1, 20, 20)
    crop = (10, 20, 30, 40)
    analyze([frame], [Tracker()], [crop], False, None, .1, [True])
    assert not calls
    analyze([frame], [Tracker()], [crop], True, None, .1, [True])
    assert calls == [crop]
    analyze([frame], [Tracker()], [None], True, None, .1, [True])
    assert calls[-1] == (1, 1, 20, 20)
    frame.fresh = False
    analyze([frame], [None], [crop], True, None, .1, [True])
    assert len(calls) == 2


def test_real_websocket_reconnect_and_cleanup():
    listener = socket.socket()
    for port in range(8860, 8900):
        try:
            listener.bind(('127.0.0.1', port))
            break
        except OSError:
            continue
    else:
        listener.close()
        pytest.skip('No available test port in 8860..8899')
    listener.listen()
    connections = []
    def handler(ws):
        connections.append(1)
        ws.send(json.dumps(event('dispense', {}, t=time.time())))
        ws.close()
    server = serve(handler, sock=listener)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    state = FeedState()
    feed = HubFeed(f'http://127.0.0.1:{port}', state)
    try:
        feed.start()
        deadline = time.monotonic()+4
        while len(connections) < 2 and time.monotonic() < deadline:
            time.sleep(.02)
        assert len(connections) >= 2
        assert state.snapshot([], [])[1][1] == 'dispense'
    finally:
        feed.close()
        server.shutdown()
        thread.join(timeout=2)
        listener.close()
    assert not feed.thread.is_alive() and not thread.is_alive()
