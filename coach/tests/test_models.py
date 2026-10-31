"""Claude and Gemini Live, with stand-in clients. No hosted model is called."""
import asyncio
import types as pytypes

import numpy as np

from coach import gemini_live, llm
from coach.speaker import Speaker
from coach.tests.helpers import T0, demo_brain, said


class FakeClaude:
    def __init__(self, reply="Draw from tube A now.", fail=False):
        self.reply, self.fail, self.calls = reply, fail, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise TimeoutError("slow")
        return pytypes.SimpleNamespace(content=[pytypes.SimpleNamespace(text=self.reply)])


def test_no_key_means_no_claude(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.make_answerer() is None


def test_claude_words_the_answer_with_the_protocol_and_rule_answer():
    fake = FakeClaude()
    b = demo_brain(answerer=llm.make_answerer(client=fake))
    assert said(b.hear("what's next?", t=T0 + 1)) == ["Draw from tube A now."]
    call = fake.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    text = call["messages"][0]["content"]
    assert "Put a fresh tip on the pipette." in text and "Suggested answer: Now: put a fresh tip." in text


def test_claude_failure_falls_back_to_the_rule_answer():
    b = demo_brain(answerer=llm.make_answerer(client=FakeClaude(fail=True)))
    assert said(b.hear("what's next?", t=T0 + 1)) == ["Now: put a fresh tip."]


def test_gemini_live_needs_a_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert gemini_live.available("gemini-live") is False
    assert gemini_live.available(None) is False


# ---------- Gemini Live conversation with a fake session ----------

def ns(**kw):
    return pytypes.SimpleNamespace(**kw)


def content(**kw):
    base = dict(input_transcription=None, output_transcription=None, model_turn=None,
                interrupted=False, turn_complete=False)
    base.update(kw)
    return ns(server_content=ns(**base))


class FakeSession:
    def __init__(self):
        self.audio = 0
        self.context = []
        self.sent_end = False

    async def send_client_content(self, turns, turn_complete):
        self.context.append(turns["parts"][0]["text"])

    async def send_realtime_input(self, audio=None, audio_stream_end=None):
        if audio is not None:
            self.audio += 1
        if audio_stream_end:
            self.sent_end = True

    async def receive(self):
        while self.audio < 3:
            await asyncio.sleep(0.01)
        yield content(input_transcription=ns(text="tip's on, what's next?"))
        yield content(output_transcription=ns(text="Next, draw from tube A."),
                      model_turn=ns(parts=[ns(inline_data=ns(mime_type="audio/pcm;rate=24000",
                                                             data=b"\x00\x01" * 2400))]))
        yield content(turn_complete=True)
        while True:
            await asyncio.sleep(1)


class FakeTypes:
    @staticmethod
    def Blob(data, mime_type):
        return ns(data=data, mime_type=mime_type)


def test_gemini_live_talks_but_never_marks_steps(tmp_path):
    from coach.__main__ import Coach, Emitter
    brain = demo_brain()
    speaker = Speaker(backend="none", sync=True)
    coach = Coach(brain, speaker, Emitter(None))

    async def chunks():
        for i in range(5):
            yield np.zeros(320, np.int16), T0 + i * 0.02
            await asyncio.sleep(0.02)

    session = FakeSession()
    asyncio.run(gemini_live.conversation(session, chunks(), coach, FakeTypes, audio_dir=tmp_path, drain=2))
    assert speaker.spoken == ["Next, draw from tube A."]
    assert brain.status[0] == "current"                       # "tip's on" did not tick anything
    assert brain.log[-1]["kind"] == "speech" and "tip's on" in brain.log[-1]["detail"]["text"]
    assert session.context and session.context[0].startswith("BENCH_STATE")
    assert len(list(tmp_path.glob("gemini-*.wav"))) == 1
    assert session.sent_end
