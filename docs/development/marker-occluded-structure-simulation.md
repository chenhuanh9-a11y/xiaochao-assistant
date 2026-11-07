# Marker-occluded wall proposal simulation

Base: PR #101 head `e5defc7f4bf5bc1c66ae5ac90d13991359c54178`, verified through the GitHub API before work. Independent branch only; no secondary-component recovery patch, merge, packaging, install or game operation.

## Evidence and scope

Inputs are explicitly generated asymmetric occupancy maps with eight differently sized/placed holes and two outer notches. They are not production S1 resources or the user's screenshot reference. The provider and prepared-index loader are synthetic. Pixel extraction, contour/edge selection, seeds, point/wall fitting, held-out checks, regional validation and final canonical residual are the real shared production functions.

The initial 44-case same-base matrix varied scale (0.55/0.75/1.0/1.2), three exploration extents, blur, colored corner/edge occlusion, and dark broken corners, each with/without a real generated prepared index. A one-pixel saturated marker is expanded by the existing radius-two marker exclusion, so it does **not** remove only one evidence pixel. Forty measured complete corner chains became zero while 40 or 80 independent sharp wall fragments remained. The original solver had no chain seed and stopped before wall validation.

The change separates proposal from measurement:

- A candidate chain may span only missing samples explained by the existing HSV marker exclusion. Each arm retains a contiguous observed fragment at least as long as the existing 12-sample wall minimum. Unexplained dark/soft gaps and crop-edge unknown bands are not bridged.
- These inferred coordinates seed only the existing wall fitter, never measured point fitting. Seed/validation budgets, all-fragment raster checks, independent held-out planes, regional checks, final canonical residual and ambiguity rejection are unchanged.
- Proposal results are cached only in a frame-owned observation context, invalidated when node/observed arrays change. A multi-map scan reuses source-independent extraction, never projections or source-specific verdicts.
- Newly selected marker proposals check all independently constrained peer islands, including the original primary when recovery used a secondary. Peers require two separated wall planes on each axis (original 24-pixel spread), after source-unknown filtering. Three-wall HUD evidence is unknown, not a contradiction.
- The simulation separately exposed an **existing fast-path** defect: zero measured chains plus marker-occluded real walls could produce a valid primary pose while ignoring a contradictory peer. The same peer check now covers that explicit-domain case. Its observation flag is distinct from the actual selected new-proposal flag.

The peer check is attached to explicit observation-domain `_AlignmentHost.fit`, used by S1 manual/current-floor candidate scans, including `prepare_structure_scan`. It does not add peer enumeration to S0/other implicit-domain host calls. The proposal generator is part of the shared initial solver and can also be used by other callers of that solver. Tests cover both nightmare/difficult mode parameters and references with/without explicit known-wall masks; this is not proof of all production resource contracts.

## Same-base results

Among the 44 deterministic cases, 22 accepted previously and 32 accepted after the change. Ten additional acceptances are five colored-occlusion conditions in two index paths; the largest full-reference-canvas error against the generated transform is 0.542715 px. Their measured-chain count remains zero, and the selected candidates contain no point correspondences. These counts describe this synthetic matrix, **not game accuracy**. Twelve cases still reject; larger occlusions, dark gaps and soft boundaries are not broadly solved.

Negative coverage includes two repeated whole maps with at least two independently accepted poses, local wall changes in a mostly similar wrong reference, contradictory primary/secondary islands, unknown peer regions, insufficient HUD evidence, cancellation, and descriptor/validation/component budgets. A mixed case with 20 measured + 20 marker proposal chains changes from 666 measured seeds/validation-budget rejection to 1025 combined seeds/descriptor-budget rejection; it remains unresolved, not unique after truncation.

Two existing precision limits are explicitly pinned, not hidden by a relaxed oracle:

