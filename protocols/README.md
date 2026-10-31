# Teaching protocol library

Five short protocols work with the existing coach, hub and preflight. Select one by
filename when starting a run with `POST /run/start`, for example:

```sh
curl -X POST http://127.0.0.1:8880/run/start \
  -H 'Content-Type: application/json' \
  -d '{"protocol":"serial-dilution.yaml"}'
```

The hub loads the selected YAML and sends its filename to the coach. The coach can
also load it alone with `--protocol protocols/serial-dilution.yaml --no-hub --mic off`.
Use the existing launcher for the full system; stop any check processes afterward.

| File | Teaching exercise | Public source |
| --- | --- | --- |
| `serial-dilution.yaml` | Four 100 microlitre transfers into 900 microlitres of water; 1:10000 final dilution | [UC Davis Roth lab](https://rothlab.ucdavis.edu/protocols/M04.shtml) |
| `pcr-master-mix.yaml` | Seven 50 microlitre reactions, same template, 10% extra mix | [Addgene PCR](https://www.addgene.org/protocols/pcr/) |
| `heat-shock-transformation.yaml` | Teaching E. coli heat shock through recovery | [Addgene transformation](https://www.addgene.org/protocols/bacterial-transformation/) |
| `loading-gel.yaml` | Prepared gel, ladder and one sample | [Addgene gel electrophoresis](https://www.addgene.org/protocols/gel-electrophoresis/) |
| `plating-cells.yaml` | Spread recovered teaching E. coli and incubate | [Addgene transformation, plating section](https://www.addgene.org/protocols/bacterial-transformation/) |

These are paraphrased teaching adaptations, not copies of the source pages. Each YAML
cites its source and states its assumptions. Dilution uses prefilled tubes and dye.
PCR assumes primers are preloaded, includes template in the common mix, and stops
before cycling. Its water amount corrects the source's rounded arithmetic: 283.36
microlitres gives 346.5 microlitres of mix, enough for seven 45 microlitre aliquots
plus 10%. Use pipettes suited to each volume. Gel loading assumes prepared reagents
and does not cover casting, electrophoresis or imaging. Transformation and plating
are separate exercises using an instructor-approved nonpathogenic teaching strain
and plasmid. Review the source and the actual reagent instructions before the lab.

Every step has nonempty `expect`. Draw and dispense are separate steps, since the
coach treats evidence entries as alternatives. Each pipetting step has the demo's
20 degree angle threshold; this is a coaching example, not a measured universal
technique limit. Mixing and actions the cameras cannot verify use `kind: speech`.
The schema now accepts that existing event kind. A finished-action confirmation or
“done” advances the coach; ordinary speech and questions do not complete a wait.
All steps still accept “done” through the coach's existing confirmation rules.

`min_seconds` records each explicit wait. The coach warns if confirmed early; it
does not block advancement, enforce temperature or prove that incubation occurred.
Camera events cannot prove volume, reagent identity, sterility, adequate mixing,
colony growth or temperature. Object names beyond the table demo require manual
confirmation unless a producer can identify them. These files do not add recognition.

## Phone view

Open `/step` on the same hub. It shows the current instruction in large type, the
next instruction and the latest warning for the active run. Done posts an event to
`/events` with `source: app`, `kind: speech` and `detail: {text: "done"}`. It records
student confirmation as said evidence after the coach processes it. No microphone,
API key or audio playback is needed.

The phone refreshes `/state` on websocket messages and after reconnecting. Done is
disabled without a current step, while disconnected, and after a tap until the
coach changes the current step. If the coach is unavailable it stays waiting; check
the coach on the bench page. A lost POST response is not automatically retried.
All tabs control the same run. The current contract cannot atomically bind speech
to a run/step, so avoid confirming from several devices at once.

The hub normally binds to loopback. A physical phone needs an already reachable hub
address on the lab network, configured through the hub's `--host` option. Use the
same host and port with `/step`; this view does not configure networking or start
services. The hub has no authentication.

## Checks and dependencies

```sh
$BENCH_PY -m pytest protocols/tests app/tests/test_step.py -q -p no:cacheprovider
node --test app/tests/step.test.cjs
# Optional, with an available Playwright Chromium installation:
$BENCH_PY app/tests/step_browser.py
```

No packages were added. Existing libraries used here:

| Library | Licence | Use |
| --- | --- | --- |
| [PyYAML](https://pyyaml.org/) | MIT | Read protocol files |
| [Pydantic](https://docs.pydantic.dev/) | MIT | Validate protocol evidence |
| [FastAPI](https://fastapi.tiangolo.com/) | MIT | Serve the phone route |
| [Starlette](https://www.starlette.io/) | BSD-3-Clause | File responses and test client |
| [HTTPX](https://www.python-httpx.org/) | BSD-3-Clause | In-process integration checks |
| [pytest](https://pytest.org/) | MIT | Coach, preflight and hub tests |
| [Node.js](https://nodejs.org/) | MIT, with bundled notices | Browser-logic tests using its built-in runner |
| [Playwright](https://playwright.dev/python/) | Apache-2.0 | Optional mobile browser check |

The phone uses original HTML/CSS/JavaScript and browser APIs, with no UI packages.
Source protocols retain their publishers' terms; library licences do not grant rights
to source-page text. See `PROTOLIB-REPORT.md` for measured results and remaining checks.
