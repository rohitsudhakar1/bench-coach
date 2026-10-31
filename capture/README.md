# Capture

Capture serves two camera feeds and one microphone on port 8701. It runs without the
hub or API keys. All timestamps use the laptop's `time.time()`. Run commands from the
repository root with the shared Python environment.

```sh
# USB cameras and the default hardware microphone
$BENCH_PY -m capture.service --source 0 --source 1

# Silent file playback; files repeat instead of freezing before Start
$BENCH_PY -m capture.service \
  --source $BENCH_SAMPLES/lsv_mv/CP-Left-Front-414-445.mp4 \
  --source $BENCH_SAMPLES/lsv_mv/CP-Right-Front-414-445.mp4 \
  --loop --fake-mic
```

Pass exactly two `--source` arguments, in camera-slot order. Each can be a USB index,
video file, RTSP/HTTP stream URL, or `phone:0`/`phone:1` in the matching slot. Without
arguments, sources are USB 0 and 1. For Pi cameras use the actual streaming server
addresses, such as `rtsp://192.168.1.51:8554/bench` and
`http://192.168.1.52:8080/stream.mjpg`. Capture does not install a server on the Pi.

Each camera owns a reader thread and retains only its latest frame. Live read/open
failures release the device and retry after 0.5 seconds. A failed network operation
can take up to its OpenCV timeout (2 seconds to open, 1 second to read) before that
retry delay. `/cams` shows `reconnecting`; the other camera keeps recording.
Files play at their reported frame rate against a common monotonic start clock.
Late frames are skipped to catch up. `--loop` restarts decoding while `media_time`
keeps increasing. Without it, EOF sets `ended` and leaves the last JPEG available.
The first frames of the supplied synchronized files are assumed to align.

`--fake-mic` emits 20 ms of silence per chunk. `--mic-file question.wav` accepts
16-bit PCM WAV, mixes channels to mono, and linearly resamples to 16 kHz. It sends
silence until the first `/mic` listener, plays the file once in real time, then sends
silence. A later listener joins at the current position. These flags are mutually
exclusive and open no audio hardware. Neither plays sound through speakers.
Without them, sounddevice opens the default input at 16 kHz, mono, signed int16.
Grant terminal microphone permission on macOS. Input setup failure closes `/mic`
with code 1011 while camera endpoints remain available; `/health` reports the error.

## API and recording

The event contract remains `docs/EVENTS.md`. Capture emits no hub events: the app owns
`run` events. It needs no hub connection or event-delivery fallback.

| Endpoint | Result |
| --- | --- |
| `GET /cams` | List of camera `id`, `source`, `fps`, `t`, `sequence`, `live`, `ended`, `error`, `reconnecting` |
| `GET /cam/{i}/frame.jpg` | Latest JPEG; `X-Frame-Time` epoch header, no caching; 503 before a first frame, 404 for an unknown slot |
| `GET /cam/{i}/mjpeg` | Multipart JPEG stream; each part carries `X-Frame-Time` and `Content-Length`; waits through reconnects |
| `WS /mic` | JSON text `{t,n}`, then binary little-endian signed int16 mono PCM at 16000 Hz; `n` is samples, byte length is `2*n` |
| `POST /record/start` | Optional JSON `{"run":"demo-1"}`; otherwise a time-based name |
| `POST /record/stop` | Drains queued data, closes files and returns the final result; concurrent/repeated stops return the same result until a new start |
| `GET /record/status` | Active or last result, including `recording`, `run`, `path`, `started`, `stopped` after close, `codecs`, `dropped`, `error` |
| `GET /health` | Camera status and microphone setup error |

```sh
curl -fsS -X POST http://127.0.0.1:8701/record/start \
  -H 'Content-Type: application/json' -d '{"run":"demo-1"}'
curl -fsS -X POST http://127.0.0.1:8701/record/stop
```

