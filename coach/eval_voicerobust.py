"""Silent voice matrix. Run generation separately from short, resumable model batches."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import numpy as np

from coach import listen as L
from coach.tests.helpers import demo_brain, said, T0
from coach.tests.test_voice import FakeStream
from coach.tests.voice_session import read, write_wav

VOICES = ('Samantha', 'Daniel', 'Karen', 'Moira', 'Rishi', 'Tessa', 'Fred', 'Aman')
SPEEDS = (140, 210)
GAINS = (0.5, 1.0, 2.0)
# Each trial starts at this step; questions must leave it there, confirmations advance once.
CASES = [
    ("What's next?", 0, 0, 'Now: put a fresh tip.'),
    ('How much do I add?', 0, 0, '100 microlitres of water from tube A.'),
    ('Did I add tube C?', 0, 0, 'No, nothing with tube C yet.'),
    ("Tip's on", 0, 1, 'Next: draw 100 microlitres of water from tube A.'),
    ('Done', 0, 1, 'Next: draw 100 microlitres of water from tube A.'),
    ('Added it to B', 2, 3, 'Next: eject the tip.'),
    ('I drew from tube A', 1, 2, 'Next: dispense it into tube B.'),
    ('I drew from tube C', 5, 6, 'Next: dispense it into tube B and mix three times.'),
    ('Ejected the tip', 3, 4, 'Next: put a fresh tip.'),
    ('Fresh tip', 0, 1, 'Next: draw 100 microlitres of water from tube A.'),
    ('Which step am I on?', 0, 0, 'Step 1 of 8, 8 left.'),
    ('What volume do I need?', 0, 0, '100 microlitres of water from tube A.'),
    ('What should I do?', 0, 0, 'Now: put a fresh tip.'),
    ('I have not added it to B', 2, 2, None),
    ('I drew from tube B', 1, 1, "That doesn't match step 2. Please check."),
    ('Mixed three times', 6, 7, 'Next: eject the tip.'),
]


def decode(path, speed=1.0):
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-vn']
    if speed != 1.0:
        args += ['-af', f'atempo={speed}']
    raw = subprocess.run(args + ['-f', 'f32le', '-ar', str(L.RATE), '-ac', '1', 'pipe:1'],
                         check=True, capture_output=True, timeout=30).stdout
    return np.frombuffer(raw, '<f4').copy()


def cached_model():
    """Resolve only complete local weights; never download during a test."""
    explicit = os.environ.get('BENCH_WHISPER_MODEL')
    hub = Path(os.environ.get('HF_HUB_CACHE', Path(os.environ.get(
        'HF_HOME', Path.home() / '.cache/huggingface')) / 'hub'))
    candidates = [Path(explicit)] if explicit else sorted(
        (hub / 'models--Systran--faster-whisper-small.en' / 'snapshots').glob('*'))
    for path in candidates:
        if all((path / f).is_file() for f in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')):
            return str(path)
    return None


def words(text):
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower().replace('’', "'"))


def distance(a, b):
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row = row, [i]
        for j, y in enumerate(b, 1):
            row.append(min(row[-1] + 1, prev[j] + 1, prev[j-1] + (x != y)))
    return row[-1]


def mixed(speech, noise, gain):
    # Preserve source voice amplitude, add 3 seconds of room calibration and 1.5 seconds tail.
    n = 3 * L.RATE + len(speech) + int(1.5 * L.RATE)
    x = np.resize(noise, n).copy() * gain
    x[3 * L.RATE:3 * L.RATE + len(speech)] += speech
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


def prepare(out, fixtures=False):
    out.mkdir(parents=True, exist_ok=True)
    rows, failures = [], []
    if fixtures:
        noise = read('bench_noise')
        sources = [('whats_next', 0), ('how_much', 1), ('did_i_add_tube_c', 2)]
        for name, case in sources:
            for rate in SPEEDS:
                speech = decode(Path(__file__).parent / 'tests/voice' / f'{name}.wav', rate / 170)
                for gain in GAINS:
                    key = f'{name}-{rate}-{gain}'
                    write_wav(out / f'{key}.wav', mixed(speech, noise, gain))
                    rows.append(dict(id=key, voice='existing-recording', rate=rate, gain=gain,
                                     case=case, text=CASES[case][0]))
    else:
        samples = os.environ.get('BENCH_SAMPLES')
        if not samples:
            raise SystemExit('Set BENCH_SAMPLES to the sample video directory.')
        noise = decode(Path(samples) / 'lsv_dji/transform_skip3.mp4')[:20 * L.RATE]
        if not len(noise) or not np.any(noise):
            raise SystemExit('Sample video has no usable audio.')
        for voice in VOICES:
            for rate in SPEEDS:
                for case, (text, *_rest) in enumerate(CASES):
                    key = f'{voice}-{rate}-{case}'
                    aiff = out / f'{key}.aiff'
                    try:
                        subprocess.run(['say', '-v', voice, '-r', str(rate), '-o', str(aiff), text],
                                       check=True, capture_output=True, timeout=20)
                        speech = decode(aiff)
                        if len(speech) < 1600 or not np.any(speech):
                            raise ValueError('empty audio from say')
                    except (ValueError, subprocess.SubprocessError) as exc:
                        failures.append(dict(id=key, error=type(exc).__name__, reason='speech generation failed or empty'))
                        continue
                    finally:
                        aiff.unlink(missing_ok=True)
                    for gain in GAINS:
                        name = f'{key}-{gain}'
                        write_wav(out / f'{name}.wav', mixed(speech, noise, gain))
                        rows.append(dict(id=name, voice=voice, rate=rate, gain=gain, case=case, text=text))
    (out / 'manifest.json').write_text(json.dumps(dict(trials=rows, generation_failures=failures), indent=2) + '\n')
    print(json.dumps(dict(trials=len(rows), generation_failures=len(failures))))
    if not rows:
        raise SystemExit('No usable speech was generated; inspect manifest.json.')


async def trial(pcm, stt, case, baseline=False):
    text, start, target, answer = CASES[case]
    brain = demo_brain()
    for i in range(start):
        brain.hear('done', t=T0 + i)
    transcripts, answers = [], []
    async def on_text(text, t):
        transcripts.append(text)
        answers.extend(said(brain.hear(text, t=T0 + 20 + t - 1000)))
    fake = FakeStream(pcm)
    gate = L.Gate(onset=0.12, min_voiced=0.25) if baseline else L.Gate()
    await L.listen(L.local_chunks(stream_factory=fake, clock=fake.clock), stt, on_text, gate=gate)
    actual = ' '.join(transcripts)
    return dict(transcript=actual, utterances=len(transcripts), answers=answers,
                current=brain.current, correct=(brain.current == target and
                (answer is None or answers == [answer])),
                exact=words(actual) == words(text), errors=distance(words(text), words(actual)),
                words=len(words(text)))


def measure(directory, output, limit, baseline=False):
    if shutil.which('memory_pressure'):
        pressure = subprocess.run(['memory_pressure', '-Q'], capture_output=True, text=True, check=True)
        free = re.search(r'free percentage: (\d+)%', pressure.stdout)
        if free and int(free.group(1)) < 40:
            print('Memory below 40% free; pause and resume this batch later.', file=sys.stderr)
            raise SystemExit(75)
    model = cached_model()
    if not model:
        raise SystemExit('Whisper weights missing; no downloads attempted.')
    manifest = json.loads((directory / 'manifest.json').read_text())
    done = {r['id'] for r in map(json.loads, output.read_text().splitlines())} if output.exists() else set()
    pending = [r for r in manifest['trials'] if r['id'] not in done][:limit]
    if not pending:
        print('No pending trials.')
        return
    stt = L.Whisper(model).load()
    if baseline:
        stt.VOCABULARY = None
    started = time.monotonic()
    with output.open('a') as f:
        for row in pending:
            if time.monotonic() - started > 140:
                break  # each model batch remains short; invoke again to resume
            pcm = (decode(directory / f"{row['id']}.wav") * 32768).astype(np.int16)
            result = asyncio.run(trial(pcm, stt, row['case'], baseline))
            f.write(json.dumps(dict(row, **result)) + '\n')
            f.flush()
            print(row['id'], result['correct'], result['transcript'], flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('prepare', 'measure'))
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--fixtures', action='store_true', help='Three existing recordings; NOT the eight-voice matrix')
    p.add_argument('--output', type=Path)
    p.add_argument('--limit', type=int, default=24)
    p.add_argument('--baseline', action='store_true', help='Original gate timing and no vocabulary prompt')
    args = p.parse_args()
    if args.action == 'prepare':
        prepare(args.directory, args.fixtures)
    else:
        if not args.output:
            p.error('--output is required for measure')
        measure(args.directory, args.output, args.limit, args.baseline)


if __name__ == '__main__':
    main()
