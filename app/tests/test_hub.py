import concurrent.futures
import json
import time
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from app.hub import create_app
from app.parts import PartStatus
from app.replay import load_events
from protocols.convert import convert_text, validate_protocol

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    protocols = tmp_path / 'protocols'; protocols.mkdir()
    (protocols / 'pipetting-demo.yaml').write_text((ROOT / 'protocols/pipetting-demo.yaml').read_text())
    calls = []
    def transport(request):
        calls.append(request)
        if '/record/' in request.url.path:
            return httpx.Response(200, json={'ok': True})
        if request.url.path.endswith('.pdf'):
            return httpx.Response(200, content=b'%PDF-test')
        if request.url.path.endswith('.zip'):
            return httpx.Response(200, content=b'PK-test')
        for extension, content in [('benchling', b'<h1>Entry</h1>'), ('eln', b'PK-eln'), ('csv', b'step,status\r\n1,done\r\n')]:
            if request.url.path.endswith('.' + extension):
                return httpx.Response(200, content=content)
        if request.url.params.get('format') == 'html':
            return httpx.Response(200, text='<html><head></head><body>Readable note</body></html>')
        if '/note/' in request.url.path:
            return httpx.Response(200, text='# Lab note')
        return httpx.Response(200, json={'ready': True})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(**{**kw, 'transport': httpx.MockTransport(transport)}))
    app = create_app(tmp_path / 'runs', 'http://capture.test', 'http://note.test', protocols)
    with TestClient(app) as client:
        yield app, client, calls, protocols, tmp_path / 'runs'


def event(kind, detail=None, **kwargs):
    return dict(t=time.time(), source='coach', kind=kind, detail=detail or {}, **kwargs)


def test_routes_validation_and_websocket(setup):
    _, client, _, _, _ = setup
    assert client.get('/').status_code == 200
    for path in ('/static/style.css', '/static/app.js', '/static/favicon.svg', '/health', '/config', '/capture-status'):
        assert client.get(path).status_code == 200
    with client.websocket_connect('/ws') as ws:
        data = event('speech', {'text': '<script>hello</script>'})
        assert client.post('/events', json=data).json() == {'accepted': 1}
        assert ws.receive_json()['detail'] == data['detail']
    assert len(client.get('/events?since=0').json()) == 1
    assert client.get('/events', params={'since': data['t']}).json() == []
    for payload in (dict(data, t=-1), dict(data, confidence=2), dict(data, kind='run'), dict(data, kind='run-config')):
        assert client.post('/events', json=payload).status_code == 422
    assert client.post('/events', json=[data] * 1001).status_code == 413
    assert client.get('/events?since=nan').status_code == 422


def test_run_shared_tabs_and_log(setup):
    app, client, calls, _, runs = setup
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: client.post('/run/start'), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    run = client.get('/state').json()['run']
    assert len([c for c in calls if c.url.path == '/record/start']) == 1
    assert json.loads(next(c for c in calls if c.url.path == '/record/start').content)['run'] == run
    batch = [event('warning', {'text':'Tilt'}), event('angle', {'deg':27,'ok':False}),
             event('step', {'index':1,'status':'current','question':'Did you draw?'}),
             event('step', {'index':0,'status':'done','evidence':'hint+speech','source':'seen+said'})]
    assert client.post('/events', json=batch).status_code == 200
    state = client.get('/state').json()
    assert state['current_step'] == 1 and state['question'] == 'Did you draw?'
    assert state['warnings'] and state['steps']['0']['source'] == 'seen+said'
    assert client.post('/protocol', json={'text':'Attach tip.'}).status_code == 409
    assert client.post('/run/stop').status_code == 200
    assert client.post('/run/stop').status_code == 409
    assert client.get('/state').json()['warnings'] == []
    client.post('/events', json=event('warning', {'text':'Late warning'}))
    assert client.get('/state').json()['warnings'] == []
    rows = [json.loads(s) for s in (runs/run/'events.jsonl').read_text().splitlines()]
    assert rows[0]['detail']['status'] == 'start' and rows[-2]['detail']['status'] == 'stop'
    assert rows[0]['detail']['protocol_data']['steps']
    assert app.state.hub.state['question'] is None


