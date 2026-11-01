import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import wave

import cv2
from fastapi.testclient import TestClient
import numpy as np
import pytest
from starlette.websockets import WebSocketDisconnect

from capture.media import Camera, RATE, wav_pcm
from capture.recording import DISK_RESERVE
from capture.service import Capture, create_app, mjpeg_chunks
from capture.phone import certificate, print_phone_url


def until(predicate, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError('condition timed out')


def jpeg(size=(48, 64), value=100):
    return cv2.imencode('.jpg', np.full((*size, 3), value, np.uint8))[1].tobytes()


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / 'input.mp4'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 20, (64, 48))
    assert writer.isOpened()
    for n in range(12):
        writer.write(np.full((48, 64, 3), n * 15, np.uint8))
    writer.release()
    return str(path)


def seeded(tmp_path):
    capture = Capture(['phone:0', 'phone:1'], tmp_path, fake_mic=True)
    for cam in capture.cameras:
        cam.receive(jpeg(), time.time())
    return capture


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_http_recording_and_concurrent_stops(tmp_path, clip):
    capture = Capture([clip, clip], tmp_path / 'runs', fake_mic=True, loop=True)
    with TestClient(create_app(capture)) as client:
        until(lambda: all(c.latest for c in capture.cameras))
        assert client.get('/cam/5/frame.jpg').status_code == 404
        a = client.get('/cam/0/frame.jpg')
        assert a.headers['content-type'] == 'image/jpeg'
        assert time.time() - float(a.headers['x-frame-time']) < 1
        folder = tmp_path / 'runs' / 'shared'
        folder.mkdir(parents=True)
        (folder / 'events.jsonl').write_text('hub data\n')
        assert client.post('/record/start', json={'run': '../escape'}).status_code == 422
        assert client.post('/record/start', json={'run': 'shared'}).status_code == 200
        assert client.post('/record/start', json={'run': 'other'}).status_code == 409
        with client.websocket_connect('/mic') as ws:
            header, pcm = ws.receive_json(), ws.receive_bytes()
            assert header['n'] == 320 and len(pcm) == 640 and not any(pcm)
        time.sleep(0.7)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: client.post('/record/stop').json(), range(4)))
        assert all(result == results[0] for result in results)
        assert results[0]['error'] is None and not results[0]['recording']
        assert client.get('/record/status').json() == results[0]
        assert client.post('/record/start', json={'run': 'shared'}).status_code == 409
        assert (folder / 'events.jsonl').read_text() == 'hub data\n'
        timing = rows(folder / 'timing.jsonl')
        assert timing[0]['kind'] == 'start' and timing[-1]['kind'] == 'stop'
        for i in (0, 1):
            frames = [r for r in timing if r['kind'] == 'frame' and r['cam'] == i]
            video = cv2.VideoCapture(str(folder / f'cam{i}.mp4'))
            assert video.isOpened() and video.get(cv2.CAP_PROP_FRAME_COUNT) == len(frames)
            assert len(frames) >= 8
            video.release()
            assert [r['frame'] for r in frames] == list(range(len(frames)))
            assert all(r['media_time'] is not None for r in frames)
        audio = [r for r in timing if r['kind'] == 'audio']
        with wave.open(str(folder / 'mic.wav')) as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
            assert wav.getnframes() == sum(r['n'] for r in audio)
        assert [r['sample'] for r in audio] == list(range(0, sum(r['n'] for r in audio), 320))
    assert all(not t.is_alive() for t in capture.threads)


def test_loop_and_eof(tmp_path, clip):
    for loop in (False, True):
        capture = Capture([clip, clip], tmp_path, fake_mic=True, loop=loop)
        capture.start()
        try:
            until(lambda: all(c.latest for c in capture.cameras))
            if loop:
                until(lambda: all(c.latest.media_time > 0.7 for c in capture.cameras))
                assert not any(c.ended for c in capture.cameras)
                assert abs(capture.cameras[0].latest.media_time - capture.cameras[1].latest.media_time) < .11
            else:
                until(lambda: all(c.ended for c in capture.cameras))
                assert not capture.cameras[0].status()['live']
                assert capture.cameras[0].latest.media_time == pytest.approx(.55)
        finally:
            capture.close()


