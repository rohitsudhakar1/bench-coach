#!/usr/bin/env python3
"""Scripted stand-in for the vision part, for rehearsals.

It listens to the hub. When a run starts it plays one pipetting session that follows
protocols/pipetting-demo.yaml with three planned mistakes:

- the pipette leans past 20 degrees while drawing from tube A,
- the tip is not changed between tube A and tube C,
- the second dispense into tube B never happens.

Vision does not measure angles, so the lean is sent as an `angle` event with source
`fake_vision` and `detail.scripted: true`. Everything else looks like vision's own events.
Stopping the run stops the script. With `--hub none` it plays once and prints JSON lines.

    python scripts/fake_vision.py --hub http://127.0.0.1:8820 --port 8822
    python scripts/fake_vision.py --hub none            # print the script once
"""
import argparse
import asyncio
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'protocols' / 'pipetting-demo.yaml'


def ev(kind, obj=None, confidence=0.9, cam=0):
    return {'source': 'vision', 'kind': kind, 'object': obj, 'hand': 'right', 'step': None,
            'confidence': confidence, 'detail': {'cam': cam}}


def lean(deg):
    return {'source': 'fake_vision', 'kind': 'angle', 'object': 'pipette', 'hand': None, 'step': None,
            'confidence': 0.8, 'detail': {'deg': deg, 'ok': deg <= 20, 'limit': 20, 'scripted': True}}


# Seconds after the run starts (at --speed 1), and the event. About 50 seconds in all.
SCRIPT = [
    (4.0, ev('hand_touch', 'tip box')),
    (5.0, ev('tip_on', 'pipette')),            # step 1 done
    (10.0, ev('hand_touch', 'tube A')),
    (11.0, lean(27.0)),                        # the tilt, while drawing from A
    (11.5, lean(28.0)),
    (12.5, ev('draw', 'tube A', 0.85)),        # step 2 done
    (14.0, lean(11.0)),                        # straight again
    (22.0, ev('dispense', 'tube B', 0.85)),    # step 3 done
    # no tip_off and tip_on: the same tip goes into tube C
    (32.0, ev('hand_touch', 'tube C', cam=1)),
    (33.0, ev('draw', 'tube C', 0.85, cam=1)), # reused tip; steps 4 and 5 skipped; step 6 done
    # no dispense into tube B: the tip comes straight off
    (45.0, ev('tip_off', 'pipette')),          # step 7 skipped; step 8 done
]


def check_script(protocol=PROTOCOL, script=SCRIPT):
    """Every action in the script must be evidence that some protocol step asks for."""
    steps = yaml.safe_load(Path(protocol).read_text())['steps']
    wanted = {(e['kind'], e.get('object')) for s in steps for e in s.get('expect', [])}
    for _, e in script:
        key = (e['kind'], None if e['kind'].startswith('tip_') else e['object'])
        if e['kind'] in ('tip_on', 'tip_off', 'draw', 'dispense') and key not in wanted:
            raise ValueError(f'the protocol never asks for {key}')
    return len(script)


class Player:
    def __init__(self, hub, speed):
        self.hub = None if hub in ('', 'none') else hub.rstrip('/')
        self.speed = speed
        self.task = None
        self.run = None
        self.sent = 0

    async def post(self, client, event):
        if self.hub:
            try:
                (await client.post(self.hub + '/events', json=event)).raise_for_status()
                self.sent += 1
                return
            except httpx.HTTPError:
                pass
        print(json.dumps(event), flush=True)

    async def play(self):
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            for offset, template in SCRIPT:
                await asyncio.sleep(max(0, start + offset / self.speed - time.monotonic()))
                event = dict(template, detail=dict(template['detail']), t=time.time())
                await self.post(client, event)
                print(f'fake_vision: {event["kind"]} {event["object"] or ""}', file=sys.stderr, flush=True)
        print('fake_vision: script finished', file=sys.stderr, flush=True)

    def on_run(self, detail):
        if self.task and not self.task.done():
            self.task.cancel()
        if detail.get('status') == 'start':
            self.run = detail.get('run')
            print(f'fake_vision: run {self.run} started', file=sys.stderr, flush=True)
            self.task = asyncio.create_task(self.play())
        else:
            print('fake_vision: run stopped', file=sys.stderr, flush=True)


async def follow(player):
    import websockets
    url = player.hub.replace('http', 'ws', 1) + '/ws'
    while True:
        try:
            async with websockets.connect(url, max_size=None) as ws:
                print(f'fake_vision: following {url}', file=sys.stderr, flush=True)
                async for message in ws:
                    data = json.loads(message)
                    for event in data if isinstance(data, list) else [data]:
                        if isinstance(event, dict) and event.get('kind') == 'run':
                            player.on_run(event.get('detail') or {})
        except asyncio.CancelledError:
            raise
        except Exception as exc:   # refused, closed, bad frame: wait and reconnect
            print(f'fake_vision: hub not reachable ({type(exc).__name__}), retrying', file=sys.stderr, flush=True)
            await asyncio.sleep(1)


def serve_health(port, player):
    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/health':
                self.send_error(404)
                return
            body = json.dumps({'ok': True, 'part': 'vision', 'fake': True,
                               'run': player.run, 'sent': player.sent}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', port), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def amain(args):
    player = Player(args.hub, args.speed)
    server = serve_health(args.port, player) if args.port else None
    try:
        if player.hub:
            await follow(player)
        else:
            await player.play()
    finally:
        if server:
            server.shutdown()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--hub', default='http://127.0.0.1:8700', help="hub address, or 'none' to print")
    p.add_argument('--port', type=int, default=8702, help='health port, 0 for none')
    p.add_argument('--speed', type=float, default=1.0, help='2 plays the script twice as fast')
    # Accepted and ignored, so the launcher can pass vision's usual options.
    p.add_argument('--capture')
    p.add_argument('--new-draw', action='store_true')
    args = p.parse_args(argv)
    if args.speed <= 0:
        p.error('--speed must be positive')
    print(f'fake_vision: {check_script()} scripted events match {PROTOCOL.name}', file=sys.stderr, flush=True)
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