The default output is repository-root `runs/<run>/`, independent of the shell's
working directory. `--runs PATH` overrides that root. The hub may create the run folder
and `events.jsonl` first. Capture preserves those files and refuses to overwrite any
existing `cam0.mp4`, `cam1.mp4`, `mic.wav` or `timing.jsonl`. Run names allow letters,
digits, underscore, dot and hyphen, start with a letter/digit, and are at most 100
characters. Invalid names return 422; conflicting starts return 409; disk/setup
I/O failures return 507. Both cameras must have a frame before recording starts.

Video encoding tries H.264 (`avc1`) first and falls back to `mp4v`. The returned
`codecs` disclose the choice; browser playback of the fallback is not assured.
Frames keep the first delivered dimensions across reconnections, resizing changes
and rounding odd dimensions down to even pixels. Mount phones before starting.
MP4 files use nominal camera frame rates. WAV files contain received samples only.
Use `timing.jsonl` to map these media indices to the shared clock:

```json
{"kind":"frame","cam":0,"frame":0,"t":1793400000.1,"sequence":42,"media_time":1.4}
{"kind":"audio","sample":0,"n":320,"t":1793400000.12}
```

`media_time` is null for live sources. Start rows include `t`, `fps`, `audio_rate`
and `codecs`; stop rows include `t`, `dropped`, `error`, `frames` and `samples`.
A bounded 256-item queue keeps disk writes off camera/audio threads. Full queues
drop incoming recording items and count them. Slow microphone listeners can miss
chunks older than the 100-chunk buffer; timestamps expose those gaps.

The disk worker checks free space before writes and at least every 50 ms while idle.
At less than 16 MiB free it stops accepting data, discards queued items with a drop
count, and closes all files while reserve space remains. Status, stop results and
the timing stop row report why. Starting below the reserve is refused. Other
processes can still consume the reserve between checks. A completely full or failed
disk can prevent final metadata writes; close errors remain in the status result.

## Phone camera

```sh
$BENCH_PY -m capture.service \
  --source phone:0 --source phone:1 --fake-mic --https
```

`--https` adds a LAN listener on port 8843, while HTTP remains at localhost:8701 for
existing consumers. Capture prints a LAN `/phone` URL and terminal QR code. The
phone and laptop must share a reachable network. Use `--phone-address LAN-IP` if
auto-detection selects the wrong interface. `--https-port` changes the TLS port.
The service is unauthenticated, including recording controls; use a trusted LAN.

1. Scan the QR code or open the printed HTTPS URL on the phone.
2. Trust the laptop's self-signed certificate if camera access is blocked. The public
   certificate is downloadable from `/phone/certificate`; the private key is never
   served. On iPhone, install the downloaded certificate profile, then enable its
   full trust in Settings → General → About → Certificate Trust Settings. If the
   browser cannot download it, transfer the public `.crt` from the laptop. On Android,
   use the device's certificate installation settings if the browser warning bypass
   is insufficient. Managed devices may forbid trust installation; menus vary.
3. Choose the slot matching `phone:0` or `phone:1`, tap Start camera, and allow access.
   Confirm its frame number advances and it shows the intended bench view.
4. Leave the phone powered, unlocked and on this page. Stop the recording before
   stopping the camera. Stop releases camera tracks; page exit does too.

Certificates last 30 days and are reused per IP in ignored `capture/.phone-tls/`.
`--cert-dir` changes that directory. Private keys are mode 0600. After expiry, stop
capture, remove that IP's certificate/key pair and restart; trust the new certificate.
Never transfer a `.key` file. Remove the phone's trust profile after the demo.

The page requests the rear camera and sends JPEGs with at most 1280 pixels on either
side, without upscaling, at up to 10 fps. It waits for each acknowledgement before
sending another frame, and retries disconnects after two seconds. The server rejects
duplicate senders, wrong slots, text, invalid JPEGs, payloads over 2 MiB, and images
over 1920 pixels on either side. Disconnected or three-second-stale feeds are not
live. Reconnects preserve sequence numbers and recording dimensions. Phone audio
is not used. Timestamps record laptop arrival, not phone exposure time.

## Checks and limits

Use ports 8800–8899 for isolated checks, for example `--port 8881 --https-port 8844`.

```sh
curl -fsS http://127.0.0.1:8881/cams
$BENCH_PY -m capture.check --url http://127.0.0.1:8881
$BENCH_PY -m pytest capture/tests -q -p no:cacheprovider \
  --basetemp=capture/.test-tmp
```

