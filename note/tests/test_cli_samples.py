import os
import subprocess
import sys
from pathlib import Path

import pytest

from note import fakerun

ROOT = Path(__file__).resolve().parents[2]


def test_cli_build(fake_run):
    r = subprocess.run([sys.executable, "-m", "note", "build", str(fake_run), "--no-claude", "--post",
                        "--hub", "http://127.0.0.1:8899"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "5 done, 3 skipped" in r.stdout and '"kind": "note"' in r.stdout
    assert (fake_run / "note.html").is_file()


def test_real_bench_clips(tmp_path):
    base = os.environ.get("BENCH_SAMPLES")
    if not base or not all((Path(base) / c).is_file() for c in fakerun.SAMPLE_CLIPS):
        pytest.skip("set BENCH_SAMPLES to the sample video folder")
    folder = fakerun.make(tmp_path / "real", samples=True)
    from note.service import make_note
    note = make_note(folder, use_claude=False)["note"]
    frames = [f for p in note["problems"] for f in p["frames"]]
    assert len(frames) == 6
    for f in frames:
        assert (folder / f["file"]).stat().st_size > 10000
