# coach

The voice lab partner. It follows the protocol from the events the other parts send, listens to
the person, and answers in short lines: the next step when one is done, a warning when something
goes wrong, an answer when they ask something.

It makes no sound by default. Lines are printed to stderr and sent to the hub as `say` events.
`--speak-to DIR` writes each line as an audio file in DIR and plays nothing. `--speak` says them
out loud through the laptop speaker; never use it in tests.

## Run it

From the repo root, with `BENCH_PY` set to the shared Python:

```bash
# replay the scripted demo, no hub, no mic
$BENCH_PY -m coach --events coach/samples/demo-events.jsonl --no-hub --port 0

# live: follow the hub on 8700, listen to capture's mic on 8701, health on 8704
$BENCH_PY -m coach

# the laptop mic instead of capture's
$BENCH_PY -m coach --mic local

# alone, listening to the sound track of a video or audio file
$BENCH_PY -m coach --source clip.mp4 --no-hub --port 0

# hint mode, for vision --new-draw
$BENCH_PY -m coach --hints

# tests (about 45 s; the Whisper ones skip if the model is not on disk)
$BENCH_PY -m pytest coach/tests -q
```

Other flags: `--protocol FILE` (a path or a name in `protocols/`, default `pipetting-demo.yaml`),
`--hub URL`, `--no-hub` (print events as JSON lines), `--speed 1` (replay in real time; 0 is no
waiting), `--capture ws://...`, `--whisper-model small.en`, `--gap 3` (seconds between lines),
`--strong 0.6` (confidence that counts as solid), `--no-llm`, `--model claude-haiku-5-5`,
`--voice gemini-live`, `--port 8704` (0 for none), `--quiet`.

## What it does

Events in: the hub's `WS /ws` (one event or a list per message), or a JSONL file with
`--events`. Its own events coming back from the hub are ignored. The app's `run` start event
resets it and loads the protocol named in the event.

Events out: `step`, `warning`, `say`, `speech` as in `docs/EVENTS.md`, posted to the hub's
`POST /events`, or printed as JSON lines when the hub is off or down. `GET /health` and
`GET /state` (steps, statuses, evidence, tip, open question) on `--port`.

### Steps (`brain.py`)
- Each step is `pending`, `current`, `done`, `skipped` or `unsure`.
- An event matching the current step marks it done. It is unsure when the confidence is under
  `--strong`, or when vision only said "tube" for a step that names tube A (vision cannot tell
  tubes apart yet). Either way the coach moves on and says the next step.
- Strong evidence for a step up to three ahead marks the ones in between skipped and warns.
- Repeating the step just finished (a second tip, dispensing again to mix) is not a skip; it
  upgrades the step to done if it was unsure.
- Done steps say where the evidence came from: `detail.evidence` is `vision`, `speech` or
  `hint+speech`, and `detail.source` is `seen`, `said` or `seen+said`.
- The last line is "All steps done. Nice work." only when every step is done. Otherwise it says
  which to check: "Run finished. Check steps 4, 5 and 7."

### Warnings
- The same tip used for two tubes ("Same tip used twice. Change it before tube C."), drawing
  with the tip ejected, a skipped step, the pipette tilted past the step's `angle_max_deg`
  (or `ok: false` from the angle part), and a step done faster than its `min_seconds`.
- Spoken tip steps count for the tip rules too: "tip's on" starts a fresh tip.
- When one event causes a skip of tip steps and a tip warning, only the tip warning is said.
- A `warning` from another part is said word for word and not posted again, so the angle
  part's own caveat ("rough camera estimate") reaches the person.
- Each warning topic has an 8 s cooldown.

### What the person says (`heard.py`)
- Finished actions count as evidence for the current step, labelled said: "tip's on", "fresh
  tip", "I drew from tube A", "added it to B", "mixed three times", "ejected the tip", "done",
  "next". Common Whisper spellings are fixed: tube bee, tube see, tube eh, "added it to be",
  "tips on". Questions, plans and negations ("I'm going to...", "I haven't...") never count.
  Speech never skips ahead.
