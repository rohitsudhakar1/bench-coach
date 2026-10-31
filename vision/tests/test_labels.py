"""OCR contract tests; no Swift, Vision, network or model needed."""
import argparse
import json
from pathlib import Path
import subprocess

import pytest

from vision import labels


def test_crop_and_pixel_mapping():
    assert labels.parse_crop(' 100, 200, 400, 200 ') == (100, 200, 400, 200)
    row = labels.parse_observations(
        '[{"text":"Tube A\\nβ", "confidence":0.8, "box":[0.25,0.1,0.5,0.2]}]',
        t=12.5, crop=(100, 200, 400, 200))[0]
    assert row == {"t": 12.5, "text": "Tube A\nβ", "confidence": 0.8,
                   "box": [200, 340, 200, 40]}
    assert json.loads(json.dumps(row)) == row
    assert labels.parse_observations('[]', t=0, crop=(0, 0, 10, 10)) == []


@pytest.mark.parametrize('value', ['1,2,3', '-1,2,3,4', '0,0,0,1', '0,0,1,-1', '1.5,0,2,3', 'x'])
def test_bad_crop(value):
    with pytest.raises(argparse.ArgumentTypeError):
        labels.parse_crop(value)


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf'])
def test_bad_rate(value):
    with pytest.raises(argparse.ArgumentTypeError):
        labels.positive_float(value)


@pytest.mark.parametrize('payload', [
    '{}', 'null', '[null]', '[{"text":3}]', 'broken',
    '[{"text":"A","confidence":NaN,"box":[0,0,1,1]}]',
    '[{"text":"A","confidence":true,"box":[0,0,1,1]}]',
    '[{"text":"A","confidence":1.1,"box":[0,0,1,1]}]',
    '[{"text":"A","confidence":0.9,"box":[0,0,0,1]}]',
    '[{"text":"A","confidence":0.9,"box":[0.8,0,0.3,1]}]',
    '[{"text":"A","confidence":0.9,"box":[0,0,1]}]',
    '[{"text":"A","confidence":0.9,"box":[0,0,1,Infinity]}]',
])
def test_malformed_native_response(payload):
    with pytest.raises(ValueError):
        labels.parse_observations(payload, t=0, crop=(0, 0, 100, 100))


def test_non_mac_fallback_never_opens_video_or_compiles(monkeypatch, capsys):
    monkeypatch.setattr(labels.platform, 'system', lambda: 'Linux')
    def forbidden(*args, **kwargs):
        pytest.fail('fallback must not run a subprocess or open a video')
    monkeypatch.setattr(labels.subprocess, 'run', forbidden)
    monkeypatch.setattr(labels, 'video_rows', forbidden)
    assert labels.main(['--source', 'does-not-exist.mov']) == 2
    row = json.loads(capsys.readouterr().out)
    assert row['status'] == 'unavailable'
    assert row['text'] is None and row['confidence'] is None and row['box'] is None
    assert row['t'] is None


def test_missing_swift(monkeypatch):
    monkeypatch.setattr(labels.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(labels.shutil, 'which', lambda name: None)
    with pytest.raises(labels.OCRUnavailable, match='swiftc'):
        with labels.native_helper():
            pytest.fail('no compiler')


def test_compile_failure_does_not_leak_diagnostics(monkeypatch, capsys):
    monkeypatch.setattr(labels.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(labels.shutil, 'which', lambda name: 'swiftc')
    monkeypatch.setattr(labels.subprocess, 'run', lambda *a, **kw:
                        subprocess.CompletedProcess(a, 1, '', 'private machine path'))
    assert labels.main(['--source', 'example.mov']) == 2
    output = capsys.readouterr().out
    assert 'private machine' not in output
    assert json.loads(output)['status'] == 'unavailable'


def test_native_failure_and_timeout(monkeypatch):
    monkeypatch.setattr(labels.subprocess, 'run', lambda *a, **kw:
                        subprocess.CompletedProcess(a, 1, '', 'failed'))
    with pytest.raises(labels.OCRUnavailable):
        labels.recognize(Path('helper'), Path('image.png'), t=0, crop=(0, 0, 100, 100))
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired('helper', 30)
    monkeypatch.setattr(labels.subprocess, 'run', timeout)
    with pytest.raises(labels.OCRUnavailable, match='timed out'):
        labels.recognize(Path('helper'), Path('image.png'), t=0, crop=(0, 0, 100, 100))


def test_video_sampling_crop_and_empty_frames(tmp_path, monkeypatch):
    import cv2
    import numpy as np
    source = tmp_path / 'sample.avi'
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'MJPG'), 10, (100, 80))
    assert writer.isOpened()
    for _ in range(20):
        writer.write(np.full((80, 100, 3), 180, dtype=np.uint8))
    writer.release()
    calls = []
    def fake_recognize(binary, image, *, t, crop):
        assert cv2.imread(str(image)).shape[:2] == (40, 60)
        calls.append((t, crop))
        return []
    monkeypatch.setattr(labels, 'recognize', fake_recognize)
    rows = list(labels.video_rows(str(source), Path('unused'), sample_rate=3,
                                 crop=(10, 20, 30, 20), upscale=2, time_origin=100,
                                 start=0.2, seconds=1))
    assert [row['t'] for row in rows] == [100.2, 100.6, 100.9]
    assert all(row['status'] == 'no_text' for row in rows)
    assert all(crop == (10, 20, 30, 20) for _, crop in calls)
    with pytest.raises(ValueError, match='crop'):
        list(labels.video_rows(str(source), Path('unused'), crop=(90, 0, 30, 20)))
    with pytest.raises(ValueError, match='sample rate'):
        list(labels.video_rows(str(source), Path('unused'), sample_rate=11))
