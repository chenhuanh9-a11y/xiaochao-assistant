# 64710EF6 interaction and import recovery

The 1.0.5 candidate was reported slow and import failed. It remains held; this change does not claim player acceptance or publish a release.

## Changed paths

- Published author-catalog snapshots memoize availability hints. Resource replacement/profile replacement and explicit refresh invalidate the hints. Selected map/floor route file checks remain live; the hint never authorizes display.
- Control font rendering reads the actual shared ttk style before writing it. Repeating the same font must not broadcast a theme/layout invalidation; external/preview changes, new color styles and DPI changes still repair the font.
- Shell geometry uses settled-context snapshots for metadata and explicit final layout settlement before native rounding/no-activation. Other callers retain synchronous defaults and fresh-wrapper realization.

## Diagnostics

`--ui-interaction-audit NEW_OUTPUT` is an opt-in actual-frozen-executable sequence, using the real DailyAssistant/DesktopMainUI constructor and production callbacks. Own windows remain nonactivating/disabled. Selected-call profiler records bounded in-memory stage metrics, not arbitrary arguments or user data. Callback/idle checkpoints are not physical display latency or game FPS. The diagnostic event pump yields after each Tcl event, rather than draining an indefinitely replenished event queue.

`--ui-import-audit OWNED_RUN_ROOT NONCE` accepts only a fresh, ownership-marked copy of the real built installation under a fixed UUID run directory and five exact known ZIP hashes. It never substitutes ROOT/executable/MEIPASS. Its only file-dialog seam returns those controlled ZIPs. The real new resource-page buttons, background inspection/install, receipt, previews, backup removal and reimport execute in the copy. The guard writes only to the owned run root. Original `--ui-audit` output restrictions remain. Native file picker clicks and helper-required packages are outside this fixed five-author scope. Only the final outer exit/cleanup plus all reports can pass; an inner completed report alone is insufficient.

## Evidence so far

Source full-app runs (not frozen PE acceptance) identified repeated whole-catalog filesystem checks and repeated ttk font writes. After eliminating redundant font setters, one isolated source run measured later control expands 234/71ms and collapses 89/116/73ms; the first expand was still 1337ms and a later idle/layout spike remained. These are not a solved/performance-accepted claim, and a matched baseline/final PE run is still required. Earlier source diagnostic timeouts and stacks are retained; no broad pass is inferred from unit tests.

The fixed five ZIPs pass read-only source inspection. Actual new-page PE import has NOT yet been run at this snapshot. Existing 15 consumer tests and 82 dependency bindings do not establish that the import button succeeds.

## Validation

- Catalog availability, style idempotence, geometry ordering, existing control layout and independent opacity contracts.
- Diagnostic ownership/path/frozen identity/one-shot/nonce/ZIP-integrity contracts, nested profiling restoration and bounded aggregation.
- Final acceptance additionally requires actual PE complete startup/interaction/import in isolated data, source/resource binding, visual review and independent review. No user installation or game is changed by the tests.
