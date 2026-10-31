# Bench Coach

Two cameras and a microphone on a lab bench. Bench Coach follows the written protocol as the
person works, says the next step out loud, warns when something goes wrong (pipette angle, reused
tip, skipped step), and writes the lab note at the end.

Built by the team at the MIT ScienceClaw hackathon, October 30 to November 1, 2026, with AI coding
agents. Everything in this repository was written during the hackathon.

## Parts
- `capture/` reads both cameras and the mic on one clock and records them.
- `vision/` finds hands, the pipette, tips and tubes and turns them into events, plus a table mode
  for colour-coded tubes and an overhead camera.
- `angle/` measures the pipette's lean from vertical with both cameras.
- `coach/` follows the protocol, listens, and speaks the next step and warnings.
- `app/` runs the event hub and the bench page.
- `note/` writes the lab note.
- `nvidia/` turns recorded runs into verifier tasks and synthetic training data with NVIDIA NeMo tools.

How the parts talk: `docs/EVENTS.md`.
