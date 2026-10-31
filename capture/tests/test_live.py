import os
"""Actual HTTP/WSS transport, with owned subprocess cleanup and check-only ports."""
import asyncio
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import time

import httpx
import websockets

from capture.check import check
from capture.phone import certificate
from capture.tests.test_capture import jpeg


def ports():
    sockets = []
    try:
        for port in range(8800, 8900):
            sock = socket.socket()
            try: sock.bind(('127.0.0.1', port))
            except OSError:
                sock.close()
                continue
            sockets.append(sock)
            if len(sockets) == 2:
                return [s.getsockname()[1] for s in sockets]
        raise RuntimeError('no free check ports')
    finally:
        for sock in sockets: sock.close()


def test_http_and_trusted_https(tmp_path):
    http_port, tls_port = ports()
    cert, _ = certificate(tmp_path/'tls','127.0.0.1')
    context = ssl.create_default_context(cafile=str(cert))
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable,'-m','capture.service','--source','phone:0','--source','phone:1',
               '--fake-mic','--port',str(http_port),'--https','--https-port',str(tls_port),
               '--phone-address','127.0.0.1','--cert-dir',str(tmp_path/'tls'),'--runs',str(tmp_path/'runs')]
    with (tmp_path/'server.log').open('w') as log:
        process = subprocess.Popen(command,cwd=root,stdout=log,stderr=log)
        try:
            with httpx.Client(verify=context,timeout=1,trust_env=False) as client:
                for _ in range(100):
                    assert process.poll() is None
                    try:
                        if client.get(f'http://127.0.0.1:{http_port}/cams').status_code == 200: break
                    except httpx.TransportError: pass
                    time.sleep(.05)
                else: raise AssertionError('server did not start')
                base = f'https://127.0.0.1:{tls_port}'
                assert client.get(base+'/phone').status_code == 200
                assert client.get(base+'/phone/certificate').content == cert.read_bytes()
                async def send():
                    async with websockets.connect(f'wss://127.0.0.1:{tls_port}/phone/0',ssl=context) as ws:
                        assert json.loads(await ws.recv())['ready']
                        await ws.send(jpeg())
                        ack = json.loads(await ws.recv())
                        response = client.get(f'http://127.0.0.1:{http_port}/cam/0/frame.jpg')
                        assert response.status_code == 200
                        assert float(response.headers['x-frame-time']) == ack['t']
                        with client.stream('GET',f'http://127.0.0.1:{http_port}/cam/0/mjpeg') as stream:
                            assert b'X-Frame-Time:' in next(stream.iter_bytes())
                asyncio.run(send())
        finally:
            process.terminate()
            try: process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        assert process.returncode == 0
    for port in (http_port,tls_port):
        with socket.socket() as sock: assert sock.connect_ex(('127.0.0.1',port)) != 0


def test_standalone_sample_recording(tmp_path):
    sources = [Path(f'{os.environ.get('BENCH_SAMPLES', '')}/lsv_mv/CP-{side}-Front-414-445.mp4') for side in ('Left','Right')]
    if not all(p.exists() for p in sources):
        import pytest
        pytest.skip('sample footage not installed')
    port, _ = ports()
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable,'-m','capture.service','--source',str(sources[0]),'--source',str(sources[1]),
               '--fake-mic','--loop','--port',str(port),'--runs',str(tmp_path/'runs')]
    with (tmp_path/'server.log').open('w') as log:
        process = subprocess.Popen(command,cwd=root,stdout=log,stderr=log)
        try:
            base = f'http://127.0.0.1:{port}'
            with httpx.Client(base_url=base,timeout=3,trust_env=False) as client:
                for _ in range(100):
                    assert process.poll() is None
                    try:
                        if all(c['live'] for c in client.get('/cams').json()): break
                    except httpx.TransportError: pass
                    time.sleep(.05)
                else: raise AssertionError('sample cameras not ready')
                asyncio.run(check(base))
                result = client.post('/record/start',json={'run':'sample'}).json()
                assert result['recording']
                time.sleep(1.25)
                result = client.post('/record/stop').json()
                assert not result['recording'] and result['error'] is None and result['dropped'] == 0
                timing = [json.loads(line) for line in (tmp_path/'runs/sample/timing.jsonl').read_text().splitlines()]
                counts = timing[-1]['frames']
                assert min(counts) >= 20
                measured = dict(duration=result['stopped']-result['started'],frames=counts,
                                samples=timing[-1]['samples'],codecs=result['codecs'],dropped=result['dropped'])
                (tmp_path/'measurements.json').write_text(json.dumps(measured,indent=2))
                # Leave another recording active: process shutdown must close its files.
                assert client.post('/record/start',json={'run':'shutdown'}).status_code == 200
                time.sleep(.1)
        finally:
            process.terminate()
            try: process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
    timing = [json.loads(line) for line in (tmp_path/'runs/shutdown/timing.jsonl').read_text().splitlines()]
    assert timing[-1]['kind'] == 'stop' and timing[-1]['error'] is None
    with socket.socket() as sock: assert sock.connect_ex(('127.0.0.1',port)) != 0
