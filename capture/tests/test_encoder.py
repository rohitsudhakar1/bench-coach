import shutil
import subprocess

import numpy as np
import pytest

from capture.encoder import H264Writer


def test_encoder_exit_failure_reaps_process(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg not installed')
    writer = H264Writer(tmp_path / 'missing' / 'out.mp4', 30, (64, 48))
    process = writer.process
    try:
        writer.write(np.zeros((48, 64, 3), np.uint8))
    except BrokenPipeError:
        pass
    with pytest.raises(OSError, match='exited with status'):
        writer.release()
    assert process.poll() is not None
    assert writer.errors.closed
    writer.release()


def test_encoder_timeout_kills_and_reaps(monkeypatch):
    class Pipe:
        def close(self): pass
    class Process:
        stdin = Pipe()
        killed = False
        waited = False
        def wait(self, timeout=None):
            if timeout:
                raise subprocess.TimeoutExpired('encoder', timeout)
            self.waited = True
            return -9
        def kill(self): self.killed = True
    process = Process()
    monkeypatch.setattr('capture.encoder.subprocess.Popen', lambda *a, **k: process)
    writer = H264Writer('unused.mp4', 30, (64, 48))
    with pytest.raises(OSError, match='did not finish'):
        writer.release()
    assert process.killed and process.waited and writer.errors.closed
