import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.hub import create_app
from app.trainer import overview
from note.build import build_note
from note.render import to_html, to_markdown
from note.runfolder import Run
from app.tests.test_hub import setup


def ev(kind, t, **detail):
    return dict(kind=kind, source='coach', t=t, detail=detail)


@pytest.fixture
def three_runs(tmp_path):
    for i, student in enumerate(['Student One', '', 'Student Three']):
        folder = tmp_path / f'practice-{i}'
        folder.mkdir()
        protocol = 'pipette.yaml' if i < 2 else 'mix.yaml'
        proto = dict(name=protocol, steps=[dict(text='Attach tip', expect=[]), dict(text='Draw', expect=[])])
        events = [ev('run', 100 + i, status='start', run=folder.name, protocol=protocol, protocol_data=proto, student=student),
                  ev('speech', 105, text='My name is Sample'),
                  ev('step', 106, index=0, status='done'),
                  ev('step', 107, index=1, status='skipped' if i == 0 else 'unsure'),
                  ev('angle', 108, deg=25, limit=20, ok=False),
                  ev('angle', 109, deg=26, limit=20, ok=False),
                  dict(ev('draw', 110), object='tube A'), dict(ev('draw', 111), object='tube B'),
                  ev('run', 120 + i, status='stop', run=folder.name),
                  ev('angle', 150, deg=40, limit=20, ok=False)]
        (folder / 'events.jsonl').write_text('\n'.join(map(json.dumps, events)) + '\n')
        if i == 0:
            note = build_note(Run(folder.name, folder, events[:-1], proto, protocol, [], 100, 120))
            (folder / 'note.json').write_text(json.dumps(note))
    return tmp_path


def test_three_run_summary_and_filters(three_runs):
    before = {p: p.read_bytes() for p in three_runs.rglob('*') if p.is_file()}
    result = overview(three_runs)
    assert result['total'] == 3
    assert result['most_missed'] == []
    assert result['protocols'] == ['mix.yaml', 'pipette.yaml']
    assert [r['run'] for r in result['runs']] == ['practice-2', 'practice-1', 'practice-0']
    for row in result['runs']:
        assert row['duration'] == 20
        assert row['counts']['done'] == 1
        assert row['reused_tips'] == 1
        assert row['tilts_over'] == 2  # readings, not warnings, excluding post-stop events
    assert result['runs'][1]['student'] == 'Sample'
    assert result['runs'][2]['student'] == 'Student One'
    assert result['runs'][2]['note_url'] == '/lab-note/practice-0?format=html'
    filtered = overview(three_runs, 'pipette.yaml')
    missed = filtered['most_missed'][0]
    assert (missed['index'], missed['runs'], missed['missed'], missed['rate']) == (1, 2, 2, 1)
    assert (missed['skipped'], missed['unsure']) == (1, 1)
    assert len(overview(three_runs, 'pipette.yaml', 'student one')['runs']) == 1
    assert not overview(three_runs, 'mix.yaml', 'Student One')['runs']
    assert before == {p: p.read_bytes() for p in three_runs.rglob('*') if p.is_file()}


def test_routes_and_typed_name_header(setup):
    app, client, _, _, runs = setup
    name = '  Student <One>  '
    response = client.post('/run/start', json={'student': name})
    assert response.status_code == 200
    run = response.json()['run']
    rows = [json.loads(line) for line in (runs / run / 'events.jsonl').read_text().splitlines()]
    assert rows[0]['detail']['student'] == name.strip()
    note = build_note(Run(run, runs / run, rows, app.state.hub.protocol, '', [], rows[0]['t'], None))
    assert note['who'] == name.strip()
    assert 'Student &lt;One&gt;' in to_html(note)
    assert '**Who:** Student <One>' in to_markdown(note)
    assert client.get('/trainer').status_code == 200
    assert client.get('/trainer/runs').json()['runs'][0]['student'] == name.strip()
    assert client.post('/run/start', json={'student': 'x' * 121}).status_code == 422


def test_partial_missing_and_symlink_data(three_runs):
    with (three_runs / 'practice-1/events.jsonl').open('a') as stream:
        stream.write('{broken')
    (three_runs / 'practice-2/note.json').write_text('{broken')
    (three_runs / 'logs').mkdir()
    (three_runs / 'linked').symlink_to(three_runs / 'practice-0', target_is_directory=True)
    result = overview(three_runs)
    assert len(result['runs']) == 3
    assert result['runs'][0]['issues'] and result['runs'][1]['issues']
    assert overview(three_runs / 'absent')['runs'] == []


def test_unfinished_and_protocol_versions(three_runs):
    p = three_runs / 'practice-1/events.jsonl'
    events = [json.loads(line) for line in p.read_text().splitlines()]
    events = [e for e in events if e['kind'] != 'step']
    events[0]['detail']['protocol_data']['steps'][1]['text'] = 'Draw slowly'
    p.write_text('\n'.join(map(json.dumps, events)))
    result = overview(three_runs, 'pipette.yaml')
    assert len(result['most_missed']) == 3
    unfinished = next(s for s in result['most_missed'] if s['text'] == 'Draw slowly')
    assert unfinished['unfinished'] == 1
