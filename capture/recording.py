"""A bounded disk worker; starts and stops serialize until all files close."""
import json
from pathlib import Path
import queue
import re
import shutil
import threading
import time
import wave

import cv2
from capture.media import RATE

DISK_RESERVE = 16 * 1024 * 1024


class Recorder:
    def __init__(self, root, cameras):
        self.root, self.cameras = Path(root), cameras
        self.control = threading.Lock()
        self.lock = threading.Lock()
        self.active = None
        self.last = {'recording': False}

    @staticmethod
    def public(state):
        return {k: v for k, v in state.items() if not k.startswith('_')}

    def status(self):
        with self.lock:
            return self.public(self.active) if self.active else dict(self.last)

    def start(self, run):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', run):
            raise ValueError('run must be a simple name of 1 to 100 characters')
        with self.control:
            with self.lock:
                if self.active:
                    raise RuntimeError('already recording')
            if any(c.latest is None for c in self.cameras):
                raise RuntimeError('both cameras need a frame')
            folder = self.root / run
            folder.mkdir(parents=True, exist_ok=True)
            if folder.is_symlink():
                raise ValueError('run folder must not be a symlink')
            names = ['cam0.mp4', 'cam1.mp4', 'mic.wav', 'timing.jsonl']
            if any((folder / name).exists() or (folder / name).is_symlink() for name in names):
                raise FileExistsError('run already contains a recording')
            if shutil.disk_usage(folder).free < DISK_RESERVE:
                raise OSError('disk space below 16 MiB; recording not started')
            writers, codecs = [], []
            audio = timing = None
            try:
                for cam in self.cameras:
                    h, w = cam.latest.image.shape[:2]
                    for codec in ('avc1', 'mp4v'):
                        writer = cv2.VideoWriter(str(folder / f'cam{cam.index}.mp4'),
                                                cv2.VideoWriter_fourcc(*codec), cam.fps, (w, h))
                        if writer.isOpened():
                            break
                        writer.release()
                    else:
                        raise OSError('MP4 encoder unavailable')
                    writers.append(writer)
                    codecs.append(codec)
                audio = wave.open(str(folder / 'mic.wav'), 'wb')
                audio.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
                timing = (folder / 'timing.jsonl').open('x')
            except Exception:
                for writer in writers:
                    writer.release()
                for resource in (audio, timing):
                    if resource:
                        resource.close()
                # Only files owned by this failed setup are removed; hub files stay.
                for name in names:
                    (folder / name).unlink(missing_ok=True)
                raise
            state = dict(run=run, path=str(folder), started=time.time(), recording=True,
                         dropped=0, error=None, codecs=codecs,
                         _queue=queue.Queue(maxsize=256), _stop=threading.Event())
            state['_thread'] = threading.Thread(target=self.write,
                args=(state, writers, audio, timing), name='capture-writer', daemon=True)
            with self.lock:
                self.active = state
                result = self.public(state)
            state['_thread'].start()
            return result

    def submit(self, kind, index, value):
        with self.lock:
            state = self.active
            if state and not state['_stop'].is_set():
                try:
                    state['_queue'].put_nowait((kind, index, value))
                except queue.Full:
                    state['dropped'] += 1

    def write(self, state, writers, audio, timing):
        counts, samples = [0, 0], 0
        def row(value):
            timing.write(json.dumps(value) + '\n')
            timing.flush()
        try:
            row(dict(kind='start', t=state['started'], fps=[c.fps for c in self.cameras],
                     audio_rate=RATE, codecs=state['codecs']))
            while not state['_stop'].is_set() or not state['_queue'].empty():
                if shutil.disk_usage(state['path']).free < DISK_RESERVE:
                    raise OSError('recording stopped: disk space below 16 MiB')
                try:
                    kind, index, value = state['_queue'].get(timeout=0.05)
                except queue.Empty:
                    continue
                if kind == 'frame':
                    writers[index].write(value.image)
                    row(dict(kind='frame', cam=index, frame=counts[index], t=value.t,
                             sequence=value.sequence, media_time=value.media_time))
                    counts[index] += 1
                else:
                    t, pcm = value
                    audio.writeframesraw(pcm)
                    row(dict(kind='audio', sample=samples, n=len(pcm)//2, t=t))
                    samples += len(pcm)//2
        except Exception as exc:
            state['error'] = str(exc)
        finally:
            with self.lock:
                state['_stop'].set()
                state['dropped'] += state['_queue'].qsize()
            for close in [w.release for w in writers] + [audio.close]:
                try:
                    close()
                except Exception as exc:
                    state['error'] = state['error'] or str(exc)
            state['stopped'] = time.time()
            try:
                row(dict(kind='stop', t=state['stopped'], dropped=state['dropped'],
                         error=state['error'], frames=counts, samples=samples))
            except Exception as exc:
                state['error'] = state['error'] or str(exc)
            try:
                timing.close()
            except Exception as exc:
                state['error'] = state['error'] or str(exc)
            with self.lock:
                state['recording'] = False
                self.last = self.public(state)
                self.active = None

    def stop(self):
        with self.control:
            with self.lock:
                state = self.active
                if state:
                    state['_stop'].set()
            if state:
                state['_thread'].join()
            return self.status()
