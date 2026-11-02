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
