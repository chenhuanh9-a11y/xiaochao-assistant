# Qt engine boundary

`build_engine_factories(exact_internal_root)` configures existing production engines without a Tk assistant. `EngineAdapter` owns offline diagnostics/replay; `client_live_runtime.LiveRuntime` adds explicit capture, round, shared alignment and core authorization. Construction starts no capture, listener, resource decoding or installed-configuration read. Algorithm work belongs to SessionService's worker.

```python
factories = build_engine_factories(sandbox / "_internal")
runtime = LiveRuntime(engine_factories=factories,
    capture_provider=explicit_client_capture_provider,
    allowed_read_root=WORK_ROOT,
    evidence_kind="live-client-capture")  # only actual native capture composition
session = SessionService(runtime=runtime)
```

Fixture compositions use `synthetic`, `recorded` or `unverified`. Action payloads cannot promote this kind. Their internal core transactions can be tested, but public `liveAuthorization` stays false.

## Lazy provider contracts

Call `inspect_engine_resources` on the worker to prepare the mapping. Until then `savedFrameCapabilities` reports `resource-validation-pending` and ranking is disabled. After validation it reports each mode separately, including exact missing/invalid metadata, typed products, entry indices or similarity data. Snapshots read cached state and never wait behind decoding. `engineConnected` means the adapter exists; live commands additionally require an injected capture provider and ready current-mode resources.

The exact root is the sandbox's `_internal`. Nightmare manifests must bind `resource-root` for every map before constructing `NightmarePack`; environment/development roots are not selected. Difficult requires declared native `runtimeInterfaces.identityPack`; no silent legacy asset fallback. Existing classes remain source owners: MapIdentityEngine/NativeDifficultCandidateEngine, NightmarePack/NativeDifficultPack, DifficultNativeContext, SharedStructureResourceStore, AuthorCoordinateAdapters and OptionalAuthorDisplays. The original version/binding validator is shared. Typed references pass existing loader, version, canvas, path and binding checks; the cache retains at most six products. Optional author absence does not block recognition. Memory paths are sandbox-local; ranking reads no user training history and writes no feedback.

## Runtime API

`dispatch(action,payload,cancelled)` and `execute(action,context,cancel_event)` are synchronous worker APIs. Cancellation accepts Event or a zero-argument predicate. Every runtime receipt echoes exact `requestContext={generation,mode,profileId,roundId,frameId}` and captures committed `resultContext={mode,profileId,roundId,frameId}`. SessionService must gate the request and reject cancellation, superseded output and post-commit snapshot drift before accepting intentional round/frame changes.

| Action | Behavior |
| --- | --- |
| inspect_engine_resources | Validate/build per-mode contexts on worker |
| bind_capture | Explicit hwnd and full-client region; records binding with capture disabled |
| start_round | Prepare current engine, explicitly enable bound capture; takes no frame |
| map | Existing map-key policy: close immediately if open; otherwise bounded map-open preparation and fit |
| capture_now | Fresh full-client capture and shared recognition/alignment |
| refresh_map | Bounded passive refresh; duplicates coalesce instead of queuing. Retain old display only through current observation/H verification, then renew or revoke before solving. |
| toggle_sheet_visibility | Toggle alignedVisible without changing pose/identity or stopping observation |
| close_map | Revoke display and close observation; retain verified structural pose |
| end_round / stop_round / reset_round / cancel | Revoke, clear core/identity/round state, disable capture |
| set_game_mode / set_context | Mode change stops/reset round; identical mode/profile preserves structural state |
| set_route_profile | Revoke display, retain identity/canonical pose, revalidate on next captured frame |
| set_floor | Reject unsupported request, revoke display, await actually observed floor |
| rank_saved_frame / load_saved_result / load_saved_pose | Offline operations with authority boundary below |

`invalidate(reason)` is a short owner-thread revocation path before canceling/queuing work. Resource-related reasons additionally stop capture/reset core and invalidate factory contexts. Old resource builds cannot publish afterward; prepare again after resource changes. Profile-only invalidation preserves structural pose. `configure_settings(settings)` records explicit solver preferences without resetting identity/pose; mode/profile remains a separate context action.

For native live authority, the injected provider must implement is_current(captured_context, generation=...). Runtime samples its binding generation immediately before acquisition, rechecks foreground/full size/screen bounds/DPI/binding after fitting before core authorization, and rechecks on every render_payload getter. Missing guards disable live start; drift revokes immediately. No additional frame is captured by this check.

