"""Fake parts use only rehearsal ports; no devices, sound, or models."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

from scripts import run_demo as demo


@pytest.fixture
def base():
    for base in range(8800, 8895, 6):
        held = []
        try:
            for port in range(base, base + 6):
                sock = socket.socket()
                held.append(sock)
                sock.bind(('127.0.0.1', port))
        except OSError:
            continue
        finally:
            for sock in held:
                sock.close()
        return base
    pytest.skip('no free rehearsal port block')


FAKE = '''import argparse, json, os, signal, subprocess, sys, threading, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
name = __package__
p = argparse.ArgumentParser()
p.add_argument('--port', type=int)
a, rest = p.parse_known_args()
with open('starts.jsonl', 'a') as f:
    f.write(json.dumps({'name': name, 'args': rest, 'pid': os.getpid(), 'hub': os.getenv('APP_URL')})+'\\n')
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
    def do_POST(self):
        row = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with open('events.jsonl', 'a') as f: f.write(json.dumps(row)+'\\n')
        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
if name == 'coach' and os.getenv('CRASH_COACH'):
    marker = Path('crashed')
    if not marker.exists() or os.getenv('CRASH_COACH') == 'always':
        marker.touch()
        def crash(): time.sleep(.5); os._exit(7)
        threading.Thread(target=crash, daemon=True).start()
if name == 'note' and os.getenv('DESCENDANT'):
    child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(120)'])
    Path('descendant.pid').write_text(str(child.pid))
HTTPServer(('127.0.0.1', a.port), Handler).serve_forever()
'''


@pytest.fixture
def fake_root(tmp_path):
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/run_demo.py').write_text(Path(demo.__file__).read_text())
    for name in demo.OFFSETS:
        (tmp_path / name).mkdir()
        (tmp_path / name / '__init__.py').touch()
        (tmp_path / name / '__main__.py').write_text(FAKE)
    return tmp_path


def wait_for(predicate, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('timed out')


def start(root, base, extra=(), env=None):
    log = root / 'launcher.log'
    with log.open('w') as out:
        process = subprocess.Popen([sys.executable, str(root / 'scripts/run_demo.py'), '--base-port', str(base), '--timeout', '3', *extra], cwd=root,
            env={**os.environ, 'BENCH_PY': sys.executable, **(env or {})}, stdout=out, stderr=subprocess.STDOUT)
    return process, log


def stop(process):
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
    process.wait(timeout=12)


def released(base):
    for port in range(base, base + 6):
        with socket.socket() as sock:
            assert sock.connect_ex(('127.0.0.1', port)) != 0


def test_start_order_restart_replay_and_ctrl_c(fake_root, base):
    replay = fake_root / 'input.jsonl'
    replay.write_text('\n'.join(json.dumps(dict(t=100+i*.05, kind='speech', source='coach', detail={'text': 'testing'})) for i in range(2)))
    process, log = start(fake_root, base, ['--replay', str(replay)], {'CRASH_COACH': 'once'})
    try:
        wait_for(lambda: 'replay: finished' in log.read_text() and 'restarting once' in log.read_text())
        wait_for(lambda: len((fake_root / 'starts.jsonl').read_text().splitlines()) == 7)
        rows = [json.loads(line) for line in (fake_root / 'starts.jsonl').read_text().splitlines()]
        assert [r['name'] for r in rows[:6]] == list(demo.OFFSETS)
        assert [r['name'] for r in rows].count('coach') == 2
        assert all(r['hub'] == f'http://127.0.0.1:{base}' for r in rows)
        assert rows[5]['args'][0] == 'serve'
        events = [json.loads(line) for line in (fake_root / 'events.jsonl').read_text().splitlines()]
        assert len(events) == 2 and abs(time.time() - events[0]['t']) < 10
        assert events[1]['t'] - events[0]['t'] == pytest.approx(.05)
        assert process.poll() is None
    finally:
        stop(process)
    assert process.returncode == 0, log.read_text()
    released(base)
    assert all((fake_root / 'runs/logs' / (name + '.log')).exists() for name in demo.OFFSETS)


def test_second_crash_fails_and_stops_all(fake_root, base):
    process, log = start(fake_root, base, env={'CRASH_COACH': 'always'})
    try:
        process.wait(timeout=15)
        assert process.returncode == 1
        assert log.read_text().count('restarting once') == 1
    finally:
        stop(process)
    released(base)


def test_occupied_port_does_not_start_children(fake_root, base):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', base + 3))
        sock.listen()
        process, log = start(fake_root, base)
        try:
            process.wait(timeout=10)
            assert process.returncode == 1
            assert 'already in use' in log.read_text()
            assert not (fake_root / 'starts.jsonl').exists()
        finally:
            stop(process)


def test_startup_timeout_stops_child(fake_root, base):
    (fake_root / 'capture/__main__.py').write_text('import time\ntime.sleep(120)\n')
    process, log = start(fake_root, base, ['--timeout', '.2'])
    try:
        process.wait(timeout=10)
        assert process.returncode == 1
        assert 'health timed out' in log.read_text()
    finally:
        stop(process)
    released(base)


def test_plan_inputs_table_and_missing(fake_root, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('BENCH_PY', sys.executable)
    files = [tmp_path / 'a.mp4', tmp_path / 'b.mp4']
    for file in files:
        file.touch()
    mic = tmp_path / 'mic.wav'
    mic.touch()
    args = demo.parse_args(['--source', *map(str, files), '--mic-file', str(mic), '--hints', '--base-port', '8880', '--angle-roi', '1,2,3,4'])
    parts = {p.name: p for p in demo.plan(args, fake_root)}
    assert '--loop' in parts['capture'].command
    assert '--fake-mic' not in parts['capture'].command
    assert '--mic-file' in parts['capture'].command
    assert '--mic' not in parts['coach'].command
    assert '--hints' in parts['coach'].command
    assert '--new-draw' in parts['vision'].command
    assert 'http://127.0.0.1:8881/cam/0/frame.jpg' in parts['angle'].command
    args.mic_file = None
    parts = {p.name: p for p in demo.plan(args, fake_root)}
    assert '--fake-mic' in parts['capture'].command
    assert parts['coach'].command[-3:] == ['--mic', 'off', '--hints']
    (fake_root / 'vision/table.py').touch()
    config = tmp_path / 'table.json'
    config.write_text('{}')
    args = demo.parse_args(['--table', '--table-source', '3', '--cams', '3', '4', '--table-config', str(config), '--base-port', '8880'])
    vision = next(p for p in demo.plan(args, fake_root) if p.name == 'vision')
    assert 'http://127.0.0.1:8881/cam/0/frame.jpg' in vision.command
    args.table_source = 'capture:1'
    args.without = ['capture']
    with pytest.raises(ValueError, match='needs capture'):
        demo.plan(args, fake_root)
    (fake_root / 'coach/__main__.py').unlink()
    args = demo.parse_args([])
    assert 'coach' not in [p.name for p in demo.plan(args, fake_root)]
    assert 'no runnable entry point' in capsys.readouterr().out


@pytest.mark.parametrize('argv', [['--base-port', '65531'], ['--timeout', 'nan'], ['--source', 'one'], ['--cams', '0', '0'], ['--angle-roi', '1,2,0,4'], ['--table-source', 'capture:2'], ['--fake-mic', '--mic-file', 'x']])
def test_bad_arguments(argv):
    with pytest.raises(SystemExit):
        demo.parse_args(argv)


def test_discovery_avoids_inert_service(tmp_path):
    (tmp_path / 'note').mkdir()
    (tmp_path / 'note/service.py').write_text('app = object()')
    assert demo.entrypoint(tmp_path, 'note') is None
    (tmp_path / 'note/__main__.py').write_text('pass')
    assert demo.entrypoint(tmp_path, 'note') == 'note'
    (tmp_path / 'capture').mkdir()
    (tmp_path / 'capture/service.py').write_text('if __name__ == "__main__": pass')
    assert demo.entrypoint(tmp_path, 'capture') == 'capture.service'


def test_process_group_removes_descendant(fake_root, base):
    process, log = start(fake_root, base, ['--without', 'capture', 'app', 'vision', 'angle', 'coach'], {'DESCENDANT': '1'})
    try:
        wait_for(lambda: 'Demo ready' in log.read_text())
        pid = int((fake_root / 'descendant.pid').read_text())
        os.kill(pid, 0)
    finally:
        stop(process)
    def gone():
        try:
            os.kill(pid, 0)
            return False
        except ProcessLookupError:
            return True
    wait_for(gone)
    released(base)


def test_existing_parts_with_generated_video(tmp_path, base):
    import cv2
    import numpy as np
    files = []
    for i in range(2):
        path = tmp_path / f'cam{i}.avi'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (160, 120))
        assert writer.isOpened()
        for index in range(12):
            frame = np.zeros((120, 160, 3), np.uint8)
            frame[:, :, i] = 80 + index * 5
            writer.write(frame)
        writer.release()
        files.append(str(path))
    log = tmp_path / 'real-parts.log'
    env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'NVIDIA_API_KEY')}
    env['BENCH_PY'] = sys.executable
    with log.open('w') as out:
        process = subprocess.Popen([sys.executable, demo.__file__, '--source', *files,
            '--without', 'app', 'vision', 'coach', '--base-port', str(base),
            '--runs', str(tmp_path / 'runs'), '--timeout', '15'], cwd=demo.ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
    try:
        wait_for(lambda: 'Demo ready' in log.read_text() or process.poll() is not None, seconds=25)
        assert process.poll() is None, log.read_text()
        assert all(f'{name}: ready' in log.read_text() for name in ('capture', 'angle', 'note'))
        time.sleep(1.5)  # Past file EOF: capture must still serve looped frames.
        from urllib.request import urlopen
        with urlopen(f'http://127.0.0.1:{base+1}/cam/0/frame.jpg', timeout=2) as response:
            assert time.time() - float(response.headers['X-Frame-Time']) < 1
    finally:
        stop(process)
    assert process.returncode == 0, log.read_text()
    released(base)


@pytest.mark.parametrize('name', ['coach', 'note'])
def test_restart_must_become_healthy(fake_root, base, name):
    (fake_root / name / '__main__.py').write_text('''from pathlib import Path
import time
p = Path('once')
if not p.exists():
    p.touch()
    raise SystemExit(1)
time.sleep(120)
''')
    excluded = [part for part in demo.OFFSETS if part != name]
    process, log = start(fake_root, base, ['--without', *excluded, '--timeout', '.3'])
    try:
        process.wait(timeout=10)
        assert process.returncode == 1
        assert log.read_text().count('restarting once') == 1
        assert 'health timed out' in log.read_text()
    finally:
        stop(process)
    released(base)


def test_table_hints_rejected_even_without_vision():
    with pytest.raises(SystemExit):
        demo.parse_args(['--table', '--hints'])


def test_bad_replay_rejected(tmp_path):
    path = tmp_path / 'events.jsonl'
    for rows in ([{'t': float('nan'), 'kind': 'draw', 'source': 'vision'}],
                 [{'t': 2, 'kind': 'draw', 'source': 'vision'}, {'t': 1, 'kind': 'draw', 'source': 'vision'}],
                 [{'t': 1, 'source': 'vision'}]):
        path.write_text('\n'.join(json.dumps(row) for row in rows))
        with pytest.raises(ValueError):
            demo.replay_rows(path)


def test_fake_vision_and_angle_without_roi(fake_root, monkeypatch):
    monkeypatch.setenv('BENCH_PY', sys.executable)
    args = demo.parse_args(['--fake-vision', '--fake-speed', '3', '--base-port', '8880'])
    parts = {p.name: p for p in demo.plan(args, fake_root)}
    command = parts['vision'].command
    assert command[2].endswith('scripts/fake_vision.py')
    assert command[3:] == ['--port', '8882', '--hub', 'http://127.0.0.1:8880', '--speed', '3.0']
    # Without a chosen crop, angle may only measure inside vision's boxes.
    assert '--need-box' in parts['angle'].command
    args = demo.parse_args(['--angle-roi', '1,2,3,4', '--base-port', '8880'])
    assert '--need-box' not in next(p for p in demo.plan(args, fake_root) if p.name == 'angle').command
    with pytest.raises(SystemExit):
        demo.parse_args(['--fake-vision', '--table'])