- If a camera saw the same action with a different tube since the last step began, the coach
  asks instead: "The camera saw tube C, you said tube A. Which one?"
- Questions answered from the protocol and the event log: "what's next", "how much do I add",
  "did I add tube C", "did I change the tip", "which step am I on", "repeat that".
- With `ANTHROPIC_API_KEY` set, Claude (`claude-haiku-5-5`) words the answers to questions,
  given the protocol, step status, last 30 events and the rule answer. It never sees statements
  or yes/no replies and never marks steps. Any error or a call over 4 s gives the rule answer.

### Hint mode (`--hints`)
Vision's `--new-draw` events carry `detail.hint: true`. They are never evidence: they do not
tick steps, count for tips or answer "did I add". With `--hints`, a hint whose kind fits the
current step makes the coach ask "Did you just draw from tube A?" (the tube comes from the
protocol). Yes, yeah, done or a matching statement ticks the step as seen+said. No, or 10 s of
silence, leaves it pending. A new fitting hint can ask again 15 s after the no or the timeout,
up to three questions per step, never two within 8 s; early hints are dropped. After 45 s on a
step it says once "Say done when step N is finished." In hint mode `step` events carry
`detail.question` (the open question, or null).

### Listening (`listen.py`)
- Sources: capture's `WS /mic` (JSON header `{t, n}` then 16 kHz mono int16), the laptop mic
  (`--mic local`, 20 ms blocks like capture), or a file through ffmpeg (`--source`). All go
  through the same loudness gate.
- The gate opens when a sound is 4 times the background, above 0.03, for 0.09 s. In the first
  2 s it learns how loud the room is, and after that a sound must also be 2.5 times that. It
  closes after 0.6 s of quiet judged on a 150 ms average, where quiet is near the background or
  under 0.4 of the speech's own level. Audio is judged in whole 30 ms frames whatever the piece
  size.
- faster-whisper `small.en`, int8 on the CPU. Pieces with a no-speech score over 0.3 or an
  average log-probability under -0.8 are dropped, and so are Whisper's usual noise words
  ("you", "thank you").
- The mic is ignored only while a line comes out of the speaker (`--speak`); printed or file
  lines never mute it.

### Voice queue (`speaker.py`)
One line at a time, at most one every `--gap` seconds. Warnings first, then answers, then step
lines; only the newest step line is kept, and lines that waited over 8 s (16 s for answers) are
dropped. Lines stay under 12 words.

### Gemini Live (`--voice gemini-live`)
Needs `GEMINI_API_KEY` and `google-genai`. Gemini hears the mic stream, gets the protocol and
the coach's state once a second when it changes, and answers. What the person said is logged as
`speech`; Gemini's answer goes through the voice queue as `say`, and with `--speak-to` its audio
is saved as WAV. It never marks steps: in this mode even "done" is only logged. Without a key,
without the package, or on any error it falls back to local Whisper. Model `gemini-3.8-live`
(`--gemini-live-model`).

## Tests

`coach/tests/`: scripted events (`test_brain.py`), transcripts (`test_speech.py`), hint mode
(`test_hints.py`), the gate on the four questions over bench noise (`test_gate.py`), the four
voice WAVs through a fake laptop mic and a fake capture `/mic` in real time with Whisper
(`test_voice.py`), the voice queue and audio files (`test_speaker.py`), Claude and Gemini Live
with stand-in clients (`test_models.py`), and the program itself: replay, a stand-in hub over
HTTP and websocket, the health port, a file as the mic (`test_runner.py`). Test servers use
ports 8841-8847. Set `BENCH_SAMPLES` to the sample video folder to also run on a real clip.

The voice clips in `tests/voice/` were rendered with macOS `say -o` to files, never played,
and mixed with bench audio from a sample video by the earlier draft.

## Sources and licences

