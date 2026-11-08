# Locked-fit exact residual reuse

Baseline: `6eb4e3567e9a85f810c2342bcac3426c9df1b2d2`.
Scope: shared `_AlignmentHost`, S0/S1; no change to round identity,
current-frame acceptance thresholds, peer checks, coordinate priors or display grants.

## Evidence and change

A generated 1200x1000 frame from `asymmetric_structure()` with a supplied correct
pose prior runs the actual extraction, proposal and verification kernels. The
provider and prepared-index loader are fixtures; this is not a game capture.
Twelve fits called `selected_route_current_frame_residual` 24 times. Its cumulative
profile time was 326 ms of 1199 ms host time (nested times must not be added).
The fast solver and final host consumer recomputed exactly the same numerical
residual, not different independent observations.

Keep both consumers and the final canonical evidence wrapper. Reuse only the
kernel result within a synchronous `_fit_observation` call. The key contains map,
floor, frame identity, reference identity, validation-mask identity, matrix dtype,
shape and exact bytes, maximum dimension, and visible-domain identity. Keep at
most two entries, strong input references, and deep-copy diagnostics so a consumer
cannot mutate another consumer's result. A `finally` restores the previous scope
on success, rejection or exception. No solver-provided success flag is trusted,
and no result survives into the next observation. Implicit/explicit domains with
different identities intentionally do not share a cached result.

The cache assumes the same immutable input convention as the synchronous solver;
it is not a cross-thread frame cache. Resource objects and masks must not be
mutated during a fit. Changed resource identities and each new fit recompute.

## Timing

Single cloud process, original baseline module loaded from `git show`, current
module in the same process. Alternate baseline/candidate execution order each
iteration; two warmups then 30 samples per implementation. Same generated frame,
reference and prepared index, NumPy linear-interpolation percentiles. Timed region
is `host.fit`, not preparation, capture, floor detection, rendering or visible pixels.
No profiler or another test process was run during the final timing pass.

| Generated frame / prior | Baseline p50/p95/max ms | Candidate p50/p95/max ms |
| --- | --- | --- |
| Clean / none | 74.689 / 91.240 / 107.806 | 64.344 / 72.853 / 78.574 |
| Clean / correct prior | 75.578 / 87.226 / 98.382 | 60.118 / 71.577 / 84.057 |
| Corner markers / none | 151.276 / 170.642 / 181.346 | 150.481 / 168.364 / 172.599 |
| Corner markers / correct prior | 76.241 / 95.778 / 107.525 | 63.523 / 90.029 / 96.819 |

All four cases accepted with exactly equal matrices and complete public canonical
residual dictionaries before/after. The no-prior marker path has essentially no
gain. A separate batched-refinement experiment made unseeded refinement slower
and was discarded. This change does not establish the 300/500 ms input-to-visible
target or explain the user's remaining slow/missed frames.

## Local additions and remaining work

Current code already composes resource-bound cross-floor priors, searches the
local +/-32 px neighborhood first, then falls back without granting the prior
identity or display authority. Those safeguards are retained.

Generated attached rooms at x=510, y=180..219 with widths 8/16/32/64 pixels passed
both modes with/without prepared indexes: 16 combinations, each full-canvas truth
error below 2 pixels. This is existing tolerance, not new recall from this patch.
It does not prove arbitrary random treasure rooms safe; no threshold was relaxed.
Wrong-reference, repeated-map and shifted-peer rejection regressions remain in
the existing marker tests. Real random-room layouts, production resource diversity,
9b failed frames and capture/floor/display timing remain unavailable here. The
local execution-backend denial was not bypassed.

## Tests

`test_observation_residual_reuse` verifies one real residual kernel call instead
of two, recomputation on the next fit, unchanged direct-call behavior, map/floor,
frame, resource, matrix and ROI key changes, bounded storage, deep-copy isolation,
exception cleanup, cancellation and the generated room additions above. It is
registered in the existing Windows `fit-simulation` job. Cloud results do not
stand in for Windows CI or a Setup/game acceptance pass.

Final cloud verification: the existing `fit-simulation` module list plus the new
module ran 359 tests in 51.716 seconds: 355 passed, 4 Windows-only tests skipped.
Command uses `PYTHONPATH=app:tests:tools:experiments/qt_quick`,
`QT_QPA_PLATFORM=offscreen` and `python -B -m unittest` with the module list in
`.github/workflows/qt-next-integration-windows.yml`. An earlier invocation omitted
`tools` and another read-only run could not create temporary fixtures; neither is
counted as a passing run. The final approved cloud run had temporary-write access.
