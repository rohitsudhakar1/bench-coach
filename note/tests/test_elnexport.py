import csv
import hashlib
import io
import json
from pathlib import PurePosixPath
import zipfile

import pytest
from fastapi.testclient import TestClient

from note.elnexport import CRATE, ELN, entry_html, eln_archive, steps_csv
from note.service import create_app, make_note


def test_exports_and_crate_structure(fake_run):
    note = make_note(fake_run, use_claude=False)["note"]
    rows = list(csv.DictReader(io.StringIO(steps_csv(note))))
    assert len(rows) == len(note['steps']) == 8
    for step, row in zip(note['steps'], rows):
        assert row['instruction'] == step['text']
        assert row['status'] == step['status']
        assert row['evidence'] == (step['label'] or '')
        assert float(row['event_epoch_seconds']) == step['t']
        assert float(row['run_seconds']) == step['at']
        assert json.loads(row['camera_offsets_seconds']) == step['offsets']
    doc = entry_html(note, fake_run)
    assert '<table' in doc and 'seen+said' in doc
    assert 'lot 4471B' in doc and 'cam0.mp4 (recording, not included)' in doc
    archive = zipfile.ZipFile(io.BytesIO(eln_archive(note, fake_run)))
    assert archive.testzip() is None
    names = set(archive.namelist())
    assert all(n.startswith('lab-note/') and '..' not in PurePosixPath(n).parts for n in names)
    assert not any(n.endswith(('.mp4', '.wav')) for n in names)
    metadata = json.loads(archive.read('lab-note/ro-crate-metadata.json'))
    assert metadata['@context'] == CRATE + '/context'
    nodes = {n['@id']: n for n in metadata['@graph']}
    assert len(nodes) == len(metadata['@graph'])
    assert nodes['ro-crate-metadata.json']['about'] == {'@id': './'}
    assert nodes['ro-crate-metadata.json']['conformsTo'] == {'@id': CRATE}
    assert nodes['./']['conformsTo'] == {'@id': ELN}
    experiment = nodes['./experiment/']
    assert experiment['genre'] == 'experiment' and experiment['text'] == doc
    assert nodes[experiment['author']['@id']]['name'] == note['who']
    root_refs = {r['@id'] for r in nodes['./']['hasPart']}
    covered = {'lab-note/ro-crate-metadata.json'}
    for ref in experiment['hasPart']:
        assert ref['@id'] in root_refs
        node = nodes[ref['@id']]
        member = 'lab-note/' + node['@id'].removeprefix('./')
        content = archive.read(member)
        covered.add(member)
        assert node['@type'] == 'File' and node['encodingFormat']
        assert int(node['contentSize']) == len(content)
        assert node['sha256'] == hashlib.sha256(content).hexdigest()
    assert covered == names
    assert archive.read('lab-note/experiment/events.jsonl') == (fake_run/'events.jsonl').read_bytes()
    assert sum('note_frames/' in n for n in names) == 6


def test_escaping_missing_times_and_containment(fake_run, tmp_path):
    note = make_note(fake_run, use_claude=False, frames=False)['note']
    note['steps'][0].update(text='=1+1, "µL"\n<script>alert(1)</script>', t=None, at=None, offsets={})
    note['start'] = None
    (tmp_path/'secret.jpg').write_bytes(b'private')
    (fake_run/'note_frames').mkdir(exist_ok=True)
    (fake_run/'note_frames/link.jpg').symlink_to(tmp_path/'secret.jpg')
    note['problems'][0]['frames'] = [{'file': 'note_frames/link.jpg'}, {'file': '../secret.jpg'}]
    (fake_run/'timing.jsonl').unlink()
    (fake_run/'timing.jsonl').symlink_to(tmp_path/'secret.jpg')
    doc = entry_html(note, fake_run)
    assert '<script>' not in doc and '&lt;script&gt;' in doc
    row = next(csv.DictReader(io.StringIO(steps_csv(note))))
    assert row['instruction'] == "'" + note['steps'][0]['text']
    assert row['event_epoch_seconds'] == row['run_seconds'] == ''
    archive = zipfile.ZipFile(io.BytesIO(eln_archive(note, fake_run)))
    assert not any('link.jpg' in n or 'timing.jsonl' in n for n in archive.namelist())
    assert all(b'private' not in archive.read(n) for n in archive.namelist())


@pytest.mark.parametrize('extension,mime', [('benchling','text/html'), ('csv','text/csv'), ('eln','application/vnd.eln+zip')])
def test_download_builds_offline(fake_run, monkeypatch, extension, mime):
    def forbidden(*args, **kwargs):
        raise AssertionError('export must not call model, PDF renderer or event hub')
    monkeypatch.setattr('note.service.polish', forbidden)
    monkeypatch.setattr('note.service.post_event', forbidden)
    monkeypatch.setattr('note.export.render_pdf', forbidden)
    with TestClient(create_app(fake_run.parent, watch=False, hub=None)) as client:
        response = client.get(f'/note/{fake_run.name}.{extension}')
        assert response.status_code == 200
        assert response.headers['content-type'].startswith(mime)
        assert 'attachment;' in response.headers['content-disposition']
        assert response.headers['cache-control'] == 'no-store'
        assert client.get(f'/note/missing.{extension}').status_code == 404
        assert client.get(f'/note/..%2Fmissing.{extension}').status_code == 404


def test_empty_note_exports_without_attachments(fake_run):
    note = make_note(fake_run, use_claude=False, frames=False)['note']
    note.update(steps=[], technique=[], said=[], problems=[], videos=[], start=None, who=None)
    assert list(csv.DictReader(io.StringIO(steps_csv(note)))) == []
    archive = zipfile.ZipFile(io.BytesIO(eln_archive(note, fake_run)))
    graph = json.loads(archive.read('lab-note/ro-crate-metadata.json'))['@graph']
    experiment = next(node for node in graph if node['@id'] == './experiment/')
    assert 'dateCreated' not in experiment
    assert '<tbody></tbody>' in experiment['text']
