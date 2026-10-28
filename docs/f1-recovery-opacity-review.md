# Shared floor recovery and independent opacity

Base integration: main a1e0055be21667c1d82177844f3360f0f143ace3 (PR36/38 included).

## Proven recovery defect

D234 shared `single_map_structure.solve` could return success with a raw mask residual missing the coordinate pack and reviewed bridge provenance required by the unchanged V2 consumer. The worker accepted the finite matrix; current-frame candidate construction rejected it before frame equivalence. The shared producer now uses the existing canonical geometry validator, including its pack/bridge checks. Native shared coordinates are explicitly passed even when the selected display is an author route. The entry-scale fallback obeys the same contract. No floor, geometry, ambiguity, identity or frame-equivalence threshold is relaxed.

An isolated comparison loads the exact D234 producer and the fixed producer with the same controlled matrix/pixels/geometry and real worker, extracted poll branch, owner and core consumers. At source generation 408/current 412, old success yields no commit; fixed success commits. This is a synthetic contract counterexample, **not replay of the missing original cycle115 matrix**. Both modes, changed pixels, changed floor, cancellation, closed map, missing pack/bridge and geometry rejection are covered.

Private observations remain outside Git. The first saved F1 failure was read as F1, but contained zero supported corner chains (44 nodes); this is legitimate insufficient structure. Some stale results already committed in D234. Later difficult-mode click-F1 observations changed from F2 to F1 after about 180 ms, followed by geometry recovery around two seconds. Those events do not prove a general floor-highlighter misread. Successful worker matrices paired with later current frames were not retained, so this patch cannot explain every field failure.

## Opacity lifecycle

`sheetOpacity` remains alignment alpha; `referenceOpacity` independently controls the fixed reference. Missing/invalid new preference falls back to the old alpha. Separate variables and bounded timers update only current authorized/displayed pixels. Reference updates do not reload resources or reveal a hidden/stale reference. Both pending values flush before the existing settings save. Editing uses a temporary minimum .4 without changing the reference preference; ending/cancelling clears the editor pixels. Normal presentation reads the independent preference, including zero. Close cancels both runtime timers.

Tests use native/widget/timer I/O substitutes. The retained compositor test preserves the actual core authorization while changing 0 to 100 without rerunning recognition. The position-controller test writes real temporary settings and checks independent alpha through edit completion/cancellation. No game input, real HWND test, installed application modification, or build was performed. Neither initial-fit 500 ms nor reopen 300 ms end-to-end targets have been measured by this work.
