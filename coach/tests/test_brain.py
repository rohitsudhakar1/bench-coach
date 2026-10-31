from coach.tests.helpers import demo_brain, emitted, ev, run, said, sample_events

CLEAN = [ev("tip_on", 5), ev("draw", 10, "tube A"), ev("dispense", 15, "tube B"), ev("tip_off", 20),
         ev("tip_on", 25), ev("draw", 30, "tube C"), ev("dispense", 35, "tube B"), ev("tip_off", 40)]


def test_clean_run_ticks_every_step_and_praises():
    b = demo_brain()
    outs = run(b, CLEAN)
    assert b.status == ["done"] * 8
    assert not emitted(outs, "warning")
    assert said(outs)[-1] == "All steps done. Nice work."
    assert all(s["source"] == "seen" for s in b.state()["steps"])


def test_scripted_demo_mistakes():
    b = demo_brain()
    events = [e if e["source"] != "coach" else {**e, "source": "speech"} for e in sample_events()]
    outs = run(b, events)
    assert b.status == ["done", "done", "done", "skipped", "skipped", "done", "skipped", "done"]
    warnings = [w["detail"]["text"] for w in emitted(outs, "warning")]
    assert any("tilted about 27 degrees" in w for w in warnings)
    assert any(w.startswith("Same tip used twice") for w in warnings)
    assert "You skipped step 7: dispense it into tube B." in warnings
    lines = said(outs)
    assert "Nice work" not in " ".join(lines)
    assert lines[-1] == "Run finished. Check steps 4, 5 and 7."
    # the reused-tip warning is said once; the skip of tip steps 4-5 is posted but not said again
    assert "You skipped steps 4 and 5." in warnings and "You skipped steps 4 and 5." not in lines


def test_never_praises_an_unsure_step():
    b = demo_brain()
    events = list(CLEAN)
    events[2] = ev("dispense", 15, "tube B", conf=0.3)
    outs = run(b, events)
    assert b.status[2] == "unsure"
    assert said(outs)[-1] == "Run finished. Check step 3."


def test_weak_evidence_for_a_later_step_is_ignored():
    b = demo_brain()
    run(b, [ev("tip_on", 5), ev("dispense", 8, "tube B", conf=0.3)])
    assert b.current == 1 and b.status[2] == "pending"


def test_bare_tube_from_vision_counts_as_unsure():
    b = demo_brain()
    run(b, [ev("tip_on", 5), ev("draw", 10, "tube")])
    assert b.status[1] == "unsure" and b.current == 2
    # a named repeat later upgrades it
    run(b, [ev("draw", 12, "tube A")])
    assert b.status[1] == "done"


def test_repeat_of_last_step_is_not_a_skip():
    b = demo_brain()
    outs = run(b, CLEAN[:7] + [ev("dispense", 36, "tube B"), ev("dispense", 37, "tube B")])
    assert "skipped" not in b.status
    assert not emitted(outs, "warning")


def test_drawing_with_the_tip_ejected_warns():
    b = demo_brain()
    outs = run(b, CLEAN[:4] + [ev("draw", 30, "tube C")])
    assert "Put on a fresh tip before drawing." in said(outs)


def test_angle_warning_has_a_cooldown_and_respects_the_step_limit():
    b = demo_brain()
    run(b, [ev("tip_on", 5)])
    outs = run(b, [ev("angle", 6, source="angle", deg=25.0, ok=True, limit=30)])
    assert said(outs) == ["Pipette looks tilted about 25 degrees. Keep it under 20."]
    outs = run(b, [ev("angle", 8, source="angle", deg=26.0, ok=False, limit=20)])
    assert said(outs) == []                       # inside the 8 s cooldown
    outs = run(b, [ev("angle", 9, source="angle", deg=12.0, ok=True, limit=20)])
    assert said(outs) == []


def test_warning_from_the_angle_part_is_said_word_for_word_and_not_reposted():
    b = demo_brain()
    text = "Rough camera estimate: pipette tilted 24 degrees."
    outs = b.handle(ev("warning", 3, source="angle", text=text))
    assert said(outs) == [text] and not emitted(outs, "warning")


def test_too_fast_step_warns(tmp_path):
    from coach import protocol
    from coach.brain import Brain
    p = tmp_path / "p.yaml"
    p.write_text("name: t\nsteps:\n  - text: Mix for ten seconds.\n    expect: [{kind: dispense}]\n    min_seconds: 10\n")
    b = Brain(protocol.load(p), clock=lambda: 0.0)
    b.start()
    outs = b.handle({"t": 2.0, "source": "vision", "kind": "dispense", "confidence": 0.9})
    assert "That was quick. Check step 1." in said(outs)


def test_run_start_resets_and_does_not_repeat_the_first_line():
    b = demo_brain()
    run(b, CLEAN[:3])
    outs = b.handle(ev("run", 50, source="app", status="start", run="r2", protocol="pipetting-demo.yaml"))
    assert b.current == 0 and b.status[0] == "current"
    assert said(outs) == ["First: put a fresh tip."]
    b2 = demo_brain()
    outs = b2.handle(ev("run", 1, source="app", status="start", run="r", protocol="pipetting-demo.yaml"))
    assert said(outs) == [] and emitted(outs, "step")


def test_own_events_are_ignored():
    b = demo_brain()
    assert b.handle({**ev("tip_on", 5), "source": "coach"}) == []
    assert b.current == 0


def test_step_events_follow_the_contract():
    b = demo_brain()
    outs = run(b, [ev("tip_on", 5)])
    for e in emitted(outs):
        assert set(e) == {"t", "source", "kind", "object", "hand", "step", "confidence", "detail"}
        assert e["source"] == "coach"
    done = emitted(outs, "step")[0]["detail"]
    assert done == {"index": 0, "status": "done", "text": "Put a fresh tip on the pipette.",
                    "evidence": "vision", "source": "seen"}


def test_every_line_is_short():
    b = demo_brain()
    events = [e if e["source"] != "coach" else {**e, "source": "speech"} for e in sample_events()]
    for line in said(run(b, events)):
        assert len(line.split()) < 12, line
