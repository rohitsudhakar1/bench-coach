"""Ctrl-C on a running coach exits cleanly, without asyncio tracebacks in its log."""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def free_port():
    for port in range(8890, 8900):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    pytest.skip('no free port in 8890-8899')


def test_ctrl_c_shuts_down_without_a_traceback():
    port = free_port()
    child = subprocess.Popen([sys.executable, '-m', 'coach', '--hub', 'http://127.0.0.1:9',
                              '--mic', 'off', '--port', str(port), '--quiet'],
                             cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env={k: v for k, v in os.environ.items()
                                  if k not in ('ANTHROPIC_API_KEY', 'GEMINI_API_KEY')})
    try:
        deadline = time.monotonic() + 20
        while True:
            assert child.poll() is None, child.stdout.read()
            try:
                if httpx.get(f'http://127.0.0.1:{port}/health', timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, 'coach never became healthy'
            time.sleep(.2)
        child.send_signal(signal.SIGINT)
        out, _ = child.communicate(timeout=15)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    assert 'Traceback' not in out, out
    assert 'Task was destroyed' not in out, out
