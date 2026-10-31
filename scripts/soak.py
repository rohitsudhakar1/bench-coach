#!/usr/bin/env python3
"""Time a silent, real-vision demo and retain interval telemetry under runs/."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time

import httpx
import psutil
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
PARTS = ('app', 'capture', 'vision', 'angle', 'coach', 'note')


def rate(previous, current, seconds):
    if previous is None or current is None or current < previous or seconds <= 0:
        return None
    return (current - previous) / seconds


class Progress:
    """Keep bounded state, recording only completed gaps above the threshold."""
    def __init__(self):
        self.last = {}
        self.gaps = []

    def update(self, name, value, now):
        old, since = self.last.get(name, (value, now))
        if value is not None and value != old:
            if now - since > 3:
                self.gaps.append(dict(stream=name, start_s=round(since, 3), seconds=round(now-since, 3)))
            self.last[name] = (value, now)
        elif name not in self.last:
            self.last[name] = (value, now)

    def finish(self, now):
        for name, (_, since) in self.last.items():
            if now - since > 3:
                self.gaps.append(dict(stream=name, start_s=round(since, 3), seconds=round(now-since, 3)))


class Events:
    def __init__(self, url):
        self.count = 0
        self.errors = 0
        self.ready = threading.Event()
        self.done = threading.Event()
        self.url = url
        self.thread = threading.Thread(target=self.listen, daemon=True)

    def listen(self):
        while not self.done.is_set():
            try:
                with connect(self.url, open_timeout=2) as ws:
                    self.ready.set()
                    while not self.done.is_set():
                        try:
                            ws.recv(timeout=.5)
                            self.count += 1
                        except TimeoutError:
                            pass
            except Exception:
                self.errors += 1
                self.done.wait(.5)


def disk_bytes(path):
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(folder) / name).stat().st_size
            except FileNotFoundError:
                pass
    return total


def memory_free():
    if sys.platform != 'darwin':
        return None
    output = subprocess.check_output(['memory_pressure', '-Q'], text=True, timeout=5)
    return int(re.search(r'free percentage: (\d+)%', output)[1])


def memory_summary(rows, part):
    values = [(r['elapsed_s'], r[f'{part}_rss_mib']) for r in rows if r[f'{part}_rss_mib'] is not None]
    if not values:
        return dict(samples=0)
    # Discard the first half to separate initial loading/allocator warmup from late growth.
    late = values[len(values)//2:]
    xs, ys = zip(*late)
    xm, ym = statistics.mean(xs), statistics.mean(ys)
    denom = sum((x-xm)**2 for x in xs)
    slope = sum((x-xm)*(y-ym) for x, y in late) / denom * 60 if denom else 0
    blocks = [statistics.median([y for _, y in late[i*len(late)//3:(i+1)*len(late)//3]]) for i in range(3)] if len(late) >= 6 else []
    return dict(samples=len(values), start_mib=values[0][1], peak_mib=max(y for _, y in values),
                end_mib=values[-1][1], late_mib_per_min=round(slope, 3),
                steady_growth=bool(blocks and blocks[0] < blocks[1] < blocks[2] and slope > 1))


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds', type=float, default=600)
    p.add_argument('--run-seconds', type=float, default=120)
    p.add_argument('--sample-seconds', type=float, default=5)
    p.add_argument('--base-port', type=int, default=8840)
    p.add_argument('--source', nargs=2, type=Path)
    p.add_argument('--output', type=Path, default=Path('runs') / time.strftime('soak-%Y%m%dT%H%M%SZ', time.gmtime()))
    a = p.parse_args(argv)
    for value in (a.seconds, a.run_seconds, a.sample_seconds):
        if not math.isfinite(value) or value <= 0:
            p.error('durations must be positive and finite')
    if not 8800 <= a.base_port <= 8894:
        p.error('all six ports must be within 8800–8899')
    if a.source is None:
        samples = os.environ.get('BENCH_SAMPLES')
        if not samples:
            p.error('set BENCH_SAMPLES or provide --source LEFT RIGHT')
        a.source = [Path(samples) / 'lsv_mv' / f'CP-{side}-Front-414-445.mp4' for side in ('Left', 'Right')]
    if not all(path.is_file() for path in a.source):
        p.error('both sample files must exist')
    a.output = a.output.resolve()
    if not a.output.is_relative_to(ROOT / 'runs'):
        p.error('--output must be under this worktree’s runs/')
    return a


def main(argv=None):
    a = arguments(argv)
    a.output.mkdir(parents=True, exist_ok=False)
    manifest = a.output / 'processes.jsonl'
    logfile = a.output / 'launcher.log'
    env = dict(os.environ, BENCH_PY=os.environ.get('BENCH_PY', sys.executable), HF_HUB_OFFLINE='1')
    for key in ('ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'NVIDIA_API_KEY'):
        env.pop(key, None)
    command = ['sh', 'scripts/demo.sh', '--source', *map(str, a.source), '--hints', '--fake-mic',
               '--base-port', str(a.base_port), '--runs', str(a.output / 'recordings'),
               '--timeout', '150', '--process-log', str(manifest)]
    rows, runs, progress = [], [], Progress()
    process = events = None
    start = None
    error = None
    active = False
    cpu = {}
    identities = {}
    previous = {}
    previous_t = None
    old_handlers = {}
    def interrupt(*_):
        raise KeyboardInterrupt
    for sig in (signal.SIGINT, signal.SIGTERM):
        old_handlers[sig] = signal.signal(sig, interrupt)
    hub = f'http://127.0.0.1:{a.base_port}'
    client = httpx.Client(timeout=2)
    def request(path, post=False):
        response = client.post(hub + path, json={}) if post else client.get(hub + path)
        response.raise_for_status()
        return response.json()
    def control(action):
        nonlocal active
        result = request('/run/' + action, True)
        active = action == 'start'
        runs.append(dict(action=action, run=result['run'], elapsed_s=round(time.monotonic()-start, 3), capture=result.get('capture')))
        if not result.get('capture', {}).get('ok'):
            raise RuntimeError('capture rejected run ' + action)
    try:
        free = memory_free()
        if free is not None and free < 40:
            raise RuntimeError('memory free below 40%; no model started')
        with logfile.open('w') as out:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 200
        while 'Demo ready.' not in logfile.read_text():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('launcher did not become ready; inspect launcher.log')
            time.sleep(.2)
        started_parts = {json.loads(line)['part'] for line in manifest.read_text().splitlines()}
        if set(PARTS) - started_parts:
            raise RuntimeError('launcher skipped a required part')
        events = Events(hub.replace('http:', 'ws:') + '/ws')
        events.thread.start()
        if not events.ready.wait(5):
            raise RuntimeError('hub websocket unavailable')
        start = time.monotonic()
        control('start')
        next_run, next_sample = a.run_seconds, 0
        with (a.output / 'samples.csv').open('w', newline='') as stream:
            writer = None
            while True:
                now = time.monotonic() - start
                if process.poll() is not None:
                    raise RuntimeError('launcher exited during soak')
                if now >= a.seconds:
                    control('stop')
                    break
                if now >= next_run:
                    control('stop')
                    control('start')
                    next_run += a.run_seconds
                counters = {'hub_events': events.count}
                failures = []
                for port, endpoint, label in ((1, '/cams', 'capture'), (2, '/health', 'vision')):
                    try:
                        resp = client.get(f'http://127.0.0.1:{a.base_port+port}{endpoint}')
                        resp.raise_for_status()
                        data = resp.json()
                        if label == 'capture':
                            counters.update({f'cam{c["id"]}_frames': c['sequence'] for c in data})
                        else:
                            counters.update({f'detector{i}': n for i, n in enumerate(data['detections'])})
                    except (httpx.HTTPError, ValueError, KeyError):
                        failures.append(label)
                for key in ('cam0_frames', 'cam1_frames', 'detector0', 'detector1', 'hub_events'):
                    progress.update(key, counters.get(key), now)
                if now >= next_sample:
                    free = memory_free()
                    if free is not None and free < 40:
                        # Stop safely instead of continuing GPU work under pressure.
                        raise RuntimeError('memory free fell below 40%; soak halted for pressure')
                    row = dict(elapsed_s=round(now, 3), epoch_s=time.time(), disk_bytes=disk_bytes(ROOT / 'runs'), memory_free_pct=free,
                               probe_errors=','.join(failures), websocket_errors=events.errors)
                    starts = [json.loads(line) for line in manifest.read_text().splitlines()]
                    latest = {s['part']: s['pid'] for s in starts}
                    for part in PARTS:
                        pid = latest.get(part)
                        identities.setdefault(part, set()).add(pid)
                        row[f'{part}_pid'] = pid
                        row[f'{part}_cpu_pct'] = row[f'{part}_rss_mib'] = None
                        if pid:
                            try:
                                proc = psutil.Process(pid)
                                usage = proc.cpu_times()
                                seconds = usage.user + usage.system
                                prior = cpu.get(pid)
                                row[f'{part}_cpu_pct'] = round(100*(seconds-prior[0])/(now-prior[1]), 3) if prior else None
                                row[f'{part}_rss_mib'] = round(proc.memory_info().rss / 2**20, 3)
                                cpu[pid] = (seconds, now)
                            except (psutil.Error, PermissionError):
                                row['probe_errors'] += ',' + part + '_resource'
                    for key in ('cam0_frames', 'cam1_frames', 'detector0', 'detector1', 'hub_events'):
                        value = counters.get(key)
                        row[key] = value
                        row[key + '_per_s'] = rate(previous.get(key), value, now-previous_t) if previous_t is not None else None
                    previous, previous_t = counters, now
                    rows.append(row)
                    if writer is None:
                        writer = csv.DictWriter(stream, fieldnames=list(row))
                        writer.writeheader()
                    writer.writerow(row)
                    stream.flush()
                    next_sample += a.sample_seconds
                    print(f'sample {len(rows)} at {now:.1f}s; runs started {sum(r["action"] == "start" for r in runs)}', flush=True)
                time.sleep(.5)
            # Let the final note complete without extending the measured interval.
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if all((a.output / 'recordings' / r['run'] / 'note.json').exists() for r in runs if r['action'] == 'stop'):
                    break
                time.sleep(.5)
    except (Exception, KeyboardInterrupt) as exc:
        error = type(exc).__name__ + ': ' + str(exc)
    finally:
        for sig in old_handlers:
            signal.signal(sig, signal.SIG_IGN)
        if start is not None:
            progress.finish(min(time.monotonic()-start, a.seconds))
        if active:
            try:
                control('stop')
            except Exception:
                pass
        if events:
            events.done.set()
            events.thread.join(timeout=4)
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                # Each service owns a separate process group; use only recorded PIDs.
                for entry in map(json.loads, manifest.read_text().splitlines()):
                    try:
                        os.killpg(entry['pid'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.kill()
                process.wait(timeout=5)
        client.close()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    ports_released = True
    for port in range(a.base_port, a.base_port+6):
        with socket.socket() as sock:
            sock.settimeout(.2)
            ports_released &= sock.connect_ex(('127.0.0.1', port)) != 0
    # Count every start, even replacements that occurred between resource samples.
    if manifest.exists():
        for entry in map(json.loads, manifest.read_text().splitlines()):
            identities.setdefault(entry['part'], set()).add(entry['pid'])
    summary = dict(requested_seconds=a.seconds, measured_seconds=round(runs[-1]['elapsed_s'], 3) if runs else 0,
                   error=error, samples=len(rows), runs=runs, ports_released=ports_released,
                   memory={p: memory_summary(rows, p) for p in PARTS},
                   restarts={p: max(0, len(ids)-1) for p, ids in identities.items()},
                   stalls=progress.gaps, websocket_errors=events.errors if events else None,
                   notes=sum((a.output / 'recordings' / r['run'] / 'note.json').exists() for r in runs if r['action'] == 'stop'))
    summary['measurement_error_samples'] = sum(bool(r['probe_errors']) for r in rows)
    stopped_runs = sum(r['action'] == 'stop' for r in runs)
    if summary['notes'] != stopped_runs and error is None:
        error = summary['error'] = 'final note deadline expired'
    (a.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    lines = [f'Samples: {len(rows)}; measured seconds: {summary["measured_seconds"]}; error: {error}',
             f'Notes: {summary["notes"]}; ports released: {ports_released}',
             'Memory MiB (start / peak / end; late MiB/min; steady growth heuristic):']
    for part, stats in summary['memory'].items():
        lines.append(f'{part}: {stats}')
    lines += [f'Restarts: {summary["restarts"]}', f'Progress gaps >3s: {len(progress.gaps)} (see summary.json)']
    (a.output / 'summary.txt').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines), flush=True)
    return int(error is not None or not ports_released)


if __name__ == '__main__':
    raise SystemExit(main())
