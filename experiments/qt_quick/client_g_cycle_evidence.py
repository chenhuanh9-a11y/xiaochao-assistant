"""Process-only G test evidence adapter; no additional acquisition or fitting.

Default map crops are the exact existing structure extractor's inputs. A separate
opt-in retains the bounded solver client frame, never a new capture. Neither mode
claims full solver reproduction. Later recovery attempts share opening IDs.
"""
import re
import time
import weakref
from uuid import uuid4

from g_cycle_observation import observe_inputs

MAX_CROP_BYTES = 8 * 1024 * 1024


def _code(value, default="unknown"):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", value) else default


def _numeric(value, depth=0):
    """No arbitrary strings, resource paths or embedded arrays in fit details."""
    import math
    if depth > 3:
        return None
    if type(value) is bool or value is None:
        return value
    if type(value) in (int, float) and abs(value) <= 1e12 and math.isfinite(value):
        return value
    if getattr(value, "shape", None) == (3, 3):
        return _numeric(value.tolist(), depth)
    if isinstance(value, (tuple, list)) and len(value) <= 9:
        return [_numeric(item, depth + 1) for item in value]
    return None


class _Trace:
    def __init__(self, runtime, recorder, action, payload):
        self.runtime, self.recorder = runtime, recorder
        self.action, self.payload = action, dict(payload or {})
        self.started = time.monotonic()
        self.capture = self.generation = self.capture_scope = None
        self.roi = None
        self.frames = []
        self.roi_key = None
        self.roi_count = 0
        self.observation_bounds = []
        self.omitted_observation_count = 0
        self.fit_evidence = {}
        self.full_input = getattr(recorder, "full_input", False) is True
        self.metadata = dict(diagnostic_id=uuid4().hex, outcome="failure",
            identity="unknown", session="unknown", round=runtime._round, cycle=runtime._cycle,
            opening="none", attempt="none", frame="none",
            reason="dispatch-exception", sample_role="none", roi_provenance="none",
            missing_reason="no-algorithm-roi", evidence_kind=runtime._evidence_kind,
            input_scope="solver-client-input" if self.full_input else "algorithm-roi")
        self.solver = {}
        self.receipt_scope = None
        self.stale = False
        with runtime._lock:
            self.source_state = self._state()

    def _state(self):
        r = self.runtime
        identity = r._identity_fields()
        # A preparation/recovery capture need not become a committed runtime
        # observation. Never label its new pixels with the last successful ID.
        frame_id = "unknown"
        observation = getattr(r, "_last_observation", None)
        if (self.capture is not None and isinstance(observation, dict)
                and observation.get("captured") is self.capture
                and observation.get("capture_generation") == self.generation
                and self._known_frame(observation.get("frame"))):
            frame_id = _code(observation.get("frame_id"))
        return dict(round=r._round, cycle=r._cycle, frame=frame_id,
            season=r._season, mode=r._mode,
            map=_code(identity.get("durableMapId") or r._map_id, "none"),
            floor=str(r._floor) or "none", locked=identity["identityLocked"])

    def _known_frame(self, frame):
        return frame is not None and any(ref() is frame for ref in self.frames)

    def _remember_frame(self, frame):
        if not self._known_frame(frame):
            self.frames = [ref for ref in self.frames if ref() is not None][-7:]
            self.frames.append(weakref.ref(frame))

    def _select(self, frame, bounds, role):
        import numpy as np
        if (self.capture is None or not isinstance(frame, np.ndarray)
                or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3
                or type(bounds) not in (tuple, list) or len(bounds) != 4):
            return
        width, height = self.capture.context.full_client_size
        if frame.shape != (height, width, 3) or not self._known_frame(frame):
            return
        if any(type(v) is not int for v in bounds):
            return
        x, y, w, h = bounds
        if min(x, y) < 0 or min(w, h) <= 0 or x+w > width or y+h > height:
            return
        if role == "floor-control-input" and self.metadata["sample_role"] in (
                "structure-input", "solver-client-input"):
            return
        key = (id(frame), tuple(bounds), role)
        if key == self.roi_key:
            return
        self.roi_count += 1
        with self.runtime._lock:
            if self.capture_scope == self.runtime._failure_capture_scope():
                self.source_state = self._state()
        if w*h*3 > MAX_CROP_BYTES:
            self.roi = None
            self.roi_key = key
            self.metadata.pop("crop_bounds", None)
            self.metadata.update(missing_reason="roi-byte-limit", sample_role=role,
                full_size=[width, height], roi_provenance="existing-solver-client-frame"
                if role == "solver-client-input" else "existing-structure-extractor")
            return
        # Snapshot while the algorithm owns these pixels. Only one crop is
        # retained, independent of later transport-buffer mutation or G events.
        self.roi = np.array(frame[y:y+h, x:x+w], copy=True, order="C")
        self.roi.setflags(write=False)
        self.roi_key = key
        provenance = {"structure-input": "existing-structure-extractor",
                      "solver-client-input": "existing-solver-client-frame",
                      "floor-control-input": "existing-floor-detector-region"}
        self.metadata.update(crop_bounds=[x, y, w, h], full_size=[width, height],
            sample_role=role, roi_provenance=provenance[role], missing_reason="")

    def __call__(self, kind, *values):
        if kind == "capture-start":
            self.capture = self.roi = self.roi_key = None
            self.generation = self.capture_scope = None
            self.frames = []
            self.observation_bounds = []
            self.omitted_observation_count = 0
            self.fit_evidence = {}
            self.solver = {}
            for key in ("crop_bounds", "full_size"):
                self.metadata.pop(key, None)
            self.metadata.update(sample_role="none", roi_provenance="none",
                frame="unknown", missing_reason="capture-unavailable")
            with self.runtime._lock:
                self.source_state = self._state()
        elif kind == "capture":
            self.capture, self.generation, self.capture_scope, attempt = values
            self.metadata["attempt"] = attempt or "none"
            self._remember_frame(self.capture.frame_bgr)
        elif kind == "full-input":
            captured, frame = values
            if captured is self.capture:
                self._remember_frame(frame)
                if self.metadata.get("missing_reason") == "capture-unavailable":
                    self.metadata["missing_reason"] = "no-algorithm-roi"
        elif kind == "input-alias":
            source, copied = values
            if self._known_frame(source):
                self._remember_frame(copied)
        elif kind == "structure-input":
            frame, evidence = values
            x0, y0, x1, y1 = evidence["observationBounds"]
            if not self._known_frame(frame):
                return
            bounds = [int(x0), int(y0), int(x1), int(y1)]
            if bounds not in self.observation_bounds:
                if len(self.observation_bounds) < 16:
                    self.observation_bounds.append(bounds)
                else:
                    self.omitted_observation_count += 1
            if not self.full_input:
                self._select(frame, [int(x0), int(y0), int(x1-x0), int(y1-y0)], "structure-input")
        elif kind == "solver-input":
            frame = values[0]
            if self.full_input and self._known_frame(frame):
                self._select(frame, [0, 0, frame.shape[1], frame.shape[0]], "solver-client-input")
        elif kind == "fit-evidence":
            frame, evidence = values
            if self._known_frame(frame):
                from s1_fit_diagnostics import safe_evidence
                self.fit_evidence = safe_evidence(evidence)
        elif kind == "control-input":
            frame, mode, observation, floor_read = values
            from recognition_diagnostics import control_band
            bounds = control_band(frame, mode, observation, floor_read)
            if bounds is not None:
                self._select(frame, list(bounds), "floor-control-input")
        elif kind == "diagnostic":
            stage, reason, details = values
            if stage == "pose-fit":
                fit = details.get("fit") or {}
                measured = {key: _numeric(fit[key]) for key in
                    ("success", "matrixSourceToClient", "score", "confidence", "inliers") if key in fit}
                measured["elapsed_ms"] = _numeric(details.get("elapsedMs"))
                self.solver = {key: value for key, value in measured.items() if value is not None}
        elif kind == "receipt":
            action, payload, success, reason, extra = values
            if action != self.action:
                return
            r = self.runtime
            self.receipt_scope = r._failure_capture_scope()
            self.stale = bool(extra.get("stale") or extra.get("cancelled"))
            state = self.source_state if (self.stale or self.capture_scope is not None
                and self.capture_scope != self.receipt_scope) else self._state()
            identities = r._failure_samples.metadata
            attempt = r._map_attempt_metadata(payload)
            self.metadata.update(identity=_code(identities.get("buildFingerprint")),
                build_fingerprint=_code(identities.get("buildFingerprint")),
                source_commit=_code(identities.get("sourceCommit")),
                session=_code(identities.get("sessionId")), **state,
                opening=attempt.get("openingId") or "none", attempt=attempt.get("attemptId") or "none",
                outcome="cancelled" if self.stale else "success" if success else "failure",
                reason=_code(reason, "non-code-reason-omitted"))
            for key, value in (("input_sequence", attempt.get("inputSequence", self.payload.get("inputSequence"))),
                               ("capture_generation", self.generation)):
                if type(value) is int and value >= 0:
                    self.metadata[key] = value

    def finish(self):
        r = self.runtime
        # Checking current capture authority is separate from the disk writer.
        # The accepted historical snapshot is never reattached to a newer G.
        valid = bool(self.capture is not None and not self.stale
            and self.receipt_scope == self.capture_scope == r._failure_capture_scope()
            and r._active and r._capture_is_current(self.capture.context, self.generation))
        if not valid:
            self.roi = None
            self.metadata["missing_reason"] = "cancelled-or-no-current-capture"
        self.metadata.update(roi_count=self.roi_count,
            timings={"dispatch_ms": (time.monotonic()-self.started)*1000}, solver=self.solver,
            observation_bounds=self.observation_bounds,
            omitted_observation_count=self.omitted_observation_count,
            fit_evidence=self.fit_evidence)
        if not self.metadata.get("missing_reason"):
            self.metadata.pop("missing_reason", None)
        return self.recorder.submit(self.roi, self.metadata)


