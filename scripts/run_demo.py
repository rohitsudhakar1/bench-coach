#!/usr/bin/env python3
"""Run available Bench Coach parts, with bounded startup and owned process groups."""
from __future__ import annotations

import argparse
import copy
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
    env: dict | None = None


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
    source.add_argument('--cams', nargs='+', type=int, default=None, help='one or two camera device numbers')
    source.add_argument('--cam', type=int, help='one camera device number (same as --cams N)')
    mic = p.add_mutually_exclusive_group()
    mic.add_argument('--fake-mic', action='store_true')
    mic.add_argument('--mic-file', type=Path)
    p.add_argument('--replay', type=Path)
    p.add_argument('--without', nargs='+', action='extend', choices=OFFSETS, default=[])
    p.add_argument('--benches', type=int, default=1)
    p.add_argument('--bench-source', nargs=3, action='append', default=[], metavar=('BENCH', 'LEFT', 'RIGHT'))
    p.add_argument('--bench-cams', nargs=3, action='append', default=[], metavar=('BENCH', 'LEFT', 'RIGHT'))
    p.add_argument('--base-port', type=int, default=8700)
    p.add_argument('--runs', type=Path, default=ROOT / 'runs')
    p.add_argument('--timeout', type=float, default=60)
    p.add_argument('--process-log', type=Path, help='append owned service PIDs as JSON lines')
    p.add_argument('--hints', action='store_true')
    p.add_argument('--fake-vision', action='store_true', help='scripted vision stand-in (scripts/fake_vision.py)')
    p.add_argument('--fake-speed', type=float, default=1.0)
    p.add_argument('--table', nargs='?', const='on', choices=('on', 'off'), default='off')
    p.add_argument('--table-source', default='capture:0')
    p.add_argument('--table-config', type=Path, default=ROOT / 'vision/table.json')
    p.add_argument('--angle-roi', action='append', default=[], metavar='X,Y,W,H')
    p.add_argument('--calibration', type=Path)
    args = p.parse_args(argv)
    args.table = args.table == 'on'
    if not 1 <= args.benches <= 100 or args.base_port + 6 * args.benches - 1 > 65535:
        p.error('Invalid bench count or port range')
    if args.benches > 1 and 'app' in args.without:
        p.error('Classroom mode needs the hub')
    args.bench_inputs = {}
    for kind, rows in [('source', args.bench_source), ('cams', args.bench_cams)]:
        for bench, left, right in rows:
            if bench not in {str(i) for i in range(1, args.benches + 1)} or bench in args.bench_inputs:
                p.error('Each bench input must name a distinct configured bench')
            if kind == 'source':
                values = [str(Path(v).resolve()) for v in (left, right)]
                if not all(Path(v).is_file() for v in values):
                    p.error('Bench video file missing')
            else:
                try:
                    values = [int(left), int(right)]
                    assert min(values) >= 0 and values[0] != values[1]
                except (ValueError, AssertionError):
                    p.error('Bench cameras must be two distinct nonnegative numbers')
            args.bench_inputs[bench] = (kind, values)
    if not 1024 <= args.base_port <= 65530:
        p.error('--base-port must be between 1024 and 65530')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        p.error('--timeout must be positive and finite')
    if args.cam is not None:
        args.cams = [args.cam]
    if args.source and len(args.source) not in (1, 2):
        p.error('--source needs one or two video files')
    if args.cams and (min(args.cams) < 0 or len(args.cams) not in (1, 2) or len(set(args.cams)) != len(args.cams)):
        p.error('--cams needs one or two distinct nonnegative device numbers')
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
    if args.source:
        # Phone footage (HEVC, 4K, 60 fps, rotated, HDR) becomes upright 1080p30 SDR H.264 first.
        try:
            from scripts.phonevid import prepare
        except ImportError:
            from phonevid import prepare
        try:
            args.source = [str(prepare(s)) for s in args.source]
        except RuntimeError as exc:
            p.error(str(exc))
    if args.fake_vision and args.table:
        p.error('--fake-vision replaces vision; it cannot run table mode')
    if not math.isfinite(args.fake_speed) or args.fake_speed <= 0:
        p.error('--fake-speed must be positive')
    if args.table and args.hints:
        p.error('--hints requires general vision, not table mode')
    if args.table_source.startswith('capture:') and args.table_source not in ('capture:0', 'capture:1'):
        p.error('--table-source capture index must be 0 or 1')
    if camera_count(args) == 1:
        if len(args.angle_roi) > 1:
            p.error('one camera takes at most one --angle-roi')
        if args.table_source == 'capture:1':
            p.error('--table-source capture:1 needs a second camera')
    args.runs = args.runs.resolve()
    args.table_config = args.table_config.resolve()
    return args


def camera_count(args):
    """How many views this bench has: one or two."""
    return len(args.source) if args.source else len(args.cams) if args.cams else 2


def single_plan(args, root=ROOT):
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
                extra += ['--capture', url('capture'), '--cams', str(camera_count(args))]
            if args.hints:
                extra += ['--new-draw']
        elif name == 'angle':
            # Without manual boxes, angle consumes vision boxes from the hub.
            # One camera gives a rough one-view lean; two give the stereo tilt.
            for cam in range(len(args.angle_roi) or camera_count(args)):
                extra += ['--source', url('capture') + f'/cam/{cam}/frame.jpg']
            for roi in args.angle_roi:
                extra += ['--roi', roi]
            if not args.angle_roi:
                extra += ['--need-box']
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
        if name == 'vision' and args.fake_vision:
            command = [python, '-u', str(root / 'scripts/fake_vision.py'), '--port', str(ports[name]),
                       '--hub', url('app'), '--speed', str(args.fake_speed)]
            result.append(Part(name, command, ports[name]))
            continue
        result.append(Part(name, [python, '-u', '-m', module, *extra], ports[name], '/state' if name == 'app' else '/health'))
    return result


