"""Listening: audio in, sentences out.

Audio comes from capture's `WS /mic` (a JSON header `{t, n}` then 16 kHz mono int16 PCM), the
laptop mic (sounddevice, 20 ms blocks like capture), or a file decoded by ffmpeg. Every source
goes through the same loudness gate, which cuts the stream into utterances. Each utterance goes to
faster-whisper small.en on the CPU, and pieces Whisper is unsure about are dropped.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import numpy as np

RATE = 16000
MIC_BLOCK = 320   # 20 ms, the same pieces capture's /mic sends


class Gate:
    """Cuts 16 kHz audio into utterances by loudness.

    It tracks the background level all the time (falls fast, rises over a few seconds). A sound
    opens the gate when it is `start_ratio` times the background, above `min_level`, for `onset`
    seconds in a row. It closes after `end_silence` seconds of quiet, judged on a 150 ms
    average: quiet means near the background, or under `end_drop` of the speech's own level.
    That second rule stops bench clatter right after a question from holding the gate open
    (it held "What's next?" open for 3 s; now 0.4 s).

    The room: in the first `room_time` seconds it notes how loud the room is (90th percentile of
    the quiet frames). After that a sound must also be `room_margin` times that level, so chatter
    in a loud room that pauses and starts again does not open it.

    Audio is judged in whole 30 ms frames whatever size the pieces come in; leftover samples wait
    for the next piece. (The earlier draft let a 10 ms scrap count as a full frame, which opened
    the gate early on bench clatter when the laptop mic sent 100 ms blocks.)
    """

    FRAME = 480   # samples, 30 ms

    def __init__(self, rate=RATE, start_ratio=4.0, end_ratio=2.0, min_level=0.03, onset=0.12,
                 end_silence=0.6, min_voiced=0.25, max_len=8.0, warmup=0.3, preroll=0.15,
                 room_time=2.0, room_margin=2.5, end_drop=0.4):
        self.rate = rate
        self.start_ratio, self.end_ratio = start_ratio, end_ratio
        self.min_level, self.onset = min_level, onset
        self.end_silence, self.min_voiced, self.max_len = end_silence, min_voiced, max_len
        self.warmup, self.room_time, self.room_margin = warmup, room_time, room_margin
        self.end_drop = end_drop
        self.pre_frames = max(1, round(preroll / (self.FRAME / rate)))
        self.floor = None
        self.heard = 0.0
        self.room_levels: list[float] = []
        self.room = None
        self.rest = np.zeros(0, np.float32)
        self.rest_t = 0.0
        self.pre: list[np.ndarray] = []
        self.clear()

    def clear(self):
        """Drop any utterance in progress (used while the coach itself is speaking)."""
        self.buf: list[np.ndarray] = []
        self.t0 = None
        self.voiced = 0.0
        self.quiet_for = 0.0
        self.run: list[tuple[np.ndarray, float]] = []
        self.levels: list[float] = []

    def room_bar(self) -> float:
        if self.room is not None:
            return self.room_margin * self.room
        if len(self.room_levels) < 5:
            return 0.0
        return self.room_margin * float(np.percentile(self.room_levels, 90))

    def _follow(self, level, inside):
        if self.floor is None:
            self.floor = level
        elif level < self.floor:
            self.floor += 0.3 * (level - self.floor)
        else:
            self.floor += (0.002 if inside else 0.008) * (level - self.floor)

    def feed(self, pcm: np.ndarray, t: float) -> list[tuple[np.ndarray, float]]:
        """Feed int16 or float audio stamped with the time of its first sample.
        Returns finished utterances as (float32 audio, start time)."""
        x = pcm.astype(np.float32)
        if pcm.dtype == np.int16:
            x /= 32768.0
        if len(self.rest):
            x, t = np.concatenate([self.rest, x]), self.rest_t
        whole = len(x) // self.FRAME * self.FRAME
        self.rest, self.rest_t = x[whole:], t + whole / self.rate
        dur = self.FRAME / self.rate
        out = []
        for k in range(0, whole, self.FRAME):
            f = x[k:k + self.FRAME]
            ft = t + k / self.rate
            level = float(np.sqrt(np.mean(f * f)))
            floor = level if self.floor is None else self.floor
            self.heard += dur
            if self.t0 is None:
                bar = max(self.min_level, floor * self.start_ratio, self.room_bar())
                loud = self.heard > self.warmup and level > bar
                self._follow(level, False)
                if not loud:
                    if self.room is None:
                        self.room_levels.append(level)
                        if self.heard >= self.room_time:
                            self.room = float(np.percentile(self.room_levels, 90))
                    self.pre = (self.pre + [g for g, _ in self.run] + [f])[-self.pre_frames:]
                    self.run = []
                    continue
                self.run.append((f, ft))
                if len(self.run) * dur + 1e-9 < self.onset:
                    continue
                self.t0 = self.run[0][1]
                self.buf = self.pre + [g for g, _ in self.run]
                self.levels = [float(np.sqrt(np.mean(g * g))) for g, _ in self.run]
                self.voiced = len(self.run) * dur
                self.run, self.pre, self.quiet_for = [], [], 0.0
                continue
            self.buf.append(f)
            self._follow(level, True)
            self.levels.append(level)
            # judge the end on a 150 ms average, so single clunks after the speech don't hold it open
            recent = float(np.mean(self.levels[-5:]))
            speech = float(np.percentile(self.levels, 90))
            if recent > max(self.min_level, floor * self.end_ratio, self.end_drop * speech):
                self.voiced += dur
                self.quiet_for = 0.0
            else:
                self.quiet_for += dur
            if self.quiet_for >= self.end_silence or len(self.buf) * dur >= self.max_len:
                if self.voiced >= self.min_voiced:
                    out.append((np.concatenate(self.buf), self.t0))
                self.clear()
        return out

    def flush(self) -> list[tuple[np.ndarray, float]]:
        """End of the stream: hand over an utterance still in progress."""
        out = []
        if self.t0 is not None and self.voiced >= self.min_voiced:
            out.append((np.concatenate(self.buf), self.t0))
        self.clear()
        return out


class Whisper:
    """faster-whisper small.en, int8 on the CPU.

    Drops pieces it is unsure about: no-speech score over 0.3 or average log-probability under
    -0.8. On bench audio, clatter and far-off talk scored about 0.4 and -0.9; a person asking a
    question near the mic scored under 0.1 and above -0.45.
    """

    MAX_NO_SPEECH = 0.3
    MIN_LOGPROB = -0.8
    NOISE_WORDS = {"", "you", "thank you", "thanks for watching", "bye", "okay"}

    def __init__(self, model="small.en"):
        self.name = model
        self.model = None

    def load(self):
        if self.model is None:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(self.name, device="cpu", compute_type="int8")
            warm = np.random.default_rng(0).normal(0, 0.01, RATE).astype(np.float32)
            list(self.model.transcribe(warm, language="en", beam_size=1, vad_filter=True)[0])  # pay first-call costs now
        return self

    def transcribe(self, audio: np.ndarray) -> str:
        self.load()
        segs, _ = self.model.transcribe(audio, language="en", beam_size=1, vad_filter=True,
                                        condition_on_previous_text=False)
        kept = [s.text for s in segs
                if s.no_speech_prob < self.MAX_NO_SPEECH and s.avg_logprob > self.MIN_LOGPROB]
        text = " ".join(" ".join(kept).split())
        return "" if text.lower().strip(" .!?,") in self.NOISE_WORDS else text


# ---------- audio sources: async generators of (int16 array, time of first sample) ----------

async def capture_chunks(url: str):
    """Capture's WS /mic. Reconnects when capture restarts."""
    import websockets
    warned = False
    while True:
        try:
            async with websockets.connect(url, max_size=None) as ws:
                print(f"coach: listening on {url}", file=sys.stderr)
                warned = False
                header = None
                async for msg in ws:
                    if isinstance(msg, str):
                        try:
                            header = json.loads(msg)
                        except ValueError:
                            header = None
                        continue
                    t = (header or {}).get("t") or time.time()
                    header = None
                    yield np.frombuffer(msg, dtype="<i2"), float(t)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not warned:
                print(f"coach: mic {url} not there ({type(e).__name__}), retrying", file=sys.stderr)
                warned = True
            await asyncio.sleep(2)


