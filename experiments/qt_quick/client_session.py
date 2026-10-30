"""Qt session state and cancellable runtime ingress; never starts a listener.

Injected runtimes implement snapshot() and dispatch(action, payload, cancelled).
The latter runs on one worker and returns an operation receipt, not recognition
authority. Only the runtime's snapshot is presented as engine state.
"""
from __future__ import annotations

from copy import deepcopy
import sys
import threading
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, Qt, Signal, Slot

from client_paths import APP_ROOT
APP = APP_ROOT
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from ui_action_state import ImmediateUiActionStateMachine
from session_state_machine import ranked_event_is_current


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
        self.signals = _ResultSignals()

    def run(self):
        try:
            receipt = self.runtime.dispatch(self.action, self.payload, self.cancelled)
            state = self.runtime.snapshot()
            self.signals.done.emit(id(self), self.context, (receipt, state), None)
        except Exception as error:
            self.signals.done.emit(id(self), self.context, None, f"{type(error).__name__}: {error}")


class SessionService(QObject):
    changed = Signal()
    actionRequested = Signal(str, dict)

    def __init__(self, runtime=None, parent=None):
        super().__init__(parent)
        self._runtime = runtime
        self._state = {}
        self._last_receipt = {}
        self._mode, self._profile = "nightmare", ""
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
        if (state.get("mode", self._mode) != self._mode
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
    currentMode = Property(str, lambda self: self._mode, notify=changed)
    currentProfile = Property(str, lambda self: self._profile, notify=changed)
    # Desired context may change before its worker commits. Controls show the
    # last applied context throughout that transition, including a failed save.
    appliedMode = Property(str, lambda self: str(self._state.get("mode", self._mode)), notify=changed)
    appliedProfile = Property(str, lambda self: str(self._state.get("profileId", self._profile)), notify=changed)

    def _context(self):
        return dict(generation=self._generation, mode=self._mode,
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

    @Slot()
    def refreshRuntime(self):
        """Read a bounded runtime snapshot on the owning UI thread."""
        if self._closed or self._runtime is None:
            return
        try:
            state = dict(self._runtime.snapshot())
            old = self._context()
            mode = str(state.get("mode", self._mode))
            profile = str(state.get("profileId", self._profile))
            new_context = dict(old, mode=mode, profileId=profile,
                               roundId=state.get("roundId", state.get("cycleId")),
                               frameId=state.get("frameId"))
            if old != new_context:
                self._invalidate("runtime-context-changed")
            self._state, self._mode, self._profile = state, mode, profile
            self._message = str(state.get("message", "Runtime ready."))
        except Exception as error:
            self._state = {}
            self._message = f"Runtime snapshot unavailable: {error}"
            self._invalidate("runtime-snapshot-failed")
        self.changed.emit()

    @Slot(str)
    def dispatch(self, action):
        self.dispatchWithPayload(action, {})

    @Slot(str, "QVariantMap")
    def dispatchWithPayload(self, action, payload):
        action = str(action)
        if self._closed:
            return
        if self._runtime is None or action not in self._available():
            self._message = f"Unavailable command: {action}. Attach a runtime that supports it."
            self.changed.emit()
            return
        if self._leases:
            self._message = "Resource operation is in progress."
            self.changed.emit()
            return
        context = self._context()
        target = (context, deepcopy(dict(payload)))
        existing = self._actions.snapshot(action)
        if (existing is not None and existing.target_state == target
                and self._actions.accepts_background_result(existing.token)):
            self._actions.begin(action, target_state=target, visual_state="requested")
            return
        self._invalidate("superseded")
        context = self._context()
        decision = self._actions.begin(action, target_state=(context, dict(payload)), visual_state="requested")
        data = dict(payload)
        data.update(context)
        work = _RuntimeWork(self._runtime, action, data, context, threading.Event())
        key = id(work)
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
            self.stale_results += 1
            self.changed.emit()
            return
        if error:
            self._actions.fail(token, fallback_state="failed", reason=error)
            self._message = error
            self._last_receipt = dict(action=work.action, success=False, reason=error,
                                      message=error, error=True, errorPhase="operation")
        else:
            receipt, state = value
            receipt = receipt if isinstance(receipt, dict) else {}
            state = dict(state)
            context_fields = ("mode", "profileId", "roundId", "frameId")
            request_context = receipt.get("requestContext", receipt)
            result_context = receipt.get("resultContext")
            if "requestContext" in receipt:
                request_mismatch = (not isinstance(request_context, dict)
                                    or any(name not in request_context or request_context[name] != expected
                                           for name, expected in context.items()))
            else:
                request_mismatch = any(name in receipt and receipt[name] != expected
                                       for name, expected in context.items())
            # An intentional round/frame transition requires an explicit pair:
            # the original request and the committed output context. A fresh
            # snapshot must still match that output; late unrelated transitions
            # cannot smuggle an old receipt onto a new frame.
            if result_context is not None:
                state_drifted = ("requestContext" not in receipt or not isinstance(result_context, dict)
                                 or any(name not in result_context or name not in state
                                        or result_context[name] != state[name] for name in context_fields))
            else:
                state_drifted = any(name in state and state[name] != context[name]
                                    for name in context_fields)
            if request_mismatch or state_drifted:
                self.stale_results += 1
                self._actions.cancel(token, reason="runtime-result-context-mismatch")
                if state_drifted:
                    self.refreshRuntime()
                self._message = "Discarded a result from a superseded runtime context."
                self.changed.emit()
                return
            self._last_receipt = deepcopy(receipt)
            # Snapshot is runtime-owned, never inferred from a successful request.
            self._state = state
            self._mode = str(self._state.get("mode", self._mode))
            self._profile = str(self._state.get("profileId", self._profile))
            if receipt.get("success", True) is False:
                self._actions.fail(token, fallback_state="failed", reason=str(receipt.get("reason", "rejected")))
            else:
                self._actions.settle(token)
            self._message = str(receipt.get("message", receipt.get("reason", self._state.get("message", "Request completed."))))
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
    def reconfigure(self, mode, profile):
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
        profile = str(profile)
        if self._runtime is not None:
            actual = self._runtime.snapshot()
            if actual.get("roundActive") and mode != actual.get("mode", self._mode):
                self._message = "请先结束本局，再切换游戏模式"
                self.changed.emit()
                return False
        if mode == self._mode and profile == self._profile:
            return True
        if self._runtime is None or "set_context" not in self._available():
            self._message = "Unavailable command: set_context. Attach a runtime that supports it."
            self.changed.emit()
            return False
        self._invalidate("context-reconfigured")
        self._mode, self._profile = mode, profile
        # dispatchWithPayload seals the context from these desired values, so
        # neither member can be replaced by the previous runtime snapshot.
        self.dispatchWithPayload("set_context", {"mode": mode, "profileId": profile})
        pending = self._jobs.get(self._pending)
        return pending is not None and pending[0].action == "set_context"

    @Slot(str, result=bool)
    def setMode(self, mode):
        return self.reconfigure(mode, self._profile)

    @Slot(str, result=bool)
    def setRouteProfile(self, profile):
        return self.reconfigure(self._mode, profile)

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
        self._invalidate("cancelled")
        self._message = "Pending requests cancelled."
        self.changed.emit()

    def cancelPending(self, reason):
        """Cancel a frame lifetime without ending its round or closing services."""
        self._invalidate(str(reason))
        self.changed.emit()

    @Slot()
    def shutdown(self):
        if self._closed:
            return
        self.stop()
        self._closed = True
        self._leases.clear()
        self.changed.emit()
