# coach report

- Follows the protocol from hub events or a replay file. On the scripted demo all 8 steps came out as planned, all 3 planned mistakes got a warning, and it ended "Run finished. Check steps 4, 5 and 7." It never says "Nice work" after a skipped or unsure step.
- Listening: 4 test questions played over bench noise through a fake mic were all heard correctly and answered right, 1.25 to 1.56 s after each question ended. The mic gate no longer stays open for 3 s of bench clatter after a question.
- Spoken confirmations, hint mode, Claude answer wording and Gemini Live voice are built and tested; Claude and Gemini only with stand-ins (no keys here). Nothing is played out loud unless --speak is given.
- Not yet tried against the real vision, capture or app parts, or a real mic. Vision names objects only as "tube", so tube-specific steps will come out unsure on real footage unless table mode names tubes A, B and C.
- One of five full test runs had a timing failure that did not repeat in four more runs, probably a timing test on a busy machine.
- Tests: 87 pass, plus one that needs BENCH_SAMPLES.
