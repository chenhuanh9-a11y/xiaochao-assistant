"""Qt-free saved-PNG adapter over the production LiveRuntime transaction.

The capture source is deliberately recorded evidence. It only decodes files
under the explicitly supplied frame root and never talks to desktop capture,
input, or listener APIs.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import threading

import numpy as np


_REPO_ROOT = Path(__file__).resolve().parents[2]
for _directory in (_REPO_ROOT / "app", _REPO_ROOT / "experiments" / "qt_quick"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))


from client_region_capture import ClientCaptureContext, RegionCapture
from client_providers import EngineFactories
from client_live_runtime import LiveRuntime
from season_context import context_key, known_modes_for_season, normalize_season


MAX_FRAME_PIXELS = 32_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _cancelled(cancelled):
    if cancelled is None:
        return False
    return bool(cancelled.is_set() if hasattr(cancelled, "is_set") else cancelled())


def _json_value(value):
    """Copy simple recorded metadata into a protocol-safe value tree."""
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()
                if isinstance(key, (str, int, float, bool))}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _capture_context(value, width, height):
    """Build the RegionCapture contract and keep coordinate provenance explicit."""
    if isinstance(value, ClientCaptureContext):
        context = value
        physical = None
        origin_known = True
        dpi_known = True
    else:
        if value is None:
            source = {}
            physical = None
        elif isinstance(value, dict):
            physical_value = value.get("physicalClient")
            physical = _json_value(physical_value) if physical_value is not None else _json_value(value)
            source = physical_value if isinstance(physical_value, dict) else value
            nested = value.get("context")
            if isinstance(nested, dict):
                source = nested
        else:
            raise ValueError("capture-context-must-be-object")

        full_size = source.get("fullClientSize") if isinstance(source, dict) else None
        source_width = source.get("Width", source.get("width")) if isinstance(source, dict) else None
        source_height = source.get("Height", source.get("height")) if isinstance(source, dict) else None
        if full_size is not None:
            try:
                source_width, source_height = full_size
            except (TypeError, ValueError):
                raise ValueError("capture-context-size-invalid") from None
        if source_width is not None and int(source_width) != width:
            raise ValueError("capture-context-width-mismatch")
        if source_height is not None and int(source_height) != height:
            raise ValueError("capture-context-height-mismatch")

        bounds = source.get("clientScreenBounds") if isinstance(source, dict) else None
        if bounds is not None:
            try:
                left, top, right, bottom = (int(item) for item in bounds)
            except (TypeError, ValueError):
                raise ValueError("capture-context-bounds-invalid") from None
            origin_known = True
        else:
            left_value = source.get("Left", source.get("left")) if isinstance(source, dict) else None
            top_value = source.get("Top", source.get("top")) if isinstance(source, dict) else None
            origin_known = left_value is not None and top_value is not None
            left, top = (int(left_value), int(top_value)) if origin_known else (0, 0)
            right, bottom = left + width, top + height
        dpi_value = source.get("Dpi", source.get("dpi")) if isinstance(source, dict) else None
        dpi_known = dpi_value is not None and int(dpi_value) > 0
        dpi = int(dpi_value) if dpi_known else 96
        context = ClientCaptureContext((width, height), (left, top, right, bottom), dpi)

    if tuple(context.full_client_size) != (width, height):
        raise ValueError("capture-context-size-mismatch")
    bounds = tuple(int(item) for item in context.client_screen_bounds)
    if len(bounds) != 4 or bounds[2] - bounds[0] != width or bounds[3] - bounds[1] != height:
        raise ValueError("capture-context-bounds-size-mismatch")
    if type(context.dpi) is not int or context.dpi <= 0:
        raise ValueError("capture-context-dpi-invalid")

    metadata = dict(fullClientSize=[width, height], clientScreenBounds=list(bounds), dpi=context.dpi,
        coordinateOrigin=("recorded-client-metadata" if origin_known else "unknown"),
        coordinateOriginKnown=bool(origin_known), dpiKnown=bool(dpi_known),
        globalPlacementAvailable=False, physicalClient=physical)
    return context, metadata


class _RecordedPngCapture:
    """Full-client capture contract backed only by one immutable decoded PNG."""

    evidence_kind = "recorded"

    def __init__(self):
        self._lock = threading.RLock()
        self._frame = None
        self._context = None
        self._context_metadata = None
        self._frame_sha256 = ""
        self._generation = 0
        self._enabled = False
        self._closed = False

    @property
    def generation(self):
        with self._lock:
            return self._generation

    @property
    def snapshot(self):
        with self._lock:
            return dict(enabled=self._enabled, generation=self._generation,
                evidenceKind=self.evidence_kind, frameAvailable=self._frame is not None,
                frameSha256=self._frame_sha256,
                captureContext=copy.deepcopy(self._context_metadata))

    def set_frame(self, frame, context, context_metadata, frame_sha256):
        with self._lock:
            if self._closed:
                raise RuntimeError("session-closed")
            self._frame = frame
            self._context = context
            self._context_metadata = copy.deepcopy(context_metadata)
            self._frame_sha256 = str(frame_sha256)
            self._generation += 1
            return self._generation

    def clear_frame(self):
        with self._lock:
            self._frame = None
            self._context = None
            self._context_metadata = None
            self._frame_sha256 = ""
            self._generation += 1
            return self._generation

    def enable(self):
        with self._lock:
            if self._closed:
                return False
            self._enabled = True
            return True

    def disable(self):
        with self._lock:
            self._enabled = False

    def shutdown(self):
        with self._lock:
            self._enabled = False
            self._frame = None
            self._context = None
            self._closed = True
            self._generation += 1

    def is_current(self, context, *, generation=None):
        with self._lock:
            return bool(not self._closed and self._context == context
                and (generation is None or generation == self._generation))

    def capture(self, cancelled=None):
        with self._lock:
            if _cancelled(cancelled):
                raise RuntimeError("recorded-capture-cancelled")
            if self._closed:
                raise RuntimeError("recorded-capture-closed")
            if not self._enabled:
                raise RuntimeError("recorded-capture-not-enabled")
            if self._frame is None or self._context is None:
                raise RuntimeError("recorded-frame-unavailable")
            frame, context = self._frame, self._context
            height, width = frame.shape[:2]
            bounds = (0, 0, width, height)
            return RegionCapture(frame, bounds, bounds, context,
                "recorded-png" if self._context_metadata["coordinateOriginKnown"]
                else "recorded-png-origin-unknown")


class RecordedRuntimeSession:
    """Qt-free session over production EngineFactories and LiveRuntime.

    Construction is lazy with respect to resource IO and engine preparation.
    Resource locking begins on the first ``prepare`` and lasts until ``close``.
    """

    def __init__(self, resource_root, data_root, frame_root):
        self.resource_root = Path(resource_root).expanduser().resolve()
        self.installation_root = (self.resource_root.parent if self.resource_root.name.casefold() == "_internal"
                                  else self.resource_root)
        self.data_root = Path(data_root).expanduser().resolve()
        self.frame_root = Path(frame_root).expanduser().resolve()
        if self.data_root.is_relative_to(self.resource_root) or self.data_root.is_relative_to(self.frame_root):
            raise ValueError("recorded data root must be outside resource and frame roots")
        self._lease = None
        self._closed = False
        self._capture = _RecordedPngCapture()
        self._factories = EngineFactories(self.resource_root, memory_root=self.data_root / "memory",
                                         resource_guard=self._ensure_resources_ready)
        self._runtime = LiveRuntime(engine_factories=self._factories,
            capture_provider=self._capture, allowed_read_root=self.data_root, evidence_kind="recorded")

    def _ensure_resources_ready(self):
        if self._closed:
            raise RuntimeError("session-closed")
        if self._lease is None:
            from resource_transaction import resource_runtime_lease
            self._lease = resource_runtime_lease(self.installation_root)
        from resource_transaction import assert_resources_ready
        assert_resources_ready(self.installation_root)

    def prepare(self, season, mode):
        if self._closed:
            return dict(available=False, prepared=True, season=str(season), mode=str(mode),
                reason="session-closed", liveAuthorization=False)
        try:
            season = normalize_season(season)
        except ValueError as error:
            return dict(available=False, prepared=True, season=str(season), mode=str(mode),
                reason=str(error), liveAuthorization=False)
        if mode not in known_modes_for_season(season):
            return dict(available=False, prepared=True, season=season, mode=str(mode),
                reason="unknown resource preparation context", liveAuthorization=False)
        try:
            self._ensure_resources_ready()
        except Exception as error:
            return dict(available=False, prepared=True, season=season, mode=mode,
                contextKey=context_key(mode, season), reason=getattr(error, "code", str(error)),
                liveAuthorization=False)

        capabilities = self._factories.prepare_context(mode, season=season)
        report = dict(capabilities[mode])
        previous = self._runtime.snapshot()
        profile = previous.get("profileId") or "shared-structure"
        receipt = self._runtime.dispatch("set_context", dict(season=season, mode=mode, profileId=profile))
        report["contextReceipt"] = receipt
        if not receipt.get("success"):
            report["available"] = False
            report["reason"] = str(receipt.get("reason", "context-update-failed"))
        return report

    def set_frame(self, path, *, capture_context=None):
        if self._closed:
            return dict(success=False, reason="session-closed", evidenceKind="recorded")
        try:
            given = Path(path).expanduser()
            candidate = given if given.is_absolute() else self.frame_root / given
            resolved = candidate.resolve(strict=True)
            root = self.frame_root.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise ValueError("frame-outside-frame-root")
            if not resolved.is_file() or resolved.suffix.casefold() != ".png":
                raise ValueError("recorded-frame-must-be-png")
            raw = resolved.read_bytes()
            if len(raw) < 24 or not raw.startswith(PNG_SIGNATURE) or raw[12:16] != b"IHDR":
                raise ValueError("recorded-frame-invalid-png")
            width_header = int.from_bytes(raw[16:20], "big")
            height_header = int.from_bytes(raw[20:24], "big")
            if (width_header <= 0 or height_header <= 0
                    or width_header * height_header > MAX_FRAME_PIXELS):
                raise ValueError("recorded-frame-size-invalid")
            import cv2
            encoded = np.frombuffer(raw, dtype=np.uint8)
            frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            if frame is None or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
                raise ValueError("recorded-frame-decode-failed")
            height, width = frame.shape[:2]
            if (width != width_header or height != height_header or width <= 0 or height <= 0
                    or width * height > MAX_FRAME_PIXELS):
                raise ValueError("recorded-frame-size-invalid")
            frame = np.ascontiguousarray(frame)
            frame.setflags(write=False)
            context, metadata = _capture_context(capture_context, width, height)
            digest = hashlib.sha256(raw).hexdigest()
            generation = self._capture.set_frame(frame, context, metadata, digest)
            # Fence in-flight work and revoke an old render immediately. This
            # does not start/end a round or grant recorded pixels live status.
            self._runtime.invalidate("recorded-frame-changed")
            return dict(success=True, frameId=digest, frameSha256=digest,
                frameShape=[height, width, 3], generation=generation,
                captureContext=copy.deepcopy(metadata), evidenceKind="recorded",
                liveAuthorization=False)
        except Exception as error:
            # A rejected replacement cannot leave an older frame eligible for
            # a later request. Preserve only the caller-facing error detail.
            self._capture.clear_frame()
            self._runtime.invalidate("recorded-frame-rejected")
            return dict(success=False, reason=f"{type(error).__name__}:{error}",
                evidenceKind="recorded", liveAuthorization=False)

    def dispatch(self, action, payload=None, cancelled=None):
        if self._closed:
            receipt = dict(action=str(action), success=False, reason="session-closed",
                liveAuthorization=False)
        else:
            receipt = self._runtime.dispatch(str(action), payload or {}, cancelled)
        return dict(receipt=receipt, snapshot=self.snapshot())

    def snapshot(self):
        return self._runtime.snapshot()

    def invalidate(self, reason="cancelled"):
        """Immediately fence in-flight production work without queueing a command."""
        self._runtime.invalidate(str(reason))
        return self.snapshot()

    def render(self):
        state = self.snapshot()
        metadata = state.get("render") or {}
        token = metadata.get("token")
        if not token:
            return None
        return self._runtime.render_payload(token)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._runtime.shutdown()
        finally:
            try:
                self._factories.invalidate()
            finally:
                if self._lease is not None:
                    self._lease.close()
                    self._lease = None
