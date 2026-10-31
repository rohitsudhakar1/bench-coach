import json

from note import fakerun
from note.build import build_note, speech_reason
from note.runfolder import load_run

T0 = fakerun.T0


def note_for(folder):
    return build_note(load_run(folder))


def test_steps_follow_the_coach(fake_run):
    note = note_for(fake_run)
    assert [s["status"] for s in note["steps"]] == ["done", "done", "done", "skipped", "skipped",
                                                     "done", "skipped", "done"]
    assert [s["label"] for s in note["steps"]][:3] == ["seen", "seen", "seen+said"]
    # A done step is timed at the camera event that proved it, not when the coach caught up.
    assert note["steps"][0]["t"] == T0 + 6.0
    assert note["steps"][1]["t"] == T0 + 14.0
    # Confirmed by a yes after a hint: timed at the confirmation, never at the hint.
    assert note["steps"][2]["t"] == T0 + 25.2
    assert note["counts"]["done"] == 5 and note["counts"]["skipped"] == 3


def test_header(fake_run):
    note = note_for(fake_run)
    assert note["who"] == "Maya Lopez"
    assert note["title"] == "Pipetting demo"
    assert round(note["duration"]) == 60
    assert note["date"] and note["time"]


def test_events_after_the_stop_are_left_out(fake_run):
    run = load_run(fake_run)
    assert not any(e.get("object") == "smoke-tube" for e in run.events)


def test_angle_readings_go_to_the_step_in_progress(fake_run):
    note = note_for(fake_run)
    angle = [t for t in note["technique"] if t["kind"] == "angle"]
    assert [(a["step"], a["readings"], a["over"], a["deg"]) for a in angle] == [(1, 3, 2, 27.4), (6, 1, 0, 11.0)]
    assert angle[0]["limit"] == 20
    tilt = [p for p in note["problems"] if p["kind"] == "angle"]
    assert len(tilt) == 1 and tilt[0]["t"] == T0 + 12.0 and "27°" in tilt[0]["text"]


def test_tip_reuse_and_fresh_tip(fake_run):
    note = note_for(fake_run)
    tips = [t for t in note["technique"] if t["kind"] == "tip"]
    assert [t["fresh"] for t in tips] == [True, False]
    assert "tube C after tube A" in tips[1]["text"]


def test_coach_warnings_that_repeat_our_lines_are_dropped(fake_run):
    note = note_for(fake_run)
    kinds = [p["kind"] for p in note["problems"]]
    assert kinds == ["angle", "tip", "skipped", "skipped", "skipped"]


def test_other_coach_warnings_are_kept(tmp_path):
    folder = fakerun.make(tmp_path / "r")
    with open(folder / "events.jsonl", "a") as fh:
        fh.write(json.dumps(fakerun.ev(30.0, "coach", "warning", text="Bubbles in the tip.")) + "\n")
    note = note_for(folder)
    warn = [p for p in note["problems"] if p["kind"] == "warning"]
    assert len(warn) == 1 and "Bubbles in the tip." in warn[0]["text"] and warn[0]["step"] == 3


def test_speech_kept_only_when_it_matters(fake_run):
    note = note_for(fake_run)
    assert [s["kind"] for s in note["said"]] == ["volume", "lot"]
    assert speech_reason("what's next") is None
    assert speech_reason("spun it at 300 rpm for 5 minutes") == "amount"
    assert speech_reason("it turned blue") == "observation"


def test_hints_are_never_proof(tmp_path):
    folder = tmp_path / "r"
    folder.mkdir()
    proto = {"name": "p", "steps": [{"text": "draw A", "expect": [{"kind": "draw", "object": "tube A"}]},
                                    {"text": "draw C", "expect": [{"kind": "draw", "object": "tube C"}]}]}
    evs = [fakerun.ev(0, "app", "run", status="start", run="r", protocol_data=proto),
           fakerun.ev(1, "vision", "tip_on"),
           fakerun.ev(2, "vision", "draw", "tube A", hint=True),
           fakerun.ev(3, "vision", "draw", "tube C", hint=True),
           fakerun.ev(4, "app", "run", status="stop", run="r")]
    (folder / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in evs))
    note = note_for(folder)
    assert [s["status"] for s in note["steps"]] == ["not reached", "not reached"]
    assert not [t for t in note["technique"] if t["kind"] == "tip"]
    assert note["hints_not_used"] == 2


def test_no_coach_events_walks_the_evidence(tmp_path):
    folder = tmp_path / "r"
    folder.mkdir()
    evs = [fakerun.ev(0, "app", "run", status="start", run="r", protocol="protocols/pipetting-demo.yaml"),
           fakerun.ev(1, "vision", "tip_on"),
           fakerun.ev(2, "vision", "draw", "tube A", conf=0.9),
           fakerun.ev(3, "vision", "tip_off", conf=0.2),        # too weak to count
           fakerun.ev(4, "vision", "tip_off"),
           fakerun.ev(9, "app", "run", status="stop", run="r")]
    (folder / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in evs))
    note = note_for(folder)
    assert note["status_source"] == "evidence"
    assert [s["status"] for s in note["steps"]][:5] == ["done", "done", "skipped", "done", "not reached"]
    assert note["steps"][3]["t"] == T0 + 4
    stopped = [p for p in note["problems"] if p["kind"] == "stopped"]
    assert stopped and "steps 5 to 8" in stopped[0]["text"]


def test_picks_this_run_from_a_shared_log(tmp_path):
    folder = tmp_path / "second"
    folder.mkdir()
    proto = {"name": "p", "steps": [{"text": "tip", "expect": [{"kind": "tip_on"}]}]}
    evs = [fakerun.ev(0, "app", "run", status="start", run="first", protocol_data=proto),
           fakerun.ev(1, "vision", "tip_on"),
           fakerun.ev(2, "app", "run", status="stop", run="first"),
           fakerun.ev(10, "app", "run", status="start", run="second", protocol_data=proto),
           fakerun.ev(12, "app", "run", status="stop", run="second")]
    (folder / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in evs))
    note = note_for(folder)
    assert note["run"] == "second" and note["steps"][0]["status"] == "not reached"
    assert round(note["duration"]) == 2


def test_empty_folder_does_not_crash(tmp_path):
    note = note_for(tmp_path)
    assert note["steps"] == [] and "No protocol" in note["summary"]


def test_half_written_last_line_is_ignored(fake_run):
    with open(fake_run / "events.jsonl", "a") as fh:
        fh.write('{"t": 17934000')
    assert len(note_for(fake_run)["steps"]) == 8
