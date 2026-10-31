# coach report

- Follows the protocol from hub events or a replay file. On the scripted demo all 8 steps came out as planned, all 3 planned mistakes got a warning, and it ended "Run finished. Check steps 4, 5 and 7." It never says "Nice work" after a skipped or unsure step.
- Listening: 4 test questions played over bench noise through a fake mic were all heard correctly and answered right, 1.25 to 1.56 s after each question ended. The mic gate no longer stays open for 3 s of bench clatter after a question.
- Spoken confirmations, hint mode, Claude answer wording and Gemini Live voice are built and tested; Claude and Gemini only with stand-ins (no keys here). Nothing is played out loud unless --speak is given.
- Not yet tried against the real vision, capture or app parts, or a real mic. Vision names objects only as "tube", so tube-specific steps will come out unsure on real footage unless table mode names tubes A, B and C.
- One of five full test runs had a timing failure that did not repeat in four more runs, probably a timing test on a busy machine.
- Tests: 87 pass, plus one that needs BENCH_SAMPLES.

## Voice robustness builder

No event-contract changes. See `VOICEROBUST-REPORT.md` and the before/after JSONL files.
The three available questions at two tempo factors and three noise gains improved from
12/18 to 18/18 exact transcripts and correct answers, with word errors falling from
11/72 to 0/72. Gate timing and Whisper vocabulary hints changed; text rules gained tube
letter joins, count variants, and preservation of unknown explicit tube letters.

The required eight-voice benchmark remains incomplete: all 256 `say -o` calls yielded
empty audio, so no scores are claimed for its planned 768 mixed trials. Its reproducible
runner and failure summary are included. The 16 text oracles cover both questions and
confirmations; they do not substitute for acoustic measurements. No sound was played.

The compact robustness test file passed all 30 tests in 17.60 s, including 18 real Whisper
trials. It skips without weights and pauses on low memory. The existing file-speech test
now checks system synthesis independently and skips when it produces zero audio bytes.
This was a pre-existing environment failure, reproduced during this work.

No new packages, downloads, servers left running, or changes outside coach/. Public-path
scan and `git diff --check` pass. Files are uncommitted.

Ran `git log --format='%h %an %aI %cI' main..HEAD`: output was empty.
No branch-only commits; author settings, dates and history were not changed.

Final full coach suite: 118 passed, 3 skipped in 51.75 s (sample environment unset,
empty system synthesis, and the compact worker's low-memory pause). The separate compact
run completed all 30 tests. No further model work ran after the memory pause.
