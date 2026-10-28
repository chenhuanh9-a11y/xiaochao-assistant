# 6484 display recovery boundary

Base: c3c53a4518d1049b0f1030646fb0c2151645ddc9 (candidate 6484E779).
The installed executable hash and startup report matched that candidate.
No running application, game window, configuration or installation was changed.

## Confirmed failure

In the existing October 29 session, cycle 9 delivered a failed frame 12 worker
at 02:18:51.682 +08:00. The terminal classified it `stale-requeue`.
Two milliseconds later the consumer hid the visible frame 14 route with
`alignment-v2-structure-candidate-unavailable`. The current-frame consumer
opened an observation before discovering that this failed old frame had no
candidate. This is a concrete stale-failure display revocation, not evidence
that capture or the entire UI stopped.

The consumer now discards an unsuccessful stale worker before opening an
observation, recording retry failure, or changing display state. An eligible
same-cycle, same-reset, same-target request uses the existing coalesced fresh
recovery queue. Successful primary/recovery poses keep their existing cache
and current-frame validation path. Actual current-frame failures still enter
normal validation. Discarded results cannot increment the B1 failure policy.
Both modes use the same boundary.

## Independent display intent

The existing `sheetVisibilityEnabled` preference now controls aligned overlay
only. `referenceSheetVisible` independently controls the fixed reference.
Neither asynchronous overlay restore nor overlay-off delivery hides/shows the
reference. The reference position editor is available with overlay disabled.
Temporary lack of floor/identity/foreground evidence does not rewrite either
preference. Reference floor and non-provisional identity requirements remain.

The control command map exports `set_reference_sheet_visible(bool)`; existing
`toggle_sheet_visibility(source=...)`, `move_sheet`, and
`finish_reference_sheet_move` signatures stay intact. The control-view worker
owns its button layout separately.

## Other observed intervals and limits

- Cycle 56, around 02:20:11, had no detected floor after 14 capture attempts;
  both surfaces were absent. The coordinator inspected the saved actual frame
  and found the map UI but no legible floor triplet or wall geometry. This does
  not establish a floor misclassification; do not lower thresholds or display
  an unverified remembered floor.
- The temporary similarity member was invalidated on a floor change. Cycles
  58–66 still captured/ranked candidates but did not commit alignment. Formal
  B1 confirmation and display resumed in cycle 68, around 02:24:13. The earlier
  provisional interval also contained successful overlay commits. This is not
  evidence of permanent capture shutdown, nor proof that this patch resolves
  every reported interval. Automatic provisional-group recovery needs separate
  evidence and product policy; no identity guess is added here.
- 120 `display-resource-error-for-previous-target` events carried
  `display-resource-ready`; they were stale readiness deliveries, not 120
  failed image loads.

## Validation

Production-method stale failure regression failed on the base (old failure
entered current observation); it passes after the fix. Synthetic lifecycle
events exercise both modes, including 20 repeated old failures per mode,
closed/suppressed/reset/new-cycle/floor-change boundaries, current failures,
successful recovery results, and B1 policy isolation. These are not pixel
replays or game end-to-end timings.

47 targeted tests passed:

- `test_stale_failure_display.py`: 4
- `test_independent_visibility_runtime.py`: 5
- `test_reopen*.py`: 10
- `test_reference_position*.py`: 24
- `test_floor_observation_evidence.py`: 4

No live GUI/game acceptance, installer, release, or 300 ms claim. Private raw
logs and captured pixels are excluded from the repository.

## Follow-up resource/controller integration

Adopted the stopped owners' local commits: visibility controls `4ad57e7`
and managed author resources `17a5084`. The daily controller implements the
three browser APIs using background workers, cached snapshots, existing idle
gate and live-import lease. Only the matching completion can release that
lease; cancellation retains it until the writer has stopped. Removal requires
the inspected receipt hash and unchanged resource context. File publication
and resource factory refresh are consumed on the UI thread. Read-only listing
does not discard an already reviewed ZIP import. Removed files stay in the
module's recoverable backup; no user's installation was modified for tests.

`test_author_management_controller.py` adds eight controller cases, including
two real temporary-install/import/remove/reimport cycles through the actual
backend. Only the UI resource-factory IO is replaced for those cycles. Other
cases exercise off-UI inspection, receipt/idle checks, cancellation, stale
completion, context changes and preservation of reviewed imports. Control UI,
resource backend/UI and existing import suites were also run after integration.

New draft PR creation was rejected by automatic approval review; no PR was
created and the operation was not retried. Further publication awaits the
coordinator's requested explicit authorization. No new installer was built.

## Follow-up recovery investigation (same local session)

The later valid frames are distinct from the empty cycle 56 frame:

