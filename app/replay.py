"""Replay a JSONL event file without cameras or audio playback."""
import argparse
import json
import time
from pathlib import Path
import httpx
from app.hub import Event


def load_events(path):
    result = []
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = Event.model_validate_json(line).model_dump()
        except ValueError as exc:
            raise ValueError(f'Invalid event on line {number}') from exc
        if event['kind'] not in ('run', 'run-config', 'note'):
            result.append(event)
    return result


def replay(source, hub='http://127.0.0.1:8700', speed=1, start_run=False):
    if speed <= 0:
        raise ValueError('Speed must be positive.')
    events = load_events(source)
    started = False
    with httpx.Client(base_url=hub.rstrip('/'), timeout=5, trust_env=False) as client:
        try:
            if start_run:
                response = client.post('/run/start'); response.raise_for_status(); started = True
            if not events:
                return
            wall, clock, first = time.time(), time.monotonic(), events[0]['t']
            for event in events:
                offset = max(0, (event['t'] - first) / speed)
                time.sleep(max(0, clock + offset - time.monotonic()))
                event['t'] = wall + offset
                try:
                    response = client.post('/events', json=event); response.raise_for_status()
                except httpx.HTTPError:
                    print(json.dumps(event), flush=True)
        finally:
            if started:
                try:
                    client.post('/run/stop').raise_for_status()
                except httpx.HTTPError:
                    print('Could not stop the replay run; use the hub Stop control.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--hub', default='http://127.0.0.1:8700')
    parser.add_argument('--speed', type=float, default=1)
    parser.add_argument('--start-run', action='store_true')
    args = parser.parse_args()
    replay(args.source, args.hub, args.speed, args.start_run)


if __name__ == '__main__':
    main()