def test_recovery_partial_line_and_protocol_snapshot(setup):
    _, client, _, protocols, runs = setup
    converted = client.post('/protocol', json={'text':'1. Attach a tip. 2. Draw from tube A.', 'name':'Test'}).json()
    run = client.post('/run/start').json()['run']
    client.post('/events', json=event('step', {'index':1,'status':'current','question':'Done?'}))
    path = runs/run/'events.jsonl'
    with path.open('a') as stream:
        stream.write('{"t":')
    (protocols/converted['protocol_file']).unlink()
    restored = create_app(runs, 'http://capture.test', 'http://note.test', protocols)
    with TestClient(restored) as second:
        state = second.get('/state').json()
        assert state['run'] == run and state['recording'] and state['recovered']
        assert state['protocol']['name'] == 'Test' and state['question'] == 'Done?'
        assert second.post('/run/start').status_code == 409
        second.post('/events', json=event('say', {'text':'Recovered'}))
        assert second.post('/run/stop').status_code == 200
    lines = path.read_text().splitlines()
    assert json.loads(lines[-2])['detail']['text'] == 'Recovered'
    assert create_app(runs, 'http://capture.test', 'http://note.test', protocols).state.hub.state['recording'] is False


def test_recovery_uses_start_clock_and_late_note_stays_in_own_log(setup):
    _, client, _, protocols, runs = setup
    first = client.post('/run/start').json()['run']; client.post('/run/stop')
    second = client.post('/run/start').json()['run']
    client.post('/events', json=event('note', {'run':first,'url':'http://note.test/note/'+first}))
    assert client.get('/state').json()['note'] is None
    assert json.loads((runs/first/'events.jsonl').read_text().splitlines()[-1])['kind'] == 'note'
    restored = create_app(runs, protocols_dir=protocols)
    assert restored.state.hub.state['run'] == second


def test_import_and_note_exports(setup):
    _, client, calls, protocols, _ = setup
    with client.websocket_connect('/ws') as ws:
        result = client.post('/protocol', json={'text':'1. Attach a tip. 2. Aspirate from tube A. 3. Wait for 2 minutes.'}).json()
        config = ws.receive_json()
    assert config['kind'] == 'run-config'
    assert yaml.safe_load((protocols/result['protocol_file']).read_text()) == result['protocol']
    assert result['protocol']['steps'][1]['technique']['angle_max_deg'] == 20
    assert result['protocol']['steps'][2]['min_seconds'] == 120
    assert client.post('/protocol', json={'text':'   '}).status_code == 422
    run = client.post('/run/start').json()['run']; client.post('/run/stop')
    client.post('/events', json=event('note', {'run':run,'url':'http://note.test/note/'+run,'path':'/never/read/me'}))
    assert client.get('/lab-note/'+run).json()['text'] == '# Lab note'
    response = client.get('/lab-note/'+run+'?format=html')
    assert '<base href="http://note.test/' in response.text and 'data-off' in response.text
    assert response.headers['content-security-policy'] == 'sandbox allow-scripts allow-popups'
    for ext, content in [('pdf',b'%PDF-test'),('zip',b'PK-test'), ('benchling',b'<h1>Entry</h1>'),
                         ('eln',b'PK-eln'), ('csv',b'step,status\r\n1,done\r\n')]:
        response = client.get('/lab-note/'+run+'?format='+ext)
        assert response.content == content and 'attachment' in response.headers['content-disposition']
        assert calls[-1].url.path == '/note/' + run + '.' + ext
        assert calls[-1].url.query == b''
    assert client.get('/lab-note/bad!').status_code == 400
    assert client.get('/lab-note/'+run+'?format=exe').status_code == 400
    client.post('/events', json=event('note', {'run':run,'url':'http://untrusted.test/secrets'}))
    client.get('/lab-note/'+run)
    assert calls[-1].url.host == 'note.test'
    assert not any(c.method == 'POST' and c.url.path == '/note' for c in calls)


