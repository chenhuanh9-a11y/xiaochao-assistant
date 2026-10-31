# Running refresh must not revoke a newer drag

The first actual pointer delta cancels Session's old refresh worker, then the
runtime publishes a transient transform. Frame ID, display token and frame
generation can stay unchanged; `transformRevision` advances. A cancelled
worker therefore cannot decide ownership by token alone.

Four real worker schedules reproduced the fault in both modes:

- native capture backend entered, then cancellation makes ClientCaptureProvider
  raise its existing cancellation exception;
- full capture has returned, before the runtime consumes it;
- current-frame residual calculation has returned;
- the early cancellation check passed, but the subsequent floor-policy helper
  sees cancellation and returns no target.

The old completion revoked the global render via `runtime-error`,
`refresh-cancelled-or-stale` or `floor-policy-deferred`, clearing a newer valid
drag and preventing its following move. These paths already existed in the
8F8B Qt baseline; this is not evidence that all were introduced by 3FFE.

Cancelled/obsolete refreshes now retire with a stale receipt and can withdraw
only the unchanged authority scope they started with. A newer frame, token,
authorization or transform revision transfers ownership; old cancellation
cannot invalidate it. Unchanged old authority is still withdrawn when its
verification is cancelled. Checks and rejection mutations share the
runtime lock, including adjacent negative map/floor results, so a first delta
cannot land between the check and withdrawal. Residual failure, current
capture failure, true close, focus loss and round changes retain their safety
behavior. No fit, threshold, timeout, token lifetime or automatic input changed.

`test_client_running_refresh_drag.py` executes real Session QRunnables,
source mailbox, queued Qt input, runtime/core and final aligned consumer with
controlled worker barriers. Native input/OS pixels, initial solver evidence,
geometry conversion and window/image sinks are fixtures. Earlier capture and
residual schedules plus backend cancellation failed 6 tests and passed 6
boundary negatives before this change; the additional floor-policy schedule
failed in both modes during independent review. All four positive schedules
and the three safety negatives per mode are now covered (14 tests).

This closes those cancellation interleavings. It does not claim installed-game
acceptance, preparation-timeout recovery or drag-then-group-choice recovery;
those are separately reviewed changes.
