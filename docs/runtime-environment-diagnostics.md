# Runtime environment export inputs

`app/runtime_environment_diagnostics.py` has no Qt dependency. On the UI thread,
call `freeze_runtime_inputs(screens=plain_screen_list, input_snapshot=input.snapshot,
capture_snapshot=capture.snapshot, bound_identity={"hwnd": hwnd, "pid": pid})`.
Use the existing binding's PID rather than searching for a game process. Screen
records should include real geometry/available geometry, devicePixelRatio,
logicalDotsPerInch and physicalDotsPerInch. Pass hook registration/liveness as
observed by the input provider; configured shortcuts alone do not prove hooks.

Pass the resulting frozen `RuntimeInputs` to the export worker and call
`collect_runtime_environment(inputs)`. The returned JSON-compatible dictionary
includes OS version, supplied screen/input/capture values, current Qt process
integrity, bound game process integrity, native window presentation and DPI.
Missing snapshots, unsupported platforms and failed native reads are explicitly
`status: unavailable` plus a reason. Token access uses only QUERY_LIMITED_INFORMATION
and TOKEN_QUERY and always closes owned handles. No elevation, process scan,
focus change, capture probe, input injection or file read occurs.

`capture.snapshot.lastCaptureObservation` contains the most recent attempted
capture's generation, status, durationMs and existing acquisition backend. Failed
attempts retain the pipeline name and phase, not an invented successful backend.
Disabled/early-cancelled requests do not count as capture attempts. Durations
measure the synchronous provider path after initial cancellation, not UI-visible
end-to-end latency. An older observation can be recognized by its generation.
Window presentation reports minimized/maximized/normal; exclusive versus
borderless fullscreen is unavailable because geometry cannot establish it.

Validation uses synthetic native refusal/stale binding/non-Windows paths and
synthetic capture pixels. A current-process token smoke query can validate the
native API locally; it does not establish live Qt/game acceptance.

Bound-window diagnostics also query `GetClientRect` / `ClientToScreen` for current
client pixels and screen bounds, and `MonitorFromWindow(hwnd, 0)` /
`GetMonitorInfoW(MONITORINFO)` for the intersecting display's geometry and work
area. Device names, titles, account names and other applications are not queried.
Client and monitor failures are separate unavailable values, so a partial native
read does not erase the successful fields. Geometry is evidence only and does not
change capture authorization, coordinate conversion or fullscreen classification.
Reference: [GetClientRect](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getclientrect),
[GetMonitorInfoW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getmonitorinfow).

Source runs report this checkout's HEAD, labelled `checkout-head-unbuilt` with
working tree verification not performed. Packaged runs continue to use their
existing installation/release metadata. Missing git metadata stays unavailable;
no commit is synthesized. Exported Qt summaries retain the observed Qt version.