Only exact full-client RegionCapture is accepted: uint8 BGR shape must match ClientCaptureContext.full_client_size, ROI must be `(0,0,w,h)`. Crop origins are never guessed. Screen placement derives from captured physical client bounds.

Map preparation reuses original `_prepare_map_open_frame`: 50 ms start cadence, 1.05 s visual budget, bounded probes, final probe near deadline, cancellation waits no longer than 25 ms. Real current map/floor evidence must settle; remembered floor is not substituted during preparation.

Both modes use one orchestration. Identity accepts only one existing identityHardGatePassed map and remains locked through pose failure. Alignment calls existing fast_structure_alignment.try_alignment, single_map_structure.solve_initial_structure, and same-floor single_map_structure.solve. Their disjoint support, wall holdouts, geometry and residual gates remain unchanged. The final transform is independently checked against actual typed geometry with selected_route_current_frame_residual and the shared canonical provenance gate, then passes AlignmentCoreV2.adjudicate/authorize/commit. Same-floor reopen retains committed linear pose. Floors without declared relations require independent current fits; no cross-floor H is inferred.

## Passive refresh and event callback

The composition root schedules refresh_map about every 200 ms only while the round/map is active, foreground is eligible and SessionService is idle. The runtime uses one nonblocking worker lock; duplicate passive requests return refresh-coalesced and take no frame. Visibility never gates capture/recognition/refresh.

A same-floor, same-resource current pose is checked with the original typed canonical current-frame residual. On success the existing core adjudicates/authorizes/commits a fresh frame binding atomically with persist_pose=False, preserving the structural root, identical RGBA object, H and stable render token. No external revoke notification or image reload occurs. On floor/unknown/closed/failed residual, the old authorization/token is revoked and a notification is emitted before shared pose solving. Native context/generation and cancellation gates still apply. The existing map_probe_transition requires two passive non-map misses to close the monitoring state; pixels disappear on the first miss, and unknown floor frames keep observation active.

set_event_callback(callback) accepts a thread-safe emitter. It is invoked on the calling worker/owner thread and must only queue a Qt signal, never update widgets/scene graph directly. Packets are JSON-safe with event=render-revoked/render-ready/aligned-visibility-changed, reason, token, epoch, mode/profile/round/frame/map/floor/cycle/generation and alignedVisible. Ready packets also identify unchanged refreshes. Callback failures never restore authority and are reported as bounded eventDeliveryError text. Snapshot pendingRenderToken is the current authorized token or empty. Parent gates queued completion and rechecks render_payload before presentation; stale revoke events must not hide a newer token. Parent must not hide merely because Session is busy with refresh_map.

alignedVisible is independent from display authorization. toggle_sheet_visibility and configure_settings(sheetVisibilityEnabled=...) change only the visibility preference. Parent persists it and hides/shows the aligned window while refresh continues, including while hidden.

## Render handoff

`snapshot()['render']` contains JSON-safe metadata and an opaque token, never NumPy pixels. `render_payload(token)` returns the exact current payload or None after revocation: immutable contiguous pixels in RGBA; measured 3x3 display-to-client matrix/canonicalMatrix; routeWidth/Height; captured clientWidth/Height and screen originX/Y; mode/profile/map/floor/round/cycle/frame/generation; authorization ID, evidence kind and exact resource identity. Parent presents only a token still current after Session accepts the receipt and hides on invalidation. No PNG is encoded per frame.

Confirmed optional author packages use original OptionalAuthorDisplays pixels and reviewed display bridge. Otherwise the existing shared-structure fallback displays the actual typed mask with identity display bridge and an explicit fallback label. Artwork never becomes identity/geometry evidence. Ordinary base-map and remaining legacy author display paths are not guessed here.

## Offline contract

EngineAdapter works without capture. rank_saved_frame accepts path/floor, optional sha256, budgetSeconds in `(0,10]`, and inputKind. It calls the injected real shared ranker, returning candidates/diagnostics with identityOrPoseAuthorized=False. Success means completion, not identity/display authority. The ranker's budget is cooperative and it has no cancellation callback; canceled output is discarded, with no hard wall-clock guarantee.