- Scale 0.55, exploration extent 250, no prepared index: full 650×650 canvas extrapolation error remains exactly 5.943738 px on the same baseline matrix. It is excluded from the ≤5 px precision assertion and tested for exact baseline-matrix preservation instead.
- The existing fast wall result on the wider two-island reference remains at 2.200165 px. Only its contradictory-peer acceptance changes to rejection; the original successful matrix is pinned. It is not counted as a new proposal recovery.

## Tests and review

Linux, Python 3.12.14, NumPy 2.2.6, OpenCV 4.12.0, Qt offscreen. Final command:

```sh
PYTHONPATH=app:experiments/qt_quick:tests:tools QT_QPA_PLATFORM=offscreen python -m unittest \
  test_marker_occluded_structure test_explicit_component_alignment test_s1_scan_observation_reuse \
  test_s1_fit_diagnostics test_s1_manual_alignment test_s1_structure_selection test_pose_prior_contract \
  test_s1_runtime_pose_prior test_shared_recovery_residual_contract test_diagnostic_redaction \
  test_diagnostic_evidence test_g_cycle_replay test_client_live_runtime -q
```

216 tests executed: 212 passed, 4 existing Windows-handle storage skips, 16.550 seconds. New generated-pixel module: 18 test methods, with scale/mode/index/cache subcases. Compile and diff checks passed. One existing arbitration fixture now also stubs the new independent proposal extractor; its fake measured-chain objects and original assertions are preserved. Independent second review reproduced the original contradictory-primary bug, verified its repair, and passed the geometry/arbitration/cache and diagnostic subsets. No real GUI, game or production S1 resource acceptance is claimed.

## Calculation cost

Same frame/reference/environment, one OpenCV thread, two warm-ups followed by 30 samples per configuration; NumPy linear percentiles. Static prepared-index generation is outside timing. Each sample includes the full host fit and, for cached rows, creating a fresh frame-owned scan. No capture, UI scheduling/rendering, catalog-wide scan or first-correct-visible-pixel endpoint is measured.

| Scene | Index | Scan | Baseline p50/p95/max ms | Candidate p50/p95/max ms |
| --- | --- | --- | --- | --- |
| Clear | no | no | 71.068 / 75.459 / 76.340 | 71.424 / 73.187 / 88.664 |
| Clear | no | yes | 80.578 / 86.855 / 88.089 | 75.095 / 78.684 / 80.220 |
| Clear | yes | no | 72.212 / 79.453 / 82.426 | 71.030 / 74.163 / 75.494 |
| Clear | yes | yes | 77.936 / 82.380 / 97.957 | 73.410 / 78.948 / 83.744 |
| Corner marker | no | no | 22.988 / 25.326 / 27.178 | 188.076 / 199.689 / 201.893 |
| Corner marker | no | yes | 31.161 / 32.478 / 36.189 | 201.185 / 215.669 / 219.138 |
| Corner marker | yes | no | 30.003 / 33.841 / 34.208 | 204.713 / 214.529 / 216.979 |
| Corner marker | yes | yes | 33.220 / 36.776 / 48.252 | 208.497 / 215.707 / 224.359 |
| Edge marker | no | no | 22.998 / 24.693 / 25.008 | 249.413 / 261.282 / 274.588 |
| Edge marker | no | yes | 33.915 / 36.780 / 38.918 | 262.908 / 294.016 / 305.261 |
| Edge marker | yes | no | 32.410 / 38.533 / 41.572 | 248.600 / 298.545 / 320.958 |
| Edge marker | yes | yes | 37.053 / 55.186 / 61.174 | 255.676 / 269.405 / 276.759 |

Baseline marker rows quickly **rejected**; candidate rows perform successful recovery with 74 seeds. This is extra work, not a speedup. Clear-path distributions show no sustained regression in this sample, not a general speed guarantee. Maximum computation exceeds 300 ms in some rows; neither the 300 ms reopen target nor 500 ms identification target is established. Multi-candidate cumulative cost and actual production maps remain unmeasured.
