"""Read-only recorded-frame adapter for the four registered runtime contexts.

The supplied runtime factory must create the existing production LiveRuntime
with the case's resource context and the provided recorded capture source.
This adapter never grants live display authority or changes solver behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
from io import BytesIO
import json
from pathlib import Path
import re
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "experiments/qt_quick")]

CONTEXTS = (("s0", "nightmare"), ("s0", "difficult"),
            ("s1", "nightmare"), ("s1", "difficult"))
MAX_MANIFEST_BYTES = 128 * 1024
MAX_FRAME_BYTES = 64 * 1024 * 1024
MAX_FRAME_PIXELS = 16_000_000


@dataclass(frozen=True)
class ReplayCase:
    season: str
    mode: str
    frame_path: Path
    sha256: str
    profile_id: str = ""
    frame_bgr: np.ndarray | None = None


class RecordedFrameCapture:
    """One immutable frame, exposed through LiveRuntime's normal capture seam."""
    def __init__(self, frame: np.ndarray):
        from client_region_capture import ClientCaptureContext, RegionCapture
        self._frame = np.ascontiguousarray(frame.copy())
        self._context_type, self._capture_type = ClientCaptureContext, RegionCapture
        self.enabled = False
        self.count = 0
        height, width = self._frame.shape[:2]
        self._bounds = (0, 0, width, height)

    @property
    def snapshot(self):
        return dict(enabled=self.enabled, hwnd="recorded-frame", region=list(self._bounds))

    def bind(self, hwnd, region):
        self._bounds = tuple(region)
        self.disable()

    def bindFullClient(self, hwnd):
        self._bounds = (0, 0, self._frame.shape[1], self._frame.shape[0])
        self.disable()

    def enable(self):
        self.enabled = True
        return True

    def disable(self):
        self.enabled = False

    def shutdown(self):
        self.disable()

    def capture(self, cancelled=None):
        cancelled_now = (cancelled.is_set() if hasattr(cancelled, "is_set")
                         else cancelled() if callable(cancelled) else False)
        if not self.enabled or cancelled_now:
            return None
        self.count += 1
        h, w = self._frame.shape[:2]
        rect = (0, 0, w, h)
        context = self._context_type((w, h), rect, 96)
        return self._capture_type(self._frame.copy(), rect, rect, context, "recorded-frame")


def load_manifest(path: Path) -> list[ReplayCase]:
    path = Path(path).resolve()
    raw = path.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest-too-large")
    data = json.loads(raw)
    if type(data) is not dict or data.get("schema") != "xingtu-four-mode-frame-replay-v1":
        raise ValueError("manifest-schema-invalid")
    rows = data.get("cases")
    if type(rows) is not list or len(rows) != len(CONTEXTS):
        raise ValueError("manifest-must-contain-four-contexts")
    cases = []
    for row in rows:
        if type(row) is not dict:
            raise ValueError("case-invalid")
        context = (row.get("season"), row.get("mode"))
        if context not in CONTEXTS or any((case.season, case.mode) == context for case in cases):
            raise ValueError("context-invalid-or-duplicate")
        name = row.get("frame")
        digest = row.get("sha256")
        if (type(name) is not str or not name or Path(name).is_absolute()
                or re.fullmatch(r"[0-9a-f]{64}", str(digest)) is None):
            raise ValueError("frame-record-invalid")
        frame = (path.parent / name).resolve()
        if not frame.is_relative_to(path.parent):
            raise ValueError("frame-path-escapes-manifest-directory")
        frame_bytes = frame.read_bytes()
        if len(frame_bytes) > MAX_FRAME_BYTES:
            raise ValueError("frame-too-large")
        if hashlib.sha256(frame_bytes).hexdigest() != digest:
            raise ValueError("frame-sha256-mismatch")
        from PIL import Image
        with Image.open(BytesIO(frame_bytes)) as header:
            width, height = header.size
            if width <= 0 or height <= 0 or width * height > MAX_FRAME_PIXELS:
                raise ValueError("frame-pixel-limit-exceeded")
        image = cv2.imdecode(np.frombuffer(frame_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("frame-decode-failed")
        if image.shape[:2] != (height, width):
            raise ValueError("frame-dimensions-mismatch")
        profile = row.get("profileId", "")
        if type(profile) is not str:
            raise ValueError("profile-id-invalid")
        cases.append(ReplayCase(context[0], context[1], frame, digest, profile, image))
    if {(case.season, case.mode) for case in cases} != set(CONTEXTS):
        raise ValueError("manifest-contexts-incomplete")
    return sorted(cases, key=lambda case: CONTEXTS.index((case.season, case.mode)))


def _summary(snapshot, events, receipt, case):
    """Keep conclusions useful while excluding pixels, paths and private evidence."""
    keys = ("season", "mode", "profileId", "mapId", "floor", "observedFloor",
            "identityLocked", "displayAuthorized", "poseStatus", "poseReason",
            "automaticIdentityAvailable", "liveEngineAvailable", "evidenceKind")
    state = {key: snapshot[key] for key in keys if key in snapshot}
    candidate_rows = snapshot.get("automaticCandidateRows", snapshot.get("candidateRows", []))
    candidates = []
    for row in candidate_rows if isinstance(candidate_rows, list) else []:
        candidates.append({key: row[key] for key in ("mapId", "floor", "score", "identityHardGatePassed", "reason")
                          if key in row})
    event_names = [row.get("event") for row in events if isinstance(row, dict) and row.get("event")]
    return dict(context={"season": case.season, "mode": case.mode}, frameSha256=case.sha256,
                receipt={key: receipt[key] for key in ("success", "reason", "unavailable", "error") if key in receipt},
                state=state, candidates=candidates, observedEvents=event_names,
                liveAuthorization=False,
                scope="recorded-frame diagnostic; no recognition, floor, fit, or display truth claim")


def replay_manifest(manifest: Path, runtime_factory) -> dict:
    """Run each frame once through the production runtime's recorded capture seam.

    ``runtime_factory(case, capture)`` must return a configured LiveRuntime backed
    by resources for exactly that season/mode. It must use evidence_kind="recorded".
    """
    reports = []
    for case in load_manifest(manifest):
        capture = RecordedFrameCapture(case.frame_bgr)
        runtime = runtime_factory(case, capture)
        events = []
        runtime.set_event_callback(events.append)
        try:
            if getattr(runtime, "_evidence_kind", None) != "recorded":
                raise ValueError("runtime-factory-must-use-recorded-evidence")
            context = {"season": case.season, "mode": case.mode}
            if case.profile_id:
                context["profileId"] = case.profile_id
            switched = runtime.dispatch("set_context", context)
            started = runtime.dispatch("start_round") if switched.get("success") else switched
            receipt = runtime.dispatch("capture_now") if started.get("success") else started
            reports.append(_summary(runtime.snapshot(), events, receipt, case))
        finally:
            runtime.shutdown()
    return {"schema": "xingtu-four-mode-frame-replay-report-v1", "reports": reports,
            "allLiveAuthorization": False}


def load_factory(spec: str):
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("runtime-factory-must-be-module:callable")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise ValueError("runtime-factory-not-callable")
    return factory


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--runtime-factory", required=True, help="module:callable returning a recorded LiveRuntime")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = replay_manifest(args.manifest, load_factory(args.runtime_factory))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
