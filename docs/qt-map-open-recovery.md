# Bounded observation after map preparation expires

The initial preparation still has its original 1.05-second budget. Previously,
an unready frame at that boundary called `invalidate("map-closed")`. The passive
Qt timer only visited an already-open map, so a map that became visible shortly
afterward could not recover until another input edge. A deadline does not prove
that the player closed the map.

An unsuccessful preparation now withdraws display authority and records an
unconfirmed state. A separate recovery ticket allows at most eight single-frame
probes, at least 200 ms apart and within two seconds. It uses the existing Qt
tracking timer, Session worker, full-client capture and floor/geometry checks.
It sends no game input and does not extend the preparation budget. Only a fresh
valid frame can reach the normal solver and authorize pixels.

The ticket belongs to the input sequence, round, cycle, mode, author/display
generation and capture binding. Close, loss of foreground/binding, cancellation,
new accepted input, context changes or exhaustion retire it. Failed probes remain
failed receipts with a pending/exhausted reason; they do not cause repeated modal
errors. Default bounded preparation diagnostics preserve each actual outcome.

Recovery workers can publish before completing their tail. A cancelled worker's
late exception may withdraw only the exact render revision it published, never
a newer drag revision of that same token. Current worker failures still revoke.

## Verification

`test_client_map_open_recovery.py` covers both modes through real Qt queued input,
Coordinator, Session QRunnable, runtime, capture provider, authorization core and
presentation consumer. The original eight cases fail before the change; the
extended twenty cases pass after it, including late readiness without new input,
finite negative probes, close during capture, focus, cancellation, new input while
resources are busy, binding generation, author and round changes. The independent
`test_client_map_recovery_late_error.py` reproduces a cancelled recovery tail
clearing a newer drag in both modes before its owner guard is added.

Native APIs and hook installation are forbidden in these tests. Pixels, native
capture services, solver measurements and the image/window boundary are controlled
fixtures. These tests establish state behavior, not player-game visual accuracy or
why the original player's first preparation probes failed. No recognition
threshold, floor policy, game input, installed application or package is changed.
