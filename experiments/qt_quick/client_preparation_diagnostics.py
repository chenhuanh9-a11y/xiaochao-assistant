"""Default, bounded metadata for one map-opening preparation; never pixels."""
from contextlib import nullcontext
import re
import traceback
from uuid import uuid4

from client_input_diagnostics import code, number, scope


def capture_capability(value):
    """Only the provider's finite capability schema can leave this boundary."""
    if not isinstance(value, dict):
        return {}
    result = {}
    choices = dict(clientDc={"not-probed", "failed", "captured"},
        safeFallback={"not-probed", "not-configured", "supported", "unsupported", "verified-for-capture"},
        exclusionMode={"WDA_EXCLUDEFROMCAPTURE"},
        reason={"platform-not-supported", "windows-build-too-old"})
    for key, allowed in choices.items():
        if isinstance(value.get(key), str) and value[key] in allowed:
            result[key] = value[key]
    for key in ("windowsBuild", "minimumWindowsBuild"):
        if type(value.get(key)) is int and 0 <= value[key] <= 1_000_000:
            result[key] = value[key]
        elif key == "windowsBuild" and key in value and value[key] is None:
            result[key] = None
    if type(value.get("fallbackRequired")) is bool:
        result["fallbackRequired"] = value["fallbackRequired"]
    return result