def test_camera_reconnect_does_not_stop_other_recording(tmp_path, monkeypatch):
    capture = Capture([0, 1], tmp_path, fake_mic=True)
    opens = [[], []]
    fail = threading.Event()
    class Device:
        def __init__(self, i): self.i = i
        def isOpened(self): return True
        def get(self, prop): return 20
        def set(self, *args): return True
        def release(self): pass
        def read(self):
            time.sleep(.03)
            if self.i == 0 and fail.is_set(): return False, None
            return True, np.full((48, 64, 3), 100, np.uint8)
    def open_source(cam):
        opens[cam.index].append(time.monotonic())
        return Device(cam.index)
    monkeypatch.setattr(Camera, 'open_source', open_source)
    capture.start()
    try:
        until(lambda: all(c.latest for c in capture.cameras))
        capture.recorder.start('drop')
        fail.set()
        until(lambda: capture.cameras[0].status()['reconnecting'])
        before = capture.cameras[1].latest.sequence
        until(lambda: len(opens[0]) >= 3)
        assert capture.cameras[1].latest.sequence > before + 10
        assert opens[0][2] - opens[0][1] >= .49
        old = capture.cameras[0].latest.sequence
        fail.clear()
        until(lambda: capture.cameras[0].latest.sequence > old)
        assert not capture.cameras[0].status()['reconnecting']
        result = capture.recorder.stop()
        assert result['error'] is None
        timing = rows(tmp_path / 'drop' / 'timing.jsonl')
        assert sum(r['kind']=='frame' and r['cam']==1 for r in timing) > 10
    finally:
        capture.close()


def test_low_disk_autostop_and_start_refusal(tmp_path, monkeypatch):
    capture = seeded(tmp_path)
    capture.recorder.start('low')
    monkeypatch.setattr('capture.recording.shutil.disk_usage', lambda _: SimpleNamespace(free=DISK_RESERVE-1))
    result = until(lambda: capture.recorder.status() if not capture.recorder.status()['recording'] else None)
    assert '16 MiB' in result['error']
    assert capture.recorder.stop() == result
    assert rows(tmp_path / 'low' / 'timing.jsonl')[-1]['error'] == result['error']
    with wave.open(str(tmp_path / 'low' / 'mic.wav')) as wav:
        assert wav.getframerate() == RATE
    with pytest.raises(OSError, match='16 MiB'):
        capture.recorder.start('refused')


def test_phone_validation_reconnect_and_record(tmp_path):
    capture = Capture(['phone:0', 'phone:1'], tmp_path, fake_mic=True)
    with TestClient(create_app(capture)) as client:
        assert client.get('/phone').status_code == 200
        assert client.get('/cam/0/frame.jpg').status_code == 503
        assert client.post('/record/start').status_code == 409
        with client.websocket_connect('/phone/0') as a, client.websocket_connect('/phone/1') as b:
            assert a.receive_json()['ready'] and b.receive_json()['ready']
            for ws in (a,b):
                start = time.time()
                ws.send_bytes(jpeg())
                assert start <= ws.receive_json()['t'] <= time.time()
            with client.websocket_connect('/phone/0') as duplicate:
                with pytest.raises(WebSocketDisconnect): duplicate.receive_json()
            assert client.post('/record/start', json={'run':'phone'}).status_code == 200
            for ws in (a,b):
                ws.send_bytes(jpeg(size=(96,128)))
                assert ws.receive_json()['sequence'] == 1
            assert capture.cameras[0].latest.image.shape == (48,64,3)
        until(lambda: capture.cameras[0].status()['reconnecting'])
        with client.websocket_connect('/phone/0') as ws:
            ws.receive_json()
            ws.send_bytes(jpeg())
            assert ws.receive_json()['sequence'] == 2
            ws.send_text('not a JPEG')
            with pytest.raises(WebSocketDisconnect) as exc: ws.receive_json()
            assert exc.value.code == 1003
        assert client.post('/record/stop').json()['error'] is None
        with client.websocket_connect('/phone/1') as ws:
            ws.receive_json()
            ws.send_bytes(b'bad')
            with pytest.raises(WebSocketDisconnect) as exc: ws.receive_json()
            assert exc.value.code == 1007
        with client.websocket_connect('/phone/3') as ws:
            with pytest.raises(WebSocketDisconnect): ws.receive_json()


def test_mjpeg_waits_through_phone_disconnect(tmp_path):
    capture = seeded(tmp_path)
    async def check():
        cam = capture.cameras[0]
        stream = mjpeg_chunks(cam, capture.done)
        assert b'X-Frame-Time:' in await anext(stream)
        cam.disconnect()
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(.03)
        assert not task.done()
        cam.receive(jpeg(), time.time())
        assert b'Content-Type: image/jpeg' in await asyncio.wait_for(task, 1)
        await stream.aclose()
    asyncio.run(check())


