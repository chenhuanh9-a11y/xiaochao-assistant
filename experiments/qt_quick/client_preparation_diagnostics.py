"""Default, bounded metadata for one map-opening preparation; never pixels."""
import traceback
from uuid import uuid4

from client_input_diagnostics import code, number, scope


ERROR_REASONS = {
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
            frames.append(f"{filename}:{item.name[:64]}:{item.lineno}")
    return dict(type=type(error).__name__[:64], errno=number(getattr(error, "errno", None)),
        winerror=number(getattr(error, "winerror", None)), reason=known or "capture-or-observation-exception",
        messageOmitted=known is None, stack=frames[-6:])


def capture_metadata(captured=None, error=None):
    # Provider attaches only metadata already observed by this transaction.
    info = getattr(error, "capture_trace", {}) if error is not None else {}
    row = {key: info[key] for key in ("provider", "backend", "phase", "foreground", "captureIntent") if key in info}
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
        self.opening = uuid4().hex
        self.request = scope(payload)
        self.input_sequence = number(payload.get("inputSequence"))
        self.sequence = self.suppressed = self.probes = self.capture_errors = 0
        self.last_outcome = "not-started"
        self.stop_reason = None

    def emit(self, phase, reason, *, final=False, **details):
        if self.sequence >= self.MAX_ROWS - 1 and not final:
            self.suppressed += 1
            return
        self.sequence += 1
        values = dict(openingId=self.opening, ordinal=self.sequence, phase=phase,
            inputSequence=self.input_sequence, requestScope=self.request, probe=self.probes,
            captureErrors=self.capture_errors, suppressed=self.suppressed, **details)
        self.runtime._notify("map-preparation", reason=reason, preparation=values)

    def finish(self, reason):
        self.emit("finished", reason, final=True, lastProbeOutcome=self.last_outcome,
            loopStopReason=code(self.stop_reason, "not-stopped"))

    @staticmethod
    def observation(observation, floor, floor_read, ready):
        return dict(mapDetected=observation.get("mapOpen", observation.get("detected")) is True,
            mapReady=bool(ready), floor=str(floor) if floor in ("1", "2", "B1") else None,
            mapReason=code(observation.get("reason"), "reason-not-provided"),
            floorReason=code(floor_read.get("reason"), "reason-not-provided"),
            selectedFloor=floor_read.get("selectedFloor") if floor_read.get("selectedFloor") in (-1, 1, 2) else None)
