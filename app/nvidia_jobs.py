"""Background NVIDIA jobs and run-scoped downloads; no new event kinds."""
import asyncio
import json
import os
from pathlib import Path
import re
import sys
from contextlib import asynccontextmanager

from fastapi import HTTPException
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[1]
SAFE = re.compile(r'^[A-Za-z0-9_-]{1,200}$')


def read_result(folder, kind):
    path = folder / 'nvidia-ui' / (kind + '.json')
    if path.is_file() and not path.is_symlink():
        try:
            result = json.loads(path.read_text())
            if isinstance(result, dict) and result.get('status') == 'ready':
                return result
        except (OSError, ValueError):
            pass
    return {'status': 'idle'}


class NvidiaJobs:
    def __init__(self, runs):
        self.runs = runs
        self.jobs = {}
        self.states = {}
        self.slot = asyncio.Semaphore(1)

    def folder(self, run):
        if not SAFE.fullmatch(run) or len(run) > 100:
            raise HTTPException(400, 'Invalid run.')
        folder = self.runs / run
        if folder.is_symlink() or not (folder / 'events.jsonl').is_file():
            raise HTTPException(404, 'Run not found.')
        # Exporters write nested files. Refuse links anywhere in their input/output tree.
        if any(p.is_symlink() for p in folder.rglob('*')):
            raise HTTPException(400, 'Run contains unsupported symbolic links.')
        return folder

    def status(self, run):
        folder = self.folder(run)
        return {kind: self.states.get((run, kind), read_result(folder, kind))
                for kind in ('export', 'synth')}

    def start(self, run, kind):
        folder = self.folder(run)
        key = (run, kind)
        if key in self.jobs or self.status(run)[kind]['status'] == 'ready':
            return
        self.states[key] = {'status': 'queued'}
        task = asyncio.create_task(self.work(run, kind, folder))
        self.jobs[key] = task
        task.add_done_callback(lambda _: self.jobs.pop(key, None))

    async def work(self, run, kind, folder):
        key = (run, kind)
        process = None
        try:
            async with self.slot:
                self.states[key] = {'status': 'running'}
                python = sys.executable
                designer = ROOT / 'nvidia/.venv/bin/python'
                if kind == 'synth' and designer.is_file():
                    python = str(designer)
                process = await asyncio.create_subprocess_exec(
                    python, '-m', 'app.nvidia_worker', kind, str(folder.resolve()),
                    cwd=ROOT, env={**os.environ, 'NEMO_TELEMETRY_ENABLED': 'false'},
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(process.wait(), timeout=170)
                if process.returncode:
                    raise RuntimeError('worker failed')
                result = read_result(folder, kind)
                if result['status'] != 'ready':
                    raise RuntimeError('missing result')
                self.states[key] = result
        except asyncio.CancelledError:
            raise
        except Exception:
            self.states[key] = {'status': 'error', 'message': 'Could not finish. Retry the job; check the run has a protocol and events.'}
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()

    async def close(self):
        tasks = list(self.jobs.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def install(app, hub):
    jobs = NvidiaJobs(hub.runs)
    app.state.nvidia = jobs

    @asynccontextmanager
    async def lifespan(app):
        # Resume a stopped run whose export was interrupted by a hub restart.
        run = hub.state['run']
        if run and not hub.state['recording']:
            try:
                jobs.start(run, 'export')
            except HTTPException:
                pass  # An unusable saved run must not prevent hub startup.
        try:
            yield
        finally:
            await jobs.close()

    app.router.lifespan_context = lifespan

    @app.get('/nvidia/{run}')
    async def status(run: str):
        return jobs.status(run)

    @app.post('/nvidia/{run}/{kind}', status_code=202)
    async def start(run: str, kind: str):
        if kind not in ('export', 'synth'):
            raise HTTPException(400, 'Unknown job.')
        jobs.folder(run)
        if hub.state['recording'] and hub.state['run'] == run:
            raise HTTPException(409, 'Stop the run first.')
        jobs.start(run, kind)
        return jobs.status(run)

    @app.get('/nvidia/{run}/tasks/{task}.zip')
    async def download(run: str, task: str):
        folder = jobs.folder(run)
        result = jobs.status(run)['export']
        if not SAFE.fullmatch(task) or task not in {t['id'] for t in result.get('tasks', [])}:
            raise HTTPException(404, 'Task not found.')
        path = folder / 'nvidia-ui' / 'zips' / (task + '.zip')
        if not path.is_file():
            raise HTTPException(404, 'Task archive not found.')
        return FileResponse(path, media_type='application/zip', filename=task + '.zip')

    return jobs
