# Tonight's demo video: report

Proposed change to docs/EVENTS.md: none.

## Outputs

Both are 1920x1080, 30 fps H.264, rendered with `scripts/render_demo.py` from the lsv_mv
cell-passaging footage (left and right front cameras together).

- `bench-coach-demo-tonight.mp4`, 47.5 s: title card (3.5 s), clip 414-445 (31 s), lab note (10 s),
  end card (3 s).
- `bench-coach-demo-tonight-cut.mp4`, 68 s: clip 249-269 as part 1 (pipette and media bottle), then
  clip 414-445 as part 2, the lab note and the end card. Joined with ffmpeg's concat demuxer, no re-encode.

They were copied to the team's shared videos folder on this Mac, outside the repo.

## Which clips, and why

| Pair | What happens | Steps ticked | Used |
| --- | --- | --- | --- |
| 414-445 | tip on, two trypsin draws, a flask dispense | 4 of 4, all hand-labelled | main video and part 2 of the cut |
| 249-269 | pipette fitted to the pipette aid, media bottle opened | 1 of 2, hand-labelled | part 1 of the cut |
| 12-27 | hand disinfection only, no pipette | 0 of 1 | not used |

12-27 has nothing the coach can check (no event kind covers hand washing) and the detector
called the tip box "waste", so it added 15 s of nothing. The second step in 249-269 ("Open cell
media bottle") is not checkable either, so in the cut it stays as the current step until part 1 ends.

## What is detected and what is hand-labelled

Detected by the cameras and drawn on every frame: object boxes (Grounding DINO tiny) and both hands
with finger points (MediaPipe). On 414-445 the vision rules also fired one tip_on at 2.9 s, which
the coach marked "Not sure"; the real tip goes on at 6.5 s. Nothing else ticked from camera events.

Hand-labelled, from the earlier draft's frame-by-frame labels: every step that shows as done in
both videos. In 414-445: tip on 6.5 s, draw 15.9 s, dispense 23.1 s, second draw 25.2 s
(`scripts/samples/cp-414-445-labels.jsonl`). In 249-269: tip on at 10.0 s
(`scripts/samples/cp-249-269-labels.jsonl`, taken from the draft's note that the pipette is fitted
to the aid by about 10 s; the draft logged no draw or dispense in that clip).

On screen each of those steps says "hand-labelled" in amber, the title card and panel say
hand-labelled events were added, and the lab note card now says "Done (hand-labelled)".

No warnings appear: none fired on these clips, and nothing was scripted to make one.

## Renderer fixes made tonight

- The lab note card said "Done (seen)" for steps decided by hand labels. That passed labels off as
  detected. The renderer now relabels those steps "hand-labelled" in amber before drawing the card
  (`mark_labelled`). The note files in the work folder still say "seen" because the note part
  writes them; the note part's own label logic should learn about `detail.labelled` later.
- `--brand` sets the small line above the title, `--end` adds an end card (repeatable lines),
  `--title-seconds` and `--note-seconds` set card lengths. Tests added; 35 pass in the two render test files.

## Known problems still visible

- A phone on the person's chest is boxed as "tip box"; a bottle is sometimes "waste".
- The note's date is the video files' modified time (26 October), not the day of filming.
- Label pills overlap when objects sit close together.

## Commands

```sh
B=(--brand "Bench Coach, MIT ScienceClaw hackathon")
E=(--end "Bench Coach" --end "Two cameras watch the bench, tick off the protocol and write the lab note." --end "MIT ScienceClaw hackathon")
"$BENCH_PY" -m scripts.render_demo --video "$BENCH_SAMPLES/lsv_mv/CP-Left-Front-414-445.mp4" \
  --video2 "$BENCH_SAMPLES/lsv_mv/CP-Right-Front-414-445.mp4" --protocol "$BENCH_SAMPLES/lsv_mv/CP-414-445-protocol.txt" \
  --labels scripts/samples/cp-414-445-labels.jsonl --title "Cell passaging: trypsin into the flask" \
  "${B[@]}" "${E[@]}" --title-seconds 3.5 --note-seconds 10 --out out/bench-coach-demo-tonight.mp4
# cut: render 249-269 with --title "Part 1: pipette and media bottle" --title-seconds 2.5 --note-seconds 0
# and no --end, render 414-445 again with --title "Part 2: trypsin into the flask" --title-seconds 2.5
# --note-seconds 9 and the end card, then: ffmpeg -f concat -safe 0 -i list.txt -c copy cut.mp4
```

Vision pass took 38 s (414-445), 23 s (249-269) and 16 s (12-27); each redraw with `--reuse` took 3-7 s.
Frames were checked by eye: title card, mid-clip panel, note card, end card and the join in the cut.
