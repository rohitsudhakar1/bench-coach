"""The voice queue: one line at a time, in order of importance.

- Never two lines at once, and at most one every `gap` seconds (start to start).
- Warnings go first, then answers, then step lines. Only the newest step line is kept.
- Lines that waited too long are dropped (answers keep twice as long), so a busy moment never
  turns into a backlog of old steps.

Backends: "print" (the default; prints the line, makes no sound), "file" (renders each line to an
.aiff in a folder with macOS `say -o`; nothing is played), "say" (out loud; only with --speak),
"none" (tests).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time


class Speaker:
    def __init__(self, backend="print", out_dir=None, voice=None, rate=None, gap=3.0,
                 max_age=8.0, on_said=None, clock=time.monotonic, sync=False):
        if backend in ("say", "file") and not shutil.which("say"):
            print("coach: macOS say not found, printing lines instead", file=sys.stderr)
            backend = "print"
        self.backend, self.out_dir = backend, out_dir
        self.voice, self.rate = voice, rate
        self.gap, self.max_age = gap, max_age
        self.on_said, self.clock = on_said, clock
        self.items: list[tuple[int, float, str, str]] = []   # priority, queued at, text, topic
        self.cv = threading.Condition()
        self.speaking = False
        self.last_start = -1e9
        self.last_end = -1e9
        self.stopping = False
        self.thread = None
        self.spoken: list[str] = []
        self.files: list[str] = []
        self.sync = sync   # speak in the caller's thread, no queue (fast replays and tests)

    def start(self):
        self.thread = threading.Thread(target=self._run, name="speaker", daemon=True)
        self.thread.start()
        return self

    def stop(self):
        with self.cv:
            self.stopping = True
            self.cv.notify_all()
        if self.thread:
            self.thread.join(timeout=10)

    def submit(self, text: str, priority: int = 1, topic: str = ""):
        if not text:
            return
        if self.sync:
            self._speak(text)
            self.spoken.append(text)
            if self.on_said:
                self.on_said(text)
            return
        with self.cv:
            if topic == "step":
                self.items = [it for it in self.items if it[3] != "step"]
            if any(it[2] == text for it in self.items):
                return
            self.items.append((priority, self.clock(), text, topic))
            self.cv.notify_all()

    def busy(self, tail: float = 0.6) -> bool:
        """True while a line is coming out of the speaker and a moment after. Printed and file
        lines make no sound, so they never mute the mic."""
        return self.backend == "say" and (self.speaking or self.clock() - self.last_end < tail)

    def idle(self) -> bool:
        with self.cv:
            return not self.items and not self.speaking

    def wait_idle(self, timeout: float = 30.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.idle():
                return True
            time.sleep(0.05)
        return False

    def _pick(self):
        now = self.clock()
        self.items = [it for it in self.items
                      if now - it[1] <= self.max_age * (2 if it[3] in ("answer", "hint") else 1)]
        if not self.items:
            return None
        best = max(self.items, key=lambda it: (it[0], -it[1]))   # most important, then oldest
        self.items.remove(best)
        return best

    def _run(self):
        while True:
            with self.cv:
                while not self.stopping and not self.items:
                    self.cv.wait(0.5)
                if self.stopping:
                    return
                wait = self.last_start + self.gap - self.clock()
                if wait > 0:
                    self.cv.wait(wait)
                    continue
                item = self._pick()
                if item is None:
                    continue
                self.speaking = True
                self.last_start = self.clock()
            try:
                self._speak(item[2])
            except Exception as e:
                print(f"coach: voice failed ({type(e).__name__})", file=sys.stderr)
            finally:
                with self.cv:
                    self.speaking = False
                    self.last_end = self.clock()
            self.spoken.append(item[2])
            if self.on_said:
                try:
                    self.on_said(item[2])
                except Exception as e:
                    print(f"coach: on_said failed ({type(e).__name__})", file=sys.stderr)

    def _cmd(self):
        cmd = ["say"]
        if self.voice:
            cmd += ["-v", self.voice]
        if self.rate:
            cmd += ["-r", str(self.rate)]
        return cmd

    def _speak(self, text: str):
        if self.backend == "say":
            subprocess.run(self._cmd() + [text], check=False)
        elif self.backend == "file":
            os.makedirs(self.out_dir or ".", exist_ok=True)
            path = os.path.join(self.out_dir or ".", f"line-{len(self.files) + 1:03d}.aiff")
            subprocess.run(self._cmd() + ["-o", path, text], check=False)
            self.files.append(path)
            print(f"[coach line -> {os.path.basename(path)}] {text}", file=sys.stderr, flush=True)
        elif self.backend == "print":
            print(f"[coach says] {text}", file=sys.stderr, flush=True)