| What | URL | Licence | Used for |
| --- | --- | --- | --- |
| faster-whisper | https://github.com/SYSTRAN/faster-whisper | MIT | local speech to text, and its Silero VAD filter |
| Whisper small.en (CTranslate2 conversion) | https://huggingface.co/Systran/faster-whisper-small.en | MIT | the speech model |
| anthropic | https://github.com/anthropics/anthropic-sdk-python | MIT | Claude wording answers |
| google-genai | https://github.com/googleapis/python-genai | Apache-2.0 | Gemini Live session |
| websockets | https://github.com/python-websockets/websockets | BSD-3-Clause | hub and mic streams |
| httpx | https://github.com/encode/httpx | BSD-3-Clause | posting events |
| FastAPI, uvicorn | https://github.com/fastapi/fastapi, https://github.com/encode/uvicorn | MIT, BSD-3-Clause | `/health`, `/state` |
| sounddevice | https://github.com/spatialaudio/python-sounddevice | MIT | laptop mic |
| NumPy, PyYAML | https://numpy.org, https://github.com/yaml/pyyaml | BSD-3-Clause, MIT | audio maths, protocol files |
| FFmpeg | https://ffmpeg.org | LGPL-2.1+ (GPL builds) | decoding `--source` files; the installed program is called, nothing bundled |
| macOS `say` | part of macOS | | the voice for `--speak-to` files and `--speak` |

The rules and the gate follow what the team's earlier draft of this part ended up doing; the
code here was written fresh for this rebuild.


## Voice robustness checks

The gate now accepts 0.18 s of voiced audio, with a 0.09 s onset. Whisper receives a
short vocabulary prompt for pipetting and tubes A/B/C; confidence filters stay unchanged.
Tube-letter joins and “aye” are recognized; explicit unknown letters stay distinct from
unnamed claims. Mixing counts include six through ten and “free/for times”. These rules
are covered by text tests; additional spoken voices remain unmeasured here.

The silent matrix runner defines 16 questions/confirmations, eight macOS voices,
140/210 words per minute, and bench-noise gains of 0.5/1/2 (relative amplitude, not SNR).
It uses only `say -o` and FFmpeg decoding. No audio is played. Empty synthesis is recorded
as a generation failure, never counted as a passing trial.

```sh
$BENCH_PY -m coach.eval_voicerobust prepare --directory coach/voicerobust-work/matrix
$BENCH_PY -m coach.eval_voicerobust measure --directory coach/voicerobust-work/matrix --output coach/voicerobust-work/results.jsonl
$BENCH_PY -m pytest coach/tests/test_voicerobust.py -q
```

Generation requires macOS voices and `BENCH_SAMPLES/lsv_dji/transform_skip3.mp4`.
Measurement requires cached small.en weights; `BENCH_WHISPER_MODEL` can name a complete
local model directory. Nothing downloads. Each invocation runs at most 24 trials with a
140-second batch budget; repeat the measure command to resume. It pauses before loading
weights if macOS reports less than 40% free memory. Use a fresh output name when changing
models or settings; completed trial IDs are skipped.

For the smaller available-recording check, prepare with `--fixtures`. This uses three
bundled questions, FFmpeg tempo factors 140/170 and 210/170, and the same noise gains;
it is not evidence for eight voices. Measure once with `--baseline` and once without,
using separate outputs. Baseline restores the original gate and removes the vocabulary
prompt; it does not restore old text rules. The compact pytest check runs these 18 trials
in an 80-second subprocess timeout, skips without complete weights, and needs no samples.
See [VOICEROBUST-REPORT.md](VOICEROBUST-REPORT.md) for measured limits.

No dependencies added. Existing [FFmpeg](https://ffmpeg.org/legal.html) decodes and changes
tempo (LGPL-2.1-or-later, or GPL depending on build); [NumPy](https://numpy.org/doc/stable/license.html)
mixes PCM (BSD-3-Clause). macOS `say` supplies system voices under the macOS licence.
The existing faster-whisper/Whisper licences above apply to transcription.
