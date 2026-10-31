import asyncio
import json
from types import SimpleNamespace
import time

import cv2
import httpx
import numpy as np
import pytest

from scripts import preflight as p


@pytest.fixture
def scene(tmp_path, monkeypatch):
    image = np.zeros((120, 240, 3), np.uint8)
    regions = {'tube A': [30, 30, 15], 'tube B': [100, 30, 15], 'tube C': [180, 30, 15]}
    for name, colour in [('tube A', (0, 0, 255)), ('tube B', (255, 0, 0)), ('tube C', (0, 255, 255))]:
        cv2.circle(image, tuple(regions[name][:2]), 10, colour, -1)
    cv2.circle(image, (110, 90), 5, (255, 0, 255), -1)
    path = tmp_path / 'table.json'
    path.write_text(json.dumps(dict(size=[240, 120], regions=regions)))
    # Fake the not-yet-merged table detector at its public boundary.
    def blob(image, name):
        colours = {'tube A': (0, 0, 255), 'tube B': (255, 0, 0), 'tube C': (0, 255, 255), 'marker': (255, 0, 255)}
        target = np.array(colours[name])
        yy, xx = np.where(np.max(np.abs(image.astype(int) - target), axis=2) < 40)
        return (float(xx.mean()), float(yy.mean()), 10) if len(xx) else None
    monkeypatch.setattr(p, 'table_tools', lambda: (blob, lambda im: blob(im, 'marker'), lambda data: data))
    return image, path


class Fake:
    def __init__(self, image, mode='good'):
        self.image, self.mode = image, mode
        self.stamp = time.time()

    def __call__(self, request):
        path = request.url.path
        if self.mode == 'down':
            return httpx.Response(503)
        if path == '/cams':
            return httpx.Response(200, json=[dict(id=i, source=str(i), live=True) for i in range(2)])
        if path == '/parts':
            return httpx.Response(200, json=dict(checked_at=time.time() - (20 if self.mode == 'stale-hub' else 0), parts={
                name: dict(alive=True, status='quiet' if self.mode == 'quiet' and name == 'vision' else 'ready')
                for name in ('capture', 'vision', 'angle', 'coach', 'note')}))
        image = self.image.copy()
        if '/1/' in path and self.mode != 'duplicate':
            image[90:110, 10:30] = 255
        stamp = self.stamp if self.mode == 'frozen' else time.time()
        if self.mode == 'stale':
            stamp -= 10
        return httpx.Response(200, content=cv2.imencode('.jpg', image)[1].tobytes(), headers={'X-Frame-Time': str(stamp)})


@pytest.mark.parametrize('mode', ['good', 'duplicate', 'frozen', 'stale'])
def test_fresh_distinct_cameras(scene, mode):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(Fake(scene[0], mode))) as client:
            args = p.parse_args(['--capture', 'http://127.0.0.1:8801'])
            if mode == 'good':
                images, text = await p.cameras(client, args)
                assert len(images) == 2 and 'distinct' in text
            else:
                with pytest.raises(ValueError):
                    await p.cameras(client, args)
    asyncio.run(run())


def test_caps_and_calibration(scene, tmp_path):
    image, config = scene
    assert 'match' in p.caps(image, config)
    bad = image.copy()
    bad[:60, :60] = 0
    with pytest.raises(ValueError):
        p.caps(bad, config)
    with pytest.raises(ValueError):
        p.caps(image[:20], config)
    data = dict(K1=np.eye(3).tolist(), K2=np.eye(3).tolist(), R=np.eye(3).tolist(),
                D1=[0]*5, D2=[0]*5, T=[100, 0, 0], gravity=[0, 1, 0], size=[240, 120],
                rms_px=.4, left_rms_px=.5, right_rms_px=.7, per_pair=[dict(left_rms_px=.8, right_rms_px=.9)])
    path = tmp_path / 'calibration.json'
    path.write_text(json.dumps(data))
    assert '0.90' in p.calibration(path, {0: image, 1: image})
    for value in [2.1, -1, float('nan')]:
        data['rms_px'] = value
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError):
            p.calibration(path, {0: image, 1: image})


@pytest.mark.parametrize('mode', ['good', 'quiet', 'stale-hub'])
def test_full_preflight(scene, mode, monkeypatch, capsys):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(p.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(Fake(scene[0], mode))))
    assert p.main(['--capture', 'http://127.0.0.1:8801', '--hub', 'http://127.0.0.1:8800', '--no-angle', '--no-mic', '--table-config', str(scene[1])]) == (0 if mode == 'good' else 1)
    output = capsys.readouterr().out
    for name in ('cameras', 'table caps', 'tip band', 'disk', 'protocol'):
        assert f'PASS {name}:' in output
    assert ('PASS' if mode == 'good' else 'FIX') + ' hub parts:' in output
    assert 'calibration:' not in output and 'microphone:' not in output


