"""Explicit capture -> shared identity/pose -> current core authorization.

No capture, listener, GUI or resource preparation starts during construction.
All algorithm work is synchronous and belongs to SessionService's worker.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

from client_paths import APP_ROOT, WORK_ROOT
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from alignment_core_v2 import AlignmentCoreV2
from client_engine import EngineAdapter


class _Cancellation:
    def __init__(self, predicate):
        self._predicate = predicate
        self._wait = threading.Event()
    def is_set(self):
        return bool(self._predicate())
    def wait(self, seconds):
        if not self.is_set():
            self._wait.wait(seconds)
        return self.is_set()


class _PreparationHost:
    """Capture/observation callbacks for the original bounded preparation loop."""
    def __init__(self, runtime, engine, obsolete):
        from map_open_preparation import CAPTURE_READY_PROBE_SECONDS, CAPTURE_VISUAL_DEADLINE_SECONDS
        self.CAPTURE_READY_PROBE_SECONDS = CAPTURE_READY_PROBE_SECONDS
        self.CAPTURE_VISUAL_DEADLINE_SECONDS = CAPTURE_VISUAL_DEADLINE_SECONDS
        self.runtime, self.engine, self.obsolete = runtime, engine, obsolete
        self.initial_context = None
        self.events = []
    def _log(self, event, **values):
        self.events.append(dict(event=event, **values))
    def _map_ready_context(self):
        return self.runtime._epoch
    def _map_ready_current(self, token, context, cancelled):
        return context == self.runtime._epoch and not cancelled.is_set()
    def _capture_map_open_full_probe(self, hwnd, token, context, capture_context, deadline_at,
                                     cancelled, **kwargs):
        from session_state_machine import verified_map_hotkey_frame
        if not self._map_ready_current(token, context, cancelled):
            return None, True
        capture_generation = self.runtime._capture_generation()
        captured = self.runtime._capture.capture(cancelled)
        frame = self.runtime._full_frame(captured)
        if self.initial_context is None:
            self.initial_context = captured.context
        if captured.context != self.initial_context or not self._map_ready_current(token, context, cancelled):
            return None, True
        observation, floor, floor_read = self.runtime._observe(self.engine, frame)
        # The shared loop supplies the original cadence/deadline. Qt requires a
        # current explicit floor before ending preparation; it never converts a
        # remembered floor into fresh display evidence during the fade.
        ready = (observation.get("mapOpen") is True if self.engine.pack.mode == "difficult"
                 else verified_map_hotkey_frame(observation, floor_read))
        if ready and floor is not None and time.perf_counter() < deadline_at:
            return (captured, observation, floor, capture_generation), False
        return None, False


class _AlignmentHost:
    """Only explicit resource/pose inputs needed by the unchanged shared solvers."""
    def __init__(self, engine, group=None, settings=None):
        self.engine, self.pack = engine, engine.pack
        self.author_coordinate_adapters = engine.qt_adapters
        self.group = group
        self.current_alignment_candidate = None
        self.settings = dict(settings or {})
        self.events = []

    def _log(self, event, **values):
        self.events.append(dict(event=event, **values))
        del self.events[:-24]

    def _shared_structure_mode_enabled(self):
        return True

    def _open_cycle_transient_motion_active(self, map_id):
        return False

    def _shared_structure_reference(self, profile, map_id, floor):
        return self.engine.qt_shared_provider(profile, map_id, floor)

    def _active_round_layer_group(self, map_id):
        return self.group if self.group is not None and self.group.map_id == map_id else None

    def _alignment_entry_locator(self):
        return self.engine.entry_identity

    def _author_route_clean_product_for_route(self, route, profile, map_id, floor):
        return self._shared_structure_reference(profile, map_id, floor)

    def _author_route_structure_reference_cached(self, route, profile, *, map_id=None, floor=None):
        reference = self._shared_structure_reference(profile, map_id, floor)
        return (reference.validation_mask, True, 0.0) if reference is not None else (None, False, 0.0)

    def _round_group_floor_view(self, map_id, floor, **kwargs):
        import numpy as np
        group = self._active_round_layer_group(map_id)
        canonical = group.canonical_pose_for_floor(str(floor)) if group else None
        basis = self.author_coordinate_adapters.matrix_profile_to_canonical("shared-structure", map_id, floor)
        if canonical is None or basis is None:
            return None
        matrix = np.asarray(canonical) @ np.asarray(basis)
        return dict(canonicalMatrixSourceToClient=canonical, recognitionMatrixSourceToClient=matrix,
                    matrixSourceToClient=matrix)

    def _canonical_geometry_current_frame_residual(self, frame, map_id, floor, profile, matrix, route, **kwargs):
        from nightmare_candidate import selected_route_current_frame_residual
        from alignment_observation import canonical_residual_evidence
        reference = self._shared_structure_reference("shared-structure", map_id, floor)
        if reference is None:
            return dict(verified=False, reason="shared-reference-unavailable"), None
        pack_id = self.author_coordinate_adapters.canonical_pack_id
        residual = dict(selected_route_current_frame_residual(frame, None, matrix,
            maximum_dimension=kwargs.get("maximum_dimension", 960),
            visible_override=kwargs.get("visible_override"), route_profile_id="wang-qingxin",
            reference_override=reference.validation_mask, shared_reference=reference))
        # These are the existing canonical consumer's provenance fields. The
        # original residual kernel supplies the verdict; no caller flag does.
        canonical_residual_evidence(residual, basis="game-rendered-shared-structure", coordinate_pack_id=pack_id)
        return residual, dict(sharedReference=reference, basis="game-rendered-shared-structure",
                              matrixSourceToClient=matrix, coordinatePackId=pack_id, profileBridgeVerified=True)

    def _rendered_profile_current_frame_residual(self, frame, route, matrix, *, profile_id=None, **kwargs):
        # The solver is explicitly bound to one map/floor before each call.
        return self._canonical_geometry_current_frame_residual(
            frame, self.map_id, self.floor, "shared-structure", matrix, route, **kwargs)[0]

    def fit(self, frame, map_id, floor, cancelled):
        from alignment_observation import rendered_profile_residual_is_verified
        from fast_structure_alignment import try_alignment
        from single_map_structure import current_geometry, solve, solve_initial_structure
        self.map_id, self.floor = map_id, str(floor)
        reference = self._shared_structure_reference("shared-structure", map_id, floor)
        if reference is None:
            return dict(success=False, reason="shared-reference-unavailable")
        context = current_geometry(frame)
        fit = try_alignment(self, frame, map_id, floor, cancelled=cancelled, context=context)
        if fit is None:
            if self.group is not None and floor in self.group.verified_floors:
                # Existing same-floor recovery keeps the committed linear pose.
                fit = solve(self, frame, map_id, floor, "shared-structure", reference.clean_mask,
                            cancelled=cancelled, _context=context)
            else:
                static = (dict(source_known_boundary=reference.known_boundary, source_unknown=reference.unknown_mask)
                          if reference.known_boundary is not None else {})
                fit = solve_initial_structure(frame, reference.clean_mask, reference.validation_mask,
                    reference.clean_mask, "shared-structure", cancelled=cancelled, _context=context,
                    wall_correspondence_identity=True, **static)
        if not fit.get("success"):
            return fit
        # Independently wrap/verify the final matrix in its actual typed canvas,
        # exactly as the production canonical consumer does after any sensor.
        residual = self._rendered_profile_current_frame_residual(
            frame, reference.clean_mask, fit["matrixSourceToClient"], profile_id="shared-structure")
        if not rendered_profile_residual_is_verified(residual):
            return dict(success=False, reason="canonical-current-frame-residual-unverified", residual=residual)
        return dict(fit, selectedRouteCurrentFrameResidual=residual,
                    coordinateSpace="shared-structure-to-client", routeProfileId="shared-structure")


class LiveRuntime:
    """One orchestration for both modes; capture provider is explicitly injected.

    evidence_kind is fixed by the composition root, never accepted from action
    payloads. Use ``synthetic``/``recorded`` for fixture capture providers: their
    real core transactions remain offline and never expose live authority.
    """
    def __init__(self, *, engine_factories, capture_provider=None,
                 allowed_read_root=None, evidence_kind="unverified", clock=None):
        if evidence_kind not in ("live-client-capture", "synthetic", "recorded", "unverified"):
            raise ValueError("unsupported capture evidence kind")
        self._offline = EngineAdapter(engine_factories=engine_factories,
                                      allowed_read_root=allowed_read_root or WORK_ROOT, clock=clock)
        self._factories, self._capture = engine_factories, capture_provider
        self._evidence_kind = evidence_kind
        self._core = AlignmentCoreV2(clock=clock)
        self._lock, self._work = threading.RLock(), threading.RLock()
        self._epoch, self._round, self._cycle, self._frame_generation = 0, 0, 0, 0
        self._mode, self._profile, self._map_id, self._floor = "nightmare", "shared-structure", "", ""
        self._frame_id = ""
        self._active, self._map_open = False, False
        self._capture_pending = False
        self._probe_misses = 0
        self._aligned_visible = True
        self._event_callback = None
        self._event_delivery_error = ""
        self._group = self._render = self._engine = None
        self._render_capture_context = None
        self._settings = {}
        self._message = "Bind a full client capture, then begin a round explicitly."

    @staticmethod
    def _cancelled(cancelled):
        return EngineAdapter._cancelled(cancelled)

    def _context(self):
        return dict(mode=self._mode, profileId=self._profile, roundId=self._round, frameId=self._frame_id)

    def snapshot(self):
        with self._lock:
            offline = self._offline.snapshot()
            capabilities = offline["savedFrameCapabilities"]
            ready = capabilities[self._mode]["available"]
            guarded = (self._evidence_kind != "live-client-capture"
                       or callable(getattr(self._capture, "is_current", None)))
            commands = list(offline["availableCommands"])
            commands += ["end_round", "close_map", "set_floor", "toggle_sheet_visibility"]
            if self._capture is not None:
                commands.append("bind_capture")
                if ready and guarded:
                    commands += ["start_round", "toggle_round"]
                if ready and guarded and self._active:
                    commands += ["capture_now", "map"]
                    if self._map_open:
                        commands.append("refresh_map")
            capture_state = getattr(self._capture, "snapshot", {}) if self._capture is not None else {}
            if callable(capture_state):
                capture_state = capture_state()
            metadata = ({key: copy.deepcopy(value) for key, value in self._render.items()
                         if key not in ("pixels",)} if self._render is not None else {})
            return dict(**self._context(), cycleId=self._cycle, mapId=self._map_id, floor=self._floor,
                generation=self._frame_generation, engineConnected=True,
                liveEngineAvailable=bool(ready and guarded and self._capture is not None),
                liveUnavailableReason=("capture-context-recheck-unavailable" if not guarded else
                                       "capture-provider-unavailable" if self._capture is None else
                                       capabilities[self._mode]["reason"] if not ready else ""),
                roundActive=self._active, roundPreparing=False, mapOpen=self._map_open,
                captureInProgress=self._capture_pending,
                alignedVisible=self._aligned_visible,
                pendingRenderToken=metadata.get("token", ""), eventDeliveryError=self._event_delivery_error,
                availableCommands=sorted(set(commands)), savedFrameCapabilities=capabilities,
                capture=copy.deepcopy(capture_state), render=metadata,
                displayAuthorized=bool(metadata), liveAuthorization=bool(metadata.get("liveAuthorization")),
                message=self._message, core=self._core.snapshot(), evidenceKind=self._evidence_kind)

    def invalidate(self, reason="invalidated"):
        """Owner-thread fast path: revoke before canceling/queueing slow work."""
        with self._lock:
            previous_token = self._render.get("token", "") if self._render is not None else ""
            self._epoch += 1
            self._core.close(reason=str(reason))
            self._render = None
            self._render_capture_context = None
            if reason in ("map-closed", "escape", "foreground-lost", "capture-context-changed", "floor-unknown"):
                self._map_open = False
            if "resource" in str(reason):
                self._active = self._map_open = False
                self._core.reset_round(reason=str(reason))
                self._group = self._engine = None
                self._map_id, self._floor = "", ""
                self._offline.invalidate_resources()
                if self._capture is not None:
                    self._capture.disable()
            self._message = str(reason)
        self._notify("render-revoked", reason=str(reason), token=previous_token)

    def set_event_callback(self, callback):
        """Install a thread-safe emitter only; it must queue work to Qt's owner."""
        if callback is not None and not callable(callback):
            raise TypeError("event callback must be callable or None")
        with self._lock:
            self._event_callback = callback

    def _notify(self, event, *, reason="", token="", **extra):
        with self._lock:
            callback = self._event_callback
            packet = dict(event=event, reason=reason, token=token, epoch=self._epoch,
                **self._context(), mapId=self._map_id, floor=self._floor,
                cycleId=self._cycle, generation=self._frame_generation,
                alignedVisible=self._aligned_visible, **extra)
        if callback is not None:
            try:
                callback(packet)
                with self._lock:
                    self._event_delivery_error = ""
            except Exception as error:
                with self._lock:
                    self._event_delivery_error = f"{type(error).__name__}:{error}"[:256]

    def _revoke_render(self, reason):
        """Revoke presentation without invalidating the current worker lifetime."""
        with self._lock:
            token = self._render.get("token", "") if self._render is not None else ""
            self._core.revoke_display(reason=reason)
            self._render = None
            self._render_capture_context = None
        self._notify("render-revoked", reason=reason, token=token)

    def configure_settings(self, settings):
        """Update explicit solver preferences; mode/profile use set_context."""
        with self._lock:
            self._settings = copy.deepcopy(dict(settings))
            visible = bool(settings.get("sheetVisibilityEnabled", self._aligned_visible))
            changed = visible != self._aligned_visible
            self._aligned_visible = visible
        if changed:
            self._notify("aligned-visibility-changed", reason="settings-updated")

    def shutdown(self):
        self.dispatch("end_round")
        if self._capture is not None:
            self._capture.shutdown()

    def render_payload(self, token):
        with self._lock:
            row = self._render
            if row is None or row["token"] != token or not self._display_current(row):
                return None
            if not self._capture_is_current(self._render_capture_context, row.get("captureGeneration")):
                self.invalidate("capture-context-changed")
                return None
            return {key: value if key == "pixels" else copy.deepcopy(value) for key, value in row.items()}

    def _capture_generation(self):
        generation = getattr(self._capture, "generation", None)
        if generation is None:
            state = getattr(self._capture, "snapshot", {})
            state = state() if callable(state) else state
            generation = state.get("generation") if isinstance(state, dict) else None
        return generation

    def _capture_is_current(self, context, generation):
        guard = getattr(self._capture, "is_current", None)
        if self._evidence_kind == "live-client-capture" and (not callable(guard) or generation is None):
            return False
        if callable(guard):
            try:
                return bool(guard(context, generation=generation))
            except Exception:
                return False
        return True  # Explicit synthetic/offline capture provider only.

    def _display_current(self, row):
        observation = self._core.active_observation
        return bool(self._active and self._map_open and row["epoch"] == self._epoch and observation
            and self._core.can_display(cycle_id=self._cycle, map_id=self._map_id, floor=self._floor,
                                      frame_generation=self._frame_generation)
            and row["frameId"] == self._frame_id and row["mode"] == self._mode)

    def _receipt(self, action, payload, *, success, reason, **extra):
        with self._lock:
            context = self._context()
            request = {key: payload.get(key) for key in ("generation", "mode", "profileId", "roundId", "frameId")}
            self._message = reason
            return dict(action=action, success=success, reason=reason, message=reason,
                        requestContext=request, resultContext=context,
                        liveAuthorization=bool(self._render and self._render.get("liveAuthorization")), **extra)

    def execute(self, action, context=None, cancel_event=None):
        return self.dispatch(action, context or {}, cancel_event)

    def dispatch(self, action, payload=None, cancelled=None):
        payload = dict(payload or {})
        action = str(action)
        if action == "toggle_sheet_visibility":
            with self._lock:
                self._aligned_visible = not self._aligned_visible
            self._notify("aligned-visibility-changed", reason="visibility-toggled")
            return self._receipt(action, payload, success=True, reason="visibility-toggled")
        if action == "map":
            from session_state_machine import map_hotkey_transition
            with self._lock:
                transition = map_hotkey_transition(self._map_open, self._capture_pending)
            if transition == "close":
                self.invalidate("map-closed")
                return self._receipt(action, payload, success=True, reason="map-closed")
            if transition == "ignore":
                return self._receipt(action, payload, success=True, reason="map-open-verification-in-progress")
        if action in ("end_round", "stop_round", "reset_round", "cancel", "close_map"):
            self.invalidate("map-closed" if action == "close_map" else action)
            with self._lock:
                if action != "close_map":
                    self._active = self._map_open = False
                    self._core.reset_round(reason=action)
                    self._group = self._engine = None
                    self._map_id, self._floor = "", ""
                    if self._capture is not None:
                        self._capture.disable()
            return self._receipt(action, payload, success=True, reason=action)
        if action in ("set_game_mode", "set_route_profile", "set_context", "set_floor"):
            mode = payload.get("mode", self._mode)
            profile = str(payload.get("profileId", self._profile))
            if mode not in ("nightmare", "difficult"):
                return self._receipt(action, payload, success=False, reason="unsupported-mode")
            if action == "set_floor":
                allowed = self._engine.identity_floors if self._engine is not None else (("1", "2", "B1") if mode == "nightmare" else ("1", "2"))
                if str(payload.get("floor", "")) not in tuple(map(str, allowed)):
                    return self._receipt(action, payload, success=False, reason="unsupported-floor")
            with self._lock:
                changed_mode = mode != self._mode
                changed_profile = profile != self._profile
                if changed_mode or changed_profile or action == "set_floor":
                    self.invalidate("mode-changed" if changed_mode else "profile-changed" if changed_profile else "floor-changed")
                if changed_mode:
                    self._active = self._map_open = False
                    self._core.reset_round(reason="mode-changed")
                    self._group = self._engine = None
                    self._map_id, self._floor = "", ""
                    if self._capture is not None:
                        self._capture.disable()
                self._mode, self._profile = mode, profile
                self._offline.dispatch("set_context", dict(mode=mode, profileId=profile))
            return self._receipt(action, payload, success=True, reason="context-updated")
        if self._cancelled(cancelled):
            return self._receipt(action, payload, success=False, reason="cancelled", cancelled=True)
        if action == "refresh_map":
            return self._refresh_map(payload, cancelled)
        if action in ("inspect_engine_resources", "rank_saved_frame", "load_saved_result", "saved_result", "load_saved_pose"):
            with self._work:
                result = self._offline.dispatch(action, payload, cancelled)
            return self._receipt(action, payload, success=result["success"], reason=result["reason"], offline=result)
        try:
            with self._work:
                if action == "bind_capture":
                    if self._capture is None:
                        raise RuntimeError("capture-provider-unavailable")
                    self.dispatch("end_round", payload)
                    self._capture.bind(int(payload["hwnd"]), tuple(payload["region"]))
                    return self._receipt(action, payload, success=True, reason="capture-bound-disabled")
                if action == "toggle_round":
                    return self.dispatch("end_round" if self._active else "start_round", payload, cancelled)
                if action == "start_round":
                    return self._start(payload, cancelled)
                if action in ("capture_now", "map"):
                    with self._lock:
                        self._capture_pending = True
                    try:
                        return self._capture_and_solve(action, payload, cancelled)
                    finally:
                        with self._lock:
                            self._capture_pending = False
            return self._receipt(action, payload, success=False, reason="unsupported-action", unavailable=True)
        except Exception as error:
            self.invalidate("runtime-error")
            return self._receipt(action, payload, success=False, reason=f"{type(error).__name__}:{error}", error=True)

    def _start(self, payload, cancelled):
        if self._capture is None:
            raise RuntimeError("capture-provider-unavailable")
        if self._evidence_kind == "live-client-capture" and not callable(getattr(self._capture, "is_current", None)):
            raise RuntimeError("capture-context-recheck-unavailable")
        with self._lock:
            epoch, mode = self._epoch, self._mode
            if self._active:
                return self._receipt("start_round", payload, success=True, reason="round-already-active")
        engine = self._factories[mode]()
        if self._cancelled(cancelled):
            return self._receipt("start_round", payload, success=False, reason="cancelled", cancelled=True)
        with self._lock:
            if epoch != self._epoch or mode != self._mode:
                return self._receipt("start_round", payload, success=False, reason="stale-context", stale=True)
            if not self._capture.enable():
                raise RuntimeError("capture-enable-rejected")
            self._core.reset_round(reason="round-started")
            self._round += 1
            self._cycle = self._frame_generation = 0
            self._probe_misses = 0
            self._active, self._map_open = True, False
            self._map_id = self._floor = self._frame_id = ""
            self._group = self._render = None
            self._engine = engine
        return self._receipt("start_round", payload, success=True, reason="round-started")

    def _refresh_map(self, payload, cancelled):
        # Passive timer requests never build an unbounded worker backlog.
        if not self._work.acquire(blocking=False):
            return self._receipt("refresh_map", payload, success=True, reason="refresh-coalesced", coalesced=True)
        owns_capture = False
        try:
            with self._lock:
                if self._capture_pending:
                    return self._receipt("refresh_map", payload, success=True, reason="refresh-coalesced", coalesced=True)
                if not self._active or not self._map_open:
                    return self._receipt("refresh_map", payload, success=False, reason="map-refresh-not-active")
                self._capture_pending = owns_capture = True
            return self._capture_and_solve("refresh_map", payload, cancelled)
        except Exception as error:
            self.invalidate("runtime-error")
            return self._receipt("refresh_map", payload, success=False,
                                 reason=f"{type(error).__name__}:{error}", error=True)
        finally:
            if owns_capture:
                with self._lock:
                    self._capture_pending = False
            self._work.release()

    def _try_refresh_current(self, payload, captured, frame, floor, digest, capture_generation,
                             engine, epoch, obsolete):
        """Renew an unchanged measured H with fresh residual/core authority."""
        import numpy as np
        from alignment_core_v2 import AlignmentCandidate
        from alignment_observation import rendered_profile_residual_is_verified
        with self._lock:
            row = self._render
            eligible = bool(row is not None and self._display_current(row)
                and row["floor"] == floor and row["profileId"] == self._profile
                and row.get("captureGeneration") == capture_generation
                and self._render_capture_context == captured.context)
            if not eligible:
                reason = "observed-floor-changed" if row is not None and row["floor"] != floor else "refresh-current-pose-unavailable"
            else:
                row = dict(row)
                map_id = self._map_id
                current_frame = self._frame_id
                cycle = self._cycle
                group = self._group
        if not eligible:
            self._revoke_render(reason)
            return None
        host = _AlignmentHost(engine, group, self._settings)
        basis = engine.qt_adapters.matrix_profile_to_canonical("shared-structure", map_id, floor)
        if basis is None:
            self._revoke_render("refresh-reference-basis-unavailable")
            return None
        canonical = np.asarray(row["canonicalMatrix"], np.float64)
        source_matrix = canonical @ np.asarray(basis, np.float64)
        residual, _ = host._canonical_geometry_current_frame_residual(
            frame, map_id, floor, "shared-structure", source_matrix, None)
        if obsolete():
            self._revoke_render("refresh-cancelled-or-stale")
            return self._receipt("refresh_map", payload, success=False, reason="cancelled-or-stale", stale=True)
        if not rendered_profile_residual_is_verified(residual):
            self._revoke_render("current-frame-residual-failed")
            return None
        with self._lock:
            if (obsolete() or self._render is None or self._render["token"] != row["token"]
                    or current_frame != self._frame_id or epoch != self._epoch
                    or cycle != self._cycle or self._floor != floor):
                self._revoke_render("refresh-cancelled-or-stale")
                return self._receipt("refresh_map", payload, success=False, reason="cancelled-or-stale", stale=True)
            if not self._capture_is_current(captured.context, capture_generation):
                self.invalidate("capture-context-changed")
                return self._receipt("refresh_map", payload, success=False, reason="capture-context-changed", stale=True)
            generation = self._frame_generation + 1
            frame_id = f"{self._round}:{generation}:{digest[:16]}"
            self._core.begin_observation(cycle_id=cycle, map_id=map_id, floor=floor,
                frame_generation=generation, frame_signature=(digest,), reason="current-pose-refresh")
            candidate = AlignmentCandidate(map_id, floor, canonical,
                "same-floor-reopen-verified-refresh", generation, confidence=1.0, anchor_count=1,
                anchor_kind="global", current_frame_verified=True, residual_verified=True,
                global_residual_verified=True, translation_only=True,
                metadata=dict(routeProfileId="shared-structure"))
            decision = self._core.adjudicate(candidate)
            token = self._core.authorize(decision)
            if token is None or not self._core.commit(token, persist_pose=False):
                self._revoke_render("refresh-core-commit-rejected")
                return self._receipt("refresh_map", payload, success=False, reason=decision.reason)
            self._frame_generation, self._frame_id = generation, frame_id
            self._probe_misses = 0
            self._render = dict(row, authorizationId=token.authorization_id, frameId=frame_id,
                                generation=generation)
            self._render_capture_context = captured.context
            render_token = self._render["token"]
        self._notify("render-ready", reason="current-pose-refreshed", token=render_token, unchanged=True)
        return self._receipt("refresh_map", payload, success=True, reason="current-pose-refreshed",
                             renderToken=render_token, unchanged=True,
                             diagnosticOnly=self._evidence_kind != "live-client-capture")

    def _capture_and_solve(self, action, payload, cancelled):
        import numpy as np
        from alignment_core_v2 import AlignmentCandidate
        from alignment_observation import rendered_profile_residual_is_verified
        from round_layer_group import RoundLayerGroup

        with self._lock:
            if not self._active:
                return self._receipt(action, payload, success=False, reason="round-not-active")
            epoch, engine, group = self._epoch, self._engine, self._group
        if action != "refresh_map":
            self._revoke_render("new-capture")
        obsolete = lambda: self._cancelled(cancelled) or epoch != self._epoch
        if action == "map":
            from map_open_preparation import prepare_map_open_frame
            preparation = _PreparationHost(self, engine, obsolete)
            prepared = prepare_map_open_frame(preparation, None, self._cycle + 1,
                time.perf_counter() + preparation.CAPTURE_VISUAL_DEADLINE_SECONDS,
                _Cancellation(obsolete), silent_hint=True, round_identity_locked=bool(self._map_id),
                capture_context_provider=lambda hwnd: None)
            if prepared is None:
                if obsolete():
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                self.invalidate("map-closed")
                return self._receipt(action, payload, success=False, reason="map-open-preparation-deadline")
            captured, observation, floor, capture_generation = prepared
            opened = True
        else:
            capture_generation = self._capture_generation()
            captured = self._capture.capture(obsolete)
            observation, floor, floor_read = self._observe(engine, self._full_frame(captured))
            from session_state_machine import verified_map_hotkey_frame
            opened = (observation.get("mapOpen") if engine.pack.mode == "difficult"
                      else verified_map_hotkey_frame(observation, floor_read))
        frame = self._full_frame(captured)
        width, height = captured.context.full_client_size
        frame = frame.copy()
        frame.setflags(write=False)
        if obsolete():
            if action == "refresh_map":
                self._revoke_render("refresh-cancelled-or-stale")
            return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
        if opened is not True:
            if action == "refresh_map":
                from session_state_machine import map_probe_transition
                probe_open = (observation.get("mapOpen") if engine.pack.mode == "difficult"
                              else bool(observation.get("detected")))
                with self._lock:
                    self._probe_misses, transition = map_probe_transition(
                        self._map_open, self._probe_misses, probe_open)
                reason = "current-floor-unavailable" if probe_open is True and floor is None else "map-not-observed"
                self._revoke_render(reason)
                if transition == "close":
                    self.invalidate("map-closed")
                return self._receipt(action, payload, success=False, reason=reason, observation=observation)
            self.invalidate("map-closed")
            return self._receipt(action, payload, success=False, reason="map-not-observed", observation=observation)
        if floor is None or str(floor) not in tuple(map(str, engine.identity_floors)):
            if action == "refresh_map":
                self._revoke_render("current-floor-unavailable")
                return self._receipt(action, payload, success=False, reason="current-floor-unavailable", observation=observation)
            self.invalidate("floor-unknown")
            return self._receipt(action, payload, success=False, reason="current-floor-unavailable", observation=observation)
        floor = str(floor)
        digest = hashlib.sha256(frame.tobytes()).hexdigest()
        if action == "refresh_map":
            renewed = self._try_refresh_current(payload, captured, frame, floor, digest, capture_generation,
                                                 engine, epoch, obsolete)
            if renewed is not None:
                return renewed
        with self._lock:
            if obsolete():
                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
            if not self._map_open:
                self._cycle += 1
            self._map_open = True
            self._probe_misses = 0
            self._floor = floor
            self._frame_generation += 1
            self._frame_id = f"{self._round}:{self._frame_generation}:{digest[:16]}"
            frame_generation, frame_id, cycle = self._frame_generation, self._frame_id, self._cycle
            map_id = self._map_id
        ranked = []
        if not map_id:
            ranked = engine.rank(frame, floor=floor, limit=len(engine.pack.maps), budget_seconds=1.0)
            accepted = {row["mapId"] for row in ranked if row.get("identityHardGatePassed") is True}
            if len(accepted) != 1:
                return self._receipt(action, payload, success=False, reason="identity-not-uniquely-verified",
                                     candidateMapIds=[row["mapId"] for row in ranked[:8]])
            map_id = next(iter(accepted))
            with self._lock:
                if obsolete() or frame_id != self._frame_id:
                    return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
                self._map_id = map_id
        host = _AlignmentHost(engine, group, self._settings)
        fit = host.fit(frame, map_id, floor, obsolete)
        if obsolete():
            return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
        if not fit.get("success"):
            return self._receipt(action, payload, success=False, reason=str(fit.get("reason", "pose-fit-failed")))
        residual = fit.get("selectedRouteCurrentFrameResidual")
        if not rendered_profile_residual_is_verified(residual):
            raise ValueError("current-frame-canonical-verification-required")
        source_matrix = np.asarray(fit["matrixSourceToClient"], np.float64)
        basis = engine.qt_adapters.matrix_profile_to_canonical("shared-structure", map_id, floor)
        if basis is None:
            raise ValueError("shared-reference-canonical-basis-unavailable")
        canonical = source_matrix @ np.linalg.inv(np.asarray(basis))
        display = self._display_layer(engine, map_id, floor, self._profile)
        render_matrix = source_matrix @ display["matrixDisplayToReference"]
        with self._lock:
            if obsolete() or frame_id != self._frame_id or cycle != self._cycle or floor != self._floor:
                return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
            if not self._capture_is_current(captured.context, capture_generation):
                self.invalidate("capture-context-changed")
                return self._receipt(action, payload, success=False, reason="capture-context-changed", stale=True)
            self._map_id = map_id
            self._core.begin_observation(cycle_id=cycle, map_id=map_id, floor=floor,
                                         frame_generation=frame_generation, frame_signature=(digest,))
            previous = self._core.pose_by_map.get(map_id)
            previous_matrix = previous.pose_for_floor(floor) if previous is not None else None
            reopen = previous_matrix is not None
            candidate = AlignmentCandidate(map_id, floor, canonical,
                "same-floor-reopen-shared-structure" if reopen else "initial-structure-v2", frame_generation,
                confidence=1.0, anchor_count=1, anchor_kind="global", current_frame_verified=True,
                residual_verified=True, global_residual_verified=True,
                translation_only=bool(reopen and np.allclose(previous_matrix[:2,:2], canonical[:2,:2], atol=1e-5, rtol=0)),
                metadata=dict(routeProfileId="shared-structure", independentFloorAnchor=True))
            decision = self._core.adjudicate(candidate)
            if not decision.accepted:
                return self._receipt(action, payload, success=False, reason=decision.reason)
            token = self._core.authorize(decision)
            if token is None or not self._core.commit(token):
                return self._receipt(action, payload, success=False, reason="core-commit-rejected")
            self._group = RoundLayerGroup(map_id, "shared-structure", floor, canonical,
                cycle_id=cycle, source=candidate.source, pose_client_size=(width, height))
            self._group.canonical_geometry_verified_floors.add(floor)
            rgba = np.ascontiguousarray(display["pixels"][:, :, [2, 1, 0, 3]])
            rgba.setflags(write=False)
            live = self._evidence_kind == "live-client-capture"
            self._render = dict(token=uuid4().hex, epoch=epoch, authorizationId=token.authorization_id,
                mode=self._mode, profileId=self._profile, mapId=map_id, floor=floor,
                roundId=self._round, cycleId=cycle, frameId=frame_id, generation=frame_generation,
                matrix=render_matrix.tolist(), canonicalMatrix=canonical.tolist(), pixels=rgba,
                routeWidth=rgba.shape[1], routeHeight=rgba.shape[0], clientWidth=width, clientHeight=height,
                originX=captured.context.client_screen_bounds[0], originY=captured.context.client_screen_bounds[1],
                captureGeneration=capture_generation,
                resourceIdentity=display["resourceIdentity"], displayKind=display["displayKind"],
                displayLabel=display["displayLabel"], routeProfileId=display["routeProfileId"],
                liveAuthorization=live, evidenceKind=self._evidence_kind)
            self._render_capture_context = captured.context
            render_token = self._render["token"]
        self._notify("render-ready", reason="current-frame-pose-authorized", token=render_token, unchanged=False)
        return self._receipt(action, payload, success=True, reason="current-frame-pose-authorized",
                             renderToken=render_token, diagnosticOnly=not live)

    @staticmethod
    def _full_frame(captured):
        import numpy as np
        frame = np.asarray(captured.frame_bgr)
        width, height = captured.context.full_client_size
        if (frame.dtype != np.uint8 or frame.shape != (height, width, 3)
                or tuple(captured.roi_client_bounds) != (0, 0, width, height)):
            raise ValueError("live-runtime-requires-full-client-capture")
        return frame

    @staticmethod
    def _observe(engine, frame):
        if engine.pack.mode == "difficult":
            result = engine.qt_map_ui.observe(frame)
            floor = str(result["floor"]) if result.get("floor") is not None else None
            return result, floor, result
        from map_interface_observation import detect_map_interface, detect_selected_floor
        floor_read = detect_selected_floor(frame)
        result = detect_map_interface(frame, floor_read=floor_read)
        floor = floor_read.get("selectedFloor")
        floor = "B1" if floor == -1 else str(floor) if floor in (1, 2) else None
        return result, floor, floor_read

    @staticmethod
    def _display_layer(engine, map_id, floor, profile):
        import numpy as np
        reference = engine.qt_shared_provider("shared-structure", map_id, floor)
        if reference is None:
            raise ValueError("display-reference-unavailable")
        optional = engine.qt_optional_displays
        binding = optional.binding(profile, map_id, floor)
        if binding is not None and binding["usable"]:
            pixels = optional.pixels(binding["path"], "aligned-overlay")
            source_basis = engine.qt_adapters.matrix_profile_to_canonical("shared-structure", map_id, floor)
            if source_basis is None:
                raise ValueError("display-reference-bridge-unavailable")
            return dict(pixels=pixels, matrixDisplayToReference=np.linalg.inv(source_basis) @ binding["matrix"],
                resourceIdentity=str(binding["path"]), displayKind="author-route", displayLabel=profile,
                routeProfileId=profile)
        # The existing shared-structure fallback displays this exact typed mask;
        # no author bitmap participates in identity, fitting or residual checks.
        pixels = np.zeros((*reference.shape, 4), np.uint8)
        pixels[reference.clean_mask != 0] = (218, 228, 238, 255)
        return dict(pixels=pixels, matrixDisplayToReference=np.eye(3),
            resourceIdentity=dict(path=str(reference.path), binding=reference.metadata["binding"],
                                  structureVersionKey=reference.metadata.get("structureVersionKey")),
            displayKind="shared-structure", displayLabel="Structure fallback; selected author display unavailable",
            routeProfileId="shared-structure")


SharedLiveRuntime = LiveRuntime
