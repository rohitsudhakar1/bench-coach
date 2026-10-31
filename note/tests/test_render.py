import json

from note import fakerun
from note.service import make_note


def test_files_written_and_linked(fake_run):
    res = make_note(fake_run, use_claude=False)
    md = (fake_run / "note.md").read_text()
    html = (fake_run / "note.html").read_text()
    data = json.loads((fake_run / "note.json").read_text())
    assert "## What went wrong" in md and "cam0.mp4#t=11.7" in md and "cam1.mp4#t=" in md
    assert "note_frames/problem-1-cam0.jpg" in md
    assert "<video" in html and "muted" in html and "data-off" in html
    assert data["run"] == "run-fake" and res["note"]["wording"] == "rule-based"
    for item in data["steps"] + data["technique"] + data["said"] + data["problems"]:
        assert item["at"] is not None and set(item["offsets"]) == {"cam0.mp4", "cam1.mp4"}


def test_text_is_escaped(tmp_path):
    folder = fakerun.make(tmp_path / "r")
    with open(folder / "events.jsonl", "a") as fh:
        fh.write(json.dumps(fakerun.ev(30.0, "coach", "speech", text="<script>alert(1)</script> 5 ml added")) + "\n")
    make_note(folder, use_claude=False, frames=False)
    html = (folder / "note.html").read_text()
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


def test_no_recordings_shows_run_times(tmp_path):
    folder = fakerun.make(tmp_path / "r")
    for p in folder.glob("*.mp4"):
        p.unlink()
    make_note(folder, use_claude=False)
    md = (folder / "note.md").read_text()
    assert "`00:12`" in md and "<video" not in (folder / "note.html").read_text()
