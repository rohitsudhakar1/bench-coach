"""Bounded health probes shared by every browser tab."""
import asyncio
import time
import httpx


class PartStatus:
    def __init__(self, endpoints):
        self.endpoints = endpoints
        self.last_events = {}
        self.health = {}
        self.expires = 0
        self.checked_at = None
        self.lock = asyncio.Lock()

    def observe(self, event):
        name = event['source']
        if name in self.endpoints:
            self.last_events[name] = max(event['t'], self.last_events.get(name, 0))

    async def snapshot(self, running=False):
        async with self.lock:
            if time.monotonic() >= self.expires:
                async with httpx.AsyncClient(timeout=0.5, trust_env=False) as client:
                    async def probe(name, url):
                        try:
                            response = await asyncio.wait_for(client.get(url), 0.6)
                            response.raise_for_status()
                            body = response.json()
                            ready = not (isinstance(body, dict) and (body.get('ready') is False or body.get('status') in ('error', 'off', 'unavailable')))
                            return name, (ready, None if ready else 'This part reports that it is not ready.')
                        except (httpx.HTTPError, asyncio.TimeoutError, ValueError):
                            return name, (False, 'Cannot reach this part or read its health response.')
                    self.health = dict(await asyncio.gather(*(probe(n, u) for n, u in self.endpoints.items())))
                self.checked_at = time.time()
                self.expires = time.monotonic() + 2
        result = {}
        for name, (alive, error) in self.health.items():
            last = self.last_events.get(name)
            age = max(0, time.time() - last) if last is not None else None
            status = 'off' if not alive else 'sending' if age is not None and age <= 10 else 'quiet' if running and name in ('vision', 'angle', 'coach') else 'ready'
            message = error or ('Ready; waiting for events.' if age is None else f'Last event {age:.1f} seconds ago.')
            if name == 'capture' and alive:
                message += ' Camera frames travel separately.'
            if name == 'note' and alive:
                message += ' The note arrives after Stop.'
            result[name] = dict(alive=alive, status=status, message=message, last_event_at=last, last_event_age=age)
        result['app'] = dict(alive=True, status='sending' if running else 'ready', message='Event hub is reachable.', last_event_at=None, last_event_age=None)
        return dict(checked_at=self.checked_at, parts=result)
