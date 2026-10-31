"""Every launcher mode, end to end, through scripts/demo.sh.

For each mode: start the launcher on a free block in 8800-8899 with a temporary runs folder,
wait for "Demo ready", start and stop a run, check the lab note and its five downloads (when
note runs), the NVIDIA training tasks and one task ZIP, the trainer, classroom, phone step and
calibration pages, then Ctrl-C and check every port is released and no part logged a
traceback. When Playwright's Chromium is installed it also opens each page and fails on any
JavaScript error; screenshots go to the test's temporary folder.

Modes: scripted vision, real vision, hints, table (generated overhead video), three benches,
and the bundled replay. Real vision and hints skip when the vision models are not cached;
the sample-video modes skip without BENCH_SAMPLES. Nothing is spoken. About 2.5 minutes.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = os.environ.get('BENCH_SAMPLES')
CLIPS = [Path(SAMPLES or '.', 'lsv_mv', f'CP-{side}-Front-12-27.mp4') for side in ('Left', 'Right')]
PAGES = ('/trainer', '/class', '/step', '/calibrate')
DOWNLOADS = {'pdf': b'%PDF', 'zip': b'PK', 'eln': b'PK', 'benchling': b'<', 'csv': b'step'}


def need_samples():
    if not SAMPLES or not all(p.is_file() for p in CLIPS):
        pytest.skip('set BENCH_SAMPLES to the folder holding lsv_mv/')
    return [str(p) for p in CLIPS]


def need_vision_models():
    try:
        from huggingface_hub import try_to_load_from_cache
        sys.path.insert(0, str(ROOT))
        from vision.detector import MODEL_ID
        from vision.hands import MODEL
    except ImportError as exc:
        pytest.skip(f'vision is not installed: {exc}')
    if not isinstance(try_to_load_from_cache(MODEL_ID, 'config.json'), str) or not MODEL.is_file():
        pytest.skip('vision models are not cached; run real vision once to fetch them')


def table_args(tmp_path):
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_table_e2e import make_video
    video, config = tmp_path / 'table.avi', tmp_path / 'table.json'
    make_video(video)
    subprocess.run([sys.executable, '-m', 'vision.table', 'setup', '--source', str(video),
                    '--config', str(config), '--headless', '--tip-box', '80', '280', '25',
                    '--waste', '400', '280', '25'], cwd=ROOT, check=True, capture_output=True, timeout=20)
    return ['--table', '--table-source', 'capture:0', '--table-config', str(config),
            '--source', str(video), str(video), '--without', 'angle']


# name -> (launcher args, seconds to let a run go, benches, note service runs)
def mode(name, tmp_path):
    if name == 'fake':
        return ['--source', *need_samples(), '--fake-vision', '--fake-speed', '5'], 12, 1, True
    if name == 'real':
        clips = need_samples()
        need_vision_models()
        return ['--source', *clips], 8, 1, True
    if name == 'hints':
        clips = need_samples()
        need_vision_models()
        return ['--source', *clips, '--hints'], 8, 1, True
    if name == 'table':
        return table_args(tmp_path), 6, 1, True
    if name == 'benches':
        return ['--benches', '3', '--source', *need_samples(), '--fake-vision', '--fake-speed', '5',
                '--without', 'angle', 'note'], 6, 3, False
    if name == 'replay':
        return ['--replay', 'app/samples/demo.jsonl',
                '--without', 'capture', 'vision', 'angle', 'coach', 'note'], 2, 1, False
    raise ValueError(name)


def bindable(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def free_block(size):
    for base in range(8800, 8900 - size + 1, 6):
        if all(bindable(p) for p in range(base, base + size)):
            return base
    pytest.skip(f'no free block of {size} ports in 8800-8899')


def wait(check, timeout, what, child, log):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert child.poll() is None, f'launcher exited while waiting for {what}\n' + log.read_text()[-4000:]
        value = check()
        if value:
            return value
        time.sleep(.3)
    pytest.fail(f'timed out waiting for {what}\n' + log.read_text()[-4000:])


def browser():
    try:
        from playwright.sync_api import sync_playwright
        manager = sync_playwright().start()
        return manager, manager.chromium.launch()
    except Exception:
        return None, None


@pytest.mark.parametrize('name', ['replay', 'fake', 'table', 'benches', 'real', 'hints'])
def test_mode(name, tmp_path):
    args, run_seconds, benches, has_note = mode(name, tmp_path)
    base = free_block(6 * benches)
    hub = f'http://127.0.0.1:{base}'
    runs, log = tmp_path / 'runs', tmp_path / 'launcher.log'
    env = {k: v for k, v in os.environ.items()
           if k not in ('ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'NVIDIA_API_KEY')}
    env['BENCH_PY'] = sys.executable
    command = ['sh', str(ROOT / 'scripts/demo.sh'), *args, '--base-port', str(base),
               '--runs', str(runs), '--timeout', '90']
    errors = []
    manager, chrome = browser()
    with log.open('w') as out, httpx.Client(timeout=60, trust_env=False) as client:
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        try:
            wait(lambda: 'Demo ready' in log.read_text(), 120, 'Demo ready', child, log)
            if name == 'replay':
                wait(lambda: 'replay: finished' in log.read_text(), 20, 'the replay', child, log)
                state = client.get(hub + '/state').json()
                assert state['steps']['1']['status'] == 'done'
            page = None
            if chrome:
                page = chrome.new_page(viewport={'width': 1400, 'height': 900})
                page.on('pageerror', lambda e: errors.append(f'/: {e}'))
                page.goto(hub + '/')
                page.wait_for_selector('#run:not([disabled])', timeout=10_000)

            run_ids = []
            for bench in range(1, benches + 1):
                prefix = '' if bench == 1 else f'/benches/{bench}'
                started = client.post(hub + prefix + '/run/start', json={})
                assert started.status_code == 200, started.text
                run_ids.append(started.json()['run'])
            if benches > 1:
                rows = client.get(hub + '/class/state').json()
                assert sorted(r['bench'] for r in rows if r['recording']) == [str(b) for b in range(1, benches + 1)]
            time.sleep(run_seconds)
            for bench in range(1, benches + 1):
                prefix = '' if bench == 1 else f'/benches/{bench}'
                stopped = client.post(hub + prefix + '/run/stop', json={})
                assert stopped.status_code == 200, stopped.text
            run = run_ids[0]
            if name not in ('replay',):
                steps = client.get(hub + '/state').json()['steps']
                assert steps, 'the coach marked no step'

            if has_note:
                wait(lambda: (client.get(hub + '/state').json().get('note') or {}).get('run') == run,
                     30, 'the note', child, log)
                for fmt, magic in DOWNLOADS.items():
                    response = client.get(f'{hub}/lab-note/{run}', params={'format': fmt})
                    assert response.status_code == 200, (fmt, response.text[:300])
                    assert response.content.startswith(magic), fmt
                assert client.get(f'{hub}/lab-note/{run}', params={'format': 'html'}).status_code == 200

            export = wait(lambda: (e := client.get(f'{hub}/nvidia/{run}').json()['export'])
                          .get('status') in ('ready', 'failed') and e, 60, 'the NVIDIA export', child, log)
            assert export['status'] == 'ready' and export['tasks'], export
            task = client.get(f"{hub}/nvidia/{run}/tasks/{export['tasks'][0]['id']}.zip")
            assert task.status_code == 200 and task.content.startswith(b'PK')

            for path in PAGES:
                assert client.get(hub + path).status_code == 200, path
            assert client.get(hub + '/trainer/runs').status_code == 200
            assert client.get(hub + '/calibrate/state').status_code == 200

            if page:
                page.click('#note-tab')
                if has_note:
                    page.wait_for_selector('#note-content iframe', timeout=15_000)
                    assert not page.is_hidden('#pdf')
                else:
                    page.wait_for_function("/not running/.test(document.querySelector('#note-content').textContent)",
                                           timeout=15_000)
                page.screenshot(path=str(tmp_path / f'{name}-labnote.png'))
                for path in PAGES:
                    other = chrome.new_page(viewport={'width': 1400, 'height': 900})
                    other.on('pageerror', lambda e, p=path: errors.append(f'{p}: {e}'))
                    other.goto(hub + path)
                    other.wait_for_timeout(800)
                    other.screenshot(path=str(tmp_path / f'{name}{path.replace("/", "-")}.png'))
                    other.close()
        finally:
            if chrome:
                chrome.close()
                manager.stop()
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                pytest.fail('launcher did not stop within 30 seconds')
    assert child.returncode == 0, log.read_text()[-4000:]
    assert not errors, errors
    for part_log in (runs / 'logs').glob('*.log'):
        assert 'Traceback' not in part_log.read_text(errors='replace'), part_log.name
    if has_note:
        assert (runs / run / 'note.md').is_file()
        assert json.loads((runs / run / 'note.json').read_text())['steps']
    time.sleep(.5)
    assert all(bindable(p) for p in range(base, base + 6 * benches)), 'a port is still held'
