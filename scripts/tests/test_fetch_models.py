"""fetch_models: the listing, the file lookups, the network guard, and the launcher's offline switch.
No downloads. The real offline load runs only when the default models are already on disk."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import fetch_models as fm
from scripts import run_demo as demo

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def empty_cache(tmp_path, monkeypatch):
    """An empty Hugging Face cache and repo root, so every model reads as missing."""
    import huggingface_hub.constants as c
    monkeypatch.setattr(c, 'HF_HUB_CACHE', str(tmp_path / 'hub'))
    (tmp_path / 'hub').mkdir()
    return tmp_path


def fake_snapshot(cache: Path, repo: str, files):
    folder = cache / 'hub' / ('models--' + repo.replace('/', '--'))
    (folder / 'refs').mkdir(parents=True)
    (folder / 'refs' / 'main').write_text('abc123')
    snap = folder / 'snapshots' / 'abc123'
    snap.mkdir(parents=True)
    for f in files:
        (snap / f).write_text('x')
    return snap


def test_every_default_model_is_listed_with_size_and_licence():
    keys = [m.key for m in fm.selected()]
    assert keys == ['gdino-tiny', 'edgetam', 'hand-landmarker', 'whisper-small.en', 'silero-vad']
    assert set(fm.OPTIONAL) == {'florence2', 'sam2.1-tiny', 'gdino-base', 'owlv2'}
    for m in fm.MODELS:
        assert m.mb > 0 and m.licence and m.used_by
    # vision's own default model id and hand URL are the ones listed here
    assert fm.BY_KEY['gdino-tiny'].source == 'IDEA-Research/grounding-dino-tiny'
    from vision import hands
    assert fm.BY_KEY['hand-landmarker'].source == hands.MODEL_URL
    assert ROOT / fm.BY_KEY['hand-landmarker'].files[0] == hands.MODEL


def test_lookup_sees_missing_and_partial_snapshots(empty_cache):
    gdino = fm.BY_KEY['gdino-tiny']
    assert fm.missing_files(gdino, empty_cache) == list(gdino.files)
    fake_snapshot(empty_cache, gdino.source, gdino.files[:-1])
    assert fm.missing_files(gdino, empty_cache) == [gdino.files[-1]]
    hand = fm.BY_KEY['hand-landmarker']
    assert fm.missing_files(hand, empty_cache) == list(hand.files)
    path = empty_cache / hand.files[0]
    path.parent.mkdir(parents=True)
    path.write_bytes(b'')
    assert fm.missing_files(hand, empty_cache) == list(hand.files), 'an empty file is not a model'
    path.write_bytes(b'model')
    assert fm.missing_files(hand, empty_cache) == []


def test_complete_snapshot_counts_as_present(empty_cache):
    whisper = fm.BY_KEY['whisper-small.en']
    fake_snapshot(empty_cache, whisper.source, whisper.files)
    assert fm.missing_files(whisper, empty_cache) == []
    assert fm.missing_for(['whisper-small.en', 'gdino-tiny'], empty_cache) == ['gdino-tiny']


def test_listing_runs_without_loading_anything(capsys):
    assert fm.main([]) == 0
    out = capsys.readouterr().out
    for key in ('gdino-tiny', 'hand-landmarker', 'whisper-small.en', 'Apache-2.0', 'MIT', 'florence2'):
        assert key in out


def test_check_reports_missing_model_with_the_fix(empty_cache, monkeypatch, capsys):
    monkeypatch.setattr(fm, 'ROOT', empty_cache)
    assert fm.check([fm.BY_KEY['gdino-tiny']]) == 1
    out = capsys.readouterr().out
    assert 'FAILED  gdino-tiny' in out and '--download' in out and 'not offline ready' in out


def test_download_refuses_with_offline_flags(monkeypatch, capsys):
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    assert fm.download(fm.selected()) == 1
    assert 'unset them' in capsys.readouterr().err


def test_download_refuses_when_disk_is_short(empty_cache, monkeypatch, capsys):
    for k in fm.OFFLINE_FLAGS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(fm, 'ROOT', empty_cache)
    monkeypatch.setattr(fm.shutil, 'disk_usage', lambda _: SimpleNamespace(free=500_000_000))
    monkeypatch.setattr(fm, 'download_one', lambda m: pytest.fail('should not download'))
    assert fm.download([fm.BY_KEY['gdino-tiny']]) == 1
    assert 'GB free' in capsys.readouterr().err


def test_guard_blocks_outside_connections_but_not_localhost():
    code = '''
import json, socket, urllib.request
from scripts import fetch_models as fm
tries = fm.block_network()
out = {}
try:
    urllib.request.urlopen("https://huggingface.co", timeout=3)
    out["outside"] = "reached"
except OSError as exc:
    out["outside"] = "blocked"
server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen(1)
client = socket.socket(); client.connect(server.getsockname()); out["local"] = "ok"
out["tries"] = len(tries)
print(json.dumps(out))
'''
    done = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True, timeout=30)
    row = json.loads(done.stdout.strip().splitlines()[-1])
    assert row == {'outside': 'blocked', 'local': 'ok', 'tries': 1}


def test_hand_model_never_downloads_with_offline_flags(tmp_path, monkeypatch):
    from vision import hands
    monkeypatch.setattr(hands, 'MODEL', tmp_path / 'models' / 'hand_landmarker.task')
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlretrieve', lambda *a: pytest.fail('tried to download'))
    with pytest.raises(RuntimeError, match='fetch_models.py --download'):
        hands.ensure_model()


def test_hand_model_failed_download_leaves_no_file(tmp_path, monkeypatch):
    from vision import hands
    monkeypatch.setattr(hands, 'MODEL', tmp_path / 'models' / 'hand_landmarker.task')
    for k in fm.OFFLINE_FLAGS:
        monkeypatch.delenv(k, raising=False)
    import urllib.request
    def broken(url, dest):
        Path(dest).write_bytes(b'half')
        raise OSError('no route')
    monkeypatch.setattr(urllib.request, 'urlretrieve', broken)
    with pytest.raises(RuntimeError, match='could not download'):
        hands.ensure_model()
    assert not hands.MODEL.exists()


@pytest.fixture
def launcher_args(tmp_path):
    for name in ('a.mp4', 'b.mp4'):
        (tmp_path / name).write_bytes(b'')
    def make(*extra):
        return demo.parse_args(['--source', str(tmp_path / 'a.mp4'), str(tmp_path / 'b.mp4'),
                                '--base-port', '8840', *extra])
    return make


def test_launcher_knows_which_parts_load_models(launcher_args):
    parts = demo.plan(launcher_args())
    # file sources give the coach --mic off, so only vision's models are needed
    assert demo.needed_models(parts) == ['gdino-tiny', 'hand-landmarker']
    assert demo.needed_models(demo.plan(launcher_args('--fake-vision'))) == []
    live = demo.plan(demo.parse_args(['--cams', '0', '1', '--base-port', '8840']))
    assert demo.needed_models(live) == ['gdino-tiny', 'hand-landmarker', 'whisper-small.en', 'silero-vad']


def test_launcher_sets_offline_flags_only_when_everything_is_here(monkeypatch, launcher_args):
    parts = demo.plan(launcher_args())
    real = demo.load_fetch_models()
    assert real is not None and real.OFFLINE_FLAGS == fm.OFFLINE_FLAGS
    def with_missing(names):
        stub = SimpleNamespace(OFFLINE_FLAGS=real.OFFLINE_FLAGS,
                               missing_for=lambda keys, root: [k for k in keys if k in names])
        return lambda root=None: stub
    monkeypatch.setattr(demo, 'load_fetch_models', with_missing(set()))
    env = {}
    assert 'start offline' in demo.offline_env(parts, env)
    assert env == fm.OFFLINE_FLAGS
    monkeypatch.setattr(demo, 'load_fetch_models', with_missing({'gdino-tiny'}))
    env = {}
    assert 'gdino-tiny not downloaded' in demo.offline_env(parts, env)
    assert env == {}
    env = {'HF_HUB_OFFLINE': '0'}
    assert 'keeping your' in demo.offline_env(parts, env)
    assert env == {'HF_HUB_OFFLINE': '0'}
    assert demo.offline_env(demo.plan(launcher_args('--fake-vision')), {}) is None


@pytest.mark.skipif(bool(fm.missing_for([m.key for m in fm.selected()])),
                    reason='default models are not on disk here')
def test_real_offline_check_passes():
    env = {k: v for k, v in os.environ.items() if k not in fm.OFFLINE_FLAGS}
    done = subprocess.run([sys.executable, 'scripts/fetch_models.py', '--check'], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout
    assert done.stdout.strip().endswith('offline ready')
    assert done.stdout.count('with the network blocked') == 5
