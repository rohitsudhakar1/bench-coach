from coach.tests.helpers import T0, demo_brain, emitted, ev, run, said


def hint(kind, t, obj=None, conf=0.8):
    e = ev(kind, t, obj, conf=conf, cam=0)
    e["detail"]["hint"] = True
    return e


def on_draw_step(**kw):
    b = demo_brain(**kw)
    run(b, [ev("tip_on", 1)])
    return b


def test_hints_never_tick_steps_without_hint_mode():
    b = on_draw_step()
    outs = run(b, [hint("draw", 5, "tube A", conf=0.95), hint("dispense", 9, "tube B", conf=0.95)])
    assert b.current == 1 and outs == []
    # nor do they count for tips or "did I add" answers
    assert b.tip.drawn == set()
    assert said(b.hear("did I add tube A", t=T0 + 10)) == ["No, nothing with tube A yet."]


def test_hint_asks_and_yes_ticks_as_seen_plus_said():
    b = on_draw_step(hints=True)
    outs = run(b, [hint("draw", 5, "tube")])
    assert said(outs) == ["Did you just draw from tube A?"]
    assert emitted(outs, "step")[0]["detail"]["question"] == "Did you just draw from tube A?"
    assert b.current == 1
    outs = b.hear("Yeah.", t=T0 + 7)
    step = emitted(outs, "step")[0]["detail"]
    assert step["status"] == "done" and step["evidence"] == "hint+speech" and step["source"] == "seen+said"
    assert step["question"] is None
    assert b.current == 2


def test_no_leaves_the_step_pending():
    b = on_draw_step(hints=True)
    run(b, [hint("draw", 5)])
    outs = b.hear("No, not yet", t=T0 + 6)
    assert said(outs) == ["Okay. Still on step 2."]
    assert b.current == 1 and b.question is None


def test_silence_closes_the_question_after_ten_seconds():
    b = on_draw_step(hints=True)
    run(b, [hint("draw", 5)])
    assert b.tick(T0 + 14.9) == [] and b.question is not None
    outs = b.tick(T0 + 15.0)
    assert b.question is None and emitted(outs, "step")[0]["detail"]["question"] is None
    assert b.current == 1


def test_reask_after_fifteen_seconds_up_to_three_times():
    b = on_draw_step(hints=True)
    run(b, [hint("draw", 5)])
    b.hear("no", t=T0 + 6)                                   # closed at 6
    assert said(run(b, [hint("draw", 20)])) == []            # 14 s after the no: too soon, dropped
    assert said(run(b, [hint("draw", 21)])) == ["Did you just draw from tube A?"]
    b.tick(T0 + 31)                                          # silence: closes at 31
    assert said(run(b, [hint("draw", 45)])) == []            # too soon
    lines = said(run(b, [hint("draw", 46)]))
    assert lines == ["Say done when step 2 is finished.", "Did you just draw from tube A?"]
    b.hear("nope", t=T0 + 47)
    assert said(run(b, [hint("draw", 70)])) == []            # three questions used up
    assert b.asked[1] == 3


def test_hint_for_a_different_tube_or_kind_is_dropped():
    b = on_draw_step(hints=True)
    assert run(b, [hint("draw", 5, "tube C")]) == []
    assert run(b, [hint("dispense", 6)]) == []
    assert b.question is None and b.asked == {}


def test_other_speech_keeps_the_question_open():
    b = on_draw_step(hints=True)
    run(b, [hint("draw", 5)])
    outs = b.hear("how much do I add", t=T0 + 6)
    assert said(outs) == ["100 microlitres of water from tube A."]
    assert b.question is not None
    b.hear("I drew from tube A", t=T0 + 7)
    assert b.status[1] == "done" and b.evidence[1] == "hint+speech"


def test_a_real_event_closes_the_question():
    b = on_draw_step(hints=True)
    run(b, [hint("draw", 5)])
    run(b, [ev("draw", 6, "tube A")])
    assert b.question is None and b.status[1] == "done" and b.evidence[1] == "vision"


def test_reminder_after_45_seconds_once():
    b = demo_brain(hints=True)          # step 1 started at T0
    assert said(b.tick(T0 + 44)) == []
    assert said(b.tick(T0 + 45)) == ["Say done when step 1 is finished."]
    assert said(b.tick(T0 + 90)) == []
    assert b.current == 0


def test_no_reminder_without_hint_mode():
    b = demo_brain()
    assert said(b.tick(T0 + 100)) == []


def test_eight_second_gap_between_questions():
    b = on_draw_step(hints=True)
    run(b, [hint("draw", 5)])
    b.hear("yes", t=T0 + 6)              # step 2 done, now on dispense
    assert said(run(b, [hint("dispense", 10)])) == []      # 5 s after the last question
    assert said(run(b, [hint("dispense", 13)])) == ["Did you just dispense into tube B?"]
