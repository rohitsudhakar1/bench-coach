import asyncio
import json
import time

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from angle.geometry import Calibration
from angle.tests.synthetic import board_pairs
from app.calibration import CalibrationUI
from app.hub import create_app


class FakeCapture:
    def __init__(self, pairs):
        self.pairs = pairs
        self.index = 0
        self.stamp = time.time()
        self.calls = 0
        self.skew = 0
        self.blank = False

    def reply(self, request):
        self.calls += 1
        cam = int(request.url.path.split('/')[2])
        image = self.pairs[self.index][cam + 1]
        if self.blank and cam == 1:
            image = np.full_like(image, 180)
        ok, jpeg = cv2.imencode('.jpg', image)
        assert ok
        return httpx.Response(200, content=jpeg.tobytes(), headers={
            'X-Frame-Time': str(self.stamp + (self.skew if cam else 0))})


@pytest.fixture(scope='module')
def rendered():
    return board_pairs()[0]


@pytest.fixture
def setup(tmp_path, monkeypatch, rendered):
    output = tmp_path / 'calibration.json'
    monkeypatch.setenv('BENCH_CALIBRATION', str(output))
    app = create_app(runs_dir=tmp_path / 'runs', capture_url='http://capture')
    fake = FakeCapture(rendered)
    service = app.state.calibration
    service.transport = httpx.MockTransport(fake.reply)
    with TestClient(app) as client:
        yield client, service, fake, output


def refresh(client, service, fake):
    service.next_detection = 0  # Advance the limiter for synthetic pose fixtures only.
    fake.stamp = time.time()
    return client.get('/calibrate/state').json()


def save(client, state, level=True):
    return client.post('/calibrate/capture', json={'revision': state['revision'], 'level': level})


def test_rendered_capture_compute_install(setup):
    client, service, fake, output = setup
    assert client.get('/calibrate').status_code == 200
    assert client.get('/calibrate/board.png').status_code == 200
    assert '/calibrate' in client.get('/').text
    assert client.post('/calibrate/use').status_code == 409
    assert client.post('/calibrate/compute').status_code == 409
    for index in range(16):
        fake.index = index
        state = refresh(client, service, fake)
        assert state['can_capture']
        assert all(v['found'] and v['image'] for v in state['views'])
        if index == 0:
            assert save(client, state, False).status_code == 409
        response = save(client, state)
        assert response.status_code == 200, response.text
        assert response.json()['count'] == index + 1
        assert save(client, state).status_code == 409
    assert len(list(service.directory.glob('*/left.png'))) == 16
    assert client.post('/calibrate/compute').status_code == 202
    assert client.get('/health').status_code == 200
    deadline = time.monotonic() + 30
    while service.verdict == 'computing' and time.monotonic() < deadline:
        time.sleep(.05)
    assert service.verdict == 'good', service.message
    assert service.result['rms_px'] < 1
    assert not output.exists()
    assert client.post('/calibrate/use').status_code == 200
    cal = Calibration.load(output)
    assert cal.size == (960, 720)
    assert json.loads(output.read_text())['physical_check'] == 'pending'
    print(f"UI FIT stereo RMS={service.result['rms_px']:.6f} px, 16 rendered JPEG pairs")
    before = output.read_bytes()
    client.post('/calibrate/reset')
    assert service.names == []
    assert client.post('/calibrate/use').status_code == 409
    assert output.read_bytes() == before


def test_missing_stale_skew_duplicate_and_rate_limit(setup):
    client, service, fake, output = setup
    state = refresh(client, service, fake)
    calls = fake.calls
    for _ in range(4):
        assert client.get('/calibrate/state').json()['revision'] == state['revision']
    assert fake.calls == calls
    assert save(client, dict(state, revision='old')).status_code == 409
    service.frames[0]['t'] -= 2
    assert save(client, state).status_code == 409
    fake.blank = True
    state = refresh(client, service, fake)
    assert not state['can_capture'] and not state['views'][1]['found']
    assert save(client, state).status_code == 409
    fake.blank = False
    fake.skew = -.2
    state = refresh(client, service, fake)
    assert not state['can_capture']
    assert save(client, state).status_code == 409
    assert not output.exists()


def test_duplicate_pose_redo_preserves_file(setup):
    client, service, fake, output = setup
    output.write_text('previous calibration')
    for _ in range(12):
        assert save(client, refresh(client, service, fake)).status_code == 200
    assert client.post('/calibrate/compute').status_code == 202
    deadline = time.monotonic() + 20
    while service.verdict == 'computing' and time.monotonic() < deadline:
        time.sleep(.05)
    assert service.verdict == 'redo'
    assert 'distinct' in service.message
    assert client.post('/calibrate/use').status_code == 409
    assert output.read_text() == 'previous calibration'


def test_bad_capture_and_storage_failure(setup, monkeypatch):
    client, service, fake, output = setup
    state = refresh(client, service, fake)
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(service, 'write_pair', fail)
    assert save(client, state).status_code == 507
    assert not service.names
    service.transport = httpx.MockTransport(lambda request: httpx.Response(503))
    service.next_detection = 0
    state = client.get('/calibrate/state').json()
    assert not state['views'] and not state['can_capture']
    assert 'unavailable' in state['message']


def test_resolution_change_and_failed_install(setup):
    client, service, fake, output = setup
    state = refresh(client, service, fake)
    service.frames[1]['image'] = service.frames[1]['image'][:300]
    assert save(client, state).status_code == 409
    assert not service.names
    service.verdict, service.result = 'good', {'rms_px': .2}
    output.mkdir()  # Atomic replacement must fail without damaging the destination.
    assert client.post('/calibrate/use').status_code == 507
    assert output.is_dir()
    assert not service.used
    assert not output.with_name(output.name + '.tmp').exists()


def test_worker_cancel_kills_process(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    async def scenario():
        waiting = asyncio.Event()
        stopped = asyncio.Event()
        async def wait():
            waiting.set()
            await stopped.wait()
        def kill():
            process.returncode = -9
            stopped.set()
        process = SimpleNamespace(returncode=None, wait=wait, kill=kill)
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
        service = CalibrationUI('http://capture', tmp_path/'pairs', tmp_path/'out.json')
        service.names = [str(i) for i in range(12)]
        await service.compute()
        task = service.task
        await waiting.wait()
        await service.compute()
        assert service.task is task
        await service.close()
        assert stopped.is_set() and task.done()
    asyncio.run(scenario())
