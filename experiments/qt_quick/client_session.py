"""Qt session state and cancellable runtime ingress; never starts a listener.

Injected runtimes implement snapshot() and dispatch(action, payload, cancelled).
The latter runs on one worker and returns an operation receipt, not recognition
authority. Only the runtime's snapshot is presented as engine state.
"""
from __future__ import annotations

from copy import deepcopy
import sys
import threading
import time
import traceback
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, Qt, Signal, Slot

from client_paths import APP_ROOT
APP = APP_ROOT
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from ui_action_state import ImmediateUiActionStateMachine
from session_state_machine import ranked_event_is_current
from season_context import normalize_season, context_key


# Existing product control command names, plus the injected diagnostic protocol.
COMMANDS = (
    "start_round", "end_round", "toggle_round", "capture_now",
    "previous_candidate", "next_candidate", "select_candidate", "confirm_route", "reselect_map", "cycle_floor",
    "toggle_sheet_visibility", "set_reference_sheet_visible", "move_sheet",
    "finish_reference_sheet_move", "cancel_reference_sheet_move",
    "_start_map_reselection", "_open_recognition_details", "set_floor",
    "set_game_mode", "set_route_profile", "rank_saved_frame",
    "load_saved_result", "load_saved_pose", "set_context", "cancel",
    "stop_round", "reset_round",
)


class _ResultSignals(QObject):
    done = Signal(object, object, object, object)


class _RuntimeWork(QRunnable):
    def __init__(self, runtime, action, payload, context, cancelled):
        super().__init__()
        self.runtime, self.action, self.payload = runtime, action, payload
        self.context, self.cancelled = context, cancelled
        self.diagnostics = getattr(runtime, "_diagnostics", None)
        self.signals = _ResultSignals()
        self.failure_diagnostics = {}

    def run(self):
        started = time.monotonic() if self.diagnostics is not None else 0
        if self.diagnostics is not None:
            self.diagnostics.record("worker-start", jobId=id(self), action=self.action,
                cancelled=self.cancelled.is_set(), frameId=self.context.get("frameId"))
        operation = "runtime-dispatch"
        try:
            receipt = self.runtime.dispatch(self.action, self.payload, self.cancelled)
            operation = "runtime-snapshot"
            state = self.runtime.snapshot()
            self.signals.done.emit(id(self), self.context, (receipt, state), None)
        except Exception as error:
            # Preserve main's original error signal/string before optional evidence.
            message = f"{type(error).__name__}: {error}"
            self.failure_diagnostics = dict(exceptionType=type(error).__name__,
                exceptionOrigin='session-worker:' + operation, exceptionAction=self.action)
            try:
                # Format frames only: do not evaluate a stateful __str__ twice.
                self.failure_diagnostics['traceback'] = ("".join(
                    traceback.format_tb(error.__traceback__)) + message)[-8192:]
            except Exception:
                self.failure_diagnostics['traceback'] = message
            self.signals.done.emit(id(self), self.context, None, message)
        finally:
            if self.diagnostics is not None:
                self.diagnostics.record("worker-end", jobId=id(self), action=self.action,
                    cancelled=self.cancelled.is_set(), elapsedMs=(time.monotonic()-started)*1000)


