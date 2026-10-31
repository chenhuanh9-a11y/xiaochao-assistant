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
import traceback
from uuid import uuid4

from client_paths import APP_ROOT, WORK_ROOT
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from alignment_core_v2 import AlignmentCoreV2
from client_engine import (EngineAdapter, clone_similarity_state, stage_similarity_rank, automatic_identity_candidates, registered_logical_family, ranked_entrance_kind, similarity_evidence_summary, initial_group_display_member, is_registered_identity_group,
                           INITIAL_IDENTITY_BUDGET_SECONDS)
from floor_observation_policy import (MANUAL_FLOOR_HOLD_SECONDS,
                                      read_floor_evidence, decide_floor_transition, game_floor_control_hit)


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
        captured = self.runtime._capture_frame(cancelled)
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
    def __init__(self, engine, group=None, settings=None, *, steady=None):
        self.engine, self.pack = engine, engine.pack
        self.author_coordinate_adapters = engine.qt_adapters
        self.group = group
        self.current_alignment_candidate = None
        self.settings = dict(settings or {})
        self.events = []
        self.steady = steady

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

    def _alignment_v2_independent_member_fit(self, frame, route, map_id, floor, profile, **kwargs):
        from alignment_structure_dispatch import independent_member_fit
        return independent_member_fit(self, frame, route, map_id, floor, profile, **kwargs)

    def _reverify_entry(self, frame, map_id, floor, reference, candidate, cancelled, context=None):
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
            checked = dict(success=False, reason="side-entry-pose-ambiguous" if accepted else "side-entry-pose-unverified")
            if bool(self.settings.get("poseIndexCandidatesEnabled", self.settings.get("difficultGridEnabled", False))):
                indexed = self._alignment_v2_independent_member_fit(frame, reference.clean_mask,
                    map_id, floor, "shared-structure", cancelled=cancelled, pose_index_only=True, context=context)
                if indexed.get("reason") != "pose-index-no-candidates":
                    return dict(indexed, entryFallback="confirmed-map-floor-pose-index", difficultEntryValidation=checked)
            return dict(success=False, reason="difficult-entry-current-frame-rejected", difficultEntryValidation=checked,
                entryReverification=dict(candidateCount=len(candidates), acceptedPoseCount=len(accepted), reason=checked["reason"]))
        return None

    def fit(self, frame, map_id, floor, cancelled, entry_candidate=None):
        from alignment_observation import rendered_profile_residual_is_verified
        from fast_structure_alignment import try_alignment
        from single_map_structure import current_geometry, solve, solve_initial_structure
        self.map_id, self.floor = map_id, str(floor)
        reference = self._shared_structure_reference("shared-structure", map_id, floor)
        if reference is None:
            return dict(success=False, reason="shared-reference-unavailable")
        # Original structure-fit order: default 0059 prior before entry/fast.
        if self.steady is not None:
            adapter, context, current, signature = self.steady
            steady = adapter.try_alignment(self, frame, map_id, floor, context=context,
                is_current=current, cancelled=cancelled, display_signature=signature,
                enabled=bool(self.settings.get("idvb0059Enabled", True)))
            if steady is not None:
                return steady
        # Original caller order: current fast proposal, established relation
        # recovery (including an unverified target), bounded entry, then initial.
        context = current_geometry(frame)
        fit = try_alignment(self, frame, map_id, floor, cancelled=cancelled, entry_seed=entry_candidate, context=context)
        strategy = "shared-fast-alignment"
        identity_profile = (self.engine.identity_profile_for_map(map_id)
            if callable(getattr(self.engine, "identity_profile_for_map", None))
            else getattr(self.engine, "identity_profile_id", None))
        entry_first = (identity_profile == "shared-structure"
            and callable(getattr(getattr(self.engine, "side_entry_identity", None), "rank_entry", None)))
        def recover_existing_group():
            if self.group is not None and self.group.verified_floors and str(floor) in self.group.relations:
                recovery = solve(self, frame, map_id, floor, "shared-structure", reference.clean_mask,
                                 cancelled=cancelled, _context=context)
                if (recovery.get("success") or recovery.get("reason") != "structure-current-geometry-failed"
                        or recovery.get("sharedProjectionGeometryInsufficient")):
                    return recovery
            return None
        if fit is None and (not entry_first or (self.group is not None and str(floor) not in self.group.verified_floors)):
            fit = recover_existing_group()
            strategy = "shared-established-group-recovery"
        if fit is None:
            fit = self._reverify_entry(frame, map_id, floor, reference, entry_candidate, cancelled, context=context)
            strategy = "verified-entry-current-frame"
        if fit is None and entry_first:
            fit = recover_existing_group()
            strategy = "shared-established-group-recovery"
        if fit is None:
            fit = self._alignment_v2_independent_member_fit(frame, reference.clean_mask, map_id, floor,
                "shared-structure", cancelled=cancelled, context=context)
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
        self._round_preparing = False
        self._prepared_round_ready = False
        self._last_map_input_sequence = 0
        self._transient_drag = self._transient_cycle = None
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
        from client_idvb0059_adapter import Idvb0059Adapter
        self._steady = Idvb0059Adapter()
        self._reset_identity()
        self._reset_floor_policy()
        self._message = "Bind a full client capture, then begin a round explicitly."

    @staticmethod
    def _cancelled(cancelled):
        return EngineAdapter._cancelled(cancelled)

    def _context(self):
        return dict(mode=self._mode, profileId=self._profile, roundId=self._round, frameId=self._frame_id)

    def _clear_candidates(self):
        self._reset_identity()
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

    def _reset_identity(self):
        self._transient_drag = self._transient_cycle = None
        if hasattr(self, "_steady"):
            self._steady.reset()
        self._steady_material_ready = False
        self._remember_proposals = {}
        self._pending_sibling = None
        self._manual_group_choice = None
        self._group_member_preference = None
        self._desired_group_member = None
        self._group_member_choice_status = ""
        self._automatic_candidate_rows = []
        self._group_intent_generation = getattr(self, "_group_intent_generation", 0) + 1
        self._group_choice_inflight = None
        self._group_choice_reason = ""
        self._identity_native = None
        self._provisional_reference_context = None
        self._identity_prior = self._identity_priority = None
        self._durable_map_id = ""
        self._identity_source = ""
        self._identity_rank_rows = []
        self._identity_review_last = None
        self._native_group_conflicts = None
        self._pose_attempt = dict(status="pending", reason="identity-pending")
        self._pose_result = dict(status="pending", reason="identity-pending")

    def _steady_context(self):
        return (self._epoch, self._mode, self._profile, self._round, self._cycle, self._frame_id,
                self._frame_generation, self._map_id, str(self._floor), id(self._engine), self._capture_generation())

    def _steady_is_current(self, context, captured=None):
        with self._lock:
            return bool(context == self._steady_context() and self._active and self._map_open
                and not self._transient_cycle_current()
                and (captured is None or self._capture_is_current(captured.context, context[-1])))

    @staticmethod
    def _steady_signature(captured):
        context = captured.context
        return (tuple(context.full_client_size), tuple(context.client_screen_bounds), context.dpi)

    def _register_material_proposal(self, render, frame, fit, captured, group, *, accepted=False):
        # Worker only. Capture frames are already owned immutable BGR arrays.
        token = render["token"]
        self._remember_proposals[token] = dict(context=self._steady_context(), frame=frame,
            fit=fit, captured=captured, group=group, mapId=render["mapId"], floor=render["floor"], accepted=accepted)
        while len(self._remember_proposals) > 3:
            self._remember_proposals.pop(next(iter(self._remember_proposals)))

    def acknowledge_render_material(self, token):
        """Accept a current material receipt; coverage work remains off the UI."""
        with self._lock:
            proposal = self._remember_proposals.get(token)
            if (proposal is None or proposal["accepted"] or self._render is None
                    or self._render["token"] != token or not self._display_current(self._render) or self._render.get("transient")
                    or proposal["context"] != self._steady_context() or proposal["group"] is not self._group):
                return dict(success=False, reason="material-token-expired-or-already-accepted", token=token)
            proposal["accepted"] = True
        self._notify("render-material-accepted", token=token, reason="current-material-accepted")
        return dict(success=True, reason="material-remember-queued", token=token, needsWorker=True)

    def _remember_render_material(self, token, payload, cancelled):
        with self._lock:
            proposal = self._remember_proposals.get(token)
            if (proposal is None or not proposal["accepted"] or proposal.get("consumed")
                    or proposal["group"] is not self._group
                    or not self._steady_is_current(proposal["context"], proposal["captured"])):
                return self._receipt("remember_render_material", payload, success=False, reason="material-context-expired")
            proposal["consumed"] = True
            engine, group, settings = self._engine, self._group, dict(self._settings)
        current = lambda context: not self._cancelled(cancelled) and self._steady_is_current(context, proposal["captured"])
        remembered = self._steady.remember_committed(_AlignmentHost(engine, group, settings),
            proposal["frame"], proposal["mapId"], proposal["floor"], proposal["fit"],
            context=proposal["context"], is_current=current, material_accepted=True,
            display_signature=self._steady_signature(proposal["captured"]),
            enabled=bool(settings.get("idvb0059Enabled", True)))
        with self._lock:
            if remembered and current(proposal["context"]):
                self._steady_material_ready = True
        return self._receipt("remember_render_material", payload, success=bool(remembered),
                             reason="material-prior-remembered" if remembered else "material-prior-not-remembered")

    def _consume_identity_conflict(self, fit, *, engine, epoch, captured,
                                   capture_generation, frame_id, frame_generation, cycle, map_id, floor):
        """Original failure consumer on a worker-owned NativeState clone only."""
        from types import SimpleNamespace
        from native_similarity_groups import native_similarity_strong_conflict
        with self._lock:
            if (epoch != self._epoch or engine is not self._engine or frame_id != self._frame_id
                    or frame_generation != self._frame_generation or cycle != self._cycle
                    or map_id != self._map_id or str(floor) != str(self._floor)
                    or not self._capture_is_current(captured.context, capture_generation)):
                return False
            state = self._identity_native
            if state is None or state.state != "MEMBER_VERIFIED":
                return False
            stage = clone_similarity_state(state)
            events = []
            owner = SimpleNamespace(native_similarity_state=stage, gate=self._gate,
                active_map_cycle_id=cycle, current_frame_generation=frame_generation,
                current_floor=floor, surface_visibility_generation=epoch,
                floor_required=self._floor_required, map_is_open=self._map_open,
                _native_group_conflicts=copy.deepcopy(self._native_group_conflicts),
                _log=lambda event, **values: events.append((event, values)))
            reopened = native_similarity_strong_conflict(owner, dict(mapId=map_id, floor=floor,
                cycleId=cycle, sourceFrameGeneration=frame_generation, sourceSurfaceGeneration=epoch,
                fit=fit, frontGroupDifferenceEvidence=fit.get("frontGroupDifferenceEvidence")))
            self._native_group_conflicts = owner._native_group_conflicts
            if reopened:
                self._identity_native = stage
                self._durable_map_id = ""
                self._identity_source = "native-similar-group"
                self._identity_priority = None
                self._identity_review_last = None
                self._pending_sibling = None
                self._provisional_reference_context = None
                self._notify("group-state", reason="repeated-current-structure-contradictions",
                             legacyEvents=[dict(event=event, **values) for event, values in events])
            return reopened

    def _identity_fields(self):
        native = self._identity_native
        grouped = is_registered_identity_group(native)
        held = self._manual_group_choice is not None
        phase = ("MANUAL_MEMBER_CONFIRMED" if held else "MEMBER_VERIFIED" if self._durable_map_id
                 else "GROUP_LOCKED" if grouped else "UNLOCKED")
        return dict(identityPhase=phase, identitySource=self._identity_source,
            identityLocked=bool(self._durable_map_id), durableMapId=self._durable_map_id,
            provisionalMapId=(native.provisional_member_id or self._map_id) if grouped else "",
            provisional=grouped and not bool(self._durable_map_id), groupLocked=grouped,
            groupId=native.group_id if grouped else None,
            groupMemberIds=list(native.member_ids) if grouped else [],
            groupGeneration=native.generation if grouped else 0,
            currentGroupMemberId=self._map_id,
            groupMemberRows=[dict(mapId=mid, name=str(getattr(self._engine.pack.by_id.get(mid), "name", mid)))
                for mid in native.member_ids] if grouped and self._engine is not None else [],
            manualGroupMemberHeld=held, manualGroupMemberId=self._manual_group_choice[3] if held else "",
            groupMemberSelectionPending=self._group_choice_inflight is not None,
            pendingGroupMemberId=self._group_choice_inflight[1] if self._group_choice_inflight else "",
            groupMemberSelectionReason=self._group_choice_reason,
            desiredGroupMemberId=self._desired_group_member[3] if self._desired_group_member else "",
            groupMemberChoiceStatus=self._group_member_choice_status)

    def _identity_context(self):
        return (self._mode, self._cycle, str(self._floor), self._frame_generation, self._epoch)

    def _attach_identity_engine(self, engine):
        from client_providers import bind_identity_context
        def current(field):
            with self._lock:
                return getattr(self, field) if engine is self._engine else None
        bind_identity_context(engine, priority=lambda: current("_identity_priority"),
                              context=lambda: current("_identity_prior"))

    @staticmethod
    def _current_entry_seeds(rows, floor):
        import numpy as np
        seeds = {}
        for row in rows:
            entry = row.get("difficultEntry") or {}
            checked = entry.get("verification") or {}
            if (entry.get("mapId") != row.get("mapId") or str(entry.get("floor", floor)) != str(floor)
                    or checked.get("success") is not True or entry.get("poseAmbiguous")
                    or (entry.get("entryCrossCheck") or {}).get("contradicted") is True):
                continue
            try:
                matrix = np.asarray(checked.get("matrixSourceToClient"), dtype=float)
                if matrix.shape == (3, 3) and np.isfinite(matrix).all():
                    seeds[row["mapId"]] = entry
            except (ValueError, TypeError):
                pass
        return seeds

    def _identity_observation_current(self):
        observation = self._last_observation
        return bool(observation is not None and observation["frame_id"] == self._frame_id
            and self._capture_is_current(observation["captured"].context, observation["capture_generation"]))

    def _publish_identity_stage(self, state, rows, context, *, reason):
        if context != self._identity_context() or not self._active or not self._identity_observation_current():
            return False
        self._identity_native = state
        self._identity_rank_rows = rows
        self._identity_prior = state.context_snapshot()
        self._identity_priority = (None if state.condition_reason=="registered-logical-family"
                                   else state.priority(self._mode))
        self._identity_source = "group-default" if is_registered_identity_group(state) else "native-similarity"
        return True

    def _rank_identity(self, engine, frame, floor, epoch, frame_id, cancelled, *, rediscover=False):
        with self._lock:
            previous, prior = self._identity_native, self._identity_prior
            context = self._identity_context()
            intent_generation = self._group_intent_generation
        review = getattr(engine, "rank_established_group", None)
        rows = (review(frame, floor, prior=prior, budget_seconds=INITIAL_IDENTITY_BUDGET_SECONDS)
                if prior and callable(review) and not rediscover else engine.rank(frame, floor=floor,
                    limit=len(engine.pack.maps), budget_seconds=INITIAL_IDENTITY_BUDGET_SECONDS))
        seeds = self._current_entry_seeds(rows, floor)
        state, proposal = stage_similarity_rank(None if rediscover else previous, rows, context,
            hashlib.sha256(frame.tobytes()).hexdigest(), seeds, group_only=True)
        group_evidence = similarity_evidence_summary(proposal, state)
        accepted = [row for row in rows if row.get("identityHardGatePassed") is True]
        top=rows[0] if rows else None
        block_member_unique=False
        unresolved_candidates=[]
        native_group=bool(len(state.member_ids)>1 and state.state=="GROUP_LOCKED")
        hard_top=bool(len(accepted)==1 and top is accepted[0] and top.get("strongSuggestion") is True)
        entrance_kind=ranked_entrance_kind(engine, rows, floor)
        if entrance_kind == "front" and (native_group or hard_top):
            family=registered_logical_family(engine,top["mapId"] if hard_top else state.provisional_member_id,floor,
                                             entrance_kind=entrance_kind,
                                             required_members=state.member_ids if native_group and not hard_top else ())
            block_member_unique=family["status"]!="none"
            if family["status"]=="group":
                declared=family["candidates"][0]
                provisional=state.provisional_member_id
                state.reset()
                state.state="GROUP_LOCKED";state.group_id=declared["groupId"]
                state.resource_key=declared["resourceKey"];state.member_ids=tuple(declared["memberIds"])
                state.provisional_member_id=provisional
                state.floor=str(floor);state.provisional_member_id=initial_group_display_member(state,seeds)
                state.condition_reason="registered-logical-family"
                proposal=dict(status="logical-group",reason=state.condition_reason)
            elif block_member_unique:
                state.reset()
                proposal=dict(status="logical-group-pending",reason="registered-family-"+family["status"])
                unresolved_candidates=[dict(key=row["groupId"],mapId="",identityKind="group",name="待确认的相似地图组",**row)
                                       for row in family.get("candidates",())]
        with self._lock:
            if (self._cancelled(cancelled) or epoch != self._epoch or frame_id != self._frame_id
                    or intent_generation != self._group_intent_generation or self._group_choice_inflight is not None):
                return None, None, "stale-context"
            if not self._identity_observation_current():
                self.invalidate("capture-context-changed")
                return None, None, "capture-context-changed"
            self._update_candidates(rows, floor)
            registered_group=is_registered_identity_group(state)
            self._automatic_candidate_rows=(unresolved_candidates if block_member_unique and not registered_group
                                            else automatic_identity_candidates(rows,state if registered_group else None))
            group_key=(self._round,self._mode,frozenset(state.member_ids))
            preference=self._group_member_preference
            previous_members = frozenset(previous.member_ids) if is_registered_identity_group(previous) else frozenset()
            same_group = bool(registered_group
                              and previous_members == frozenset(state.member_ids))
            changed_group = bool(previous_members and registered_group
                                 and previous_members != frozenset(state.member_ids))
            if changed_group:
                # A fresh, validated different family invalidates every old
                # member transaction. A mere pose failure never reaches here.
                self._discard_sibling()
                self._group_intent_generation += 1
                self._group_choice_inflight = None
                self._manual_group_choice = self._desired_group_member = None
                self._group_member_choice_status = self._group_choice_reason = ""
                self._group_member_preference = preference = None
                self._group = None
                self._core.reset_round(reason="logical-group-changed")
                self._steady.reset()
                self._steady_material_ready = False
                self._remember_proposals = {}
                self._reference_sheet = self._reference_capture_context = None
                self._provisional_reference_context = None
                self._durable_map_id = ""
            if (registered_group
                    and preference is not None and preference[:3]==group_key
                    and preference[3] in state.member_ids):
                # A remembered choice is not recognition/display authority.
                # Only a newly observed real group with the same members can
                # reuse it, including after none evidence or floor index churn.
                state.verified_member_id = state.pending_identity = None
                state.provisional_member_id = preference[3]
            grouped = registered_group
            if grouped and len(state.member_ids)>1:
                if self._desired_group_member and self._desired_group_member[:3]!=group_key:
                    self._desired_group_member=None
                    self._group_member_choice_status=""
            self._publish_identity_stage(state, rows, context, reason="ranked-current-frame")
            if same_group and self._manual_group_choice is not None:
                self._manual_group_choice = (self._round, state.group_id, state.resource_key, state.provisional_member_id)
                self._identity_source = "manual-group-member"
            if (not grouped and is_registered_identity_group(previous)
                    and (rediscover or not self._durable_map_id)):
                # A reset state no longer owns an automatic provisional choice.
                # Keep warm geometric cache separate from withdrawn identity.
                self._map_id = ""
                self._durable_map_id = ""
                self._gate.select(None)
                self._group = None
                self._discard_sibling()
                self._group_intent_generation += 1
                self._group_choice_inflight = self._manual_group_choice = None
                if self._desired_group_member:
                    self._group_member_choice_status = "awaiting-geometry"
                self._reference_sheet = self._reference_capture_context = None
                self._provisional_reference_context = None
            selected = (state.provisional_member_id if grouped and (state.context_snapshot()
                or state.condition_reason=="registered-logical-family") and state.provisional_member_id in state.member_ids else None)
            top = rows[0] if rows else None
            # Tk first consumes the optional native group, then independently
            # tests the final top-row auto-lock predicate. A hard unique native
            # proposal may have no established condition bindings/member list.
            from native_similarity_groups import should_auto_lock_candidate
            if selected is None and not block_member_unique and not grouped and len(accepted) == 1 and should_auto_lock_candidate(
                    top, self._floor_required, self._gate.confirmed_id):
                selected = top["mapId"]
                front_provisional = bool(top.get("frontDoorGroupState") == "GROUP_LOCKED"
                    and top.get("frontDoorProvisionalMemberId") == selected)
                if not front_provisional:
                    self._durable_map_id = selected
                    self._identity_source = "automatic-hard-gate"
            if selected:
                if grouped and len(state.member_ids)>1:
                    self._group_member_preference=(*group_key,selected)
                elif self._group_member_preference and selected not in self._group_member_preference[2]:
                    self._group_member_preference=None
                    self._desired_group_member=None
                    self._group_member_choice_status=self._group_choice_reason=""
                self._map_id = selected
                self._gate.select(selected)
                self._gate.confirm()
        self._notify("group-state", reason=proposal.get("reason", proposal.get("status", "ranked")),
            identity=self._identity_fields(), groupEvidence=group_evidence, rankVerdict=dict(
                topMapId=top.get("mapId") if top else None,
                topStrongSuggestion=top.get("strongSuggestion") if top else None,
                topIdentityHardGatePassed=top.get("identityHardGatePassed") if top else None,
                frontDoorGroupState=top.get("frontDoorGroupState") if top else None,
                frontDoorProvisionalMemberId=top.get("frontDoorProvisionalMemberId") if top else None,
                acceptedMapIds=[row.get("mapId") for row in accepted][:32],
                nativeStatus=proposal.get("status"), nativeCandidateMemberId=proposal.get("candidateMemberId"),
                establishedMemberIds=list(state.member_ids)[:32], selectedMapId=selected,
                entranceKind=entrance_kind))
        seed = seeds.get(selected)
        if seed is None and len(accepted) == 1 and accepted[0].get("mapId") == selected:
            seed = accepted[0].get("difficultEntry")
        return selected, seed, False

    def _stage_current_member_material(self, action, payload, state, *, manual_choice=None):
        with self._lock:
            if self._render is None or not self._display_current(self._render):
                return self._receipt(action, payload, success=False, reason="sibling-source-authorization-expired", stale=True)
            self._discard_sibling()
            source = self._render
            target = dict(source, token=uuid4().hex, requiresMaterialAck=True,
                sourceToken=source["token"], sourceMapId=self._map_id)
            self._group_member_choice_status="awaiting-material" if manual_choice is not None else self._group_member_choice_status
            self._pending_sibling = dict(scope=self._sibling_scope(), sourceCore=self._core,
                sourceToken=source["token"], sourceMapId=self._map_id,
                core=self._copy_pose_core(self._core), group=self._group, identity=state,
                render=target, reference=dict(self._reference_sheet), captureContext=self._render_capture_context,
                manualChoice=manual_choice)
            source_proposal = self._remember_proposals.get(source["token"])
            if source_proposal is not None:
                self._remember_proposals[target["token"]] = dict(source_proposal, accepted=False, consumed=False)
        self._notify("sibling-material-pending", reason="current-member-awaiting-material", token=target["token"],
            sourceMapId=source["mapId"], targetMapId=target["mapId"])
        return self._receipt(action, payload, success=True, reason="sibling-material-pending",
            renderToken=source["token"], pendingSiblingToken=target["token"],
            diagnosticOnly=self._evidence_kind != "live-client-capture")

    def _sibling_scope(self):
        return (self._epoch, self._mode, self._profile, self._round, self._cycle,
            self._frame_id, self._frame_generation, str(self._floor),
            getattr(self, "_display_generation", 0), id(self._engine))

    @staticmethod
    def _copy_pose_core(core):
        staged = copy.copy(core)
        for key, value in vars(core).items():
            if key not in ("_clock", "_event_sink"):
                setattr(staged, key, copy.deepcopy(value))
        return staged

    def _pending_sibling_current(self, pending):
        row = pending["render"]
        common = bool(self._active and self._map_open and not self._selection_pending
            and not self._floor_required and pending["scope"] == self._sibling_scope()
            and self._core is pending["sourceCore"] and self._map_id == pending["sourceMapId"]
            and self._capture_is_current(pending["captureContext"], row["captureGeneration"])
            and pending["core"].can_display(cycle_id=self._cycle, map_id=row["mapId"],
                floor=self._floor, frame_generation=self._frame_generation))
        if not common:
            return False
        if pending.get("projection"):
            return bool(self._transient_cycle_current() and pending["group"] is self._group)
        plan = pending.get("manualChoice")
        if plan is not None:
            from client_group_member_choice import group_member_choice_is_current
            # Pending is our own transaction, not a second external selection.
            state = self.snapshot(); state["groupMemberSelectionPending"] = False
            return bool(self._desired_group_member and self._desired_group_member[3]==plan.map_id
                        and group_member_choice_is_current(plan, state))
        return bool(self._render is not None and self._render["token"] == pending["sourceToken"]
            and self._gate.confirmed_id == pending["sourceMapId"] and self._display_current(self._render))

    def _discard_sibling(self):
        pending = self._pending_sibling
        self._pending_sibling = None
        if pending is not None and pending.get("manualChoice") is not None:
            self._group_choice_inflight = None
            if self._desired_group_member and self._group_member_choice_status!="confirmed":
                self._group_member_choice_status="awaiting-geometry"
        if pending is not None:
            # A rejected target's authorization must never be recycled.
            for name in ("_next_observation_id", "_next_authorization_id"):
                setattr(pending["sourceCore"], name, max(getattr(pending["sourceCore"], name),
                    getattr(pending["core"], name)))
        return pending

    def pending_sibling_payload(self, token):
        """Target material only; source remains the sole published pose owner."""
        import numpy as np
        with self._lock:
            pending = self._pending_sibling
            if pending is None or pending["render"]["token"] != token or not self._pending_sibling_current(pending):
                return None
            row = pending["render"]
            matrix = np.asarray(row["matrix"], dtype=float)
            if (matrix.shape != (3, 3) or not np.isfinite(matrix).all()
                    or (row["clientWidth"], row["clientHeight"]) != pending["captureContext"].full_client_size):
                return None
            return {key: value if key == "pixels" else copy.deepcopy(value) for key, value in row.items()}

    def _acknowledge_projection(self, token, accepted, reason):
        with self._lock:
            pending=self._pending_sibling
            if pending is None or not pending.get("projection") or pending["render"]["token"]!=token:
                return dict(success=False,reason="projection-token-expired",token=token)
            committed=bool(accepted is True and self._pending_sibling_current(pending))
            if committed:
                self._core.revoke_display(reason="shared-root-projection-material-accepted")
                self._core=pending["core"]
                self._render,self._reference_sheet=pending["render"],pending["reference"]
                self._render["requiresMaterialAck"]=False
                self._render_capture_context=self._reference_capture_context=pending["captureContext"]
                self._transient_cycle["current"]=pending["candidate"]
                self._pending_sibling=None
                self._remember_provisional_reference()
            else:
                self._discard_sibling()
            retained=bool(not committed and self._render is not None and self._display_current(self._render))
        outcome="shared-root-provisional-material-accepted" if committed else "shared-root-projection-material-rejected"
        self._notify("sibling-committed" if committed else "sibling-rolled-back",token=token,reason=outcome,
                     materialReason=str(reason)[:256],sourceRetained=retained)
        if committed:self._notify("render-ready",token=token,reason=outcome,unchanged=False)
        return dict(success=committed,reason=outcome,token=token,sourceRetained=retained,projectionOnly=True)

    def acknowledge_sibling(self, token, accepted, reason=""):
        """Commit original sibling identity only after Image.Ready acceptance.

        Pure bounded state work: callable on the Qt owner, with no fit/capture,
        disk IO or dependency on overlay visibility. Clone reowns the identity
        transaction on this caller without mutating the published source.
        """
        with self._lock:
            pending = self._pending_sibling
            if pending is not None and pending.get("projection") and pending["render"]["token"]==token:
                return self._acknowledge_projection(token,accepted,reason)
            if pending is None or pending["render"]["token"] != token:
                return dict(success=False, reason="sibling-token-expired", token=token)
            valid = self._pending_sibling_current(pending)
            committed = False
            failure = "sibling-context-expired" if not valid else "sibling-material-rejected"
            if valid and accepted is True:
                state = clone_similarity_state(pending["identity"])
                target = pending["render"]["mapId"]
                gate = copy.copy(self._gate)
                gate.select(target)
                try:
                    committed = bool(gate.confirm() and (pending.get("manualChoice") is not None
                        or state.confirm(target, self._floor, self._identity_context())))
                except Exception:
                    committed = False
                if not committed:
                    failure = "sibling-identity-commit-failed"
                else:
                    state.provisional_member_id = target
                    if pending.get("manualChoice") is not None:
                        state.state = "GROUP_LOCKED"
                        state.verified_member_id = state.pending_identity = None
                        state.generation += 1
                        self._manual_group_choice = (self._round, state.group_id, state.resource_key, target)
                        self._group_member_preference=(self._round,self._mode,frozenset(state.member_ids),target)
                        self._group_member_choice_status="confirmed"
                        self._identity_source = "manual-group-member"
                        self._group_choice_reason = "本局保持手动选择"
                    self._core.revoke_display(reason="native-sibling-committed")
                    self._core, self._group, self._gate = pending["core"], pending["group"], gate
                    self._map_id = self._durable_map_id = target
                    self._identity_native, self._identity_priority = state, None
                    self._render, self._reference_sheet = pending["render"], pending["reference"]
                    metadata = self._identity_fields()
                    self._render.update(metadata, requiresMaterialAck=False)
                    self._reference_sheet.update(metadata)
                    if pending.get("manualChoice") is not None:
                        self._provisional_reference_context = self._reference_owner_context(self._reference_sheet)
                    self._render_capture_context = self._reference_capture_context = pending["captureContext"]
                    proposal = self._remember_proposals.get(token)
                    if proposal is not None:
                        proposal["context"] = self._steady_context()
                        proposal["group"] = self._group
                    self._pending_sibling = None
                    if pending.get("manualChoice") is not None:
                        self._group_choice_inflight = None
            if not committed:
                if pending.get("manualChoice") is not None and self._desired_group_member:
                    self._group_member_choice_status="awaiting-geometry"
                    self._group_choice_reason = "已选择地图，等待显示资源就绪；当前显示尚未切换"
                self._discard_sibling()
            source_retained = bool(not committed and self._render is not None and self._display_current(self._render)
                and self._capture_is_current(self._render_capture_context, self._render.get("captureGeneration")))
            result = dict(success=committed, reason="sibling-material-and-identity-committed" if committed else failure,
                token=token, sourceRetained=source_retained)
        self._notify("sibling-committed" if committed else "sibling-rolled-back", token=token,
            reason=result["reason"], materialReason=str(reason)[:256], sourceRetained=source_retained)
        if committed:
            self.acknowledge_render_material(token)
            self._notify("identity-committed", reason=("manual-group-member-material-verified"
                if self._manual_group_choice is not None else "original-identity-and-current-material-verified"), identity=self._identity_fields())
            self._notify("render-ready", token=token, reason="sibling-material-and-identity-committed", unchanged=False)
        return result

    def _reset_provisional_floor(self, floor):
        if self._identity_native is not None and not self._durable_map_id and str(self._floor) != str(floor):
            # A floor-specific index/generation is not another logical group.
            # Retain its member; discard floor-bound work and display authority.
            self._discard_sibling()
            self._group_intent_generation += 1
            self._group_choice_inflight = None
            self._reference_sheet = self._reference_capture_context = None
            self._provisional_reference_context = None

    def _set_pose_phase(self, status, reason, map_id, floor, frame_id, generation, **extra):
        with self._lock:
            if not self._active or frame_id != self._frame_id:
                return
            phase = dict(status=status, reason=reason, mapId=map_id, floor=floor,
                         frameId=frame_id, generation=generation, **extra)
            if status == "pending":
                self._pose_attempt = phase
            self._pose_result = phase
        self._notify("pose-attempt" if status == "pending" else "pose-result", reason=reason, pose=phase)

    def _reset_floor_policy(self):
        self._floor_observations = []
        self._manual_override = False
        self._floor_hard_locked = False
        self._manual_hold_until = 0.0
        self._floor_required = True
        self._game_floor_intent = None
        self._floor_click_epoch_span = None
        self._last_floor_policy = ""

    def _game_floor_click_context_locked(self):
        from map_pointer_policy import normalize_drag_boundary
        observation = self._last_observation
        if (not self._active or not self._map_open or observation is None
                or observation["frame_id"] != self._frame_id or "floor_read" not in observation):
            return None
        captured = observation["captured"].context
        return dict(epoch=self._epoch, mode=self._mode, profileId=self._profile,
            roundId=self._round, cycleId=self._cycle, frameId=self._frame_id,
            generation=self._frame_generation, captureGeneration=observation["capture_generation"],
            clientBounds=list(captured.client_screen_bounds), fullClientSize=list(captured.full_client_size),
            dpi=captured.dpi,
            observationCreatedAt=observation.get("created_at", 0),
            dragBoundaryNormalized=normalize_drag_boundary(self._settings.get("dragBoundaryNormalized")))

    def game_floor_click_context(self):
        """Hook-safe bounded metadata only. Contention drops this click."""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            context = self._game_floor_click_context_locked()
            if context is not None:
                context["createdAt"] = self._monotonic()
            return context
        finally:
            self._lock.release()

    def _transient_cycle_current(self):
        row = self._transient_cycle
        return bool(row and self._active and self._map_open and row["scope"] ==
                    (self._mode, self._round, self._cycle, self._map_id))

    def observe_game_drag(self, event):
        """Apply only coordinator-approved physical deltas to transient pixels.

        Original calibrated motion/hit rules run in DragMotionAccumulator.
        This fast owner receipt never captures, fits, changes a pose, or learns.
        """
        import numpy as np
        if not isinstance(event, dict):
            return dict(success=False, reason="drag-event-invalid")
        phase, gesture = event.get("phase"), event.get("gestureId")
        with self._lock:
            if phase == "cancel":
                if self._transient_drag and self._transient_drag["gestureId"] == gesture:
                    self._transient_drag = None
                    return dict(success=True, reason="drag-cancelled", probeRequired=self._transient_cycle_current())
                return dict(success=False, reason="drag-gesture-expired")
            context = event.get("context")
            current = self._game_floor_click_context_locked()
            sequence = event.get("pointerSequence")
            if (phase not in ("down", "move", "up") or not isinstance(context, dict) or current is None
                    or any(context.get(key) != value for key, value in current.items())
                    or type(sequence) is not int or sequence < 1 or not gesture):
                return dict(success=False, reason="drag-context-expired")
            observation = self._last_observation
            if (self._pending_sibling is not None or self._group_choice_inflight is not None
                    or self._render is None or not self._display_current(self._render)
                    or not self._capture_is_current(observation["captured"].context, observation["capture_generation"])):
                return dict(success=False, reason="drag-current-display-unavailable")
            if phase == "down":
                created = event.get("createdAt")
                if (isinstance(created, bool) or not isinstance(created, (int,float)) or not math.isfinite(created)
                        or not 0 <= self._monotonic() - created <= MANUAL_FLOOR_HOLD_SECONDS):
                    return dict(success=False, reason="drag-press-expired")
                self._transient_drag = dict(gestureId=gesture, context=dict(current), lastSequence=sequence,
                    baseMatrix=np.asarray(self._render["matrix"], dtype=np.float64).copy(),
                    baseOffset=tuple(self._transient_cycle["offset"]) if self._transient_cycle_current() else (0.,0.),
                    moved=False)
                return dict(success=True, reason="drag-started", persistentPoseUpdated=False)
            drag = self._transient_drag
            if (drag is None or drag["gestureId"] != gesture or drag["context"] != current
                    or sequence <= drag["lastSequence"]):
                return dict(success=False, reason="drag-gesture-expired")
            try:
                total = np.asarray(event.get("totalDelta"), dtype=np.float64)
                delta = np.asarray(event.get("delta"), dtype=np.float64)
            except (ValueError, TypeError):
                return dict(success=False, reason="drag-delta-invalid")
            if total.shape != (2,) or delta.shape != (2,) or not np.isfinite([*total,*delta]).all():
                return dict(success=False, reason="drag-delta-invalid")
            previous = np.asarray(drag.get("lastTotal", (0.,0.)))
            if not np.allclose(total - previous, delta, rtol=0, atol=1e-6):
                return dict(success=False, reason="drag-approved-delta-inconsistent")
            drag["lastSequence"], drag["lastTotal"] = sequence, total.tolist()
            if np.any(delta):
                if not drag["moved"]:
                    self._group_intent_generation += 1
                drag["moved"] = True
                matrix = drag["baseMatrix"].copy()
                matrix[:2, :] += total[:,None] * matrix[2:3, :]
                if not np.isfinite(matrix).all():
                    return dict(success=False, reason="drag-matrix-invalid")
                offset = np.asarray(drag["baseOffset"]) + total
                if not self._transient_cycle_current():
                    self._transient_cycle = dict(scope=(self._mode,self._round,self._cycle,self._map_id),
                        current=dict(mapId=self._map_id, floor=self._floor, cycleId=self._cycle,
                            accepted=not self._render.get("projectionOnly",False),
                            provisional=bool(self._render.get("projectionOnly",False)),
                            projectionSourceFloor=self._render.get("projectionSourceFloor")))
                self._transient_cycle.update(offset=offset.tolist(),gestureId=gesture,pointerSequence=sequence)
                self._render = dict(self._render, matrix=matrix.tolist(),
                    transformRevision=int(self._render.get("transformRevision",0))+1, transient=True, transientOffset=offset.tolist(), persistentPoseUpdated=False)
            moved = drag["moved"]
            if phase == "up":
                if self._transient_cycle_current():
                    self._transient_cycle.update(gestureId=gesture,pointerSequence=sequence,probePending=bool(moved))
                self._transient_drag = None
            return dict(success=True, reason="drag-transient-updated" if moved else "drag-click-ignored",
                renderToken=self._render["token"], transient=moved, persistentPoseUpdated=False,
                poseCommits=0, probeRequired=bool(phase == "up" and moved))

    def _probe_map_after_drag(self, payload, cancelled):
        """Original post-mouse-up observation only; no fit or pose write."""
        with self._lock:
            if not self._transient_cycle_current() or self._transient_drag is not None:
                return self._receipt("probe_map_after_drag", payload, success=False, reason="transient-probe-unavailable")
            cycle = dict(self._transient_cycle)
            scope = self._sibling_scope()
            engine, epoch, target = self._engine, self._epoch, self._floor
            if (payload.get("gestureId") is not None and (payload["gestureId"] != cycle["gestureId"]
                    or payload.get("pointerSequence") != cycle["pointerSequence"])):
                return self._receipt("probe_map_after_drag", payload, success=False, reason="transient-probe-expired", stale=True)
            requested = payload.get("context")
            if requested is not None and (not isinstance(requested,dict) or any(requested.get(key) != value
                    for key,value in self._game_floor_click_context_locked().items())):
                return self._receipt("probe_map_after_drag", payload, success=False, reason="transient-probe-expired", stale=True)
        capture_generation = self._capture_generation()
        captured = self._capture_frame(cancelled)
        frame = self._full_frame(captured)
        obsolete = lambda: self._cancelled(cancelled) or epoch != self._epoch
        if obsolete():
            return self._receipt("probe_map_after_drag", payload, success=False, reason="cancelled-or-stale", stale=True)
        observation, floor, floor_read = self._observe(engine, frame)
        detected = observation.get("mapOpen") is True if self._mode == "difficult" else observation.get("detected") is True
        with self._lock:
            current_scope = self._sibling_scope()
            if (obsolete() or scope[:7] + scope[8:] != current_scope[:7] + current_scope[8:]
                    or not self._capture_is_current(captured.context, capture_generation)):
                return self._receipt("probe_map_after_drag", payload, success=False, reason="transient-probe-expired", stale=True)
            if not detected:
                self._probe_misses += 1
                if self._probe_misses >= 2:
                    self.invalidate("map-closed")
                return self._receipt("probe_map_after_drag", payload, success=False, reason="map-not-observed")
            self._probe_misses = 0
            self._observe_floor_policy(floor_read, obsolete=obsolete)
            if self._transient_cycle is not None:
                self._transient_cycle["probePending"]=False
            projected_floor=str(self._floor)
            transition=bool(projected_floor!=str(target) and not self._floor_read_conflicts(floor_read,projected_floor))
        if transition:
            projected=self._try_transient_floor_projection(payload,cancelled,engine,captured,frame,projected_floor,
                capture_generation,advance_frame=True,floor_read=floor_read)
            if projected is not None:return projected
        with self._lock:
            if self._floor_read_conflicts(floor_read,target):
                self._revoke_render("post-drag-floor-conflict")
                self._reference_sheet = self._reference_capture_context = None
                return self._receipt("probe_map_after_drag",payload,success=False,reason="current-frame-floor-conflict",
                    observedFloor=self._observed_floor,targetFloor=target,fitAttempted=False)
        return self._receipt("probe_map_after_drag", payload, success=True, reason="post-drag-observation-current",
                             poseCommits=0, persistentPoseUpdated=False, fitAttempted=False)

    def observe_game_floor_click(self, screen_x, screen_y, context):
        """UI ordered click intent; fresh highlighted pixels still own floor."""
        if not isinstance(context, dict):
            return dict(success=False, reason="floor-click-context-unavailable")
        with self._lock:
            current = self._game_floor_click_context_locked()
            if current is None:
                return dict(success=False, reason="floor-click-context-unavailable")
            supplied_epoch = context.get("epoch")
            span = self._floor_click_epoch_span
            own_click_epoch = bool(span is not None and span[1] == self._epoch
                and isinstance(supplied_epoch, int) and span[0] <= supplied_epoch <= span[1])
            if ((supplied_epoch != self._epoch and not own_click_epoch)
                    or any(context.get(key) != value for key, value in current.items() if key != "epoch")):
                return dict(success=False, reason="floor-click-context-stale")
            created = context.get("createdAt")
            now = self._monotonic()
            if (isinstance(created, bool) or not isinstance(created, (int, float))
                    or not math.isfinite(created) or not 0 <= now - created <= MANUAL_FLOOR_HOLD_SECONDS):
                return dict(success=False, reason="floor-click-expired")
            if (isinstance(screen_x, bool) or isinstance(screen_y, bool)
                    or not isinstance(screen_x, (int, float)) or not isinstance(screen_y, (int, float))
                    or not math.isfinite(screen_x) or not math.isfinite(screen_y)):
                return dict(success=False, reason="floor-click-coordinate-invalid")
            observation = self._last_observation
            if not self._capture_is_current(observation["captured"].context, observation["capture_generation"]):
                return dict(success=False, reason="capture-context-changed")
            try:
                clicked = game_floor_control_hit((screen_x, screen_y), current["clientBounds"], observation["floor_read"])
            except Exception:
                # Original native click producer treats malformed HUD geometry
                # as no hit; it must never mutate floor/pose from an exception.
                clicked = None
            if clicked is None:
                return dict(success=False, reason="floor-click-not-a-control")
            first_epoch = span[0] if own_click_epoch else self._epoch
            self.invalidate("game-floor-control-intent")
            self._floor_click_epoch_span = (first_epoch, self._epoch)
            self._floor_observations = []
            self._floor_required = True
            self._observe_floor_policy(observation["floor_read"], explicit_floor=clicked)
            self._game_floor_intent = dict(cycleId=self._cycle, floor=clicked,
                awaitingObservation=True, expiresAt=created + MANUAL_FLOOR_HOLD_SECONDS)
            result = dict(success=True, reason="game-floor-control-intent", clickedFloor=clicked,
                clickedFloorId="B1" if clicked == -1 else str(clicked), captureRequired=True,
                expiresAt=self._game_floor_intent["expiresAt"])
        self._notify("game-floor-control-intent", reason="awaiting-current-highlight", clickedFloor=clicked,
            expiresAt=result["expiresAt"])
        return result

    def _observe_floor_policy(self, floor_read, *, obsolete=None, explicit_floor=None):
        """Apply the shared legacy temporal/intent decision, never pose authority."""
        with self._lock:
            if obsolete is not None and obsolete():
                return None
            evidence = read_floor_evidence(floor_read, self._floor_observations, explicit_floor=explicit_floor)
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
                self._reset_provisional_floor(policy.target)
                self._floor = policy.target
                self._manual_floor = None
            if policy.reason == "pending-click-mismatch":
                return None
            return self._floor if evidence.stable is not None or self._manual_override else None

    @staticmethod
    def _floor_read_conflicts(floor_read, target):
        observed = floor_read.get("selectedFloor")
        return bool(observed in (-1, 1, 2)
            and ("B1" if observed == -1 else str(observed)) != str(target))

    def _current_frame_floor_conflicts(self, target, *, frame_id=None):
        # Exact Tk V2 boundary: only the detector bound to this cycle/frame
        # can contradict a target. Unknown/ambiguous reads grant no veto.
        observation = self._last_observation
        return bool(observation is not None
            and observation.get("frame_id") == self._frame_id
            and (frame_id is None or frame_id == self._frame_id)
            and observation.get("cycle_id") == self._cycle
            and observation.get("generation") == self._frame_generation
            and self._floor_read_conflicts(observation.get("floor_read", {}), target))

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

    def _group_member_action(self, action, payload, cancelled):
        from client_group_member_choice import plan_group_member_choice, group_member_choice_is_current
        with self._lock:
            if payload.get("_retryIntent") is not None and payload["_retryIntent"]!=self._group_intent_generation:
                return self._receipt(action,payload,success=False,reason="stale-group-choice-intent",stale=True)
            if self._cancelled(cancelled):
                return self._receipt(action,payload,success=False,reason="cancelled",stale=True)
            choice_state=self.snapshot()
            cancel_waiting=bool(self._manual_group_choice and self._desired_group_member
                                and self._desired_group_member[3]!=self._map_id and payload.get("mapId")==self._map_id)
            if cancel_waiting:choice_state["groupMemberSelectionPending"]=False
            plan = plan_group_member_choice(choice_state, action, payload)
            if not plan.accepted or not plan.changed:
                if plan.accepted and cancel_waiting:
                    self._discard_sibling()
                    self._group_intent_generation+=1
                    self._group_choice_inflight=None
                    self._desired_group_member=(self._round,self._mode,frozenset(self._identity_native.member_ids),self._map_id)
                    self._group_member_choice_status="confirmed"
                    self._group_choice_reason="本局保持手动选择"
                return self._receipt(action, payload, success=plan.accepted, reason=plan.reason, messageDetail=plan.message)
            if self._cancelled(cancelled):
                return self._receipt(action, payload, success=False, reason="cancelled", stale=True)
            previous_pending = self._discard_sibling()
            self._group_intent_generation += 1
            intent = self._group_intent_generation
            request_scope = self._sibling_scope()
            self._group_choice_inflight = (intent, plan.map_id)
            self._desired_group_member=(self._round,self._mode,frozenset(self._identity_native.member_ids),plan.map_id)
            self._group_member_choice_status="awaiting-geometry"
        if previous_pending is not None:
            self._notify("sibling-rolled-back", token=previous_pending["render"]["token"],
                reason="manual-group-intent-superseded-automatic-material")
        with self._work:
            with self._lock:
                current = self.snapshot(); current["groupMemberSelectionPending"] = False
                if (self._cancelled(cancelled) or intent != self._group_intent_generation
                        or request_scope != self._sibling_scope() or not group_member_choice_is_current(plan, current)):
                    if self._group_choice_inflight and self._group_choice_inflight[0] == intent:
                        self._group_choice_inflight = None
                    return self._receipt(action, payload, success=False, reason="group-choice-context-expired", stale=True)
                state = clone_similarity_state(self._identity_native)
                observation = self._last_observation
                if (not self._map_open or self._floor_required or observation is None
                        or observation["frame_id"] != self._frame_id
                        or self._current_frame_floor_conflicts(self._floor)
                        or not self._capture_is_current(observation["captured"].context, observation["capture_generation"])):
                    self._group_choice_inflight = None
                    self._group_choice_reason="已选择地图，等待当前楼层定位；当前显示尚未切换"
                    return self._receipt(action, payload, success=True, reason="group-member-awaiting-geometry",choiceApplied=False)
                self._group_choice_reason = "正在核对所选地图，成功前保持当前显示"
                if plan.map_id == self._map_id and self._render is not None and self._display_current(self._render):
                    return self._stage_current_member_material(action, payload, state, manual_choice=plan)
                engine, epoch, floor = self._engine, self._epoch, self._floor
                frame_generation, frame_id, cycle = self._frame_generation, self._frame_id, self._cycle
            result = self._solve_observation(action, payload, cancelled, engine, None, epoch,
                observation["captured"], observation["frame"], floor, observation["digest"],
                observation["capture_generation"], frame_generation, frame_id, cycle, plan.map_id,
                identity_stage=state, manual_choice=plan)
            with self._lock:
                if intent!=self._group_intent_generation:
                    return self._receipt(action,payload,success=False,reason="stale-group-choice-intent",stale=True)
            if not result.get("success"):
                with self._lock:
                    if intent!=self._group_intent_generation:
                        return self._receipt(action,payload,success=False,reason="stale-group-choice-intent",stale=True)
                    if self._group_choice_inflight and self._group_choice_inflight[0] == intent:
                        self._group_choice_inflight = None
                    if epoch == self._epoch:
                        self._group_member_choice_status="awaiting-geometry"
                        self._group_choice_reason="已选择地图，等待定位；当前显示尚未切换"
                if not result.get("stale") and not self._cancelled(cancelled):
                    return self._receipt(action,payload,success=True,reason="group-member-awaiting-geometry",
                        choiceApplied=False,poseReason=result.get("reason"))
            return result

    def _retry_desired_group_member(self, action, payload, cancelled, result):
        with self._lock:
            desired=self._desired_group_member
            native=self._identity_native
            if (desired is None or self._group_member_choice_status=="confirmed" or native is None
                    or desired[:3]!=(self._round,self._mode,frozenset(native.member_ids))
                    or self._group_choice_inflight is not None or self._pending_sibling is not None
                    or not self._active or not self._map_open or not self._identity_observation_current()):
                return result
            current=self.snapshot()
            request={k:current[k] for k in ("groupId","groupGeneration","cycleId","floor")}
            request["mapId"]=desired[3]
            request["_retryIntent"]=self._group_intent_generation
        choice=self._group_member_action("select_group_member",request,cancelled)
        return dict(choice,action=action,choiceAction="select_group_member",
                    requestContext=copy.deepcopy(result.get("requestContext",{})))

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
                        self._attach_identity_engine(engine)
                        self._gate = RouteDisplayGate(engine.pack)
                        self._update_candidates([], self._floor)
                    selected = self._gate.confirmed_id or self._gate.selected_id
                    self._desired_group_member=None
                    self._group_member_choice_status=""
                    self._group_member_preference=None
                    if self._manual_group_choice is not None:
                        self.invalidate("manual-group-reselection")
                        self._reset_identity()
                        self._map_id = ""
                        self._gate.confirmed_id = None
                        self._selection_pending = True
                        self._reference_sheet = self._reference_capture_context = None
                    self._candidate_edit = True
                    self._gate.selected_id = selected
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
            self._reset_identity()
            self._durable_map_id = selected
            self._identity_source = "manual"
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
                    reason=f"{type(error).__name__}:{error}", error=True,
                    **self._error_fields(error, action))
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
            self._reset_provisional_floor(target or self._observed_floor)
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
                if engine.pack.mode == "difficult" and str(floor) == "2":
                    return self._receipt(action, payload, success=False,
                        reason="difficult-f2-entry-recognition-disabled", posePending=True)
                selected, entry_candidate, stale = self._rank_identity(engine, observation["frame"], floor,
                    epoch, frame_id, cancelled)
                if stale:
                    return self._receipt(action, payload, success=False, reason=str(stale), stale=True)
                if not selected:
                    grouped = self._identity_fields()["groupLocked"]
                    return self._receipt(action, payload, success=grouped,
                        reason="similar-group-pending" if grouped else "identity-not-uniquely-verified",
                        posePending=True)
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
            if self._active and is_registered_identity_group(self._identity_native):
                if self._map_open and not self._floor_required and self._last_observation is not None:
                    commands.append("select_group_member")
            if self._transient_cycle_current() and self._transient_drag is None:
                commands.append("probe_map_after_drag")
            selected = next((i for i, row in enumerate(self._candidate_rows)
                             if self._gate is not None and row["key"] == self._gate.selected_id), -1)
            if self._capture is not None:
                commands.append("bind_capture")
                if ready and guarded:
                    commands += ["start_round", "toggle_round"]
                if ready and guarded:
                    commands += ["capture_now", "map"]
                    if self._map_open:
                        commands.append("refresh_map")
            capture_state = getattr(self._capture, "snapshot", {}) if self._capture is not None else {}
            if callable(capture_state):
                capture_state = capture_state()
            metadata = ({key: copy.deepcopy(value) for key, value in self._render.items()
                         if key not in ("pixels",)} if self._render is not None else {})
            return dict(**self._context(), cycleId=self._cycle, mapId=self._durable_map_id, floor=self._floor,
                generation=self._frame_generation, preferredProfileId=self._profile,
                effectiveProfileId=metadata.get("effectiveProfileId", ""),
                displayFallback=metadata.get("fallback", False), fallbackReason=metadata.get("fallbackReason", ""),
                engineConnected=True,
                liveEngineAvailable=bool(ready and guarded and self._capture is not None),
                liveUnavailableReason=("capture-context-recheck-unavailable" if not guarded else
                                       "capture-provider-unavailable" if self._capture is None else
                                       capabilities[self._mode]["reason"] if not ready else ""),
                roundActive=self._active, roundPreparing=self._round_preparing, mapOpen=self._map_open,
                captureInProgress=self._capture_pending,
                alignedVisible=self._aligned_visible,
                automaticCandidateRows=copy.deepcopy(self._automatic_candidate_rows),
                candidateRows=copy.deepcopy(self._candidate_rows), selectedCandidateIndex=selected,
                selectedCandidate=copy.deepcopy(self._candidate_rows[selected]) if selected >= 0 else {},
                **self._identity_fields(), candidateEditMode=self._candidate_edit,
                poseAttempt=copy.deepcopy(self._pose_attempt), poseResult=copy.deepcopy(self._pose_result),
                siblingPending=self._pending_sibling is not None,
                pendingSiblingToken=self._pending_sibling["render"]["token"] if self._pending_sibling is not None else "",
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
            self._transient_drag = None
            if reason in ("map-closed","escape","foreground-lost","capture-context-changed","floor-unknown") or any(
                    marker in str(reason) for marker in ("round","resource","identity","candidate","mode","reselection")):
                self._transient_cycle = None
            self._discard_sibling()
            self._group_intent_generation += 1
            self._group_choice_inflight = None
            if reason != "game-floor-control-intent":
                self._game_floor_intent = None
                self._floor_click_epoch_span = None
            if reason == "custom-gallery-changed":
                self._reference_sheet = self._reference_capture_context = None
            self._epoch += 1
            self._core.close(reason=str(reason))
            self._render = None
            self._render_capture_context = None
            if reason in ("map-closed", "escape", "foreground-lost", "capture-context-changed", "floor-unknown"):
                self._map_open = False
            if "resource" in str(reason):
                self._active = self._map_open = self._round_preparing = False
                self._prepared_round_ready = False
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
            self._discard_sibling()
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

    def _reproject_route_display(self, action, payload, cancelled):
        """A saved author changes pixels/basis only, never runs a pose sensor."""
        import numpy as np
        from types import SimpleNamespace
        from client_route_reprojection import prepare_route_reprojection
        from round_layer_group_commit import rebase_group_display_tracking
        with self._work:
            with self._lock:
                engine, profile, map_id, floor = self._engine, self._profile, self._map_id, self._floor
                generation = getattr(self, "_display_generation", 0)
                scope = (self._epoch, self._round, self._cycle, self._frame_id, engine, profile, map_id, floor, generation)
                source = self._render
                sheet = self._reference_sheet
                if not self._active or engine is None or not map_id or not floor:
                    return self._receipt(action, payload, success=True, reason="context-updated")
                pose_current = bool(source and self._display_current(source)
                    and self._capture_is_current(self._render_capture_context, source.get("captureGeneration")))
                source_context = self._render_capture_context
                sheet_context = self._reference_capture_context
                staged_group = copy.deepcopy(self._group)
                variant = self._settings.get("zhan10CoffinVariantId", "variant-01")
                canonical = source["canonicalMatrix"] if pose_current else None
            try:
                prepared = prepare_route_reprojection(engine, map_id, floor, profile, canonical, variant_id=variant)
                layer = prepared["layer"]
                if staged_group is not None:
                    owner = SimpleNamespace(
                        _effective_route_profile=lambda *args: profile,
                        _shared_display_layer=lambda pref, mid, fl: prepare_route_reprojection(
                            engine, mid, fl, pref, variant_id=variant)["layer"],
                        _shared_display_bridge=lambda pref, mid, fl: prepare_route_reprojection(
                            engine, mid, fl, pref, variant_id=variant)["layer"]["matrixDisplayToReference"])
                    rebase_group_display_tracking(owner, staged_group, map_id, layer.get("effectiveProfileId", "shared-structure"))
                pixels = np.ascontiguousarray(layer["pixels"][:, :, [2, 1, 0, 3]])
                pixels.setflags(write=False)
            except Exception as error:
                with self._lock:
                    if scope == (self._epoch, self._round, self._cycle, self._frame_id, self._engine,
                            self._profile, self._map_id, self._floor, getattr(self, "_display_generation", 0)):
                        self._render = self._render_capture_context = None
                        self._reference_sheet = self._reference_capture_context = None
                return self._receipt(action, payload, success=False, reason=f"route-display-reprojection-failed:{error}")
            with self._lock:
                if (self._cancelled(cancelled) or scope != (self._epoch, self._round, self._cycle,
                        self._frame_id, self._engine, self._profile, self._map_id, self._floor,
                        getattr(self, "_display_generation", 0))):
                    return self._receipt(action, payload, success=False, reason="stale-display-settings", stale=True)
                metadata = dict(profileId=profile, preferredProfileId=profile,
                    effectiveProfileId=layer.get("effectiveProfileId", layer["routeProfileId"]),
                    resourceIdentity=layer["resourceIdentity"], displayKind=layer["displayKind"],
                    displayLabel=layer["displayLabel"], routeProfileId=layer["routeProfileId"],
                    authorStatus=layer.get("authorStatus", "none"), fallback=layer.get("fallback", False),
                    fallbackReason=layer.get("fallbackReason", ""), variantId=layer.get("variantId"),
                    displayGeneration=generation, referencePath=layer.get("referencePath", ""))
                if sheet is not None:
                    self._reference_sheet = dict(sheet, **metadata)
                    self._reference_sheet.update(token=uuid4().hex, pixels=pixels)
                    self._reference_capture_context = sheet_context
                render_token = ""
                if (pose_current and self._render is source and self._display_current(source)
                        and self._capture_is_current(source_context, source.get("captureGeneration"))):
                    self._render = dict(source, **metadata)
                    display_matrix = prepared["matrixSourceToClient"].copy()
                    if self._transient_cycle_current():
                        offset = self._transient_cycle["offset"]
                        display_matrix[:2,:] += np.asarray(offset)[:,None] * display_matrix[2:3,:]
                    self._render.update(token=uuid4().hex, pixels=pixels,
                        matrix=display_matrix.tolist(),
                        routeWidth=pixels.shape[1], routeHeight=pixels.shape[0])
                    self._render_capture_context = source_context
                    render_token = self._render["token"]
                else:
                    self._render = self._render_capture_context = None
                if self._group is not None and staged_group is not None:
                    self._group.tracking_by_floor = staged_group.tracking_by_floor
                    self._group.tracking = staged_group.tracking
                    self._group.render_profile_verified_floors = staged_group.render_profile_verified_floors
                    self._group.route_profile_id = staged_group.route_profile_id
                    self._group.displayed_overlay = None
            if render_token:
                self._notify("render-ready", reason="route-display-reprojected", token=render_token, unchanged=False)
            self._notify("reference-ready", reason="route-display-reprojected")
            return self._receipt(action, payload, success=True, reason="route-display-reprojected", renderToken=render_token)

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

    @staticmethod
    def _reference_owner_context(row):
        return tuple(row.get(key) for key in ("mode", "roundId", "mapId", "floor",
            "engineIdentity", "captureGeneration"))

    def _remember_provisional_reference(self):
        row = self._reference_sheet
        if (row is not None and not self._durable_map_id and self._map_open
                and row.get("mapId") == self._map_id and row.get("floor") == self._floor
                and self._core.can_display(cycle_id=self._cycle, map_id=self._map_id,
                    floor=self._floor, frame_generation=self._frame_generation)):
            self._provisional_reference_context = self._reference_owner_context(row)

    def reference_payload(self):
        """Current selection/floor grants a static sheet, never an aligned pose.

        Its pixels are prepared by the capture worker. Closing the map retains
        the selected current-round sheet. Binding/resource/identity/floor changes
        revoke it. Durable static content ignores live window geometry,
        foreground, pose and material ACK. Provisional members retain the old
        current-pose / previously verified closed-context exception.
        """
        with self._lock:
            row = self._reference_sheet
            if (row is None or not self._active or self._selection_pending or self._floor_required
                    or self._current_frame_floor_conflicts(self._floor)
                    or self._gate is None or self._gate.confirmed_id != self._map_id
                    or any(row.get(key) != expected for key, expected in (
                        ("mode", self._mode), ("profileId", self._profile), ("mapId", self._map_id),
                        ("floor", self._floor), ("roundId", self._round),
                        ("displayGeneration", getattr(self, "_display_generation", 0)),
                        ("engineIdentity", id(self._engine))))
                    or row.get("captureGeneration") != self._capture_generation()
                    or (self._evidence_kind == "live-client-capture" and row.get("captureGeneration") is None)):
                return None
            if not self._durable_map_id:
                # Preserve the original provisional exception: an open-map
                # reference needs current core authority; a closed-map sheet
                # may reuse only its previously verified logical context.
                valid_provisional = (self._core.can_display(cycle_id=self._cycle, map_id=self._map_id,
                    floor=self._floor, frame_generation=self._frame_generation) if self._map_open
                    else self._provisional_reference_context == self._reference_owner_context(row))
                if not valid_provisional:
                    return None
            return {**{key: value if key == "pixels" else copy.deepcopy(value) for key, value in row.items()},
                    **self._identity_fields()}

    def _capture_frame(self, cancelled):
        try:
            return self._capture.capture(cancelled)
        except Exception as error:
            error.error_phase = "capture"
            raise

    def _error_fields(self, error, action):
        fields = dict(traceback="".join(traceback.format_exception(
            type(error), error, error.__traceback__, limit=-12, chain=False))[-8192:])
        if getattr(error, "error_phase", None) == "capture" or action == "bind_capture":
            fields["errorPhase"] = "capture"
            raw_diagnostics = getattr(error, "capture_diagnostics", {})
            allowed = {"frameShape", "frameDtype", "roiClientBounds", "requestedClientBounds",
                       "fullClientSize", "clientBounds", "dpi", "expectedHwnd", "foregroundHwnd", "foregroundPid"}
            diagnostics = {key:value for key,value in raw_diagnostics.items() if key in allowed} if isinstance(raw_diagnostics, dict) else {}
            try:
                state = getattr(self._capture, "snapshot", {})
                state = state() if callable(state) else state
                generation = getattr(self._capture, "generation", None)
                if generation is None and isinstance(state, dict):
                    generation = state.get("generation")
                if type(generation) is int:
                    diagnostics["providerGeneration"] = generation
                    diagnostics["providerGenerationSource"] = "current-provider-at-error"
                if isinstance(state, dict) and state.get("captureIntent") in ("full-client", "roi"):
                    diagnostics["providerIntent"] = state["captureIntent"]
            except Exception:
                pass
            fields["captureDiagnostics"] = diagnostics
        return fields

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
        if action == "resume_group_automatic":
            return self._receipt(action, payload, success=False, reason="unsupported-action")
        if action == "select_group_member":
            try:
                return self._group_member_action(action, payload, cancelled)
            except Exception as error:
                with self._lock:
                    if self._pending_sibling is None:
                        self._group_choice_inflight = None
                        self._group_choice_reason = "所选地图处理失败，当前选择未改变"
                return self._receipt(action, payload, success=False,
                    reason=f"{type(error).__name__}:{error}", error=True, **self._error_fields(error, action))
        if action in ("previous_candidate", "next_candidate", "select_candidate", "confirm_route",
                      "reselect_map", "map_reselection", "set_floor", "cycle_floor"):
            if self._cancelled(cancelled):
                return self._receipt(action, payload, success=False, reason="cancelled", cancelled=True)
            try:
                return self._manual_action(action, payload, cancelled)
            except Exception as error:
                self.invalidate("manual-action-error")
                return self._receipt(action, payload, success=False,
                    reason=f"{type(error).__name__}:{error}", error=True,
                    **self._error_fields(error, action))
        if action == "toggle_sheet_visibility":
            with self._lock:
                self._aligned_visible = not self._aligned_visible
            self._notify("aligned-visibility-changed", reason="visibility-toggled")
            return self._receipt(action, payload, success=True, reason="visibility-toggled")
        capture_request_epoch = self._epoch
        if action == "map":
            from session_state_machine import map_hotkey_transition
            with self._lock:
                sequence = payload.get("inputSequence")
                if sequence is not None:
                    if type(sequence) is not int or sequence <= self._last_map_input_sequence:
                        return self._receipt(action, payload, success=False, reason="map-input-sequence-expired", stale=True)
                    self._last_map_input_sequence = sequence
                if "openIntent" in payload:
                    if type(payload["openIntent"]) is not bool:
                        return self._receipt(action, payload, success=False, reason="map-input-intent-invalid")
                    transition = "open" if payload["openIntent"] else "close"
                else:
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
                    self._active = self._map_open = self._round_preparing = False
                    self._prepared_round_ready = False
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
                if changed_mode:
                    self.invalidate("mode-changed")
                elif changed_profile:
                    self._display_generation = getattr(self, "_display_generation", 0) + 1
                    self._discard_sibling()
                if changed_mode:
                    self._active = self._map_open = self._round_preparing = False
                    self._prepared_round_ready = False
                    self._core.reset_round(reason="mode-changed")
                    self._group = self._engine = None
                    self._clear_candidates()
                    self._map_id, self._floor = "", ""
                    if self._capture is not None:
                        self._capture.disable()
                self._mode, self._profile = mode, profile
                self._offline.dispatch("set_context", dict(mode=mode, profileId=profile))
            if changed_profile and not changed_mode:
                return self._reproject_route_display(action, payload, cancelled)
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
                if action == "probe_map_after_drag":
                    return self._probe_map_after_drag(payload, cancelled)
                if action == "remember_render_material":
                    return self._remember_render_material(str(payload.get("renderToken", "")), payload, cancelled)
                if action == "bind_capture":
                    if self._capture is None:
                        raise RuntimeError("capture-provider-unavailable")
                    preparing = self._round_preparing and not self._active
                    self.dispatch("end_round", payload)
                    bind_full = getattr(self._capture, "bindFullClient", None)
                    if not callable(bind_full):
                        raise RuntimeError("full-client-capture-binding-unavailable")
                    bind_full(int(payload["hwnd"]))
                    if preparing:
                        prepared = self._start(payload, cancelled)
                        if not prepared["success"]:
                            return prepared
                    return self._receipt(action, payload, success=True, reason="capture-bound-disabled")
                if action == "toggle_round":
                    return self.dispatch("end_round" if self._active or self._round_preparing else "start_round", payload, cancelled)
                if action == "start_round":
                    return self._start(payload, cancelled)
                if action in ("capture_now", "map"):
                    if capture_request_epoch != self._epoch or self._cancelled(cancelled):
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                    activated = self._ensure_round_active(payload, cancelled)
                    if not activated["success"]:
                        return self._receipt(action, payload, success=False, reason=activated["reason"],
                            stale=bool(activated.get("stale")))
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
            return self._receipt(action, payload, success=False, reason=f"{type(error).__name__}:{error}", error=True,
                    **self._error_fields(error, action))

    def _start(self, payload, cancelled):
        if self._capture is None:
            raise RuntimeError("capture-provider-unavailable")
        if self._evidence_kind == "live-client-capture" and not callable(getattr(self._capture, "is_current", None)):
            raise RuntimeError("capture-context-recheck-unavailable")
        with self._lock:
            epoch, mode = self._epoch, self._mode
            if self._active or (self._round_preparing and self._prepared_round_ready):
                return self._receipt("start_round", payload, success=True,
                    reason="round-already-active" if self._active else "round-already-prepared")
            preselected = self._gate.selected_id if self._candidate_edit and self._gate is not None else None
            self._round_preparing = True
            self._prepared_round_ready = False
        try:
            engine = self._factories[mode]()
        except Exception:
            with self._lock:
                if epoch == self._epoch:
                    self._round_preparing = False
            raise
        if self._cancelled(cancelled):
            with self._lock:
                if epoch == self._epoch:
                    self._round_preparing = False
            return self._receipt("start_round", payload, success=False, reason="cancelled", cancelled=True)
        with self._lock:
            if epoch != self._epoch or mode != self._mode:
                return self._receipt("start_round", payload, success=False, reason="stale-context", stale=True)
            self._core.reset_round(reason="round-prepared")
            self._round += 1
            self._cycle = self._frame_generation = 0
            self._probe_misses = 0
            self._active, self._map_open = False, False
            self._round_preparing = self._prepared_round_ready = True
            self._map_id = self._floor = self._frame_id = ""
            self._group = self._render = None
            self._engine = engine
            self._attach_identity_engine(engine)
            from nightmare_candidate import RouteDisplayGate
            self._clear_candidates()
            self._gate = RouteDisplayGate(engine.pack)
            self._update_candidates([], "")
            if preselected and preselected in engine.pack.by_id:
                self._gate.select(preselected)
                self._candidate_edit = self._selection_pending = True
        return self._receipt("start_round", payload, success=True, reason="round-prepared")

    def _ensure_round_active(self, payload, cancelled):
        with self._lock:
            if self._active:
                return dict(success=True, reason="round-already-active")
        prepared = self._start(payload, cancelled)
        if not prepared["success"]:
            return prepared
        with self._lock:
            epoch, engine = self._epoch, self._engine
        if self._cancelled(cancelled):
            return dict(success=False, reason="cancelled-or-stale", stale=True)
        if not self._capture.enable():
            return dict(success=False, reason="capture-enable-rejected")
        with self._lock:
            if (self._cancelled(cancelled) or epoch != self._epoch or engine is not self._engine
                    or not self._round_preparing or not self._prepared_round_ready):
                self._capture.disable()
                return dict(success=False, reason="cancelled-or-stale", stale=True)
            self._active, self._round_preparing = True, False
        self._notify("round-started", reason="round-active")
        return dict(success=True, reason="round-active")

    def _refresh_map(self, payload, cancelled):
        # Passive timer requests never build an unbounded worker backlog.
        if not self._work.acquire(blocking=False):
            return self._receipt("refresh_map", payload, success=True, reason="refresh-coalesced", coalesced=True)
        owns_capture = False
        try:
            with self._lock:
                if (self._capture_pending or self._group_choice_inflight is not None or self._transient_drag is not None
                        or self._pending_sibling is not None and self._pending_sibling.get("projection")):
                    return self._receipt("refresh_map", payload, success=True, reason="refresh-coalesced", coalesced=True)
                if not self._active or not self._map_open:
                    return self._receipt("refresh_map", payload, success=False, reason="map-refresh-not-active")
                transient = self._transient_cycle_current()
                self._capture_pending = owns_capture = True
            if transient:
                return self._probe_map_after_drag(payload, cancelled)
            return self._capture_and_solve("refresh_map", payload, cancelled)
        except Exception as error:
            self.invalidate("runtime-error")
            return self._receipt("refresh_map", payload, success=False,
                                 reason=f"{type(error).__name__}:{error}", error=True,
                    **self._error_fields(error, "refresh_map"))
        finally:
            if owns_capture:
                with self._lock:
                    self._capture_pending = False
            self._work.release()

    def _try_refresh_current(self, payload, captured, frame, floor, digest, capture_generation,
                             engine, epoch, obsolete, *, floor_read=None):
        """Renew an unchanged measured H with fresh residual/core authority."""
        import numpy as np
        from alignment_core_v2 import AlignmentCandidate
        from alignment_observation import rendered_profile_residual_is_verified
        # Do not renew a cached pose on a newly captured contradictory floor.
        # The normal observation path below binds this frame/generation and
        # emits the common structured rejection before any fit is attempted.
        if self._floor_read_conflicts(floor_read or {}, floor):
            return None
        if self._steady_material_ready and bool(self._settings.get("idvb0059Enabled", True)):
            return None
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
        self._set_pose_phase("pending", "current-frame-refresh", map_id, floor, current_frame, self._frame_generation)
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
            if self._transient_drag is not None or self._transient_cycle_current():
                return self._receipt("refresh_map", payload, success=True, reason="refresh-coalesced", coalesced=True)
            if (obsolete() or self._render is None or self._render["token"] != row["token"]
                    or current_frame != self._frame_id or epoch != self._epoch
                    or cycle != self._cycle or self._floor != floor):
                self._revoke_render("refresh-cancelled-or-stale")
                return self._receipt("refresh_map", payload, success=False, reason="cancelled-or-stale", stale=True)
            if not self._capture_is_current(captured.context, capture_generation):
                self.invalidate("capture-context-changed")
                return self._receipt("refresh_map", payload, success=False, reason="capture-context-changed", stale=True)
            # Image.Ready can acknowledge this unchanged material while the
            # residual calculation runs. Preserve only that receipt, after all
            # old-scope checks and before advancing frame authority. The queued
            # remember worker must consume the fresh frame/fit/context below.
            previous = self._remember_proposals.get(row["token"])
            material_accepted = bool(previous and previous["accepted"]
                and previous["context"] == self._steady_context()
                and previous["group"] is self._group)
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
            self._discard_sibling()
            self._frame_generation, self._frame_id = generation, frame_id
            self._last_observation = dict(captured=captured, frame=frame, floor=floor, digest=digest, created_at=self._monotonic(),
                capture_generation=capture_generation, frame_id=frame_id, observed_floor=self._observed_floor,
                cycle_id=self._cycle, generation=self._frame_generation, floor_read=copy.deepcopy(floor_read or {}))
            self._probe_misses = 0
            self._render = dict(row, authorizationId=token.authorization_id, frameId=frame_id,
                generation=generation, observedFloor=self._observed_floor,
                floorSource="manual" if self._manual_floor else "automatic")
            self._render_capture_context = captured.context
            if self._reference_sheet is not None:
                self._reference_sheet.update(self._identity_fields())
            self._remember_provisional_reference()
            render_token = self._render["token"]
            self._register_material_proposal(self._render, frame,
                dict(success=True, selectedRouteCurrentFrameResidual=residual), captured, self._group,
                accepted=material_accepted)
        self._set_pose_phase("valid", "current-pose-refreshed", map_id, floor, frame_id, generation)
        self._notify("render-ready", reason="current-pose-refreshed", token=render_token, unchanged=True)
        if material_accepted:
            # A refresh may have superseded a running remember job. Re-offer
            # its fresh proposal; Session deduplicates an already queued token.
            self._notify("render-material-accepted", token=render_token, reason="current-material-revalidated")
        if obsolete():
            return self._receipt("refresh_map", payload, success=False, reason="cancelled-or-stale", stale=True)
        return self._receipt("refresh_map", payload, success=True, reason="current-pose-refreshed",
                             renderToken=render_token, unchanged=True,
                             diagnosticOnly=self._evidence_kind != "live-client-capture")

    def _capture_and_solve(self, action, payload, cancelled):
        with self._lock:
            if not self._active:
                return self._receipt(action, payload, success=False, reason="round-not-active")
            epoch, engine, group = self._epoch, self._engine, self._group
        if action != "refresh_map":
            with self._lock:
                self._game_floor_intent = None
                self._floor_click_epoch_span = None
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
            captured = self._capture_frame(obsolete)
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
                self._last_observation = dict(captured=captured, frame=frame, floor=None, digest=digest, created_at=self._monotonic(),
                    capture_generation=capture_generation, frame_id=self._frame_id, observed_floor="",
                    cycle_id=self._cycle, generation=self._frame_generation, floor_read=copy.deepcopy(floor_read))
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
                                                 engine, epoch, obsolete, floor_read=floor_read)
            if renewed is not None:
                return self._retry_desired_group_member(action,payload,cancelled,renewed)
        with self._lock:
            if obsolete():
                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
            if not self._map_open:
                self._cycle += 1
            self._map_open = True
            self._probe_misses = 0
            self._reset_provisional_floor(floor)
            self._floor = floor
            self._frame_generation += 1
            self._frame_id = f"{self._round}:{self._frame_generation}:{digest[:16]}"
            frame_generation, frame_id, cycle = self._frame_generation, self._frame_id, self._cycle
            map_id = self._map_id
            self._last_observation = dict(captured=captured, frame=frame, floor=floor, digest=digest, created_at=self._monotonic(),
                capture_generation=capture_generation, frame_id=frame_id, observed_floor=self._observed_floor,
                cycle_id=self._cycle, generation=self._frame_generation, floor_read=copy.deepcopy(floor_read or {}))
            for row in self._candidate_rows:
                row["floor"] = floor
        if pending_selection:
            return self._receipt(action, payload, success=False, reason="candidate-awaiting-confirmation")
        ranked = []
        entry_candidate = None
        if not map_id:
            if not map_id and engine.pack.mode == "difficult" and str(floor) == "2":
                return self._receipt(action, payload, success=False,
                    reason="difficult-f2-entry-recognition-disabled", posePending=True)
            # Opening the map again refreshes the selected member's geometry.
            # Only an identity reset/new round starts discovery; later side
            # views must not silently replace a front-group member selection.
            map_id, entry_candidate, stale = self._rank_identity(engine, frame, floor, epoch, frame_id, cancelled)
            if stale:
                return self._receipt(action, payload, success=False, reason=str(stale), stale=True)
            if not map_id:
                grouped = self._identity_fields()["groupLocked"]
                return self._receipt(action, payload, success=grouped,
                    reason="similar-group-pending" if grouped else "identity-not-uniquely-verified", posePending=True)
            with self._lock:
                group = self._group
        if group is not None and group.map_id != map_id:
            group = None
        result=self._solve_observation(action, payload, cancelled, engine, group, epoch, captured, frame,
            floor, digest, capture_generation, frame_generation, frame_id, cycle, map_id, entry_candidate)
        return self._retry_desired_group_member(action,payload,cancelled,result)

    @staticmethod
    def _seed_group_core(core, group):
        from types import SimpleNamespace
        from round_layer_group_commit import seed_group_pose
        owner=SimpleNamespace(alignment_core_v2=core,_active_round_layer_group=lambda mid:group
                              if group is not None and group.map_id==mid else None)
        return seed_group_pose(owner,group.map_id) if group is not None else False

    def _try_transient_floor_projection(self, payload, cancelled, engine, captured, frame, floor,
                                        capture_generation, *, advance_frame=False, floor_read=None):
        """Original reviewed F1/F2 provisional projection, staged for Image.Ready."""
        import numpy as np
        from types import SimpleNamespace
        from round_layer_group_commit import show_shared_projection, translated_source_matrix
        from client_route_reprojection import prepare_route_reprojection
        with self._lock:
            if (not self._transient_cycle_current() or self._transient_drag is not None
                    or self._pending_sibling is not None or str(floor)!=str(self._floor)
                    or self._current_frame_floor_conflicts(floor) and not advance_frame):
                return None
            cycle=self._transient_cycle
            source=dict(cycle.get("current") or {})
            previous=source.get("floor")
            if str(previous)==str(floor) and not source.get("provisional"):
                return None
            group=self._group
            if group is None or group.map_id!=self._map_id:
                return None
            epoch=self._epoch
            if advance_frame:
                if self._floor_read_conflicts(floor_read or {},floor):return None
                immutable=np.ascontiguousarray(frame).copy();immutable.setflags(write=False);frame=immutable
                digest=hashlib.sha256(frame.tobytes()).hexdigest()
                self._frame_generation+=1
                self._frame_id=f"{self._round}:{self._frame_generation}:{digest[:16]}"
                self._last_observation=dict(captured=captured,frame=frame,floor=floor,digest=digest,created_at=self._monotonic(),
                    capture_generation=capture_generation,frame_id=self._frame_id,observed_floor=self._observed_floor,
                    cycle_id=self._cycle,generation=self._frame_generation,floor_read=copy.deepcopy(floor_read or {}))
            scope=self._sibling_scope()
            map_id,profile,variant=self._map_id,self._profile,self._settings.get("zhan10CoffinVariantId","variant-01")
            generation,frame_id=self._frame_generation,self._frame_id
            context=captured.context
            signature=self._steady_signature(captured)
            stage=self._copy_pose_core(self._core)
            self._seed_group_core(stage,group)
            stage.begin_observation(cycle_id=self._cycle,map_id=map_id,floor=floor,
                                    frame_generation=generation,frame_signature=(frame_id,),reason="transient-floor-projection")
            offset=tuple(cycle["offset"])
        base=group.canonical_pose_for_floor(str(floor))
        if base is None:return None
        prepared=prepare_route_reprojection(engine,map_id,floor,profile,base,variant_id=variant)
        layer=prepared["layer"]
        rgba=np.ascontiguousarray(layer["pixels"][:,:,[2,1,0,3]]);rgba.setflags(write=False)
        display_matrix=translated_source_matrix(prepared["matrixSourceToClient"],*offset)
        projected=translated_source_matrix(base,*offset)
        view=dict(overlay=rgba,matrixSourceToClient=display_matrix.tolist(),
                  canonicalMatrixSourceToClient=projected.tolist())
        owner=SimpleNamespace(pack=engine.pack,engine=engine,alignment_core_v2=stage,
            current_frame=frame,current_floor=str(floor),active_map_cycle_id=self._cycle,
            current_frame_generation=generation,current_bounds=context.client_screen_bounds,
            current_alignment_candidate=source,author_coordinate_adapters=engine.qt_adapters,
            route_profile_id=layer.get("effectiveProfileId","shared-structure"),display_environment=None,
            gate=SimpleNamespace(confirmed_id=map_id),mouse_drag_prediction_pending=bool(cycle.get("probePending")),
            _active_round_layer_group=lambda mid:group if mid==group.map_id else None,
            _alignment_v2_current_frame_equivalent=lambda event:False,
            _alignment_v2_failed_input_unchanged=lambda *args:False,
            _spatial_route_surface_is_visible=lambda:False,
            _round_group_floor_view=lambda *args:view,
            _alignment_v2_show_overlay=lambda *args,**kwargs:True,
            _aligned_author_route_image=lambda *args:layer["pixels"],
            _mark_overlay_display_verified=lambda *args,**kwargs:None,
            _log_core_chain_event=lambda *args,**kwargs:None,show_sheet=lambda:None)
        if not show_shared_projection(owner,map_id,str(floor),previous,{},display_environment_signature=lambda _:signature):
            return None
        with self._lock:
            if (self._cancelled(cancelled) or epoch!=self._epoch or scope!=self._sibling_scope()
                    or group is not self._group or cycle is not self._transient_cycle
                    or not self._capture_is_current(context,capture_generation)):
                return None
            metadata=dict(mode=self._mode,profileId=profile,preferredProfileId=profile,
                effectiveProfileId=layer.get("effectiveProfileId","shared-structure"),mapId=map_id,floor=str(floor),
                roundId=self._round,variantId=layer.get("variantId"),displayGeneration=getattr(self,"_display_generation",0),
                resourceIdentity=layer["resourceIdentity"],engineIdentity=id(engine),captureGeneration=capture_generation,
                displayKind=layer["displayKind"],displayLabel=layer["displayLabel"],routeProfileId=layer["routeProfileId"],
                authorStatus=layer.get("authorStatus","none"),fallback=layer.get("fallback",False),
                fallbackReason=layer.get("fallbackReason",""),evidenceKind=self._evidence_kind,**self._identity_fields())
            reference=dict(metadata,token=uuid4().hex,pixels=rgba,referenceAuthorized=self._evidence_kind=="live-client-capture")
            token=uuid4().hex
            row=dict(metadata,token=token,epoch=self._epoch,authorizationId=stage.display_authorization.authorization_id,
                cycleId=self._cycle,frameId=frame_id,generation=generation,pixels=rgba,
                matrix=display_matrix.tolist(),canonicalMatrix=np.asarray(base).tolist(),projectedCanonicalMatrix=projected.tolist(),
                routeWidth=rgba.shape[1],routeHeight=rgba.shape[0],clientWidth=context.full_client_size[0],clientHeight=context.full_client_size[1],
                originX=context.client_screen_bounds[0],originY=context.client_screen_bounds[1],
                liveAuthorization=self._evidence_kind=="live-client-capture",projectionOnly=True,poseProvisional=True,
                currentFrameVerified=False,transient=True,transientOffset=list(offset),
                projectionSourceFloor=owner.current_alignment_candidate.get("projectionSourceFloor"),
                poseReason="shared-root-provisional",poseStrategy="shared-root-projection",requiresMaterialAck=True,
                sourceToken=self._render["token"] if self._render else "",sourceMapId=map_id)
            self._pending_sibling=dict(scope=scope,sourceCore=self._core,sourceToken=row["sourceToken"],sourceMapId=map_id,
                core=stage,group=group,identity=None,render=row,reference=reference,captureContext=context,
                projection=True,candidate=owner.current_alignment_candidate)
        self._set_pose_phase("pending","shared-root-provisional-geometry-pending",map_id,str(floor),frame_id,generation)
        self._notify("sibling-material-pending",token=token,reason="shared-root-projection-awaiting-material")
        return self._receipt("probe_map_after_drag",payload,success=True,reason="sibling-material-pending",
            pendingSiblingToken=token,renderToken=row["sourceToken"],projectionOnly=True,posePending=True)

    def _stage_round_group_commit(self, engine, group, map_id, floor, canonical, render_matrix,
                                  display, frame, captured, cycle, frame_generation, residual):
        """Reuse the original single-root relation and validation-pool writer."""
        from types import SimpleNamespace
        from round_layer_group_commit import (commit_alignment_v2_round_pose,
            prime_round_layer_group_relations, map_anchor_focus_point)
        stage = group if group is not None and group.map_id == map_id else None
        owner = SimpleNamespace(pack=engine.pack, round_layer_group=stage,
            author_coordinate_adapters=engine.qt_adapters, active_map_cycle_id=cycle,
            current_frame_generation=frame_generation, display_environment=None,
            _native_similarity_resources_ready=lambda: True,
            _open_cycle_transient_motion_active=lambda _map: False,
            _shared_structure_mode_enabled=lambda: True, _log=lambda *args, **kwargs: None)
        owner._active_round_layer_group = lambda mid, profile=None: (
            owner.round_layer_group if owner.round_layer_group is not None
            and owner.round_layer_group.map_id == mid
            else None)
        owner._prime_round_layer_group_relations = lambda mid, anchor: prime_round_layer_group_relations(owner, mid, anchor)
        context = captured.context
        signature = (tuple(context.full_client_size), tuple(context.client_screen_bounds), context.dpi)
        accepted = commit_alignment_v2_round_pose(owner, map_id, floor, display["pixels"], frame,
            context.client_screen_bounds, render_matrix=render_matrix, canonical_matrix=canonical,
            route_profile_id=display.get("effectiveProfileId", "shared-structure"), source="initial-structure-v2", residual=residual,
            normalize_route_profile_id=lambda profile: profile,
            display_environment_signature=lambda _: signature, map_anchor_focus_point=map_anchor_focus_point)
        return owner.round_layer_group if accepted else None

    def _solve_observation(self, action, payload, cancelled, engine, group, epoch, captured, frame,
                           floor, digest, capture_generation, frame_generation, frame_id, cycle,
                           map_id, entry_candidate=None, *, identity_stage=None, manual_choice=None):
        import numpy as np
        from alignment_core_v2 import AlignmentCandidate
        from alignment_observation import rendered_profile_residual_is_verified
        from round_layer_group import RoundLayerGroup
        obsolete = lambda: self._cancelled(cancelled) or epoch != self._epoch
        width, height = captured.context.full_client_size
        with self._lock:
            if obsolete() or frame_id != self._frame_id or floor != self._floor:
                return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
            if self._current_frame_floor_conflicts(floor, frame_id=frame_id):
                reason = "current-frame-floor-conflict"
                observed_floor = self._observed_floor
                self._revoke_render(reason)
                self._reference_sheet = self._reference_capture_context = None
                self._set_pose_phase("rejected", reason, map_id, floor, frame_id, frame_generation,
                    observedFloor=observed_floor, targetFloor=str(floor), fitAttempted=False)
                return self._receipt(action, payload, success=False, reason=reason,
                    observedFloor=observed_floor, targetFloor=str(floor), fitAttempted=False)
            display_generation = getattr(self, "_display_generation", 0)
            group_intent_generation = self._group_intent_generation
            variant_id = self._settings.get("zhan10CoffinVariantId", "variant-01")
        # Static reference follows the independently confirmed map and floor;
        # a failed pose solve must not also remove the player's reference map.
        display = self._display_layer(engine, map_id, floor, self._profile, variant_id=variant_id)
        reference_pixels = np.ascontiguousarray(display["pixels"][:, :, [2, 1, 0, 3]])
        reference_pixels.setflags(write=False)
        with self._lock:
            if display_generation != getattr(self, "_display_generation", 0):
                return self._receipt(action, payload, success=False, reason="stale-display-settings", stale=True)
            if (group_intent_generation != self._group_intent_generation
                    or manual_choice is None and self._group_choice_inflight is not None):
                return self._receipt(action, payload, success=False, reason="manual-group-intent-superseded-work", stale=True)
            if obsolete() or frame_id != self._frame_id or floor != self._floor:
                return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
            if capture_generation != self._capture_generation():
                return self._receipt(action, payload, success=False, reason="capture-context-changed", stale=True)
            reference_sheet = dict(token=uuid4().hex, pixels=reference_pixels,
                mode=self._mode, profileId=self._profile, mapId=map_id, floor=floor, roundId=self._round,
                variantId=display.get("variantId"), displayGeneration=display_generation,
                resourceIdentity=display["resourceIdentity"], captureGeneration=capture_generation, engineIdentity=id(engine),
                referenceAuthorized=self._evidence_kind == "live-client-capture", evidenceKind=self._evidence_kind,
                preferredProfileId=self._profile, effectiveProfileId=display.get("effectiveProfileId", display["routeProfileId"]),
                displayKind=display["displayKind"], displayLabel=display["displayLabel"],
                routeProfileId=display["routeProfileId"], authorStatus=display.get("authorStatus", "none"), fallback=display.get("fallback", False),
                fallbackReason=display.get("fallbackReason", ""), **self._identity_fields())
            if (identity_stage is None and not self._transient_cycle_current() and self._gate is not None
                    and self._gate.confirmed_id == map_id and self._map_id == map_id):
                # A selected static sheet is not aligned pixels or a durable
                # identity promotion. Keep its provisional metadata explicit.
                self._reference_sheet = reference_sheet
                self._reference_capture_context = captured.context
            if not self._capture_is_current(captured.context, capture_generation):
                self.invalidate("capture-context-changed")
                return self._receipt(action, payload, success=False, reason="capture-context-changed", stale=True)
        with self._lock:
            transient=self._transient_cycle_current()
        if transient:
            projected=self._try_transient_floor_projection(payload,cancelled,engine,captured,frame,floor,capture_generation)
            if projected is not None:return projected
            with self._lock:
                if not obsolete() and frame_id==self._frame_id:
                    self._reference_sheet,self._reference_capture_context=reference_sheet,captured.context
            return self._receipt(action,payload,success=False,reason="transient-projection-unavailable",fitAttempted=False)
        self._set_pose_phase("pending", "current-frame-fit", map_id, floor, frame_id, frame_generation)
        with self._lock:
            steady_context = self._steady_context()
        host = _AlignmentHost(engine, group, self._settings, steady=(self._steady, steady_context,
            lambda context: not obsolete() and self._steady_is_current(context, captured),
            self._steady_signature(captured)))
        fit = host.fit(frame, map_id, floor, obsolete, entry_candidate) if entry_candidate is not None else host.fit(frame, map_id, floor, obsolete)
        if obsolete():
            return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
        if not fit.get("success"):
            reason = str(fit.get("reason", "pose-fit-failed"))
            reopened = self._consume_identity_conflict(fit, engine=engine, epoch=epoch, captured=captured,
                capture_generation=capture_generation, frame_id=frame_id, frame_generation=frame_generation,
                cycle=cycle, map_id=map_id, floor=floor)
            self._set_pose_phase("rejected", reason, map_id, floor, frame_id, frame_generation)
            return self._receipt(action, payload, success=False, reason=reason, identityReopened=reopened)
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
            previous_group = copy.deepcopy(group) if group is not None else None
        target_group = self._stage_round_group_commit(engine, previous_group, map_id, floor, canonical,
            render_matrix, display, frame, captured, cycle, frame_generation, residual)
        if target_group is None:
            reason = "round-group-pose-commit-rejected"
            self._set_pose_phase("rejected", reason, map_id, floor, frame_id, frame_generation)
            return self._receipt(action, payload, success=False, reason=reason)
        with self._lock:
            if display_generation != getattr(self, "_display_generation", 0):
                return self._receipt(action, payload, success=False, reason="stale-display-settings", stale=True)
            if (group_intent_generation != self._group_intent_generation
                    or manual_choice is None and self._group_choice_inflight is not None):
                return self._receipt(action, payload, success=False, reason="manual-group-intent-superseded-work", stale=True)
            if obsolete() or frame_id != self._frame_id or cycle != self._cycle or floor != self._floor:
                return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
            if not self._capture_is_current(captured.context, capture_generation):
                self.invalidate("capture-context-changed")
                return self._receipt(action, payload, success=False, reason="capture-context-changed", stale=True)
            core = self._copy_pose_core(self._core) if identity_stage is not None else self._core
            self._seed_group_core(core,group)
            core.begin_observation(cycle_id=cycle, map_id=map_id, floor=floor,
                                         frame_generation=frame_generation, frame_signature=(digest,))
            # This is the full shared-structure sensor result, as in the Tk
            # consumer. Existing pose presence cannot relabel it as a bounded
            # translation recovery; the separate refresh path owns that source.
            candidate = AlignmentCandidate(map_id, floor, canonical,
                "initial-structure-v2", frame_generation,
                confidence=1.0, anchor_count=1, anchor_kind="global", current_frame_verified=True,
                residual_verified=True, global_residual_verified=True,
                translation_only=False,
                metadata=dict(routeProfileId="shared-structure", independentFloorAnchor=True))
            decision = core.adjudicate(candidate)
            if not decision.accepted:
                self._set_pose_phase("rejected", decision.reason, map_id, floor, frame_id, frame_generation)
                return self._receipt(action, payload, success=False, reason=decision.reason)
            token = core.authorize(decision)
            if token is None or not core.commit(token):
                return self._receipt(action, payload, success=False, reason="core-commit-rejected")
            self._seed_group_core(core,target_group)
            rgba = np.ascontiguousarray(display["pixels"][:, :, [2, 1, 0, 3]])
            rgba.setflags(write=False)
            live = self._evidence_kind == "live-client-capture"
            target_render = dict(token=uuid4().hex, epoch=epoch, authorizationId=token.authorization_id,
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
                preferredProfileId=self._profile, effectiveProfileId=display.get("effectiveProfileId", display["routeProfileId"]),
                authorStatus=display.get("authorStatus", "none"), fallback=display.get("fallback", False),
                fallbackReason=display.get("fallbackReason", ""),
                variantId=display.get("variantId"), displayGeneration=display_generation,
                liveAuthorization=live, evidenceKind=self._evidence_kind, **self._identity_fields())
            render_token = target_render["token"]
            self._register_material_proposal(target_render, frame, fit, captured, target_group)
            if identity_stage is not None:
                if manual_choice is None and (self._render is None or not self._display_current(self._render)):
                    return self._receipt(action, payload, success=False, reason="sibling-source-authorization-expired", stale=True)
                self._discard_sibling()
                source_token = self._render["token"] if self._render is not None else ""
                target_render.update(requiresMaterialAck=True, sourceToken=source_token, sourceMapId=self._map_id,
                    manualGroupChoice=manual_choice is not None,
                    provisionalMapId=map_id, provisional=True, identityLocked=False, durableMapId="")
                if manual_choice is not None:self._group_member_choice_status="awaiting-material"
                self._pending_sibling = dict(scope=self._sibling_scope(), sourceCore=self._core,
                    sourceToken=source_token, sourceMapId=self._map_id, core=core,
                    group=target_group, identity=identity_stage, render=target_render,
                    reference=reference_sheet, captureContext=captured.context, manualChoice=manual_choice)
            else:
                self._map_id = map_id
                self._gate.select(map_id)
                self._gate.confirm()
                self._group, self._render = target_group, target_render
                self._render_capture_context = captured.context
                self._reference_sheet = reference_sheet
                self._reference_capture_context = captured.context
                self._remember_provisional_reference()
        self._set_pose_phase("valid", "current-frame-pose-authorized", map_id, floor, frame_id, frame_generation)
        if identity_stage is not None:
            self._notify("sibling-material-pending", reason="target-current-pose-awaiting-material", token=render_token,
                sourceMapId=self._map_id, targetMapId=map_id)
            return self._receipt(action, payload, success=True, reason="sibling-material-pending",
                renderToken=source_token, pendingSiblingToken=render_token, diagnosticOnly=not live)
        self._notify("render-ready", reason="current-frame-pose-authorized", token=render_token, unchanged=False)
        if obsolete():
            return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
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
            error = ValueError("live-runtime-requires-full-client-capture")
            def bounds(value, size):
                try:
                    return [int(item) for item in tuple(value)[:size]]
                except (TypeError, ValueError, OverflowError):
                    return None
            error.error_phase = "capture"
            dpi = bounds((getattr(captured.context, "dpi", None),), 1)
            error.capture_diagnostics = dict(frameShape=list(frame.shape[:4]), frameDtype=str(frame.dtype)[:64],
                roiClientBounds=bounds(captured.roi_client_bounds, 4),
                requestedClientBounds=bounds(getattr(captured, "requested_client_bounds", None), 4),
                fullClientSize=bounds(captured.context.full_client_size, 2),
                clientBounds=bounds(getattr(captured.context, "client_screen_bounds", None), 4),
                dpi=dpi[0] if dpi else None)
            raise error
        return frame

    @staticmethod
    def _observe(engine, frame):
        if engine.pack.mode == "difficult":
            result = engine.qt_map_ui.observe(frame)
            floor = str(result["floor"]) if result.get("floor") is not None else None
            return result, floor, dict(result, selectedFloor=int(floor) if floor in ("1", "2") else None,
                scores={}, margin=result.get("highlightDifference", 0.), confidence=1. if floor else 0.,
                layout="difficult-two-buttons", layoutFamily="difficult-two-buttons", difficultObservation=result,
                centers=[g["center"][0] / frame.shape[1] for g in result.get("glyphs", [])],
                controlVertical=dict(regionTop=(min((g["center"][1] for g in result.get("glyphs", [])), default=0)-22)/frame.shape[0],
                                     regionHeight=44/frame.shape[0]))
        from map_interface_observation import detect_map_interface, detect_selected_floor
        floor_read = detect_selected_floor(frame)
        result = detect_map_interface(frame, floor_read=floor_read)
        floor = floor_read.get("selectedFloor")
        floor = "B1" if floor == -1 else str(floor) if floor in (1, 2) else None
        return result, floor, floor_read

    @staticmethod
    def _display_layer(engine, map_id, floor, profile, *, variant_id="variant-01"):
        from client_route_preferences import prepare_display_layer
        return prepare_display_layer(engine, map_id, floor, profile, variant_id=variant_id)


SharedLiveRuntime = LiveRuntime
