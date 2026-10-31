import os
from pathlib import Path

import pytest

from scripts.run_demo import parse_args, plan


def test_three_bench_plan(tmp_path):
    videos = [tmp_path/'left.mp4', tmp_path/'right.mp4']
    for file in videos:
        file.touch()
    args = parse_args(['--benches', '3', '--base-port', '8820', '--source', *map(str, videos)])
    parts = plan(args)
    assert len(parts) == 16
    assert len({p.port for p in parts}) == 16
    assert parts[0].name == 'app'
    for n in (1, 2, 3):
        capture = next(p for p in parts if p.name == f'capture-{n}')
        coach = next(p for p in parts if p.name == f'coach-{n}')
        assert capture.port == 8821 + 6*(n-1)
        assert all(str(file) in capture.command for file in videos)
        assert '--fake-mic' in capture.command and '--loop' in capture.command
        assert coach.command[coach.command.index('--hub')+1] == f'http://127.0.0.1:8820/benches/{n}'
        assert coach.command[coach.command.index('--mic')+1] == 'off'
    overridden = parse_args(['--benches','3','--source', *map(str,videos), '--bench-source','2',str(videos[1]),str(videos[0])])
    capture = next(p for p in plan(overridden) if p.name == 'capture-2')
    assert capture.command[capture.command.index('--source')+1] == str(videos[1])


def test_bad_inputs():
    for argv in (['--benches','0'], ['--benches','3','--without','app'], ['--bench-cams','2','0','1']):
        with pytest.raises(SystemExit):
            parse_args(argv)
    with pytest.raises(ValueError, match='shared'):
        plan(parse_args(['--benches','3','--cams','0','1']))


def test_sample_plan():
    samples = os.environ.get('BENCH_SAMPLES')
    if not samples:
        pytest.skip('BENCH_SAMPLES is unset')
    files = [Path(samples)/'multicam_test'/name for name in ('camA.mp4','camB_sim.mp4')]
    if not all(file.is_file() for file in files):
        pytest.skip('Sample videos missing')
    parts = plan(parse_args(['--benches','3','--source',*map(str,files)]))
    assert sum(p.name.startswith('capture-') for p in parts) == 3


def test_three_sample_benches_running(tmp_path):
    import json
    import signal
    import socket
    import subprocess
    import sys
    import time
    import httpx
    from scripts import run_demo

    samples = os.environ.get('BENCH_SAMPLES')
    if not samples:
        pytest.skip('BENCH_SAMPLES is unset')
    files = [Path(samples)/'multicam_test'/name for name in ('camA.mp4','camB_sim.mp4')]
    if not all(file.is_file() for file in files):
        pytest.skip('Sample videos missing')
    base = None
    for candidate in range(8800, 8883):
        held = []
        try:
            for port in range(candidate, candidate + 18):
                sock = socket.socket(); held.append(sock); sock.bind(('127.0.0.1',port))
            base = candidate
            break
        except OSError:
            pass
        finally:
            for sock in held:
                sock.close()
    if base is None:
        pytest.skip('No free classroom port block')
    env = {k:v for k,v in os.environ.items() if k not in ('ANTHROPIC_API_KEY','GEMINI_API_KEY','NVIDIA_API_KEY')}
    env['BENCH_PY'] = sys.executable
    log = tmp_path/'launcher.log'
    with log.open('w') as stream:
        process = subprocess.Popen([sys.executable, run_demo.__file__, '--benches','3',
            '--source',*map(str,files),'--base-port',str(base),'--runs',str(tmp_path/'runs'),
            '--fake-vision','--fake-speed','10','--without','angle','note','--timeout','20'],
            env=env, cwd=run_demo.ROOT, stdout=stream, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic()+55
        while 'Demo ready' not in log.read_text():
            assert process.poll() is None, log.read_text()
            assert time.monotonic() < deadline, log.read_text()
            time.sleep(.1)
        with httpx.Client(base_url=f'http://127.0.0.1:{base}', timeout=5) as client:
            for n in (1,2,3):
                response = client.post(f'/benches/{n}/run/start', json={'student':f'Student {n}'})
                assert response.status_code == 200, response.text
                assert response.json()['capture']['ok']
            deadline = time.monotonic()+20
            while True:
                states = [client.get(f'/benches/{n}/state').json() for n in (1,2,3)]
                if all(len(state['steps']) == 8 and state['current_step'] is None for state in states):
                    break
                assert time.monotonic() < deadline, states
                time.sleep(.2)
            for n, state in enumerate(states,1):
                assert state['student'] == f'Student {n}'
                assert state['warnings']
                response = client.post(f'/benches/{n}/run/stop')
                assert response.json()['capture']['ok']
                folder = tmp_path/'runs'/state['run']
                for filename in ('cam0.mp4','cam1.mp4','timing.jsonl'):
                    assert (folder/filename).stat().st_size > 0
                rows = [json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines()]
                assert {row['bench'] for row in rows} == {str(n)}
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        process.wait(timeout=15)
    assert process.returncode == 0, log.read_text()
    for port in range(base,base+18):
        with socket.socket() as sock:
            assert sock.connect_ex(('127.0.0.1',port)) != 0