async def local_chunks(stream_factory=None, clock=time.time):
    """The laptop mic, in 20 ms blocks. `stream_factory` lets tests stand in for sounddevice."""
    if stream_factory is None:
        import sounddevice as sd
        stream_factory = sd.InputStream
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def cb(indata, frames, tinfo, status):
        item = None if indata is None else (indata[:, 0].copy(), clock() - frames / RATE)
        loop.call_soon_threadsafe(q.put_nowait, item)   # None ends the stream (test stand-ins)

    with stream_factory(samplerate=RATE, channels=1, dtype="int16", blocksize=MIC_BLOCK, callback=cb):
        print("coach: listening on the laptop mic", file=sys.stderr)
        while True:
            item = await q.get()
            if item is None:
                return
            yield item


async def file_chunks(path, realtime=True, start=None):
    """Decode a file's audio to 16 kHz mono with ffmpeg and hand it over in 20 ms pieces.
    Nothing is played."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-f", "s16le",
        "-acodec", "pcm_s16le", "-ar", str(RATE), "-ac", "1", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    t0 = time.time() if start is None else start
    m0 = time.monotonic()
    n = 0
    try:
        while True:
            try:
                data = await proc.stdout.readexactly(MIC_BLOCK * 2)
            except asyncio.IncompleteReadError as e:
                data = e.partial[: len(e.partial) // 2 * 2]
                if not data:
                    break
            if realtime:
                await asyncio.sleep(max(0.0, m0 + n / RATE - time.monotonic()))
            yield np.frombuffer(data, dtype="<i2"), t0 + n / RATE
            n += len(data) // 2
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def listen(chunks, stt, on_text, muted=lambda: False, gate: Gate | None = None):
    """Cut audio into utterances, transcribe each and await on_text(text, start_time)."""
    gate = gate or Gate()
    await asyncio.to_thread(stt.load)

    async def say(found):
        for audio, t0 in found:
            try:
                text = await asyncio.to_thread(stt.transcribe, audio)
            except Exception as e:
                print(f"coach: could not transcribe ({type(e).__name__})", file=sys.stderr)
                continue
            if text:
                await on_text(text, t0)

    async for pcm, t in chunks:
        if muted():
            gate.clear()   # never transcribe our own voice
            continue
        await say(gate.feed(pcm, t))
    await say(gate.flush())
