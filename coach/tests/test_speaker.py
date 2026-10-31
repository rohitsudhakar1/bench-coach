import shutil
import subprocess
import time

import pytest

from coach.speaker import Speaker


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_order_warnings_then_answers_then_newest_step():
    s = Speaker(backend="none", gap=0.0)
    s.submit("Next: step two.", 1, "step")
    s.submit("Next: step three.", 1, "step")
    s.submit("Did you just draw?", 2, "answer")
    s.submit("Same tip used twice.", 3, "tip")
    s.start()
    assert s.wait_idle(5)
    s.stop()
    assert s.spoken == ["Same tip used twice.", "Did you just draw?", "Next: step three."]


def test_old_lines_are_dropped():
    clock = Clock()
    s = Speaker(backend="none", gap=0.0, max_age=8.0, clock=clock)
    s.submit("Next: old step.", 1, "step")
    s.submit("An old answer.", 2, "answer")
    clock.t = 10.0
    assert s._pick()[2] == "An old answer."   # answers keep for 16 s
    assert s._pick() is None                  # the step line expired


def test_gap_between_lines():
    s = Speaker(backend="none", gap=0.3)
    s.start()
    t0 = time.monotonic()
    s.submit("one", 3)
    s.submit("two", 3)
    assert s.wait_idle(5)
    s.stop()
    assert s.spoken == ["one", "two"] and time.monotonic() - t0 >= 0.3


def test_printed_lines_never_mute_the_mic(capsys):
    s = Speaker(backend="print", sync=True)
    s.submit("Next: eject the tip.")
    assert "[coach says] Next: eject the tip." in capsys.readouterr().err
    assert not s.busy()


# Requires an installed system speech voice, even though output is file-only.
@pytest.mark.models
@pytest.mark.skipif(not shutil.which("say") or not shutil.which("afinfo"), reason="macOS only")
def test_speak_to_writes_audio_files_and_plays_nothing(tmp_path):
    # Check the system synthesizer independently of Speaker. Some restricted sessions
    # report success from say but return a header with zero audio samples.
    probe = tmp_path / "system-probe.aiff"
    subprocess.run(["say", "-o", str(probe), "Testing speech."], check=True, timeout=15)
    probe_info = subprocess.run(["afinfo", str(probe)], capture_output=True, text=True,
                                check=True, timeout=10).stdout
    if "audio bytes: 0" in probe_info:
        pytest.skip("System say produces empty file audio in this session")
    s = Speaker(backend="file", out_dir=str(tmp_path), sync=True)
    s.submit("Next: dispense it into tube B.")
    assert len(s.files) == 1
    info = subprocess.run(["afinfo", s.files[0]], capture_output=True, text=True).stdout
    sec = float(info.split("estimated duration:")[1].split()[0])
    assert 0.5 < sec < 5, info
    assert not s.busy()   # a file is not the speaker