def test_wav_waits_for_first_listener_and_sends_silence_after(tmp_path):
    path = tmp_path / 'voice.wav'
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((2,2,8000,0,'NONE','not compressed'))
        wav.writeframes(np.full((800,2), 2000, dtype='<i2').tobytes())
    assert len(wav_pcm(path)) == 3200
    capture = Capture(['phone:0','phone:1'], tmp_path, mic_file=path)
    with TestClient(create_app(capture)) as client:
        time.sleep(.12)
        assert not any(capture.audio.chunks[-1][2])
        start = time.monotonic()
        with client.websocket_connect('/mic') as ws:
            heard = []
            for _ in range(8):
                header, pcm = ws.receive_json(), ws.receive_bytes()
                assert header['n'] * 2 == len(pcm)
                heard.append(any(pcm))
            assert any(heard[:2]) and not heard[-1]
        assert time.monotonic() - start >= .12


def test_microphone_failure_closes_socket(tmp_path):
    capture = seeded(tmp_path)
    capture.audio.error = 'test unavailable'
    with TestClient(create_app(capture)) as client:
        with client.websocket_connect('/mic') as ws:
            with pytest.raises(WebSocketDisconnect) as exc: ws.receive_json()
            assert exc.value.code == 1011
        assert client.get('/cam/0/frame.jpg').status_code == 200


def test_certificate_reuse_and_qr(tmp_path, capsys):
    cert, key = certificate(tmp_path / 'tls', '127.0.0.1')
    before = cert.read_bytes()
    assert key.stat().st_mode & 0o777 == 0o600
    assert certificate(tmp_path / 'tls','127.0.0.1') == (cert,key)
    assert cert.read_bytes() == before
    print_phone_url('127.0.0.1', 8844)
    assert 'https://127.0.0.1:8844/phone' in capsys.readouterr().out


@pytest.mark.parametrize('pair', ['12-27','249-269','414-445'])
def test_supplied_footage(tmp_path, pair):
    paths = [Path(f'{os.environ.get('BENCH_SAMPLES', '')}/lsv_mv/CP-{side}-Front-{pair}.mp4') for side in ('Left','Right')]
    if not all(p.exists() for p in paths): pytest.skip('sample footage not installed')
    capture = Capture(list(map(str, paths)), tmp_path, fake_mic=True, loop=True)
    capture.start()
    try:
        until(lambda: all(c.latest for c in capture.cameras))
        before = [c.latest.sequence for c in capture.cameras]
        until(lambda: all(c.latest.sequence > n+2 for c,n in zip(capture.cameras,before)))
        assert all(time.time()-c.latest.t < .5 for c in capture.cameras)
    finally:
        capture.close()


def test_encoder_fallback_and_cleanup(tmp_path, monkeypatch):
    capture = seeded(tmp_path)
    monkeypatch.setattr('capture.recording.shutil.which', lambda _: None)
    actual = cv2.VideoWriter
    attempted = []
    class Unavailable:
        def isOpened(self): return False
        def release(self): pass
    def writer(path, codec, fps, size):
        attempted.append(codec)
        return Unavailable() if codec == cv2.VideoWriter_fourcc(*'avc1') else actual(path,codec,fps,size)
    monkeypatch.setattr('capture.recording.cv2.VideoWriter', writer)
    result = capture.recorder.start('fallback')
    assert result['codecs'] == ['mp4v','mp4v']
    capture.recorder.stop()
    assert len(attempted) == 2
    monkeypatch.setattr('capture.recording.cv2.VideoWriter', lambda *args: Unavailable())
    folder = tmp_path / 'failed'
    folder.mkdir()
    (folder/'events.jsonl').write_text('preserved')
    with pytest.raises(OSError): capture.recorder.start('failed')
    assert sorted(p.name for p in folder.iterdir()) == ['events.jsonl']


def test_queue_overload_reports_drops(tmp_path, monkeypatch):
    capture = seeded(tmp_path)
    monkeypatch.setattr('capture.recording.shutil.which', lambda _: None)
    actual = cv2.VideoWriter
    entered, release = threading.Event(), threading.Event()
    class SlowWriter:
        def __init__(self, *args): self.writer = actual(*args)
        def isOpened(self): return self.writer.isOpened()
        def release(self): self.writer.release()
        def write(self, image):
            entered.set()
            assert release.wait(3)
            self.writer.write(image)
    monkeypatch.setattr('capture.recording.cv2.VideoWriter', SlowWriter)
    capture.recorder.start('slow')
    try:
        capture.recorder.submit('frame', 0, capture.cameras[0].latest)
        assert entered.wait(1)
        for _ in range(300): capture.recorder.submit('audio',0,(time.time(),bytes(640)))
        assert capture.recorder.status()['dropped'] == 44
    finally:
        release.set()
        result = capture.recorder.stop()
    assert result['dropped'] == 44 and result['error'] is None
    assert rows(tmp_path/'slow'/'timing.jsonl')[-1]['dropped'] == 44


