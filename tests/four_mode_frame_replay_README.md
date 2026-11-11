# Four-context recorded-frame replay

This is an opt-in adapter for the existing `LiveRuntime` capture seam. It covers
S0 nightmare, S0 difficult, S1 nightmare, and S1 difficult. For each case it
feeds one saved BGR frame through `set_context`, `start_round`, and
`capture_now`, then writes a compact report of the current recognition, floor,
fit, and display conclusions and diagnostic event names.

No complete game frames are stored in the repository. Put authorized frames in
a private local directory and create a JSON manifest there with this shape:

```json
{
  "schema": "xingtu-four-mode-frame-replay-v1",
  "cases": [
    {"season": "s0", "mode": "nightmare", "frame": "s0-nightmare.png", "sha256": "<64 lowercase hex chars>"},
    {"season": "s0", "mode": "difficult", "frame": "s0-difficult.png", "sha256": "<64 lowercase hex chars>"},
    {"season": "s1", "mode": "nightmare", "frame": "s1-nightmare.png", "sha256": "<64 lowercase hex chars>"},
    {"season": "s1", "mode": "difficult", "frame": "s1-difficult.png", "sha256": "<64 lowercase hex chars>"}
  ]
}
```

Supply a local `module:callable` factory that constructs the production
`LiveRuntime` with resources for the requested context, the provided
`RecordedFrameCapture`, and `evidence_kind="recorded"`. Then run:

```sh
python tests/four_mode_frame_replay.py /private/path/manifest.json \
  --runtime-factory local_replay_setup:build_runtime \
  --output /private/path/report.json
```

The factory receives `(case, capture)`. Keep resource roots and frame files
local; do not commit private captures or resource packages. The report marks
recorded evidence as diagnostic and always denies live authorization. A
recorded replay is not a claim that recognition, floor judgment, route fitting,
or visible display is correct in a live game. No accuracy score or threshold
tuning is performed.

The harness is scoped to **排查与帮助**. It observes outcomes from **地图识别**,
**楼层判断**, and **路线贴合**; those adjacent modules' algorithms are not
modified.
