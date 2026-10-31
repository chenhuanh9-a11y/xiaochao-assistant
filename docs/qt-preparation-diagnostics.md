# Default map preparation evidence

The shared preparation loop already had a 1.05 s budget and 50 ms cadence.
This change preserves them, including existing readiness and withdrawal rules.
It addresses an evidence gap: Qt's `_PreparationHost._log` retained loop events
only in an unread instance list. Repeated capture OSErrors could therefore end
in a generic deadline with no capture failure in the standard exported log.

Each preparation now has an `openingId`, input sequence and immutable request
scope. Default `map-preparation` events identify begin, capture start/result,
observation result and the final caller branch. Each probe distinguishes a
frame from an exception, current geometry changes, obsolete work, unavailable
map/floor observation and a ready frame arriving after the deadline. The final
row preserves the loop stop reason and last probe outcome separately from the
user-visible receipt. Thus a context-change stop is not rewritten as proof of
an elapsed budget merely because the existing final receipt says deadline.

Capture metadata comes from that transaction's existing checks: backend or
pipeline, acquisition source when available, bounds, dimensions, DPI and
foreground result. No additional native query or capture is performed. A
failure with no acquired frame does not claim capture success; an unknown
foreground remains unknown. Durations are per stage and event timestamps are
provided by the normal log service.

Exception metadata preserves the class, errno/winerror, known product reason
and bounded product-only stack locations. Arbitrary exception text is omitted
and `messageOmitted` marks that limit. Paths, account data, exception locals,
window titles and screenshots are not copied. The actual exception still
follows the existing retry/propagation path; logging does not turn it into a
successful observation or change its action receipt.

Events pass through the existing runtime callback, queued Qt Coordinator,
ProblemsService and standard `events-*.jsonl` export. They do not require the
optional lifecycle-detail logger. A preparation allows at most 72 rows,
reserving its final row and counting suppressed details; the production loop
is already capped at 21 probes. The old instance list is also capped at 24.
Existing service limits apply: 64 queued/in-flight rows, 1 MiB rotation with
one previous file per process, bounded JSON metadata and bounded ZIP export.
Queue saturation can still lose rows and is reported by ProblemsService;
this is not an unlimited event replay or an age-retention change.

Tests use actual map input -> Session worker -> LiveRuntime preparation ->
ClientCaptureProvider -> default ProblemsService -> standard ZIP export in
both modes. Native access, pixels, solver measurements and window consumers
are controlled fixtures. The first six contracts fail on the parent path and
pass with diagnostics. Additional checks cover changed geometry, visible map
with unknown floor, running-capture cancellation, the row cap, and existing
capture guards. No installed-client or game acceptance is claimed.

This commit does not fix preparation-deadline recovery or the separate
running-refresh/new-drag cancellation race. Their causal reproductions and
repairs remain separate work. Collection of these new fields on the player's
machine requires a later explicitly authorized test package; none is produced
or installed here, and no historical missing evidence can be reconstructed.
