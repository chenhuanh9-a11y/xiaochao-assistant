"""Bounded metadata for the app's existing pointer/receipt consumers only.

No native calls, images, hooks, extra worker, or authority checks live here.
The event is evidence of a consumer decision, never a display authorization.
"""
import math
import re
import time


def code(value, fallback="unclassified-reason"):
    # Production reason codes; exceptions/free text must never enter this lane.
    return value if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,95}", value) else fallback


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and abs(value) <= 2**53 else None


def identity(value):
    if type(value) is int:
        return number(value)
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F:.-]{1,128}", value) else None


def scope(value):
    value = value if isinstance(value, dict) else {}
    return {key: identity(value[key]) for key in
        ("epoch", "roundId", "cycleId", "frameId", "generation", "captureGeneration", "token")
        if key in value}


def map_attempt(value):
    """Fixed diagnostic identifiers only; never copy a request or capture frame."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("attemptId", "openingId", "recoveryId"):
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(r"[0-9a-fA-F]{32}", item):
            result[key] = item.lower()
    for key in ("inputSequence", "requestGeneration", "inputCreatedAt"):
        item = number(value.get(key))
        if item is not None:
            result[key] = item
    return result


def receipt_fields(receipt):
    receipt = receipt if isinstance(receipt, dict) else {}
    result = {key: receipt[key] for key in ("success", "stale", "error", "probeRequired", "persistentPoseUpdated")
        if type(receipt.get(key)) is bool}
    result["reason"] = "operation-exception" if receipt.get("error") else code(receipt.get("reason"))
    return result


class InputDiagnostics:
    """UI-thread state: 12-row burst + 2 rows/s, moves once per gesture/outcome.

    Receipts have a separate 6-row burst + 1 row/s so pointer floods cannot
    starve them. Suppressed counts accompany the next emitted row. Constant
    storage; existing ProblemsService owns queue, rotation and shutdown.
    """
    def __init__(self, emit, clock=time.monotonic):
        self.emit, self.clock = emit, clock
        now = clock()
        self.buckets = {"pointer-input": [12., now, 0], "action-receipt": [6., now, 0]}
        self.serial = 0
        self.gesture = self.last_move = None
        self.moves = 0
        self.last_receipt = None

    def _take(self, stage):
        capacity, rate = (12, 2) if stage == "pointer-input" else (6, 1)
        bucket = self.buckets[stage]
        now = self.clock()
        bucket[0] = min(capacity, bucket[0] + max(0., now - bucket[1]) * rate)
        bucket[1] = now
        if bucket[0] < 1:
            bucket[2] = min(2**31 - 1, bucket[2] + 1)
            return None
        bucket[0] -= 1
        suppressed, bucket[2] = bucket[2], 0
        self.serial += 1
        return suppressed

    def pointer(self, event, status, reason, state_provider, display, *, receipt=None, error=None):
        phase, gesture = event.get("phase"), identity(event.get("gestureId"))
        if phase not in {"down", "move", "up", "cancel"}:
            return
        reason = code(reason)
        if gesture != self.gesture or phase == "down":
            self.gesture, self.moves, self.last_move = gesture, 0, None
        if phase == "move":
            self.moves = min(2**31 - 1, self.moves + 1)
            signature = (gesture, status, reason)
            if signature == self.last_move:
                return
            self.last_move = signature
        suppressed = self._take("pointer-input")
        if suppressed is None:
            return
        state = state_provider()
        context = event.get("context") or {}
        diag = dict(gestureId=gesture, pointerSequence=identity(event.get("sequence")),
            pointerGeneration=identity(event.get("pointerGeneration")), inputScope=scope(context),
            state=self._state(state), display=scope(display), moveEvents=self.moves,
            rateSuppressed=suppressed)
        # Only a down position inside this event's client rectangle. Never log
        # screen origin, outside-client positions, move trajectory or raw input.
        bounds = context.get("clientBounds") if isinstance(context, dict) else None
        geometry_validated = status in {"accepted", "routed"} or reason == "pointer-outside-drag-boundary"
        if phase == "down" and geometry_validated and isinstance(bounds, (list, tuple)) and len(bounds) == 4:
            values = [number(v) for v in (*bounds, event.get("x"), event.get("y"))]
            if all(v is not None for v in values):
                left, top, right, bottom, x, y = values
                if left < right and top < bottom and left <= x < right and top <= y < bottom:
                    diag.update(pressClient=[round(x-left, 2), round(y-top, 2)],
                        clientSize=[right-left, bottom-top])
                    boundary = context.get("dragBoundaryNormalized")
                    if isinstance(boundary, (tuple, list)) and len(boundary) == 4 and all(
                            number(v) is not None and 0 <= v <= 1 for v in boundary):
                        diag["dragBoundaryNormalized"] = list(boundary)
        if receipt is not None:
            diag["receipt"] = receipt_fields(receipt)
        if error is not None:
            diag["exceptionType"] = type(error).__name__ if type(error) in (OSError, ValueError, TypeError, KeyError) else "Exception"
        self.emit(dict(role="input", stage="pointer-input", phase=phase, status=status,
            reason=reason, decision=self.serial, diag=diag))

    @staticmethod
    def _state(state):
        result = {key: state[key] for key in ("roundActive", "mapOpen", "displayAuthorized", "resourceLocked")
            if type(state.get(key)) is bool}
        result["requestGeneration"] = identity(state.get("generation"))
        result["scope"] = scope(state)
        result["mode"] = state.get("mode") if state.get("mode") in {"nightmare", "difficult"} else None
        # This is the cached Session view, explicitly not a fresh authority test.
        result["source"] = "session-snapshot"
        return result

    def action(self, receipt, state, display):
        fields = receipt_fields(receipt)
        action = code(receipt.get("action"), "unknown-action")
        request = scope(receipt.get("requestContext"))
        result = scope(receipt.get("resultContext"))
        attempt = map_attempt(receipt.get("mapAttempt"))
        key = (action, tuple(fields.items()), tuple(request.items()), tuple(result.items()),
            state.get("generation"), tuple(attempt.items()))
        if not receipt or key == self.last_receipt:
            return
        self.last_receipt = key
        suppressed = self._take("action-receipt")
        if suppressed is None:
            return
        self.emit(dict(role="input", stage="action-receipt", action=action,
            status="exception" if fields.get("error") else "rejected" if fields.get("success") is False else
                "accepted" if fields.get("success") is True else "unknown",
            reason=fields["reason"], decision=self.serial,
            **({"mapAttempt": attempt} if attempt else {}),
            diag=dict(receipt=fields, requestScope=request, resultScope=result,
                state=self._state(state), display=scope(display), rateSuppressed=suppressed)))