def test_failure_continues_and_no_false_passes(scene, monkeypatch, capsys, tmp_path):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(p.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(Fake(scene[0], 'down'))))
    monkeypatch.setattr(p.shutil, 'disk_usage', lambda _: SimpleNamespace(free=2_000_000_000))
    assert p.main(['--no-mic', '--protocol', str(tmp_path / 'missing')]) == 1
    output = capsys.readouterr().out
    for name in ('cameras', 'table caps', 'tip band', 'calibration', 'hub parts', 'disk', 'protocol'):
        assert f'FIX {name}:' in output
    assert 'PASS ' not in output


@pytest.mark.parametrize('text,level,good', [('Testing.', .1, True), ('other words', .1, False), ('testing', 0, False)])
def test_mic_transcription(monkeypatch, text, level, good):
    async def audio(url):
        return np.full(80000, level, np.float32)
    class Child:
        returncode = 0
        async def communicate(self, pcm):
            assert len(pcm) == 320000
            return text.encode(), b''
        async def wait(self):
            pass
    async def child(*args, **kwargs):
        assert 'local_files_only=True' in args[2]
        return Child()
    monkeypatch.setattr(p, 'read_mic', audio)
    monkeypatch.setattr(p.asyncio, 'create_subprocess_exec', child)
    if good:
        assert 'heard' in asyncio.run(p.microphone(p.parse_args([])))
    else:
        with pytest.raises(ValueError):
            asyncio.run(p.microphone(p.parse_args([])))


def test_transcription_timeout_kills_and_reaps(monkeypatch):
    async def audio(url):
        return np.ones(80000, np.float32)
    class Child:
        returncode = None
        killed = waited = False
        async def communicate(self, pcm):
            raise asyncio.TimeoutError
        def kill(self):
            self.killed = True
            self.returncode = -9
        async def wait(self):
            self.waited = True
    process = Child()
    async def child(*args, **kwargs):
        return process
    monkeypatch.setattr(p, 'read_mic', audio)
    monkeypatch.setattr(p.asyncio, 'create_subprocess_exec', child)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(p.microphone(p.parse_args([])))
    assert process.killed and process.waited


@pytest.mark.parametrize('invalid', [False, True])
def test_capture_audio_wire_format(monkeypatch, invalid):
    import websockets
    class Socket:
        count = 0
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def recv(self):
            self.count += 1
            if self.count == 1:
                return json.dumps(dict(t=time.time(), n=1600))
            if self.count == 2:
                return b'x' if invalid else np.full(1600, 4000, '<i2').tobytes()
            raise asyncio.TimeoutError
    monkeypatch.setattr(websockets, 'connect', lambda *a, **kw: Socket())
    if invalid:
        with pytest.raises(ValueError):
            asyncio.run(p.read_mic('ws://127.0.0.1:8801/mic'))
    else:
        audio = asyncio.run(p.read_mic('ws://127.0.0.1:8801/mic'))
        assert len(audio) == 1600 and audio[0] == pytest.approx(4000/32768)


@pytest.mark.parametrize('content', ['steps: []', 'steps: [{text: ""}]', 'steps: [{text: hi, expect: [{object: A}]}]', 'steps: [{text: hi, min_seconds: -1}]'])
def test_invalid_protocol(tmp_path, content):
    path = tmp_path / 'protocol.yaml'
    path.write_text(content)
    with pytest.raises((ValueError, KeyError)):
        p.protocol(path)


def test_skip_flags_and_missing_table_module(scene, monkeypatch, capsys):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(p.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(Fake(scene[0]))))
    def missing():
        raise ModuleNotFoundError('vision')
    monkeypatch.setattr(p, 'table_tools', missing)
    assert p.main(['--no-table', '--no-angle', '--no-mic']) == 0
    output = capsys.readouterr().out
    for name in ('table caps:', 'tip band:', 'calibration:', 'microphone:', 'part angle:'):
        assert name not in output


def test_missing_table_reports_fix_and_continues(scene, monkeypatch, capsys):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(p.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(Fake(scene[0]))))
    def missing():
        raise ModuleNotFoundError('vision')
    monkeypatch.setattr(p, 'table_tools', missing)
    assert p.main(['--no-angle', '--no-mic']) == 1
    output = capsys.readouterr().out
    assert 'FIX table caps:' in output and 'FIX tip band:' in output
    assert 'PASS hub parts:' in output and 'PASS protocol:' in output