def plan(args, root=ROOT):
    if args.benches == 1 and not args.bench_inputs:
        return single_plan(args, root)
    result = []
    camera_devices = set()
    for number in range(1, args.benches + 1):
        bench = str(number)
        local = copy.deepcopy(args)
        local.base_port += (number - 1) * 6
        if bench in args.bench_inputs:
            kind, values = args.bench_inputs[bench]
            local.source, local.cams = (values, None) if kind == 'source' else ([], values)
        elif not local.source:
            local.cams = [2 * (number - 1), 2 * (number - 1) + 1] if args.cams is None else args.cams
        if not local.source and 'capture' not in args.without:
            if camera_devices.intersection(local.cams):
                raise ValueError('Camera devices cannot be shared between benches')
            camera_devices.update(local.cams)
        saved_calibration = args.runs / 'calibrations' / f'bench-{number}.json'
        if args.benches > 1 and local.calibration is None and saved_calibration.is_file():
            local.calibration = saved_calibration
        if args.benches > 1:
            local.fake_mic = True  # One physical microphone cannot be assigned to several benches.
        if number > 1:
            local.without = [*local.without, 'app']
        scoped = f'http://127.0.0.1:{args.base_port}/benches/{bench}' if args.benches > 1 else f'http://127.0.0.1:{args.base_port}'
        for part in single_plan(local, root):
            env = {name.upper() + '_URL': f'http://127.0.0.1:{local.base_port + offset}' for name, offset in OFFSETS.items()}
            env['APP_URL'] = scoped
            part.env = env
            if part.name == 'app':
                part.command += ['--benches', str(args.benches)]
            else:
                if '--hub' in part.command:
                    part.command[part.command.index('--hub') + 1] = scoped
                if args.benches > 1:
                    part.name += '-' + bench
            result.append(part)
    # Start the one hub before producers from any bench.
    return sorted(result, key=lambda part: part.name != 'app')


def needed_models(parts):
    """Model keys (see scripts/fetch_models.py) that the planned parts load with their settings."""
    keys = []
    for part in parts:
        command = part.command
        module = command[command.index('-m') + 1] if '-m' in command else None
        if module == 'vision' and '--no-detector' not in command:
            keys += ['gdino-tiny', 'hand-landmarker']
        elif module == 'coach' and not ('--mic' in command and command[command.index('--mic') + 1] == 'off'):
            keys += ['whisper-small.en', 'silero-vad']
    return list(dict.fromkeys(keys))


def load_fetch_models(root=ROOT):
    script = root / 'scripts/fetch_models.py'
    if not script.is_file():
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location('bench_fetch_models', script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


def offline_env(parts, env, root=ROOT):
    """Set HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE for the parts when every model they need is on disk.

    Without the flags a cached model still loads, but with Wi-Fi off the Hub library retries
    for about 70 s first, longer than a part's startup deadline. A flag the user set is kept."""
    keys = needed_models(parts)
    if not keys:
        return None
    if any(k in env for k in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE')):
        return 'models: keeping your HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE setting'
    fetch = load_fetch_models(root)
    if fetch is None:
        return None
    missing = fetch.missing_for(keys, root)
    if missing:
        return (f'models: {", ".join(missing)} not downloaded yet; the first start fetches them and needs '
                'network (or run scripts/fetch_models.py --download first)')
    env.update(fetch.OFFLINE_FLAGS)
    return 'models: all on disk, parts start offline (no network needed)'


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
            # Closed connections from the last demo leave the port in TIME_WAIT for a while;
            # the servers bind with SO_REUSEADDR, so only a live listener counts as in use.
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
    def __init__(self, root=ROOT, logs=None, timeout=60, env=None, process_log=None):
        self.root, self.logs = root, Path(logs or root / 'runs/logs')
        self.timeout, self.env = timeout, env
        self.process_log = process_log
        self.children = {}
        self.restarted = set()
        self.pending = {}
        self.history = []

    def start(self, part):
        self.logs.mkdir(parents=True, exist_ok=True)
        with (self.logs / f'{part.name}.log').open('ab') as log:
            process = subprocess.Popen(part.command, cwd=self.root, env={**(self.env or os.environ), **(part.env or {})}, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        self.children[part.name] = (part, process)
        self.history.append(process)
        if self.process_log:
            with Path(self.process_log).open('a') as stream:
                stream.write(json.dumps(dict(part=part.name, pid=process.pid, t=time.time())) + "\n")
        self.pending[part.name] = time.monotonic() + self.timeout

    def check(self):
        for name, (part, process) in list(self.children.items()):
            code = process.poll()
            if code is not None:
                if name.split('-')[0] in ('coach', 'note') and name not in self.restarted:
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
                    except PermissionError:
                        # Some sandboxes reject signal 0 even for owned groups.
                        # Keep escalating real shutdown signals, then reap children.
                        alive = True
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
    supervisor = Supervisor(logs=args.runs / 'logs', timeout=args.timeout, env=env, process_log=args.process_log)
    old = {}
    def interrupt(*_):
        raise KeyboardInterrupt
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            old[sig] = signal.signal(sig, interrupt)
        parts = plan(args)
        if not parts:
            raise ValueError('no available services selected')
        note = offline_env(parts, env)
        if note:
            print(note, flush=True)
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
