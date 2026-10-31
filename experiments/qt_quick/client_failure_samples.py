"""Opt-in failure evidence on the existing Session worker; no capture/thread."""
from __future__ import annotations

import hashlib
import threading
import time

from failure_sampling import FailureSampling


def bounded_evidence(value):
    """Keep measured diagnostics, marking every omitted branch explicitly."""
    import numpy as np
    remaining = [20000]
    truncated = [False]
    def visit(item, depth=0):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 12:
            truncated[0] = True
            return {"omitted": "diagnostic-bound"}
        if isinstance(item, np.ndarray):
            if item.size > 4096:
                truncated[0] = True
                return dict(omitted="array-bound", shape=list(item.shape), dtype=str(item.dtype))
            return visit(item.tolist(), depth + 1)
        if isinstance(item, np.generic):
            return item.item()
        if isinstance(item, dict):
            if len(item) > 512:
                truncated[0] = True
            return {str(k): visit(v, depth+1) for k, v in list(item.items())[:512]}
        if isinstance(item, (tuple, list)):
            if len(item) > 512:
                truncated[0] = True
            return [visit(v, depth+1) for v in item[:512]]
        if isinstance(item, str):
            if len(item) > 8192:
                truncated[0] = True
            return item[:8192]
        return item if isinstance(item, (int, float, bool, type(None))) else str(item)[:512]
    result = visit(value)
    return result, truncated[0]


class FailureSampleRecorder:
    """Reuse store's lossless writer/disk budget and shared cooldown policy.

    A write is part of the existing Session job and therefore its drain. There
    is no daemon to outlive shutdown. At most 12 successful samples per process
    are admitted; enabling diagnostics does not reset cooldown or this limit.
    """
    def __init__(self):
        self.enabled = False
        self.metadata = {}
        self.policy = FailureSampling()
        self.lock = threading.Lock()
        self.saved_count = 0
        self.last_error = ""

    def configure(self, *, event_path, build_fingerprint, source_commit):
        self.metadata = dict(eventPath=str(event_path), buildFingerprint=str(build_fingerprint),
                             sourceCommit=str(source_commit))

    def can_record(self, frame, key):
        """Avoid preparing resources/diagnostics for a rejected admission."""
        commit = self.metadata.get("sourceCommit", "")
        if (not self.enabled or self.saved_count >= 12 or frame.nbytes > 64 * 1024 * 1024
                or not self.metadata.get("eventPath")
                or self.metadata.get("buildFingerprint") in (None, "", "UNKNOWN")
                or len(commit) != 40 or any(c not in "0123456789abcdefABCDEF" for c in commit)
                or not self.lock.acquire(blocking=False)):
            return False
        try:
            return self.policy.eligible(key, time.monotonic())
        finally:
            self.lock.release()

    def record(self, store, *, map_id, floor, reason, fit, frame, client_bounds,
               cycle_id, diagnostic, current):
        metadata = dict(self.metadata)
        commit = metadata.get("sourceCommit", "")
        if (not self.enabled or self.saved_count >= 12 or store is None
                or not metadata.get("eventPath") or metadata.get("buildFingerprint") in (None, "", "UNKNOWN")
                or len(commit) != 40 or any(c not in "0123456789abcdefABCDEF" for c in commit)
                or frame.nbytes > 64 * 1024 * 1024 or not self.lock.acquire(blocking=False)):
            return False
        key = (diagnostic.get("mode"), map_id, diagnostic.get("profileId"), floor, reason)
        success = False
        admitted = False
        try:
            if not self.enabled or not current() or not self.policy.eligible(key, time.monotonic()):
                return False
            compact, truncated = bounded_evidence(dict(diagnostic, **metadata, fit=fit,
                framePixelsSha256=hashlib.sha256(frame.tobytes()).hexdigest()))
            compact["diagnosticsTruncated"] = truncated
            if not self.enabled or not current():
                return False
            admitted = True
            # No additional capture or mutable frame copy is taken here. The
            # synchronous owner retains the exact frame until the writer returns.
            store.record_alignment_failure(map_id, floor, reason, compact["fit"], frame,
                client_bounds, cycle_id=cycle_id, lossless=True, diagnostic=compact)
            success = True
            self.saved_count += 1
            self.last_error = ""
            return True
        except Exception as error:
            self.last_error = type(error).__name__
            return False  # Diagnostics must not alter the pose result.
        finally:
            if admitted:
                self.policy.completed(key, time.monotonic(), success)
            self.lock.release()
