"""Explicit capture -> shared identity/pose -> current core authorization.

No capture, listener, GUI or resource preparation starts during construction.
All algorithm work is synchronous and belongs to SessionService's worker.
"""
from __future__ import annotations

import copy
import hashlib
import math
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
from floor_observation_policy import (MANUAL_FLOOR_HOLD_SECONDS,
                                      read_floor_evidence, decide_floor_transition)


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
        self.last_unresolved = None
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
        map_detected = (observation.get("mapOpen") is True if self.engine.pack.mode == "difficult"
                        else observation.get("detected") is True)
        self.last_unresolved = ((captured, observation, floor, capture_generation, floor_read)
                                if map_detected and floor is None else None)
        # The shared loop supplies the original cadence/deadline. Qt requires a
        # current explicit floor before ending preparation; it never converts a
        # remembered floor into fresh display evidence during the fade.
        ready = (observation.get("mapOpen") is True if self.engine.pack.mode == "difficult"
                 else verified_map_hotkey_frame(observation, floor_read))
        if ready and floor is not None and time.perf_counter() < deadline_at:
            return (captured, observation, floor, capture_generation, floor_read), False
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

    def _reverify_entry(self, frame, map_id, floor, reference, candidate, cancelled):
        """Adapt the legacy bounded entry set through its existing verifiers."""
        import cv2
        from alignment_observation import rendered_profile_residual_is_verified
        if (not isinstance(candidate, dict) or candidate.get("mapId") != map_id
                or str(candidate.get("floor", floor)) != str(floor)):
            return None
        side = candidate.get("locatorKind") == "side" or candidate.get("entryKind") == "side"
        anchored = side and ("entryPoseCandidates" in candidate or candidate.get("exitAnchoredEntry") is True)
        candidates = candidate.get("entryPoseCandidates") if anchored else [candidate]
        candidates = candidates if isinstance(candidates, list) else [candidate]
        accepted = []
        for item in candidates:
            if cancelled and cancelled():
                return dict(success=False, reason="structure-work-obsolete")
            if (not isinstance(item, dict) or item.get("mapId") != map_id
                    or str(item.get("floor", floor)) != str(floor)):
                continue
            try:
                if side:
                    locator = self.engine.side_entry_identity
                    checked = locator.verify(frame, item) if locator is not None else None
                elif isinstance(item.get("hypothesis"), dict):
                    entry = self.engine.entry_identity
                    checked = entry.verify(frame, dict(mapId=map_id, hypothesis=item["hypothesis"]),
                        context=entry.geometry(frame), route=reference.clean_mask)
                else:
                    checked = None
                if isinstance(checked, dict) and checked.get("success"):
                    residual = self._rendered_profile_current_frame_residual(
                        frame, reference.clean_mask, checked["matrixSourceToClient"], profile_id="shared-structure")
                    if rendered_profile_residual_is_verified(residual):
                        accepted.append((checked, residual))
            except (ValueError, TypeError, KeyError, OSError, cv2.error) as error:
                self._log("entry-seed-reverification-unavailable", error=str(error))
        if cancelled and cancelled():
            return dict(success=False, reason="structure-work-obsolete")
        if len(accepted) == 1:
            checked, residual = accepted[0]
            return dict(success=True, reason="difficult-entry-current-frame-candidate",
                matrixSourceToClient=checked["matrixSourceToClient"],
                coordinateSpace="shared-structure-to-client", routeProfileId="shared-structure",
                selectedRouteCurrentFrameResidual=residual, strategy="verified-entry-current-frame",
                difficultEntryValidation=checked)
        if anchored:
            # Legacy exit-anchored candidates never fall through to a broad
            # search after failure/ambiguity. No pose-index fallback is enabled
            # by this host, so retain the original explicit rejection.
            return dict(success=False, reason="difficult-entry-current-frame-rejected",
                entryReverification=dict(candidateCount=len(candidates), acceptedPoseCount=len(accepted),
                    reason="side-entry-pose-ambiguous" if accepted else "side-entry-pose-unverified"))
        return None

    def fit(self, frame, map_id, floor, cancelled, entry_candidate=None):
        from alignment_observation import rendered_profile_residual_is_verified
        from fast_structure_alignment import try_alignment
        from single_map_structure import current_geometry, solve, solve_initial_structure
        self.map_id, self.floor = map_id, str(floor)
        reference = self._shared_structure_reference("shared-structure", map_id, floor)
        if reference is None:
            return dict(success=False, reason="shared-reference-unavailable")
        # Preserve the legacy first-pose entry seam: the accepted rank row is
        # bound to this worker's current frame. Re-run its original verifier,
        # then the canonical residual; never accept a caller-supplied matrix.
        seed = self._reverify_entry(frame, map_id, floor, reference, entry_candidate, cancelled)
        if seed is not None:
            return seed
        context = current_geometry(frame)
        fit = try_alignment(self, frame, map_id, floor, cancelled=cancelled, context=context)
        strategy = "shared-fast-alignment"
        if fit is None:
            if self.group is not None and floor in self.group.verified_floors:
                # Existing same-floor recovery keeps the committed linear pose.
                fit = solve(self, frame, map_id, floor, "shared-structure", reference.clean_mask,
                            cancelled=cancelled, _context=context)
                strategy = "shared-same-floor-recovery"
            else:
                static = (dict(source_known_boundary=reference.known_boundary, source_unknown=reference.unknown_mask)
                          if reference.known_boundary is not None else {})
                fit = solve_initial_structure(frame, reference.clean_mask, reference.validation_mask,
                    reference.clean_mask, "shared-structure", cancelled=cancelled, _context=context,
                    wall_correspondence_identity=True, **static)
                strategy = "shared-initial-structure"
        if not fit.get("success"):
            return fit
        # Independently wrap/verify the final matrix in its actual typed canvas,
        # exactly as the production canonical consumer does after any sensor.
        residual = self._rendered_profile_current_frame_residual(
            frame, reference.clean_mask, fit["matrixSourceToClient"], profile_id="shared-structure")
        if not rendered_profile_residual_is_verified(residual):
            return dict(success=False, reason="canonical-current-frame-residual-unverified", residual=residual)
        return dict(fit, selectedRouteCurrentFrameResidual=residual,
                    coordinateSpace="shared-structure-to-client", routeProfileId="shared-structure",
                    strategy=strategy)


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
        self._monotonic = clock or time.monotonic
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
        self._reference_sheet = None
        self._reference_capture_context = None
        self._settings = {}
        self._gate = None
        self._candidate_rows = []
        self._candidate_edit = False
        self._selection_pending = False
        self._manual_floor = None
        self._observed_floor = ""
        self._last_observation = None
        self._reset_floor_policy()
        self._message = "Bind a full client capture, then begin a round explicitly."

    @staticmethod
    def _cancelled(cancelled):
        return EngineAdapter._cancelled(cancelled)

    def _context(self):
        return dict(mode=self._mode, profileId=self._profile, roundId=self._round, frameId=self._frame_id)

    def _clear_candidates(self):
        self._reference_sheet = None
        self._reference_capture_context = None
        self._gate = None
        self._candidate_rows = []
        self._candidate_edit = False
        self._selection_pending = False
        self._manual_floor = None
        self._observed_floor = ""
        self._last_observation = None
        self._reset_floor_policy()

    def _reset_floor_policy(self):
        self._floor_observations = []
        self._manual_override = False
        self._floor_hard_locked = False
        self._manual_hold_until = 0.0
        self._floor_required = True
        self._game_floor_intent = None
        self._last_floor_policy = ""

    def _observe_floor_policy(self, floor_read, *, obsolete=None):
        """Apply the shared legacy temporal/intent decision, never pose authority."""
        with self._lock:
            if obsolete is not None and obsolete():
                return None
            evidence = read_floor_evidence(floor_read, self._floor_observations)
            self._floor_observations = list(evidence.observations)
            self._observed_floor = ("B1" if evidence.observed == -1 else str(evidence.observed)
                                    if evidence.observed in (1, 2) else "")
            policy = decide_floor_transition(evidence, current_floor=self._floor,
                floor_required=self._floor_required, manual_override=self._manual_override,
                hard_locked=self._floor_hard_locked, hold_until=self._manual_hold_until,
                cycle_id=self._cycle if self._map_open else self._cycle + 1,
                game_intent=self._game_floor_intent, now=self._monotonic())
            self._last_floor_policy = policy.reason
            if policy.clear_game_intent:
                self._game_floor_intent = None
            if policy.manual_hold_ended:
                self._manual_override = False
                self._manual_floor = None
            if policy.clear_floor_required:
                self._floor_required = False
            if policy.apply_target:
                self._floor = policy.target
                self._manual_floor = None
            if policy.reason == "pending-click-mismatch":
                return None
            return self._floor if evidence.stable is not None or self._manual_override else None

    def _available_floors(self):
        if self._engine is None:
            return []
        return list(map(str, getattr(self._engine.pack, "floors", self._engine.identity_floors)))

    def _update_candidates(self, ranked, floor):
        # The legacy unfiltered view keeps every map; scores reorder, never hide.
        scores = {row["mapId"]: row for row in ranked}
        maps = sorted(self._engine.pack.maps,
            key=lambda item: float(scores.get(item.id, {}).get("score", -999)), reverse=True)
        rows = []
        for item in maps:
            evidence = scores.get(item.id, {})
            score = evidence.get("score")
            score = float(score) if isinstance(score, (int, float)) and math.isfinite(score) else None
            rows.append(dict(key=item.id, mapId=item.id, name=str(getattr(item, "name", item.id)),
                floor=str(floor), score=score,
                reason="入口结构已确认" if evidence.get("identityHardGatePassed") is True else
                       "请对照地图人工选择"))
        self._candidate_rows = rows

    def _manual_action(self, action, payload, cancelled):
        if action in ("set_floor", "cycle_floor"):
            return self._set_floor(action, payload, cancelled)
        if action in ("reselect_map", "map_reselection"):
            with self._work:
                with self._lock:
                    epoch, mode = self._epoch, self._mode
                    engine = self._engine
                engine = engine or self._factories[mode]()
                with self._lock:
                    if self._cancelled(cancelled) or epoch != self._epoch or mode != self._mode:
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                    if self._gate is None:
                        from nightmare_candidate import RouteDisplayGate
                        self._engine = engine
                        self._gate = RouteDisplayGate(engine.pack)
                        self._update_candidates([], self._floor)
                    self._candidate_edit = True
                    self._gate.selected_id = self._gate.confirmed_id or self._gate.selected_id
                    return self._receipt(action, payload, success=True, reason="candidate-reselection-opened")
        with self._lock:
            if self._gate is None or (not self._active and not self._candidate_edit):
                return self._receipt(action, payload, success=False, reason="candidate-control-unavailable")
            if action in ("previous_candidate", "next_candidate", "select_candidate"):
                if self._map_id and (action != "select_candidate" or not self._candidate_edit):
                    return self._receipt(action, payload, success=False, reason="identity-locked")
                count = len(self._candidate_rows)
                if not count:
                    return self._receipt(action, payload, success=False, reason="no-candidates")
                if action == "select_candidate":
                    index = payload.get("index")
                    if type(index) is not int or not 0 <= index < count:
                        return self._receipt(action, payload, success=False, reason="invalid-candidate-index")
                else:
                    selected = next((i for i, row in enumerate(self._candidate_rows)
                                     if row["key"] == self._gate.selected_id), None)
                    step = -1 if action == "previous_candidate" else 1
                    index = ((selected if selected is not None else (0 if step < 0 else -1)) + step) % count
                key = self._candidate_rows[index]["key"]
                if key != self._gate.confirmed_id:
                    self.invalidate("candidate-selected")
                    self._selection_pending = True
                self._gate.select(key)
                return self._receipt(action, payload, success=True, reason="candidate-selected")
            if action != "confirm_route":
                return self._receipt(action, payload, success=False, reason="unsupported-action")
            if not self._active:
                return self._receipt(action, payload, success=False, reason="start-round-before-confirming-map")
            if self._map_id and not self._candidate_edit:
                return self._receipt(action, payload, success=False, reason="identity-locked")
            if not self._floor or not self._gate.confirm():
                return self._receipt(action, payload, success=False, reason="candidate-or-floor-missing")
            selected = self._gate.confirmed_id
            self.invalidate("manual-identity-confirmed")
            if selected != self._map_id:
                self._core.reset_round(reason="manual-map-changed")
                self._group = None
            self._map_id = selected
            self._candidate_edit = False
            self._selection_pending = False
            confirmation = (self._epoch, self._engine, self._mode, self._round,
                            selected, self._floor, self._frame_generation)
        with self._work:
            try:
                result = self._solve_retained(action, payload, cancelled)
            except Exception as error:
                # Legacy also records a valid human identity when pose work raises.
                # Keep the dispatcher's existing pose error/revocation behavior,
                # and retain independent learning diagnostics in its receipt.
                learning = self._learn_confirmation(confirmation, cancelled)
                self.invalidate("manual-action-error")
                result = self._receipt(action, payload, success=False,
                    reason=f"{type(error).__name__}:{error}", error=True)
            else:
                learning = self._learn_confirmation(confirmation, cancelled)
            result["learning"] = learning
            if learning["status"] == "error":
                warning = ("标签已存，但记忆刷新失败" if learning["saved"]
                           else "学习标签保存失败")
                result["message"] = f"{result['message']}；{warning}：{learning['error']}"
            return result

    def _learn_confirmation(self, confirmation, cancelled):
        from client_learning import persist_manual_confirmation
        # No action payload can promote saved/replayed/fixture pixels to live
        # evidence. Automatic gate.confirm calls never enter this method.
        if self._evidence_kind != "live-client-capture":
            return dict(status="skipped", reason="non-live-evidence", saved=False)
        epoch, engine, mode, round_id, map_id, floor, generation = confirmation
        with self._lock:
            observation = self._last_observation
            if (self._cancelled(cancelled) or epoch != self._epoch
                    or engine is not self._engine or mode != self._mode or round_id != self._round
                    or not self._active or not self._map_open or map_id != self._map_id
                    or floor != self._floor or self._selection_pending
                    or observation is None or observation["frame_id"] != self._frame_id
                    or self._frame_generation != generation + 1):
                return dict(status="skipped", reason="confirmation-context-obsolete", saved=False)
            captured = observation["captured"]
            if not self._capture_is_current(captured.context, observation["capture_generation"]):
                return dict(status="skipped", reason="capture-context-changed", saved=False)
            # This is the acceptance point for the human label: invalidation
            # before this check rejects it; after acceptance the original write
            # drains even if the round closes. Retain the immutable captured
            # pixels/context, never read replacement state during disk IO.
            frame = observation["frame"]
            bounds = tuple(captured.context.client_screen_bounds)
        # The existing worker/_work owns this one submission. Keep filesystem IO
        # and engine cache reload outside the hot state lock so snapshot/close
        # stay responsive. No later automatic result can retry this submission,
        # including after a partial write/reload failure.
        learning = persist_manual_confirmation(engine, map_id, floor, frame, bounds)
        learning.update(mode=mode, roundId=round_id, mapId=map_id, floor=floor)
        self._notify("memory-saved" if learning["status"] == "saved" else "memory-error",
                     reason=learning.get("error", "human-confirmed"), learning=learning)
        return learning

    def _set_floor(self, action, payload, cancelled):
        with self._lock:
            floors = self._available_floors()
            if not floors or (action == "cycle_floor" and not self._map_open):
                return self._receipt(action, payload, success=False, reason="floor-control-unavailable")
            if action == "cycle_floor":
                current = self._manual_floor or self._floor
                target = floors[(floors.index(current) + 1) % len(floors)] if current in floors else floors[0]
            else:
                value = payload.get("floor")
                target = None if value in (None, "", "auto") else str(value)
                if target is not None and target not in floors:
                    return self._receipt(action, payload, success=False, reason="unsupported-floor")
            self.invalidate("floor-changed")
            self._manual_floor = target
            self._manual_override = target is not None
            self._floor_hard_locked = False
            self._manual_hold_until = self._monotonic() + MANUAL_FLOOR_HOLD_SECONDS if target else 0.0
            self._floor_observations = []
            self._floor_required = target is None and not self._observed_floor
            self._floor = target or self._observed_floor
            for row in self._candidate_rows:
                row["floor"] = self._floor
        # As in legacy select_floor, immediately attempt the target geometry.
        # The worker acquires a current frame; the sensor is kept separately;
        # neither the manual target nor an old pose can grant authorization.
        return self._solve_retained(action, payload, cancelled)

    def _solve_retained(self, action, payload, cancelled, *, acquire_current=True):
        with self._lock:
            observation = self._last_observation
            if self._selection_pending:
                return self._receipt(action, payload, success=False, reason="candidate-awaiting-confirmation")
            if (not self._map_open or observation is None
                    or observation["frame_id"] != self._frame_id or not self._floor):
                return self._receipt(action, payload, success=True,
                    reason="identity-confirmed-pose-pending" if action == "confirm_route" else "floor-target-updated",
                    posePending=True)
            scope = (self._engine, self._group, self._epoch, self._frame_generation,
                     self._frame_id, self._cycle, self._map_id, self._floor)
        with self._work:
            engine, group, epoch, generation, frame_id, cycle, selected, floor = scope
            if self._cancelled(cancelled) or epoch != self._epoch:
                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
            if acquire_current:
                # Geometry/foreground rechecks cannot establish pixel freshness.
                # Manual UI actions explicitly capture once in the same worker.
                return self._capture_and_solve(action, payload, cancelled)
            entry_candidate = None
            if not selected:
                ranked = engine.rank(observation["frame"], floor=floor,
                    limit=len(engine.pack.maps), budget_seconds=1.0)
                with self._lock:
                    if self._cancelled(cancelled) or epoch != self._epoch or frame_id != self._frame_id:
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                    self._update_candidates(ranked, floor)
                    accepted = {row["mapId"] for row in ranked if row.get("identityHardGatePassed") is True}
                    if len(accepted) != 1:
                        return self._receipt(action, payload, success=False, reason="identity-not-uniquely-verified")
                    selected = self._map_id = next(iter(accepted))
                    self._gate.select(selected)
                    self._gate.confirm()
                    entry_candidate = next((row.get("difficultEntry") for row in ranked
                        if row.get("mapId") == selected and row.get("identityHardGatePassed") is True), None)
            return self._solve_observation(action, payload, cancelled, engine, group, epoch,
                observation["captured"], observation["frame"], floor, observation["digest"],
                observation["capture_generation"], generation, frame_id, cycle, selected, entry_candidate)

    def snapshot(self):
        with self._lock:
            offline = self._offline.snapshot()
            capabilities = offline["savedFrameCapabilities"]
            ready = capabilities[self._mode]["available"]
            guarded = (self._evidence_kind != "live-client-capture"
                       or callable(getattr(self._capture, "is_current", None)))
            commands = list(offline["availableCommands"])
            commands += ["end_round", "close_map", "toggle_sheet_visibility"]
            if ready:
                commands.append("reselect_map")
            if self._candidate_edit and self._gate is not None:
                commands += ["select_candidate", "previous_candidate", "next_candidate"]
            if self._engine is not None:
                commands.append("set_floor")
            if self._active and self._map_open:
                commands.append("cycle_floor")
                if self._map_id:
                    commands.append("reselect_map")
                if self._candidate_rows and (not self._map_id or self._candidate_edit):
                    commands.append("select_candidate")
                    if not self._map_id:
                        commands += ["previous_candidate", "next_candidate"]
                    if self._gate is not None and self._gate.selected_id and self._floor:
                        commands.append("confirm_route")
            selected = next((i for i, row in enumerate(self._candidate_rows)
                             if self._gate is not None and row["key"] == self._gate.selected_id), -1)
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
                candidateRows=copy.deepcopy(self._candidate_rows), selectedCandidateIndex=selected,
                selectedCandidate=copy.deepcopy(self._candidate_rows[selected]) if selected >= 0 else {},
                identityLocked=bool(self._map_id), candidateEditMode=self._candidate_edit,
                availableFloors=self._available_floors(), manualFloor=self._manual_floor,
                observedFloor=self._observed_floor, floorSource="manual" if self._manual_floor else "automatic",
                floorRequired=self._floor_required, floorPolicyReason=self._last_floor_policy,
                manualFloorHoldRemaining=max(0.0, self._manual_hold_until - self._monotonic())
                    if self._manual_override and not self._floor_hard_locked else 0.0,
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
                self._clear_candidates()
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
            variant_changed = settings.get("zhan10CoffinVariantId", "variant-01") != self._settings.get("zhan10CoffinVariantId", "variant-01")
            self._settings = copy.deepcopy(dict(settings))
            if variant_changed:
                self._display_generation = getattr(self, "_display_generation", 0) + 1
                if (self._profile, self._map_id, str(self._floor)) == ("zhan10-coffin", "nightmare-01", "1"):
                    # Retain identity, floor and canonical pose. Only presentation
                    # authority expires; the next normal refresh revalidates it.
                    self._revoke_render("coffin-variant-changed")
            visible = bool(settings.get("sheetVisibilityEnabled", self._aligned_visible))
            changed = visible != self._aligned_visible
            self._aligned_visible = visible
        if changed:
            self._notify("aligned-visibility-changed", reason="settings-updated")

    def route_preferences_metadata(self):
        from client_route_preferences import coffin_variant_metadata
        with self._lock:
            context = (self._engine, self._mode, self._profile, self._map_id, self._floor)
        return coffin_variant_metadata(*context)

    def route_preferences_context(self):
        with self._lock:
            return dict(mode=self._mode, profileId=self._profile, mapId=self._map_id,
                        floor=str(self._floor), resourceIdentity=str(id(self._engine)))

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

    def reference_payload(self):
        """Confirmed identity/floor grants a static sheet, never an aligned pose.

        Its pixels are prepared by the capture worker. Closing the map retains
        this confirmed sheet; changing identity, floor, binding or round does not.
        """
        with self._lock:
            row = self._reference_sheet
            if (row is None or not self._active or self._selection_pending or self._floor_required
                    or self._gate is None or self._gate.confirmed_id != self._map_id
                    or any(row.get(key) != expected for key, expected in (
                        ("mode", self._mode), ("profileId", self._profile), ("mapId", self._map_id),
                        ("floor", self._floor), ("roundId", self._round),
                        ("displayGeneration", getattr(self, "_display_generation", 0))))
                    or not self._capture_is_current(self._reference_capture_context, row.get("captureGeneration"))):
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
        if action in ("previous_candidate", "next_candidate", "select_candidate", "confirm_route",
                      "reselect_map", "map_reselection", "set_floor", "cycle_floor"):
            if self._cancelled(cancelled):
                return self._receipt(action, payload, success=False, reason="cancelled", cancelled=True)
            try:
                return self._manual_action(action, payload, cancelled)
            except Exception as error:
                self.invalidate("manual-action-error")
                return self._receipt(action, payload, success=False,
                    reason=f"{type(error).__name__}:{error}", error=True)
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
                    self._clear_candidates()
                    self._map_id, self._floor = "", ""
                    if self._capture is not None:
                        self._capture.disable()
            return self._receipt(action, payload, success=True, reason=action)
        if action in ("set_game_mode", "set_route_profile", "set_context"):
            mode = payload.get("mode", self._mode)
            profile = str(payload.get("profileId", self._profile))
            if mode not in ("nightmare", "difficult"):
                return self._receipt(action, payload, success=False, reason="unsupported-mode")
            with self._lock:
                changed_mode = mode != self._mode
                changed_profile = profile != self._profile
                if changed_mode and self._active:
                    return self._receipt(action, payload, success=False,
                                         reason="end-round-before-changing-mode")
                if changed_mode or changed_profile:
                    self.invalidate("mode-changed" if changed_mode else "profile-changed")
                if changed_mode:
                    self._active = self._map_open = False
                    self._core.reset_round(reason="mode-changed")
                    self._group = self._engine = None
                    self._clear_candidates()
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
            preselected = self._gate.selected_id if self._candidate_edit and self._gate is not None else None
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
            from nightmare_candidate import RouteDisplayGate
            self._clear_candidates()
            self._gate = RouteDisplayGate(engine.pack)
            self._update_candidates([], "")
            if preselected and preselected in engine.pack.by_id:
                self._gate.select(preselected)
                self._candidate_edit = self._selection_pending = True
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
            self._last_observation = dict(captured=captured, frame=frame, floor=floor, digest=digest,
                capture_generation=capture_generation, frame_id=frame_id, observed_floor=self._observed_floor)
            self._probe_misses = 0
            self._render = dict(row, authorizationId=token.authorization_id, frameId=frame_id,
                generation=generation, observedFloor=self._observed_floor,
                floorSource="manual" if self._manual_floor else "automatic")
            self._render_capture_context = captured.context
            render_token = self._render["token"]
        self._notify("render-ready", reason="current-pose-refreshed", token=render_token, unchanged=True)
        return self._receipt("refresh_map", payload, success=True, reason="current-pose-refreshed",
                             renderToken=render_token, unchanged=True,
                             diagnosticOnly=self._evidence_kind != "live-client-capture")

    def _capture_and_solve(self, action, payload, cancelled):
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
                if preparation.last_unresolved is None:
                    self.invalidate("map-closed")
                    return self._receipt(action, payload, success=False, reason="map-open-preparation-deadline")
                # A recognized map with unreadable floor stays available for
                # explicit manual correction; it grants no floor/pose authority.
                prepared = preparation.last_unresolved
            captured, observation, floor, capture_generation, floor_read = prepared
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
        map_detected = (observation.get("mapOpen") is True if engine.pack.mode == "difficult"
                        else observation.get("detected") is True)
        effective_floor = self._observe_floor_policy(floor_read, obsolete=obsolete) if map_detected else None
        if floor is None and map_detected:
            self._revoke_render("current-floor-unavailable")
            digest = hashlib.sha256(frame.tobytes()).hexdigest()
            with self._lock:
                if obsolete():
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                if not self._map_open:
                    self._cycle += 1
                self._map_open = True
                self._floor = effective_floor or ""
                if effective_floor is None:
                    self._floor_required = True
                self._frame_generation += 1
                self._frame_id = f"{self._round}:{self._frame_generation}:{digest[:16]}"
                self._last_observation = dict(captured=captured, frame=frame, floor=None, digest=digest,
                    capture_generation=capture_generation, frame_id=self._frame_id, observed_floor="")
                for row in self._candidate_rows:
                    row["floor"] = self._floor
            if effective_floor is not None:
                return self._solve_retained(action, payload, cancelled, acquire_current=False)
            return self._receipt(action, payload, success=False, reason="current-floor-unavailable", observation=observation)
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
            with self._lock:
                self._observed_floor = ""
                self._last_observation = None
            if action == "refresh_map":
                self._revoke_render("current-floor-unavailable")
                return self._receipt(action, payload, success=False, reason="current-floor-unavailable", observation=observation)
            self.invalidate("floor-unknown")
            return self._receipt(action, payload, success=False, reason="current-floor-unavailable", observation=observation)
        if effective_floor is None:
            self._revoke_render("floor-policy-deferred")
            return self._receipt(action, payload, success=False, reason="floor-policy-deferred")
        floor = effective_floor
        digest = hashlib.sha256(frame.tobytes()).hexdigest()
        with self._lock:
            pending_selection = self._selection_pending
        if pending_selection:
            self._revoke_render("candidate-awaiting-confirmation")
        if action == "refresh_map" and not pending_selection:
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
            self._last_observation = dict(captured=captured, frame=frame, floor=floor, digest=digest,
                capture_generation=capture_generation, frame_id=frame_id, observed_floor=self._observed_floor)
            for row in self._candidate_rows:
                row["floor"] = floor
        if pending_selection:
            return self._receipt(action, payload, success=False, reason="candidate-awaiting-confirmation")
        ranked = []
        entry_candidate = None
        if not map_id:
            ranked = engine.rank(frame, floor=floor, limit=len(engine.pack.maps), budget_seconds=1.0)
            with self._lock:
                if obsolete() or frame_id != self._frame_id:
                    return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
                self._update_candidates(ranked, floor)
            accepted = {row["mapId"] for row in ranked if row.get("identityHardGatePassed") is True}
            if len(accepted) != 1:
                return self._receipt(action, payload, success=False, reason="identity-not-uniquely-verified",
                                     candidateMapIds=[row["mapId"] for row in ranked[:8]])
            map_id = next(iter(accepted))
            entry_candidate = next((row.get("difficultEntry") for row in ranked
                if row.get("mapId") == map_id and row.get("identityHardGatePassed") is True), None)
            with self._lock:
                if obsolete() or frame_id != self._frame_id:
                    return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
                self._map_id = map_id
                self._gate.select(map_id)
                self._gate.confirm()
        return self._solve_observation(action, payload, cancelled, engine, group, epoch, captured, frame,
            floor, digest, capture_generation, frame_generation, frame_id, cycle, map_id, entry_candidate)

    def _solve_observation(self, action, payload, cancelled, engine, group, epoch, captured, frame,
                           floor, digest, capture_generation, frame_generation, frame_id, cycle,
                           map_id, entry_candidate=None):
        import numpy as np
        from alignment_core_v2 import AlignmentCandidate
        from alignment_observation import rendered_profile_residual_is_verified
        from round_layer_group import RoundLayerGroup
        obsolete = lambda: self._cancelled(cancelled) or epoch != self._epoch
        width, height = captured.context.full_client_size
        with self._lock:
            display_generation = getattr(self, "_display_generation", 0)
            variant_id = self._settings.get("zhan10CoffinVariantId", "variant-01")
        # Static reference follows the independently confirmed map and floor;
        # a failed pose solve must not also remove the player's reference map.
        display = self._display_layer(engine, map_id, floor, self._profile, variant_id=variant_id)
        reference_pixels = np.ascontiguousarray(display["pixels"][:, :, [2, 1, 0, 3]])
        reference_pixels.setflags(write=False)
        with self._lock:
            if (obsolete() or frame_id != self._frame_id or floor != self._floor
                    or display_generation != getattr(self, "_display_generation", 0)):
                return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
            if not self._capture_is_current(captured.context, capture_generation):
                self.invalidate("capture-context-changed")
                return self._receipt(action, payload, success=False, reason="capture-context-changed", stale=True)
            self._reference_sheet = dict(token=uuid4().hex, pixels=reference_pixels,
                mode=self._mode, profileId=self._profile, mapId=map_id, floor=floor, roundId=self._round,
                variantId=display.get("variantId"), displayGeneration=display_generation,
                resourceIdentity=display["resourceIdentity"], captureGeneration=capture_generation,
                referenceAuthorized=self._evidence_kind == "live-client-capture", evidenceKind=self._evidence_kind)
            self._reference_capture_context = captured.context
        host = _AlignmentHost(engine, group, self._settings)
        fit = host.fit(frame, map_id, floor, obsolete, entry_candidate) if entry_candidate is not None else host.fit(frame, map_id, floor, obsolete)
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
        render_matrix = source_matrix @ display["matrixDisplayToReference"]
        with self._lock:
            if display_generation != getattr(self, "_display_generation", 0):
                return self._receipt(action, payload, success=False, reason="stale-display-settings", stale=True)
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
                poseReason=fit.get("reason"), poseStrategy=fit.get("strategy", "not-recorded"),
                floorSource="manual" if self._manual_floor else "automatic", observedFloor=self._observed_floor,
                resourceIdentity=display["resourceIdentity"], displayKind=display["displayKind"],
                referencePath=display.get("referencePath", ""),
                displayLabel=display["displayLabel"], routeProfileId=display["routeProfileId"],
                variantId=display.get("variantId"), displayGeneration=display_generation,
                liveAuthorization=live, evidenceKind=self._evidence_kind)
            self._render_capture_context = captured.context
            render_token = self._render["token"]
        self._notify("render-ready", reason="current-frame-pose-authorized", token=render_token, unchanged=False)
        return self._receipt(action, payload, success=True, reason="current-frame-pose-authorized",
                             renderToken=render_token, diagnosticOnly=not live,
                             poseReason=fit.get("reason"), poseStrategy=fit.get("strategy", "not-recorded"))

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
            return result, floor, dict(result, selectedFloor=int(floor) if floor in ("1", "2") else None,
                                       scores={}, layout="difficult-two-buttons")
        from map_interface_observation import detect_map_interface, detect_selected_floor
        floor_read = detect_selected_floor(frame)
        result = detect_map_interface(frame, floor_read=floor_read)
        floor = floor_read.get("selectedFloor")
        floor = "B1" if floor == -1 else str(floor) if floor in (1, 2) else None
        return result, floor, floor_read

    @staticmethod
    def _display_layer(engine, map_id, floor, profile, *, variant_id="variant-01"):
        import numpy as np
        reference = engine.qt_shared_provider("shared-structure", map_id, floor)
        if reference is None:
            raise ValueError("display-reference-unavailable")
        custom = getattr(engine, "qt_custom_display", None)
        if callable(custom):
            layer = custom(profile, map_id, str(floor), reference)
            if layer is not None:
                return layer
        optional = engine.qt_optional_displays
        from client_route_preferences import selected_coffin_binding
        binding = (selected_coffin_binding(engine, map_id, floor, profile, variant_id)
                   if (profile, map_id, str(floor)) == ("zhan10-coffin", "nightmare-01", "1")
                   else optional.binding(profile, map_id, floor))
        if binding is not None and binding["usable"]:
            if binding.get("optionalPixels", True):
                pixels = optional.pixels(binding["path"], "aligned-overlay")
            else:
                import cv2
                from nightmare_candidate import imread_unicode
                pixels = imread_unicode(binding["path"], cv2.IMREAD_UNCHANGED)
                if (pixels is None or pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 4):
                    raise ValueError("coffin-variant-original-BGRA-unavailable")
            source_basis = engine.qt_adapters.matrix_profile_to_canonical("shared-structure", map_id, floor)
            if source_basis is None:
                raise ValueError("display-reference-bridge-unavailable")
            return dict(pixels=pixels, matrixDisplayToReference=np.linalg.inv(source_basis) @ binding["matrix"],
                resourceIdentity=str(binding["path"]), displayKind="author-route", displayLabel=profile,
                referencePath=str(binding["path"]),
                routeProfileId=profile, variantId=variant_id if profile == "zhan10-coffin" else None)
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
