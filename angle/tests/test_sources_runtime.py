import json
from pathlib import Path
import subprocess
import sys
import time

import cv2
import httpx
import numpy as np
import pytest

from angle.calibrate import video_pairs, capture_pairs
from angle.run import main
from angle.sources import Reader, Timing, read_jpeg


def video(path, count=20):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (160, 120))
    assert writer.isOpened()
    for i in range(count):
        frame = np.full((120, 160, 3), i*5, np.uint8)
        cv2.line(frame, (80, 15), (80, 105), (255, 255, 255), 3)
        writer.write(frame)
    writer.release()


def test_video_offsets(tmp_path):
    a, b = tmp_path/'a.avi', tmp_path/'b.avi'
    video(a)
    video(b)
    pairs = list(video_pairs([str(a), str(b)], .5, 1.5, .1))
    assert len(pairs) == 3
    for i, (_, first, second) in enumerate(pairs):
        assert first[0, 0, 0] == pytest.approx(i*25, abs=2)
        assert second[0, 0, 0] == pytest.approx(i*25+5, abs=2)


def test_capture_reader_rejects_repeat_and_future(monkeypatch):
    import angle.sources as sources
    _, encoded = cv2.imencode('.jpg', np.zeros((40, 60, 3), np.uint8))
    stamp = [99.95]
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(
        200, headers={'X-Frame-Time': str(stamp[0])}, content=encoded.tobytes())))
    reader = Reader('http://capture/cam/0/frame.jpg')
    reader.client.close()
    reader.client = client
    monkeypatch.setattr(sources.time, 'time', lambda: 100)
    try:
        assert reader.read(0).fresh
        assert not reader.read(0).fresh
        stamp[0] = 101
        assert not reader.read(0).fresh
        stamp[0] = float('nan')
        with pytest.raises(ValueError):
            reader.read(0)
    finally:
        reader.close()


def test_ledger_gaps_and_frame_numbers(tmp_path):
    path = tmp_path/'cam0.avi'
    video(path)
    timing = tmp_path/'timing.jsonl'
    timing.write_text('\n'.join(json.dumps(dict(kind='frame', cam=0, frame=i, t=t))
                                 for i, t in [(0, 1000), (1, 1000.5), (2, 1000.6)]))
    reader = Reader(str(path))
    try:
        assert reader.read(0).image[0, 0, 0] == pytest.approx(0, abs=2)
        assert reader.read(.25).image.size == 0
        assert reader.read(.5).image[0, 0, 0] == pytest.approx(5, abs=2)
        assert reader.read(1) is None
    finally:
        reader.close()


def test_file_overlay_and_idle_gate(tmp_path, capsys):
    source, overlay = tmp_path/'input.avi', tmp_path/'overlay.mp4'
    video(source)
    args = ['--source', str(source), '--roi', '65,5,30,110', '--fixed-roi', '--duration', '.3',
            '--hub', '', '--no-server', '--fast']
    assert main(args) == 0
    assert capsys.readouterr().out == ''
    assert main(args+['--always', '--overlay', str(overlay)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events and events[0]['detail']['rough']
    assert events[0]['detail']['deg'] < 1
    cap = cv2.VideoCapture(str(overlay))
    assert cap.get(cv2.CAP_PROP_FRAME_COUNT) == 3
    cap.release()


@pytest.mark.parametrize('option', [['--fps', 'nan'], ['--duration', 'inf'], ['--hz', '0'], ['--cam', '-1']])
def test_bad_options(option):
    with pytest.raises(ValueError):
        main(['--source', 'unused.mp4', '--no-server', *option])


def test_calibration_capture_generator(monkeypatch):
    import angle.calibrate as calibration
    import angle.sources as sources
    now = [100.]
    _, encoded = cv2.imencode('.jpg', np.zeros((40, 60, 3), np.uint8))
    real_client = httpx.Client
    monkeypatch.setattr(calibration.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, headers={'X-Frame-Time': str(now[0]-.01)}, content=encoded.tobytes()))))
    monkeypatch.setattr(calibration.time, 'time', lambda: now[0])
    monkeypatch.setattr(calibration.time, 'sleep', lambda seconds: None)
    pairs = capture_pairs('http://capture', .1, 1, .1)
    try:
        assert next(pairs)[0] == '0000.png'
        now[0] += .2
        assert next(pairs)[0] == '0001.png'
    finally:
        pairs.close()


def test_health_server_stops_at_eof(tmp_path):
    import socket
    source = tmp_path/'source.avi'
    video(source, count=15)
    probe = socket.socket()
    for port in range(8830, 8860):
        try:
            probe.bind(('127.0.0.1', port))
            break
        except OSError:
            continue
    else:
        probe.close()
        pytest.skip('No test port available')
    probe.close()
    process = subprocess.Popen([sys.executable, '-m', 'angle', '--source', str(source),
                                '--roi', '65,5,30,110', '--fixed-roi', '--always', '--hub', '',
                                '--port', str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    status = None
    try:
        deadline = time.monotonic()+5
        with httpx.Client(timeout=.2) as client:
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    status = client.get(f'http://127.0.0.1:{port}/health').json()
                    if status['frames']:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
        output, error = process.communicate(timeout=8)
        assert process.returncode == 0, error
        assert status and status['part'] == 'angle' and status['frames'] > 0
        events = [json.loads(line) for line in output.splitlines()]
        assert len(events) >= 2 and all(e['t'] > 1_700_000_000 for e in events)
        with socket.socket() as check:
            assert check.connect_ex(('127.0.0.1', port)) != 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
