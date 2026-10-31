"""H.264 encoding with bounded threading and a per-recording native lifetime."""
import subprocess
import tempfile


class H264Writer:
    def __init__(self, path, fps, size):
        self.errors = tempfile.TemporaryFile()
        self.process = None
        try:
            self.process = subprocess.Popen([
                'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                '-f', 'rawvideo', '-pixel_format', 'bgr24', '-video_size',
                f'{size[0]}x{size[1]}', '-framerate', str(fps), '-i', 'pipe:0',
                '-an', '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast',
                '-tune', 'zerolatency', '-pix_fmt', 'yuv420p', str(path)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.errors)
        except Exception:
            self.errors.close()
            raise

    def isOpened(self):
        return self.process is not None and self.process.poll() is None

    def write(self, image):
        self.process.stdin.write(memoryview(image).cast('B'))

    def release(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
            try:
                code = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise OSError('H.264 encoder did not finish within 15 seconds')
            if code:
                raise OSError(f'H.264 encoder exited with status {code}')
        finally:
            self.errors.close()
