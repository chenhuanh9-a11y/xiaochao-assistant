# Session ingress verification

Source correspondence:

- `experiments/qt_quick/client_session.py`: `SessionService` dispatch, atomic desired/applied context, cancellation, snapshot recovery, resource leases, private bounded material work, final shutdown.
- `app/ui_action_state.py`: `ImmediateUiActionStateMachine` intent tokens, immediate acknowledgement, duplicate coalescing, settle/fail/cancel, stale completion feedback, and timestamps.
- `app/session_state_machine.py`: map-hotkey/probe/floor/current-event/foreground/confirmed-source pure policies. Non-S0 floor resolution requires an injected target floor contract; no cross-season fallback is introduced.

`SessionSequencer` remains the only session/generation/sequence writer. `ResultGate` keeps its existing CLI/slice API. It now stores terminal state before notifying observers, enforces a deadline at its exact cutoff, and discards superseded terminal bookkeeping.

## Runtime seam

Construct `SessionService` on its owner with an explicit `ISessionDispatcher`; WPF can supply `SynchronizationContextSessionDispatcher` with its actual UI context. Methods, reads and notifications belong to that owner. Worker dispatches execute off the owner and remain serial until the real call exits, even after cancellation/timeout. `DrainAsync` requires the owner to continue pumping; shutdown does not block or terminate a worker.

`ISessionRuntime.ReadSnapshot` must be bounded and safe on the owner. `DispatchAsync` returns an `OperationReceipt` and an immutable snapshot of its real committed state. Every receipt must echo the complete request context. Intentional changes to round/map/floor/resource/frame require the explicit result-context pair. The owner independently rereads current runtime context at completion. Receipt success does not construct identity, pose, round state, or display authority. A rejected context change preserves the actual runtime reason and restores the applied selection. Snapshot errors preserve observed round locks while withholding commands, identity/render JSON and live grants.

The typed protocol deliberately requires the explicit request echo rather than accepting Python's legacy, partially populated S0 receipt. Ingress improves the old pool queue by discarding cancelled queued foreground work and retaining one latest foreground intent. Private material work has at most two outstanding entries, cannot issue a session sequence or overwrite a foreground receipt, and requires the runtime's material-token validation. No live runtime implementation is supplied here.

Owner mutations commit state before delivering synchronous cancellation, recovery and UI callbacks. Reentrant intents therefore observe a complete state, replace the previous foreground intent, and finish their own state mutation before a worker can enter the runtime. The scheduler runs only after those callbacks drain and checks the existing sequencer/current foreground item before dispatch. A replacement closed by a token callback returns `Closed`; one replaced by a newer callback intent returns `Superseded`. This preserves same-turn feedback without putting a second writer around the sequencer. Reconfigure, cancel, refresh, timer expiry and completion expiry share this mutation boundary, so an older operation cannot restore its desired context over a newer callback intent.

Resource lease acquisition also finishes its mutation scope before returning: it grants only the token acquired by that invocation, if the session is still open and that same token remains current after notifications. A callback's shutdown, release, or replacement cannot leak a revoked token or transfer a different caller's replacement lease to a refused caller. Regression callbacks use the actual `Changed`, `Shutdown`, acquire and release APIs; no private lease state is rewritten or exposed for testing.

For a runtime implementing `ISessionRuntimeProgress`, refresh obtains snapshot plus ownership in one `ReadSnapshotFor` result and can retain the exact active request while accepting that producer's floor/frame/identity progress. A second later ownership query is not allowed because the same producer can advance context in between. Its original request context is never rewritten. A request-bound progress context is used only while that same ticket/action remains current. Losing previously accepted ownership cancels even when visible fields stay equal; external context changes and final receipt/request/output/current-context checks remain enforced. Attribution describes one read point, not a continuing authorization. The Runtime project tests exercise the real service with its real rank/fit producer; Session tests also reject a wrong ticket/malformed final echo, preserve atomic snapshot A when the same producer advances B before returning, and revoke equal-context work when its ownership is lost.

Completion itself now obtains the atomic snapshot/ownership result before processing either receipt or worker error. It does not depend on another `RefreshRuntime` after external revocation. Old success and old failure with equal visible context are rejected as `OwnershipRevoked`; final original-request echo and output/fresh-context checks still follow for current operations.

## Commands

Run these serially from `csharp/tests/StarMap.Session.Tests` after other builds have stopped:

```powershell
dotnet build-server shutdown
dotnet build
dotnet run --no-build
```

The executable runs `reference_oracle.py` against this checkout's unchanged Python pure state modules. It requires Python but no NumPy, OpenCV or Qt. Set `STARMAP_PYTHON` to the desired interpreter executable when necessary. To inspect independent expected values directly from the repository root:

```powershell
python csharp/tests/StarMap.Session.Tests/reference_oracle.py .
```

Async tests replace only the not-yet-migrated runtime. They use the real C# sequencer, result gate, action state and session service, with an owner queue and deterministic timer. Cases cover single-worker execution, immediate UI feedback, latest-queued replacement, every context identity, explicit request/result pairs, cancellation order, active and queued timeout, delayed timer delivery, context refusal/reconciliation, snapshot/worker errors, resource leases, bounded maintenance, owner-thread enforcement, closed results, and synchronous shutdown reentry. The replacement regressions register real cancellation-token callbacks that call `Shutdown` or dispatch a newer intent, then assert runtime-call history and queue ownership; sibling cases exercise reconfigure/cancel and both timer-first and completion-first expiry.

This is state/lifecycle verification, not geometry, live-runtime, GUI, capture, game, installation, or player acceptance. No global listener, capture, round, or window is started by the session module or its tests.

Final candidate execution: root reported a project-local bare build with zero warnings/errors and ran the executable. Independent readback of `full-csharp-validation/session-after-final-ownership-tests.txt` found all 43 named results `ok`; the implementation agent did not independently rerun them. The Runtime project's corresponding real-producer combination suite recorded 30/30 passed in `runtime-after-final-ownership/summary.json`. Earlier 41/29 evidence remains preserved, as do the new no-refresh counterexamples that failed before final ownership was checked. These results cover the stated fixtures and interleavings, not all concurrency schedules or real-window/game/display acceptance.
