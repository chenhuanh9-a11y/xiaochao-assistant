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
    "token", "eventName", "status", "imageStatus", "buildFingerprint", "sourceCommit"))
NUMBER_FIELDS = frozenset(("inputAt", "producedAt", "elapsedMs", "floorMs", "deliveryMs",
    "jobId", "sequence", "epoch", "roundId", "cycleId", "generation", "probeMisses"))
BOOL_FIELDS = frozenset(("success", "accepted", "cancelled", "mapDetected", "openIntent",
    "materialReady", "visible", "combinedSensor"))
CODE = re.compile(r"[A-Za-z0-9_.:-]{0,96}\Z")
STATE_FIELDS = frozenset(("action", "source", "reason", "eventName", "status", "imageStatus"))
STATE_CODE = re.compile(r"[a-z][a-z0-9_-]{0,95}\Z")


def _write_batch(path, rows):
    # A logging.Handler would register for atexit flush/close and could make
    # optional daemon IO block interpreter shutdown. Keep this sink independent.
    from client_paths import _no_links
    previous = path.with_name(path.stem + "-previous.jsonl")
    _no_links(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _no_links(path); _no_links(previous)
    stream = path.open("ab")
    try:
        for row in rows:
            data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
            if len(data) > LOG_MAX_BYTES:
                continue
            if stream.tell() + len(data) > LOG_MAX_BYTES:
                stream.close()
                path.replace(previous)
                stream = path.open("ab")
            stream.write(data)
    finally:
        stream.close()


def create_lifecycle_trace(paths):
    # No writer, timer, thread, directory or diagnostic clock when disabled.
    return LifecycleTrace(paths) if os.environ.get(ENVIRONMENT_FLAG) == "1" else None


class LifecycleTrace:
    def __init__(self, paths):
        self.log_path = Path(paths.work_root) / "nightmare-live" / "lifecycle-detail.jsonl"
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
