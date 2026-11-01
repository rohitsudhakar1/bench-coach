"""Single-worker event hub. Start with python -m app.hub."""
import argparse
import asyncio
import copy
import html
import json
import os
import re
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.calibration import install as install_calibration
from app.nvidia_jobs import install as install_nvidia
from app.parts import PartStatus
from app.trainer import overview
from protocols.convert import MAX_TEXT, convert_text, validate_protocol

ROOT = Path(__file__).resolve().parent
RUNS = Path(os.environ.get('BENCH_RUNS', ROOT.parent / 'runs'))
SAFE_ID = re.compile(r'^[A-Za-z0-9_-]{1,100}$')


class Event(BaseModel):
    model_config = ConfigDict(extra='allow', allow_inf_nan=False)
    bench: str = Field(default='1', pattern=r'^[A-Za-z0-9_-]{1,100}$')
    t: float = Field(ge=0)
    source: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=100)
    object: str | None = None
    hand: str | None = None
    step: int | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator('detail')
    @classmethod
    def safe_json(cls, value):
        json.dumps(value, allow_nan=False)
        return value

    @model_validator(mode='after')
    def finite_json(self):
        json.dumps(self.model_dump(), allow_nan=False)
        return self


class RunRequest(BaseModel):
    protocol: str | None = None
    student: str = Field(default="", max_length=120)


class ProtocolRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    name: str = Field(default='Imported protocol', min_length=1, max_length=200)