def dispatch_with_evidence(runtime, recorder, action, payload, operation):
    """Observe an already registered arrival; never allocate a second ticket."""
    try:
        trace = _Trace(runtime, recorder, action, payload)
    except Exception:
        # Opt-in telemetry setup cannot prevent the original operation.
        return operation()
    try:
        with observe_inputs(trace):
            return operation()
    finally:
        try:
            result = trace.finish()
            report = getattr(runtime, "g_cycle_reporter", None)
            if report is not None:
                report.publish(dict(result, metadata=trace.metadata), "admission")
        except Exception:
            # Permission, disk or diagnostic errors do not change the verdict.
            report = getattr(runtime, "g_cycle_reporter", None)
            if report is not None:
                try:
                    report.publish(dict(status="observer-error", metadata=trace.metadata), "admission")
                except Exception:
                    pass


# QObject lives on the UI thread; producers only emit bounded immutable metadata.
from collections import Counter
from threading import Lock
from PySide6.QtCore import QObject, Qt, Signal, Slot


class EvidenceReporter(QObject):
    resultReady = Signal(object)
    warning = Signal(str, str, str)
    LIMIT = 64

    def __init__(self, problems, parent=None):
        super().__init__(parent)
        self.problems = problems
        self._lock = Lock()
        self._pending = 0
        self._dropped = 0
        self._warned = False
        self.counters = Counter()
        self.resultReady.connect(self._deliver, Qt.QueuedConnection)

    @property
    def pendingWrites(self):
        with self._lock:
            pending = self._pending
        return pending + bool(self.problems.pendingWrites)

    def publish(self, result, phase="completion"):
        # Fixed identifiers only: never send pixels, paths or exception text to Qt.
        meta = result.get("metadata", {})
        from client_input_diagnostics import map_attempt
        attempt = map_attempt(dict(openingId=meta.get("opening"), attemptId=meta.get("attempt"),
                                   inputSequence=meta.get("input_sequence")))
        status = _code(result.get("status"), "observer-error")
        detail = dict(status=status, evidencePhase=phase)
        for target, source in (("caseId", "case_id"),):
            value = result.get(source)
            if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value):
                detail[target] = value
        value = meta.get("diagnostic_id")
        if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value):
            detail["diagnosticId"] = value
        if meta.get("missing_reason"):
            detail["missingReason"] = _code(meta["missing_reason"])
        detail["outcome"] = _code(meta.get("outcome"))
        event = dict(stage="g-cycle-evidence", reason=status, gCycleEvidence=detail, mapAttempt=attempt)
        for key in ("round", "cycle"):
            if type(meta.get(key)) is int:
                event[key] = meta[key]
        with self._lock:
            self.counters[status] = min(2**63-1, self.counters[status]+1)
            if self._pending >= self.LIMIT:
                self._dropped = min(2**63-1, self._dropped+1)
                return
            self._pending += 1
        self.resultReady.emit(event)

    @Slot(object)
    def _deliver(self, event):
        try:
            with self._lock:
                dropped, self._dropped = self._dropped, 0
            if dropped:
                event["gCycleEvidence"]["dropped"] = dropped
            accepted = self.problems.recordTrace(event)
            status = event["reason"]
            if (not accepted or dropped or status not in {"queued", "saved", "saved-missing-input"}) and not self._warned:
                self._warned = True
                self.warning.emit("部分测试留样未保存", "留样队列或本地存储未能保存全部记录。"
                    "请查看运行诊断中的 g-cycle-evidence 状态；不要把缺失样本当作识别成功。", "warning")
        finally:
            with self._lock:
                self._pending -= 1
