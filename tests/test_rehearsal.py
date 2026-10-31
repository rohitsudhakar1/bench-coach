"""Dress rehearsal through the launcher with the scripted vision stand-in.

Starts capture (the two 12-27 sample videos, looping, silent mic), the hub, scripts/fake_vision.py
at three times speed, angle, coach and note with scripts/run_demo.py on a free block in
8800-8899 and a temporary runs folder. Starts a run, lets the script play (one tilt, one reused
tip, one skipped dispense), stops it, and checks the coach, the hub, the note and that every
port is released. Nothing is spoken. Skips without BENCH_SAMPLES. About 35 seconds.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = os.environ.get('BENCH_SAMPLES')
CLIPS = [Path(SAMPLES or '.', 'lsv_mv', f'CP-{side}-Front-12-27.mp4') for side in ('Left', 'Right')]
SPEED = 3


def bind(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))


def free_base():
    for base in range(8840, 8895, 6):
        try:
            for port in range(base, base + 6):
                bind(port)
            return base
        except OSError:
            continue
    pytest.skip('no free block of six ports in 8840-8899')


def get(url):
    with urlopen(url, timeout=3) as response:
        return json.load(response)


def post(url):
    request = Request(url, data=b'{}', headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def wait_for(check, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.3)
    pytest.fail(f'timed out waiting for {what}')


def test_dress_rehearsal(tmp_path):
    if not SAMPLES or not all(p.is_file() for p in CLIPS):
        pytest.skip('set BENCH_SAMPLES to the folder holding lsv_mv/')
    base = free_base()
    hub = f'http://127.0.0.1:{base}'
    runs = tmp_path / 'runs'
    command = [sys.executable, str(ROOT / 'scripts/run_demo.py'), '--source', *map(str, CLIPS),
               '--fake-vision', '--fake-speed', str(SPEED), '--base-port', str(base),
               '--runs', str(runs), '--timeout', '40']
    out = tmp_path / 'launcher.out'
    with out.open('w') as stream:
        launcher = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                    start_new_session=True,
                                    env=dict(os.environ, BENCH_PY=sys.executable))
        try:
            wait_for(lambda: launcher.poll() is not None or 'Demo ready' in out.read_text(), 45, 'Demo ready')
            assert launcher.poll() is None, out.read_text()
            run_id = post(f'{hub}/run/start')['run']
            wait_for(lambda: any('Run finished' in e['detail'].get('text', '')
                                 for e in get(f'{hub}/events') if e['kind'] == 'say'), 45 / SPEED + 20, 'the last coach line')
            post(f'{hub}/run/stop')
            state = wait_for(lambda: (s := get(f'{hub}/state'))['note'] and s, 25, 'the note')
            events = get(f'{hub}/events')
            page = urlopen(hub + '/', timeout=3).read().decode()
            lab_note = urlopen(f'{hub}/lab-note/{run_id}?format=html', timeout=5).status
        finally:
            launcher.send_signal(signal.SIGINT)
            try:
                launcher.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(launcher.pid, signal.SIGKILL)
                launcher.wait()
                pytest.fail('launcher did not shut down')
    text = out.read_text()
    assert launcher.returncode == 0, text
    for part in ('capture', 'app', 'vision', 'angle', 'coach', 'note'):
        assert f'{part}: ready' in text, text

    start = next(e['t'] for e in events if e['kind'] == 'run' and e['detail']['status'] == 'start')
    events = [e for e in events if e['t'] >= start]

    # The coach moved through every step, with the planned skips.
    steps = {}
    for e in events:
        if e['kind'] == 'step':
            steps[e['detail']['index']] = e['detail']['status']
    assert [steps[i] for i in range(8)] == ['done', 'done', 'done', 'skipped', 'skipped', 'done', 'skipped', 'done']
    warnings = [e['detail']['text'] for e in events if e['kind'] == 'warning']
    assert any('tilted about 2' in w for w in warnings), warnings
    assert any('Same tip used twice' in w for w in warnings), warnings
    assert any('skipped step 7' in w for w in warnings), warnings
    said = [e['detail']['text'] for e in events if e['kind'] == 'say']
    assert said[-1].startswith('Run finished. Check steps 4, 5 and 7')
    assert any(s.startswith('Next') for s in said)

    # Angle events reached the hub; the angle part raised no false tilt of its own.
    assert [e['detail']['deg'] for e in events if e['kind'] == 'angle'][:2] == [27.0, 28.0]
    assert not [e for e in events if e['source'] == 'angle' and e['kind'] == 'warning']

    # Printed, never spoken.
    assert '[coach says] ' in (runs / 'logs' / 'coach.log').read_text()
    assert '--speak' not in text

    # Recordings and the note sit in the run folder, and the page can show the note.
    folder = runs / run_id
    for name in ('events.jsonl', 'cam0.mp4', 'cam1.mp4', 'mic.wav', 'timing.jsonl', 'note.md', 'note.html'):
        assert (folder / name).is_file(), name
    assert (folder / 'cam0.mp4').stat().st_size > 50_000
    assert state['note']['run'] == run_id
    assert state['note']['url'].startswith(f'http://127.0.0.1:{base + 5}/')
    assert 'note-tab' in page and lab_note == 200
    note = (folder / 'note.md').read_text()
    assert '5 done, 3 skipped' in note
    assert 'Same tip reused' in note
    assert 'Pipette tilted up to 28° from vertical during step 2' in note

    for port in range(base, base + 6):
        bind(port)


def test_fake_vision_script_matches_the_protocol():
    sys.path.insert(0, str(ROOT / 'scripts'))
    import fake_vision
    assert fake_vision.check_script() == len(fake_vision.SCRIPT)
    bad = [(1.0, fake_vision.ev('draw', 'tube Z'))]
    with pytest.raises(ValueError):
        fake_vision.check_script(script=bad)