def test_writer_exception_closes_files(tmp_path, monkeypatch):
    capture = seeded(tmp_path)
    monkeypatch.setattr('capture.recording.shutil.which', lambda _: None)
    actual = cv2.VideoWriter
    class BrokenWriter:
        def __init__(self, *args): self.writer = actual(*args)
        def isOpened(self): return self.writer.isOpened()
        def release(self): self.writer.release()
        def write(self, image): raise OSError('injected disk error')
    monkeypatch.setattr('capture.recording.cv2.VideoWriter', BrokenWriter)
    capture.recorder.start('broken')
    capture.recorder.submit('frame',0,capture.cameras[0].latest)
    result = until(lambda: capture.recorder.status() if not capture.recorder.status()['recording'] else None)
    assert result['error'] == 'injected disk error'
    assert rows(tmp_path/'broken'/'timing.jsonl')[-1]['error'] == result['error']
    with wave.open(str(tmp_path/'broken'/'mic.wav')) as wav:
        assert wav.getnchannels() == 1


def test_phone_stale_size_and_slot_validation(tmp_path):
    with pytest.raises(ValueError): Capture(['phone:1','phone:0'],tmp_path)
    capture = seeded(tmp_path)
    cam = capture.cameras[0]
    cam.receive(jpeg(),time.time()-4)
    assert not cam.status()['live'] and cam.status()['reconnecting']
    with pytest.raises(ValueError): cam.receive(b'\xff\xd8'+bytes(2*1024*1024),time.time())
    with pytest.raises(ValueError): cam.receive(jpeg(size=(2,1922)),time.time())
    assert capture.recorder.root == tmp_path
    assert Capture(['phone:0','phone:1'],fake_mic=True).recorder.root == Path(__file__).resolve().parents[2]/'runs'


def test_queue_byte_limit_and_failed_writer_releases_frames(tmp_path, monkeypatch):
    import weakref
    from capture.media import Frame
    capture = seeded(tmp_path)
    entered, release = threading.Event(), threading.Event()
    class FailingWriter:
        def __init__(self, *args): pass
        def isOpened(self): return True
        def release(self): pass
        def write(self, image):
            entered.set()
            assert release.wait(3)
            raise OSError('injected encoder failure')
    monkeypatch.setattr('capture.recording.shutil.which', lambda _: None)
    monkeypatch.setattr('capture.recording.cv2.VideoWriter', FailingWriter)
    monkeypatch.setattr('capture.recording.QUEUE_BYTES', 10000)
    capture.recorder.start('bounded')
    state = capture.recorder.active
    capture.recorder.submit('frame', 0, capture.cameras[0].latest)
    assert entered.wait(1)
    frame = Frame(time.time(), 2, np.zeros((48, 64, 3), np.uint8), b'jpeg')
    ref = weakref.ref(frame)
    try:
        capture.recorder.submit('frame', 0, frame)
        capture.recorder.submit('frame', 0, frame)
        assert state['_bytes'] <= 10000
        assert state['dropped'] == 1
        del frame
    finally:
        release.set()
        result = capture.recorder.stop()
    assert result['error'] == 'injected encoder failure'
    assert result['dropped'] == 2
    assert state['_queue'].empty() and state['_bytes'] == 0
    assert ref() is None


def test_one_camera_serves_and_records(tmp_path, clip):
    capture = Capture([clip], tmp_path / 'runs', fake_mic=True, loop=True)
    with TestClient(create_app(capture)) as client:
        until(lambda: capture.cameras[0].latest)
        assert len(client.get('/cams').json()) == 1
        assert client.get('/cam/0/frame.jpg').status_code == 200
        assert client.get('/cam/1/frame.jpg').status_code == 404
        assert client.post('/record/start', json={'run': 'solo'}).status_code == 200
        time.sleep(0.5)
        assert client.post('/record/stop').json()['error'] is None
    folder = tmp_path / 'runs' / 'solo'
    assert (folder / 'cam0.mp4').is_file() and not (folder / 'cam1.mp4').exists()
    assert {r.get('cam') for r in rows(folder / 'timing.jsonl') if r['kind'] == 'frame'} == {0}
    with pytest.raises(ValueError):
        Capture([clip, clip, clip], tmp_path)