Saved paths stay beneath allowed_read_root (default WORK_ROOT); JSON <=512 KiB, frames <=64 MiB and 16 million pixels. Input is never written/copied. load_saved_result (aliases saved_result/replay) uses exact xingtu-qt-render-replay-v1 from prepare_replay and original load_render_replay. Child paths, sizes and hashes are verified. Synthetic/recorded/unverified kinds remain separate. Historical currentFrameVerified or even liveAuthorization=True cannot grant current authority. Historical IDs stay inside replay; schema has no recorded mode/cycle/frame/resource generation, so recordedMode=None. Raw caller logs must pass existing normalization first. load_saved_pose reuses RoundPose.snapshot/pose_for_floor; missing relations fail.

## Extraction and evidence

Shared owners with legacy reexports/delegation:

- app/map_observation.py: three original current-frame observation primitives.
- app/map_interface_observation.py: original visual map/floor detectors/constants.
- app/alignment_observation.py: original residual predicate/translation helpers and shared canonical evidence gate.
- app/shared_structure_context.py: original version/binding validator.
- app/map_open_preparation.py: original bounded full-frame preparation cadence.

Moved observation/translation declarations were compared with original ASTs. Nightmare/difficult algorithms and thresholds were not forked.

Isolated resource readback: nightmare 26 maps/78 typed floors; difficult 30 maps/58 typed native floors plus 2 explicitly declared legacy-clean map-29 floors. Only missing recognition/ was copied from parent's authorized baseline into ignored sandbox: 8 files, 8,407,309 bytes. No resource assets are tracked.

Ignored evidence in work/provider-evidence/:

- production-rank-evidence.json: actual shared rank on existing saved synthetic input. Nightmare accepted 4f5d6ac0-b1ae-4338-8d6e-b67cd1e3af51; difficult produced 30 candidates without acceptance on that nightmare image. No Tk/live-module import.
- live-runtime-refresh-chain.json: actual saved synthetic steady refresh reused identical token/pixels/H, renewed core authorization without structural-pose mutation or revoke (~34.85 ms); F2/F1 refresh transitions emitted revoke before real solver recovery (~168/151 ms). A synthetic closed frame revoked on first observation (~4.5 ms), monitoring closed on second. All public live authority stayed false; no Tk/live imports or native game calls.
- live-runtime-synthetic-floor-chain.json plus synthetic-floor2-provenance.json: real shared algorithms/core passed F1 → independently fitted F2 → independently fitted F1 → end, with typed floor-2 pixels used only to author the synthetic input. Generator H was never given to the runtime/solver. F2/F1 refreshes were about 165/148 ms in one run; this is not game or installed-display evidence.
- live-runtime-synthetic-chain.json: real map/floor detection, identity, shared pose, independent canonical residual, core commit, exact RGBA/H handoff, close/reopen/end with fake synthetic capture. Public liveAuthorization false throughout. One run: cold worker preparation~6.1 s, first identity+pose~975 ms, same-floor reopen~166 ms. These are not live or GUI end-to-end timings.

Deterministic suites test_client_engine.py (8), test_client_providers.py (8), and test_client_live_runtime.py (20) pass and cover isolation, both mode contracts, resource failures, bounded snapshots, cancellation/stale output, real core transactions, profile preservation, mode/end revocation, full-client rejection, map fade preparation/cancellation, entry reuse/rejection, and replay integrity.

The first-pose adapter preserves the legacy current-frame entry seam: fresh rank candidates are reverified by the original entry verifier and canonical residual before core submission. Bounded side entries verify the whole candidate set, require exactly one pose, and reject failure/ambiguity without broad fallback. No historical or caller-supplied H is accepted.

`entry-seed-reuse-same-input-chain.json` compares the existing saved synthetic input against the old production result after actual solving: identical input SHA, map/floor, canonical H and display H, stable refresh token, and public live authority false. This fixes an adapter omission which previously discarded the verified entry pose and independently refitted it. Old suju versus new structural-fallback pixels differ; matrix equality is not an assertion of identical artwork or universal game acceptance. One corrected run: map operation ~902ms and stable refresh ~35ms; these are single synthetic worker samples, not display latency targets.

Native game capture/shortcuts, real difficult pose inputs, every floor transition, live drag/zoom scheduling, ordinary base-map/legacy author display migration and 500/300 ms targets still require installed-client acceptance. The richer assistant's queue delivery, semantic floor-group sensors, temporal caches and 0059 acceleration are not claimed migrated by this bounded runtime. Shared solvers/core remain the source owners for subsequent work.

