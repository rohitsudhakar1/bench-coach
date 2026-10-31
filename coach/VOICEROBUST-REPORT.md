# Voice robustness report

No event-contract changes proposed. Work is confined to coach/ and remains uncommitted.

## Result and missing measurement

The smaller available-recording check improved from 12/18 to 18/18 exact transcripts and
correct answers. This is a tuning result on reused recordings, not a held-out accuracy claim.
The requested eight-voice spoken confirmation benchmark could not be measured: all 256
file-only macOS synthesis attempts decoded to empty audio. No speaker playback was attempted.

The runner defines 16 phrases, eight voices (Samantha, Daniel, Karen, Moira, Rishi, Tessa,
Fred, Aman), two rates (140 and 210 words/minute), and three noise gains (0.5, 1, 2).
Those voices span male/female voices and US, UK, Australian, Irish, Indian and South African
English. All 8 × 2 × 16 synthesis attempts ran; none yielded usable audio, so the planned
768 mixed trials have no scores. `voicerobust-generation.json` records that failure.
Do not interpret text-rule tests as spoken recognition results.

## Available audio before and after

Three existing recordings: “what's next”, “how much do I add”, “did I add tube C”. Each
was tempo-adjusted with FFmpeg at 140/170 and 210/170, then mixed over the existing
20-second bench-noise recording at three gains. Rates label tempo variants; the original
recording's exact speaking rate is not asserted. The noise came from the earlier sample-video
bench fixture. Full generation instead extracts the sound track of
`BENCH_SAMPLES/lsv_dji/transform_skip3.mp4` directly.

Speech begins after 3 seconds of room audio, with 1.5 seconds of trailing room audio.
Each independent trial uses a fake sounddevice input stream with 20 ms int16 blocks,
`local_chunks`, production `listen`, Gate, cached small.en CPU/int8 Whisper, and Brain.
No real microphone, hub, server or hosted model is used. The fake clock is shifted to the
existing test epoch before Brain receives it. No output goes through a speech backend.

Success requires exactly the expected answer and unchanged current step for questions.
The full matrix also checks the destination step for confirmations and non-advancement for
negations/wrong tubes. Transcript scoring lowercases and ignores punctuation; it does not
normalize tube homophones. Word error rate uses edit distance, including missed utterances.

| Check | Before | After |
| --- | ---: | ---: |
| Exact transcripts | 12/18 (66.7%) | 18/18 (100%) |
| Correct answer and step state | 12/18 (66.7%) | 18/18 (100%) |
| Word errors / reference words | 11/72 (15.3%) | 0/72 (0%) |
| What's next: correct | 3/6 | 6/6 |
| How much: correct | 6/6 | 6/6 |
| Did I add tube C: correct | 3/6 | 6/6 |
| Noise-only gate openings, 20 s each at 0.5/1/2 gain | 0/0/0 | 0/0/0 |

| Noise gain | Before correct / trials | After correct / trials |
| --- | ---: | ---: |
| 0.5 | 5/6 | 6/6 |
| 1.0 | 5/6 | 6/6 |
| 2.0 | 2/6 | 6/6 |

Per-trial transcripts, answers and scores are in `voicerobust-before.jsonl` and
`voicerobust-after.jsonl`. Missing transcripts are failures. Noise gain is relative PCM
amplitude, not a measured SNR or physical loudness level. Sample count is too small to
support a general claim about accents, confirmations, real microphones or noisy rooms.

## Changes

Gate onset is 0.09 s instead of 0.12 s, and minimum voiced time 0.18 s instead of 0.25 s.
The three missing “what's next” trials had no accepted gate segment with the original
settings; the new settings produce one each. Loudness ratios, room margin and silence
closure stay unchanged. A 0.06 s onset trial split one question into two segments, so it
was rejected. Existing clatter, paused-murmur and block-size invariance checks still pass.

Whisper receives a vocabulary-only initial prompt: pipette, fresh tip, tubes A/B/C,
microlitres, aspirate, dispense. Confidence thresholds and noise-word filtering are unchanged.
The three former tube-C errors were “tube Z”, “tobsie”, and “jubesi”; all transcribe as
“tube C” with the new settings. We do not rewrite Z as C, since that could confirm the wrong
vessel. The combined change was measured; independent model-level ablations were not run.

Text rules accept “aye”, joined tubeA/B/C, and count words six through ten, with “free times”
and “for times” treated as three/four. Unknown explicit single tube letters such as Z are
preserved, preventing them from matching any tube as an unnamed action. These extensions
have direct rule tests, not new acoustic evidence. Ambiguous or unrelated vessel names,
general speech understanding, and volume validation remain outside this change.

## Reproduction and checks

See README for generation and resumable measurement commands. `--baseline` restores old
gate timing and disables the vocabulary prompt; it retains current text rules, which do not
change the three-question scores. No weights or packages were downloaded. Model batches
were under three minutes, with reported memory above the required 40% before model runs.
The compact model test has an 80-second worker timeout and skips without complete local
weights. It also skips if FFmpeg is absent. Scratch audio is ignored under voicerobust-work/.

## Date check

Ran `git log --format='%h %an %aI %cI' main..HEAD` in the assigned worktree.
Output was empty: no branch-only commits. No dates, author settings or history were changed.

The compact test file passed 30 tests in 17.60 seconds, including all 18 model trials.
The file-speech backend test now probes `say -o` independently and skips if that system
probe has zero audio bytes. A failed full-suite attempt also returned a worker error
without captured diagnostics; the isolated compact rerun passed. The worker now reports
stderr on failure and uses a distinct low-memory exit code that pytest records as a skip.

Final full coach suite: 118 passed, 3 skipped in 51.75 seconds. Skips were the existing
sample-dependent test without BENCH_SAMPLES, unavailable system file synthesis, and the
compact model worker pausing below 40% free memory during the full suite. The isolated
compact run above completed all model trials. The full suite includes the older fake local
and websocket voice-loop checks. No additional model run was started after the memory pause.
`git diff --check`, the required tracked-file public-path scan, and a coach source/docs/data
scan including new files found no prohibited absolute home paths.

## Larger run (lead, Oct 31 overnight)

The eight-voice set (768 clips) was generated outside the sandbox with no failures. The scoring script stopped after about 100 clips per run, almost all in one voice (Samantha), so this is not the full matrix.

| Run | Clips scored | Correct | Exact transcript |
| --- | --- | --- | --- |
| Before (old gate, no vocabulary hints) | 99 | 81 (82%) | 65 |
| After | 107 | 90 (84%) | 76 |

By noise level after the change: quiet 31 of 36, normal 32 of 36, loud 27 of 35. The gain is small at this size; the voices other than Samantha are barely covered, so treat multi-voice robustness as unmeasured.