def fallback_trigger(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    if isinstance(value.get("type"), str) and value["type"] in {"OSError", "PermissionError", "RuntimeError", "ValueError", "ClientRegionCaptureError"}:
        result["type"] = value["type"]
    if isinstance(value.get("reason"), str) and value["reason"] in {"game-client-dc-returned-blank-frame", "client-dc-native-error"}:
        result["reason"] = value["reason"]
    for key in ("errno", "winerror"):
        if key in value and (value[key] is None or type(value[key]) is int and abs(value[key]) <= 2**31):
            result[key] = value[key]
    return result


ERROR_REASONS = {
    "capture-safe-fallback-unsupported": "capture-safe-fallback-unsupported",
    "game-client-dc-returned-blank-frame": "game-client-dc-returned-blank-frame",
    "game window is minimized; not restored by capture": "game-window-minimized",
    "client size or DPI unavailable": "client-geometry-unavailable",
    "GdiFlush failed before reading ROI pixels": "gdi-flush-failed",
    "Bound game must own the foreground before capture": "game-not-foreground",
    "Foreground changed during capture; frame discarded": "foreground-changed-during-capture",
    "Captured frame was superseded or cancelled": "capture-superseded-or-cancelled",
    "Captured frame was superseded": "capture-superseded",
    "Capture request cancelled": "capture-request-cancelled",
    "Client capture requires explicit enable": "capture-not-enabled",
    "Bound game window is unavailable; rebind explicitly": "game-window-unavailable",
    "client changed before ROI capture": "capture-context-changed",
    "client changed before fallback": "capture-context-changed",
    "client changed during fallback": "capture-context-changed",
}


def exception_metadata(error):
    """Preserve type, OS code and product stack, without arbitrary message data."""
    known = ERROR_REASONS.get(str(error))
    frames = []
    for item in traceback.extract_tb(error.__traceback__, limit=12):
        filename = item.filename.replace("\\", "/").rsplit("/", 1)[-1]
        if filename in {"client_live_runtime.py", "client_capture.py", "client_region_capture.py"}:
            frames.append(f'File "{filename}", line {item.lineno}, in {item.name[:64]}')
    return dict(type=type(error).__name__[:64], errno=number(getattr(error, "errno", None)),
        winerror=number(getattr(error, "winerror", None)), reason=known or "capture-or-observation-exception",
        messageOmitted=known is None, stack=frames[-6:])


def capture_metadata(captured=None, error=None):
    # Provider attaches only metadata already observed by this transaction.
    info = getattr(error, "capture_trace", {}) if error is not None else {}
    info = info if isinstance(info, dict) else {}
    row = {key: info[key] for key in ("provider", "backend", "phase", "foreground", "captureIntent",
                                   "captureCapability", "fallbackTrigger") if key in info}
    for key, cleaner in (("captureCapability", capture_capability), ("fallbackTrigger", fallback_trigger)):
        if key in row:
            row[key] = cleaner(row[key])
    if captured is not None:
        context = captured.context
        row.update(provider="client-capture", backend=code(getattr(captured, "acquisition", None), "unknown-backend"),
            source="full-client-frame", foreground="verified-by-capture",
            bounds=list(context.client_screen_bounds), clientSize=list(context.full_client_size), dpi=context.dpi)
    elif "bounds" in info:
        row.update({key: info[key] for key in ("bounds", "clientSize", "dpi")})
    return row


class PreparationDiagnostics:
    MAX_ROWS = 72  # 21 production probes * 3 edges + begin/stop; spare room is bounded.

    def __init__(self, runtime, payload):
        self.runtime = runtime
        # One opening can own several bounded preparation attempts. These
        # random diagnostic identities never participate in capture authority.
        payload = payload if isinstance(payload, dict) else {}
        def diagnostic_id(value):
            return value.lower() if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{32}", value) else None
        self.opening = diagnostic_id(payload.get("_diagnosticOpeningId")) or uuid4().hex
        self.attempt = diagnostic_id(payload.get("_diagnosticAttemptId")) or uuid4().hex
        payload["_diagnosticOpeningId"], payload["_diagnosticAttemptId"] = self.opening, self.attempt
        self.recovery = diagnostic_id(payload.get("recoveryId"))
        self.request = {key: value for key, value in scope(payload).items() if value is not None}
        self.input_sequence = number(payload.get("inputSequence"))
        self.sequence = self.suppressed = self.probes = self.capture_errors = 0
        self.last_outcome = "not-started"
        self.stop_reason = None

    def emit(self, phase, reason, *, final=False, **details):
        if self.sequence >= self.MAX_ROWS - 1 and not final:
            self.suppressed += 1
            return
        self.sequence += 1
        values = dict(openingId=self.opening, attemptId=self.attempt, ordinal=self.sequence, phase=phase,
            inputSequence=self.input_sequence, requestScope=self.request, probe=self.probes,
            captureErrors=self.capture_errors, suppressed=self.suppressed, **details)
        if self.recovery is not None:
            values["recoveryId"] = self.recovery
        if phase == "begin" or final:
            state = {"source": "runtime-memory"}
            try:
                # Reuse the lifetime lock and booleans only. No provider query,
                # full snapshot, capture, or remembered observation is consulted.
                with getattr(self.runtime, "_lock", None) or nullcontext():
                    for key, attribute in (("roundActive", "_active"), ("mapOpen", "_map_open"),
                                           ("captureInProgress", "_capture_pending")):
                        value = getattr(self.runtime, attribute, None)
                        if type(value) is bool:
                            state[key] = value
            except Exception:
                state = {"source": "runtime-memory"}
            values["state"] = state
        values = {key: value for key, value in values.items() if value is not None}
        self.runtime._notify("map-preparation", reason=reason, preparation=values)

    def finish(self, reason):
        self.emit("finished", reason, final=True, lastProbeOutcome=self.last_outcome,
            loopStopReason=code(self.stop_reason, "not-stopped"))

    @staticmethod
    def observation(observation, floor, floor_read, ready):
        return dict(mapDetected=observation.get("mapOpen", observation.get("detected")) is True,
            mapReady=bool(ready), floor=str(floor) if floor in ("1", "2", "B1", "B2", "B3", "B4") else None,
            mapReason=code(observation.get("reason"), "reason-not-provided"),
            floorReason=code(floor_read.get("reason"), "reason-not-provided"),
            selectedFloor=floor_read.get("selectedFloor") if (type(floor_read.get("selectedFloor")) is int
                and floor_read.get("selectedFloor") in (-4, -3, -2, -1, 1, 2)) else None)
