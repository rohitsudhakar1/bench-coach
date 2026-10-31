"""Send events to the hub; print them as JSON lines while the hub is not there."""
from __future__ import annotations

import json
import sys
import threading
import time


class Sink:
    def __init__(self, hub: str | None, log_path: str | None = None, quiet_objects: bool = False,
                 retry: float = 5.0, client=None, out=None):
        self.url = hub.rstrip("/") + "/events" if hub else None
        self.down_until, self.retry = 0.0, retry
        self.log = open(log_path, "a") if log_path else None
        self.quiet_objects = quiet_objects
        self.out = out or sys.stdout
        self.lock = threading.Lock()
        self.client = client
        if self.url and client is None:
            import httpx
            self.client = httpx.Client(timeout=1.0)

    def send(self, events: list):
        if not events:
            return
        with self.lock:
            if self.log:
                self.log.writelines(json.dumps(e) + "\n" for e in events)
                self.log.flush()
            posted = False
            if self.client is not None and self.url and time.time() >= self.down_until:
                try:
                    posted = self.client.post(self.url, json=events).status_code < 300
                except Exception:
                    posted = False
                if not posted:
                    self.down_until = time.time() + self.retry  # try the hub again later
            if not posted:
                for e in events:
                    if not (self.quiet_objects and e["kind"] == "objects"):
                        self.out.write(json.dumps(e) + "\n")
                self.out.flush()

    def close(self):
        if self.client is not None:
            self.client.close()
        if self.log:
            self.log.close()
