# Diagnostic-only main port

Target parent: `59bba3dcea90811964688960f0b8086e345f3c77`.
Source evidence: `8122d18d4a40ca03432af16c7311d7b93a68aa7b` (tree `66a8b60cb7cb31e43101c457f51c6f49cd2dc2b5`).
No ancestry merge or wholesale runtime replacement. Old Windows evidence does not validate this port.

## Necessary diagnostic dependencies / conflict inventory

1. New independent identity, privacy/schema, storage and recognition serialization modules; adapt existing AsyncEventLog, lifecycle and watchdog sinks. These have no recognition/input dependencies.
2. Main ProblemsService lacks persisted occurrence/checkpoint history. Port bounded problem checkpoints/history and session-aware IDs, separate recognition queue/stream and precise loss reporting. Do not port resource-error UI policy changes from old hotfixes.
3. Main export lacks automatic-sample separation, session grouping and structured redaction. Port only exporter/ownership and independent consent contracts. Existing image pixels and capture authorization remain untouched.
4. Main lacks the read-only runtime environment collector. Add bound-window observations and Qt snapshots; unknown values retain reason. No foreground names or unbound HWND/PID.
5. Main lacks automatic FailureSampleRecorder and its bounded ownership helpers. These are pure diagnostic prerequisites, default disabled; settings add only its boolean, not old shortcut/settings changes. Hook only current main failure entrances with original captured frame/current guards. Do not port map preparation retry/deadline changes.
6. Main runtime, coordinator, help/info and Tk contain overlapping newer behavior. Apply individual diagnostic hooks only, retaining main #78/#80/#81 map selection/input sequencing. Pointer/preparation diagnostics are not required for E3/E2/E1/F2 and are not a reason to import those historical runtime changes.
7. Capture provider main differs from old compatibility hotfix. Only remove foreign diagnostic metadata, if present; do not import fallback decisions or permission changes.
8. Noninterference fixtures must be copied from this exact main parent and hash recorded. Old 980d fixtures may not stand in for main equivalence.

Initial dry-run of the 980d-to-8122 patch failed on missing/changed diagnostic and integration files. This is an explicit adaptation, not a conflict-free rebase. Original feature branch remains untouched.

## Explicit differences from 8122

- No pointer/preparation instrumentor, resource-error presentation mapping, package identity collector, capture compatibility fallback, map-recovery retry behavior, static-reference hotfix or worker cancellation changes were imported.
- Main's worker error signal remains a string. Type/origin/action/stack are added as a separate worker diagnostic snapshot and copied to the already-existing failure receipt. Existing message, action failure and emission timing remain unchanged. Runtime-owned exceptions add the same metadata at the original catch. The original three exception coverage tests are restored with the string ABI assertion, not skipped.
- Main has a custom floor-region override. Its original `_observe` body and returned floor decisions are retained; added observations read the actual main result. Detector-band crop uses only the existing fixed detector's bounds, never alters the custom region or detection.
- Old manual failure sampling hooks referenced obsolete `frame_current` and `staged_observation` locals. The port reads main's existing capture-generation guard and last observation; stale/current capture and original fit verdict tests cover these seams.
- Old problem repeated-count checkpoint fixture expected only original rows; the port explicitly checks the additional summary row and its count rather than changing capacity or dropping that evidence.

## Local evidence before Windows CI

Shared diagnostics: 62 tests, 6 Windows-only skipped; automatic sampling: 17 passed; environment: 7 tests, 1 Windows-only skipped. Same-input/main capture authority + production sampler/history/export tests passed (39 before exception metadata addition). Restored exception/production/noninterference set: 30 passed.

Real elapsed synthetic mixed-stream load: 60.079 seconds, problems 1896 attempted/accepted/retained, recognition 711 attempts, 65 suppressed, 646 accepted/retained, zero queue/write loss; problem bytes 1849988, recognition bytes 305274. This Linux result is not Windows or installed-game evidence.

Combined main behavior modules: 229 tests executed, 2 failures + 9 errors all trace to Linux's absent ctypes.windll/WinDLL in existing Windows capture/input paths. No product or original test was weakened to hide that platform limit. These modules run unchanged on Windows CI. QML teardown warnings are retained in raw test logs, not treated as a visual acceptance claim.

## Legacy startup retention compatibility

Main's old startup prune deleted dated logs by name/mtime without ownership. That cannot safely coexist with the reviewed no-unknown-file-deletion boundary. The import/call and None-return ABI is retained, but unknown historical logs are explicitly preserved. `diagnostic_log_retention_status()` and exported `manifest.logRetention` expose that choice. Fixed-slot byte/file budgets apply to newly managed logs, not all historical files; no new dated files are added by the migrated producers. This is an intentional safety compatibility difference, not a claim of equivalent historical cleanup. Main's independent installed-download cleanup remains unchanged.

The actual Qt/Tk startup prune statements are executed in a temp boundary test; this is not a full Tk GUI launch. Existing real QtClient composition/startup tests remain in Windows CI.

## Automatic image retirement safety difference

Expiry and capacity eviction previously checked ownership then unlinked by pathname, permitting an external replacement to be deleted. No pathname image deletion is now attempted: expired or full automatic image storage reports `automatic-safe-retirement-unavailable`, preserves files and refuses new sampling. Images remain bounded at six / 12 MiB per mode under normal owned storage, but do not automatically resume after expiry/capacity; the Help text states this availability cost. Sampling remains default off; export choice remains separate. Only temp injected replacements were tested; no user image was deleted.

## Final automatic-journal safety adaptation

Automatic sampling mutation is now Windows-only: the existing pinned-root/exclusive-file helpers keep the journal handle open through inventory, validation and same-handle write/truncate. No journal `os.replace` or pathname deletion is used. POSIX returns `automatic-safe-storage-unsupported` without creating automatic storage. Text logging, manual existing evidence and text export remain available.

A saved but uncommitted image (including cancellation after PNG write) is retained with its durable intent, not deleted and not automatically promoted to exportable success. `automatic-image-recovery-required` / mismatch blocks additional automatic writes. Missing-image intent can still be cancelled safely through the held journal handle. This intentionally replaces prior automatic recovery of complete pending PNGs: completeness alone does not prove the final current/consent check succeeded. Normal completed Windows samples remain exportable only with independent export choice.

Windows-specific automatic mutation tests are explicitly skipped on Linux rather than mocked as native-safe. POSIX refusal has a separate test. Historical interruption fixtures remain and now assert preserved intent/non-export instead of automatic promotion. Unknown auxiliary files are still preserved and bounded in inventory.

Main's manual-picker help binding was restored verbatim; the old diagnostic source had omitted that newer action. Difficult observation diagnostics now log both fixed-detector floor and effective selected floor/source/region read. Same-input floor decisions remain equal to main; production bounded/writer test checks effective region floor 2 survives serialization.

Automatic storage validates the lexical absolute memory-root path and every ancestor before pinning; it does not resolve away memory-root symlinks or junctions. Root/ancestor redirect tests preserve an outside journal sentinel; Windows CI uses actual directory junctions, Linux uses real symlinks (not a native Windows safety claim).
