"""Contract events, throttling and hub delivery with a stdout fallback."""
import json
import math
import time
import httpx


class EventGate:
    def __init__(self, limit=20., hz=2.):
        if not math.isfinite(limit) or not 0 <= limit <= 90 or not 0 < hz <= 3:
            raise ValueError('Limit must be 0..90; event rate must be above 0 and at most 3 Hz')
        self.limit, self.period = limit, 1/hz
        self.last_angle = self.last_warning = -math.inf

    def update(self, result, step=None, now=None, stamp=None):
        if result is None:
            return []
        deg, confidence, detail = result
        if not math.isfinite(deg) or not 0 <= deg <= 90 or not 0 <= confidence <= 1:
            raise ValueError('Invalid angle or confidence')
        now = time.monotonic() if now is None else now
        if now - self.last_angle < self.period:
            return []
        self.last_angle = now
        base = dict(t=time.time() if stamp is None else stamp, source='angle', object='pipette',
                    hand=None, step=step, confidence=confidence)
        ok = deg <= self.limit
        events = [dict(base, kind='angle', detail=dict(detail, deg=round(deg, 2), ok=ok, limit=self.limit))]
        if not ok and now-self.last_warning >= 5:
            self.last_warning = now
            prefix = 'Rough camera estimate: ' if detail['rough'] else ''
            warning = dict(text=f'{prefix}pipette tilt is {deg:.0f} degrees; keep it within {self.limit:g} degrees of vertical.',
                           rough=detail['rough'])
            events.append(dict(base, kind='warning', detail=warning))
        return events


class Publisher:
    def __init__(self, hub):
        self.url = hub.rstrip('/') + '/events' if hub else None
        self.client = httpx.Client(timeout=.25)
        self.retry_at = 0

    def send(self, events):
        for event in events:
            if self.url and time.monotonic() >= self.retry_at:
                try:
                    self.client.post(self.url, json=event).raise_for_status()
                    continue
                except httpx.HTTPError:
                    self.retry_at = time.monotonic() + 5
            print(json.dumps(event, allow_nan=False), flush=True)

    def close(self):
        self.client.close()
