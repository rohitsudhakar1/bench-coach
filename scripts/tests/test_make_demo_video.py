"""Checks for the demo video script that do not need the demo running."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

import scripts.make_demo_video as mdv

needs_ffmpeg = pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg missing')


def test_launch_uses_the_rehearsed_clips_and_scripted_vision(tmp_path):
    cmd = mdv.launch_command(tmp_path, 8700)
    assert cmd[0].endswith('scripts/demo.sh')
    assert '--fake-vision' in cmd
    assert str(tmp_path / 'lsv_mv/CP-Left-Front-12-27.mp4') in cmd
    assert str(tmp_path / 'lsv_mv/CP-Right-Front-12-27.mp4') in cmd
    assert '--speak' not in cmd          # never sound


def test_missing_clips_are_reported(tmp_path):
    assert mdv.missing_clips(tmp_path) == list(mdv.CLIPS)


def test_cards_and_tag_say_what_the_brief_asks():
    assert 'Built at MIT ScienceClaw 2026' in mdv.CARD
    assert 'Bench Coach' in mdv.CARD
    assert mdv.TAG == 'Sample clips as cameras. Vision events scripted for this recording.'
    assert mdv.json.dumps(mdv.TAG) in mdv.OVERLAY_JS


def test_cues_run_in_order_and_before_stop():
    times = [t for t, _ in mdv.RUN_CUES]
    assert times == sorted(times) and times[-1] < mdv.STOP_AT


def test_script_has_no_local_paths():
    text = Path(mdv.__file__).read_text()
    assert '/Users/' not in text


@needs_ffmpeg
def test_mp4v_recordings_become_h264(tmp_path):
    cam = tmp_path / 'cam0.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=10',
                    '-t', '1', '-c:v', 'mpeg4', str(cam)], check=True)
    assert mdv.codec(cam) == 'mpeg4'
    assert mdv.playable_recordings(tmp_path) == [('cam0.mp4', 'mpeg4')]
    assert mdv.codec(cam) == 'h264'
    assert mdv.playable_recordings(tmp_path) == []   # already h264: left alone


def test_finished_video_if_present():
    final = mdv.ROOT / 'runs' / 'demo-video' / 'bench-coach-demo.mp4'
    if not final.is_file() or not shutil.which('ffprobe'):
        pytest.skip('no recorded video here')
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                            'stream=codec_type,codec_name,width,height:format=duration',
                            '-of', 'default=nw=1', str(final)], capture_output=True, text=True).stdout
    assert 'codec_name=h264' in probe and 'width=1920' in probe and 'height=1080' in probe
    assert 'codec_type=audio' not in probe
    duration = float(probe.split('duration=')[1].split()[0])
    assert 100 <= duration <= 140
    assert final.stat().st_size < 50e6
