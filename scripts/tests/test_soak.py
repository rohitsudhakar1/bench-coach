import json
from pathlib import Path
import sys

import pytest

from scripts import soak
from scripts.run_demo import Part, Supervisor


def test_rates_handle_reset_missing_and_real_interval():
    assert soak.rate(100, 145, 5.5) == pytest.approx(45 / 5.5)
    assert soak.rate(100, 2, 5) is None
    assert soak.rate(None, 2, 5) is None
    assert soak.rate(2, None, 5) is None
    assert soak.rate(2, 2, 5) == 0


def test_progress_records_recovery_and_unfinished_gap_once():
    p = soak.Progress()
    for t, value in [(0, 3), (1, 4), (2, 4), (3, None), (4, 4), (4.5, 5), (5, 6)]:
        p.update('camera', value, t)
    p.finish(9)
    assert p.gaps == [dict(stream='camera', start_s=1, seconds=3.5),
                      dict(stream='camera', start_s=5, seconds=4)]


def test_memory_summary_distinguishes_warmup_from_sustained_growth():
    def rows(values):
        return [dict(elapsed_s=i*5, app_rss_mib=value) for i, value in enumerate(values)]
    stable = soak.memory_summary(rows([10, 20, 40, 70] + [100]*8), 'app')
    assert stable['peak_mib'] == stable['end_mib'] == 100
    assert not stable['steady_growth']
    growing = soak.memory_summary(rows(range(24)), 'app')
    assert growing['steady_growth']
    assert growing['late_mib_per_min'] == 12
    assert soak.memory_summary(rows([None]*5), 'app') == {'samples': 0}


def test_launcher_pid_log_records_replacements_without_command_or_environment(tmp_path):
    manifest = tmp_path / 'processes.jsonl'
    supervisor = Supervisor(root=tmp_path, logs=tmp_path / 'logs', process_log=manifest)
    part = Part('note', [sys.executable, '-c', 'import time; time.sleep(30)'], 8845)
    try:
        supervisor.start(part)
        first = supervisor.children['note'][1]
        first.terminate()
        first.wait(timeout=3)
        supervisor.start(part)
        rows = [json.loads(line) for line in manifest.read_text().splitlines()]
        assert len(rows) == 2
        assert all(set(row) == {'part', 'pid', 't'} for row in rows)
        assert rows[0]['pid'] != rows[1]['pid']
    finally:
        supervisor.stop()


@pytest.mark.parametrize('args', [['--seconds', 'nan'], ['--run-seconds', '0'], ['--sample-seconds', '-1'], ['--base-port', '8799'], ['--base-port', '8895']])
def test_invalid_limits_fail_before_launch(args):
    with pytest.raises(SystemExit):
        soak.arguments(args)


def test_samples_are_optional_test_data(monkeypatch):
    folder = __import__('os').environ.get('BENCH_SAMPLES')
    if not folder:
        pytest.skip('BENCH_SAMPLES unset')
    paths = [Path(folder) / 'lsv_mv' / f'CP-{side}-Front-414-445.mp4' for side in ('Left', 'Right')]
    if not all(p.is_file() for p in paths):
        pytest.skip('sample pair missing')
    assert soak.arguments([]).source == paths


@pytest.mark.parametrize('failure', [None, 'capture', 'note'])
def test_full_schedule_and_cleanup_with_fake_services(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    clock = [0.0]
    monkeypatch.setattr(soak, 'ROOT', tmp_path)
    monkeypatch.setattr(soak.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(soak.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    monkeypatch.setattr(soak, 'memory_free', lambda: 70)
    output = tmp_path / 'runs' / 'soak'
    sources = [tmp_path / 'left.mp4', tmp_path / 'right.mp4']
    for path in sources:
        path.touch()
    launched = []
    class Process:
        stopped = False
        def poll(self):
            return 0 if self.stopped else None
        def send_signal(self, sig):
            self.stopped = True
        def wait(self, timeout):
            return 0
    def launch(command, **kwargs):
        launched.append(Process())
        kwargs['stdout'].write('Demo ready.\n')
        kwargs['stdout'].flush()
        (output / 'processes.jsonl').write_text('\n'.join(json.dumps(dict(part=p, pid=i+1)) for i, p in enumerate(soak.PARTS)))
        assert '--hints' in command and '--fake-mic' in command
        assert '--fake-vision' not in command
        assert 'ANTHROPIC_API_KEY' not in kwargs['env']
        return launched[-1]
    monkeypatch.setattr(soak.subprocess, 'Popen', launch)
    class Client:
        run = 0
        def get(self, url):
            if url.endswith('/cams'):
                payload = [dict(id=i, sequence=int(clock[0]*30)) for i in range(2)]
            else:
                payload = dict(detections=[int(clock[0]*3)]*2)
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
        def post(self, url, json):
            if url.endswith('/start'):
                self.run += 1
            elif failure != 'note':
                folder = output / 'recordings' / str(self.run)
                folder.mkdir(parents=True)
                (folder / 'note.json').write_text('{}')
            payload = dict(run=str(self.run), capture={'ok': failure != 'capture'})
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
        def close(self):
            pass
    monkeypatch.setattr(soak.httpx, 'Client', lambda **kwargs: Client())
    class Events:
        errors = 0
        def __init__(self, url):
            self.ready = SimpleNamespace(wait=lambda timeout: True)
            self.done = SimpleNamespace(set=lambda: None)
            self.thread = SimpleNamespace(start=lambda: None, join=lambda **kwargs: None)
        @property
        def count(self):
            return int(clock[0]*4)
    monkeypatch.setattr(soak, 'Events', Events)
    monkeypatch.setattr(soak.psutil, 'Process', lambda pid: SimpleNamespace(
        cpu_times=lambda: SimpleNamespace(user=clock[0]*.5, system=clock[0]*.1),
        memory_info=lambda: SimpleNamespace(rss=2**20*100)))
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): pass
        def connect_ex(self, address): return 1
    monkeypatch.setattr(soak.socket, 'socket', Socket)
    assert soak.main(['--source', *map(str, sources), '--output', str(output)]) == int(failure is not None)
    summary = json.loads((output / 'summary.json').read_text())
    assert all(p.stopped for p in launched)
    if failure == 'capture':
        assert 'capture rejected' in summary['error']
        assert [r['action'] for r in summary['runs']] == ['start', 'stop']
        return
    if failure == 'note':
        assert summary['error'] == 'final note deadline expired'
        assert summary['notes'] == 0
        return
    assert summary['measured_seconds'] == 600
    assert summary['samples'] == 120
    assert summary['notes'] == 5
    assert [r['action'] for r in summary['runs']] == ['start', 'stop']*5
    assert [r['elapsed_s'] for r in summary['runs'] if r['action'] == 'stop'] == [120, 240, 360, 480, 600]
    assert summary['stalls'] == []
    assert summary['ports_released']
    assert all(p.stopped for p in launched)
