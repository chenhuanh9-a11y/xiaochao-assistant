# 64710EF6 interaction and import recovery

The 1.0.5 candidate was reported slow and import failed. It remains held; this change does not claim player acceptance or publish a release.

## Changed paths

- Published author-catalog snapshots memoize availability hints. Resource replacement/profile replacement and explicit refresh invalidate the hints. Selected map/floor route file checks remain live; the hint never authorizes display.
- Control font rendering reads the actual shared ttk style before writing it. Repeating the same font must not broadcast a theme/layout invalidation; external/preview changes, new color styles and DPI changes still repair the font.
- Shell geometry uses settled-context snapshots for metadata and explicit final layout settlement before native rounding/no-activation. Other callers retain synchronous defaults and fresh-wrapper realization.

## Diagnostics

`--ui-interaction-audit NEW_OUTPUT` is an opt-in actual-frozen-executable sequence, using the real DailyAssistant/DesktopMainUI constructor and production callbacks. Own windows remain nonactivating/disabled. Selected-call profiler records bounded in-memory stage metrics, not arbitrary arguments or user data. Callback/idle checkpoints are not physical display latency or game FPS. The diagnostic event pump yields after each Tcl event, rather than draining an indefinitely replenished event queue.

`--ui-import-audit OWNED_RUN_ROOT NONCE` accepts only a fresh, ownership-marked copy of the real built installation under a fixed UUID run directory and five exact known ZIP hashes. It never substitutes ROOT/executable/MEIPASS. Its file-dialog seam returns those controlled ZIPs. A second, explicit diagnostic seam returns immutable admission-time lstat metadata for only the exact validated outer ancestors. It issues no new outside-root filesystem calls, leaves all other guard checks intact, and does not test concurrent outer-ancestor mutation. The real new resource-page buttons, background inspection/install, receipt, previews, backup removal and reimport execute in the copy. The guard writes only to the owned run root. Original `--ui-audit` output restrictions remain. Native file picker clicks and helper-required packages are outside this fixed five-author scope. Only the final outer exit/cleanup plus all reports can pass; an inner completed report alone is insufficient.

## Evidence so far

Source full-app runs (not frozen PE acceptance) identified repeated whole-catalog filesystem checks and repeated ttk font writes. After eliminating redundant font setters, one isolated source run measured later control expands 234/71ms and collapses 89/116/73ms; the first expand was still 1337ms and a later idle/layout spike remained. These are not a solved/performance-accepted claim, and a matched baseline/final PE run is still required. Earlier source diagnostic timeouts and stacks are retained; no broad pass is inferred from unit tests.

The fixed five ZIPs pass read-only source inspection. Actual new-page PE import has NOT yet been run at this snapshot. Existing 15 consumer tests and 82 dependency bindings do not establish that the import button succeeds.

## Validation

- Catalog availability, style idempotence, geometry ordering, existing control layout and independent opacity contracts.
- Diagnostic ownership/path/frozen identity/one-shot/nonce/ZIP-integrity contracts, nested profiling restoration and bounded aggregation.
- Final acceptance additionally requires actual PE complete startup/interaction/import in isolated data, source/resource binding, visual review and independent review. No user installation or game is changed by the tests.

## Follow-up checkpoint

The first actual frozen import run (9C349DF5) constructed the real app but exited 2 before any business stage: the diagnostic inventory's own no_links walker read above its owned root. This is a diagnostic failure, not reproduction of the player's import failure. The original failed PE report is preserved. A controlled PathGuard child process recorded the exact _inventory → no_links → lstat/stat → check stack. Inventory now stops at and validates the owned root, while pre-guard installation/input identity checks still validate every ancestor; read/write permissions are unchanged. Its red/green unit probe passed without filesystem attempts. A new actual PE run is required.

A matched source full-app operation sequence then isolated the cold-control invalidation: normal quick mode restyled its already-hidden legacy navigation button, creating a new ttk style and forcing whole-tree theme layout. Quick mode now leaves that hidden chrome alone; the visible diagnostic/settings modes still refresh their styles. One source run measured first control expansion 65ms and repeats 73ms (baseline same sequence first2230ms, repeats741/630ms). A later resize/idle stall around739ms remains and blocks a blanket fluidity claim. Physical rendering and new packaged measurements are still required.

The preferences display card now exposes both independent 0–100% values using the original controller IntVars and the same update coordinator as the game control. No duplicate queue is created. External controller changes cancel an older pending value on the affected lane, and page leave/hide/shutdown commits pending preferences before teardown. Source/Tk regression checks cover interleaving, persistence, zero/full values and preserving the other alpha/visibility/editor states. No author image or pose/identity matrix is changed.

## Final source checkpoint before replacement PE

The e551 actual interaction run completed its real frozen constructor and fixed 28-action sequence with exit 0, no filesystem denials/errors, and no foreground change. Control expands measured 93-111ms, but a later resize/idle still blocked the heartbeat for 719ms. This is not a performance acceptance pass. Profiling located that remaining stall in periodic UI-environment polling recursively draining Tk idle layout. Stable polls now read snapshots and the last naturally measured control size; real DPI/topology/scale changes and explicit user actions retain synchronous final measurement. A matched source run reduced that last heartbeat maximum to 168ms. The replacement PE must confirm it; source results are not presented-pixel latency.

The second actual import run reached the new resource-page inspection worker and returned its error through the real poll consumer. Its failure was still diagnostic: production inspect_package validates every lexical ancestor, while the private diagnostic guard denied fresh metadata above the UUID root. The production validator is unchanged. The admitted, exact-ancestor immutable snapshot now bridges this test-only protocol mismatch, and is removed back to guarded functions on cleanup. Reads/writes/enumeration of external content remain denied. Tests cover eleven denied capabilities and live owned-descendant metadata.

With that snapshot prototype, the five exact approved packages completed source production inspect/install/repeat/backup-remove/reimport in 61.401s, with all 88 protected dependencies and the custom marker unchanged and no guard denial. This is source-backend evidence only. The actual new-page PE sequence still must pass; the player's original archive/state failure has not been reproduced or explained.

Both preferences opacity sliders now use the same existing coordinator as the compact control, with independent canonical values. Thirteen new settings tests and eight existing independent-opacity tests passed. Source widget measurements show both complete rows and zero ancestor clipping. Owned-window PrintWindow captures still have paint artifacts and are not accepted as complete visual evidence; reliable own-window capture is being checked separately. No game or user installation was touched.

No replacement Setup is authorized from this checkpoint alone: actual new-page five-package UI flow, same-sequence performance, DPI/state/visual checks, independent final review and clean-source binding remain gates. The old 64710 artifact and all diagnostic failures are preserved.
