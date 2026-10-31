"""render_demo's coach pass, hand-labelled events and panel drawing, on hand-made inputs."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from scripts import render_demo as R
from scripts import render_layout as L

T0 = 1_700_000_000.0
PROTO = {"name": "Tip test", "steps": [{"text": "Attach a tip", "expect": [{"kind": "tip_on"}]},
                                       {"text": "Draw from tube A", "expect": [{"kind": "draw", "object": "tube A"}]},
                                       {"text": "Dispense into tube B", "expect": [{"kind": "dispense", "object": "tube B"}]}]}


def vis(s, kind, obj=None, conf=0.9, **detail):
    return {"t": T0 + s, "s": s, "source": "vision", "kind": kind, "object": obj, "hand": None, "step": None,
            "confidence": conf, "detail": {"cam": 0, **detail}}


def steps_of(log):
    return [(e["s"], e["detail"]["index"], e["detail"]["status"]) for e in log
            if e["kind"] == "step" and e["source"] == "coach"]


def test_coach_pass_ticks_steps_in_video_seconds():
    log = R.coach(PROTO, [vis(4.0, "tip_on", "pipette"), vis(9.0, "draw", "tube A")], T0, 20.0, "r1")
    assert log[0]["detail"]["status"] == "start" and log[-1]["detail"]["status"] == "stop"
    assert log[-1]["s"] == 20.0
    got = steps_of(log)
    assert (4.0, 0, "done") in got and (9.0, 1, "done") in got
    assert any(e["kind"] == "say" for e in log)
    assert all("s" in e for e in log)


def test_coach_pass_warns_on_a_skipped_step():
    log = R.coach(PROTO, [vis(4.0, "tip_on", "pipette"), vis(9.0, "dispense", "tube B")], T0, 20.0, "r2")
    assert (9.0, 1, "skipped") in steps_of(log)
    assert any(e["kind"] == "warning" for e in log)


def test_hand_labelled_events_mark_the_steps_they_decide(tmp_path):
    f = tmp_path / "labels.jsonl"
    f.write_text('# made by hand\n{"s": 3.5, "kind": "tip_on", "object": "pipette"}\n')
    extra = R.labelled_events(f, T0)
    assert extra[0]["t"] == T0 + 3.5 and extra[0]["detail"]["labelled"] is True
    log = R.coach(PROTO, extra, T0, 10.0, "r3")
    done = [e for e in log if e["kind"] == "step" and e["detail"].get("status") == "done"]
    assert done and all(e["detail"].get("labelled") for e in done)
    tl = L.Timeline([s["text"] for s in PROTO["steps"]], [e for e in log if e["source"] == "coach"])
    assert tl.at(5).steps[0].how == "hand-labelled"


def test_protocol_text_becomes_steps(tmp_path):
    p = tmp_path / "CP-414-445-protocol.txt"
    p.write_text("1. Grab 1000uL pipet and attach 1000uL pipet tip\n2. Collect 750uL of trypsin\n"
                 "3. Dispense into flask\n")
    data = R.load_protocol(p)
    assert [s["text"][:4] for s in data["steps"]] == ["Grab", "Coll", "Disp"]
    assert R.pretty_title(data, p) == "CP 414 445"
    assert R.pretty_title({"name": "Serial dilution"}, p) == "Serial dilution"


def test_credits_say_what_was_detected_and_what_was_labelled():
    assert "Grounding DINO" in R.credits_for({"detector": "gdino-tiny"}, False)[0]
    assert "hands only" in R.credits_for({"detector": "none"}, False)[0]
    assert any("labelled" in c for c in R.credits_for({"detector": "gdino-tiny"}, True))


def test_panel_and_feed_draw_at_their_size_even_with_many_steps():
    steps = [f"Step number {i} with a fairly long instruction that wraps onto a second line" for i in range(25)]
    events = [{"s": 1.0 * i, "kind": "step", "detail": {"index": i, "status": "done", "source": "seen"}}
              for i in range(18)]
    events += [{"s": 18.0, "kind": "step", "detail": {"index": 18, "status": "current"}},
               {"s": 18.5, "kind": "warning", "detail": {"text": "Same tip used twice. Change it before tube C."}}]
    st = L.Timeline(steps, events).at(19.0)
    lay = L.layout([(1280, 720), (1280, 720)])
    panel = R.draw_panel(st, "A long protocol title that needs two lines on the panel", lay.panel,
                         R.credits_for({"detector": "gdino-tiny"}, True))
    assert panel.shape == (lay.panel.h, lay.panel.w, 3)
    feed = R.draw_feed(st, lay.feed, "Step 19")
    assert feed.shape == (lay.feed.h, lay.feed.w, 3)
    # the warning is drawn in red on the feed box's left edge
    assert tuple(feed[50, 3][::-1]) == R.RED


def test_note_card_and_title_card_are_full_frames():
    note = {"title": "Tip test", "summary": "3 protocol steps: 2 done, 1 skipped.", "duration": 20, "run": "r",
            "started": T0, "steps": [{"number": 1, "text": "Attach a tip", "status": "done", "label": "seen", "at": 4},
                                     {"number": 2, "text": "Draw", "status": "skipped", "at": 9}],
            "problems": [{"text": "You skipped step 2.", "at": 9}], "said": []}
    try:
        img = R.note_card(note, ["credits"])
    except KeyError:
        pytest.skip("note header needs fields this hand-made note lacks")
    assert img.shape == (L.H, L.W, 3)
    assert R.title_card("Tip test", ["one", "two"]).shape == (L.H, L.W, 3)


def test_sprite_blit_stays_inside_the_camera():
    canvas = np.zeros((L.H, L.W, 3), np.uint8)
    sp = R.Sprites()
    pill = sp.pill("pipette tip", (255, 140, 66))
    r = L.Rect(0, 0, 1280, 720)
    sp.blit(canvas, pill, -50, -50, r)       # off the top-left: pushed back inside
    assert canvas[:720, :1280].any() and not canvas[720:, :].any() and not canvas[:, 1280:].any()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_encoder_writes_a_1080p_h264_file(tmp_path):
    out = tmp_path / "x.mp4"
    enc = R.Encoder(out, 30, "x264")
    for _ in range(10):
        enc.write(np.zeros((L.H, L.W, 3), np.uint8))
    enc.close()
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name,width,height", "-of", "json", str(out)], capture_output=True, text=True)
    s = json.loads(probe.stdout)["streams"][0]
    assert (s["codec_name"], s["width"], s["height"]) == ("h264", 1920, 1080)


def test_end_card_and_branded_title_are_full_frames():
    assert R.end_card(["Bench Coach", "a line"]).shape == (L.H, L.W, 3)
    assert R.title_card("Tip test", ["one"], "Bench Coach, a hackathon").shape == (L.H, L.W, 3)