| Cycle | Valid B1 probe dispositions | Rankings | Verified identity candidates | Overlay commits |
|---|---:|---:|---|---:|
| 58 | 18 | 1 | none; 11 competition-limit hypotheses | 0 |
| 60 | 11 | 1 | none; 11 competition-limit hypotheses | 0 |
| 62 | 10 | 1 | none; 11 competition-limit hypotheses | 0 |
| 64 | 19 | 1 | none; 11 competition-limit hypotheses | 0 |
| 66 | 9 | 1 | none; 14 competition-limit hypotheses | 0 |
| 68 | 25 | 1 | nightmare-04; 2 competitive hypotheses tested | 11 |

Cycle 58 accepted B1 at 02:23:52.484 and reset the F1 provisional group.
That reset is justified: F1 group evidence cannot establish a unique B1 map.
The probe consumer subsequently observed valid B1 repeatedly but same-floor
observations returned without another identity ranking. The pose/provisional/
drift recovery paths all require a known map/group. This is a same-open
recognition retry gap, not a missing callback or stopped capture. Reopening
did rerank. In cycle 68, `map-locked` explicitly says `automatic-hard-gate`
and `complete-unique-side-structure-and-current-author`; no manual confirmation
was needed to recover. The earlier brief report did not establish this detail.

Added a shared unconfirmed-identity retry on changed probe pixels. It requires
current-frame floor evidence and an open foreground cycle, preserves all
identity/display gates, allows one recovery worker, and schedules at most once
per second using the existing passive-drift change threshold. Unchanged failed
evidence is not repeatedly ranked. The initial ambiguous ranking seeds the
comparison baseline. Import/removal idle checks include this worker.

Recovery rank deliveries additionally require the same cycle/floor/pack/mode/
surface, no newer identity, equal client bounds, and exact current pixel equality.
They cannot rewind the live frame. Changed late deliveries request a bounded
fresh retry. Exact equality is deliberately conservative: continuously animated
frames may delay acceptance. Exceptions emit a terminal diagnostic and release
the thread; future changed pixels can retry. Existing manual reranking semantics
are unchanged.

Eight synthetic production-method/state tests cover group reset, initial rank
baseline, unchanged/changed probes in both modes, single-worker/rate limits,
floor/lifecycle guards, late pixel changes, and worker errors. No recorded-frame
replay demonstrates that the newly added retries would have uniquely identified
cycles 58–66: those logs explicitly lack verified candidates. Thus this closes
the scheduling gap, not a claim of complete disappearance resolution or latency.

A provisional reference image is a separate presentation decision. It could be
shown only with a persistent “temporary candidate, map unconfirmed” label and
explicit floor/source state, with no implication of route authorization. The
current reference surface has no such label contract, so this patch preserves
its non-provisional gate. Automatically showing the top B1 candidate would have
shown nightmare-09 in cycles 58–62 while cycle 68 proved nightmare-04; it is not
a safe substitute for recovery.

Animation owner commit `718ae78` is also adopted without altering runtime
ownership. New repository authorization permits pushing this branch; the main
coordinator owns the one PR-creation retry and merge/build, avoiding duplicates.

## PR32 review repair: original producer shares the slot

Review reproduced a missed producer seam on `7add9e6`: the original
`_consider_floor_observation` pending-floor recovery launched an unregistered
`automatic-stable` rerank. After 1.1 seconds the changed-frame retry launched a
second engine call; the first result also lacked recovery context. The same
three real production methods were replayed before and after repair. Before:
two concurrent calls, source means `[0, 1]`, first producer context absent.
After: one call, source mean `[0]`, first producer context present. Both runs
joined their controlled workers; no game or native UI was used.

All `_rerank_current` entries now share a UI-owned request slot. The slot is
retained through queued terminal delivery, not just while the thread is alive.
Additional requests replace one pending intent and copy the latest frame only
when that intent can run. Requests bind cycle/capture generation, floor, pack,
engine, mode, surface and selected identity/profile. All unconfirmed entries,
including pending-floor and manual reranks, carry current-pixel provenance.
Original initial capture/ranking retains its existing separate capture lane.

Capture invalidation/close cancels the rerank owner and clears pending work;
the slot is released only by that owner's terminal. Exceptions and thread-start
failures also produce terminals. A duplicate old terminal cannot release a new
owner. Pending automatic work waits for fresh current-floor evidence and an
eligible foreground cycle; importing resources remains blocked while the slot
is occupied. A newly locked identity invalidates pending unconfirmed work.

`test_rerank_shared_lane.py`: 9 PASS. Covers the real floor producer in both
modes, a finished worker whose terminal is not yet consumed, 20 continuous
changes coalescing to the latest frame and accepting a valid result after
stabilization, cancellation/invalidation, stale duplicate terminals, errors,
thread-start failure, manual source preservation and fresh-floor gating.
Related reruns: native similarity runtime 28, prior recovery 8, live import
controller 15, resource controller 8, reopen 10, stale failure 4 PASS.

This fixes concurrent reranking and the missing producer guard. Exact-pixel
acceptance remains conservative: continuous pixel changes may keep results
rejected. Stabilization acceptance is tested, not a claim of universal
non-starvation, actual identity recognition on ambiguous frames, or complete
resolution of every reported disappearance.
