#!/usr/bin/env python3
"""Check a running bench silently; print PASS/FIX and finish within 110 seconds."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
import re
import shutil
import sys
import time

import cv2
import httpx
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def emit(name, ok, detail):
    print(f'{"PASS" if ok else "FIX"} {name}: {detail}', flush=True)
    return ok


async def fetch(client, url):
    response = await asyncio.wait_for(client.get(url), 3)
    response.raise_for_status()
    return response


async def frame(client, url):
    response = await fetch(client, url)
    stamp = float(response.headers['X-Frame-Time'])
    if not math.isfinite(stamp) or not -.5 <= time.time() - stamp <= 2:
        raise ValueError('stale camera frame')
    image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('invalid JPEG')
    return stamp, image


async def cameras(client, args):
    rows = {row['id']: row for row in (await fetch(client, args.capture + '/cams')).json()}
    stamps = [[], []]
    images = {}
    end = time.monotonic() + 3
    while time.monotonic() < end:
        pair = await asyncio.gather(*(frame(client, args.capture + f'/cam/{i}/frame.jpg') for i in range(2)))
        for i, (stamp, image) in enumerate(pair):
            if stamps[i] and stamp < stamps[i][-1]:
                raise ValueError('camera clock moved backwards')
            stamps[i].append(stamp)
            images[i] = image
        await asyncio.sleep(.09)
    rates = [(len(set(s)) - 1) / (s[-1] - s[0]) if s[-1] > s[0] else 0 for s in stamps]
    if not all(rows[i].get('live') is True for i in range(2)) or min(rates) < args.min_fps:
        raise ValueError('camera stopped or too slow')
    sources = args.camera_sources or [rows[i].get('source') for i in range(2)]
    def canonical(source):
        source = str(source).strip()
        return str(int(source)) if source.isdecimal() else source.rstrip('/')
    if any(s is None or not str(s).strip() for s in sources) or canonical(sources[0]) == canonical(sources[1]):
        raise ValueError('camera identities missing or duplicated')
    if images[0].shape == images[1].shape and np.array_equal(images[0], images[1]):
        raise ValueError('camera images identical')
    return images, f'fresh, distinct views at {rates[0]:.1f}/{rates[1]:.1f} observed fps; verify physical identity by covering each lens'


def table_tools():
    # Use the detector's own thresholds and schema, once vision is merged.
    from vision.table import colour_blob, marker_tip, validate
    return colour_blob, marker_tip, validate


def caps(image, path):
    colour_blob, _, validate = table_tools()
    config = validate(json.loads(path.read_text()))
    if tuple(config['size']) != (image.shape[1], image.shape[0]):
        raise ValueError('table resolution changed')
    for name in ('tube A', 'tube B', 'tube C'):
        blob = colour_blob(image, name)
        x, y, radius = config['regions'][name]
        if blob is None or math.hypot(blob[0] - x, blob[1] - y) > radius:
            raise ValueError('cap not in saved region')
    return 'red A, blue B, yellow C caps match saved regions'


def calibration(path, images):
    from angle.geometry import Calibration
    cal = Calibration.load(path)
    data = json.loads(path.read_text())
    errors = [data[k] for k in ('rms_px', 'left_rms_px', 'right_rms_px')]
    pairs = data['per_pair']
    if not pairs:
        raise ValueError('missing per-pair errors')
    errors.extend(row[k] for row in pairs for k in ('left_rms_px', 'right_rms_px'))
    if not all(math.isfinite(x) and 0 <= x <= 2 for x in errors):
        raise ValueError('calibration error exceeds 2 px')
    if len(images) != 2 or any((im.shape[1], im.shape[0]) != tuple(cal.size) for im in images.values()):
        raise ValueError('calibration does not match both camera sizes')
    return f'worst RMS {max(errors):.2f} px; still perform the physical pencil check'


def protocol(path):
    data = yaml.safe_load(path.read_text())
    steps = data['steps']
    if not isinstance(steps, list) or not steps:
        raise ValueError('empty protocol')
    for step in steps:
        if not isinstance(step['text'], str) or not step['text'].strip():
            raise ValueError('empty step text')
        expected = step.get('expect', [])
        if not isinstance(expected, list):
            raise ValueError('expect must be a list')
        for event in expected:
            if not isinstance(event['kind'], str) or not event['kind'].strip():
                raise ValueError('expect needs an event kind')
        if 'min_seconds' in step and (not math.isfinite(step['min_seconds']) or step['min_seconds'] < 0):
            raise ValueError('invalid min_seconds')
    # Also exercise the coach loader when it is available.
    try:
        from coach.protocol import load_protocol
    except ModuleNotFoundError as exc:
        if exc.name not in ('coach', 'coach.protocol'):
            raise
    else:
        load_protocol(path)
    return f'{len(steps)} steps load; review the same protocol in the dashboard'


async def read_mic(url):
    import websockets
    chunks, count = [], 0
    async with websockets.connect(url, open_timeout=3, close_timeout=1, max_size=200000) as ws:
        print('Say "testing" now; listening for 5 seconds. No playback.', flush=True)
        end = time.monotonic() + 5
        previous = -math.inf
        while time.monotonic() < end and count < 80000:
            try:
                header = json.loads(await asyncio.wait_for(ws.recv(), max(.001, end - time.monotonic())))
                pcm = await asyncio.wait_for(ws.recv(), max(.001, end - time.monotonic()))
            except asyncio.TimeoutError:
                break
            n, stamp = header['n'], header['t']
            if not isinstance(n, int) or n <= 0 or not isinstance(pcm, bytes) or len(pcm) != n * 2:
                raise ValueError('invalid PCM chunk')
            if not math.isfinite(stamp) or stamp <= previous or not -.5 <= time.time() - stamp <= 2:
                raise ValueError('stale or repeated audio')
            previous = stamp
            chunk = np.frombuffer(pcm, '<i2')[:80000-count].astype(np.float32) / 32768
            chunks.append(chunk)
            count += len(chunk)
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


async def microphone(args):
    audio = await read_mic(args.capture.replace('https:', 'wss:').replace('http:', 'ws:') + '/mic')
    if not len(audio) or float(np.sqrt(np.mean(audio ** 2))) < .003:
        raise ValueError('microphone has no speech-level signal')
    code = '''import sys, numpy as np
from faster_whisper import WhisperModel
model = WhisperModel(sys.argv[1], device="cpu", compute_type="int8", local_files_only=True)
segments, _ = model.transcribe(np.frombuffer(sys.stdin.buffer.read(), np.float32), language="en", beam_size=1, vad_filter=True, condition_on_previous_text=False)
print(" ".join(s.text for s in segments if s.no_speech_prob < .3 and s.avg_logprob > -.8))
'''
    child = await asyncio.create_subprocess_exec(sys.executable, '-c', code, args.whisper_model,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(child.communicate(audio.tobytes()), 45)
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()
    if child.returncode != 0 or not re.search(r'\btesting\b', out.decode().lower()):
        raise ValueError('did not hear testing')
    return 'heard "testing" in the 5-second capture; nothing played'


async def parts(client, args):
    data = (await fetch(client, args.hub + '/parts')).json()
    if not -.5 <= time.time() - data['checked_at'] <= 5:
        raise ValueError('stale hub status')
    required = {'capture', 'vision', 'coach', 'note'}
    if not args.no_angle:
        required.add('angle')
    good = True
    for name in sorted(required | set(data['parts'])):
        row = data['parts'].get(name, {})
        # An explicitly disabled angle is allowed to be offline.
        if name == 'angle' and args.no_angle:
            continue
        ok = row.get('alive') is True and row.get('status') in ('ready', 'sending')
        good &= emit('part ' + name, ok, 'ready/sending' if ok else f'Restart {name}; inspect runs/logs/{name}.log and hub /parts.')
    if not good:
        raise ValueError('parts not green')
    return 'all required parts green; hub responded'


async def run(args):
    results, images = [], {}
    async def check(name, action, fix, timeout=12):
        try:
            detail = await asyncio.wait_for(action(), timeout)
        except Exception:
            results.append(emit(name, False, fix))
        else:
            results.append(emit(name, True, detail))
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        async def camera_check():
            nonlocal images
            images, detail = await cameras(client, args)
            return detail
        await check('cameras', camera_check, 'Restore two fresh, distinct camera feeds; pass --camera-sources matching capture, cover each lens, and check both reach 5 fps.')
        if not args.no_table:
            async def cap_check():
                _, image = await frame(client, args.table_url)
                return caps(image, args.table_config)
            await check('table caps', cap_check, 'Install the vision table part; close red A, blue B, yellow C caps in the saved positions, or rerun python -m vision.table setup with matching --config.')
            async def band_check():
                _, marker_tip, _ = table_tools()
                print('Hold the magenta tip band in the overhead view now (5 seconds).', flush=True)
                end = time.monotonic() + 5
                while time.monotonic() < end:
                    _, image = await frame(client, args.table_url)
                    if marker_tip(image) is not None:
                        return 'magenta tip band visible'
                    await asyncio.sleep(.1)
                raise ValueError('no magenta band')
            await check('tip band', band_check, 'Hold matte magenta tape above the tip liquid-contact area in view; remove other magenta objects and improve lighting.')
        if not args.no_angle:
            async def angle_check():
                return calibration(args.calibration, images)
            await check('calibration', angle_check, 'Save angle calibration for both current views; require all RMS errors <=2 px, then complete angle/CALIBRATE.md pencil check.')
        if not args.no_mic:
            await check('microphone', lambda: microphone(args), 'Enable capture mic access, say "testing" within 5 seconds, and provide a cached --whisper-model. Disable fake mic for live speech.', 55)
        await check('hub parts', lambda: parts(client, args), 'Restore hub /parts and restart offline or quiet parts; verify service URLs and logs.')
    try:
        free = shutil.disk_usage(args.disk).free
        ok = free > 2_000_000_000
    except OSError:
        ok = False
    results.append(emit('disk', ok, 'more than 2 GB free' if ok else 'Free more than 2 GB on the recording disk; set --disk to an existing folder there.'))
    try:
        detail = protocol(args.protocol)
    except Exception:
        results.append(emit('protocol', False, 'Choose a valid --protocol YAML with nonempty steps/text and valid expect entries.'))
    else:
        results.append(emit('protocol', True, detail))
    return 0 if all(results) else 1


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture', default='http://127.0.0.1:8701')
    p.add_argument('--hub', default='http://127.0.0.1:8700')
    p.add_argument('--camera-sources', nargs=2)
    p.add_argument('--table-url')
    p.add_argument('--table-config', type=Path, default=ROOT / 'vision/table.json')
    p.add_argument('--calibration', type=Path, default=ROOT / 'angle/calibration.json')
    p.add_argument('--protocol', type=Path, default=ROOT / 'protocols/pipetting-demo.yaml')
    p.add_argument('--disk', type=Path, default=ROOT)
    p.add_argument('--whisper-model', default='small.en')
    p.add_argument('--min-fps', type=float, default=5)
    for name in ('table', 'angle', 'mic'):
        p.add_argument('--no-' + name, action='store_true')
    args = p.parse_args(argv)
    if not math.isfinite(args.min_fps) or args.min_fps <= 0:
        p.error('--min-fps must be positive and finite')
    args.capture, args.hub = args.capture.rstrip('/'), args.hub.rstrip('/')
    args.table_url = args.table_url or args.capture + '/cam/0/frame.jpg'
    return args


def main(argv=None):
    args = parse_args(argv)
    async def bounded():
        try:
            return await asyncio.wait_for(run(args), 105)
        except asyncio.TimeoutError:
            emit('deadline', False, 'Preflight stopped at 105 seconds; restore responsive services and a cached speech model.')
            return 1
    return asyncio.run(bounded())


if __name__ == '__main__':
    raise SystemExit(main())
