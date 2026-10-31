# note: the lab note

Turns one recorded run into the note a scientist keeps. It reads the run folder
(`runs/<run>/events.jsonl` from the hub, `cam0.mp4`, `cam1.mp4` and `timing.jsonl` from capture)
and writes, in the same folder:

- `note.md` and `note.html` (the page has a muted player; click any time to jump there, switch cameras)
- `note.json` (the same note as data)
- `note_frames/problem-<n>-cam<i>.jpg`: one still per camera for up to 3 of the things that went wrong

What is in the note:

- Header: date, start time, length, protocol, who (if they said their name), run id.
- Steps: done, skipped, unsure, not finished or not reached, each with a time and, for done steps,
  how it was known: seen, said, or seen+said.
- Technique: pipette angle per step (highest reading, against the limit, how many readings were
  over), and fresh or reused tip for every draw.
- What was said that matters: volumes, amounts, lot numbers and things noticed. "What's next" is not kept.
- What went wrong: tilt, reused tip, skipped and unsure steps, other coach or angle warnings, a run
  that stopped early. The three most serious get key frames from both cameras.
- Every time links to that moment in each camera's recording.

## Run it

```bash
PY=${BENCH_PY:-python}

$PY -m note build runs/<run>                        # writes the note into the run folder
$PY -m note build some/folder --source a.mp4 --source b.mp4   # loose files, no capture timing
$PY -m note build runs/<run> --post                  # also send the note event (hub, or stdout)

$PY -m note.fakerun runs/demo-fake                   # a scripted run folder to try it
$PY -m note.fakerun runs/demo-real --samples         # same events over two real clips from $BENCH_SAMPLES

$PY -m note serve                                    # port 8705; --runs, --hub, --public-url, --no-watch
$PY -m pytest note/tests -q                          # BENCH_SAMPLES set: also runs the real-clip test
```

With no hub, the note event goes to stdout as one JSON line. Nothing here plays sound.

## Service (port 8705)

| Route | What it does |
| --- | --- |
| `POST /note` | build the note now; body `{"run", "polish", "frames"}`, all optional (no run means the newest) |
| `GET /note/{run}` | the HTML note; `?format=md` or `?format=json` |
| `GET /note/{run}.pdf` | A4 PDF of the saved note, key frames included |
| `GET /note/{run}.zip` | note.html (printable, images inside), note.pdf, note.md, note.json, events.jsonl, timing.jsonl, key frames. No videos |
| `GET /runs/{run}/{file}` | recordings and key frames, so the page's links work (range requests work) |
| `GET /health` | `{"ok": true}` |

The service listens to the hub (`ws://<hub>/ws`). On a `run` stop it waits until the hub has
logged the stop and capture has written its stop row to `timing.jsonl` (10 s at most), builds the
note and posts `{"kind": "note", "detail": {"run", "path", "url"}}`. `url` is the full address.
The app never needs to call `POST /note`.

## How it decides things

- Only events between this run's `run` start and stop count (2 s slack each side). The hub's log
  can hold other runs, and parts keep sending after a stop.
- The protocol comes from the start event's `protocol_data`, else the `protocol` file.
- Step status: the coach's last final status per step. A done step that was seen is timed at the
  camera event that proved it, not when the coach caught up. With no coach events at all the note
  walks the protocol's `expect` evidence itself (events under 0.5 confidence ignored) and marks a
  step skipped when a later step's evidence shows up first.
- Hints (`detail.hint: true`) are never proof: they do not tick steps and do not count as draws for
  the tip check. A step the coach confirmed after a yes is labelled seen+said and timed at the yes.
- Angle readings have no step (the angle part cannot know it); they go to the step the coach had
  as current at that moment.
- Reused tip: a draw from a second tube with no `tip_off`/`tip_on` in between.
- A coach warning within 4 s of one of the note's own tilt, tip or skipped lines repeats it and is
  dropped. Other warnings are quoted word for word, so the angle part's own caveats survive.
- Video times come from capture's `timing.jsonl`: frame `i` of `cam0.mp4` is the i-th frame row for
  camera 0, so dropped frames and late starts are handled. A time is matched to the nearest frame.
  Files with no timing rows are taken to start at the run start. Times past the end hold at the end.

## The Claude wording pass

With `ANTHROPIC_API_KEY` set, the summary, technique and problem lines go to Claude
(`claude-opus-5-5`, change with `BENCH_NOTE_MODEL`) to read more like a notebook. Quotes of the
person, quoted warnings and the protocol's step text are never sent. Each rewrite is checked
against the rule-based line: any number added, dropped or changed, or any capitalised word that
was not there (single letters, which are tube names, must match exactly), and the rule-based line
stays. With no key, or on any error, the note is rule-based and its footer says so.