class Hub:
    def __init__(self, runs, protocols, parts, bench="1"):
        self.bench = bench
        self.runs, self.protocols, self.parts = runs, protocols, parts
        self.selected_file = 'pipetting-demo.yaml'
        self.protocol = validate_protocol(yaml.safe_load((protocols / self.selected_file).read_text()))
        self.history = deque(maxlen=10000)
        self.subscribers = set()
        self.lock = asyncio.Lock()
        self.controls = asyncio.Lock()
        self.reset()
        self.recover()

    def reset(self):
        self.state = dict(bench=self.bench, run=None, recording=False, capture=None, recovered=False,
                          current_step=None, question=None, steps={}, warnings=[], chat=[],
                          angle=None, objects={}, note=None, protocol=copy.deepcopy(self.protocol),
                          protocol_file=self.selected_file)

    def apply(self, event):
        kind, detail = event['kind'], event['detail']
        if kind == 'run' and detail.get('status') == 'start':
            self.reset()
            self.state.update(run=detail['run'], recording=True, student=detail.get('student', ''))
        elif kind == 'run' and detail.get('status') == 'stop':
            self.state.update(recording=False, warnings=[], question=None, angle=None, objects={})
        elif kind == 'step':
            index = detail.get('index', event.get('step'))
            if isinstance(index, int) and index >= 0:
                self.state['steps'][str(index)] = detail
                if detail.get('status') == 'current':
                    self.state['current_step'] = index
                elif self.state['current_step'] == index:
                    self.state['current_step'] = None
                if 'question' in detail and self.state['recording']:
                    self.state['question'] = detail['question']
        elif kind == 'warning' and self.state['recording']:
            self.state['warnings'] = (self.state['warnings'] + [event])[-30:]
        elif kind == 'angle' and self.state['recording']:
            self.state['angle'] = detail
        elif kind == 'objects':
            self.state['objects'][str(detail.get('cam', 0))] = dict(detail, t=event['t'])
        elif kind in ('speech', 'say'):
            self.state['chat'] = (self.state['chat'] + [event])[-150:]
        elif kind == 'note' and isinstance(detail.get('run'), str) and detail['run'] == self.state['run']:
            self.state['note'] = detail
        self.parts.observe(event)
        self.history.append(event)

    def publish(self, event):
        event = dict(event, bench=self.bench)
        # Persist before making an event visible. Caller owns the state lock.
        run = event['detail'].get('run') if event['kind'] in ('run', 'note') else self.state['run']
        if isinstance(run, str) and SAFE_ID.fullmatch(run):
            folder = self.runs / run
            # Late notes can append to real runs, never create producer-named folders.
            known = event['kind'] != 'note' or self.owns_run(folder)
            if known and not folder.is_symlink() and not (folder / 'events.jsonl').is_symlink():
                folder.mkdir(parents=True, exist_ok=True)
                with (folder / 'events.jsonl').open('a') as stream:
                    stream.write(json.dumps(event, allow_nan=False) + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
        self.apply(event)
        for queue in tuple(self.subscribers):
            if queue.full():
                self.subscribers.remove(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
            else:
                queue.put_nowait(event)

    def owns_run(self, folder):
        path = folder / 'events.jsonl'
        if folder.is_symlink() or path.is_symlink() or not path.is_file():
            return False
        with path.open() as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if row.get('kind') == 'run' and row.get('detail', {}).get('status') == 'start':
                        return row.get('bench', '1') == self.bench
                except (ValueError, AttributeError):
                    continue
        return False

    def recover(self):
        # Use the start clock, not mtime: a late note may update an older log.
        candidates = []
        for path in self.runs.glob('*/events.jsonl'):
            if not SAFE_ID.fullmatch(path.parent.name) or path.is_symlink() or path.parent.is_symlink():
                continue
            with path.open() as stream:
                for line in stream:
                    try:
                        event = Event.model_validate_json(line).model_dump()
                        if event['bench'] == self.bench and event['kind'] == 'run' and event['detail'].get('status') == 'start' and event['detail'].get('run') == path.parent.name:
                            candidates.append((event['t'], str(path), path))
                            break
                    except ValueError:
                        continue
        if not candidates:
            return
        path = max(candidates)[2]
        tail = ''
        with path.open() as stream:
            for line in stream:
                tail = line
                try:
                    event = Event.model_validate_json(line).model_dump()
                    if event['bench'] != self.bench:
                        continue
                    if event['kind'] == 'run':
                        if event['detail'].get('run') != path.parent.name:
                            continue
                        if event['detail'].get('status') == 'start':
                            data = event['detail'].get('protocol_data')
                            if data:
                                self.protocol = validate_protocol(data)
                            self.selected_file = Path(event['detail'].get('protocol', self.selected_file)).name
                    if self.state['run'] or event['kind'] == 'run' and event['detail'].get('status') == 'start':
                        self.apply(event)
                except (ValueError, TypeError):
                    continue
        if tail and not tail.endswith('\n'):
            with path.open('a') as stream:
                stream.write('\n')
        self.state['recovered'] = True
        if self.state['recording']:
            self.state['capture'] = {'ok': False, 'error': 'Hub restarted. Event logging resumed; camera recording status is unknown.'}


def make_event(kind, detail):
    return Event(t=time.time(), source='app', kind=kind, detail=detail).model_dump()


def create_app(runs_dir=None, capture_url=None, note_service_url=None, protocols_dir=None, bench="1", service_urls=None):
    app = FastAPI(title='Bench Coach')
    capture = (capture_url or os.getenv('CAPTURE_URL', 'http://127.0.0.1:8701')).rstrip('/')
    note = (note_service_url or os.getenv('NOTE_URL', 'http://127.0.0.1:8705')).rstrip('/')
    parts = PartStatus({'capture': capture + '/cams', 'note': note + '/health', **{
        name: (service_urls or {}).get(name, os.getenv(name.upper() + '_URL', f'http://127.0.0.1:{port}')).rstrip('/') + '/health'
        for name, port in [('vision', 8702), ('angle', 8703), ('coach', 8704)]}})
    hub = Hub(Path(runs_dir or RUNS), Path(protocols_dir or ROOT.parent / 'protocols'), parts, bench)
    app.state.hub = hub
    nvidia = install_nvidia(app, hub)
    install_calibration(app, capture, hub.runs)
    from app.classroom import install_overview
    install_overview(app, {bench: app})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo non-finite numbers or large producer payloads in an error body.
        return JSONResponse(status_code=422, content={'detail': [
            {key: error[key] for key in ('loc', 'msg', 'type')} for error in exc.errors()]})

    @app.get('/health')
    async def health():
        return {'status': 'ready', 'recording': hub.state['recording']}

    @app.post('/events')
    async def events(payload: Event | list[Event]):
        batch = payload if isinstance(payload, list) else [payload]
        if len(batch) > 1000:
            raise HTTPException(413, 'Send at most 1000 events at once.')
        if any(e.kind in ('run', 'run-config') for e in batch):
            raise HTTPException(422, 'Use the run and protocol controls.')
        if any('bench' in e.model_fields_set and e.bench != bench for e in batch):
            raise HTTPException(422, 'Event belongs to a different bench.')
        async with hub.lock:
            for event in batch:
                hub.publish(event.model_dump())
        return {'accepted': len(batch)}

    @app.get('/events')
    async def history(since: float = Query(default=0, ge=0, allow_inf_nan=False)):
        return [e for e in hub.history if e['t'] > since]

    @app.get('/state')
    async def state():
        return copy.deepcopy(hub.state)

    @app.get('/parts')
    async def part_status():
        return await parts.snapshot(hub.state['recording'])

    @app.get('/config')
    async def config():
        # Camera count from capture, so the page lays out one view or two; 2 when capture is down.
        cams = 2
        try:
            async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                response = await client.get(capture + '/cams')
                response.raise_for_status()
                if isinstance(response.json(), list) and len(response.json()) in (1, 2):
                    cams = len(response.json())
        except (httpx.HTTPError, ValueError):
            pass
        return {'capture_url': capture if service_urls else os.getenv('CAPTURE_PUBLIC_URL', capture), 'protocol': hub.protocol,
                'protocol_file': hub.selected_file, 'cams': cams}

    @app.websocket('/ws')
    async def websocket(ws: WebSocket):
        await ws.accept()
        queue = asyncio.Queue(maxsize=256)
        hub.subscribers.add(queue)
        async def send():
            while True:
                event = await queue.get()
                if event is None:
                    await ws.close(code=1013)
                    return
                await ws.send_json(event)
        async def receive():
            while True:
                await ws.receive_text()
        tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            hub.subscribers.discard(queue)
            for task in tasks:
                task.cancel()
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                pass

    async def record(action, run):
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                response = await client.post(capture + '/record/' + action, json={
                    'run': run, 'protocol': 'protocols/' + hub.selected_file})
                response.raise_for_status()
                data = response.json() if response.content else {}
                if isinstance(data, dict) and (data.get('error') or data.get('ok') is False):
                    return {'ok': False, 'error': 'Capture could not complete the recording request.'}
            return {'ok': True}
        except (httpx.HTTPError, ValueError):
            return {'ok': False, 'error': 'Capture is unavailable. The event log is still saved.'}

    @app.get('/capture-status')
    async def capture_status():
        try:
            async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                cams, recording = await asyncio.gather(
                    client.get(capture + '/cams'), client.get(capture + '/record/status'))
                cams.raise_for_status()
                recording.raise_for_status()
                return {'cams': cams.json(), 'recording': recording.json()}
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, 'Capture status is unavailable.') from None

    @app.post('/run/start')
    async def start(request: RunRequest = RunRequest()):
        async with hub.controls:
            if hub.state['recording']:
                raise HTTPException(409, 'A run is already active in this hub.')
            filename = Path(request.protocol or hub.selected_file).name
            path = hub.protocols / filename
            if not path.is_file() or path.is_symlink():
                raise HTTPException(404, 'Protocol not found.')
            try:
                protocol = validate_protocol(yaml.safe_load(path.read_text()))
            except (ValueError, yaml.YAMLError):
                raise HTTPException(422, 'Protocol is invalid.') from None
            run = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
            async with hub.lock:
                hub.selected_file, hub.protocol = filename, protocol
                hub.publish(make_event('run', dict(status='start', run=run,
                    protocol='protocols/' + filename, protocol_data=protocol, student=request.student.strip())))
            result = await record('start', run)
            hub.state['capture'] = result
            return {'run': run, 'capture': result}

    @app.post('/run/stop')
    async def stop():
        async with hub.controls:
            if not hub.state['recording']:
                raise HTTPException(409, 'No active run.')
            run = hub.state['run']
            result = await record('stop', run)
            async with hub.lock:
                hub.publish(make_event('run', dict(status='stop', run=run, protocol='protocols/' + hub.selected_file)))
                hub.state['capture'] = result
            nvidia.start(run, 'export')
            return {'run': run, 'capture': result}

    @app.post('/protocol')
    async def load(request: ProtocolRequest):
        async with hub.controls:
            if hub.state['recording']:
                raise HTTPException(409, 'Stop the run before loading a protocol.')
            try:
                document, method = await asyncio.to_thread(convert_text, request.text, request.name)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            filename = 'imported-' + uuid.uuid4().hex + '.yaml'
            hub.protocols.mkdir(parents=True, exist_ok=True)
            path = hub.protocols / filename
            temporary = path.with_suffix('.tmp')
            try:
                temporary.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True))
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            async with hub.lock:
                hub.selected_file, hub.protocol = filename, document
                hub.reset()
                hub.publish(make_event('run-config', dict(protocol='protocols/' + filename,
                    protocol_data=document, conversion=method)))
            return {'protocol': document, 'protocol_file': filename, 'conversion': method}

    @app.get('/lab-note/{run}')
    async def lab_note(run: str, format: str = 'md'):
        if not SAFE_ID.fullmatch(run) or format not in ('md', 'html', 'pdf', 'zip', 'benchling', 'eln', 'csv'):
            raise HTTPException(400, 'Invalid run or note format.')
        detail = hub.state['note'] or {}
        url = detail.get('url') if detail.get('run') == run else None
        # Event URLs may select a note path, but never a different service origin.
        origin = urlsplit(note)
        parsed = urlsplit(url) if isinstance(url, str) else None
        if not parsed or (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
            parsed = urlsplit(note + '/note/' + run)
        target = urlunsplit((parsed.scheme, parsed.netloc, parsed.path + ('.' + format if format in ('pdf', 'zip', 'benchling', 'eln', 'csv') else ''),
                            '' if format in ('pdf', 'zip', 'benchling', 'eln', 'csv') else 'format=' + format, ''))
        try:
            async with httpx.AsyncClient(timeout=60 if format in ('pdf', 'zip', 'benchling', 'eln', 'csv') else 5, trust_env=False) as client:
                response = await client.get(target)
                response.raise_for_status()
        except httpx.HTTPError:
            raise HTTPException(503, 'The note is not available yet. Try again shortly.') from None
        headers = {'Cache-Control': 'no-store'}
        if format in ('pdf', 'zip', 'benchling', 'eln', 'csv'):
            extension = 'html' if format == 'benchling' else format
            headers['Content-Disposition'] = f'attachment; filename="bench-coach-{run}.{extension}"'
            mime = {'pdf': 'application/pdf', 'zip': 'application/zip', 'benchling': 'text/html',
                    'eln': 'application/vnd.eln+zip', 'csv': 'text/csv'}
            return Response(response.content, media_type=mime[format], headers=headers)
        if format == 'html':
            base = '<base href="' + html.escape(str(response.url), quote=True) + '">'
            content = re.sub(r'<head\b[^>]*>', lambda m: m[0] + base, response.text, count=1, flags=re.I)
            if content == response.text:
                content = base + content
            content += '<script>' + (ROOT / 'static/note-player.js').read_text() + '</script>'
            headers['Content-Security-Policy'] = 'sandbox allow-scripts allow-popups'
            return HTMLResponse(content, headers=headers)
        try:
            return response.json()
        except ValueError:
            return {'text': response.text}

    @app.get('/trainer')
    async def trainer_page():
        return FileResponse(ROOT / 'static/trainer.html', headers={'Cache-Control': 'no-store'})

    @app.get('/trainer/runs')
    def trainer_runs(protocol: str = '', student: str = ''):
        return JSONResponse(overview(hub.runs, protocol, student), headers={'Cache-Control': 'no-store'})

    @app.get('/')
    async def index():
        return FileResponse(ROOT / 'static/index.html', headers={'Cache-Control': 'no-cache'})

    @app.get('/live')
    async def live_page():
        # The live view runs in its own process (python -m app.live), next to the camera models.
        return RedirectResponse(os.environ.get('LIVE_URL', 'http://127.0.0.1:8706/'), status_code=307)

    @app.get('/step')
    async def phone_step():
        return FileResponse(ROOT / 'static/step.html', headers={'Cache-Control': 'no-cache'})

    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=Path, default=RUNS)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8700)
    parser.add_argument('--benches', type=int, default=1)
    parser.add_argument('--capture')
    parser.add_argument('--note')
    args = parser.parse_args()
    if not 1 <= args.benches <= 100 or args.port + 6 * args.benches - 1 > 65535:
        parser.error('Invalid bench count or port range.')
    if args.benches > 1:
        from app.classroom import create_classroom
        application = create_classroom(args.benches, args.port, args.runs)
    else:
        application = create_app(args.runs, args.capture, args.note)
    uvicorn.run(application, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
