#!/usr/bin/env python3
"""Run available Bench Coach parts, with bounded startup and owned process groups."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OFFSETS = {'capture': 1, 'app': 0, 'vision': 2, 'angle': 3, 'coach': 4, 'note': 5}


@dataclass
class Part:
    name: str
    command: list[str]
    port: int
    path: str = '/health'


def entrypoint(root, name):
    # Prefer package mains: note.service defines an app but does not run it.
    candidates = [name, name + '.service']
    if name == 'app':
        candidates.append('app.hub')
    if name == 'angle':
        candidates.append('angle.run')
    for module in candidates:
        path = root.joinpath(*module.split('.'))
        if (path / '__main__.py').is_file():
            return module
        if path.with_suffix('.py').is_file():
            # Do not import model-heavy modules just to discover an entry point.
            if '__main__' in path.with_suffix('.py').read_text():
                return module
    return None


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group()
    source.add_argument('--source', nargs='+', action='extend', default=[])
    source.add_argument('--cams', nargs=2, type=int, default=None)
    mic = p.add_mutually_exclusive_group()
    mic.add_argument('--fake-mic', action='store_true')
    mic.add_argument('--mic-file', type=Path)
    p.add_argument('--replay', type=Path)
    p.add_argument('--without', nargs='+', action='extend', choices=OFFSETS, default=[])
    p.add_argument('--base-port', type=int, default=8700)
    p.add_argument('--runs', type=Path, default=ROOT / 'runs')
    p.add_argument('--timeout', type=float, default=60)
    p.add_argument('--hints', action='store_true')
    p.add_argument('--table', nargs='?', const='on', choices=('on', 'off'), default='off')
    p.add_argument('--table-source', default='capture:0')
    p.add_argument('--table-config', type=Path, default=ROOT / 'vision/table.json')
    p.add_argument('--angle-roi', action='append', default=[], metavar='X,Y,W,H')
    p.add_argument('--calibration', type=Path)
    args = p.parse_args(argv)
    args.table = args.table == 'on'
    if not 1024 <= args.base_port <= 65530:
        p.error('--base-port must be between 1024 and 65530')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        p.error('--timeout must be positive and finite')
    if args.source and len(args.source) != 2:
        p.error('--source needs two video files')
    if args.cams and (min(args.cams) < 0 or len(set(args.cams)) != 2):
        p.error('--cams needs two distinct nonnegative device numbers')
    if len(args.angle_roi) > 2:
        p.error('give at most two --angle-roi values')
    for roi in args.angle_roi:
        try:
            x, y, w, h = map(int, roi.split(','))
            assert min(x, y) >= 0 and min(w, h) > 0
        except (ValueError, AssertionError):
            p.error('--angle-roi needs nonnegative X,Y and positive W,H')
    args.source = [str(Path(s).resolve()) for s in args.source]
    for field in ('replay', 'mic_file', 'calibration'):
        value = getattr(args, field)
        if value:
            setattr(args, field, value.resolve())
    for path in [*args.source, args.replay, args.mic_file, args.calibration]:
        if path and not Path(path).is_file():
            p.error(f'input file missing: {path}')
    if args.table and args.hints:
        p.error('--hints requires general vision, not table mode')
    if args.table_source.startswith('capture:') and args.table_source not in ('capture:0', 'capture:1'):
        p.error('--table-source capture index must be 0 or 1')
    args.runs = args.runs.resolve()
    args.table_config = args.table_config.resolve()
    return args


def plan(args, root=ROOT):
    ports = {name: args.base_port + offset for name, offset in OFFSETS.items()}
    url = lambda name: f'http://127.0.0.1:{ports[name]}'
    python = os.environ.get('BENCH_PY') or sys.executable
    available = {name: entrypoint(root, name) for name in OFFSETS}
    result = []
    for name in OFFSETS:
        if name in args.without:
            print(f'{name}: skipped (--without)', flush=True)
            continue
        module = 'vision.table' if name == 'vision' and args.table else available[name]
        if module == 'vision.table' and not (root / 'vision/table.py').is_file() and not (root / 'vision/table/__main__.py').is_file():
            module = None
        if module is None:
            print(f'{name}: skipped (no runnable entry point yet)', flush=True)
            continue
        extra = ['--port', str(ports[name])]
        if name == 'capture':
            for source in args.source or [str(n) for n in (args.cams or [0, 1])]:
                extra += ['--source', source]
            if args.source:
                extra += ['--loop']
            if args.mic_file:
                extra += ['--mic-file', str(args.mic_file)]
            elif args.source or args.fake_mic:
                extra += ['--fake-mic']
            extra += ['--runs', str(args.runs)]
        elif name == 'app':
            extra += ['--capture', url('capture'), '--note', url('note'), '--runs', str(args.runs)]
        elif name == 'vision':
            extra += ['--hub', url('app')]
            if args.table:
                if not args.table_config.is_file():
                    raise ValueError('table calibration missing; run python -m vision.table setup')
                if 'app' in args.without or available['app'] is None:
                    raise ValueError('table mode needs the app for run start/stop')
                source = args.table_source
                devices = [str(n) for n in (args.cams or [0, 1])]
                if source.isdecimal() and not args.source and source in devices and 'capture' not in args.without and available['capture']:
                    source = 'capture:' + str(devices.index(source))
                if source.startswith('capture:'):
                    if 'capture' in args.without or available['capture'] is None:
                        raise ValueError('table source needs capture')
                    source = url('capture') + f'/cam/{source[-1]}/frame.jpg'
                extra = ['run', *extra, '--source', source, '--config', str(args.table_config), '--follow-runs', '--realtime']
            else:
                extra += ['--capture', url('capture')]
            if args.hints:
                extra += ['--new-draw']
        elif name == 'angle':
            # Without manual boxes, angle consumes vision boxes from the hub.
            for cam in range(len(args.angle_roi) or 2):
                extra += ['--source', url('capture') + f'/cam/{cam}/frame.jpg']
            for roi in args.angle_roi:
                extra += ['--roi', roi]
            extra += ['--hub', url('app')]
            if args.calibration:
                extra += ['--calibration', str(args.calibration)]
        elif name == 'coach':
            extra += ['--hub', url('app'), '--capture', url('capture').replace('http:', 'ws:') + '/mic']
            if (args.source or args.fake_mic) and not args.mic_file:
                extra += ['--mic', 'off']
            if args.hints:
                extra += ['--hints']
        elif name == 'note':
            extra += ['--hub', url('app'), '--runs', str(args.runs)]
            if module == 'note':
                extra.insert(0, 'serve')
        result.append(Part(name, [python, '-u', '-m', module, *extra], ports[name], '/state' if name == 'app' else '/health'))
    return result


def ready(part):
    try:
        with urlopen(f'http://127.0.0.1:{part.port}{part.path}', timeout=.4) as response:
            data = json.load(response)
        return isinstance(data, dict) and data.get('ok') is not False and data.get('status') not in ('error', 'offline', 'failed')
    except (OSError, ValueError):
        return False


def check_ports(parts):
    for part in parts:
        with socket.socket() as sock:
            # Match the servers: permit TIME_WAIT sockets from a just-finished run.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('127.0.0.1', part.port))
            except OSError as exc:
                raise RuntimeError(f'{part.name}: port {part.port} already in use') from exc


def signal_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


class Supervisor:
    def __init__(self, root=ROOT, logs=None, timeout=60, env=None):
        self.root, self.logs = root, Path(logs or root / 'runs/logs')
        self.timeout, self.env = timeout, env
        self.children = {}
        self.restarted = set()
        self.pending = {}
        self.history = []

    def start(self, part):
        self.logs.mkdir(parents=True, exist_ok=True)
        with (self.logs / f'{part.name}.log').open('ab') as log:
            process = subprocess.Popen(part.command, cwd=self.root, env=self.env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        self.children[part.name] = (part, process)
        self.history.append(process)
        self.pending[part.name] = time.monotonic() + self.timeout

    def check(self):
        for name, (part, process) in list(self.children.items()):
            code = process.poll()
            if code is not None:
                if name in ('coach', 'note') and name not in self.restarted:
                    self.restarted.add(name)
                    signal_group(process, signal.SIGKILL)
                    message = f'{name}: exited ({code}); restarting once'
                    print(message, flush=True)
                    with (self.logs / f'{name}.log').open('a') as log:
                        log.write('\n' + message + '\n')
                    self.start(part)
                    continue
                raise RuntimeError(f'{name}: exited ({code}); see {self.logs / (name + ".log")}')
            if name in self.pending:
                if ready(part):
                    del self.pending[name]
                    print(f'{name}: ready on {part.port}', flush=True)
                elif time.monotonic() >= self.pending[name]:
                    raise RuntimeError(f'{name}: health timed out; see {self.logs / (name + ".log")}')

    def wait_ready(self, part):
        while part.name in self.pending:
            self.check()
            time.sleep(.1)

    def stop(self):
        for sig, seconds in ((signal.SIGINT, 2), (signal.SIGTERM, 1), (signal.SIGKILL, 1)):
            for process in reversed(self.history):
                signal_group(process, sig)
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                for process in self.history:
                    process.poll()
                alive = False
                for process in self.history:
                    try:
                        os.killpg(process.pid, 0)
                        alive = True
                    except ProcessLookupError:
                        pass
                if not alive:
                    break
                time.sleep(.05)
            if not alive:
                break
        for process in self.history:
            process.wait(timeout=2)


def replay_rows(path):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('t'), (int, float)) or not math.isfinite(row['t']) or not row.get('kind') or not row.get('source'):
            raise ValueError('replay needs event objects with finite t, kind and source')
    if any(b['t'] < a['t'] for a, b in zip(rows, rows[1:])):
        raise ValueError('replay timestamps must be ordered')
    return rows


def main(argv=None):
    args = parse_args(argv)
    env = dict(os.environ)
    env.update({name.upper() + '_URL': f'http://127.0.0.1:{args.base_port + offset}' for name, offset in OFFSETS.items()})
    supervisor = Supervisor(logs=args.runs / 'logs', timeout=args.timeout, env=env)
    old = {}
    def interrupt(*_):
        raise KeyboardInterrupt
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            old[sig] = signal.signal(sig, interrupt)
        parts = plan(args)
        if not parts:
            raise ValueError('no available services selected')
        rows = replay_rows(args.replay) if args.replay else []
        if args.replay and not any(p.name == 'app' for p in parts):
            raise ValueError('--replay requires app')
        check_ports(parts)
        for part in parts:
            supervisor.start(part)
            supervisor.wait_ready(part)
        start, epoch, index = time.monotonic(), time.time(), 0
        print(f'Demo ready. Hub: http://127.0.0.1:{args.base_port}. Ctrl-C stops all parts.', flush=True)
        while True:
            supervisor.check()
            if index < len(rows) and time.monotonic() - start >= rows[index]['t'] - rows[0]['t']:
                event = dict(rows[index], t=epoch + rows[index]['t'] - rows[0]['t'])
                request = Request(env['APP_URL'] + '/events', data=json.dumps(event).encode(), headers={'Content-Type': 'application/json'})
                with urlopen(request, timeout=2):
                    pass
                index += 1
                if index == len(rows):
                    print(f'replay: finished ({index} events); hub remains open', flush=True)
            time.sleep(.05)
    except KeyboardInterrupt:
        print('Stopping demo.', flush=True)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        for sig in old:
            signal.signal(sig, signal.SIG_IGN)
        try:
            supervisor.stop()
        finally:
            for sig, handler in old.items():
                signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