The delivery check verifies advancing frames, recent timestamps and valid PCM, not
volume; silence passes. Tests use generated files, simulated failures, fake phones,
and the supplied read-only sample footage when present. Live tests use free check
ports and stop their subprocesses by PID, including on failure. No hardware or
speaker is opened. Tests include trusted TLS/WSS; no TLS verification bypass.

Physical USB cameras, Pi/network streams, microphone capture and iPhone/Android
browser capture still need bench testing. Hardware exposure synchronization and
variable-frame-rate media alignment are not provided. Network buffering affects
arrival timestamps. Backend USB calls may block beyond shutdown's join timeout;
reader threads are daemons so they do not hold process exit. Recording overload and
missed input cannot be recovered. The service reports loss; it cannot certify that
a recorded experiment is complete.

## Dependencies

No packages were added. `requirements.txt` lists direct and test dependencies used
from the shared environment. Python standard library code uses the PSF licence.
No Labprint source code was read or imported; supplied footage was read in place.

| Source | Licence | Use |
| --- | --- | --- |
| https://github.com/fastapi/fastapi | MIT | HTTP and websocket routes |
| https://github.com/encode/starlette | BSD-3-Clause | Responses, lifecycle and test client |
| https://github.com/pydantic/pydantic | MIT | Request validation |
| https://github.com/encode/uvicorn | BSD-3-Clause | HTTP/TLS servers |
| https://github.com/opencv/opencv | Apache-2.0 | Camera reads, JPEG and MP4 encoding |
| https://github.com/opencv/opencv-python | MIT for wheel packaging | Python distribution |
| https://ffmpeg.org | LGPL-2.1-or-later or GPL, build dependent | OpenCV video backend |
| https://github.com/numpy/numpy | BSD-3-Clause | Image/audio arrays and sample conversion |
| https://github.com/spatialaudio/python-sounddevice | MIT | Microphone input |
| https://www.portaudio.com | MIT | Native audio input backend |
| https://github.com/lincolnloop/python-qrcode | BSD-3-Clause | Terminal QR code |
| https://www.openssl.org | Apache-2.0 for OpenSSL 3+; older builds have OpenSSL/SSLeay terms | Self-signed TLS certificate through installed CLI |
| https://github.com/encode/httpx | BSD-3-Clause | Delivery check and tests |
| https://github.com/python-websockets/websockets | BSD-3-Clause | Delivery check and WSS tests |
| https://github.com/pytest-dev/pytest | MIT | Tests |

## Recording memory

H.264 recording uses the installed `ffmpeg` executable with `libx264`, two encoder
threads per camera and zero lookahead. Each recording owns two encoder processes;
Stop drains the queue, closes their input and waits for them to exit. This releases
native encoder allocations between runs. If ffmpeg is absent, OpenCV's `mp4v`
encoder is used; that fallback may not play in every browser. An installed ffmpeg
without libx264 reports a recording error. Install ffmpeg with libx264 for H.264.
The disk queue holds at most 256 items and 32 MiB of image/JPEG/PCM payloads;
overload drops incoming items and records the drop count. Failed writers discard
their remaining queue immediately. Timing rows still stream directly to disk.

Memory regression (ten three-second recordings; skips without sample footage):

```sh
"$BENCH_PY" -m pytest capture/tests/test_memory.py -q -s
"$BENCH_PY" -m capture.leak_probe --output runs/capture-memory
"$BENCH_PY" -m capture.note_leak_probe runs/capture-memory
```

The probe uses [psutil](https://github.com/giampaolo/psutil), BSD-3-Clause, to measure
current RSS, including owned encoder PIDs, and Python's tracemalloc to compare
allocations. FFmpeg and [x264](https://www.videolan.org/developers/x264.html) provide
the H.264 subprocess encoder; x264 is GPL-2.0-or-later (commercial licensing is
also available), and linking it makes the FFmpeg build GPL. Neither is vendored.
See [LEAK-REPORT.md](LEAK-REPORT.md) for measurements and remaining limits.
