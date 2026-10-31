"""Shared, demand-driven checkerboard collection and background angle fitting."""
import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid
from contextlib import asynccontextmanager

import cv2
import httpx
import numpy as np
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from angle.calibrate import fit, validate_times

ROOT = Path(__file__).resolve().parent
PATTERN = (9, 6)


class CaptureRequest(BaseModel):
    revision: str
    level: bool = False


class CalibrationUI:
    def __init__(self, capture, folder, output, transport=None):
        self.capture = capture
        self.folder = Path(folder)
        self.output = Path(output)
        self.transport = transport
        self.lock = asyncio.Lock()
        self.next_detection = 0
        self.frames = []
        self.views = []
        self.revision = ''
        self.message = 'Show the full board in both cameras.'
        self.session = uuid.uuid4().hex
        self.names = []
        self.previous = [-np.inf, -np.inf]
        self.size = None
        self.result = None
        self.verdict = 'collecting'
        self.task = None
        self.used = False

    @property
    def directory(self):
        return self.folder / self.session

    def ready(self):
        try:
            if len(self.frames) != 2 or any(f['corners'] is None for f in self.frames):
                return False
            validate_times([f['t'] for f in self.frames], self.previous, time.time(), .1)
            return True
        except ValueError:
            return False

    def state(self):
        return dict(views=self.views, revision=self.revision, count=len(self.names),
                    can_capture=self.ready() and self.verdict != 'computing' and len(self.names) < 40,
                    verdict=self.verdict, message=self.message, result=self.result, used=self.used)

    async def refresh(self):
        async with self.lock:
            if time.monotonic() < self.next_detection or self.verdict == 'computing':
                return self.state()
            self.frames = []
            self.views = []
            try:
                async with httpx.AsyncClient(timeout=2, trust_env=False, transport=self.transport) as client:
                    replies = await asyncio.gather(*[client.get(f'{self.capture}/cam/{i}/frame.jpg') for i in (0, 1)])
                frames = await asyncio.to_thread(self.detect, replies)
                self.frames = frames
                self.views = [dict(image=f['overlay'], found=f['corners'] is not None, t=f['t']) for f in frames]
                self.revision = uuid.uuid4().hex
                if self.verdict == 'collecting':
                    self.message = 'Hold the board still, then capture.' if self.ready() else 'Show the whole board in both views; wait for fresh, matching frames.'
            except (ValueError, httpx.HTTPError, cv2.error):
                self.message = 'Capture unavailable or invalid frames. Check both camera feeds.'
            finally:
                # Completion-based cooldown bounds every camera to <=2 detections/sec,
                # even with concurrent browser tabs or slow OpenCV calls.
                self.next_detection = time.monotonic() + .5
            return self.state()

    def detect(self, replies):
        frames = []
        for reply in replies:
            reply.raise_for_status()
            stamp = float(reply.headers.get('X-Frame-Time', 'nan'))
            if not np.isfinite(stamp):
                raise ValueError('Missing timestamp')
            image = cv2.imdecode(np.frombuffer(reply.content, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError('Invalid JPEG')
            found, corners = cv2.findChessboardCornersSB(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), PATTERN)
            overlay = image.copy()
            if found:
                cv2.drawChessboardCorners(overlay, PATTERN, corners, True)
            ok, jpeg = cv2.imencode('.jpg', overlay, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if not ok:
                raise ValueError('Cannot encode preview')
            frames.append(dict(image=image, corners=corners if found else None, t=stamp,
                               overlay=base64.b64encode(jpeg).decode('ascii')))
        return frames

    async def save(self, request):
        async with self.lock:
            if self.verdict == 'computing' or len(self.names) >= 40:
                raise HTTPException(409, 'Compute or start a new set before capturing more pairs.')
            if request.revision != self.revision or not self.ready():
                raise HTTPException(409, 'Pair changed, is stale, or the board is missing. Wait for both views and try again.')
            if not self.names and not request.level:
                raise HTTPException(409, 'Confirm the first board is flat on a level surface.')
            sizes = [(f['image'].shape[1], f['image'].shape[0]) for f in self.frames]
            if sizes[0] != sizes[1] or (self.size and self.size != sizes[0]):
                raise HTTPException(409, 'Both cameras must keep the same resolution. Start a new set after fixing them.')
            name = f'{len(self.names):04d}'
            try:
                await asyncio.to_thread(self.write_pair, name)
            except (OSError, cv2.error):
                raise HTTPException(507, 'Could not save the pair. Check storage and try again.') from None
            self.size = sizes[0]
            self.names.append(name)
            self.previous = [f['t'] for f in self.frames]
            self.result, self.used, self.verdict = None, False, 'collecting'
            self.message = 'Level reference saved. Now vary the angle, depth and position.' if len(self.names) == 1 else 'Pair saved. Vary the angle, depth and position.'
            return self.state()

    def write_pair(self, name):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / (name + '.tmp')
        temporary.mkdir()
        try:
            for side, frame in zip(('left', 'right'), self.frames):
                if not cv2.imwrite(str(temporary / (side + '.png')), frame['image']):
                    raise OSError('Cannot save image')
            np.savez(temporary / 'corners.npz', left=self.frames[0]['corners'], right=self.frames[1]['corners'],
                     size=self.frames[0]['image'].shape[1::-1], stamps=[f['t'] for f in self.frames])
            temporary.rename(self.directory / name)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    async def compute(self):
        async with self.lock:
            if self.verdict == 'computing':
                return self.state()
            if len(self.names) < 12:
                raise HTTPException(409, 'Capture at least 12 different board poses first.')
            self.verdict, self.result, self.used = 'computing', None, False
            self.message = 'Computing the camera fit…'
            self.task = asyncio.create_task(self.run_fit())
            return self.state()

    async def run_fit(self):
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'app.calibration', '--fit', str(self.directory.resolve()),
                cwd=str(ROOT.parent), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(process.wait(), 170)
            if process.returncode:
                raise ValueError('Fit failed. Retake sharp pairs with the whole board visible.')
            response = json.loads((self.directory / 'verdict.json').read_text())
            self.result = response.get('result')
            self.verdict = 'good' if self.result else 'redo'
            self.message = response['message']
        except (OSError, ValueError, asyncio.TimeoutError):
            self.verdict = 'redo'
            self.message = 'Fit could not finish. Start a new set with clear, varied poses and retry.'
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()

    async def use(self):
        async with self.lock:
            if self.verdict != 'good' or not self.result:
                raise HTTPException(409, 'Only a good fit can be used.')
            temporary = self.output.with_name(self.output.name + '.tmp')
            try:
                self.output.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(self.result, indent=2, allow_nan=False) + '\n')
                temporary.replace(self.output)
            except OSError:
                raise HTTPException(507, 'Could not install the calibration. Check storage and permissions.') from None
            finally:
                temporary.unlink(missing_ok=True)
            self.used = True
            self.message = 'Saved. Restart angle with this calibration, then complete the physical pencil check before trusting tilt.'
            return self.state()

    async def reset(self):
        async with self.lock:
            if self.verdict == 'computing':
                raise HTTPException(409, 'Wait for the fit to finish before starting a new set.')
            self.session = uuid.uuid4().hex
            self.names, self.previous = [], [-np.inf, -np.inf]
            self.size, self.result, self.used = None, None, False
            self.verdict = 'collecting'
            self.message = 'New set. Lay the board flat on a level surface for the first pair.'
            return self.state()

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


def install(app, capture, runs):
    service = CalibrationUI(capture, Path(runs) / 'calibration-pairs',
                            os.getenv('BENCH_CALIBRATION', str(ROOT.parent / 'angle/calibration.json')))
    app.state.calibration = service
    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application):
            try:
                yield
            finally:
                await service.close()
    app.router.lifespan_context = lifespan

    @app.get('/calibrate')
    async def page():
        return FileResponse(ROOT / 'static/calibrate.html')

    @app.get('/calibrate/board.png')
    async def board():
        return FileResponse(ROOT.parent / 'angle/checkerboard-25mm.png')

    @app.get('/calibrate/state')
    async def state():
        return await service.refresh()

    @app.post('/calibrate/capture')
    async def capture_pair(request: CaptureRequest):
        return await service.save(request)

    @app.post('/calibrate/compute', status_code=202)
    async def compute():
        return await service.compute()

    @app.post('/calibrate/use')
    async def use():
        return await service.use()

    @app.post('/calibrate/reset')
    async def reset():
        return await service.reset()
    return service


def worker(directory):
    left, right, names, size = [], [], [], None
    for path in sorted(directory.glob('[0-9][0-9][0-9][0-9]/corners.npz')):
        with np.load(path, allow_pickle=False) as data:
            left.append(data['left'])
            right.append(data['right'])
            size = tuple(map(int, data['size']))
        names.append(path.parent.name)
    try:
        result = fit(left, right, names, size, PATTERN, 25)
        response = dict(result=result, message='Good fit. Keep cameras fixed; the physical pencil check is still required.')
    except ValueError as exc:
        response = dict(result=None, message='Redo: ' + str(exc) + '. Start a new set; hold the board still and vary tilt, depth and position.')
    except cv2.error:
        response = dict(result=None, message='Redo: camera fit failed. Retake clear, varied board poses.')
    (directory / 'verdict.json').write_text(json.dumps(response, allow_nan=False))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--fit', type=Path, required=True)
    worker(parser.parse_args().fit)
