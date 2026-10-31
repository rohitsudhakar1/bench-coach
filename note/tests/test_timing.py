from note import fakerun
from note.frames import attach_offsets, save_key_frames
from note.build import build_note
from note.runfolder import Video, load_run

T0 = fakerun.T0


def test_offsets_follow_capture_frame_times(fake_run):
    run = load_run(fake_run)
    cams = {v.cam: v for v in run.videos}
    assert set(cams) == {0, 1} and all(v.frame_times for v in run.videos)
    assert cams[0].offset(T0 + 14.0) == 13.7
    # Camera 1 starts 0.5 s later and dropped frames 100-149: file frame 100 is at T0+15.8.
    assert cams[1].offset(T0 + 5.0) == 4.2
    assert cams[1].offset(T0 + 14.0) == 10.0
    assert cams[1].offset(T0 + 20.0) == 14.2
    # Past the end of the file: held at the last frame.
    assert cams[0].offset(T0 + 500) == cams[0].offset(T0 + 60.3)


def test_offset_without_timing_uses_the_start():
    v = Video(name="x.mp4", path=None, cam=None, start=100.0, fps=30.0, frames=300)
    assert v.offset(105.0) == 5.0 and v.offset(90.0) == 0.0 and v.offset(200.0) < 10.0
    assert Video(name="y", path=None, cam=None, start=None).offset(5.0) is None


def test_key_frames_from_both_cameras(fake_run):
    run = load_run(fake_run)
    note = build_note(run)
    attach_offsets(note, run)
    saved = save_key_frames(note, run, fake_run / "note_frames")
    with_frames = [p for p in note["problems"] if p["frames"]]
    assert saved == 6 and len(with_frames) == 3
    assert [p["kind"] for p in with_frames] == ["angle", "tip", "skipped"]
    for p in with_frames:
        assert sorted(f["video"] for f in p["frames"]) == ["cam0.mp4", "cam1.mp4"]
        for f in p["frames"]:
            assert (fake_run / f["file"]).stat().st_size > 1000


def test_loose_sources_start_at_the_run_start(tmp_path, fake_run):
    import shutil
    loose = tmp_path / "loose"
    loose.mkdir()
    shutil.copy(fake_run / "events.jsonl", loose / "events.jsonl")
    shutil.copy(fake_run / "cam0.mp4", tmp_path / "a.mp4")
    run = load_run(loose, sources=[str(tmp_path / "a.mp4")])
    assert len(run.videos) == 1 and run.videos[0].cam is None
    assert run.videos[0].offset(T0 + 12.0) == 12.0
