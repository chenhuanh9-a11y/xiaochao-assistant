"""Opt-in local timing metadata. No images, raw input, UI or network transport."""
from collections import deque
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

from client_problems import LOG_MAX_BYTES, PENDING_LIMIT

ENVIRONMENT_FLAG = "XINGTU_LIFECYCLE_TRACE"
# Codes/identities only. In particular no arbitrary error text or nested payload.
TEXT_FIELDS = frozenset(("action", "source", "reason", "mode", "mapId", "floor", "frameId",
    "token", "eventName", "status", "imageStatus", "buildFingerprint", "sourceCommit", "floorSource"))
NUMBER_FIELDS = frozenset(("inputAt", "producedAt", "elapsedMs", "floorMs", "deliveryMs",
    "jobId", "sequence", "epoch", "roundId", "cycleId", "generation", "probeMisses"))
BOOL_FIELDS = frozenset(("success", "accepted", "cancelled", "mapDetected", "openIntent",
    "materialReady", "visible", "combinedSensor"))
CODE = re.compile(r"[A-Za-z0-9_.:-]{0,96}\Z")
STATE_FIELDS = frozenset(("action", "source", "reason", "eventName", "status", "imageStatus"))
STATE_CODE = re.compile(r"[a-z][a-z0-9_-]{0,95}\Z")

# Private experimental telemetry is bounded and code-only at BOTH boundaries.
TEXT_FIELDS |= frozenset(("season", "attemptId", "openingId", "privateStage", "privateStageState",
    "privateFrameDigest", "privateResourceDigest", "privateLockMapId", "privateCoordinateReason",
    "privateCoordinateBasis", "privateGateStatus", "privateGateReason", "exceptionType"))
NUMBER_FIELDS |= frozenset(("inputSequence", "snapshotPoolRevision", "currentPoolRevision",
    "privateObservationCount", "privateCoordinateErrorPx", "privateHypothesisCount", "privateGateMs",
    "privateObservationMs", "privateResourceMs", "privateCompareMs", "privateTotalMs", "privateScanMs", "privateMaterialMs"))
BOOL_FIELDS |= frozenset(("privateExperimental", "privateExperimentalLocked",
    "privateCoordinateVerified", "privatePoseDomainNarrow", "scanSkipped"))
TEXT_FIELDS |= frozenset(("candidateReuse",))
PRIVATE_ID_LISTS = frozenset(("privatePoolBefore", "privatePoolAfter", "privatePoolMapIds",
    "privateEliminatedMapIds", "privateContradictedMapIds", "privateUnknownMapIds"))


def _write_batch(path, rows):
    from diagnostic_storage import append_records
    for start in range(0,len(rows),16):
        append_records(path, rows[start:start+16], file_limit=LOG_MAX_BYTES)


def create_lifecycle_trace(paths, *, candidate_purpose=None):
    # No writer, timer, thread, directory or diagnostic clock when disabled.
    # Production candidate composition does not silently opt into private logs.
    # The existing explicit metadata-tracing opt-in never grants runtime purpose.
    from s1_candidate_purpose import private_native_candidate_purpose_is_valid
    enabled = os.environ.get(ENVIRONMENT_FLAG) == "1" or private_native_candidate_purpose_is_valid(candidate_purpose)
    return LifecycleTrace(paths) if enabled else None


class LifecycleTrace:
    def __init__(self, paths):
        self.log_path = Path(paths.work_root) / "nightmare-live" / "lifecycle-detail.jsonl"
        from diagnostic_storage import slot_path
        self.log_path = slot_path(self.log_path, 'lifecycle')
        self._lock, self._queue = threading.Lock(), deque()
        self._accepting, self._inflight, self._dropped = True, 0, 0
        self._sequence, self._run_id, self._error = 0, uuid4().hex, ""
        self._stop = threading.Event()
        # Optional telemetry must never become another exit-drain dependency.
        # One daemon performs bounded batches with the existing size/queue limits.
        self._thread = threading.Thread(target=self._run, name="local-lifecycle-trace", daemon=True)
        self._thread.start()

    @property
    def pendingWrites(self):
        with self._lock:
            return self._accepting and bool(self._queue or self._inflight)

    def record(self, stage, **fields):
        # Never queue one UI signal or one IO task per event.
        if not isinstance(stage, str) or not CODE.fullmatch(stage):
            return
        with self._lock:
            if not self._accepting:
                return
            if len(self._queue) + self._inflight >= PENDING_LIMIT:
                self._dropped += 1
                return
            self._sequence += 1
            row = dict(event="qt-lifecycle-detail", run=self._run_id, sequence=self._sequence,
                       stage=stage, at=time.monotonic(), dropped=self._dropped)
            for key, value in fields.items():
                if key in TEXT_FIELDS and isinstance(value, str):
                    pattern = STATE_CODE if key in STATE_FIELDS else CODE
                    row[key] = value if pattern.fullmatch(value) else "non-code-value"
                elif key in NUMBER_FIELDS and type(value) in (int, float) and abs(value) <= 1e18 and math.isfinite(value):
                    row["inputSequence" if key == "sequence" else key] = value
                elif key in BOOL_FIELDS and type(value) is bool:
                    row[key] = value
                elif key in PRIVATE_ID_LISTS and isinstance(value, (tuple, list)) and len(value) <= 44:
                    if all(isinstance(mid, str) and CODE.fullmatch(mid) for mid in value):
                        row[key] = list(value)
                elif key == "privateScaleRange" and isinstance(value, (tuple, list)) and len(value) <= 2:
                    if all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1e18 for v in value):
                        row[key] = list(value)
            self._queue.append(row)

    def _run(self):
        while not self._stop.wait(.25):
            with self._lock:
                if not self._accepting:
                    return
                rows = [self._queue.popleft() for _ in range(min(16, len(self._queue)))]
                self._inflight = len(rows)
            try:
                if rows:
                    _write_batch(self.log_path, rows)
            except Exception:
                with self._lock:
                    self._error = "write-failed"
                self.shutdown()
            finally:
                with self._lock:
                    self._inflight = 0

    def shutdown(self):
        with self._lock:
            self._accepting = False
            self._dropped += len(self._queue)
            self._queue.clear()
        self._stop.set()  # No join: an in-progress diagnostic write cannot hold exit.

    cancel = shutdown
