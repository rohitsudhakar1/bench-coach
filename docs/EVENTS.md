# How the parts talk

Every process runs on this laptop (or the GPU box) and talks over HTTP and websockets.

## Clock
All times are Unix epoch seconds as floats (`time.time()` on the capture machine). Every frame,
audio chunk and event carries one.

## Ports
| Part | Port | Serves |
| --- | --- | --- |
| app (hub + web page) | 8700 | `POST /events` (one event or a list), `GET /events?since=<t>`, `WS /ws` (every event as it arrives), `GET /state` (current step, warnings), the web page at `/` |
| capture | 8701 | `GET /cams` (list), `GET /cam/{i}/frame.jpg` (latest frame; header `X-Frame-Time`), `GET /cam/{i}/mjpeg` (live stream), `WS /mic` (16 kHz mono 16-bit PCM chunks, each preceded by a JSON header `{t, n}`), `POST /record/start`, `POST /record/stop` |
| vision | 8702 | `GET /health`; posts events to the hub |
| angle | 8703 | `GET /health`; posts events to the hub |
| coach | 8704 | `GET /health`; posts events to the hub; speaks through the laptop speaker |
| note | 8705 | `POST /note` (builds the note for a run), `GET /note/{run}` |

Any part can run without the others. With no hub, a part logs its events to stdout as JSON lines.
Use `--source video.mp4` (or two of them) instead of live cameras when testing.

## Event
One JSON object:

```json
{"t": 1793400000.12, "source": "vision", "kind": "draw", "object": "tube A",
 "hand": "right", "step": null, "confidence": 0.8, "detail": {}}
```

| kind | sent by | detail |
| --- | --- | --- |
| `objects` | vision | `{"cam": 0, "boxes": [{"label", "box": [x1,y1,x2,y2], "score"}]}` (at most 2 per second per camera) |
| `hand_touch` | vision | `{"cam": 0}` with `object` = what the hand touched |
| `tip_on`, `tip_off` | vision | `{}` (a fresh tip was put on or ejected) |
| `draw`, `dispense` | vision | `{"cam": 0}` with `object` = the tube or plate |
| `angle` | angle | `{"deg": 12.5, "ok": true, "limit": 20}` |
| `speech` | coach | `{"text": "what the person said"}` |
| `step` | coach | `{"index": 2, "status": "done" or "current" or "skipped" or "unsure", "text": "..."}` |
| `warning` | coach or angle | `{"text": "plain words, said out loud"}` |
| `say` | coach | `{"text": "what the coach said out loud"}` |
| `run` | app | `{"status": "start" or "stop", "run": "<id>", "protocol": "<file>"}` |
| `note` | note | `{"run": "<id>", "path": "..."}` |

`source` is the part's name. `confidence` is 0 to 1, or null. `step` is the protocol step index
the event belongs to, or null if unknown.

## Protocol file
YAML in `protocols/`. Each step has `text` and what counts as evidence (`expect`: event kinds and
objects), and optionally `min_seconds` and `technique` checks. See `protocols/pipetting-demo.yaml`.

## Decisions (lead, Oct 30 evening)
- One run folder for everything: `runs/<run>/` at the repo root. The app writes `events.jsonl`
  there, capture writes `cam0.mp4`, `cam1.mp4`, `mic.wav` and `timing.jsonl` there, and the note
  part writes `note.md`, `note.html`, `note.json` and key frames there. Parts take `--runs` to
  override; the default is the repo-root `runs/`.
- Capture's `timing.jsonl` is the timing format: one JSON row per frame
  (`{"kind": "frame", "cam", "frame", "t", "sequence", "media_time"}`), per audio chunk
  (`{"kind": "audio", "sample", "n", "t"}`) and start/stop rows. Other parts read this file.
- The note part owns building the note: it builds it on the hub's `run` stop event. The app
  never calls `POST /note`; it shows the note when the `note` event arrives.
- The `note` event's detail is `{"run", "path", "url"}`; `url` is the full address of the note.
- Hints (accepted from hintconfirm): `draw` and `dispense` events from `vision --new-draw` carry
  `detail.hint: true`; no part may treat a hint as proof. In hint mode the coach's `step` events
  carry `detail.question` (the open question, or null once it closes). A step confirmed by a yes
  after a hint has `detail.evidence: "hint+speech"` and `detail.source: "seen+said"`, next to
  `vision`/`seen` and `speech`/`said`.

## Classroom benches

The classroom extension adds one envelope field: `bench`, a nonempty string ID
(default `"1"` when absent). The hub writes it on every stored and delivered event,
including run starts/stops, protocol selections and notes. Old logs belong to bench 1.
No event kind or clock changes. Run IDs remain unique across benches and all files
stay in `runs/<run>/`.

With `--benches N`, one hub serves `/benches/<id>/` for IDs `1` through `N`.
Append the existing endpoint paths to that prefix: `/events`, `/ws`, `/state`,
`/run/start`, `/run/stop`, `/config`, and the other bench controls. The launcher
passes this scoped hub URL to each bench's producers. Events posted there without
`bench` inherit that bench; an explicit different ID is rejected. Its history and
websocket contain only that bench. This keeps older producers isolated without
changing their event builders. Undelivered producer stdout retains its old envelope;
the launcher's per-bench process log identifies its bench.

Root `POST /events` accepts explicit bench IDs and routes a batch to the appropriate
states/logs; unknown IDs reject the whole batch before publishing. Other original
root endpoints, including `GET /events` and `WS /ws`, continue to describe bench 1.
`GET /class/state` lists every configured bench for the live grid at `/class`.

For bench number B, service ports are the base port plus `6 * (B - 1)` plus the
existing capture/vision/angle/coach/note offset. There is only one hub at the base
port; the later hub slots are unused. The default one-bench ports are unchanged.
