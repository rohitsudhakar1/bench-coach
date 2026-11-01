"""Serve one or two cameras and a microphone. Run with python -m capture.service."""
import argparse
import asyncio
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
import threading
import signal
import time

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from capture.media import Audio, Camera, MAX_JPEG
from capture.recording import Recorder
from capture.phone import certificate, lan_address, print_phone_url


class Capture:
    def __init__(self, sources, runs=None, fake_mic=False, loop=False, mic_file=None):
        if len(sources) not in (1, 2):
            raise ValueError('one or two sources required')
        self.done = threading.Event()
        for i, source in enumerate(sources):
            if isinstance(source, str) and source.startswith('phone:') and source != f'phone:{i}':
                raise ValueError(f'phone source in slot {i} must be phone:{i}')
        self.cameras = [Camera(i, s, self.submit, self.done, loop) for i, s in enumerate(sources)]
        self.recorder = Recorder(runs or Path(__file__).resolve().parent.parent / 'runs', self.cameras)
        self.audio = Audio(self.submit, self.done, fake_mic, mic_file)
        self.threads = []

    def submit(self, *args):
        self.recorder.submit(*args)

    def start(self):
        epoch = time.monotonic() + 0.05
        jobs = [(cam.read, (epoch,)) for cam in self.cameras if not cam.phone]
        jobs.append((self.audio.run, ()))
        for target, args in jobs:
            thread = threading.Thread(target=target, args=args, daemon=True)
            self.threads.append(thread)
            thread.start()

    def close(self):
        self.done.set()
        self.recorder.stop()
        for thread in self.threads:
            thread.join(timeout=4)


def mjpeg_chunks(camera, done):
    async def generate():
        previous = None
        while not done.is_set():
            frame = camera.latest
            if frame and frame.sequence != previous:
                previous = frame.sequence
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n' +
                       f'X-Frame-Time: {frame.t}\r\nContent-Length: {len(frame.jpeg)}\r\n\r\n'.encode() +
                       frame.jpeg + b'\r\n')
            if camera.ended:
                return
            await asyncio.sleep(0.01)
    return generate()


class StartRequest(BaseModel):
    run: str | None = None