## Libraries

All were already in the shared environment.

| Library | Licence | Used for |
| --- | --- | --- |
| FastAPI (https://github.com/fastapi/fastapi) | MIT | the HTTP service |
| Uvicorn (https://github.com/encode/uvicorn) | BSD-3-Clause | running it |
| HTTPX (https://github.com/encode/httpx) | BSD-3-Clause | posting the note event |
| websockets (https://github.com/python-websockets/websockets) | BSD-3-Clause | listening to the hub |
| Pydantic (https://github.com/pydantic/pydantic) | MIT | request body |
| PyYAML (https://github.com/yaml/pyyaml) | MIT | reading protocol files |
| OpenCV (https://github.com/opencv/opencv-python) | Apache-2.0 | video length, key frames, the scripted test videos |
| NumPy (https://github.com/numpy/numpy) | BSD-3-Clause | test video frames |
| WeasyPrint 70 (https://github.com/Kozea/WeasyPrint) | BSD-3-Clause | the PDF; needs Pango (LGPL-2.1+, https://gitlab.gnome.org/GNOME/pango) on the host |
| Anthropic Python SDK (https://github.com/anthropics/anthropic-sdk-python) | MIT | the optional wording pass |
| pytest (https://github.com/pytest-dev/pytest) | MIT | tests |
| Poppler `pdftotext` (https://poppler.freedesktop.org/) | GPL-2.0-or-later | tests only, to read the PDF back; the test skips that check without it |

The design follows the earlier draft's note part; the code was written fresh here.

Typed student names from the app's start event (`detail.student`) take precedence over
spoken names in the note header. Blank names keep the existing spoken-name fallback.
Rebuild an older note to update its header; the trainer view does not rewrite notes.

## Notebook exports

The Lab note tab offers **Benchling HTML**, **eLabFTW .eln**, and **Steps CSV**
next to Download PDF. They reuse saved note wording and evidence. If the note is
missing, the service builds it with rules only. Export generation uses local files
and Python's standard library; it makes no external requests and needs no PDF renderer.
The browser downloads through the existing local hub-to-note proxy.

| Note route | Download |
| --- | --- |
| `GET /note/{run}.benchling` | `benchling-entry.html`: open locally, select the rendered entry and paste into a Benchling notebook entry |
| `GET /note/{run}.eln` | `lab-note.eln`: import through eLabFTW's Profile → Import page; select experiment if the destination requires an explicit type |
| `GET /note/{run}.csv` | `steps.csv`: one row per protocol step, UTF-8, with a header |

The HTML contains a results table, note sections and an attachment list. Pasting
HTML does not upload attachments: upload those separately from the .eln archive.
The archive includes entry.html, steps.csv, note.json, available events.jsonl and
timing.jsonl, and referenced key frames contained within the run directory.
Camera and microphone recordings are listed but excluded. Arbitrary run files are
not collected. Archives are buffered in memory, intended for short bench runs.

CSV columns are step number, instruction, status, evidence label, Unix epoch
seconds, seconds from run start, and a JSON map of per-camera offsets in seconds.
Times describe recorded status/evidence events, not inferred step durations.
Missing times are blank; unconfirmed steps and seen/said/seen+said distinctions
remain unchanged. Text beginning with a spreadsheet formula marker (after leading
whitespace) gets an apostrophe prefix in CSV. HTML and note.json preserve that text.

The archive follows the [ELN specification 1.2+20260923](https://github.com/TheELNConsortium/TheELNFileFormat/blob/master/SPECIFICATION.md)
and [RO-Crate 1.2](https://www.researchobject.org/ro-crate/specification/1.2/).
It has one enclosing folder, JSON-LD metadata, an experiment Dataset with HTML text
and `genre: experiment`, and File entities with MIME types, byte sizes and SHA-256.
[eLabFTW's import documentation](https://doc.elabftw.net/import-export.html)
describes .eln imports and type selection. Offline tests check archive structure,
references and payload hashes. Live eLabFTW import and Benchling paste fidelity
have not been tested; these files do not use either vendor's API.

No dependencies were added. Python's [standard library](https://docs.python.org/3/library/)
(PSF-2.0) supplies csv, html, json, hashlib and zipfile. The ELN specification is MIT
licensed; no third-party implementation was copied.

Run the export checks with `$BENCH_PY -m pytest note/tests/test_elnexport.py -q`.
