"""Three simulated benches sharing one hub, with no devices or model calls."""
import json
import time

import httpx
from fastapi.testclient import TestClient

from app.classroom import create_classroom
from app.hub import Event


def test_three_benches(tmp_path, monkeypatch):
    calls = []
    original = httpx.AsyncClient
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'ok': True})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(**{**kw, 'transport': httpx.MockTransport(respond)}))
    app = create_classroom(3, 8820, tmp_path)
    for child in app.state.benches.values():
        monkeypatch.setattr(child.state.nvidia, 'start', lambda *a: None)
    with TestClient(app) as client:
        assert client.get('/class').status_code == 200
        runs = {}
        for bench in ('1', '2', '3'):
            prefix = f'/benches/{bench}'
            runs[bench] = client.post(prefix + '/run/start', json={'student': f'Student {bench}'}).json()['run']
            assert client.get(prefix + '/').status_code == 200
            assert client.get(prefix + '/state').json()['bench'] == bench
            assert client.get(prefix + '/config').json()['capture_url'].endswith(str(8821 + (int(bench)-1)*6))
        assert len(set(runs.values())) == 3
        with client.websocket_connect('/benches/2/ws') as ws:
            batch = [dict(t=time.time(), source='coach', kind='step', bench=str(n),
                          detail={'index': n - 1, 'status': 'current'}) for n in (1, 2, 3)]
            assert client.post('/events', json=batch).status_code == 200
            assert ws.receive_json()['bench'] == '2'
            assert client.post('/benches/2/events', json=dict(t=time.time(), source='coach', kind='warning', detail={'text':'Change tip'})).status_code == 200
            assert ws.receive_json()['bench'] == '2'
        states = client.get('/class/state').json()
        assert [s['bench'] for s in states] == ['2', '1', '3']
        assert states[0]['student'] == 'Student 2' and states[0]['attention']
        assert states[0]['warning'] == 'Change tip'
        for bench in ('1', '2', '3'):
            state = client.get(f'/benches/{bench}/state').json()
            assert state['current_step'] == int(bench) - 1
            assert bool(state['warnings']) == (bench == '2')
            history = client.get(f'/benches/{bench}/events').json()
            assert {e['bench'] for e in history} == {bench}
        assert client.post('/benches/2/events', json=batch[0]).status_code == 422
        invalid = dict(batch[0], bench='missing')
        assert client.post('/events', json=[batch[0], invalid]).status_code == 422
        first_log = tmp_path / runs['1'] / 'events.jsonl'
        before = first_log.read_text()
        client.post('/benches/2/events', json=dict(t=time.time(), source='note', kind='note', detail={'run': runs['1']}))
        assert first_log.read_text() == before
        assert client.post('/benches/2/run/start').status_code == 409
        assert client.post('/benches/2/run/stop').status_code == 200
        assert [s['bench'] for s in client.get('/class/state').json()] == ['1', '2', '3']
        assert client.get('/state').json()['recording']
        assert client.get('/benches/3/state').json()['recording']
        assert 'http://127.0.0.1:8827/record/stop' in calls
        assert 'http://127.0.0.1:8821/record/stop' not in calls
    restored = create_classroom(3, 8820, tmp_path)
    for bench, child in restored.state.benches.items():
        state = child.state.hub.state
        assert state['run'] == runs[bench]
        assert state['recording'] == (bench != '2')
        assert state['student'] == f'Student {bench}'
        rows = [json.loads(line) for line in (tmp_path/runs[bench]/'events.jsonl').read_text().splitlines()]
        assert {row['bench'] for row in rows} == {bench}
    assert Event(t=1, source='old', kind='say').bench == '1'