def test_offline_capture_and_note(setup, monkeypatch):
    _, client, _, _, _ = setup
    # Replace the factory with an isolated failing transport.
    original = __import__('httpx._client', fromlist=['AsyncClient']).AsyncClient
    def fail(request):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(**{**kw,'transport':httpx.MockTransport(fail)}))
    result = client.post('/run/start').json()
    assert result['capture']['ok'] is False and client.get('/state').json()['recording']
    assert client.post('/run/stop').status_code == 200
    assert client.get('/lab-note/'+result['run']).status_code == 503
    assert client.get('/capture-status').status_code == 503
    assert all(not p['alive'] for n,p in client.get('/parts').json()['parts'].items() if n != 'app')


def test_parts_cache_and_running_status(setup):
    app, client, calls, _, _ = setup
    before = len(calls)
    assert all(p['status'] == 'ready' for p in client.get('/parts').json()['parts'].values())
    client.get('/parts'); assert len(calls) - before == 5
    client.post('/run/start')
    assert client.get('/parts').json()['parts']['vision']['status'] == 'quiet'
    client.post('/events', json=dict(event('objects'), source='vision'))
    assert client.get('/parts').json()['parts']['vision']['status'] == 'sending'
    latest = app.state.hub.parts.last_events['vision']
    app.state.hub.parts.observe(dict(event('objects'),source='vision',t=1))
    assert app.state.hub.parts.last_events['vision'] == latest


def test_replay_input(tmp_path):
    path = tmp_path/'events.jsonl'
    path.write_text('\n'.join(json.dumps(event(kind)) for kind in ['run','say','run-config','note','speech']))
    assert [e['kind'] for e in load_events(path)] == ['say','speech']
    path.write_text('{broken')
    with pytest.raises(ValueError, match='line 1'):
        load_events(path)


def test_malformed_json_numbers_and_untrusted_note_ids(setup):
    _, client, _, _, runs = setup
    for body in ('{"t":NaN,"kind":"speech","source":"coach"}',
                 '{"t":1,"kind":"speech","source":"coach","extra":Infinity}',
                 '{"t":1,"kind":"speech","source":"coach","detail":{"deg":NaN}}'):
        assert client.post('/events',content=body,headers={'content-type':'application/json'}).status_code == 422
    for run in (123, '../outside', None, 'unknown'):
        assert client.post('/events',json=event('note',{'run':run})).status_code == 200
    assert client.get('/state').json()['note'] is None
    assert not runs.exists()


def test_slow_subscriber_reconnects_instead_of_losing_order(setup):
    import asyncio
    app, _, _, _, _ = setup
    hub = app.state.hub
    queue = asyncio.Queue(maxsize=2)
    hub.subscribers.add(queue)
    for number in range(3):
        hub.publish(event('say', {'text':str(number)}))
    assert queue.get_nowait() is None
    assert queue not in hub.subscribers
    assert [e['detail']['text'] for e in hub.history] == ['0','1','2']


def test_health_probes_are_parallel_and_cached(monkeypatch):
    import asyncio
    count = 0
    async def respond(request):
        nonlocal count
        count += 1
        await asyncio.sleep(.04)
        return httpx.Response(200,json={'ready':True})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: original(**{**kw,'transport':httpx.MockTransport(respond)}))
    parts = PartStatus({n:'http://parts.test/'+n for n in ['capture','vision','angle','coach','note']})
    async def exercise():
        start = time.monotonic()
        results = await asyncio.gather(parts.snapshot(),parts.snapshot())
        assert time.monotonic() - start < .2
        assert count == 5
        assert results[0]['checked_at'] == results[1]['checked_at']
        parts.expires = 0
        await parts.snapshot()
        assert count == 10
    asyncio.run(exercise())