## Source-only manual controls after f3d3493

`LiveRuntime` now reuses `nightmare_candidate.RouteDisplayGate` for selection/confirmation. Snapshot exposes `candidateRows` (`key/mapId/name/floor/score/reason`), `selectedCandidateIndex` (-1 when absent), `selectedCandidate`, `identityLocked`, `candidateEditMode`, and `availableFloors` in the configured pack order. All pack maps remain visible; rank scores only reorder the unfiltered view. Identity lock is separate from display authorization.

Available commands are `previous_candidate`, `next_candidate`, `select_candidate` with integer `index`, `confirm_route`, `reselect_map` (alias `map_reselection`), `set_floor` with `floor`, and `cycle_floor`. Navigation wraps as legacy `_cycle_candidate_by_key` does and is unavailable while locked/closed. Reselection preserves the confirmed route until a different choice invalidates the gate. An explicit pending-selection state prevents periodic refresh from replacing an unconfirmed human choice, including selecting another map and then returning to the original map. Confirmation changes identity only; a changed map resets the old canonical group. The existing shared fitting, current-frame residual, typed resource bridge and core authorization remain mandatory.

Manual floor actions immediately attempt the target geometry, including when the sensor reports another floor or cannot read a floor. The worker takes one explicit current capture before fitting. `manualFloor` is the target, `observedFloor` is the independent sensor reading (empty when unknown), `floor` is the active target/core scope, and `floorSource` states `manual` or `automatic`. No cached H is relabeled to grant authority. Unknown sensor evidence retains a recognized map frame for manual correction without granting automatic floor/pose authority. `set_floor` with null/empty/`auto` restores sensor selection. Pending human candidate selection continues to require confirmation before fitting. Close/end/mode/resource invalidation and cancellation still reject late results.

`app/floor_observation_policy.py` now owns the original two-second manual hold, five-reading diagnostic window, latest-valid sensor rule, pending-click conflict/expiry, explicit-intent/hard-lock handling and floor-required/target transition decisions. Both legacy `_consider_floor_observation` and Qt runtime call these pure functions; the legacy method keeps its UI/logging and group recovery/projection side effects. At two seconds, the next valid sensor reading restores automatic mode; an unknown latest reading never inherits history and does not prematurely expire manual intent. The legacy front-anchor helper `bind_manual_front_pose_seed_from_current_frame` is specialized to `FrontDoorLiveTestEngine`; configured providers here use the production `MapIdentityEngine`, with its existing shared fit/entry-verification path. No Tk constructor or copied solver is introduced.

An optional four-argument `engine.qt_custom_display(profile, map_id, floor, reference)` hook runs only after resolving the typed structural reference and only supplies display pixels/bridge metadata. The injected callback owns the existing custom-library validation/render contract. It cannot supply identity, fitting evidence or core authority.

The targeted suites now pass 55 tests: runtime/policy 39, offline adapter 8, providers 8. Added fake/offline checks cover candidate wrapping, failed manual residual, fresh-capture manual confirmation, reselection preservation and pending-selection races, target-floor correction/unknown sensor in both modes, target reranking, cancellation/end during target fitting, lifecycle cleanup, and the display-only hook. No installed files, shipped binaries, native input/capture, builds or visible GUI launches were performed in this batch. Existing real-algorithm same-input evidence was reused without rerunning a map sweep. Information-only `work/provider-evidence/legacy-qt-same-input-comparison.json` consolidates the original 2.645px adapter-refit difference and the corrected exact H equality; it contains no image data.

Floor extraction checks: fake clocks exercise the exact two-second boundary in both modes, sensor/manual separation, unknown latest evidence, pending-click conflicts/expiry, hard locks versus explicit intent, and cancelled actions/mode cleanup. A bounded read-only AST comparison of the pre-extraction HEAD method and its delegated version produced identical state outcomes for 192 controlled combinations; no Tk object or native capture was created. Six existing floor-evidence, two-mode floor-to-rerank and native-group F2-release checks pass. A wider rerank suite additionally exposed one existing fixture error (`Host` lacks `_cancel_native_resource_validation` in `test_capture_invalidation_cancels_but_does_not_release_running_slot`); `_invalidate_capture_work` is AST-identical before/after this extraction, and that unrelated fixture was left unchanged. Original cross-floor group recovery/projection algorithms remain with their existing owners; the shared policy only decides the floor transition and never fabricates a pose or display grant.