def create_app(capture, cert=None):
    @asynccontextmanager
    async def lifespan(app):
        capture.start()
        try:
            yield
        finally:
            await asyncio.to_thread(capture.close)

    app = FastAPI(lifespan=lifespan)

    def camera(i):
        if not 0 <= i < len(capture.cameras):
            raise HTTPException(404, 'unknown camera')
        return capture.cameras[i]

    @app.get('/cams')
    async def cams():
        return [cam.status() for cam in capture.cameras]

    @app.get('/health')
    async def health():
        return dict(cameras=[cam.status() for cam in capture.cameras], mic_error=capture.audio.error)

    @app.get('/cam/{i}/frame.jpg')
    async def frame(i: int):
        value = camera(i).latest
        if value is None:
            raise HTTPException(503, 'no frame yet')
        return Response(value.jpeg, media_type='image/jpeg', headers={
            'X-Frame-Time': str(value.t), 'Cache-Control': 'no-store'})

    @app.get('/cam/{i}/mjpeg')
    async def mjpeg(i: int):
        return StreamingResponse(mjpeg_chunks(camera(i), capture.done),
                                 media_type='multipart/x-mixed-replace; boundary=frame')

    @app.websocket('/mic')
    async def mic(ws: WebSocket):
        await ws.accept()
        audio = capture.audio
        with audio.lock:
            previous = audio.sequence
        audio.listening.set()
        try:
            while not capture.done.is_set():
                if audio.error:
                    await ws.close(code=1011, reason='microphone unavailable')
                    return
                with audio.lock:
                    pending = [c for c in audio.chunks if c[0] > previous]
                for sequence, t, pcm in pending:
                    await ws.send_json(dict(t=t, n=len(pcm)//2))
                    await ws.send_bytes(pcm)
                    previous = sequence
                await asyncio.sleep(0.005)
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.post('/record/start')
    async def start(body: StartRequest | None = None):
        try:
            return await asyncio.to_thread(capture.recorder.start,
                                            body.run if body and body.run else str(time.time_ns()))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (RuntimeError, FileExistsError) as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(507, str(exc)) from exc

    @app.post('/record/stop')
    async def stop():
        return await asyncio.to_thread(capture.recorder.stop)

    @app.get('/record/status')
    async def status():
        return capture.recorder.status()

    @app.get('/phone')
    async def phone():
        return FileResponse(Path(__file__).with_name('phone.html'), headers={'Cache-Control': 'no-store'})

    if cert:
        @app.get('/phone/certificate')
        async def download_cert():
            return FileResponse(cert, media_type='application/x-x509-ca-cert', filename='Bench-Coach.crt')

    @app.websocket('/phone/{i}')
    async def phone_frames(ws: WebSocket, i: int):
        await ws.accept()
        if i not in (0, 1) or not capture.cameras[i].phone:
            await ws.close(code=1008, reason='slot is not a phone source')
            return
        cam = capture.cameras[i]
        if not cam.claim():
            await ws.close(code=1008, reason='slot already occupied')
            return
        try:
            await ws.send_json(dict(cam=i, ready=True))
            while not capture.done.is_set():
                message = await ws.receive()
                t = time.time()
                if message['type'] == 'websocket.disconnect':
                    break
                if message.get('bytes') is None:
                    await ws.close(code=1003, reason='send binary JPEG')
                    break
                try:
                    frame = await asyncio.to_thread(cam.receive, message['bytes'], t)
                except ValueError as exc:
                    await ws.close(code=1007, reason=str(exc))
                    break
                await ws.send_json(dict(cam=i, t=frame.t, sequence=frame.sequence))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            cam.disconnect()
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', help='USB index, URL, file or phone:0/phone:1; once for one camera, twice for two')
    parser.add_argument('--loop', action='store_true')
    mic = parser.add_mutually_exclusive_group()
    mic.add_argument('--fake-mic', action='store_true')
    mic.add_argument('--mic-file')
    parser.add_argument('--runs', default=str(Path(__file__).resolve().parent.parent / 'runs'))
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8701)
    parser.add_argument('--https', action='store_true')
    parser.add_argument('--https-port', type=int, default=8843)
    parser.add_argument('--phone-address', help='LAN IP for certificate and QR')
    parser.add_argument('--cert-dir', default=str(Path(__file__).parent / '.phone-tls'))
    args = parser.parse_args()
    sources = [int(s) if s.isdecimal() else s for s in (args.source or ['0', '1'])]
    try:
        capture = Capture(sources, args.runs, args.fake_mic, args.loop, args.mic_file)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    import uvicorn
    cert = key = None
    if args.https:
        if args.https_port == args.port:
            parser.error('HTTP and HTTPS ports must differ')
        address = args.phone_address or lan_address()
        cert, key = certificate(args.cert_dir, address)
    app = create_app(capture, cert)
    if not args.https:
        uvicorn.run(app, host=args.host, port=args.port, ws_max_size=MAX_JPEG)
        return
    # Own signals across both listeners so Uvicorn cannot re-raise SIGTERM
    # before the second listener has finished shutting down.
    class ManagedServer(uvicorn.Server):
        def capture_signals(self):
            return nullcontext()

    secure = ManagedServer(uvicorn.Config(app, host='0.0.0.0', port=args.https_port,
        ssl_certfile=str(cert), ssl_keyfile=str(key), lifespan='off',
        ws_max_size=MAX_JPEG, ws_max_queue=1, timeout_graceful_shutdown=2))
    plain = ManagedServer(uvicorn.Config(app, host=args.host, port=args.port,
        ws_max_size=MAX_JPEG, timeout_graceful_shutdown=2))
    def shutdown(signum, frame):
        plain.should_exit = secure.should_exit = True
    previous = {sig: signal.signal(sig, shutdown) for sig in (signal.SIGINT, signal.SIGTERM)}
    thread = threading.Thread(target=secure.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not secure.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('HTTPS listener did not start')
            time.sleep(0.02)
        print_phone_url(address, args.https_port)
        plain.run()
    finally:
        secure.should_exit = True
        thread.join(timeout=5)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    main()