class SessionService(QObject):
    changed = Signal()
    actionRequested = Signal(str, dict)
    contextFinished = Signal(object)

    def __init__(self, runtime=None, parent=None):
        super().__init__(parent)
        self._runtime = runtime
        self._diagnostics = getattr(runtime, "_diagnostics", None)
        self._state = {}
        self._last_receipt = {}
        self._mode, self._profile = "nightmare", ""
        self._season = "s0"
        self._message = "Live engine is not attached."
        self._generation = 0
        self._closed = False
        self._jobs = {}
        self._maintenance = {}
        self._pending = None
        self._leases = set()
        self._actions = ImmediateUiActionStateMachine()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self.stale_results = 0
        if runtime is not None:
            self.refreshRuntime()

    def _available(self):
        available = self._state.get("availableCommands", ())
        if isinstance(available, dict):
            return {name for name, enabled in available.items() if enabled}
        return set(available or ())

    def _snapshot(self):
        state = deepcopy(self._state)
        pending = self._jobs.get(self._pending)
        if (state.get("season", "s0") != self._season
                or state.get("mode", self._mode) != self._mode
                or state.get("profileId", self._profile) != self._profile):
            # Desired author/mode can be pending without ending its actual
            # round. Keep lifecycle facts so guards cannot unlock mid-round;
            # old identity/render data is still withheld from the new context.
            state = {key: state[key] for key in ("roundActive", "roundPreparing",
                "roundWorkActive", "roundId", "cycleId") if key in state}
            state.update(contextPending=True, liveAuthorization=False)
        available = self._available() if not self._closed else set()
        commands = set(COMMANDS) | available
        state.update(
            season=self._season, contextKey=context_key(self._mode, self._season),
            mode=self._mode, profileId=self._profile,
            engineConnected=self._connected(), busy=self._pending is not None,
            pendingAction=pending[0].action if pending else "",
            message=self._message, generation=self._generation,
            availableCommands=sorted(available),
            commands={name: {"available": name in available,
                             "reason": "" if name in available else "Runtime command is unavailable."}
                      for name in sorted(commands)},
            resourceLocked=bool(self._leases), closed=self._closed,
            lastReceipt=deepcopy(self._last_receipt),
        )
        return state

    def _connected(self):
        return bool(self._runtime is not None and self._state.get("engineConnected", False)
                    and not self._closed)

    snapshot = Property("QVariantMap", _snapshot, notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)
    busy = Property(bool, lambda self: self._pending is not None, notify=changed)
    engineConnected = Property(bool, _connected, notify=changed)
    currentSeason = Property(str, lambda self: self._season, notify=changed)
    appliedSeason = Property(str, lambda self: str(self._state.get("season", "s0")), notify=changed)
    currentMode = Property(str, lambda self: self._mode, notify=changed)
    currentProfile = Property(str, lambda self: self._profile, notify=changed)
    # Desired context may change before its worker commits. Controls show the
    # last applied context throughout that transition, including a failed save.
    appliedMode = Property(str, lambda self: str(self._state.get("mode", self._mode)), notify=changed)
    appliedProfile = Property(str, lambda self: str(self._state.get("profileId", self._profile)), notify=changed)

    def _context(self):
        return dict(generation=self._generation, season=self._season, mode=self._mode,
                    profileId=self._profile, roundId=self._state.get("roundId", self._state.get("cycleId")),
                    frameId=self._state.get("frameId"))

    def _invalidate(self, reason):
        self._generation += 1
        self._last_receipt = {}
        for job, token in self._jobs.values():
            job.cancelled.set()
            self._actions.cancel(token, reason=reason)
        for job in self._maintenance.values():
            job.cancelled.set()
        self._pending = None

    def _restore_applied_context(self):
        """Reconcile a terminal context attempt, without erasing its receipt/error."""
        try:
            self._state = dict(self._runtime.snapshot())
        except Exception:
            # The last observed applied context is safer than the failed intent.
            # Do not grant authority when fresh runtime state is unavailable.
            self._state = dict(self._state, liveAuthorization=False,
                               engineConnected=False, liveEngineAvailable=False,
                               availableCommands=[], message="Runtime snapshot unavailable.")
        self._season = normalize_season(self._state.get("season", "s0"))
        self._mode = str(self._state.get("mode", "nightmare"))
        self._profile = str(self._state.get("profileId", ""))

    def _context_finished(self, work, context):
        if work.action == "set_context":
            self.contextFinished.emit((context["mode"], context["profileId"], context["season"]))

    @Slot()
    def refreshRuntime(self):
        """Read a bounded runtime snapshot on the owning UI thread."""
        if self._closed or self._runtime is None:
            return
        pending = self._jobs.get(self._pending)
        try:
            state = dict(self._runtime.snapshot())
            old = self._context()
            season = normalize_season(state.get("season", "s0"))
            mode = str(state.get("mode", self._mode))
            profile = str(state.get("profileId", self._profile))
            new_context = dict(old, season=season, mode=mode, profileId=profile,
                               roundId=state.get("roundId", state.get("cycleId")),
                               frameId=state.get("frameId"))
            if old != new_context:
                self._invalidate("runtime-context-changed")
            self._state, self._season, self._mode, self._profile = state, season, mode, profile
            self._message = str(state.get("message", "Runtime ready."))
        except Exception as error:
            self._state = dict(self._state, engineConnected=False, liveEngineAvailable=False,
                               liveAuthorization=False, availableCommands=[])
            self._season = normalize_season(self._state.get("season", "s0"))
            self._mode = str(self._state.get("mode", "nightmare"))
            self._profile = str(self._state.get("profileId", ""))
            self._message = f"Runtime snapshot unavailable: {error}"
            self._invalidate("runtime-snapshot-failed")
        if pending and pending[0].cancelled.is_set():
            self._context_finished(pending[0], pending[0].context)
        self.changed.emit()

    @Slot(str)
    def dispatch(self, action):
        self.dispatchWithPayload(action, {})

    @Slot(str, "QVariantMap")
    def dispatchWithPayload(self, action, payload):
        action = str(action)
        if self._closed:
            return
        available = self._available()
        manual_pick = False
        if self._runtime is not None and action == "confirm_route" and action not in available:
            # The picker's commit is gated by its own snapshot flag, not the
            # advertised confirm_route command that the existing buttons use.
            # Runtime _manual_action still validates mapId and round state.
            map_id = payload.get("mapId") if isinstance(payload, dict) else None
            if isinstance(map_id, str) and map_id:
                try:
                    manual_pick = bool(self._runtime.snapshot().get("manualMapPickAvailable"))
                except Exception:
                    manual_pick = False
        if self._runtime is None or (action not in available and not manual_pick):
            self._message = f"Unavailable command: {action}. Attach a runtime that supports it."
            self.changed.emit()
            return
        if self._leases:
            self._message = "Resource operation is in progress."
            self.changed.emit()
            return
        pending = self._jobs.get(self._pending)
        if pending and pending[0].action == "set_context" and action != "set_context":
            # A hotkey/terminal command can supersede a context worker without
            # going through stop(). Its payload must use applied runtime state,
            # and the coordinator must not retain the cancelled target forever.
            self._invalidate("context-superseded")
            self._restore_applied_context()
            self._context_finished(pending[0], pending[0].context)
        context = self._context()
        target = (context, deepcopy(dict(payload)))
        existing = self._actions.snapshot(action)
        if (existing is not None and existing.target_state == target
                and self._actions.accepts_background_result(existing.token)):
            self._actions.begin(action, target_state=target, visual_state="requested")
            return
        pending = self._jobs.get(self._pending)
        self._invalidate("superseded")
        if pending:
            self._context_finished(pending[0], pending[0].context)
        context = self._context()
        decision = self._actions.begin(action, target_state=(context, dict(payload)), visual_state="requested")
        data = dict(payload)
        data.update(context)
        work = _RuntimeWork(self._runtime, action, data, context, threading.Event())
        key = id(work)
        if self._diagnostics is not None:
            self._diagnostics.record("enqueue", jobId=key, action=action, frameId=context.get("frameId"),
                generation=context.get("generation"), inputAt=payload.get("inputCreatedAt", payload.get("floorClickCreatedAt")))
        self._jobs[key] = (work, decision.token)
        self._pending = key
        self._actions.mark_enqueued(decision.token)
        work.signals.done.connect(self._complete, Qt.QueuedConnection)
        self._message = f"Requested: {action}"
        self.changed.emit()
        self.actionRequested.emit(action, deepcopy(data))
        self._pool.start(work)

    @Slot(object, object, object, object)
    def _complete(self, key, context, value, error):
        item = self._jobs.pop(key, None)
        if item is None:
            return
        work, token = item
        current = (not self._closed and not work.cancelled.is_set()
                   and ranked_event_is_current(context["generation"], self._generation)
                   and context == self._context()
                   and self._actions.accepts_background_result(token))
        if self._pending == key:
            self._pending = None
        if not current:
            if self._diagnostics is not None:
                self._diagnostics.record("result-consumed", jobId=key, action=work.action,
                    accepted=False, reason="superseded-or-cancelled", cancelled=work.cancelled.is_set())
            self.stale_results += 1
            self.changed.emit()
            return
        if error:
            if work.action == "set_context":
                self._restore_applied_context()
            self._actions.fail(token, fallback_state="failed", reason=error)
            self._message = error
            self._last_receipt = dict(action=work.action, success=False, reason=error,
                                      message=error, error=True, errorPhase="operation",
                                      **work.failure_diagnostics)
        else:
            receipt, state = value
            receipt = receipt if isinstance(receipt, dict) else {}
            state = dict(state)
            state.setdefault("season", "s0")
            context_fields = ("season", "mode", "profileId", "roundId", "frameId")
            request_context = receipt.get("requestContext", receipt)
            result_context = receipt.get("resultContext")
            # Legacy receipts can only describe S0, never the selected season.
            if isinstance(request_context, dict):
                request_context = dict(request_context, season=request_context.get("season", "s0"))
            if isinstance(result_context, dict):
                result_context = dict(result_context, season=result_context.get("season", "s0"))
            if "requestContext" in receipt:
                request_mismatch = (not isinstance(request_context, dict)
                                    or any(name not in request_context or request_context[name] != expected
                                           for name, expected in context.items()))
            else:
                request_mismatch = (receipt.get("season", "s0") != context["season"]
                                    or any(name in receipt and receipt[name] != expected
                                           for name, expected in context.items()))
            # An intentional round/frame transition requires an explicit pair:
            # the original request and the committed output context. A fresh
            # snapshot must still match that output; late unrelated transitions
            # cannot smuggle an old receipt onto a new frame.
            if result_context is not None:
                state_drifted = ("requestContext" not in receipt or not isinstance(result_context, dict)
                                 or any(state.get(name, "s0" if name == "season" else None) != context[name]
                                        for name in ("season", "mode", "profileId"))
                                 or any(name not in result_context or name not in state
                                        or result_context[name] != state[name] for name in context_fields))
            else:
                state_drifted = any(name in state and state[name] != context[name]
                                    for name in context_fields)
            # A current explicit refusal intentionally retains the old applied
            # context. Preserve its real reason instead of calling it stale.
            rejected_context = (work.action == "set_context" and receipt.get("success") is False
                                and not request_mismatch)
            if request_mismatch or (state_drifted and not rejected_context):
                if self._diagnostics is not None:
                    self._diagnostics.record("result-consumed", jobId=key, action=work.action,
                        accepted=False, reason="runtime-result-context-mismatch")
                self.stale_results += 1
                self._actions.cancel(token, reason="runtime-result-context-mismatch")
                if state_drifted or work.action == "set_context":
                    self.refreshRuntime()
                self._message = "Discarded a result from a superseded runtime context."
                self._context_finished(work, context)
                self.changed.emit()
                return
            self._last_receipt = deepcopy(receipt)
            # Snapshot is runtime-owned, never inferred from a successful request.
            self._state = state
            self._season = normalize_season(self._state.get("season", "s0"))
            self._mode = str(self._state.get("mode", self._mode))
            self._profile = str(self._state.get("profileId", self._profile))
            if receipt.get("success", True) is False:
                self._actions.fail(token, fallback_state="failed", reason=str(receipt.get("reason", "rejected")))
            else:
                self._actions.settle(token)
            self._message = str(receipt.get("message", receipt.get("reason", self._state.get("message", "Request completed."))))
        if self._diagnostics is not None:
            self._diagnostics.record("result-consumed", jobId=key, action=work.action,
                accepted=not bool(error), reason="worker-error" if error else "current-result")
        self._context_finished(work, context)
        self.changed.emit()

    def dispatchMaintenance(self, action, payload):
        """Bounded serial cache work, independent of the visible command receipt.

        The runtime seals/validates its material token. This lane never replaces
        a foreground command, publishes its snapshot, or grants a display pose.
        """
        if (self._closed or self._runtime is None or self._leases
                or action != "remember_render_material"
                or len(self._maintenance) >= 2):
            return False
        token = payload.get("renderToken")
        if not token or any(job.payload.get("renderToken") == token for job in self._maintenance.values()):
            return False
        work = _RuntimeWork(self._runtime, action, {"renderToken": token},
                            self._context(), threading.Event())
        self._maintenance[id(work)] = work
        work.signals.done.connect(self._complete_maintenance, Qt.QueuedConnection)
        self._pool.start(work)
        return True

    @Slot(object, object, object, object)
    def _complete_maintenance(self, key, context, value, error):
        self._maintenance.pop(key, None)
        # A failed/stale cache warmup cannot replace an actual recognition
        # receipt or its error. The runtime retains its bounded diagnostics.

    @Slot(str, str, result=bool)
    @Slot(str, str, str, result=bool)
    def reconfigure(self, mode, profile, season="s0"):
        """Atomically enqueue one desired mode/profile context on the UI thread."""
        if self._closed:
            return False
        if self._leases:
            self._message = "Resource operation is in progress."
            self.changed.emit()
            return False
        if mode not in ("nightmare", "difficult"):
            self._message = f"Unknown mode: {mode}"
            self.changed.emit()
            return False
        try:
            season = normalize_season(season)
        except ValueError as error:
            self._message = str(error)
            self.changed.emit()
            return False
        profile = str(profile)
        if self._runtime is not None:
            actual = self._runtime.snapshot()
            pending = self._jobs.get(self._pending)
            starting = pending is not None and pending[0].action in {"start_round", "toggle_round"}
            if ((starting or any(actual.get(key) for key in ("roundActive", "roundPreparing", "roundWorkActive")))
                    and (mode, season) != (actual.get("mode", self._mode), actual.get("season", "s0"))):
                self._message = "请先结束本局，再切换游戏模式"
                self.changed.emit()
                return False
        if (season, mode, profile) == (self._season, self._mode, self._profile):
            pending = self._jobs.get(self._pending)
            if (self._runtime is None
                    or (actual.get("season", "s0"), actual.get("mode"), actual.get("profileId")) == (season, mode, profile)
                    or (pending is not None and pending[0].action == "set_context"
                        and not pending[0].cancelled.is_set())):
                return True
        if self._runtime is None or "set_context" not in self._available():
            self._message = "Unavailable command: set_context. Attach a runtime that supports it."
            self.changed.emit()
            return False
        pending = self._jobs.get(self._pending)
        self._invalidate("context-reconfigured")
        if pending:
            # Equal live targets returned above, so this is a different target.
            # End the old attempt before dispatch seals the new generation.
            self._context_finished(pending[0], pending[0].context)
        self._season, self._mode, self._profile = season, mode, profile
        # dispatchWithPayload seals the context from these desired values, so
        # neither member can be replaced by the previous runtime snapshot.
        self.dispatchWithPayload("set_context", {"season": season, "mode": mode, "profileId": profile})
        pending = self._jobs.get(self._pending)
        return pending is not None and pending[0].action == "set_context"

    @Slot(str, result=bool)
    def setMode(self, mode):
        return self.reconfigure(mode, self._profile, self._season)

    @Slot(str, result=bool)
    def setRouteProfile(self, profile):
        return self.reconfigure(self._mode, profile, self._season)

    @Slot(result=str)
    def acquireResourceLease(self):
        """Exclusive idle lease; caller releases in success/failure/cancel paths."""
        if not self._closed and not self._jobs and not self._maintenance and not self._leases:
            self.refreshRuntime()
        if (self._closed or self._leases or self._jobs or self._maintenance or self._state.get("roundActive")
                or self._state.get("roundWorkActive") or self._state.get("roundPreparing")):
            self._message = "Resources require an idle session."
            self.changed.emit()
            return ""
        lease = uuid4().hex
        self._leases.add(lease)
        self.changed.emit()
        return lease

    @Slot(str)
    def releaseResourceLease(self, lease):
        if lease in self._leases:
            self._leases.remove(lease)
            self.changed.emit()

    @Slot()
    def stop(self):
        cancel = getattr(self._runtime, "cancel_map_open_recovery", None)
        if callable(cancel):
            cancel("session-stopped")
        pending = self._jobs.get(self._pending)
        self._invalidate("cancelled")
        if pending and pending[0].action == "set_context":
            self._restore_applied_context()
            self._context_finished(pending[0], pending[0].context)
        self._message = "Pending requests cancelled."
        self.changed.emit()

    def cancelPending(self, reason):
        """Cancel a frame lifetime without ending its round or closing services."""
        cancel = getattr(self._runtime, "cancel_map_open_recovery", None)
        if callable(cancel):
            cancel(str(reason))
        pending = self._jobs.get(self._pending)
        self._invalidate(str(reason))
        if pending and pending[0].action == "set_context":
            self._restore_applied_context()
            self._context_finished(pending[0], pending[0].context)
        self.changed.emit()

    @Slot()
    def shutdown(self):
        if self._closed:
            return
        self.stop()
        self._closed = True
        self._leases.clear()
        self.changed.emit()
