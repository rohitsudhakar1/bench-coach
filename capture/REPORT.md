# Capture report

No event-contract changes proposed.

Built on October 30, 2026 in `capture`, branch `capture`.
All changes are under `capture/` and are uncommitted. The main checkout and earlier
scienceclaw draft were read only. No packages were installed, no models downloaded,
no hardware opened, and no sound played.

Implemented two independent latest-frame readers, shared-clock file pacing and
looping, USB/RTSP/HTTP inputs, phone JPEG input with HTTPS and terminal QR, hardware
mic input, fake silence and first-listener WAV playback. Recording writes repo-root
`runs/<run>/cam0.mp4`, `cam1.mp4`, `mic.wav` and contract timing rows. Existing hub
run folders are accepted without touching their event files. The default port is
8701; all actual socket tests used available ports within 8800–8899.

The rebuild carries forward the draft's fixes: H.264-first encoding with an explicit
fallback result, file loops, stable dimensions across phone reconnects, bounded
recording queues, idempotent concurrent stops, and recording errors in the result.
Live camera failures retry after 0.5 seconds while the other source continues.
The disk worker checks the 16 MiB reserve even with no incoming data, closes each
resource independently on errors, and records why it stopped. Phone MJPEG readers
wait through disconnects. HTTP and HTTPS share the same capture instance.

Validation command:

```sh
$BENCH_PY -m pytest capture/tests -q -p no:cacheprovider --basetemp=capture/.test-tmp
```

Result: **18 passed in 8.08 seconds**. Coverage includes generated video recording
and decode counts, all three provided left/right footage pairs, file loop/EOF,
wall-clock headers, PCM sample offsets, first-listener WAV pacing, silent tails,
mic-error socket close, phone slot conflicts/malformed images/staleness/size changes,
reconnect continuation, actual multipart HTTP, trusted TLS/WSS, certificate reuse
and key permissions, concurrent stops, low disk with idle inputs, writer exceptions,
encoder fallback and failed-start cleanup, bounded queue overload, and process
shutdown with a recording active. The overload test submitted 300 audio items while
a video write was blocked and reported exactly 44 drops from the 256-item queue.
The failed-camera test checked at least 0.49 seconds between reopen attempts and
continued frames from the other camera.

A standalone subprocess using both supplied `414-445` clips passed the delivery
check and recorded 38 left frames, 37 right frames and 20,160 audio samples with
zero reported drops. Both codecs were `avc1`. Start-to-final-close was 1.495 seconds,
including file finalization; the test waited 1.25 seconds before requesting stop.
These are short local checks, not throughput or hardware-sync benchmarks.
OpenCV was 5.0.0 and Uvicorn was 0.54.0 in the shared environment.

The first TLS process test exposed Uvicorn's SIGTERM re-raise interrupting outer
cleanup before the second listener shut down. The dual-listener path now owns
signal handling across both servers, and the test verifies clean process exit and
both listener ports closed. All started subprocesses were terminated and waited
for by PID. No capture server remains running.

The phone page JavaScript passed `node --check`. `git diff --check` passed.
Physical iPhone/Android camera access, certificate installation and QR scanning
were not tested. Neither were real USB devices, Pi streams or physical microphone
capture. Tests inject device and disk failures; they do not disconnect a real
camera or fill the disk. Browser playback is expected with H.264 but was not
visually verified; the fallback may not play in browsers. Network timestamps are
arrival times, not exposure sync. The WAV resampler uses linear interpolation;
variable-frame-rate file alignment and audio gap insertion are not implemented.
A catastrophic disk failure can still prevent final headers or timing rows.

The README includes commands, phone trust steps, API details, limits, dependency
source URLs and licences. No hub events are emitted because the unchanged contract
assigns run events to the app.

Required history check, run in this worktree:

```sh
git log --format='%h %an %aI %cI' main..HEAD
```

Output: empty (zero lines; this branch has no commits beyond main).
No commits, Git identity changes, date overrides or history rewrites were made.
The lead can review and commit `capture/` with normal current timestamps.
