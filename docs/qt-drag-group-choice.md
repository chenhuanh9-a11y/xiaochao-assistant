# Explicit group choice after dragging

A drag leaves a transient offset for the current open-map cycle. Previously,
manual group selection reached `_solve_observation` with the old observation,
then the transient projection branch returned without fitting another member on
the same floor. Subsequent refreshes only probed map visibility and never retried
the retained selection. Closing/reopening could retire that state; the block was
not permanent across lifecycles.

An explicit choice now captures a fresh full-client frame and fits only that
registered target using its own current geometry. The target observation and
authorization are staged separately from the source. The source frame, display,
identity and root pose remain unchanged while the target is being checked. A
target geometry failure keeps the player's desired member, and a later refresh
can retry on new pixels. Normal dragging without a choice still does observation
only. No automatic member competition or geometry threshold changes are added.

Image.Ready acceptance atomically commits the target frame, pose, identity and
pointer context, then retires the old transient offset. Source scope, render
revision, manual intent, floor, author and capture context protect this transaction.
A new drag movement expires old target work. A target acknowledgement during an
already-pressed drag cannot replace its pointer frame. Rejected material retains
the source and desired selection for a later retry. Fresh evidence that the map
or floor is unavailable still withdraws source display authority.

Target pose diagnostics name the staged frame and mark `pendingMaterial` with its
source frame; acceptance records the actual committed frame. Optional existing
failure sampling uses the target frame/floor metadata. Sampling remains opt-in.

## Verification

The original eight tests in `test_client_drag_group_choice.py` produced six
failures and two passing no-selection controls before the fix. The extended
tests cover both modes, fresh target pixels, an independently measured target
matrix, material acceptance, failed geometry/retry, real close, and missing map
or floor evidence. `test_client_drag_group_choice_boundaries.py` adds twenty
checks for source retention, atomic frame commit, repeat drag/refresh, material
rejection, competing drag while solving, floor/author changes and late material.
The down/ack/move interleaving was independently reproduced failing before its
additional ownership guard. All 32 focused checks pass after the final change.

Tests execute the actual queued input, Coordinator, Session worker, runtime,
authorization core and consumer path. Native capture services, pixels, solver
measurements and image/window boundaries are fixtures; native APIs/hooks are
forbidden by the runner. This proves state contracts, not real-game geometry
accuracy or that all reported player failures have been explained.
