# Marker-occluded structure fit: calculation profiling and local raster gate

## Baseline and scope

GitHub PR #101 was read through its normal API before this work: head
`642c472f5c1ee45735dff4ce83f20d8dc8764de0`, tree
`ed68b20971c91c42b4407371394456edb7d59c9b`. Work is on an independent branch.
The baseline already contains the implicit S0 peer guard, clipped default ROI,
and failed-marker-primary/clean-secondary correction. Those changes are not
performance gains from this patch.

All images here are generated geometry. The existing 44-case probe and the
independent review scripts were not edited:

- `probe.py`: SHA256 `e60485c076a7bd60518202cb013edc91ca05ab6b5a24a6daadd41a1ffaf23e1f`.
- `audit_marker_extra.py`: SHA256 `5859c764a443584cb6aa2bf31dc47f0ed77c04b17c714908a4506044a06aa3d3`.
- `audit_marker_guard.py`: SHA256 `88ce584ded1da1f7184e42761cf51b3391b0d87c93567ca4716722d5ddd4cd80`.

The extra script runs ten different map IDs backed by the same generated
reference. It is a repeatable cumulative solver workload, not a production
catalog. A second fixed-order group uses ten different geometry/channel
combinations: correct, changed hole, resized canvas, translated canvas,
partial unknown, wrong validation mask, repeated rooms, mirrored, all unknown,
and rotated. Each group scans all ten entries. No whole-fit result is memoized,
no map ID is collapsed, and multiple accepted entries do not imply unique map
identity.

## Profile before editing

`cProfile` on the unchanged extra script recorded 134 actual host fits:

- Host fit: 27.68 s inclusive.
- Initial solver: 24.39 s inclusive.
- Wall fitting: 20.25 s inclusive; raster validation: 16.74 s inclusive.
- OpenCV distance transforms: 12.36 s exclusive.
- Inner and final residual kernels: 3.26 s inclusive; peer guard: 0.36 s.

These nested times must not be added. A diagnostic probe found 30 different
projected rasters from 30 wall models, so caching an identical projection was
not a valid explanation for this workload. Source-provider calls in this
fixture only return generated references; they are not production disk reads.

## Change and unchanged gates

Only `single_map_structure.py` changes product behavior. During descriptor wall
proposal evaluation, compute the same `DIST_L2`, mask-size-3 distance test in
seven-pixel-halo windows around every observed fragment. Sources are still the
complete directed raster support with source-unknown pixels removed. All
measured fragment pixels must satisfy the same six-client-pixel gate; this is
not sparse sampling or a percentage acceptance rule.

A path leaving that halo needs at least seven positive-cost steps, more than
six even at the backend's cheapest step cost. Local cropping therefore cannot
supply or remove a source that changes the threshold decision. Cropped and full
OpenCV distance fields can differ by one floating-point ULP away from the
threshold. The local result is consequently only an internal proposal gate.
After the existing exact wall-correspondence identity/subset and pose
arbitration, every retained candidate is checked by the original complete
raster kernel. Its original `maxPixels`, `worstPoints`, counts and other
successful/public diagnostics are retained. Direct public raster/geometry
calls and the bounded pose-index seed path keep immediate complete diagnostics.
Budget-failure diagnostic completion also checks cancellation before each
candidate and before returning.

No geometry threshold, measured corner admission, leave-one-plane-out fit,
source-unknown treatment, seed/validation/component budget, ambiguity rule,
regional validation, final canonical residual, S0/S1 domain or peer guard is
relaxed. No parallel workers or persistent/cross-source caches are introduced.
The existing 128-point negative witness remains rejection-only.

## Same-machine uninstrumented timing

Python 3.12.14, NumPy 2.2.6, OpenCV 4.12.0, one OpenCV thread. Each original or
mixed workload uses two warm-ups and ten retained samples. Baseline and
candidate run sequentially; percentile is NumPy's default linear percentile.
Index preparation is outside each timed scan. These samples do not run
`cProfile` or stage wrappers.

| Workload | Baseline p50 / p95 / max (ms) | Candidate p50 / p95 / max (ms) |
|---|---:|---:|
| Original extra, one marker candidate | 221.82 / 232.69 / 234.09 | 160.76 / 165.60 / 165.95 |
| Original extra, ten same-source IDs | 1763.62 / 1830.20 / 1840.47 | 1213.35 / 1251.32 / 1254.72 |
| Ten mixed sources, no prepared index | 1446.50 / 1512.38 / 1546.74 | 921.45 / 1039.95 / 1081.45 |
| Ten mixed sources, prepared index available | 1509.07 / 1697.25 / 1723.74 | 1053.37 / 1112.16 / 1139.87 |

The ten-ID workload's p95 decreased about 31.6%; mixed-source p95 decreased
about 31.2% without an index and 34.5% with an index. The marked input still
needs initial wall solving after the prepared fast path does not accept it.

A separate clean-path measurement alternated baseline/candidate processes,
reversed their order every batch, used two warm-ups plus five samples per
batch, and collected thirty samples per configuration without concurrent test
processes:

| Clean single-candidate path | Baseline p50 / p95 / max (ms) | Candidate p50 / p95 / max (ms) |
|---|---:|---:|
| No prepared index | 75.11 / 83.05 / 83.91 | 77.01 / 81.06 / 92.28 |
| Prepared index | 73.10 / 79.04 / 81.26 | 75.25 / 82.24 / 83.08 |

Small clean-path changes remain in the measured distributions; do not claim
zero regression or a clean-path speedup. The indexed clean profile does not
enter the changed initial solver. Earlier ten-sample clean runs had higher
candidate p95 too; the alternating run is additional evidence, not removal of
those samples.

## Stage attribution and source preparation

Separate instrumented runs wrap stage boundaries; they are not the timing
samples above. Mixed no-index exclusive time (ms):

| Stage | Before | After |
|---|---:|---:|
| Observation extraction / parts | 11.91 / 11.51 | 9.19 / 9.66 |
| Provider lookup / prepared attempt | 0.30 / 0.79 | 0.08 / 0.55 |
| Raster proposal / complete raster | 0 / 1040.83 | 485.84 / 37.36 |
| Other wall fit work | 139.79 | 118.81 |
| Other initial solver work | 85.52 | 75.61 |
| Regional / solver residual | 20.19 / 75.99 | 17.55 / 69.41 |
| Final canonical residual / peer guard | 47.87 / 8.00 | 45.06 / 6.59 |

The scan still evaluates 727 wall assignments and 286 raster proposals; the
original full diagnostic raster kernel is now needed for seven retained
candidates rather than all 286 proposals. Seven regional and solver-residual
calls, four final residuals and four peer guards remain. This is reduced
intermediate calculation, not reduced candidate/validation coverage.

Separately generated ten compressed synthetic NPZ files total 114,117 bytes.
The real shared-structure loader, on a warm local filesystem, measured
p50/p95/max 31.01/33.49/33.79 ms before and 29.06/29.75/29.84 ms after. Preparing
ten native fast indexes measured 49.13/54.90/55.12 and 48.11/51.54/51.70 ms.
Those functions are unchanged. These values do not measure cold production
resource I/O, downloads or the full production resource catalog.

## Correctness evidence

- Unchanged 44-case probe: 32 successes before and after. Matrices, decisions
  and all non-timing diagnostics are equal; newly recovered marker scenarios
  keep maximum full-canvas truth error 0.542714 px. This maximum is not claimed
  for every old clean/partial-observation success.
- Ten mixed sources: four successes and six rejections before/after; complete
  results, except timing, are equal. Repeated rooms remain ambiguous, and wrong
  geometry, wrong validation and all-unknown references remain rejected.
- New nine test methods cover randomized every-pixel threshold equivalence,
  six-pixel boundary, empty and unknown source, all-unknown query, tiny/clipped
  windows, slanted raster and explicit wall channel, a single unexplained gap
  in a long fragment, deferred/public diagnostic equality, mixed-source scans
  in both modes with/without prepared indexes, pose-index diagnostic completion,
  and cancellation while completing budget-failure evidence.
- Combined suite: 265 tests, 261 passed and four existing Windows-storage
  skips (66.193 s). It contains the prior 225 plus the nine new methods and
  31 current S0 observation/ambiguity tests. After keeping the pose-index path
  immediate, the nine affected methods were rerun separately.
- Original reviewer guard: four conflict cases reject and four consistent
  controls preserve their matrices. Original extra: both clean-secondary and
  marked-secondary contradictions reject. Four-edge ROI scratch audit: all
  48 implicit/default/full-domain and index combinations pass.

Representative command, with `PYTHONPATH=app:experiments/qt_quick:tests:tools`
and `QT_QPA_PLATFORM=offscreen`:

```sh
python -B -m unittest test_wall_raster_local_distances \
  test_marker_implicit_domain test_marker_occluded_structure \
  test_explicit_component_alignment test_s1_scan_observation_reuse \
  test_s1_fit_diagnostics test_s1_manual_alignment test_s1_structure_selection \
  test_pose_prior_contract test_s1_runtime_pose_prior \
  test_shared_recovery_residual_contract test_diagnostic_redaction \
  test_diagnostic_evidence test_g_cycle_replay test_client_live_runtime \
  test_s0_bright_controls test_s0_map_geometry_proof test_s0_floor_ambiguity -q
```

Generated evidence retained outside the source tree: `baseline-extra.prof`,
`candidate-extra.prof`, `baseline-extra-unprofiled.log`,
`candidate-extra-unprofiled.log`, `baseline-stages.json`, `candidate-stages.json`,
`clean-interleaved.json`, baseline/candidate `probe44.json`,
`regression-first.log`, `new-tests-final.log`, and reviewer guard/ROI logs.
The `benchmark_stages.py` evidence script uses the committed
`mixed_references()` fixture and a fresh frame-owned scan for each sample.

No capture, GUI rendering, first-correct-visible-pixel timing, production S1
resource, screenshot-specific reproduction, Windows installation, game action
or release was tested. The ten-candidate calculation still exceeds 500 ms;
this patch does not establish the 300 ms reopen or 500 ms initial-display
end-to-end targets. Independent review and any later integration are separate
from these producing-worker results.
