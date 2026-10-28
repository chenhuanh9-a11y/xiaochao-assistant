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
