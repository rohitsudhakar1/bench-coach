"""Run the coach.

    python -m coach                                    # follow the hub, listen on capture's mic
    python -m coach --events coach/samples/demo-events.jsonl --no-hub   # replay, prints lines
    python -m coach --source clip.mp4 --no-hub         # listen to a file's sound track
    python -m coach --mic local                        # the laptop mic instead of capture's
    python -m coach --speak                            # out loud (off by default)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import queue
import signal
import sys
import threading
import time
from pathlib import Path

from . import protocol as protocol_mod
from .brain import Brain
from .speaker import Speaker


class Emitter:
    """Posts events to the hub from a thread. When the hub is off or down, prints JSON lines."""

    def __init__(self, hub: str | None):
        self.hub = hub.rstrip("/") if hub else None
        self.q: queue.Queue = queue.Queue()
        self.down_until = 0.0
        self.sent: list[dict] = []
        self.pending = 0
        self.lock = threading.Lock()
        threading.Thread(target=self._run, name="emitter", daemon=True).start()

    def send(self, ev: dict):
        with self.lock:
            self.sent.append(ev)
            self.pending += 1
        self.q.put(ev)

    def flush(self, timeout=3.0):
        end = time.monotonic() + timeout
        while self.pending and time.monotonic() < end:
            time.sleep(0.02)

    def _run(self):
        import httpx
        client = httpx.Client(timeout=1.0)
        while True:
            ev = self.q.get()
            posted = False
            if self.hub and time.monotonic() >= self.down_until:
                try:
                    client.post(f"{self.hub}/events", json=ev).raise_for_status()
                    posted = True
                except Exception:
                    self.down_until = time.monotonic() + 5
            if not posted:
                print(json.dumps(ev), flush=True)
            with self.lock:
                self.pending -= 1


class Coach:
    """Holds the brain, voice and emitter, and moves outputs between them."""

    def __init__(self, brain: Brain, speaker: Speaker, emitter: Emitter):
        self.brain, self.speaker, self.emitter = brain, speaker, emitter
        self.lock = threading.RLock()
        self.replaying = False
        speaker.on_said = self._said

    def _said(self, text):
        with self.lock:
            self.brain.said(text)
            step = None if self.brain.finished() else self.brain.current
            t = self.brain.now if self.replaying else time.time()
        self.emitter.send({"t": t, "source": "coach", "kind": "say", "object": None, "hand": None,
                           "step": step, "confidence": None, "detail": {"text": text}})

    def route(self, outs):
        for o in outs:
            if o.kind == "emit":
                self.emitter.send(o.event)
            else:
                self.speaker.submit(o.text, o.priority, o.topic)

    def call(self, fn, *a):
        with self.lock:
            outs = fn(*a)
        self.route(outs)

    async def on_event(self, ev):
        self.call(self.brain.handle, ev)

    async def on_text(self, text, t):
        print(f"coach: heard: {text}", file=sys.stderr)
        self.call(self.brain.hear, text, t)

    async def on_live_text(self, text, t):
        """Gemini Live heard something: log it, never act on it."""
        with self.lock:
            b = self.brain
            ev = {"t": t, "source": "coach", "kind": "speech", "object": None, "hand": None,
                  "step": None if b.finished() else b.current, "confidence": None,
                  "detail": {"text": text}}
            b.log.append(ev)
        self.emitter.send(ev)

    async def ticker(self):
        while True:
            await asyncio.sleep(0.5)
            self.call(self.brain.tick, time.time())


async def replay(coach: Coach, path: Path, speed: float):
    """Feed a JSONL file through the coach. speed 1 = real time, 0 = no waiting."""
    prev = None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ev = json.loads(line)
        if ev.get("kind") == "speech" and ev.get("source") == "coach":
            ev = {**ev, "source": "speech"}   # recorded speech from an earlier run counts again
        t = ev.get("t")
        if speed > 0 and prev is not None and t is not None:
            await asyncio.sleep(max(0.0, (t - prev) / speed))
        prev = t if t is not None else prev
        await coach.on_event(ev)


async def follow_hub(coach: Coach, url: str):
    import websockets
    warned = False
    while True:
        try:
            async with websockets.connect(url, max_size=None) as ws:
                print(f"coach: following {url}", file=sys.stderr)
                warned = False
                async for msg in ws:
                    try:
                        data = json.loads(msg)
                    except ValueError:
                        continue
                    for ev in data if isinstance(data, list) else [data]:
                        if isinstance(ev, dict):
                            await coach.on_event(ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not warned:
                print(f"coach: hub {url} not there ({type(e).__name__}), retrying", file=sys.stderr)
                warned = True
            await asyncio.sleep(2)


def health_app(coach: Coach):
    from fastapi import FastAPI
    app = FastAPI(title="coach")

    @app.get("/health")
    def health():
        with coach.lock:
            b = coach.brain
            return {"ok": True, "part": "coach", "protocol": b.protocol.name,
                    "current": b.current, "finished": b.finished()}

    @app.get("/state")
    def state():
        with coach.lock:
            return coach.brain.state()

    return app


def ws_url(hub: str) -> str:
    return hub.replace("http://", "ws://").replace("https://", "wss://").rstrip("/") + "/ws"


async def amain(args):
    proto = protocol_mod.load(args.protocol)
    answerer = None
    if not args.no_llm:
        from .llm import make_answerer
        answerer = make_answerer(args.model)
    print(f"coach: '{proto.name}', {len(proto.steps)} steps, answers by "
          f"{'Claude' if answerer else 'rules'}{', hint mode' if args.hints else ''}", file=sys.stderr)

    backend = "say" if args.speak else ("file" if args.speak_to else "print")
    if args.quiet and backend == "print":
        backend = "none"
    fast = bool(args.events) and args.speed == 0
    speaker = Speaker(backend=backend, out_dir=args.speak_to,
                      voice=None if args.voice in (None, "gemini-live") else args.voice,
                      rate=args.rate, gap=args.gap, sync=fast and backend != "say")
    if not speaker.sync:
        speaker.start()
    emitter = Emitter(None if args.no_hub else args.hub)
    brain = Brain(proto, strong=args.strong, answerer=answerer, hints=args.hints)
    coach = Coach(brain, speaker, emitter)

    mic = "file" if args.source else (args.mic or ("off" if args.events else "capture"))
    tasks = []
    server = serving = None
    if args.port:
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(health_app(coach), host="127.0.0.1", port=args.port,
                                               log_level="warning"))
        serving = asyncio.create_task(server.serve())
    if not args.events:
        tasks.append(asyncio.create_task(coach.ticker()))

    listener = None
    if mic != "off":
        from . import listen as L

        def make_chunks():
            if mic == "file":
                return L.file_chunks(args.source, realtime=not args.fast_source)
            if mic == "local":
                return L.local_chunks()
            return L.capture_chunks(args.capture)

        stt = L.Whisper(args.whisper_model)

        async def whisper(chunks):
            await L.listen(chunks, stt, coach.on_text, muted=speaker.busy)

        from . import gemini_live
        if gemini_live.available(args.voice):
            listener = asyncio.create_task(gemini_live.listen_live(
                make_chunks, coach, whisper, model=args.gemini_live_model, audio_dir=args.speak_to))
        else:
            listener = asyncio.create_task(whisper(make_chunks()))
        tasks.append(listener)

    try:
        coach.replaying = bool(args.events)
        coach.call(brain.start)
        if args.events:
            await replay(coach, Path(args.events), args.speed)
        else:
            if not args.no_hub:
                tasks.append(asyncio.create_task(follow_hub(coach, ws_url(args.hub))))
            if mic == "file":
                await listener                   # stops at the end of the file
            else:
                await asyncio.Event().wait()     # live: run until stopped
        await asyncio.to_thread(speaker.wait_idle, 60)
    finally:
        if server:
            # Let uvicorn finish its own shutdown; cancelling it mid-way leaves its lifespan
            # task pending and prints "Event loop is closed" tracebacks on every Ctrl-C.
            server.should_exit = True
            try:
                await asyncio.wait_for(serving, 5)
            except BaseException:
                serving.cancel()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        speaker.stop()
        emitter.flush()
        if args.state_out:
            Path(args.state_out).write_text(json.dumps(brain.state(), indent=1))


def parse(argv=None):
    ap = argparse.ArgumentParser(prog="python -m coach", description="Bench Coach: the voice lab partner")
    ap.add_argument("--protocol", default="pipetting-demo.yaml", help="file, or a name in protocols/")
    ap.add_argument("--hub", default="http://127.0.0.1:8700")
    ap.add_argument("--no-hub", action="store_true", help="never use the hub; print events as JSON lines")
    ap.add_argument("--events", help="replay a JSONL event file instead of following the hub")
    ap.add_argument("--speed", type=float, default=0, help="replay speed: 1 = real time, 0 = no waiting")
    ap.add_argument("--mic", choices=["capture", "local", "off"], default=None,
                    help="capture's WS /mic (default when following the hub), the laptop mic, or off")
    ap.add_argument("--source", help="listen to this audio or video file instead of a mic")
    ap.add_argument("--fast-source", action="store_true", help="read --source as fast as possible")
    ap.add_argument("--capture", default="ws://127.0.0.1:8701/mic")
    ap.add_argument("--whisper-model", default="small.en")
    ap.add_argument("--speak", action="store_true", help="say lines out loud (off by default)")
    ap.add_argument("--speak-to", metavar="DIR", help="write each line as an audio file in DIR, play nothing")
    ap.add_argument("--quiet", action="store_true", help="do not print spoken lines")
    ap.add_argument("--voice", default=None, help="gemini-live, or a macOS voice name for --speak/--speak-to")
    ap.add_argument("--gemini-live-model", default="gemini-3.8-live")
    ap.add_argument("--rate", type=int, default=None, help="words per minute for the macOS voice")
    ap.add_argument("--gap", type=float, default=3.0, help="seconds between spoken lines")
    ap.add_argument("--strong", type=float, default=0.6, help="confidence that counts as solid evidence")
    ap.add_argument("--no-llm", action="store_true", help="rule answers even when a key is set")
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--hints", action="store_true", help="ask yes/no about vision hints")
    ap.add_argument("--port", type=int, default=8704, help="GET /health and /state; 0 for none")
    ap.add_argument("--state-out", help="write the final state as JSON here (tests)")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse(argv)
    try:
        protocol_mod.resolve(args.protocol)
    except FileNotFoundError as e:
        raise SystemExit(f"coach: {e}")
    loop = asyncio.new_event_loop()
    task = loop.create_task(amain(args))
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
