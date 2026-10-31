"""Fresh-process RSS regression; includes native allocations and reaped workers."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from note import worker
from note.runfolder import video_info


@pytest.mark.parametrize('samples', [False, True], ids=['synthetic', 'sample-clips'])
def test_ten_builds_and_exports_release_memory(tmp_path, samples):
    from note.fakerun import SAMPLE_CLIPS
    options = []
    if samples:
        base = os.environ.get('BENCH_SAMPLES')
        if not base or not all((Path(base) / p).is_file() for p in SAMPLE_CLIPS):
            pytest.skip('BENCH_SAMPLES clips unavailable')
        options = ['--samples']
    subprocess.run([sys.executable, '-m', 'note.memory_probe', str(tmp_path), '--prepare', *options],
                   check=True, capture_output=True, timeout=90)
    measured = subprocess.run([sys.executable, '-m', 'note.memory_probe', str(tmp_path), '--pdf'],
                              check=True, capture_output=True, text=True, timeout=180)
    rows = [json.loads(line) for line in measured.stdout.splitlines()]
    print(measured.stdout)
    assert len(rows) == 10
    assert all(r['frames'] == 6 and r['locks'] == 0 for r in rows)
    late = rows[2:]
    assert max(r['rss_mib'] for r in late) - min(r['rss_mib'] for r in late) < 12
    assert max(r['peak_total_mib'] for r in late) - min(r['peak_total_mib'] for r in late) < 48


def test_worker_timeout_kills_and_reaps(monkeypatch):
    original = subprocess.Popen
    children = []
    def sleeping(*args, **kwargs):
        child = original([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        children.append(child)
        return child
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', sleeping)
        with pytest.raises(RuntimeError, match='timed out'):
            worker.run_worker('build', b'{}', timeout=.1)
    assert len(children) == 1 and children[0].poll() is not None
    # The semaphore must also be released after a timeout or failed worker.
    with pytest.raises(RuntimeError, match='worker failed'):
        worker.run_worker('unknown', b'')
    assert worker.run_worker('pdf', b'<p>Recovered</p>').startswith(b'%PDF')


def test_metadata_capture_released_on_error(monkeypatch):
    import cv2
    class BrokenCapture:
        released = False
        def get(self, _):
            raise ValueError('bad metadata')
        def release(self):
            self.released = True
    cap = BrokenCapture()
    monkeypatch.setattr(cv2, 'VideoCapture', lambda _: cap)
    assert video_info(Path('broken.mp4')) == (0.0, 0)
    assert cap.released
