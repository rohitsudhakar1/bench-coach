"""The four spoken questions over bench noise, through a fake mic, Whisper and the coach.

Skipped when the faster-whisper small.en weights are not on disk. No microphone is opened and
nothing is played: a stand-in for sounddevice feeds the session, or a little websocket server
on a test port plays the part of capture's /mic.
"""
import asyncio
import json
import os
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from coach import listen as L
from coach.tests.helpers import demo_brain, said
from coach.tests.voice_session import RATE, session


def whisper_cached() -> bool:
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    return (hub / "models--Systran--faster-whisper-small.en").is_dir()


pytestmark = pytest.mark.skipif(not whisper_cached(), reason="Whisper small.en not on disk")

# what each question must come back as, and what the coach must answer (on step 1 of the demo)
EXPECT = {
    "whats_next": ("what's next", "Now: put a fresh tip."),
    "how_much": ("how much do i add", "100 microlitres of water from tube A."),
    "did_i_add_tube_c": ("did i add tube c", "No, nothing with tube C yet."),
    "long_noisy": ("fresh tip", "Next: draw 100 microlitres of water from tube A."),
}

_stt = None


def stt():
    global _stt
    if _stt is None:
        _stt = L.Whisper("small.en").load()
    return _stt


class FakeStream:
    """Stands in for sounddevice.InputStream: calls back with 20 ms blocks of a recording."""

    def __init__(self, pcm, pace=0.0):
        self.pcm, self.pace, self.n = pcm, pace, 0

    def clock(self):
        return 1000.0 + self.n / RATE

    def __call__(self, samplerate, channels, dtype, blocksize, callback):
        assert (samplerate, channels, dtype) == (RATE, 1, "int16")
        self.block, self.cb = blocksize, callback
        return self

    def __enter__(self):
        def run():
            for i in range(0, len(self.pcm), self.block):
                b = self.pcm[i:i + self.block]
                self.n = i + len(b)
                self.cb(b.reshape(-1, 1), len(b), None, None)
                if self.pace:
                    time.sleep(self.pace)
            self.cb(None, 0, None, None)
        threading.Thread(target=run, daemon=True).start()
        return self

    def __exit__(self, *a):
        return False


def check(heard, marks, answers):
    assert len(heard) == 4, heard
    for (text, t0), (name, _q, start, _end), ans in zip(heard, marks, answers):
        want, reply = EXPECT[name]
        clean = text.lower().replace("?", "").replace(",", "").replace(".", "")
        assert want in clean, (name, text)
        assert abs(t0 - start) < 0.4, (name, t0, start)
        assert ans == reply, (name, ans)


async def run_coach(chunks, brain_fresh=True):
    heard, answers = [], []

    async def on_text(text, t):
        heard.append((text, t))
        answers.append(said(demo_brain().hear(text, t=t))[-1])

    await L.listen(chunks, stt(), on_text)
    return heard, answers


def test_fake_laptop_mic():
    pcm, marks = session()
    fake = FakeStream(pcm)
    heard, answers = asyncio.run(run_coach(L.local_chunks(stream_factory=fake, clock=fake.clock)))
    heard = [(t, s - 1000.0) for t, s in heard]
    check(heard, marks, answers)


def test_fake_capture_mic_in_real_time_answers_within_three_seconds():
    """capture's WS /mic, header then 20 ms PCM, paced like a real mic; times the answers."""
    import websockets
    pcm, marks = session()
    port = 8847
    start = {}

    async def serve(ws):
        start["t"] = time.time()
        t0 = time.monotonic()
        for k, i in enumerate(range(0, len(pcm), 320)):
            b = pcm[i:i + 320]
            await ws.send(json.dumps({"t": start["t"] + i / RATE, "n": len(b)}))
            await ws.send(b.tobytes())
            await asyncio.sleep(max(0.0, t0 + (i + 320) / RATE - time.monotonic()))
        await ws.close()

    async def main():
        stt()
        heard, answers, delays = [], [], []

        async def on_text(text, t):
            heard.append((text, t - start["t"]))
            answers.append(said(demo_brain().hear(text, t=t))[-1])
            delays.append(time.time() - start["t"])

        async with websockets.serve(serve, "127.0.0.1", port):
            chunks = L.capture_chunks(f"ws://127.0.0.1:{port}")
            # capture_chunks reconnects forever; stop once the server has closed the stream
            async def bounded():
                end = len(pcm) / RATE
                async for item in chunks:
                    yield item
                    if item[1] - start["t"] >= end - 0.03:
                        return
            await L.listen(bounded(), stt(), on_text)
        return heard, answers, delays

    heard, answers, delays = asyncio.run(main())
    check(heard, marks, answers)
    lags = [d - end for d, (_n, _q, _s, end) in zip(delays, marks)]
    print("answer delay after the question ended:", [round(x, 2) for x in lags])
    assert all(x < 3.0 for x in lags), lags
