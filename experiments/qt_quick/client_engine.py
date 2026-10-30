"""Headless production boundary for Qt workers, with no implicit live resources.

``dispatch``/``execute`` are synchronous: SessionService runs them on its worker.
Constructing this object never loads a pack, imports Tk, captures, or polls keys.
Saved inputs are diagnostics/replays and can never authorize a live overlay.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import threading
from typing import Callable, Mapping
from client_paths import APP_ROOT, RESOURCE_ROOT, WORK_ROOT

ROOT = RESOURCE_ROOT
APP = str(APP_ROOT)
if APP not in sys.path:
    sys.path.insert(0, APP)

from alignment_core_v2 import AlignmentCoreV2


class EngineAdapter:
    """Inject ``{mode: factory}`` of configured MapIdentityEngine instances.

    A factory is called on the requesting worker, once per mode. It must bind
    the real pack and shared resource providers; no fallback mode is inferred.
    The factory/engine must not own UI, capture, listeners, or installed config.
    """

    MODES = ("nightmare", "difficult")
    OFFLINE = ("load_saved_result", "saved_result", "load_saved_pose",
               "set_game_mode", "set_route_profile", "set_context", "cancel",
               "stop_round", "reset_round")
    ALIASES = {"saved_frame": "rank_saved_frame", "recognize": "rank_saved_frame",
               "saved_result": "load_saved_result", "replay": "load_saved_result"}

    def __init__(self, *, engine_factories: Mapping[str, Callable] | None = None,
                 allowed_read_root: Path | None = None, clock=None):
        self._provider_registry = (engine_factories if callable(getattr(engine_factories, "capabilities", None))
                                   and callable(getattr(engine_factories, "prepare", None)) else None)
        self._factories = dict(engine_factories or {})
        if set(self._factories) - set(self.MODES):
            raise ValueError("engine factories must name nightmare or difficult")
        self._read_root = Path(allowed_read_root or WORK_ROOT).resolve()
        self._engines = {}
        self._engine_locks = {mode: threading.Lock() for mode in self.MODES}
        self._lock = threading.RLock()
        self._core = AlignmentCoreV2(clock=clock)
        self._revision = 0
        self._context = dict(mode="nightmare", profileId="", mapId="", floor="1",
                             roundId=0, cycleId=0, frameId="", generation=0)

    def snapshot(self):
        with self._lock:
            commands = list(self.OFFLINE)
            capabilities = (self._provider_registry.capabilities() if self._provider_registry is not None
                            else {mode: dict(available=mode in self._factories,
                                             reason="configured-factory" if mode in self._factories else "factory-unavailable")
                                  for mode in self.MODES})
            if self._provider_registry is not None:
                commands.append("inspect_engine_resources")
            if capabilities[self._context["mode"]]["available"]:
                commands += ["rank_saved_frame", "saved_frame", "recognize"]
            return dict(**copy.deepcopy(self._context), engineConnected=True,
                        adapterConnected=True, liveEngineAvailable=False,
                        savedFrameEngineAvailable=capabilities[self._context["mode"]]["available"],
                        savedFrameCapabilities=capabilities,
                        roundActive=False, roundPreparing=False,
                        availableCommands=commands, liveAuthorization=False,
                        core=self._core.snapshot(),
                        message="Saved replay is available. Live recognition is not attached.",
                        unavailableReason="live-recognition-orchestration-not-extracted")

    @staticmethod
    def _cancelled(cancelled):
        if cancelled is None:
            return False
        return bool(cancelled.is_set() if hasattr(cancelled, "is_set") else cancelled())

    def _request_context(self, payload):
        context = copy.deepcopy(self._context)
        supplied = dict(payload.get("context") or {})
        supplied.update({key: payload[key] for key in context if key in payload})
        context.update({key: supplied[key] for key in context if key in supplied})
        if "roundId" in supplied and "cycleId" not in supplied:
            context["cycleId"] = supplied["roundId"]
        if "cycleId" in supplied and "roundId" not in supplied:
            context["roundId"] = supplied["cycleId"]
        if context["mode"] not in self.MODES:
            raise ValueError("unsupported mode")
        context["floor"] = str(context["floor"])
        if context["floor"] not in ("B1", "1", "2"):
            raise ValueError("unsupported floor")
        return context

    @staticmethod
    def _receipt(action, context, *, success=False, reason="", scope="none", **extra):
        return dict(action=action, **copy.deepcopy(context), success=success,
                    reason=reason, message=reason, liveAuthorization=False,
                    authorization=dict(live=False, scope=scope), **extra)

    def _path(self, value, *, maximum=512 * 1024):
        path = Path(value).resolve()
        if not path.is_relative_to(self._read_root):
            raise ValueError("saved input must be beneath allowed_read_root")
        if not path.is_file() or path.stat().st_size > maximum:
            raise ValueError("saved input missing or exceeds size bound")
        return path

    def execute(self, action, context=None, cancel_event=None):
        """SessionService compatibility facade; ``context`` is the full payload."""
        return self.dispatch(action, context or {}, cancel_event)

    def invalidate_resources(self):
        with self._lock:
            self._revision += 1
            self._engines.clear()
            self._core.reset_round(reason="resources-changed")
            if self._provider_registry is not None:
                self._provider_registry.invalidate()

    def dispatch(self, action, payload=None, cancelled=None):
        payload = dict(payload or {})
        action = self.ALIASES.get(str(action), str(action))
        with self._lock:
            try:
                context = self._request_context(payload)
            except (TypeError, ValueError) as error:
                return self._receipt(action, self._context, reason=str(error), error=True)
            revision = self._revision
        result = lambda **values: self._receipt(action, context, **values)
        if self._cancelled(cancelled):
            return result(reason="cancelled", cancelled=True)
        if action in ("set_game_mode", "set_route_profile", "set_context"):
            with self._lock:
                self._context = context
                self._revision += 1
                self._core.reset_round(reason=action)
                return result(success=True, reason="context-updated", snapshot=self.snapshot())
        if action in ("cancel", "stop_round", "reset_round"):
            with self._lock:
                self._revision += 1
                if action == "reset_round":
                    self._core.reset_round(reason=action)
                else:
                    self._core.close(reason=action)
                return result(success=True, reason=action, snapshot=self.snapshot())
        try:
            if action == "inspect_engine_resources" and self._provider_registry is not None:
                data = dict(capabilities=self._provider_registry.prepare(cancelled), diagnosticOnly=True)
                scope = "resource-inventory"
            elif action == "rank_saved_frame":
                data = self._rank_saved_frame(payload, context, cancelled)
                scope = "saved-frame-diagnostic"
            elif action == "load_saved_result":
                from replay_adapter import load_render_replay
                data = dict(replay=load_render_replay(self._path(payload["path"]), self._read_root),
                            diagnosticOnly=True, recordedMode=None)
                scope = "historical-replay"
            elif action == "load_saved_pose":
                from replay_adapter import load_pose
                data = dict(pose=load_pose(self._path(payload["path"]), context["floor"]),
                            diagnosticOnly=True, evidenceKind="pose-snapshot-input-unverified")
                scope = "pose-replay"
            else:
                return result(reason="live-recognition-orchestration-not-extracted"
                              if action in ("start_round", "prepare_round", "recognize_live", "start")
                              else "unsupported-action", unavailable=True)
            with self._lock:
                if self._cancelled(cancelled):
                    return result(reason="cancelled", cancelled=True)
                if self._revision != revision:
                    return result(reason="stale-context", stale=True)
            # SessionService compares the echoed generation/frame/mode context
            # before publishing. Historical IDs live inside replay/pose only.
            return result(success=True, reason="completed", scope=scope, **data)
        except EngineUnavailable as error:
            return result(reason=str(error), unavailable=True)
        except Exception as error:
            return result(reason=f"{type(error).__name__}: {error}", error=True)

    def _rank_saved_frame(self, payload, context, cancelled):
        mode = context["mode"]
        if mode not in self._factories:
            raise EngineUnavailable(f"saved-frame-engine-unavailable:{mode}")
        from PIL import Image
        import cv2
        import numpy as np

        path = self._path(payload["path"], maximum=64 * 1024 * 1024)
        # PIL checks dimensions before OpenCV allocates the decoded BGR frame.
        with Image.open(path) as opened:
            if opened.width * opened.height > 16_000_000:
                raise ValueError("saved frame exceeds pixel bound")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        expected = payload.get("sha256")
        if expected is not None and str(expected).lower() != digest:
            raise ValueError("saved frame checksum mismatch")
        frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("unable to decode saved frame")
        budget = float(payload.get("budgetSeconds", 1.0))
        if not np.isfinite(budget) or not 0 < budget <= 10:
            raise ValueError("budgetSeconds must be finite and in (0, 10]")
        with self._engine_locks[mode]:
            if self._cancelled(cancelled):
                return {}
            engine = self._engines.get(mode)
            if engine is None:
                try:
                    engine = self._factories[mode]()
                except Exception as error:
                    if getattr(error, "unavailable", False):
                        raise EngineUnavailable(str(error)) from error
                    raise
                if getattr(getattr(engine, "pack", None), "mode", None) != mode:
                    raise ValueError("engine pack mode does not match requested mode")
                self._engines[mode] = engine
            if context["floor"] not in tuple(map(str, engine.identity_floors)):
                raise EngineUnavailable(f"saved-frame-floor-unavailable:{mode}/{context['floor']}")
            if self._cancelled(cancelled):
                return {}
            # This is the existing shared production entry, not an adapter solve.
            # Its budget is cooperative; it has no cancellation callback. Cancel
            # is checked before and after rank, and obsolete output is discarded.
            rows = engine.rank(frame, floor=context["floor"], limit=len(engine.pack.maps),
                               budget_seconds=budget)
            diagnostics = copy.deepcopy(getattr(engine, "last_difficult_evidence", None))
            candidates = copy.deepcopy(rows)
        # Shared rank output may contain NumPy matrices/scalars. Keep the
        # boundary JSON/QVariant compatible without copying solver logic.
        def native(value):
            if isinstance(value, np.ndarray):
                return value.tolist()
            if isinstance(value, np.generic):
                return value.item()
            raise TypeError(f"non-serializable engine result: {type(value).__name__}")
        serial = json.loads(json.dumps(dict(candidates=candidates, diagnostics=diagnostics),
                                     default=native, allow_nan=False))
        return dict(**serial,
                    diagnosticOnly=True, identityOrPoseAuthorized=False,
                    evidenceKind={"synthetic": "saved-synthetic-frame",
                                  "recorded": "saved-recorded-frame"}.get(
                                      payload.get("inputKind"), "saved-frame-input-unverified"),
                    inputSha256=digest, frameSize=[frame.shape[1], frame.shape[0]])


class EngineUnavailable(RuntimeError):
    """A missing resource/provider seam, not recognition success."""
