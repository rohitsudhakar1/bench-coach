from coach.brain import Brain
from coach.protocol import from_data
from app.tests.test_hub import setup, event


def test_phone_route_and_confirmation_through_hub_and_coach(setup):
    _, client, _, _, _ = setup
    page = client.get('/step')
    assert page.status_code == 200
    assert page.headers['cache-control'] == 'no-cache'
    assert 'name="viewport"' in page.text
    for file in ('step.js', 'step.css'):
        assert client.get('/static/' + file).status_code == 200
    client.post('/run/start')
    state = client.get('/state').json()
    brain = Brain(from_data(state['protocol']))
    for out in brain.start():
        if out.kind == 'emit':
            client.post('/events', json=out.event)
    with client.websocket_connect('/ws') as ws:
        confirm = dict(event('speech', {'text': 'done'}, step=0), source='app')
        assert client.post('/events', json=confirm).json() == {'accepted': 1}
        delivered = ws.receive_json()
        assert delivered['source'] == 'app'
        # A tap alone never manufactures a completed step in the hub.
        assert client.get('/state').json()['current_step'] == 0
        for out in brain.handle(delivered):
            if out.kind == 'emit':
                client.post('/events', json=out.event)
    state = client.get('/state').json()
    assert state['current_step'] == 1
    assert state['steps']['0']['status'] == 'done'
    assert state['steps']['0']['source'] == 'said'
    assert state['steps']['0']['evidence'] == 'speech'
    client.post('/events', json=event('warning', {'text': 'Last warning'}))
    assert client.get('/state').json()['warnings'][-1]['detail']['text'] == 'Last warning'
    client.post('/run/stop')
    assert not client.get('/state').json()['warnings']
