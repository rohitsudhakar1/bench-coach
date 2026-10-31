"""Several isolated bench applications in one hub process."""
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.exceptions import RequestValidationError


def create_classroom(count, base_port, runs_dir=None):
    from app.hub import create_app, Event

    benches = {}
    for number in range(1, count + 1):
        port = base_port + (number - 1) * 6
        urls = {name: f'http://127.0.0.1:{port + offset}'
                for name, offset in [('capture', 1), ('vision', 2), ('angle', 3), ('coach', 4), ('note', 5)]}
        child = create_app(runs_dir, urls['capture'], urls['note'],
                           bench=str(number), service_urls=urls)
        child.state.calibration.output = child.state.hub.runs / 'calibrations' / f'bench-{number}.json'
        benches[str(number)] = child

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            for child in benches.values():
                await stack.enter_async_context(child.router.lifespan_context(child))
            yield

    app = FastAPI(title='Bench Coach classroom', lifespan=lifespan)
    app.state.benches = benches
    app.add_exception_handler(RequestValidationError, benches['1'].exception_handlers[RequestValidationError])

    install_overview(app, benches)

    @app.post('/events')
    async def events(payload: Event | list[Event]):
        batch = payload if isinstance(payload, list) else [payload]
        if len(batch) > 1000:
            raise HTTPException(413, 'Send at most 1000 events at once.')
        if any(e.bench not in benches or e.kind in ('run', 'run-config') for e in batch):
            raise HTTPException(422, 'Unknown bench or reserved event kind.')
        for event in batch:
            hub = benches[event.bench].state.hub
            async with hub.lock:
                hub.publish(event.model_dump())
        return {'accepted': len(batch)}

    for bench, child in benches.items():
        app.mount(f'/benches/{bench}', child)
    # Original URLs continue to operate bench 1, including its producer stream.
    app.mount('/', benches['1'])
    return app


def install_overview(app, benches):
    from app.hub import ROOT

    @app.get('/class')
    async def classroom():
        return FileResponse(ROOT / 'static/class.html', headers={'Cache-Control': 'no-store'})

    @app.get('/class/state')
    async def states():
        rows = []
        for bench, child in benches.items():
            state = child.state.hub.state
            index = state['current_step']
            steps = state['protocol']['steps']
            warning = state['warnings'][-1]['detail'].get('text', '') if state['warnings'] else ''
            rows.append(dict(bench=bench, student=state.get('student', ''), run=state['run'],
                             recording=state['recording'], current_step=index,
                             instruction=steps[index]['text'] if isinstance(index, int) and 0 <= index < len(steps) else '',
                             warning=warning, attention=bool(state['recording'] and (warning or state['question'])),
                             question=state['question'], url=f'/benches/{bench}/' if len(benches) > 1 else './'))
        return sorted(rows, key=lambda row: (not row['attention'], int(row['bench'])))

