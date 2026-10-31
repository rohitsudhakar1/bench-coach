import io
import json
import shutil
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.hub import create_app
from app.tests.test_hub import setup  # shared mocked upstreams

ROOT = Path(__file__).resolve().parents[2]


def wait(client, run, kind):
    for _ in range(200):
        result = client.get('/nvidia/' + run).json()[kind]
        if result['status'] not in ('queued', 'running'):
            assert result['status'] == 'ready', result
            return result
        time.sleep(.05)
    pytest.fail('NVIDIA job did not finish')


def test_stop_exports_without_waiting_and_downloads(setup):
    app, client, _, _, runs = setup
    run = client.post('/run/start').json()['run']
    assert client.post(f'/nvidia/{run}/synth').status_code == 409
    assert client.post('/run/stop').status_code == 200
    assert client.get('/health').status_code == 200
    result = wait(client, run, 'export')
    assert result['tasks'] and result['score']['tasks'] == len(result['tasks'])
    task = result['tasks'][0]
    response = client.get(f"/nvidia/{run}/tasks/{task['id']}.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert task['id'] + '/tests/verify.py' in names
        assert task['id'] + '/environment/context/task.json' in names
        assert not any('events.jsonl' in name for name in names)
    assert client.get(f'/nvidia/{run}/tasks/unknown.zip').status_code == 404
    assert client.post(f'/nvidia/{run}/export').status_code == 202
    assert not app.state.nvidia.jobs


def test_fake_run_synthesis_and_restart(tmp_path, monkeypatch):
    monkeypatch.delenv('NVIDIA_API_KEY', raising=False)
    folder = tmp_path / 'fake'; folder.mkdir()
    shutil.copy(ROOT / 'nvidia/samples/run-fake/events.jsonl', folder / 'events.jsonl')
    with TestClient(create_app(tmp_path)) as client:
        assert client.post('/nvidia/fake/export').status_code == 202
        result = wait(client, 'fake', 'export')
        assert len(result['tasks']) >= 3
        assert client.post('/nvidia/fake/synth').status_code == 202
        result = wait(client, 'fake', 'synth')
        assert result['manifest']['synthetic_runs'] == 20
        assert result['manifest']['engine'] in ('local', 'datadesigner')
        assert len(list((folder/'nvidia-ui/synth/runs').glob('*/events.jsonl'))) == 20
        for name, stats in result['recorded'].items():
            assert len(stats['skip_rate']) == len(result['synthetic'][name]['skip_rate'])
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/nvidia/fake').json()['synth'] == result
        assert client.post('/nvidia/fake/synth').json()['synth'] == result


def test_invalid_missing_symlink_and_worker_error(tmp_path):
    folder = tmp_path / 'broken'; folder.mkdir()
    (folder / 'events.jsonl').write_text('{}\n')
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/nvidia/bad!').status_code == 400
        assert client.get('/nvidia/missing').status_code == 404
        assert client.post('/nvidia/broken/unknown').status_code == 400
        assert client.post('/nvidia/broken/export').status_code == 202
        for _ in range(100):
            data = client.get('/nvidia/broken').json()['export']
            if data['status'] == 'error':
                break
            time.sleep(.02)
        assert data['status'] == 'error'
        assert str(tmp_path) not in json.dumps(data)
        (folder / 'tasks').symlink_to(tmp_path, target_is_directory=True)
        assert client.post('/nvidia/broken/export').status_code == 400


def test_shutdown_cancels_worker(tmp_path, monkeypatch):
    import asyncio
    from app.nvidia_jobs import NvidiaJobs
    folder = tmp_path / 'fake'; folder.mkdir()
    (folder / 'events.jsonl').write_text('')
    class Process:
        returncode = None
        killed = False
        async def wait(self):
            if not self.killed:
                await asyncio.Event().wait()
        def kill(self):
            self.killed = True
            self.returncode = -9
    process = Process()
    async def spawn(*args, **kwargs):
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    async def check():
        jobs = NvidiaJobs(tmp_path)
        jobs.start('fake', 'export')
        await asyncio.sleep(.01)
        jobs.start('fake', 'export')
        assert len(jobs.jobs) == 1
        await jobs.close()
        assert process.killed
    asyncio.run(check())


def test_slow_export_does_not_block_hub(setup, monkeypatch):
    import asyncio
    app, client, _, _, _ = setup
    class Process:
        returncode = None
        async def wait(self):
            if self.returncode is None:
                await asyncio.Event().wait()
        def kill(self):
            self.returncode = -9
    async def spawn(*args, **kwargs):
        return Process()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    run = client.post('/run/start').json()['run']
    assert client.post('/run/stop').status_code == 200
    assert client.get('/nvidia/' + run).json()['export']['status'] in ('queued','running')
    assert client.get('/health').status_code == 200
    assert client.post('/run/start').status_code == 200
    assert len(app.state.nvidia.jobs) == 1
