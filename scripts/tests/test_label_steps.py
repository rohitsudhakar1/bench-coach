"""Label editor endpoints, silent preview preparation, and renderer compatibility."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from scripts import label_steps as S
from scripts import render_demo as R
from scripts.render_layout import Timeline


@pytest.fixture
def setup(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0123456789abcdef")
    protocol = tmp_path / "protocol.txt"
    protocol.write_text("1. Attach a tip\n2. Open the bottle\n3. Draw from tube A\n")
    out = tmp_path / "labels.jsonl"
    info = {"duration": 10.0, "fps": 30, "frames": [0, 1 / 30], "codec": "h264"}
    return video, protocol, out, info


@pytest.fixture
def client(setup):
    with TestClient(S.create_app(*setup)) as client:
        yield client


def save(client, rows):
    return client.post("/api/save", json=rows, headers={"X-Label-Token": client.get("/api/session").json()["token"]})


def test_page_session_and_video_ranges(client):
    assert "Label protocol steps" in client.get("/").text
    session = client.get("/api/session").json()
    assert len(session["steps"]) == 3 and session["labels"] == []
    assert session["video"] == "clip.mp4"
    response = client.get("/video", headers={"Range": "bytes=3-7"})
    assert response.status_code == 206 and response.content == b"34567"
    assert response.headers["content-range"] == "bytes 3-7/16"
    assert client.get("/video", headers={"Range": "bytes=100-200"}).status_code == 416
    assert client.get("/missing").status_code == 404


def test_explicit_steps_roundtrip_at_exact_video_times(client, setup):
    _, protocol, out, _ = setup
    rows = [{"s": 4.125, "kind": "step", "step": 1, "status": "done"},
            {"s": 2.5, "kind": "step", "step": 0, "status": "done"},
            {"s": 7, "kind": "step", "step": 2, "status": "skipped"},
            {"s": 7, "kind": "warning", "step": 2, "code": "skipped_step"},
            {"s": 3, "kind": "warning", "step": 1, "code": "reused_tip"}]
    assert save(client, rows).json() == {"saved": 5, "out": "labels.jsonl"}
    saved = [json.loads(line) for line in out.read_text().splitlines()]
    assert [row["s"] for row in saved] == [2.5, 3, 4.125, 7, 7]
    loaded = R.labelled_events(out, 1000)
    assert loaded[2]["t"] == 1004.125
    assert all(e["detail"]["labelled"] for e in loaded)
    # A bogus detected draw must not override the explicit manual outcomes.
    detected = {"s": 1, "t": 1001, "source": "vision", "kind": "draw", "object": "tube A", "detail": {}, "confidence": .99}
    log = R.coach(R.load_protocol(protocol), [detected, *loaded], 1000, 10, "test")
    timeline = Timeline([s["text"] for s in R.load_protocol(protocol)["steps"]], [e for e in log if e["source"] == "coach"])
    assert [s.status for s in timeline.at(1).steps] == ["current", "pending", "pending"]
    assert timeline.at(4).steps[1].status == "current"
    final = timeline.at(8)
    assert [s.status for s in final.steps] == ["done", "done", "skipped"]
    assert [s.at for s in final.steps] == [2.5, 4.125, 7]
    assert all(s.how == "hand-labelled" for s in final.steps)
    assert len(final.warnings) == 2 and all("hand-labelled" in text for _, text in final.warnings)
    note = {"steps": [{"index": i} for i in range(3)]}
    R.mark_labelled(note, log)
    assert all(s["label"] == "hand-labelled" for s in note["steps"])
    with TestClient(S.create_app(*setup)) as reopened:
        assert reopened.get("/api/session").json()["labels"] == saved


@pytest.mark.parametrize("row", [
    {"s": -1, "kind": "step", "step": 0, "status": "done"},
    {"s": 11, "kind": "step", "step": 0, "status": "done"},
    {"s": True, "kind": "step", "step": 0, "status": "done"},
    {"s": "1", "kind": "step", "step": 0, "status": "done"},
    {"s": 1, "kind": "step", "step": -1, "status": "done"},
    {"s": 1, "kind": "step", "step": 3, "status": "done"},
    {"s": 1, "kind": "step", "step": True, "status": "done"},
    {"s": 1, "kind": "step", "step": 0, "status": "seen"},
    {"s": 1, "kind": "warning", "code": "other"},
    {"s": 1, "kind": "warning", "code": "reused_tip", "step": 5},
    {"s": 1, "kind": "draw", "object": "tube A"},
    None,
])
def test_invalid_save_does_not_touch_existing_file(client, setup, row):
    assert save(client, []).status_code == 200
    assert save(client, [row]).status_code == 422
    assert setup[2].read_text() == ""


def test_duplicate_steps_nan_and_missing_token(client, setup):
    row = {"s": 1, "kind": "step", "step": 0, "status": "done"}
    assert save(client, [row, row]).status_code == 422
    assert client.post("/api/save", json=[]).status_code == 403
    token = client.get("/api/session").json()["token"]
    assert client.post("/api/save", content='[{"s": NaN}]', headers={"X-Label-Token": token}).status_code == 422
    assert client.post("/api/save", content="{", headers={"X-Label-Token": token}).status_code == 422
    assert client.get("/", headers={"Host": "external.example"}).status_code == 400
    assert not setup[2].exists()


def test_atomic_save_failure_preserves_file(tmp_path, monkeypatch):
    out = tmp_path / "labels.jsonl"
    out.write_text("old\n")
    def fail(*args):
        raise OSError("disk failure")
    monkeypatch.setattr(S.os, "replace", fail)
    with pytest.raises(OSError):
        S.atomic_save(out, [{"s": 1}])
    assert out.read_text() == "old\n"
    assert list(tmp_path.iterdir()) == [out]


def test_output_cannot_overwrite_inputs(setup):
    video, protocol, _, info = setup
    for out in (video, protocol):
        with pytest.raises(ValueError, match="Output must differ"):
            S.create_app(video, protocol, out, info)


def test_legacy_action_labels_still_load_but_editor_refuses_to_overwrite(setup):
    _, protocol, out, _ = setup
    out.write_text('# legacy labels\n{"s": 2, "kind": "tip_on", "object": "pipette"}\n')
    loaded = R.labelled_events(out, 1000)
    assert loaded[0]["source"] == "vision"
    log = R.coach(R.load_protocol(protocol), loaded, 1000, 10, "legacy")
    assert any(e["kind"] == "step" and e["detail"].get("status") == "done" for e in log)
    with pytest.raises(ValueError, match="legacy"):
        S.create_app(*setup)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_mov_is_converted_silently_and_cleaned_up(tmp_path):
    video = tmp_path / "phone.mov"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=24",
                    "-t", "1", "-c:v", "libx264", str(video)], check=True, timeout=30)
    original = video.read_bytes()
    with S.prepared_video(video) as (preview, info, converted):
        assert converted and preview != video and preview.is_file()
        assert info["codec"] == "h264" and info["fps"] == 24
        assert len(info["frames"]) == 24
        with S.prepared_video(preview) as (same, _, converted_again):
            assert same == preview and not converted_again
        streams = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(preview)]))["streams"]
        assert len(streams) == 1 and streams[0]["codec_type"] == "video"
    assert not preview.exists() and video.read_bytes() == original


def test_sample_protocol_when_available(tmp_path):
    root = os.environ.get("BENCH_SAMPLES")
    if not root:
        pytest.skip("BENCH_SAMPLES unset")
    protocol = Path(root) / "lsv_mv/CP-414-445-protocol.txt"
    if not protocol.is_file():
        pytest.skip("Sample protocol missing")
    data = R.load_protocol(protocol)
    rows = [{"s": t, "kind": "step", "step": i, "status": "done"} for i, t in enumerate([6.5, 15.9, 23.1, 25.2])]
    out = tmp_path / "labels.jsonl"
    S.atomic_save(out, S.validate_labels(rows, data["steps"], 31))
    log = R.coach(data, R.labelled_events(out, 1000), 1000, 31, "sample")
    done = [e for e in log if e["kind"] == "step" and e["detail"].get("status") == "done"]
    assert len(done) == 4 and [e["s"] for e in done] == [6.5, 15.9, 23.1, 25.2]


def test_explicit_label_times_and_attribution_survive_note_builder(setup, tmp_path, monkeypatch):
    from note import service
    from note.build import build_note
    from note.frames import attach_offsets
    from note.runfolder import load_run

    _, protocol, out, _ = setup
    rows = [{"s": 4.5, "kind": "step", "step": 0, "status": "done"},
            {"s": 6, "kind": "step", "step": 1, "status": "skipped"}]
    S.atomic_save(out, S.validate_labels(rows, R.load_protocol(protocol)["steps"], 10))
    detected = {"t": 1002, "s": 2, "source": "vision", "kind": "tip_on", "object": None,
                "step": None, "confidence": .9, "detail": {}}
    log = R.coach(R.load_protocol(protocol), [detected, *R.labelled_events(out, 1000)], 1000, 10, "note-test")
    # Use the real note builder without a hosted wording pass or video decoder.
    def make(folder, **kwargs):
        run = load_run(folder)
        note = build_note(run)
        attach_offsets(note, run)
        return {"note": note}
    monkeypatch.setattr(service, "_make_note", make)
    note = R.build_note(tmp_path / "work", "note-test", log, [])
    assert note is not None
    assert note["steps"][0]["t"] == 1004.5
    assert [s["label"] for s in note["steps"][:2]] == ["hand-labelled", "hand-labelled"]
    saved = json.loads((tmp_path / "work/note-test/note.json").read_text())
    assert saved["steps"][0]["at"] == 4.5
    assert "hand-labelled" in (tmp_path / "work/note-test/note.md").read_text()
    assert "hand-labelled" in (tmp_path / "work/note-test/note.html").read_text()


def test_protocol_mismatch_is_not_silently_relabelled(client):
    response = save(client, [{"s": 1, "kind": "step", "step": 0, "status": "done", "text": "Different protocol"}])
    assert response.status_code == 422


def test_warning_only_file_reaches_renderer(client, setup):
    assert save(client, [{"s": 2, "kind": "warning", "code": "reused_tip", "step": None}]).status_code == 200
    events = R.labelled_events(setup[2], 1000)
    log = R.coach(R.load_protocol(setup[1]), events, 1000, 10, "warning-only")
    warnings = [e for e in log if e["kind"] == "warning"]
    assert len(warnings) == 1 and warnings[0]["s"] == 2 and warnings[0]["source"] == "coach"


@pytest.mark.parametrize("time", ["NaN", "Infinity", "-1"])
def test_renderer_rejects_invalid_label_times(tmp_path, time):
    path = tmp_path / "labels.jsonl"
    path.write_text('{"s": ' + time + ', "kind": "tip_on"}')
    with pytest.raises(ValueError, match="finite"):
        R.labelled_events(path, 1000)


def test_short_render_does_not_include_future_manual_outcomes(client, setup):
    save(client, [{"s": 9, "kind": "step", "step": 0, "status": "done"}])
    log = R.coach(R.load_protocol(setup[1]), R.labelled_events(setup[2], 1000), 1000, 5, "short")
    assert all(e["s"] <= 5 for e in log)
    assert not any(e["kind"] == "step" and e["detail"].get("status") == "done" for e in log)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_label_file_renders_to_h264_with_saved_manual_note(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=30",
                    "-t", "1", "-c:v", "libx264", str(video)], check=True, timeout=30)
    protocol = tmp_path / "protocol.txt"
    protocol.write_text("1. Open the bottle\n2. Close the bottle\n")
    labels = tmp_path / "labels.jsonl"
    S.atomic_save(labels, S.validate_labels([
        {"s": .2, "kind": "step", "step": 0, "status": "done"},
        {"s": .7, "kind": "step", "step": 1, "status": "skipped"},
        {"s": .7, "kind": "warning", "step": 1, "code": "skipped_step"},
    ], R.load_protocol(protocol)["steps"], 1))
    # Exercise real loading, coaching, note creation, drawing and encoding; no model run.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(R, "analyse", lambda *args, **kwargs: {
        "t0": 1000, "detector": "none", "events": [], "snaps": [[]], "hands": [[]]})
    output = tmp_path / "render.mp4"
    R.main(["--video", str(video), "--protocol", str(protocol), "--labels", str(labels), "--out", str(output),
            "--codec", "x264", "--title-seconds", "0", "--note-seconds", "0.2"])
    assert S.probe(output)["codec"] == "h264"
    saved = json.loads((tmp_path / "render-work/render-render/note.json").read_text())
    assert [s["label"] for s in saved["steps"]] == ["hand-labelled", "hand-labelled"]
    assert [s["at"] for s in saved["steps"]] == [.2, .7]
