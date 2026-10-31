"""Real table pixels -> capture JPEG -> vision -> hub -> coach -> saved note."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import cv2
import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASE = 8860
EXPECTED = ['done', 'done', 'done', 'skipped', 'skipped', 'done', 'skipped', 'done']
OBSERVATIONS = [('tip_on', 'tip box'), ('draw', 'tube A'), ('dispense', 'tube B'),
                ('draw', 'tube C'), ('tip_off', 'waste')]


def make_video(path):
    """Startup lead-in, then reuse the A/B tip in C and eject without dispensing."""
    background = np.full((360, 480, 3), 90, np.uint8)
    points = {'tube A': (100, 60), 'tube B': (220, 60), 'tube C': (340, 60),
              'tip box': (80, 280), 'waste': (400, 280)}
    for name, colour in zip(('tube A', 'tube B', 'tube C'),
                            ((0, 0, 255), (255, 0, 0), (0, 255, 255))):
        cv2.circle(background, points[name], 14, colour, -1)
    cv2.rectangle(background, (55, 255), (105, 305), (150, 150, 150), -1)
    cv2.circle(background, points['waste'], 25, (35, 35, 35), -1)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (480, 360))
    assert writer.isOpened()
    try:
        segments = [(None, 15)]
        for _, name in OBSERVATIONS:
            segments += [((240, 180), 1), (points[name], 1)]
        segments += [((240, 180), 1), (None, 10)]
        for point, seconds in segments:
            frame = background.copy()
            if point:
                cv2.circle(frame, point, 7, (255, 0, 255), -1)
            for _ in range(seconds * 10):
                writer.write(frame)
    finally:
        writer.release()


def assert_ports_free():
    for port in range(BASE, BASE + 6):
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', port))


def test_table_e2e(tmp_path):
    started_at = time.monotonic()
    deadline = started_at + 90
    assert_ports_free()  # Never adopt or stop somebody else's service.
    video, config = tmp_path / 'table.avi', tmp_path / 'table.json'
    make_video(video)
    python = os.environ.get('BENCH_PY') or sys.executable
    env = {k: v for k, v in os.environ.items()
           if k not in ('ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'NVIDIA_API_KEY')}
    subprocess.run([python, '-m', 'vision.table', 'setup', '--source', str(video),
                    '--config', str(config), '--headless', '--tip-box', '80', '280', '25',
                    '--waste', '400', '280', '25'], cwd=ROOT, env=env, check=True,
                   capture_output=True, timeout=10)
    layout = json.loads(config.read_text())
    assert layout['size'] == [480, 360]
    for name, x in [('tube A', 100), ('tube B', 220), ('tube C', 340)]:
        assert abs(layout['regions'][name][0] - x) < 2
    runs, output = tmp_path / 'runs', tmp_path / 'launcher.log'
    command = [python, '-u', 'scripts/run_demo.py', '--table', 'on', '--base-port', str(BASE),
               '--table-source', 'capture:0', '--table-config', str(config),
               '--source', str(video), str(video), '--without', 'angle',
               '--runs', str(runs), '--timeout', '15']
    with output.open('w') as log, httpx.Client(timeout=4, trust_env=False) as client:
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        def diagnostics():
            return '\n'.join(p.name + '\n' + p.read_text()[-6000:]
                             for p in [output, *sorted((runs / 'logs').glob('*.log'))])
        def wait(check):
            while time.monotonic() < deadline:
                assert child.poll() is None, diagnostics()
                result = check()
                if result:
                    return result
                time.sleep(.1)
            raise AssertionError('90-second deadline exceeded\n' + diagnostics())
        hub = f'http://127.0.0.1:{BASE}'
        def get(path):
            response = client.get(hub + path)
            response.raise_for_status()
            return response.json()
        def post(path):
            response = client.post(hub + path, json={})
            response.raise_for_status()
            return response.json()
        try:
            wait(lambda: 'Demo ready' in output.read_text())
            assert not [e for e in get('/events') if e['source'] == 'vision']
            for cam in (0, 1):
                frame = client.get(f'http://127.0.0.1:{BASE+1}/cam/{cam}/frame.jpg')
                frame.raise_for_status()
                assert frame.content.startswith(b'\xff\xd8')
                assert abs(time.time() - float(frame.headers['X-Frame-Time'])) < 2
            run = post('/run/start')
            assert run['capture']['ok']
            wait(lambda: get('/state')['steps'].get('7', {}).get('status') == 'done')
            state = get('/state')
            assert [state['steps'][str(i)]['status'] for i in range(8)] == EXPECTED
            events = get('/events')
            seen = [e for e in events if e['source'] == 'vision']
            assert [(e['kind'], e['object']) for e in seen] == OBSERVATIONS
            assert all(e['detail']['mode'] == 'table' and e['confidence'] == .75 for e in seen)
            steps = [e for e in events if e['source'] == 'coach' and e['kind'] == 'step'
                     and e['detail']['status'] == 'done']
            assert [e['detail']['index'] for e in steps] == [0, 1, 2, 5, 7]
            assert all(e['detail']['evidence'] == 'vision' for e in steps)
            warnings = [e['detail']['text'] for e in events
                        if e['source'] == 'coach' and e['kind'] == 'warning']
            assert any('Same tip used twice' in text and 'tube C' in text for text in warnings)
            assert any('skipped step 7' in text.lower() for text in warnings)
            assert post('/run/stop')['capture']['ok']
            wait(lambda: any(e['kind'] == 'note' and e['detail']['run'] == run['run']
                             for e in get('/events')))
            folder = runs / run['run']
            note = json.loads((folder / 'note.json').read_text())
            assert [s['status'] for s in note['steps']] == EXPECTED
            assert note['counts']['done'] == 5 and note['counts']['skipped'] == 3
            markdown = (folder / 'note.md').read_text()
            assert 'Same tip reused' in markdown
            assert 'Step 7 skipped: Dispense it into tube B' in markdown
            for name in ('cam0.mp4', 'cam1.mp4', 'mic.wav', 'timing.jsonl', 'events.jsonl', 'note.html'):
                assert (folder / name).stat().st_size > 0
            timing = [json.loads(line) for line in (folder / 'timing.jsonl').read_text().splitlines()]
            frame_times = {row['t'] for row in timing if row.get('kind') == 'frame' and row.get('cam') == 0}
            assert all(e['t'] in frame_times for e in seen)
            assert not get('/state')['recording']
            wait(lambda: client.get(f'http://127.0.0.1:{BASE+2}/health').json()['run'] is None)
        finally:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=5)
        assert child.returncode == 0, diagnostics()
    assert_ports_free()
    assert time.monotonic() - started_at < 120
