# Protolib report

## Contract clarification proposed to the lead

The brief explicitly requests `source: app`, `kind: speech`, `detail.text: done`.
Add `app` alongside `coach` in the speech sender row of `docs/EVENTS.md`, and mention
speech confirmation in the protocol-file section. That file was not changed here.
No new event kind or port was added. `protocols/convert.py` now permits the existing
speech kind in `expect`, so manually confirmed steps pass the hub's strict schema.
The coach already handles these confirmations; no coach code changed.

## Delivered

Five sourced YAML teaching adaptations, all with nonempty evidence rules:

| Protocol | Steps | Timed steps |
| --- | ---: | ---: |
| Serial dilution 1:10 x4 | 21 | 0 |
| PCR master mix setup | 54 | 0 |
| Bacterial transformation heat shock | 16 | 5 |
| Loading an agarose gel | 16 | 0 |
| Plating cells | 9 | 1 |

All 116 steps completed in a deterministic coach run using app confirmations, with
said evidence and no warnings when each prescribed wait was respected. Every file
passed the real coach loader, strict hub schema and preflight protocol function.
The separate early-wait test confirms that the coach warns rather than enforcing a
lock. Questions do not complete the wait. This tests event handling, not bench work.

Sources were read from the public UC Davis Roth lab and Addgene teaching protocol
pages linked in each YAML and in `README.md`. PCR uses seven reactions and corrects
the source's rounded water amount to 283.36 microlitres. Serial dilution uses dye
and prefilled water tubes. Preparation assumptions are stated, and transformation
ends at recovery so the plating exercise can follow separately.

`GET /step` serves a dedicated phone page with large current text, the next step,
the last active-run warning, a confirmation question when present, and a large
Done button. It loads state after websocket events and reconnects. Done posts one
app speech event; only coach step events change progress. Duplicate taps are
blocked while sending and while waiting for the coach. A pre-send state refresh
avoids confirming a different step that has already changed on the hub. Stop and
disconnect disable the button. Text uses `textContent`, including warnings.

The earlier app reports identified warnings lingering after stop and an unverified
phone layout. This view explicitly hides warnings after stop; its logic is tested.
It shares the existing hub and leaves desktop behavior intact.

## Measured checks

```sh
$BENCH_PY -m pytest app/tests protocols/tests scripts/tests/test_preflight.py \
  coach/tests/test_brain.py coach/tests/test_speech.py coach/tests/test_hints.py \
  -q -p no:cacheprovider
```

Result: **110 passed, 1 skipped in 24.62 seconds**. This includes a real in-process
hub/websocket/coach integration test: Done arrives as app speech, does not itself
change hub progress, and causes the coach to publish a done step labelled said.
No live capture, microphone, model download or hosted API was used for the new tests.

```sh
node --test app/tests/step.test.cjs
```

Result: **5 passed**, about 34 ms on the final run. Covers current/next/warning
rendering logic, completion and stop, duplicate taps, exact event payload,
disconnect/reconnect, a stale displayed step, rejected POST and uncertain delivery.
These tests run the actual page script against a small DOM/network stand-in.

`git diff --check` passed. The required tracked-file absolute-path scan found no
hits; new source, tests and documentation were scanned too. No packages were added,
no audio was played, no commits were made, and no server was left running.

## Not verified and limits

`$BENCH_PY app/tests/step_browser.py` could not launch Chromium: the sandbox denied
browser startup (`bootstrap_check_in`, permission denied). No screenshot, actual
font-size measurement or browser layout assertion is claimed. The repeatable test
checks widths 320, 390 and 768, no horizontal overflow, 36 px minimum current text,
80 px button height, click submission, websocket refresh and stop behavior when a
browser can launch. It mocks networking and starts no server.

There was no physical phone or wet-lab test and no full-system preflight. Named
reagents, gel wells and these new tube labels are not newly recognized by vision.
Manual confirmation is required where the producer cannot distinguish them. Timers
are warning thresholds, not incubation enforcement. The 20 degree angle threshold
is inherited from the demo rather than validated for each procedure.

Done needs a running coach. If the hub accepts the event but the coach is absent,
the button remains disabled for that step. An uncertain POST is not resent. Speech
has no atomic run/step binding in the existing coach contract; simultaneous users
or a change between refresh and POST can still confirm a newer step. Use one
confirmation device per run. Networking and authentication remain the hub's existing
behavior; a phone must be able to reach its address.

## Git history check

Ran the required command before finishing:

```sh
git log --format='%h %an %aI %cI' main..HEAD
```

Output was empty. This branch has no commits ahead of main. All changes remain
uncommitted for the lead; no author, dates, git configuration or history were changed.
