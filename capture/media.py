"""Latest-frame sources and real-time microphone delivery."""
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import threading
import time
import wave

import cv2
import numpy as np

RATE = 16000
SAMPLES = 320
MAX_JPEG = 2 * 1024 * 1024


@dataclass(frozen=True)
class Frame:
    t: float
    sequence: int
    image: np.ndarray
    jpeg: bytes
    media_time: float | None = None


class Camera:
    def __init__(self, index, source, publish, done, loop=False):
        self.index, self.source = index, source
        self.publish, self.done, self.loop = publish, done, loop
        self.file = isinstance(source, str) and Path(source).is_file()
        self.phone = source == f"phone:{index}"
        self.latest = None
        self.fps = 10.0 if self.phone else 30.0
        self.error = "waiting for phone" if self.phone else "opening"
        self.ended = False
        self.connected = False
        self.sequence = 0
        self.size = None
        self.lock = threading.Lock()

    def status(self):
        frame = self.latest
        stale = frame is None or time.time() - frame.t > 3
        return dict(id=self.index, source=self.source, fps=self.fps,
                    t=frame.t if frame else None,
                    sequence=frame.sequence if frame else None,
                    live=not stale and not self.error and not self.ended,
                    ended=self.ended, error=self.error,
                    reconnecting=not self.file and (bool(self.error) or stale))

    def deliver(self, image, t, media_time=None):
        # Keep dimensions stable across reconnects for an already-open MP4 writer.
        h, w = image.shape[:2]
        if self.size is None:
            self.size = (w - w % 2, h - h % 2)
        if (w, h) != self.size:
            image = cv2.resize(image, self.size)
        ok, jpeg = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise ValueError("JPEG encoding failed")
        frame = Frame(t, self.sequence, image, jpeg.tobytes(), media_time)
        self.sequence += 1
        self.latest, self.error = frame, None
        self.publish('frame', self.index, frame)
        return frame

    def claim(self):
        with self.lock:
            if self.connected:
                return False
            self.connected = True
            return True

    def disconnect(self):
        with self.lock:
            self.connected = False
            self.error = "reconnecting: phone disconnected"

    def receive(self, jpeg, t):
        if not 0 < len(jpeg) <= MAX_JPEG or not jpeg.startswith(b'\xff\xd8'):
            raise ValueError("expected JPEG up to 2 MiB")
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None or min(image.shape[:2]) < 2 or max(image.shape[:2]) > 1920:
            raise ValueError("invalid JPEG or dimensions outside 2..1920")
        return self.deliver(image, t)

    def open_source(self):
        if isinstance(self.source, str) and '://' in self.source:
            return cv2.VideoCapture(self.source, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 2000,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1000])
        return cv2.VideoCapture(self.source)

    def read(self, epoch):
        cap = None
        position = 0
        try:
            while not self.done.is_set():
                try:
                    if cap is None:
                        cap = self.open_source()
                        if not cap.isOpened():
                            raise OSError("cannot open source")
                        fps = cap.get(cv2.CAP_PROP_FPS)
                        self.fps = float(fps) if np.isfinite(fps) and 0 < fps <= 240 else 30.0
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    ok, image = cap.read()
                    if not ok and self.file and self.loop and position:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ok, image = cap.read()
                    if not ok:
                        if self.file:
                            self.ended = True
                            return
                        raise OSError("camera read failed")
                    media = position / self.fps if self.file else None
                    position += 1
                    if self.file:
                        delay = epoch + media - time.monotonic()
                        if self.done.wait(max(0, delay)):
                            return
                        if delay < -1 / self.fps:
                            continue
                    self.deliver(image, time.time(), media)
                except Exception as exc:
                    self.error = ("file failed: " if self.file else "reconnecting: ") + str(exc)
                    if cap is not None:
                        cap.release()
                        cap = None
                    if self.file or self.done.wait(0.5):
                        return
        finally:
            if cap is not None:
                cap.release()


def wav_pcm(path):
    with wave.open(str(path), 'rb') as source:
        if source.getsampwidth() != 2:
            raise ValueError("mic file must be a 16-bit PCM WAV")
        rate, channels = source.getframerate(), source.getnchannels()
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2')
    samples = samples.reshape(-1, channels).astype(np.float64).mean(axis=1)
    if len(samples) and rate != RATE:
        samples = np.interp(np.arange(round(len(samples) * RATE / rate)) * rate / RATE,
                            np.arange(len(samples)), samples)
    return np.clip(np.rint(samples), -32768, 32767).astype('<i2').tobytes()


class Audio:
    def __init__(self, publish, done, fake=False, mic_file=None):
        self.publish, self.done, self.fake = publish, done, fake
        self.pcm = wav_pcm(mic_file) if mic_file else None
        self.listening = threading.Event()
        self.lock = threading.Lock()
        self.chunks = deque(maxlen=100)
        self.sequence = 0
        self.error = None
        self.stream = None

    def emit(self, pcm):
        t = time.time()
        with self.lock:
            self.sequence += 1
            self.chunks.append((self.sequence, t, pcm))
        self.publish('audio', 0, (t, pcm))

    def run(self):
        if self.fake or self.pcm is not None:
            offset = 0
            deadline = time.monotonic()
            while not self.done.is_set():
                chunk = b''
                if self.pcm is not None and self.listening.is_set():
                    chunk = self.pcm[offset:offset + SAMPLES * 2]
                    offset += len(chunk)
                self.emit(chunk.ljust(SAMPLES * 2, b'\0'))
                deadline += SAMPLES / RATE
                # Do not burst stale silence after a scheduling pause.
                deadline = max(deadline, time.monotonic())
                self.done.wait(max(0, deadline - time.monotonic()))
            return
        try:
            import sounddevice as sd
            def callback(data, frames, timing, status):
                self.emit(bytes(data))
            with sd.RawInputStream(samplerate=RATE, channels=1, dtype='int16',
                                   blocksize=SAMPLES, callback=callback) as stream:
                self.stream = stream
                self.done.wait()
        except Exception as exc:
            self.error = str(exc)
