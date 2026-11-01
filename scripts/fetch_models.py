#!/usr/bin/env python3
"""List, download and check the model files the demo needs, so it can start with Wi-Fi off.

    fetch_models.py               list every model, its size, licence and whether it is here
    fetch_models.py --download    fetch the missing default models into the normal caches
    fetch_models.py --check       load each one with the network blocked; exit 1 if any fails
    --with florence2 gdino-base owlv2   also handle these optional detectors

Hugging Face models go to the Hugging Face cache (HF_HOME or HF_HUB_CACHE move it). The hand
model goes to vision/models/, where vision looks for it. --check sets HF_HUB_OFFLINE=1 and
TRANSFORMERS_OFFLINE=1 and also refuses every non-local connection, so a pass means no part
will reach for the network to load these files.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OFFLINE_FLAGS = {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
HAND_URL = ('https://storage.googleapis.com/mediapipe-models/hand_landmarker/'
            'hand_landmarker/float16/latest/hand_landmarker.task')
GDINO_FILES = ('config.json', 'model.safetensors', 'preprocessor_config.json', 'tokenizer.json',
               'tokenizer_config.json', 'special_tokens_map.json', 'vocab.txt')
SAM_FILES = ('config.json', 'model.safetensors', 'preprocessor_config.json', 'processor_config.json')


@dataclass(frozen=True)
class Model:
    key: str
    kind: str          # hf, whisper, file or bundled
    source: str        # Hugging Face repo, download URL or package
    files: tuple       # files that must be there (relative to the repo snapshot, or one path)
    mb: int            # download size
    licence: str
    used_by: str
    default: bool = True


MODELS = (
    Model('gdino-tiny', 'hf', 'IDEA-Research/grounding-dino-tiny', GDINO_FILES, 690, 'Apache-2.0',
          'vision boxes (default detector)'),
    Model('edgetam', 'hf', 'yonigozlan/EdgeTAM-hf', SAM_FILES, 56, 'Apache-2.0',
          'object outlines in render_demo and the live view (vision/masks.py)'),
    Model('hand-landmarker', 'file', HAND_URL, ('vision/models/hand_landmarker.task',), 8, 'Apache-2.0',
          'vision hands'),
    Model('whisper-small.en', 'whisper', 'Systran/faster-whisper-small.en',
          ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt'), 486, 'MIT',
          'coach speech, preflight microphone'),
    Model('silero-vad', 'bundled', 'faster_whisper', ('assets/silero_vad_v6.onnx',), 2, 'MIT',
          'coach speech (ships inside faster-whisper)'),
    Model('florence2', 'hf', 'florence-community/Florence-2-base',
          ('config.json', 'model.safetensors', 'preprocessor_config.json', 'processor_config.json',
           'tokenizer.json', 'tokenizer_config.json', 'vocab.json', 'merges.txt', 'added_tokens.json',
           'special_tokens_map.json', 'generation_config.json'), 470, 'MIT',
          'vision --detector florence2', default=False),
    Model('sam2.1-tiny', 'hf', 'facebook/sam2.1-hiera-tiny', SAM_FILES, 156, 'Apache-2.0',
          'outlines with VISION_MASKS_MODEL=sam2.1-tiny', default=False),
    Model('gdino-base', 'hf', 'IDEA-Research/grounding-dino-base', GDINO_FILES, 935, 'Apache-2.0',
          'vision --detector gdino-base', default=False),
    Model('owlv2', 'hf', 'google/owlv2-base-patch16-ensemble',
          ('config.json', 'model.safetensors', 'preprocessor_config.json', 'tokenizer_config.json',
           'vocab.json', 'merges.txt', 'special_tokens_map.json', 'added_tokens.json'), 622, 'Apache-2.0',
          'vision --detector owlv2', default=False),
)
BY_KEY = {m.key: m for m in MODELS}
OPTIONAL = [m.key for m in MODELS if not m.default]
# Models each launcher part loads with its default settings.
PART_MODELS = {'vision': ('gdino-tiny', 'hand-landmarker'), 'coach': ('whisper-small.en', 'silero-vad')}


def selected(extra=()):
    return [m for m in MODELS if m.default or m.key in extra]


def offline_flags_set(env=None):
    env = os.environ if env is None else env
    return any(env.get(k, '').strip().lower() in ('1', 'true', 'yes', 'on') for k in OFFLINE_FLAGS)


def missing_files(model: Model, root: Path = ROOT) -> list[str]:
    """Files of this model that are not on disk. Looks only, never loads or downloads."""
    if model.kind == 'file':
        return [f for f in model.files if not (root / f).is_file() or (root / f).stat().st_size == 0]
    if model.kind == 'bundled':
        try:
            import importlib.util
            spec = importlib.util.find_spec(model.source)
        except (ImportError, ValueError):
            spec = None
        if spec is None or not spec.origin:
            return [f'{model.source} (package not installed)']
        base = Path(spec.origin).parent
        return [f for f in model.files if not (base / f).is_file()]
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return [f'{f} (huggingface_hub not installed)' for f in model.files]
    out = []
    for f in model.files:
        path = try_to_load_from_cache(model.source, f)
        if not isinstance(path, str) or not os.path.isfile(path) or os.path.getsize(path) == 0:
            out.append(f)
    return out


def missing_for(keys, root: Path = ROOT) -> list[str]:
    return [k for k in keys if missing_files(BY_KEY[k], root)]


# ---------- network guard and loaders, run in a child process ----------

def block_network():
    """Refuse DNS lookups and connections to anything but this machine; count the attempts."""
    import socket
    tries = []
    local = ('localhost', '127.0.0.1', '::1', '', None)

    def is_local(host):
        return host in local or str(host).startswith('127.')

    real_getaddrinfo, real_connect, real_connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex

    def getaddrinfo(host, *a, **k):
        if not is_local(host if not isinstance(host, bytes) else host.decode()):
            tries.append(str(host))
            raise socket.gaierror(socket.EAI_NONAME, 'network blocked by fetch_models --check')
        return real_getaddrinfo(host, *a, **k)

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not is_local(address[0]):
            tries.append(str(address[0]))
            raise OSError('network blocked by fetch_models --check')
        return real_connect(self, address)

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not is_local(address[0]):
            tries.append(str(address[0]))
            return 111
        return real_connect_ex(self, address)

    socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = getaddrinfo, connect, connect_ex
    return tries


def load(key: str):
    """Load one model from disk and run it once on a blank input."""
    import numpy as np
    m = BY_KEY[key]
    if key in ('gdino-tiny', 'gdino-base'):
        import torch
        from PIL import Image
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        proc = AutoProcessor.from_pretrained(m.source)
        model = AutoModelForZeroShotObjectDetection.from_pretrained(m.source).eval()
        inputs = proc(images=Image.new('RGB', (320, 240)), text='pipette .', return_tensors='pt')
        with torch.no_grad():
            model(**inputs)
    elif key == 'owlv2':
        import torch
        from PIL import Image
        from transformers import Owlv2ForObjectDetection, Owlv2Processor
        proc = Owlv2Processor.from_pretrained(m.source)
        model = Owlv2ForObjectDetection.from_pretrained(m.source).eval()
        with torch.no_grad():
            model(**proc(text=[['a pipette']], images=Image.new('RGB', (320, 240)), return_tensors='pt'))
    elif key in ('edgetam', 'sam2.1-tiny'):
        sys.path.insert(0, str(ROOT))
        from vision.masks import Segmenter
        Segmenter(key, device='cpu')(np.zeros((240, 320, 3), np.uint8), [[40, 40, 200, 200]])
    elif key == 'florence2':
        from transformers import AutoProcessor, Florence2ForConditionalGeneration
        AutoProcessor.from_pretrained(m.source)
        Florence2ForConditionalGeneration.from_pretrained(m.source).eval()
    elif key == 'hand-landmarker':
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision as mpv
        options = mpv.HandLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(ROOT / m.files[0])),
                                            num_hands=2, running_mode=mpv.RunningMode.IMAGE)
        with mpv.HandLandmarker.create_from_options(options) as tracker:
            tracker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.zeros((240, 320, 3), np.uint8)))
    elif key == 'whisper-small.en':
        from faster_whisper import WhisperModel
        model = WhisperModel('small.en', device='cpu', compute_type='int8', local_files_only=True)
        list(model.transcribe(np.zeros(16000, np.float32), language='en', beam_size=1)[0])
    elif key == 'silero-vad':
        from faster_whisper.vad import get_speech_timestamps
        get_speech_timestamps(np.zeros(16000, np.float32))
    else:
        raise KeyError(key)


def child_main(key):
    os.environ.update(OFFLINE_FLAGS)
    tries = block_network()
    start = time.monotonic()
    error = None
    try:
        load(key)
    except Exception as exc:  # reported to the parent as one line
        error = f'{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ""}'
    print(json.dumps({'key': key, 'seconds': round(time.monotonic() - start, 1),
                      'error': error, 'network_tries': len(tries)}))
    return 0 if error is None and not tries else 1


def check_one(model: Model, timeout: float = 150) -> tuple[bool, str]:
    gone = missing_files(model)
    if gone:
        return False, 'missing ' + ', '.join(gone) + '; run scripts/fetch_models.py --download' + (
            f' --with {model.key}' if not model.default else '')
    env = {**os.environ, **OFFLINE_FLAGS}
    try:
        done = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--load-one', model.key],
                              capture_output=True, text=True, timeout=timeout, env=env, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return False, f'load took over {timeout:.0f} s'
    try:
        row = json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        tail = (done.stderr.strip().splitlines() or ['no output'])[-1][:200]
        return False, f'loader crashed: {tail}'
    if row['error']:
        return False, row['error']
    if row['network_tries']:
        return False, f'loaded, but tried the network {row["network_tries"]} times'
    return True, f'loads in {row["seconds"]} s with the network blocked'


def check(models) -> int:
    print(f'Checking {len(models)} models with HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1 and the network blocked.', flush=True)
    bad = 0
    for m in models:
        ok, detail = check_one(m)
        bad += not ok
        print(f'{"ok     " if ok else "FAILED "} {m.key:<17} {detail}', flush=True)
    print('offline ready' if not bad else f'not offline ready: {bad} of {len(models)} failed', flush=True)
    return 1 if bad else 0


# ---------- download ----------

def download_one(m: Model):
    if m.kind == 'bundled':
        raise RuntimeError(f'reinstall {m.source} (pip install -r requirements.txt); it ships this file')
    if m.kind == 'file':
        import urllib.request
        dest = ROOT / m.files[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + '.part')
        urllib.request.urlretrieve(m.source, part)
        part.replace(dest)
        return
    from huggingface_hub import snapshot_download
    snapshot_download(m.source, allow_patterns=list(m.files))


def download(models) -> int:
    if offline_flags_set():
        print('HF_HUB_OFFLINE or TRANSFORMERS_OFFLINE is set; unset them to download.', file=sys.stderr)
        return 1
    todo = [m for m in models if missing_files(m)]
    if not todo:
        print('Everything is already here.')
        return check(models)
    need = sum(m.mb for m in todo) * 1_000_000
    free = shutil.disk_usage(ROOT).free
    if free < need + 1_000_000_000:
        print(f'Need about {need / 1e9:.1f} GB plus 1 GB spare; only {free / 1e9:.1f} GB free.', file=sys.stderr)
        return 1
    failed = 0
    for m in todo:
        print(f'downloading {m.key} (about {m.mb} MB, {m.licence}) ...', flush=True)
        try:
            download_one(m)
        except Exception as exc:
            failed += 1
            print(f'FAILED {m.key}: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
    if failed:
        return 1
    return check(models)


def listing(models) -> int:
    print(f'{"model":<17} {"size":>7}  {"licence":<10} {"here":<5} used by')
    for m in models:
        here = 'yes' if not missing_files(m) else 'no'
        print(f'{m.key:<17} {m.mb:>4} MB  {m.licence:<10} {here:<5} {m.used_by}')
    total = sum(m.mb for m in models if missing_files(m))
    print(f'\nStill to download: about {total} MB.' if total else '\nAll here. Run --check to load each one offline.')
    print('Optional, add with --with: ' + ', '.join(f'{k} ({BY_KEY[k].mb} MB)' for k in OPTIONAL))
    print('Not handled here: YOLOE (AGPL-3.0, vision --detector yoloe downloads into VISION_YOLO_DIR) '
          'and the unadopted tip model (vision/tipmodel, built locally).')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--download', action='store_true', help='fetch missing models, then check them')
    mode.add_argument('--check', action='store_true', help='load every model with the network blocked')
    mode.add_argument('--load-one', metavar='KEY', help=argparse.SUPPRESS)
    p.add_argument('--with', dest='extra', nargs='+', choices=OPTIONAL, default=[], help='optional models too')
    a = p.parse_args(argv)
    if a.load_one:
        return child_main(a.load_one)
    models = selected(a.extra)
    if a.check:
        return check(models)
    if a.download:
        return download(models)
    return listing(models)


if __name__ == '__main__':
    raise SystemExit(main())
