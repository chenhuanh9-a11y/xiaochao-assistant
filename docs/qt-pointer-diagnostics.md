# Bounded pointer and action diagnostics

This change adds evidence, not input or display authority. It is separate from
`71f7396` (retain authorized display during the post-drag observation). It does
not alter recognition thresholds, capture, deadlines, display expiry, or the
conditions that accept a pointer gesture. No screenshots are added or enabled.

## What is recorded

The existing target-window pointer mailbox, native input consumer and
Coordinator emit `pointer-input` decisions for down, sampled move, up and
cancellation. The reason distinguishes boundary rejection, inactive input,
busy resources, stale scope/sequence, unavailable current display, routing to a
floor/close control, and exceptions. Runtime receipt reasons remain failures.
Exceptions record their type, not arbitrary exception text in this input lane.

A down event can include `pressClient`, client size and the calibrated drag
boundary, but only if its existing capture context places the position inside
that client rectangle. These are client-relative physical pixels, not screen
coordinates. No move trajectory, key text, window title, HWND, profile, path,
account information or raw event dictionaries enter the new rows. A target
press without a map context records `pointer-context-unavailable` with no
position; it still cannot become a gameplay input. Unrelated-window presses
are discarded by the existing target/foreground filter.

`inputScope` contains epoch, round/cycle, frame and capture/frame generations.
`display` identifies the currently presented token/frame/generation. The
`state` object is explicitly a **cached Session snapshot**, not a fresh
authorization test. Runtime receipt reasons supply the actual acceptance or
rejection. These counters have different owners and must not be equated.

`action-receipt` records the receipt consumed by the Coordinator, including
success/error/stale flags and request/result scopes. It is not a new popup and
does not replace existing Problems records. It makes it possible to associate
a failed action with the surrounding drag/map state. Repeated delivery of the
same cached receipt is suppressed. A bounded serial `decision` distinguishes
consecutive gestures even when ProblemsService's presentation-state dedup
would otherwise discard their differing generation/token metadata.

## Cost and retention

No new hook, native query, image capture, service, worker or file is created.
The pointer lane has a 12-row burst and refills at 2 rows/second; the independent
receipt lane has a 6-row burst and refills at 1 row/second. State is constant in
size. Move decisions are emitted once per current gesture/outcome/reason;
later move calls are counted and reported at the next emitted edge. Source
presses with no context are limited to one diagnostic enqueue per 500 ms.
Rate-suppressed rows are counted in the next emitted row. Flooded intervals
therefore have explicit evidence gaps, not a promise of complete input replay.

Rows reuse ProblemsService's existing queue cap (64 including in-flight),
metadata limits (2 KiB diagnostics), 1 MiB per-process log rotation and one
previous file. No additional retention store is introduced. Existing files
from other process sessions are **not age-cleaned by this change**; this is
not a global storage-retention fix. No old evidence is deleted.

## Validation and limits

`test_client_pointer_diagnostics` drives the actual source mailbox, queued Qt
consumer, Coordinator, live runtime and Session receipt path for both modes,
then reads the actual ProblemsService output. It covers rejection followed by
successful drag, inactive map, expired context, exception privacy, disabled
input and recovery, move sampling, separate gesture identity, rate recovery,
and filtering unrelated positions. Native hooks/OS capture, solver evidence,
geometry conversion and window/image sinks are fixtures. It is not game or
installed-GUI acceptance.

The four initial contracts in each mode failed against the pre-change
production path: no structured rows existed. Subsequent tests also cover
privacy and limits. Existing drag/display cancellation, map input, floor input
and ProblemsService rotation tests must remain passing.

This code is not deployed to the user's current installation. Historical
missing pointer fields cannot be reconstructed. A future explicitly authorized
test build is required to collect these new records during a player's normal
testing. Geometry/visibility failures still require the existing opt-in
failure-frame sampling when pixel evidence is necessary. This diagnostic change
does not fix the separately reported preparation deadline or running-refresh
cancellation races.
