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
import itertools
import time
import traceback
from uuid import uuid4

from client_paths import APP_ROOT, WORK_ROOT
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from alignment_core_v2 import AlignmentCoreV2
from structure_pose_solver import StructurePoseSolver as _AlignmentHost
from season_context import normalize_season, context_key
from client_engine import (EngineAdapter, clone_similarity_state, stage_similarity_rank, automatic_identity_candidates, registered_logical_family, ranked_entrance_kind, similarity_evidence_summary, initial_group_display_member, is_registered_identity_group,
                           INITIAL_IDENTITY_BUDGET_SECONDS, registered_family_identity,
                           validate_established_registered_family)
from floor_observation_policy import (MANUAL_FLOOR_HOLD_SECONDS,
                                      read_floor_evidence, decide_floor_transition, game_floor_control_hit)
from client_manual_map import manual_map_thumbnail_source
from g_cycle_observation import (emit as _test_evidence, copy_input as _test_input_copy,
                                 observing as _test_evidence_active)

MAP_RECOVERY_PROBES = 8
MAP_RECOVERY_INTERVAL = .2
MAP_RECOVERY_SECONDS = 2.
# S1 has no entrance shortlist; allow its exact selected-mode catalog scan.
# This cooperative ceiling is not the 500 ms latency target.
S1_STRUCTURE_CANDIDATE_BUDGET_SECONDS = 4.5
CANDIDATE_CONTROL_ACTIONS = ("next_candidate", "previous_candidate", "select_candidate",
    "reselect_map", "map_reselection", "confirm_route")


def _display_rgba(layer):
    """Use the provider's immutable native material, or convert its BGRA layer."""
    if 'rgbaPixels' in layer:
        return layer['rgbaPixels']
    import numpy as np
    pixels = np.ascontiguousarray(layer['pixels'][:, :, [2, 1, 0, 3]])
    pixels.setflags(write=False)
    return pixels


def _prepare_alignment_host(engine, group, settings, map_id, floor, *, frame=None,
                            steady=None, cancelled=None, usable_fit_callback=None):
    """Keep resource and temporal-state preparation outside numerical fitting."""
    if getattr(engine, 'qt_native_base', None) is None:
        return (_AlignmentHost(engine, group, settings, steady=steady,
                    usable_fit_callback=usable_fit_callback),
                dict(kind='legacy-provider'), None)
    from client_providers import prepare_pose_solver
    host, preparation = prepare_pose_solver(engine, map_id, floor, group=group,
        settings=settings, cancelled=cancelled, usable_fit_callback=usable_fit_callback)
    receipt = None
    if steady is not None:
        adapter, context, current, signature = steady
        host.steady_input, generation = adapter.capture_prepared_input(
            host, frame, map_id, floor, context=context, is_current=current,
            cancelled=cancelled, display_signature=signature,
            enabled=bool(settings.get('idvb0059Enabled', True)))
        receipt = adapter, generation
    return host, preparation, receipt


def _alignment_host_current(engine, host, steady_receipt=None):
    from client_providers import pose_inputs_current
    return bool((not hasattr(host, 'inputs') or pose_inputs_current(engine, host.inputs))
        and (steady_receipt is None or steady_receipt[0].generation_current(steady_receipt[1])))


def _engine_resource_current(engine):
    """Prepared resource ownership only; never load or validate files here."""
    current = getattr(engine, 'qt_resource_context_current', None)
    return current is None or bool(current())


def _fit_alignment_host(host, frame, map_id, floor, cancelled, entry_candidate=None, *,
                        observation_bounds=None, on_usable_fit=None, timing=None):
    """Consume numerical progress on the runtime worker, outside the solver."""
    from structure_pose_solver import PreparedStructurePoseSolver
    options = {}
    if entry_candidate is not None:
        options['entry_candidate'] = entry_candidate
    if observation_bounds is not None:
        options['observation_bounds'] = observation_bounds
    def compute(stop):
        started = time.perf_counter()
        try:
            return host.fit(frame, map_id, floor, stop, **options)
        finally:
            if timing is not None:
                timing.update(elapsedMs=(time.perf_counter()-started)*1000,
                    kind='prepared-ram' if isinstance(host, PreparedStructurePoseSolver) else 'legacy-combined')
    if not isinstance(host, PreparedStructurePoseSolver) or on_usable_fit is None:
        return compute(cancelled)
    from concurrent.futures import ThreadPoolExecutor
    from queue import Empty, Full, Queue
    offers, ready, stopped = Queue(maxsize=1), threading.Event(), threading.Event()
    previous = host.usable_fit_callback
    def offer(fit):
        if not stopped.is_set():
            try:
                offers.put_nowait(copy.deepcopy(fit))
            except Full:
                return
            ready.set()
    host.usable_fit_callback = offer
    try:
        # One synchronous runtime operation owns this worker and joins it on
        # every exit. Cancellation cannot accumulate detached fit tasks.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='structure-pose') as pool:
            future = pool.submit(compute, lambda: stopped.is_set() or cancelled())
            future.add_done_callback(lambda _: ready.set())
            try:
                while True:
                    ready.wait(.05)
                    ready.clear()
                    while True:
                        try:
                            first = offers.get_nowait()
                        except Empty:
                            break
                        if not cancelled():
                            on_usable_fit(first)
                    if future.done() and offers.empty():
                        return future.result()
            finally:
                stopped.set()
    finally:
        host.usable_fit_callback = previous


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
    def __init__(self, runtime, engine, obsolete, payload=None):
        from map_open_preparation import CAPTURE_READY_PROBE_SECONDS, CAPTURE_VISUAL_DEADLINE_SECONDS
        from client_preparation_diagnostics import PreparationDiagnostics
        self.CAPTURE_READY_PROBE_SECONDS = CAPTURE_READY_PROBE_SECONDS
        self.CAPTURE_VISUAL_DEADLINE_SECONDS = CAPTURE_VISUAL_DEADLINE_SECONDS
        self.runtime, self.engine, self.obsolete = runtime, engine, obsolete
        self.payload = payload if payload is not None else {}
        self.initial_context = None
        self.last_unresolved = None
        self.last_capture = None
        self.last_observation_diagnostic = None
        self.events = []
        self.diagnostics = PreparationDiagnostics(runtime, self.payload)
        self.stop_reason = None
    def _log(self, event, **values):
        if event == 'map-ready-full-unavailable':
            # A failed newer capture cannot be presented as a size rejection.
            self.last_observation_diagnostic = None
        self.events.append(dict(event=event, **values))
        del self.events[:-24]
        if event == "map-ready-begin":
            self.diagnostics.emit("begin", "map-open-preparation",
                deadlineSeconds=self.CAPTURE_VISUAL_DEADLINE_SECONDS,
                probePeriodSeconds=self.CAPTURE_READY_PROBE_SECONDS)
        elif event == "map-ready-stopped":
            self.stop_reason = values.get("reason")
    def observation_diagnostic(self):
        row = self.last_observation_diagnostic
        if (row is None or self.obsolete()
                or not self.runtime._capture_is_current(row[0], row[1])):
            return {}
        return dict(row[2])

    def _map_ready_context(self):
        return self.runtime._epoch
    def _map_ready_current(self, token, context, cancelled):
        return context == self.runtime._epoch and not cancelled.is_set()
    def _capture_map_open_full_probe(self, hwnd, token, context, capture_context, deadline_at,
                                     cancelled, **kwargs):
        from session_state_machine import verified_map_hotkey_frame
        from client_preparation_diagnostics import capture_metadata, exception_metadata
        if not self._map_ready_current(token, context, cancelled):
            return None, True
        self.last_observation_diagnostic = None
        self.diagnostics.probes += 1
        self.last_capture = None
        capture_generation = self.runtime._capture_generation()
        started = time.perf_counter()
        self.diagnostics.emit("capture-start", "capture-requested", captureGeneration=capture_generation)
        try:
            captured = self.runtime._capture_frame(cancelled, attempt_id=self.payload.get("_diagnosticAttemptId"))
            frame = self.runtime._full_frame(captured)
        except Exception as error:
            self.diagnostics.capture_errors += 1
            self.diagnostics.last_outcome = "capture-exception"
            self.diagnostics.emit("capture-result", "capture-exception", capture=capture_metadata(error=error),
                exception=exception_metadata(error), elapsedMs=round((time.perf_counter()-started)*1000, 3))
            raise
        self.diagnostics.emit("capture-result", "frame-captured", capture=capture_metadata(captured),
            elapsedMs=round((time.perf_counter()-started)*1000, 3))
        if self.initial_context is None:
            self.initial_context = captured.context
        if captured.context != self.initial_context or not self._map_ready_current(token, context, cancelled):
            self.diagnostics.last_outcome = "capture-context-changed" if captured.context != self.initial_context else "obsolete"
            self.diagnostics.emit("observation-result", self.diagnostics.last_outcome)
            return None, True
        observed_at = time.perf_counter()
        try:
            observation, floor, floor_read = self.runtime._observe(self.engine, frame)
        except Exception as error:
            self.diagnostics.last_outcome = "observation-exception"
            self.diagnostics.emit("observation-result", "observation-exception", exception=exception_metadata(error),
                elapsedMs=round((time.perf_counter()-observed_at)*1000, 3))
            raise
        if (self._map_ready_current(token, context, cancelled)
                and self.runtime._capture_is_current(captured.context, capture_generation)):
            from map_interface_observation import map_preparation_diagnostic
            self.last_observation_diagnostic = (captured.context, capture_generation,
                map_preparation_diagnostic(frame, observation, floor_read, mode=self.engine.pack.mode))
            self.last_capture = (captured, capture_generation)
        map_detected = observation.get("detected") is True
        self.last_unresolved = ((captured, observation, floor, capture_generation, floor_read)
                                if map_detected and floor is None else None)
        # The shared loop supplies the original cadence/deadline. Qt requires a
        # current explicit floor before ending preparation; it never converts a
        # remembered floor into fresh display evidence during the fade.
        s1_manual = self.runtime._manual_alignment and self.runtime._s1_floor_observer_available(self.engine)
        ready = (observation.get("mapOpen") is True if self.runtime._season == "s1"
                 else verified_map_hotkey_frame(observation, floor_read))
        usable = ready and (floor is not None or s1_manual)
        before_deadline = time.perf_counter() < deadline_at
        outcome = ("prepared-current-frame" if before_deadline else "ready-after-deadline") if usable else (
            "current-floor-unavailable" if map_detected and floor is None else "map-not-ready")
        self.diagnostics.last_outcome = outcome
        self.diagnostics.emit("observation-result", outcome,
            observation=self.diagnostics.observation(observation, floor, floor_read, ready),
            elapsedMs=round((time.perf_counter()-observed_at)*1000, 3), beforeDeadline=before_deadline)
        if usable and before_deadline:
            return (captured, observation, floor, capture_generation, floor_read), False
        return None, False




class LiveRuntime:
    """One orchestration for both modes; capture provider is explicitly injected.

    evidence_kind is fixed by the composition root, never accepted from action
    payloads. Use ``synthetic``/``recorded`` for fixture capture providers: their
    real core transactions remain offline and never expose live authority.
    """
    def __init__(self, *, engine_factories, capture_provider=None,
                 allowed_read_root=None, evidence_kind="unverified", clock=None, diagnostics=None,
                 recorded_s1_provisional_display=False, recorded_s1_streaming=False,
                 recorded_s1_scan_order="natural", recorded_s1_scan_budget=S1_STRUCTURE_CANDIDATE_BUDGET_SECONDS,
                 recorded_s1_priority_index=None, s1_candidate_purpose=None):
        self._diagnostics = diagnostics
        self.g_cycle_evidence = None  # Explicit process-only test mode; no normal writer.
        from s1_candidate_purpose import native_candidate_purpose_is_valid, private_native_candidate_purpose_is_valid, native_round_lock_purpose_is_valid, native_final_round_lock_purpose_is_valid
        self._native_s1_candidate_preview = bool(
            evidence_kind == "live-client-capture"
            and native_candidate_purpose_is_valid(s1_candidate_purpose)
            and callable(getattr(capture_provider, "is_current", None)))
        # Scoped cumulative evidence is independent of private trace permission.
        self._native_s1_round_lock = bool(self._native_s1_candidate_preview
            and native_round_lock_purpose_is_valid(s1_candidate_purpose))
        self._native_s1_private_experiment = bool(self._native_s1_candidate_preview
            and private_native_candidate_purpose_is_valid(s1_candidate_purpose))
        self._native_s1_final_round_lock = bool(self._native_s1_round_lock
            and native_final_round_lock_purpose_is_valid(s1_candidate_purpose))
        # Explicit research opt-in cannot grant live capture readiness.
        self._recorded_s1_provisional_display = bool(recorded_s1_provisional_display and evidence_kind == "recorded")
        import math
        self._recorded_s1_streaming = bool(recorded_s1_streaming and evidence_kind == "recorded")
        if recorded_s1_scan_order not in ("natural", "history", "index"):
            raise ValueError("invalid-recorded-scan-order")
        if recorded_s1_scan_budget is not None and (type(recorded_s1_scan_budget) not in (int, float)
                or not math.isfinite(recorded_s1_scan_budget) or recorded_s1_scan_budget <= 0):
            raise ValueError("invalid-recorded-scan-budget")
        self._recorded_s1_scan_order = recorded_s1_scan_order
        self._recorded_s1_scan_budget = recorded_s1_scan_budget
        self._recorded_s1_priority_index = copy.deepcopy(recorded_s1_priority_index)
        self._stream_priority_handles = {}
        self._stream_owner = None
        from client_failure_samples import FailureSampleRecorder
        self._failure_samples = FailureSampleRecorder()
        self._failure_capture_stamp = None
        self._failure_control_observation = None
        self._failure_notice_limits = {}
        if evidence_kind not in ("live-client-capture", "synthetic", "recorded", "unverified"):
            raise ValueError("unsupported capture evidence kind")
        self._offline = EngineAdapter(engine_factories=engine_factories,
                                      allowed_read_root=allowed_read_root or WORK_ROOT, clock=clock)
        from client_fit_reporting import FitReporting
        self._fit_reporting = FitReporting()
        self._monotonic = clock or time.monotonic
        self._factories, self._capture = engine_factories, capture_provider
        self._evidence_kind = evidence_kind
        self._core = AlignmentCoreV2(clock=clock)
        self._lock, self._work = threading.RLock(), threading.RLock()
        self._epoch, self._round, self._cycle, self._frame_generation = 0, 0, 0, 0
        self._request_lifetime_generation = 0
        self._dispatch_serials = itertools.count(1)
        self._dispatch_owners = threading.local()
        self._dispatch_cutoff = 0
        self._season = "s0"
        self._mode, self._profile, self._map_id, self._floor = "nightmare", "shared-structure", "", ""
        self._frame_id = ""
        self._active, self._map_open = False, False
        self._round_preparing = False
        self._prepared_round_ready = False
        self._last_map_input_sequence = 0
        self._map_input_observation = None
        self._map_recovery = None
        self._transient_drag = self._transient_cycle = None
        self._capture_pending = False
        self._capture_pending_owner = None
        self._probe_misses = 0
        self._aligned_visible = True
        self._event_callback = None
        self._event_delivery_error = ""
        self._group = self._render = self._engine = None
        self._render_capture_context = None
        self._reference_sheet = None
        self._reference_capture_context = None
        self._settings = {}
        self._settings_generation = 0
        self._s1_solver_policy_generation = 0
        # Latest full-client frame in which the map was seen open; only the
        # floor-region editor reads it. A reference, never a copy per frame.
        self._latest_map_frame = None
        self._gate = None
        self._candidate_rows = []
        self._candidate_edit = False
        self._selection_pending = False
        self._manual_floor = None
        self._manual_alignment = False
        self._s1_floor_source = "unknown"
        self._s1_raw_floor = None
        self._s1_floor_reason = "not-observed"
        self._manual_capture_owner = None
        self._observed_floor = ""
        self._last_observation = None
        self._pointer_context_snapshot = None
        self._manual_map_thumbnail_cache = {}
        from client_idvb0059_adapter import Idvb0059Adapter
        self._steady = Idvb0059Adapter()
        from s1_private_experiment import LatestEvidenceQueue
        self._s1_private_jobs = LatestEvidenceQueue()
        self._private_observation_sequence = 0
        self._reset_identity()
        self._reset_floor_policy()
        self._message = "Bind a full client capture, then begin a round explicitly."

    @staticmethod
    def _cancelled(cancelled):
        return EngineAdapter._cancelled(cancelled)

    def _context(self):
        return dict(season=self._season, contextKey=context_key(self._mode, self._season), mode=self._mode, profileId=self._profile, roundId=self._round, frameId=self._frame_id)

    def _s1_candidate_available(self, report=None, engine=None):
        """Selected-context candidate purpose; never general live readiness."""
        if self._season != "s1" or not (
                self._evidence_kind == "recorded" or self._native_s1_candidate_preview):
            return False
        capability = ((report or {}).get("candidateObservation") or
            getattr(getattr(engine, "qt_native_context", None), "structure_candidate_capability", {}))
        if not isinstance(capability, dict) or capability.get("candidateReady") is not True:
            return False
        return (not self._native_s1_candidate_preview or (
            capability.get("contract") == "s1-recorded-structure-candidate-floor-v2"
            and tuple(capability.get("observedFloors", ())) == (-1, -2, -3, -4)))

    def _candidate_preview_requested(self):
        return self._recorded_s1_provisional_display or self._native_s1_candidate_preview

    def _structure_candidate_available(self, report=None, engine=None):
        """Current-frame candidate purpose, separate from formal identity rank."""
        if self._season == 's1':
            return self._s1_candidate_available(report, engine)
        if self._season != 's0' or self._mode != 'difficult':
            return False
        if report is not None and report.get('dataReady') is not True:
            return False
        capability = ((report or {}).get('candidateObservation') or
            getattr(getattr(engine, 'qt_native_context', None), 'structure_candidate_capability', {}))
        return isinstance(capability, dict) and capability.get('candidateReady') is True

    def _candidate_streaming_requested(self):
        return self._recorded_s1_streaming or self._native_s1_candidate_preview

    def _effective_map_selection_mode(self):
        # This legacy preference belongs to S0. Keep its stored value intact
        # across season changes, but never strand S1 waiting for a hidden picker.
        # Auto selection is not a capability grant: purpose/resource gates and
        # the separate explicit manual-alignment session remain unchanged.
        return "auto" if self._season == "s1" else self._settings.get("mapSelectionMode", "auto")

    def _s1_provisional_enabled(self):
        return bool(self._candidate_preview_requested() and self._season == "s1"
            and not self._durable_map_id
            and not self._manual_alignment)

    def _candidate_provisional_enabled(self, engine=None):
        """Current-ticket geometry can display without owning map identity.

        S1 keeps its explicit purpose qualification. Imported S0 uses its
        selected-mode scanner capability; no S1 identity/pool policy is granted.
        """
        if self._season == "s1":
            return self._s1_provisional_enabled()
        engine = self._engine if engine is None else engine
        return bool(self._season == "s0" and self._mode in ("difficult", "nightmare")
            and engine is not None and not self._map_id and not self._durable_map_id
            and not self._manual_alignment and not self._manual_selection_unlocked()
            and self._observation_stream_enabled(engine))

    def _candidate_probe_available(self, engine=None):
        engine = self._engine if engine is None else engine
        return bool(self._candidate_provisional_enabled(engine) and (
            self._s1_candidate_available(engine=engine) if self._season == 's1'
            else self._observation_stream_enabled(engine)))

    def _manual_selection_unlocked(self):
        """Manual mode waits for the player's pick instead of ranking identity."""
        return self._effective_map_selection_mode() == "manual" and not self._map_id

    def _s1_floor_observer_available(self, engine=None):
        """Separate selected-context floor capability, not live/identity readiness."""
        engine = self._engine if engine is None else engine
        if (self._season != "s1" or engine is None or self._mode not in ("difficulty2", "difficult", "nightmare")
                or getattr(engine.pack, "mode", None) != self._mode
                or tuple(getattr(engine, "identity_floors", ())) != ("-1", "-2", "-3", "-4")):
            return False
        capability = getattr(getattr(engine, "qt_native_context", None), "floor_observation_capability", {})
        return (isinstance(capability, dict)
                and capability.get("contract") == "s1-floor-only-manual-map-use"
                and capability.get("manualMapObservation") is True
                and tuple(capability.get("automaticFloors", ())) == (-1, -2, -3, -4))

    def _current_floor_source(self):
        if self._manual_alignment and self._s1_floor_observer_available():
            return self._s1_floor_source
        return "manual" if self._manual_floor else "automatic"

    def _clear_candidates(self):
        self._reset_identity()
        self._reference_sheet = None
        self._reference_capture_context = None
        self._gate = None
        self._candidate_rows = []
        self._candidate_edit = False
        self._selection_pending = False
        self._manual_floor = None
        self._s1_floor_source = "unknown"
        self._s1_raw_floor = None
        self._s1_floor_reason = "not-observed"
        self._observed_floor = ""
        self._last_observation = None
        self._pointer_context_snapshot = None
        self._reset_floor_policy()

    def _reset_identity(self):
        self._s1_round_map_id = ""
        self._clear_candidate_reference()
        self._s1_candidate_preference = None
        self._s1_candidate_pose_hint = None
        self._clear_private_pool()
        old_structure = getattr(self, "_s1_structure_session", None)
        if old_structure is not None:
            old_structure.end_round()
        self._s1_structure_session = self._s1_structure_ticket = None
        for row in getattr(self, "_candidate_rows", ()):
            row["structureSupported"] = False
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
        self._provisional_display_map_id = ""
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

    @staticmethod
    def _s1_solver_policy(settings):
        # Only shared-solver settings own S1 identity evidence. The legacy
        # mapSelectionMode is an S0 preference, ignored by S1 auto selection.
        # Author, window, opacity, keys and labels are presentation/interaction.
        # New solver controls must join this explicit signature.
        return (bool(settings.get("poseIndexCandidatesEnabled", settings.get("difficultGridEnabled", False))),
            bool(settings.get("idvb0059Enabled", True)))

    def _s1_native_identity_scope(self, catalog, capture_context):
        return (self._round, self._season, self._mode, id(self._engine),
            catalog, self._s1_solver_policy_generation,
            self._capture_generation(), capture_context)

    def _s1_candidate_preference_scope(self, captured, catalog):
        # Stable native identity owner, independent of display preference.
        # Every new frame/floor still needs its own fit; historical identity or
        # pose proposals never license old pixels. Material owners separately
        # retain profile/settings/display-generation and current-ticket guards.
        return self._s1_native_identity_scope(catalog, captured.context)

    def _register_material_proposal(self, render, frame, fit, captured, group, *, accepted=False,
                                    candidate_only=False):
        # Worker only. Capture frames are already owned immutable BGR arrays.
        token = render["token"]
        self._remember_proposals[token] = dict(context=self._steady_context(), frame=frame,
            fit=fit, captured=captured, group=None if candidate_only else group,
            mapId=render["mapId"], floor=render["floor"], accepted=accepted)
        if candidate_only and self._s1_structure_session is not None:
            session = self._s1_structure_session
            self._remember_proposals[token].update(candidateOnly=True, candidateSession=session,
                candidateTicket=self._s1_structure_ticket,
                candidateCaptureGeneration=render["captureGeneration"],
                candidateScope=self._s1_candidate_preference_scope(captured, session.catalog))
        while len(self._remember_proposals) > 3:
            self._remember_proposals.pop(next(iter(self._remember_proposals)))

    def acknowledge_render_material(self, token):
        """Accept a current material receipt; coverage work remains off the UI."""
        with self._lock:
            proposal = self._remember_proposals.get(token)
            if (proposal is None or proposal["accepted"] or self._render is None
                    or self._render["token"] != token or not self._display_current(self._render) or self._render.get("transient")
                    or self._render.get("displayGeneration") != getattr(self, "_display_generation", 0)
                    or proposal["context"] != self._steady_context() or proposal["group"] is not self._group):
                return dict(success=False, reason="material-token-expired-or-already-accepted", token=token)
            if proposal.get("candidateOnly"):
                session = proposal["candidateSession"]
                ticket = proposal["candidateTicket"]
                if (not self._capture_is_current(proposal["captured"].context, proposal["candidateCaptureGeneration"])
                        or self._render is None or self._render.get("token") != token
                        or not self._candidate_provisional_enabled() or session is not self._s1_structure_session
                        or ticket != self._s1_structure_ticket
                        or proposal["candidateScope"] != self._s1_candidate_preference_scope(proposal["captured"], session.catalog)
                        or not session.choose_candidate(proposal["mapId"], ticket).get("success")):
                    return dict(success=False, reason="material-token-expired-or-already-accepted", token=token)
                # This ACK may retain a geometry proposal for the same owned
                # round/window. It grants no future pose, identity or group.
                self._s1_candidate_preference = (proposal["candidateScope"], proposal["mapId"])
                from s1_pose_prior import AcknowledgedPose, isotropic_matrix
                from s1_structure_selection import verified_pose
                matrix = isotropic_matrix(proposal["fit"].get("matrixSourceToClient"))
                self._s1_candidate_pose_hint = (AcknowledgedPose(
                    self._mode, session.catalog.resource_key, proposal["mapId"], str(proposal["floor"]),
                    proposal["candidateScope"], self._render["frameId"], tuple(map(tuple, matrix)), True, True)
                    if matrix is not None and verified_pose(proposal["fit"]) else None)
            proposal["accepted"] = True
            candidate_only = proposal.get("candidateOnly", False)
        if candidate_only:
            self._notify("candidate-material-accepted", token=token, reason="current-material-accepted")
            return dict(success=True, reason="current-material-accepted", token=token, needsWorker=False)
        self._notify("render-material-accepted", token=token, reason="current-material-accepted")
        return dict(success=True, reason="material-remember-queued", token=token, needsWorker=True)

    def _remember_render_material(self, token, payload, cancelled):
        with self._lock:
            proposal = self._remember_proposals.get(token)
            if (proposal is None or proposal.get("candidateOnly") or not proposal["accepted"] or proposal.get("consumed")
                    or proposal["group"] is not self._group
                    or not self._steady_is_current(proposal["context"], proposal["captured"])):
                return self._receipt("remember_render_material", payload, success=False, reason="material-context-expired")
            proposal["consumed"] = True
            engine, group, settings = self._engine, self._group, dict(self._settings)
        host, _, _ = _prepare_alignment_host(engine, group, settings,
            proposal["mapId"], proposal["floor"], cancelled=lambda: self._cancelled(cancelled))
        current = lambda context: (not self._cancelled(cancelled)
            and _alignment_host_current(engine, host)
            and self._steady_is_current(context, proposal["captured"]))
        remembered = self._steady.remember_committed(host,
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
                # The original repeated-evidence policy withdrew this identity;
                # its material is no longer an ordinary failed-frame fallback.
                self._revoke_render("identity-current-structure-conflict")
                self._notify("group-state", reason="repeated-current-structure-contradictions",
                             legacyEvents=[dict(event=event, **values) for event, values in events])
            return reopened

    def _identity_fields(self):
        native = self._identity_native
        grouped = is_registered_identity_group(native)
        held = self._manual_group_choice is not None
        phase = ("MANUAL_MEMBER_CONFIRMED" if held else "MEMBER_VERIFIED" if self._durable_map_id
                 else "GROUP_LOCKED" if grouped else "UNLOCKED")
        provisional_id = self._provisional_display_map_id
        pool = getattr(self, "_s1_private_pool", None)
        formal_lock = self._s1_round_locked_map()
        return dict(identityPhase="MEMBER_VERIFIED" if formal_lock else "CANDIDATE_DISPLAY" if provisional_id else phase, identitySource=self._identity_source,
            candidateFullImage=bool(provisional_id), candidateDisplayOnly=bool(provisional_id),
            identityLocked=bool(self._durable_map_id or formal_lock), durableMapId=self._durable_map_id or formal_lock,
            s1RoundLockEnabled=bool(self._native_s1_round_lock and self._season == "s1" and not self._manual_alignment),
            s1FormalRoundLock=bool(self._native_s1_final_round_lock
                and self._season == "s1" and not self._manual_alignment),
            s1RemainingCandidateCount=1 if formal_lock else len(pool.remaining) if pool is not None else 0,
            provisionalMapId=provisional_id or ((native.provisional_member_id or self._map_id) if grouped else ""),
            provisional=not bool(formal_lock) and (bool(provisional_id) or (grouped and not bool(self._durable_map_id))), groupLocked=grouped,
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

    def _s1_round_locked_map(self):
        # Logical identity belongs to the round, not a capture/settings scope.
        # It never grants a current pose or pixels. Scoped pools and all their
        # resource/capture/material checks can still be discarded independently.
        return (self._s1_round_map_id if self._native_s1_final_round_lock
            and self._season == "s1" and self._active and not self._manual_alignment else "")

    def _identity_context(self):
        return (self._mode, self._cycle, str(self._floor), self._frame_generation, self._epoch)

    def _attach_identity_engine(self, engine):
        from client_providers import bind_identity_context
        def current(field):
            with self._lock:
                return getattr(self, field) if engine is self._engine else None
        bind_identity_context(engine, priority=lambda: current("_identity_priority"),
                              context=lambda: current("_identity_prior"))

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


    def _observation_stream_enabled(self, engine):
        # Cancellation polls this predicate. Use prepared capabilities here;
        # the scanner and fit publication boundaries own resource revalidation.
        scan = getattr(engine, 'qt_structure_scan', None) or getattr(engine, 'qt_s1_structure_scan', None)
        return bool(callable(scan) and (
            self._season == 's1' and not self._manual_alignment
            or self._season == 's0' and getattr(engine, 'qt_native_base', None) is not None
            and (getattr(engine, 'qt_rank_ready', False) or getattr(engine, 'qt_structure_scan_ready', False))))

    def _stream_observation(self, action, payload, cancelled, engine, epoch,
                               captured, frame, floor, digest, capture_generation,
                               frame_generation, frame_id, cycle, snapshot):
        """One captured ticket streams material; finalization never chooses again."""
        from s1_structure_selection import StructureCatalog, StructureEvidenceSession, StructureCandidateScanner
        from s1_stream_priority import candidate_order
        catalog = StructureCatalog.from_engine(engine)
        provisional_display = self._candidate_provisional_enabled(engine)
        shape = tuple(frame.shape)
        dimensions = tuple(captured.context.full_client_size)
        if snapshot is None or shape[:2] != (dimensions[1], dimensions[0]):
            return self._receipt(action, payload, success=False, reason="stream-frame-shape-mismatch")
        with self._lock:
            if (epoch != self._epoch or frame_id != self._frame_id or not self._observation_stream_enabled(engine)
                    or not self._identity_observation_current()):
                return self._receipt(action, payload, success=False, reason="stale-stream-observation", stale=True)
            session = self._s1_structure_session
            if session is None or session.catalog != catalog:
                if session is not None:
                    session.end_round()
                session = self._s1_structure_session = StructureEvidenceSession(catalog, str(self._round))
            prior = session.priority_supported(str(floor))
            preference_scope = self._s1_candidate_preference_scope(captured, catalog)
            if self._s1_candidate_preference is not None and self._s1_candidate_preference[0] != preference_scope:
                self._s1_candidate_preference = None
                self._clear_private_pool()
            preferred = self._s1_candidate_preference[1] if self._s1_candidate_preference is not None else None
            prior_source = self._s1_candidate_pose_hint if (
                self._s1_candidate_preference is not None and payload.get("forceCatalogue") is not True
                and action not in ("reselect_map", "map_reselection")) else None
            private_prepare = getattr(engine, "qt_s1_private_coordinate_prepare", None)
            private_enabled = catalog.season == 's1' and self._native_s1_round_lock and callable(private_prepare)
            round_map_id = self._s1_round_locked_map()
            if round_map_id and (not private_enabled or round_map_id not in catalog.map_ids):
                # A changed/missing catalogue cannot silently substitute a map.
                # Resource replacement still ends the unsafe runtime context.
                self._clear_candidate_reference()
                self._clear_private_pool()
                return self._receipt(action, payload, success=False,
                    reason="locked-map-resource-unavailable", posePending=True)
            pool = getattr(self, "_s1_private_pool", None)
            if private_enabled and (pool is None or pool.scope != preference_scope):
                from s1_private_experiment import PrivateRoundPool
                pool = self._s1_private_pool = PrivateRoundPool(preference_scope, catalog.map_ids,
                    retain_locked_identity=self._native_s1_final_round_lock)
                if round_map_id:
                    # This is the same already-confirmed round identity, not a
                    # fresh exclusion claim about a newly captured frame.
                    pool.remaining = (round_map_id,)
                    pool.locked_id = round_map_id
                    pool.eliminated = set(catalog.map_ids) - {round_map_id}
                self._notify("s1-candidate-pool-changed", reason="s1-pool-created",
                    **pool.snapshot(), privateResourceDigest=catalog.resource_key)
            recovery_revision = pool.recovery_revision if private_enabled else None
            recovery_scan = bool(private_enabled and pool.recovery_pending)
            if recovery_scan:
                preferred = prior_source = None
            if private_enabled:
                preferred = pool.locked_id or (pool.remaining[0] if len(pool.remaining) == 1 else preferred)
                if preferred not in pool.remaining:
                    preferred = None
            ticket = session.begin_observation(str(floor))
            self._s1_structure_ticket = ticket
            self._automatic_candidate_rows = []
            owner = object()
            self._stream_owner = owner
            settings_generation = self._settings_generation
            display_generation = getattr(self, "_display_generation", 0)
            intent_generation = self._group_intent_generation
            round_id = self._round
            identity_snapshot = (self._identity_native, self._identity_context(), intent_generation)
            priority_handles = dict(self._stream_priority_handles)
            self._private_observation_sequence += 1
            private_sequence = self._private_observation_sequence
        first_result = None
        entry_selection, entry_seed, entry_published, entry_preserved = None, None, False, False
        attempted = set()
        last_material_reason = "no-current-supported-candidates"
        started = time.monotonic()
        scan_budget = (self._recorded_s1_scan_budget if provisional_display and catalog.season == 's1' else
            S1_STRUCTURE_CANDIDATE_BUDGET_SECONDS if catalog.season == 's1' else INITIAL_IDENTITY_BUDGET_SECONDS)
        deadline = time.perf_counter() + scan_budget if scan_budget is not None else None
        private_report = None
        private_material_ms = 0.0
        scan_plan, scan_revision = (), None
        pose_priors = {}
        prior_binding = {}

        def owns_ticket():
            return (self._stream_owner is owner and epoch == self._epoch
                and frame_id == self._frame_id and round_id == self._round and cycle == self._cycle
                and engine is self._engine and session is self._s1_structure_session
                and ticket == self._s1_structure_ticket)

        def current():
            return (owns_ticket() and not self._cancelled(cancelled)
                and self._active and self._map_open and self._observation_stream_enabled(engine)
                and _engine_resource_current(engine)
                and settings_generation == self._settings_generation
                and display_generation == getattr(self, "_display_generation", 0)
                and intent_generation == self._group_intent_generation
                and floor == self._floor == self._observed_floor
                and capture_generation == self._capture_generation()
                and self._capture_is_current(captured.context, capture_generation)
                and self._identity_observation_current()
                and self._last_observation["digest"] == digest
                and tuple(self._last_observation["frame"].shape) == shape
                and tuple(self._last_observation["captured"].context.full_client_size) == dimensions)

        def obsolete():
            with self._lock:
                return not current()

        def withdraw(reason):
            with self._lock:
                # Complete old writes before notification: its consumer may
                # synchronously open/capture a successor under the RLock.
                if not owns_ticket():
                    return
                row = self._render
                revoke_pixels = (row is not None and row.get("epoch") == epoch
                    and row.get("frameId") == frame_id and row.get("candidateDisplayOnly")
                    and not self._durable_map_id)
                session.revoke_current()
                self._s1_structure_ticket = None
                self._automatic_candidate_rows = []
                for candidate in self._candidate_rows:
                    candidate["structureSupported"] = False
                if revoke_pixels and not self._display_current(row):
                    self._revoke_render(reason)  # Last mutation; notification may reenter.

        def publish_entry(evidence):
            nonlocal entry_selection, entry_seed, entry_published, entry_preserved, intent_generation, first_result
            with self._lock:
                if (not current() or evidence.get('frameKey') != digest
                        or not session.commit_entry_evidence(evidence, ticket)):
                    return False
                entry_selection, entry_seed, stale = self._commit_identity_rows(
                    engine, evidence['rows'], frame, floor, epoch, frame_id, cancelled,
                    identity_snapshot=identity_snapshot)
                if stale or not owns_ticket():
                    return False
                # The reducer checks the intent again after synchronous
                # notifications. Adopt only its own completed group transition.
                intent_generation = self._group_intent_generation
                if not current():
                    return False
                entry_published = True
                if entry_selection:
                    # A candidate's display receipt is not the entry lock's
                    # material receipt, even when its current pose is reusable.
                    first_result = None
                entry_preserved = bool(entry_selection or self._identity_fields()['groupLocked']
                    or any(row.get('identityHardGatePassed') is True for row in evidence['rows'])
                    or any(row.get('identityKind') == 'group' for row in self._automatic_candidate_rows))
                return entry_preserved

        def publish(report):
            nonlocal first_result, last_material_reason, private_material_ms
            rows = StructureCandidateScanner.rank_rows(report, engine.pack)
            with self._lock:
                if private_enabled:
                    if self._s1_private_pool is not pool or pool.recovery_revision != recovery_revision:
                        # A restored pool is not permission to reuse this scan's
                        # old singleton poses. Keep the owner/ticket untouched.
                        first_result = None
                        last_material_reason = "private-contradiction-recovery"
                        return True
                    rows = [row for row in rows if row["mapId"] in pool.remaining]
                    if (first_result is not None and self._render is not None
                            and self._render.get("mapId") not in pool.remaining):
                        first_result = None
                if (not current() or report.get("frameKey") != digest
                        or not session.commit(report, ticket)):
                    return False
                if entry_preserved:
                    return True
                self._identity_rank_rows = rows
                if not self._provisional_display_map_id:
                    self._identity_source = f"{catalog.season}-structure-candidates"
                self._automatic_candidate_rows = [dict(row, key=row["mapId"], identityKind="candidate") for row in rows]
                self._update_candidates(rows, floor)
                # A list update must not change an already materialized choice.
                if first_result is None and rows and self._gate.selected_id not in report["supportedMapIds"]:
                    self._gate.select(rows[0]["mapId"])
            self._notify("structure-scan-progress", reason="current-ticket-update", streamFrameId=frame_id,
                streamEpoch=epoch, streamRoundId=round_id, streamCycleId=cycle,
                updateSeq=report["updateSeq"], status=report["status"], scanComplete=report["scanComplete"],
                checkedMapIds=report["checkedMapIds"], supportedMapIds=report["supportedMapIds"],
                firstVerifiedPoseElapsedSeconds=report.get("firstVerifiedPoseElapsedSeconds"),
                rawFrameDigest=digest, shapeSnapshotId=snapshot.sha256, frameShape=list(shape))
            for row in rows if provisional_display else ():
                mid = row["mapId"]
                with self._lock:
                    eligible = not private_enabled or (self._s1_private_pool is pool and mid in pool.remaining)
                if first_result is not None or mid in attempted or obsolete() or not eligible:
                    continue
                attempted.add(mid)
                material_started = time.monotonic()
                try:
                    result = self._solve_observation(action, payload, cancelled, engine, None, epoch,
                        captured, frame, floor, digest, capture_generation, frame_generation,
                        frame_id, cycle, mid, structure_ticket=(session, ticket),
                        structure_current=lambda: not obsolete() and (not private_enabled
                            or self._s1_private_pool is pool and mid in pool.remaining
                            and pool.recovery_revision == recovery_revision))
                except MemoryError:
                    raise
                except Exception as error:
                    last_material_reason = type(error).__name__ + ":" + str(error)
                    continue
                private_material_ms += (time.monotonic() - material_started) * 1000
                last_material_reason = result.get("reason", "material-unavailable")
                with self._lock:
                    if (current() and (not private_enabled or self._s1_private_pool is pool and mid in pool.remaining)
                            and result.get("success") and self._render is not None
                            and self._render.get("frameId") == frame_id
                            and self._render.get("mapId") == mid
                            and self._render.get("token") == result.get("renderToken")
                            and self._display_current(self._render)):
                        first_result = result
                        if (private_enabled and self._s1_private_pool is pool
                                and pool.accept_material(mid, coordinate_current=True)):
                            if self._native_s1_final_round_lock:
                                self._s1_round_map_id = mid
                                # Material was published just before the ledger
                                # committed. Refresh identity metadata only; its
                                # token, transform and display grant do not change.
                                self._render.update(self._identity_fields())
                            self._notify("private-experimental-locked", reason="private-unique-pool-current-material",
                                **pool.snapshot(), privateResourceDigest=catalog.resource_key)
            return True

        def preferred_material_committed(map_id):
            with self._lock:
                row = self._render
                return bool(not recovery_scan and map_id == preferred and current() and first_result is not None
                    and row is not None and row.get("token") == first_result.get("renderToken")
                    and row.get("mapId") == map_id and row.get("frameId") == frame_id
                    and row.get("candidateDisplayOnly") and self._display_current(row))

        def first_material_committed(map_id):
            with self._lock:
                row = self._render
                return bool(current() and (not private_enabled or self._s1_private_pool is pool and map_id in pool.remaining)
                    and first_result is not None and row is not None
                    and row.get("token") == first_result.get("renderToken")
                    and row.get("mapId") == map_id and row.get("frameId") == frame_id
                    and row.get("candidateDisplayOnly") and self._display_current(row))

        try:
            if private_enabled and pool.retain_locked_identity:
                self._prepare_s1_static_reference(engine, pool, captured, floor, current)
            prior_reason = "pose-prior-source-unavailable"
            prior_provider = getattr(engine, "qt_s1_pose_prior", None)
            if prior_source is not None and callable(prior_provider):
                prior_reason = "pose-prior-unavailable"
                try:
                    hint = prior_provider(prior_source, str(floor), preference_scope)
                    if hint is not None and not obsolete():
                        matrix = hint.matrix_for(mode=catalog.mode, resource_key=catalog.resource_key,
                            map_id=prior_source.map_id, floor=str(floor), owner_scope=preference_scope)
                        if matrix is not None:
                            pose_priors[(prior_source.map_id, str(floor))] = matrix
                            prior_reason = "pose-prior-bound"
                            if self._failure_samples.automatic_enabled or _test_evidence_active():
                                prior_binding = dict(mode=catalog.mode, resourceKey=catalog.resource_key,
                                    mapId=prior_source.map_id, sourceFloor=str(prior_source.floor), targetFloor=str(floor),
                                    indexSha256=getattr(hint, 'index_sha256', None),
                                    ownerScopeDigest=hashlib.sha256(repr(preference_scope).encode()).hexdigest())
                        else:
                            prior_reason = "pose-prior-scope-rejected"
                except (ValueError, TypeError, KeyError, OSError):
                    pass  # A missing/unbound hint leaves the original full fit.
            if private_enabled and pool.retain_locked_identity and pool.locked_id:
                # No catalogue ordering or identity competition after the round
                # lock. Reuse the guarded fit/material pipeline for this ID only.
                order, ordering = (pool.locked_id,), dict(reason="preferred-material-id-first")
            else:
                order, ordering = candidate_order(engine, catalog, str(floor), snapshot, prior,
                    self._recorded_s1_scan_order, self._recorded_s1_priority_index, priority_handles)
            if preferred is not None:
                order = (preferred,) + tuple(mid for mid in order if mid != preferred)
                ordering = dict(ordering, reason="preferred-material-id-first")
            if obsolete():
                withdraw("stream-order-cancelled")
                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
            with self._lock:
                if current():
                    self._stream_priority_handles.update(priority_handles)
            self._notify("structure-scan-started", reason=ordering["reason"], streamFrameId=frame_id,
                streamEpoch=epoch, streamRoundId=round_id, streamCycleId=cycle,
                candidateOrder=list(order), ordering=ordering, budgetSeconds=scan_budget,
                rawFrameDigest=digest, shapeSnapshotId=snapshot.sha256, frameShape=list(shape),
                clientDimensions=list(dimensions))
            def scan_current():
                nonlocal scan_plan, scan_revision
                with self._lock:
                    # Queued historical evidence may prune again after a
                    # contradiction. The first fresh recovery observation still
                    # owes one full catalogue pass, regardless of that ledger.
                    scan_plan, scan_revision = ((tuple(catalog.map_ids) if recovery_scan else tuple(pool.remaining)),
                        pool.revision) if private_enabled else ((), None)
                    options = dict(planned_map_ids=scan_plan,
                        first_material_committed=(first_material_committed
                            if not recovery_scan and payload.get("forceCatalogue") is not True
                            and action not in ("reselect_map", "map_reselection") else None)) if private_enabled else {}
                    if pose_priors:
                        options["pose_priors"] = dict(pose_priors)
                    if (private_enabled and pool.retain_locked_identity and pool.locked_id
                            and self._failure_samples.automatic_enabled):
                        options['collect_fit_diagnostics'] = True
                    if _test_evidence_active():
                        options['collect_fit_diagnostics'] = True
                    self._recognition_diagnostic("s1-scan-plan", prior_reason,
                        inputs=self._failure_diagnostic(payload),
                        plannedCount=len(scan_plan) if private_enabled else len(catalog.map_ids),
                        plannedMapIds=list(scan_plan) if private_enabled else list(catalog.map_ids),
                        posePriorAvailable=bool(pose_priors),
                        sourceFloor=str(prior_source.floor) if prior_source is not None else None,
                        targetFloor=str(floor),
                        privatePoolId=pool.pool_id if private_enabled else None,
                        privatePoolRevision=pool.revision if private_enabled else None,
                        privateExperimentalLocked=bool(private_enabled and pool.locked_id),
                        privateRecoveryPending=recovery_scan)
                scan = getattr(engine, 'qt_structure_scan', None) or getattr(engine, 'qt_s1_structure_scan', None)
                report = scan(frame, floor=str(floor), floor_source="current-frame-verified",
                    cancelled=obsolete, budget_seconds=scan_budget,
                    on_progress=publish, candidate_order=order, preferred_map_id=preferred,
                    preferred_material_committed=preferred_material_committed,
                    **(dict(on_entry_evidence=publish_entry) if catalog.season == 's0' else {}),
                    **({'deadline': deadline} if deadline is not None else {}),
                    frame_snapshot=snapshot, **options)
                if _test_evidence_active() and type(report.get('fitEvidence')) is dict:
                    _test_evidence('fit-evidence', frame, dict(report['fitEvidence'],
                        priorBinding=prior_binding, priorReason=prior_reason))
                return report
            report = scan_current()
            if catalog.season == 's0' and report['status'] != 'cancelled' and not obsolete():
                if not entry_published and 'entryEvidence' in report:
                    publish_entry(report['entryEvidence'])
                if entry_selection and not obsolete():
                    first_result = self._solve_observation(action, payload, cancelled, engine, self._group, epoch,
                        captured, frame, floor, digest, capture_generation, frame_generation,
                        frame_id, cycle, entry_selection, entry_seed,
                        structure_ticket=(session, ticket), structure_current=current)
                elif not entry_selection and first_result is None:
                    last_material_reason = 'identity-not-uniquely-verified'
            if first_result is not None and not first_result.get('success'):
                # Entry evidence may lock identity after this stream began as
                # a provisional display. Preserve its solver's concrete failure,
                # including one that revoked its own context before returning.
                if first_result.get('stale'):
                    withdraw("stream-scan-cancelled")
                return first_result
            # A locked ID outlives an UNKNOWN current fit/material result, but
            # its old pixels do not. Final round locks never reopen identity;
            # legacy auto-test evidence experiments retain their own policy.
            # An unconfirmed singleton may still restore competition once on
            # this immutable frame, without acquiring a new public owner.
            if private_enabled and len(scan_plan) == 1 and first_result is None:
                retry_full = False
                with self._lock:
                    if (current() and self._s1_private_pool is pool and pool.revision == scan_revision
                            and not pool.locked_id and not pool.retain_locked_identity):
                        pool.restore()
                        retry_full = True
                        preferred = None
                        attempted.clear()
                        ticket = session.begin_observation(str(floor))
                        self._s1_structure_ticket = ticket
                        self._notify("private-experimental-unlocked", reason="private-current-fit-unknown",
                            **pool.snapshot(), privateResourceDigest=catalog.resource_key)
                if retry_full and not obsolete():
                    report = scan_current()
            with self._lock:
                still_current = current()
            if report["status"] == "cancelled" or not still_current:
                withdraw("stream-scan-cancelled")
                result = self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
            elif not publish(report):
                withdraw("stream-final-rejected")
                result = self._receipt(action, payload, success=False, reason="stream-final-rejected", stale=True)
            elif first_result is not None:
                result = dict(first_result, structureScanStatus=report["status"],
                    structureScanComplete=report["scanComplete"], structureScanElapsedSeconds=report["elapsedSeconds"],
                    candidateReuse=report.get("candidateReuse", "none"), scanSkipped=report.get("scanSkipped", False))
            elif catalog.season == 's1' and not provisional_display:
                result = self._s1_structure_pending_receipt(action, payload)
            else:
                result = self._receipt(action, payload, success=False, reason=last_material_reason,
                    posePending=True, identityUnresolved=not bool(self._s1_round_locked_map()))
                # Reuse this ticket's guarded capture and the existing bounded
                # recorder/settings. Diagnostic failure must not change the scan.
                try:
                    from recognition_diagnostics import s1_scan_failure
                    self._recognition_diagnostic("s1-scan-no-material", last_material_reason,
                        inputs=self._failure_diagnostic(payload),
                        structureScanFailure=s1_scan_failure(report, preferred))
                except Exception:
                    pass
                self._terminal_failure_sample(
                    "no-current-supported-candidates" if not report["supportedMapIds"] else "s1-no-current-material",
                    payload, captured, lambda: not obsolete(),
                    diagnostic=(dict(fitEvidence=dict(report['fitEvidence'], priorBinding=prior_binding,
                        priorReason=prior_reason)) if report.get('fitEvidence')
                        and private_enabled and pool.retain_locked_identity and pool.locked_id else None))
            with self._lock:
                if (recovery_scan and current() and self._s1_private_pool is pool
                        and pool.recovery_revision == recovery_revision
                        and report["status"] == "complete"
                        and set(report["checkedMapIds"]) == set(catalog.map_ids)):
                    pool.recovery_pending = False
            self._notify("structure-scan-finished", reason=report["status"], streamFrameId=frame_id,
                streamEpoch=epoch, streamRoundId=round_id, streamCycleId=cycle,
                status=report["status"], scanComplete=report["scanComplete"], updateSeq=report["updateSeq"],
                checkedMapIds=report["checkedMapIds"], supportedMapIds=report["supportedMapIds"],
                candidateReuse=report.get("candidateReuse", "none"), scanSkipped=report.get("scanSkipped", False),
                privateScanMs=report["elapsedSeconds"] * 1000, privateMaterialMs=private_material_ms,
                elapsedSeconds=report["elapsedSeconds"], totalSeconds=time.monotonic()-started,
                firstVerifiedPoseElapsedSeconds=report.get("firstVerifiedPoseElapsedSeconds"))
            direct = report.get('directStructureRecognition', {})
            preparation = report.get('posePreparation', [])
            self._recognition_diagnostic("s1-scan-result", report["status"],
                inputs=self._failure_diagnostic(payload), checkedCount=len(report["checkedMapIds"]),
                supportedCount=len(report["supportedMapIds"]),
                elapsedMs=report["elapsedSeconds"] * 1000,
                posePriorOutcomes=report.get("posePriorOutcomes", []),
                candidatePreparationCount=len(preparation),
                candidatePreparationMs=sum(row.get('elapsedMs', 0) for row in preparation),
                candidateFitMs=sum(row.get('fitMs', 0) for row in preparation),
                directStructureRecognition={key: direct[key] for key in (
                    'status', 'floor', 'authorizesIdentity', 'lock', 'measuredChainCount',
                    'aCount', 'bCount', 'fingerprintUnique', 'retrievalComplete',
                    'qualificationGaps', 'extractionMs', 'retrievalMs', 'heldOutMs',
                    'computationMs', 'elapsedMs') if key in direct})
            return result
        except (MemoryError, KeyboardInterrupt, SystemExit):
            withdraw("stream-scan-exception")
            raise
        except Exception as error:
            with self._lock:
                stale_error = not owns_ticket()
            withdraw("stream-scan-exception")
            with self._lock:
                stale_error = stale_error or self._stream_owner is not owner or (
                    epoch != self._epoch or frame_id != self._frame_id or cycle != self._cycle
                    or round_id != self._round or engine is not self._engine
                    or session is not self._s1_structure_session)
            if stale_error:
                # Never hand a known old-frame error to the generic dispatch
                # cleanup, which owns only its original acquisition epoch.
                return self._receipt(action, payload, success=False,
                    reason=f"{type(error).__name__}:{error}", stale=True, error=True,
                    **self._error_fields(error, action))
            raise
        finally:
            with self._lock:
                if self._stream_owner is owner:
                    self._stream_owner = None
            # A valid captured frame remains historical evidence after an
            # ordinary close, even if first paint lost the race. No UI authority
            # follows it. Queue ordering is capture admission, not finish time.
            if private_enabled and not (pool.retain_locked_identity and pool.locked_id):
                self._enqueue_private_observation(pool, engine, catalog, captured, frame, str(floor),
                    digest, epoch, frame_id, round_id, cycle, payload, private_sequence)

    @property
    def private_evidence_busy(self):
        return self._s1_private_jobs.busy

    def stop_private_evidence(self):
        # Stop admission first, then invalidate the running immutable task.
        # Exit drain polls busy; the UI never joins a native kernel here.
        self._s1_private_jobs.shutdown()
        with self._lock:
            self._clear_private_pool()

    def _clear_private_pool(self):
        self._s1_private_pool = None
        jobs = getattr(self, "_s1_private_jobs", None)
        if jobs is not None:
            jobs.discard_pending()

    def _private_pool_scope_current(self, pool):
        if pool is None or self._s1_private_pool is not pool:
            return False
        scope = pool.scope
        return (self._active and self._s1_provisional_enabled()
            and self._s1_native_identity_scope(scope[4], scope[-1]) == scope
            and self._capture_is_current(scope[-1], scope[-2]))

    def private_experiment_event_is_current(self, event):
        """Read-only delivery check, including capture geometry after queueing."""
        with self._lock:
            pool = self._s1_private_pool
            return bool(self._private_pool_scope_current(pool)
                and event.get("privatePoolId") == pool.pool_id
                and event.get("privatePoolRevision") == pool.revision
                and event.get("privateResourceDigest") == pool.scope[4].resource_key
                and (event.get("epoch"), event.get("season"), event.get("mode"), event.get("roundId"))
                    == (self._epoch, self._season, self._mode, self._round))

    def _private_record(self, context, event, **values):
        """Disk-only telemetry. Never enter the UI/session callback channel."""
        diagnostics = self._diagnostics
        if diagnostics is not None:
            try:
                diagnostics.record("runtime-event", eventName=event, **context, **values)
            except Exception:
                pass

    def _enqueue_private_observation(self, pool, engine, catalog, captured, frame, floor,
                                     digest, epoch, frame_id, round_id, cycle, payload, sequence):
        import numpy as np
        # Immutable source pixels, at most one running plus one latest queued.
        owned_frame = np.frombuffer(frame.tobytes(order="C"), dtype=np.uint8).reshape(frame.shape)
        context = dict(season=catalog.season, mode=catalog.mode, floor=floor,
            frameId=frame_id, roundId=round_id, epoch=epoch, cycleId=cycle,
            privateFrameDigest=digest, privateResourceDigest=catalog.resource_key,
            attemptId=payload.get("_diagnosticAttemptId", ""),
            openingId=payload.get("_diagnosticOpeningId", ""), inputSequence=payload.get("inputSequence"))
        scope = pool.scope
        def scope_current():
            return (pool.scope == scope and engine is self._engine and catalog == scope[4]
                and captured.context == scope[-1] and self._private_pool_scope_current(pool))
        def cancelled():
            with self._lock:
                return not scope_current()
        def stage(name, state, elapsed, **extra):
            self._private_record(context, "private-stage", privateStage=name,
                privateStageState=state, elapsedMs=elapsed, **extra)
        def discard():
            stage("queue", "cancelled", 0.)
        def run():
            began = time.monotonic()
            state = "cancelled"
            stage("prepare", "started", 0.)
            try:
                with self._lock:
                    if not scope_current() or (pool.retain_locked_identity and pool.locked_id):
                        return
                    # Read remaining/revision at START, not queue admission.
                    before, revision = tuple(pool.remaining), pool.revision
                evidence = engine.qt_s1_private_coordinate_prepare(owned_frame, before, floor,
                    cancelled=cancelled, stage=stage)
                with self._lock:
                    if not scope_current() or pool.revision != revision:
                        self._private_record(context, "private-evidence-discarded", reason="private-owner-or-revision-changed",
                            snapshotPoolRevision=revision, currentPoolRevision=pool.revision)
                        return
                    if (evidence.get("mode"), evidence.get("resourceKey"), evidence.get("floor")) != (
                            catalog.mode, catalog.resource_key, floor):
                        state = "failed"
                        return
                    was_locked = pool.locked_id
                    reason = pool.observe(evidence)
                    summary = evidence.get("coordinateEvidence", {})
                    self._private_record(context, "private-pool-observed", reason=reason,
                        privatePoolBefore=list(before), privatePoolAfter=list(pool.remaining),
                        snapshotPoolRevision=revision, currentPoolRevision=pool.revision,
                        privateContradictedMapIds=evidence.get("contradictedMapIds", []),
                        privateUnknownMapIds=evidence.get("unknownMapIds", list(before)),
                        privateCoordinateReason=evidence.get("reason", ""),
                        privateCoordinateVerified=evidence.get("coordinateVerified") is True,
                        privatePoseDomainNarrow=evidence.get("poseAvailable") is True,
                        privateCoordinateBasis=summary.get("basis", ""), privateScaleRange=summary.get("scaleRange", []),
                        privateCoordinateErrorPx=summary.get("pointCoordinateErrorBoundPx", summary.get("anchorErrorPx", 0)),
                        privateHypothesisCount=summary.get("sharedHypothesisCount", 0),
                        privateGateStatus=evidence.get("gateStatus", "unknown"),
                        privateGateReason=evidence.get("gateReason", "private-coordinate-unavailable"),
                        privateGateMs=evidence.get("gateMs", 0), privateObservationMs=evidence.get("observationMs", 0),
                        privateCompareMs=evidence.get("elapsedMs", 0), privateTotalMs=evidence.get("totalMs", 0),
                        privateResourceMs=evidence.get("resourceMs", 0),
                        **pool.snapshot())
                    if reason == "private-contradiction-recovery":
                        self._private_contradiction_recovered(pool, was_locked, evidence, catalog)
                    if reason == "private-pool-updated" and tuple(pool.remaining) != before:
                        # Historical positive evidence may complete after paint.
                        # Retire a now-excluded candidate immediately, not only
                        # when a future foreground scan happens to publish.
                        row = self._render
                        if (row is not None and row.get("candidateDisplayOnly")
                                and row.get("mapId") not in pool.remaining):
                            self._s1_candidate_preference = self._s1_candidate_pose_hint = None
                            self._revoke_render("s1-candidate-excluded")
                        self._notify("s1-candidate-pool-changed", reason=reason,
                            **pool.snapshot(), privateResourceDigest=catalog.resource_key)
                    # Formal evidence needs bounded reasons too; the optional
                    # private timing sink is deliberately disabled in production.
                    try:
                        self._recognition_diagnostic("s1-round-evidence", reason,
                            inputs={key: context[key] for key in
                                ("attemptId", "openingId", "roundId", "epoch", "cycleId", "floor")},
                            remainingCount=len(pool.remaining),
                            contradictedCount=len(evidence.get("contradictedMapIds", [])),
                            unknownCount=len(evidence.get("unknownMapIds", before)),
                            privatePoolAfter=list(pool.remaining),
                            privateCoordinateReason=evidence.get("reason", "private-coordinate-unknown"),
                            privateCoordinateVerified=evidence.get("coordinateVerified") is True,
                            privatePoseDomainNarrow=evidence.get("poseAvailable") is True,
                            candidateChecks=[{key: check[key] for key in
                                ("mapId", "unavoidableMissCount", "sampleCount", "unavoidableSpanPx", "explicitContradiction")
                                if key in check} for check in evidence.get("candidateChecks", [])[:32]],
                            privatePoolId=pool.pool_id, privatePoolRevision=pool.revision,
                            privateExperimentalLocked=bool(pool.locked_id))
                    except Exception:
                        pass  # Diagnostics cannot change the ledger result.
                    state = "finished"
            except BaseException as error:
                state = "failed"
                stage("prepare-error", state, (time.monotonic()-began)*1000, exceptionType=type(error).__name__)
            finally:
                # Records the original frame even when a later owner is active.
                stage("prepare", state, (time.monotonic()-began)*1000)
        if cancelled():
            discard()
            return
        stage("queue", "started", 0.)
        self._s1_private_jobs.submit(run, discard, sequence=sequence)

    def _private_contradiction_recovered(self, pool, previous_lock, evidence, catalog):
        """Under the lifetime lock: retire old material, then emit one transition.

        No Session refresh/dispatch, epoch change, hotkey or foreground cancel.
        The next fresh observation owns a full-catalogue recovery scan.
        """
        self._clear_candidate_reference()
        self._s1_candidate_preference = self._s1_candidate_pose_hint = None
        self._remember_proposals = {token: proposal for token, proposal in self._remember_proposals.items()
            if not proposal.get("candidateOnly")}
        session, ticket = self._s1_structure_session, self._s1_structure_ticket
        if session is not None and ticket is not None:
            session.withdraw_material(ticket)
        self._automatic_candidate_rows = []
        for candidate in self._candidate_rows:
            candidate["structureSupported"] = False
        row = self._render
        token = ""
        if row is not None and row.get("candidateDisplayOnly"):
            token = row["token"]
            self._core.revoke_display(reason="private-contradiction-recovery")
            self._render = self._render_capture_context = None
            self._provisional_display_map_id = ""
        # Finish every mutation before either signal. A synchronous callback
        # may open a successor; no old cleanup may erase its owner or material.
        transition = dict(pool.snapshot(), privatePreviousLockMapId=previous_lock,
            privateCoordinateReason=evidence.get("reason", ""),
            privateResourceDigest=catalog.resource_key)
        if token:
            self._notify("render-revoked", reason="private-contradiction-recovery", token=token)
        if previous_lock:
            self._notify("private-experimental-unlocked", reason="private-contradiction-recovery", **transition)

    def _s1_structure_pending_receipt(self, action, payload):
        session = self._s1_structure_session
        evidence = session.snapshot() if session is not None else {}
        candidates = evidence.get("currentSupportedMapIds", [])
        return self._receipt(action, payload, success=bool(candidates),
            reason="s1-structure-candidates-awaiting-confirmation" if candidates else "s1-structure-candidates-unresolved",
            posePending=True, identityUnresolved=True, structureEvidence=evidence)

    def _collect_legacy_entry_evidence(self, engine, frame, floor, epoch, frame_id, cancelled, *, rediscover=False):
        """Explicit legacy providers use the shared source-plan collector."""
        with self._lock:
            previous, prior = self._identity_native, self._identity_prior
            context = self._identity_context()
            intent_generation = self._group_intent_generation
        review = getattr(engine, "rank_established_group", None)
        rows = (review(frame, floor, prior=prior, budget_seconds=INITIAL_IDENTITY_BUDGET_SECONDS)
                if prior and callable(review) and not rediscover else engine.rank(frame, floor=floor,
                    limit=len(engine.pack.maps), budget_seconds=INITIAL_IDENTITY_BUDGET_SECONDS))
        return self._commit_identity_rows(engine, rows, frame, floor,
            epoch, frame_id, cancelled, rediscover=rediscover,
            identity_snapshot=(previous, context, intent_generation))

    def _commit_identity_rows(self, engine, rows, frame, floor, epoch, frame_id, cancelled, *,
                              identity_snapshot, rediscover=False):
        """The runtime alone commits identity; this reducer schedules no work."""
        from entry_pose_proposals import current_entry_pose_seeds
        previous, context, intent_generation = identity_snapshot
        seeds = current_entry_pose_seeds(rows, floor)
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
        retired_preview_token = ""
        with self._lock:
            if (self._cancelled(cancelled) or epoch != self._epoch or frame_id != self._frame_id
                    or intent_generation != self._group_intent_generation or self._group_choice_inflight is not None):
                return None, None, "stale-context"
            if engine is not self._engine:
                return None, None, "stale-context"
            if not _engine_resource_current(engine):
                self.invalidate("resource-context-superseded")
                return None, None, "resource-context-superseded"
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
                    retired_preview_token = self._retire_candidate_preview(reason="candidate-identity-committed")
                    self._durable_map_id = selected
                    self._identity_source = "automatic-hard-gate"
            if selected:
                if not retired_preview_token:
                    retired_preview_token = self._retire_candidate_preview(reason="candidate-identity-committed")
                if grouped and len(state.member_ids)>1:
                    self._group_member_preference=(*group_key,selected)
                elif self._group_member_preference and selected not in self._group_member_preference[2]:
                    self._group_member_preference=None
                    self._desired_group_member=None
                    self._group_member_choice_status=self._group_choice_reason=""
                self._map_id = selected
                self._gate.select(selected)
                self._gate.confirm()
            committed_intent_generation = self._group_intent_generation
        if retired_preview_token:
            self._notify("render-revoked", reason="candidate-identity-committed", token=retired_preview_token)
        self._recognition_diagnostic('identity-rank', proposal.get('reason', proposal.get('status', 'ranked')),
            inputs=dict(candidateCount=len(rows), acceptedCount=len(accepted), seedCount=len(seeds),
                floor=floor, frameId=frame_id, budgetSeconds=INITIAL_IDENTITY_BUDGET_SECONDS),
            selectedMapId=selected, topCandidate=top, proposal=proposal)
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
        with self._lock:
            if (self._cancelled(cancelled) or epoch != self._epoch or frame_id != self._frame_id
                    or committed_intent_generation != self._group_intent_generation
                    or self._group_choice_inflight is not None):
                return None, None, 'stale-context'
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
        staged = pending.get("stagedObservation")
        common = bool(self._active and self._map_open and not self._selection_pending
            and not self._floor_required and pending["scope"] == self._sibling_scope()
            and self._core is pending["sourceCore"] and self._map_id == pending["sourceMapId"]
            and self._capture_is_current(pending["captureContext"], row["captureGeneration"])
            and pending["core"].can_display(cycle_id=self._cycle, map_id=row["mapId"],
                floor=self._floor, frame_generation=staged["generation"] if staged else self._frame_generation)
            and (staged is None or self._transient_drag is None and self._staged_choice_current(staged)))
        if not common:
            return False
        registered = registered_family_identity(pending.get('identity'))
        if registered is not None and registered != registered_family_identity(self._identity_native):
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

    def sibling_requires_registry_validation(self, token):
        """Pure query for the UI; file-backed declarations are checked by a worker."""
        with self._lock:
            pending = self._pending_sibling
            if pending is None or pending['render']['token'] != token:
                return False
            state = pending.get('identity') or self._identity_native
            registry = getattr(self._engine, 'qt_logical_group_registry', None)
            return bool(registered_family_identity(state) is not None
                        and getattr(registry, 'file_ref', None) is not None)

    def _validate_sibling_registry(self, token, payload, cancelled):
        action = 'validate_sibling_registry'
        with self._lock:
            pending = self._pending_sibling
            if (pending is None or pending['render']['token'] != token or self._cancelled(cancelled)
                    or not self._pending_sibling_current(pending)):
                return self._receipt(action, payload, success=False, reason='sibling-context-expired')
            state = clone_similarity_state(pending.get('identity') or self._identity_native)
            engine, scope = self._engine, self._sibling_scope()
        failure = None
        try:
            validate_established_registered_family(engine, state)
        except (OSError, ValueError) as error:
            failure = str(error)[:512]
        with self._lock:
            if (self._pending_sibling is not pending or engine is not self._engine
                    or scope != self._sibling_scope() or self._cancelled(cancelled)
                    or not self._pending_sibling_current(pending)):
                return self._receipt(action, payload, success=False, reason='sibling-context-expired')
            if failure is not None:
                self.invalidate('registered-group-resource-unavailable', source=action)
                return self._receipt(action, payload, success=False, reason='registered-group-resource-unavailable',
                                     messageDetail=failure)
            pending['registryVerified'] = registered_family_identity(state)
            return self._receipt(action, payload, success=True, reason='registered-group-resource-verified')

    def acknowledge_sibling(self, token, accepted, reason=""):
        """Commit original sibling identity only after Image.Ready acceptance.

        Pure bounded state work: callable on the Qt owner, with no fit/capture,
        disk IO or dependency on overlay visibility. Clone reowns the identity
        transaction on this caller without mutating the published source.
        """
        with self._lock:
            pending = self._pending_sibling
            if (accepted is True and self.sibling_requires_registry_validation(token)
                    and pending.get('registryVerified') != registered_family_identity(
                        pending.get('identity') or self._identity_native)):
                accepted, reason = False, 'registered-group-validation-required'
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
                    staged = pending.get("stagedObservation")
                    if staged is not None:
                        self._frame_generation, self._frame_id = staged["generation"], staged["frame_id"]
                        self._last_observation = {key: value for key, value in staged.items()
                            if key not in ("sourceScope", "sourceOwner", "intent")}
                        self._transient_drag = self._transient_cycle = None
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
                    self._fit_reporting.success(self._fit_reporting_scope())
                    self._reference_sheet.update(metadata)
                    if pending.get("manualChoice") is not None:
                        self._provisional_reference_context = self._reference_owner_context(self._reference_sheet)
                    self._render_capture_context = self._reference_capture_context = pending["captureContext"]
                    if staged is not None:
                        self._publish_game_pointer_context_locked()
                        self._set_pose_phase("valid", "current-frame-pose-authorized", target,
                            self._floor, self._frame_id, self._frame_generation, materialAccepted=True)
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

    def _set_pose_phase(self, status, reason, map_id, floor, frame_id, generation, *, staged_observation=None, **extra):
        with self._lock:
            current = (self._staged_choice_current(staged_observation) if staged_observation is not None
                else frame_id == self._frame_id)
            if not self._active or not current:
                return
            if staged_observation is not None:
                extra.update(pendingMaterial=True, sourceFrameId=self._frame_id)
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
        return dict(epoch=self._epoch, season=self._season, mode=self._mode, profileId=self._profile,
            roundId=self._round, cycleId=self._cycle, frameId=self._frame_id,
            generation=self._frame_generation, captureGeneration=observation["capture_generation"],
            clientBounds=list(captured.client_screen_bounds), fullClientSize=list(captured.full_client_size),
            dpi=captured.dpi,
            observationCreatedAt=observation.get("created_at", 0),
            dragBoundaryNormalized=normalize_drag_boundary(self._settings.get("dragBoundaryNormalized")))

    def _publish_game_pointer_context_locked(self):
        # Publish a complete private value. Never mutate its nested values or
        # combine a cached press scope with newer runtime fields on the reader.
        self._pointer_context_snapshot = self._game_floor_click_context_locked()

    def game_floor_click_context(self):
        """Nonblocking metadata; the consumer still validates the whole scope."""
        if self._lock.acquire(blocking=False):
            try:
                self._publish_game_pointer_context_locked()
                published = self._pointer_context_snapshot
            finally:
                self._lock.release()
        else:
            published = self._pointer_context_snapshot
        context = copy.deepcopy(published)
        if context is not None:
            context["createdAt"] = self._monotonic()
        return context

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
            staged_choice = bool(self._transient_cycle_current() and
                (self._pending_sibling is not None and self._pending_sibling.get("stagedObservation") is not None
                 or self._group_choice_inflight is not None))
            if ((self._pending_sibling is not None or self._group_choice_inflight is not None) and not staged_choice
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
                    if staged_choice:
                        pending = self._discard_sibling()
                        self._group_choice_inflight = None
                        if pending is not None:
                            self._notify("sibling-rolled-back", token=pending["render"]["token"],
                                reason="manual-group-pixels-moved")
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
            render_owner = self._refresh_render_owner()
            input_sequence = self._last_map_input_sequence
            if (payload.get("gestureId") is not None and (payload["gestureId"] != cycle["gestureId"]
                    or payload.get("pointerSequence") != cycle["pointerSequence"])):
                return self._receipt("probe_map_after_drag", payload, success=False, reason="transient-probe-expired", stale=True)
            requested = payload.get("context")
            if requested is not None and (not isinstance(requested,dict) or any(requested.get(key) != value
                    for key,value in self._game_floor_click_context_locked().items())):
                return self._receipt("probe_map_after_drag", payload, success=False, reason="transient-probe-expired", stale=True)
        self._new_diagnostic_attempt(payload)
        capture_generation = self._capture_generation()
        obsolete = lambda: self._cancelled(cancelled) or epoch != self._epoch
        try:
            # Observation does not replace this opening's candidate geometry.
            captured = self._capture_frame(cancelled, attempt_id=payload.get("_diagnosticAttemptId"),
                                           preserve_candidate=True)
            frame = self._full_frame(captured)
            if obsolete():
                self._retire_refresh_owner(render_owner)
                return self._receipt("probe_map_after_drag", payload, success=False, reason="cancelled-or-stale", stale=True)
            observation, floor, floor_read = self._observe(engine, frame)
        except Exception as error:
            with self._lock:
                if engine is self._engine and not _engine_resource_current(engine):
                    self.invalidate("resource-context-superseded")
                    return self._receipt("probe_map_after_drag", payload, success=False,
                        reason="resource-context-superseded", stale=True)
                if obsolete() or render_owner != self._refresh_render_owner():
                    self._retire_refresh_owner(render_owner)
                    return self._receipt("probe_map_after_drag", payload, success=False,
                        reason="cancelled-or-stale", stale=True, errorType=type(error).__name__)
                # Consume a current failure under the ownership lock. Handing
                # it to the generic dispatcher would open a window where a new
                # drag owns these pixels before the old error revokes them.
                if not self._retained_material_current(render_owner):
                    self.invalidate("runtime-error", source=f'execute:probe_map_after_drag:{type(error).__name__}')
                return self._receipt("probe_map_after_drag", payload, success=False,
                    reason=f"{type(error).__name__}:{error}", error=True, stale=False,
                    unavailable=bool(getattr(error, "unavailable", False)),
                    **self._error_fields(error, "probe_map_after_drag"))
        detected = self._map_observed(engine, observation, floor_read)
        with self._lock:
            if engine is self._engine and not _engine_resource_current(engine):
                self.invalidate("resource-context-superseded")
                return self._receipt("probe_map_after_drag", payload, success=False,
                    reason="resource-context-superseded", stale=True)
            current_scope = self._sibling_scope()
            if (obsolete() or render_owner != self._refresh_render_owner()
                    or scope[:7] + scope[8:] != current_scope[:7] + current_scope[8:]
                    or not self._capture_is_current(captured.context, capture_generation)):
                self._retire_refresh_owner(render_owner)
                return self._receipt("probe_map_after_drag", payload, success=False, reason="transient-probe-expired", stale=True)
            if not detected:
                self._probe_misses += 1
                self._retire_material_owner(render_owner, "map-not-observed")
                if self._probe_misses >= 2:
                    self.invalidate("map-closed", source="post-drag-probe")
                    payload["_mapInputVisual"] = dict(open=False, epoch=self._epoch, sequence=input_sequence,
                        captured=captured, captureGeneration=capture_generation)
                return self._receipt("probe_map_after_drag", payload, success=False, reason="map-not-observed")
            self._probe_misses = 0
            payload["_mapInputVisual"] = dict(open=True, epoch=epoch, sequence=input_sequence,
                captured=captured, captureGeneration=capture_generation)
            self._observe_floor_policy(floor_read, obsolete=obsolete)
            if (self._native_s1_candidate_preview and self._season == "s1"
                    and not self._manual_alignment and floor is None):
                self._observed_floor = ""
                if not self._retained_material_current(render_owner):
                    self._revoke_render("current-floor-unavailable")
                return self._receipt("probe_map_after_drag", payload, success=False,
                    reason="current-floor-unavailable", fitAttempted=False)
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
            observation_fields = {"frameId", "generation", "observationCreatedAt"}
            same_observation = all(context.get(key) == current.get(key) for key in observation_fields)
            # A floor-button press is UI intent, not a pose grant. A passive
            # observation can finish while this press waits in Qt's queue.
            # Keep its round/window scope and original age, but use the latest
            # localized controls; fresh matching highlight still owns display.
            before, after = context.get("generation"), current.get("generation")
            old_time, new_time = context.get("observationCreatedAt"), current.get("observationCreatedAt")
            observation_advanced = (type(before) is int and type(after) is int and 0 <= before < after
                and type(old_time) in (int, float) and type(new_time) in (int, float)
                and math.isfinite(old_time) and math.isfinite(new_time) and old_time <= new_time)
            if ((supplied_epoch != self._epoch and not own_click_epoch)
                    or any(context.get(key) != value for key, value in current.items()
                           if key not in observation_fields | {"epoch"})
                    or not (same_observation or observation_advanced)):
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
                clicked = game_floor_control_hit((screen_x, screen_y), current["clientBounds"], observation["floor_read"],
                    controls=getattr(self._engine, 'qt_controls_profile', None))
            except Exception:
                # Original native click producer treats malformed HUD geometry
                # as no hit; it must never mutate floor/pose from an exception.
                clicked = None
            if clicked is None:
                from map_close_control import map_close_control_hit
                return dict(success=False, reason="floor-click-not-a-control",
                    closeControlHit=map_close_control_hit(observation["frame"],
                        (screen_x, screen_y), current["clientBounds"]))
            first_epoch = span[0] if own_click_epoch else self._epoch
            self.invalidate("game-floor-control-intent")
            self._floor_click_epoch_span = (first_epoch, self._epoch)
            self._floor_observations = []
            self._floor_required = True
            self._observe_floor_policy(observation["floor_read"], explicit_floor=clicked)
            self._game_floor_intent = dict(cycleId=self._cycle, floor=clicked,
                awaitingObservation=True, expiresAt=created + MANUAL_FLOOR_HOLD_SECONDS)
            result = dict(success=True, reason="game-floor-control-intent", clickedFloor=clicked,
                clickedFloorId=self._floor_contract().floor_id_for(clicked), captureRequired=True,
                expiresAt=self._game_floor_intent["expiresAt"])
        self._notify("game-floor-control-intent", reason="awaiting-current-highlight", clickedFloor=clicked,
            expiresAt=result["expiresAt"])
        return result

    def _observe_floor_policy(self, floor_read, *, obsolete=None, explicit_floor=None):
        """Apply the shared legacy temporal/intent decision, never pose authority."""
        with self._lock:
            if obsolete is not None and obsolete():
                return None
            contract = self._floor_contract()
            evidence = read_floor_evidence(floor_read, self._floor_observations, explicit_floor=explicit_floor, contract=contract)
            self._floor_observations = list(evidence.observations)
            self._observed_floor = (contract.floor_id_for(evidence.observed) or "") if contract else ""
            policy = decide_floor_transition(evidence, current_floor=self._floor,
                floor_required=self._floor_required, manual_override=self._manual_override,
                hard_locked=self._floor_hard_locked, hold_until=self._manual_hold_until,
                cycle_id=self._cycle if self._map_open else self._cycle + 1,
                game_intent=self._game_floor_intent, now=self._monotonic(), contract=contract)
            self._last_floor_policy = policy.reason
            if self._diagnostics is not None:
                self._diagnostics.record("floor-decision", reason=policy.reason, season=self._season, mode=self._mode,
                    floor=str(policy.target), accepted=bool(policy.apply_target),
                    frameId=self._frame_id, roundId=self._round, cycleId=self._cycle)
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

    def _floor_read_conflicts(self, floor_read, target):
        from floor_contract import floor_contract
        controls = getattr(getattr(self, '_engine', None), 'qt_controls_profile', None)
        if controls is None:
            contract = floor_contract(floor_read.get("mode", "nightmare"), season=floor_read.get("season", "s0"))
        else:
            context = controls.for_context(floor_read.get('season', self._season), floor_read.get('mode', self._mode))
            contract = context.contract if context is not None else None
        observed = floor_read.get("selectedFloor")
        floor = contract.floor_id_for(observed) if contract else None
        return floor is not None and floor != str(target)

    def _floor_contract(self):
        from floor_contract import floor_contract
        controls = getattr(getattr(self, '_engine', None), 'qt_controls_profile', None)
        if controls is not None:
            context = controls.for_context(self._season, self._mode)
            return context.contract if context is not None else None
        return floor_contract(self._mode, season=self._season)

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
            if self._s1_provisional_enabled() and item.id not in scores:
                continue
            evidence = scores.get(item.id, {})
            score = evidence.get("score")
            score = float(score) if isinstance(score, (int, float)) and math.isfinite(score) else None
            rows.append(dict(key=item.id, mapId=item.id, name=str(getattr(item, "name", item.id)),
                floor=str(floor), score=score,
                selectionSeason=self._season,
                structureSupported=evidence.get("identityEvidence") in (
                    "s0-current-structure-proposal", "s1-current-structure-proposal"),
                structureFrameId=self._frame_id, structureGeneration=self._frame_generation, structureRoundId=self._round,
                reason="当前帧支持的未锁定候选，可直接切换显示" if self._candidate_provisional_enabled() and item.id in scores else
                       "入口结构已确认" if evidence.get("identityHardGatePassed") is True else
                       "结构匹配，地图身份待确认" if evidence.get("identityEvidence") in (
                           "s0-current-structure-proposal", "s1-current-structure-proposal") else
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
                engine = self._engine
            try:
                validate_established_registered_family(engine, state)
            except (OSError, ValueError) as error:
                with self._lock:
                    if self._group_choice_inflight and self._group_choice_inflight[0] == intent:
                        self._group_choice_inflight = None
                    if (not self._cancelled(cancelled) and intent == self._group_intent_generation
                            and request_scope == self._sibling_scope()):
                        self.invalidate('registered-group-resource-unavailable', source=action)
                return self._receipt(action, payload, success=False, reason='registered-group-resource-unavailable',
                                     messageDetail=str(error)[:512])
            with self._lock:
                current = self.snapshot(); current['groupMemberSelectionPending'] = False
                if (self._cancelled(cancelled) or intent != self._group_intent_generation
                        or request_scope != self._sibling_scope() or not group_member_choice_is_current(plan, current)
                        or registered_family_identity(state) != registered_family_identity(self._identity_native)):
                    if self._group_choice_inflight and self._group_choice_inflight[0] == intent:
                        self._group_choice_inflight = None
                    return self._receipt(action, payload, success=False, reason='group-choice-context-expired', stale=True)
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
                fresh = self._transient_cycle_current()
            result = None
            if fresh:
                observation, result = self._capture_group_choice_observation(
                    action, payload, cancelled, engine, request_scope, intent, floor)
                if observation is not None:
                    frame_generation, frame_id = observation["generation"], observation["frame_id"]
            if result is None:
                result = self._solve_observation(action, payload, cancelled, engine, None, epoch,
                    observation["captured"], observation["frame"], floor, observation["digest"],
                    observation["capture_generation"], frame_generation, frame_id, cycle, plan.map_id,
                    identity_stage=state, manual_choice=plan, staged_observation=observation if fresh else None)
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

    def _staged_choice_current(self, observation):
        return bool(observation["sourceScope"] == self._sibling_scope()
            and observation["sourceOwner"] == self._refresh_render_owner()
            and observation["intent"] == self._group_intent_generation)

    def _capture_group_choice_observation(self, action, payload, cancelled, engine, scope, intent, floor):
        """Fit a manual target on new pixels without promoting its frame yet."""
        obsolete = lambda: (self._cancelled(cancelled) or scope != self._sibling_scope()
            or intent != self._group_intent_generation)
        self._new_diagnostic_attempt(payload)
        capture_generation = self._capture_generation()
        captured = self._capture_frame(obsolete, attempt_id=payload.get("_diagnosticAttemptId"))
        frame = _test_input_copy(self._full_frame(captured))
        frame.setflags(write=False)
        observation, observed_floor, floor_read = self._observe(engine, frame)
        self._remember_control_observation(frame, engine.pack.mode, observation, floor_read, captured=captured)
        detected = self._map_observed(engine, observation, floor_read)
        with self._lock:
            if obsolete() or not self._capture_is_current(captured.context, capture_generation):
                return None, self._receipt(action, payload, success=False, reason="group-choice-context-expired", stale=True)
            if not detected:
                self._probe_misses += 1
                self._revoke_render("map-not-observed")
                if self._probe_misses >= 2:
                    self.invalidate("map-closed", source="manual-group-probe")
                return None, self._receipt(action, payload, success=False, reason="map-not-observed")
            self._probe_misses = 0
            effective_floor = self._observe_floor_policy(floor_read, obsolete=obsolete)
            if (observed_floor is None or effective_floor != floor or self._floor_read_conflicts(floor_read, floor)):
                self._revoke_render("current-frame-floor-conflict" if observed_floor else "current-floor-unavailable")
                failure_receipt = self._receipt(action, payload, success=False, reason="current-floor-unavailable")
            else:
                if obsolete():
                    return None, self._receipt(action, payload, success=False, reason="group-choice-context-expired", stale=True)
                digest = hashlib.sha256(frame.tobytes()).hexdigest()
                generation = self._frame_generation + 1
                return dict(captured=captured, frame=frame, floor=floor, digest=digest,
                    created_at=self._monotonic(), capture_generation=capture_generation,
                    frame_id=f"{self._round}:{generation}:{digest[:16]}", observed_floor=self._observed_floor,
                    cycle_id=self._cycle, generation=generation, floor_read=copy.deepcopy(floor_read),
                    sourceScope=scope, sourceOwner=self._refresh_render_owner(), intent=intent), None
        self._terminal_failure_sample("current-floor-unavailable", payload, captured, lambda: not obsolete())
        return None, failure_receipt

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

    def _select_s1_provisional(self, action, payload, cancelled, *, birth_ticket=None, request_id=None):
        # Keep the S1 dispatch seam for its existing request-lifetime consumers.
        return self._select_candidate_provisional(action, payload, cancelled,
            birth_ticket=birth_ticket, request_id=request_id)

    def _select_candidate_provisional(self, action, payload, cancelled, *, birth_ticket=None, request_id=None):
        # Fail stale clicks immediately even while another capture owns work.
        with self._lock:
            if self._s1_round_locked_map():
                return self._receipt(action, payload, success=False, reason="identity-locked")
            if birth_ticket is None:
                birth_ticket = (self._epoch, self._request_lifetime_generation, self._settings_generation)
            def owns_birth():
                return (not self._cancelled(cancelled) and birth_ticket == (
                    self._epoch, self._request_lifetime_generation, self._settings_generation))
            if not owns_birth() or self._capture_pending or self._s1_structure_ticket is None or not self._identity_observation_current():
                return self._receipt(action, payload, success=False, reason="stale-structure-candidate", stale=True)
            original_session, original_ticket = self._s1_structure_session, self._s1_structure_ticket
        with self._work:
            switch_from = None
            refreshed_after_drag = False
            requested_map_id = None
            with self._lock:
                if self._s1_round_locked_map():
                    return self._receipt(action, payload, success=False, reason="identity-locked")
                if (not owns_birth() or self._capture_pending or self._s1_structure_session is not original_session
                        or self._s1_structure_ticket != original_ticket or not self._identity_observation_current()):
                    return self._receipt(action, payload, success=False, reason="stale-structure-candidate", stale=True)
                session = self._s1_structure_session
                refreshed_after_drag = self._transient_cycle_current()
                if refreshed_after_drag and action == "select_candidate":
                    # Admit the old row's intent before replacing its frame.
                    # The requested ID must still earn a fresh ticket pose below.
                    if any(payload.get(key) != value for key, value in (
                            ("structureFrameId", self._frame_id), ("structureGeneration", self._frame_generation),
                            ("structureRoundId", self._round))):
                        return self._receipt(action, payload, success=False, reason="stale-structure-candidate", stale=True)
                    requested_map_id = payload.get("mapId")
                    choice = session.choose_candidate(requested_map_id, original_ticket)
                    if (not choice.get("success")
                            or choice.get("frameKey") != self._last_observation.get("digest")):
                        return self._receipt(action, payload, success=False, reason="structure-candidate-not-current", stale=True)
                if (refreshed_after_drag or action in ("previous_candidate", "next_candidate") and session is not None
                        and session.snapshot().get("currentStatus") in ("preferred-material-committed", "first-material-committed", "subset-unresolved")):
                    # Dragged pixels no longer match the saved ticket. A partial
                    # catalogue likewise lacks other poses. Both need a fresh
                    # full-catalogue observation before switching candidates.
                    switch_from = self._provisional_display_map_id
                    self._s1_candidate_preference = None
                    self._clear_private_pool()
                    rescan_owner = (birth_ticket[0], request_id if request_id is not None else object())
                    self._capture_pending = True
                    self._capture_pending_owner = rescan_owner
            if switch_from:
                try:
                    # Keep this user's original lifetime across the handoff.
                    # A nested dispatch could adopt a later close/new round.
                    rescanned = self._capture_and_solve("capture_now", {"forceCatalogue": True}, cancelled,
                        expected_epoch=birth_ticket[0])
                finally:
                    with self._lock:
                        if self._capture_pending_owner is rescan_owner:
                            self._capture_pending = False
                            self._capture_pending_owner = None
                if not rescanned.get("success"):
                    return dict(rescanned, action=action)
                with self._lock:
                    if not owns_birth():
                        return self._receipt(action, payload, success=False, reason="stale-structure-candidate", stale=True)
                    if self._s1_round_locked_map() or not self._candidate_provisional_enabled():
                        # A newly qualified identity wins during that capture;
                        # this earlier display choice cannot replace its map.
                        return dict(rescanned, action=action)
            with self._lock:
                session, ticket = self._s1_structure_session, self._s1_structure_ticket
                observation = self._last_observation or {}
                if (not owns_birth() or not self._active or not self._map_open or self._capture_pending
                        or session is None or ticket is None or not self._identity_observation_current()):
                    return self._receipt(action, payload, success=False, reason="stale-structure-candidate", stale=True)
                rows = self._automatic_candidate_rows
                if action == "select_candidate":
                    # A visual row must carry its originating observation.
                    if not refreshed_after_drag and any(payload.get(key) != value for key, value in (
                        ("structureFrameId", self._frame_id), ("structureGeneration", self._frame_generation),
                        ("structureRoundId", self._round))):
                        return self._receipt(action, payload, success=False, reason="stale-structure-candidate", stale=True)
                    mid = requested_map_id if refreshed_after_drag else payload.get("mapId")
                else:
                    ids = [row["mapId"] for row in rows]
                    if not ids:
                        return self._receipt(action, payload, success=False, reason="no-current-supported-candidates")
                    previous_id = switch_from or self._provisional_display_map_id
                    index = ids.index(previous_id) if previous_id in ids else 0
                    mid = ids[(index + (-1 if action == "previous_candidate" else 1)) % len(ids)]
                choice = session.choose_candidate(mid, ticket)
                if not choice.get("success") or choice.get("frameKey") != observation.get("digest"):
                    return self._receipt(action, payload, success=False, reason="structure-candidate-not-current", stale=True)
                from s1_structure_selection import StructureCatalog
                try:
                    check_current = getattr(self._engine, "qt_assert_resource_current", None)
                    if check_current is not None:
                        check_current()
                    if StructureCatalog.from_engine(self._engine) != session.catalog:
                        raise ValueError("structure-catalog-changed")
                except (OSError, ValueError, RuntimeError):
                    self._revoke_render("structure-resource-changed")
                    return self._receipt(action, payload, success=False, reason="stale-structure-candidate", stale=True)
                self._s1_candidate_preference = None
                self._clear_private_pool()
                # Revoke old material without erasing the current support ticket.
                self._clear_candidate_reference()
                old_token = self._render.get("token", "") if self._render else ""
                self._core.revoke_display(reason="provisional-candidate-switch")
                self._transient_drag = self._transient_cycle = None
                self._remember_proposals = {token: proposal for token, proposal in self._remember_proposals.items()
                    if not proposal.get("candidateOnly")}
                self._render = self._render_capture_context = None
                self._provisional_display_map_id = ""
                self._gate.select(mid)
                epoch, engine = self._epoch, self._engine
            self._notify("render-revoked", reason="provisional-candidate-switch", token=old_token)
            return self._solve_observation(action, payload, cancelled, engine, None, epoch,
                observation["captured"], observation["frame"], observation["floor"], observation["digest"],
                observation["capture_generation"], observation["generation"], observation["frame_id"],
                observation["cycle_id"], mid)

    def _manual_action(self, action, payload, cancelled, *, birth_ticket=None, request_id=None):
        def request_current():
            return not self._cancelled(cancelled) and (birth_ticket is None or birth_ticket == (
                self._epoch, self._request_lifetime_generation, self._settings_generation))
        with self._lock:
            if not request_current():
                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
        if self._candidate_provisional_enabled() and (action in ("previous_candidate", "next_candidate")
                and (self._season == "s1" or bool(self._provisional_display_map_id))
                or action == "select_candidate" and (self._season == "s1" or "structureFrameId" in payload)):
            select = self._select_s1_provisional if self._season == "s1" else self._select_candidate_provisional
            return select(action, payload, cancelled,
                birth_ticket=birth_ticket, request_id=request_id)

        if action in ("set_floor", "cycle_floor"):
            return self._set_floor(action, payload, cancelled)
        if action in ("reselect_map", "map_reselection"):
            with self._lock:
                if not request_current():
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                if self._s1_round_locked_map():
                    return self._receipt(action, payload, success=False, reason="identity-locked")
                reselection_source = (self._round, self._frame_id, self._frame_generation, id(self._engine))
                if self._candidate_provisional_enabled():
                    # Clear before waiting for work, so an old scan/ACK cannot
                    # restore the preference after explicit re-identification.
                    self._s1_candidate_preference = None
                    self._clear_private_pool()
                    expected_birth = (self._epoch + 1, self._request_lifetime_generation + 1,
                        self._settings_generation)
                    self.invalidate("candidate-reselection")
                    if (expected_birth != (self._epoch, self._request_lifetime_generation, self._settings_generation)
                            or reselection_source != (self._round, self._frame_id, self._frame_generation, id(self._engine))):
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                reselection_birth = (self._epoch, self._request_lifetime_generation, self._settings_generation)
                def reselection_current():
                    return (not self._cancelled(cancelled) and reselection_birth == (
                        self._epoch, self._request_lifetime_generation, self._settings_generation)
                        and reselection_source == (self._round, self._frame_id, self._frame_generation, id(self._engine)))
            with self._work:
                with self._lock:
                    if not reselection_current():
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                    epoch, mode, season = self._epoch, self._mode, self._season
                    engine = self._engine
                engine = engine or self._get_engine(mode, season)
                with self._lock:
                    if self._s1_round_locked_map():
                        return self._receipt(action, payload, success=False, reason="identity-locked")
                    if not reselection_current() or mode != self._mode or season != self._season:
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
            if not request_current():
                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
            if self._s1_round_locked_map():
                return self._receipt(action, payload, success=False, reason="identity-locked")
            if self._gate is None or (not self._active and not self._candidate_edit):
                return self._receipt(action, payload, success=False, reason="candidate-control-unavailable")
            if action in ("previous_candidate", "next_candidate", "select_candidate"):
                if self._map_id and (action != "select_candidate" or not self._candidate_edit):
                    return self._receipt(action, payload, success=False, reason="identity-locked")
                if action == "select_candidate" and "structureFrameId" in payload:
                    session, ticket = self._s1_structure_session, self._s1_structure_ticket
                    observation = self._last_observation or {}
                    mid = payload.get("mapId")
                    if (self._map_id or not self._map_open or self._capture_pending
                            or session is None or ticket is None
                            or session.catalog.season != self._season or session.catalog.mode != self._mode
                            or payload.get("structureFrameId") != self._frame_id
                            or payload.get("structureGeneration") != self._frame_generation
                            or payload.get("structureRoundId") != self._round
                            or not self._identity_observation_current()):
                        return self._receipt(action, payload, success=False, reason="stale-structure-candidate", stale=True)
                    from s1_structure_selection import StructureCatalog
                    try:
                        check_current = getattr(self._engine, 'qt_assert_resource_current', None)
                        if check_current is not None:
                            check_current()
                        catalog = StructureCatalog.from_engine(self._engine)
                    except (OSError, ValueError, RuntimeError):
                        return self._receipt(action, payload, success=False, reason="stale-structure-candidate", stale=True)
                    if catalog != session.catalog:
                        return self._receipt(action, payload, success=False, reason="stale-structure-candidate", stale=True)
                    choice = session.choose_candidate(mid, ticket)
                    if not choice.get("success") or choice.get("frameKey") != observation.get("digest"):
                        return self._receipt(action, payload, success=False, reason="structure-candidate-not-current", stale=True)
                    self._gate.select(mid)
                    self._selection_pending = True
                    return self._receipt(action, payload, success=True, reason="structure-candidate-selected-awaiting-confirmation",
                        candidateMapId=mid, identityVerified=False, posePending=True)
                count = len(self._candidate_rows)
                if not count:
                    return self._receipt(action, payload, success=False, reason="no-candidates")
                if action == "select_candidate":
                    requested_id = payload.get("candidateMapId")
                    if requested_id is not None:
                        if type(requested_id) is not str:
                            return self._receipt(action, payload, success=False, reason="invalid-candidate-id")
                        index = next((i for i, row in enumerate(self._candidate_rows)
                                      if row["mapId"] == requested_id), -1)
                    else:
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
            manual_map_id = payload.get("mapId")
            if manual_map_id is not None:
                # The picker's choice enters through this same confirm branch:
                # select it exactly as a candidate, then let confirm_route own
                # identity, floor seeding and the fitting hand-off. Reject
                # before touching gate/floor state when no round is active.
                if not self._active:
                    return self._receipt(action, payload, success=False, reason="start-round-before-confirming-map")
                pack = getattr(self._engine, "pack", None)
                if type(manual_map_id) is not str or manual_map_id not in getattr(pack, "by_id", {}):
                    return self._receipt(action, payload, success=False, reason="invalid-manual-map")
                self._candidate_edit = True
                self._gate.select(manual_map_id)
                if not self._floor and not (self._manual_alignment and self._s1_floor_observer_available()):
                    self._floor = str(self._manual_floor
                                      or getattr(pack, "default_entry_floor", "1"))
                    self._floor_required = False
            if not self._active:
                return self._receipt(action, payload, success=False, reason="start-round-before-confirming-map")
            if self._map_id and not self._candidate_edit:
                return self._receipt(action, payload, success=False, reason="identity-locked")
            if ((not self._floor and not (self._manual_alignment and self._s1_floor_observer_available()))
                    or not self._gate.confirm()):
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
                result = self._solve_retained(action, payload, cancelled, expected_epoch=confirmation[0])
            except Exception as error:
                # Legacy also records a valid human identity when pose work raises.
                # Keep the dispatcher's existing pose error/revocation behavior,
                # and retain independent learning diagnostics in its receipt.
                learning = self._learn_confirmation(confirmation, cancelled)
                with self._lock:
                    stale_confirmation = confirmation[0] != self._epoch
                    if not stale_confirmation:
                        self.invalidate("manual-action-error")
                result = self._receipt(action, payload, success=False, stale=stale_confirmation,
                    reason=f"{type(error).__name__}:{error}", error=True,
                    unavailable=bool(getattr(error, "unavailable", False)),
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
        if self._season != "s0":
            return dict(status="skipped", reason="season-manual-learning-unregistered", saved=False)
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
            season = self._season
            bounds = tuple(captured.context.client_screen_bounds)
        # The existing worker/_work owns this one submission. Keep filesystem IO
        # and engine cache reload outside the hot state lock so snapshot/close
        # stay responsive. No later automatic result can retry this submission,
        # including after a partial write/reload failure.
        learning = persist_manual_confirmation(engine, map_id, floor, frame, bounds)
        learning.update(season=season, mode=mode, roundId=round_id, mapId=map_id, floor=floor)
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
            if self._manual_alignment and target is None and not self._s1_floor_observer_available():
                return self._receipt(action, payload, success=False, reason="manual-floor-required")
            self.invalidate("floor-changed")
            self._manual_floor = target
            self._manual_override = target is not None
            self._floor_hard_locked = False
            self._manual_hold_until = self._monotonic() + MANUAL_FLOOR_HOLD_SECONDS if target else 0.0
            self._floor_observations = []
            self._floor_required = target is None and not self._observed_floor
            self._reset_provisional_floor(target or self._observed_floor)
            self._floor = target or self._observed_floor
            if self._manual_alignment:
                self._s1_floor_source = "unknown"
                self._observed_floor = ""
                self._s1_raw_floor = None
                if self._s1_floor_observer_available():
                    self._floor = ""
                    self._floor_required = True
                self._group = None
                self._core.reset_round(reason="manual-floor-target-changed")
            for row in self._candidate_rows:
                row["floor"] = self._floor
            floor_epoch = self._epoch
        # As in legacy select_floor, immediately attempt the target geometry.
        # The worker acquires a current frame; the sensor is kept separately;
        # neither the manual target nor an old pose can grant authorization.
        return self._solve_retained(action, payload, cancelled, expected_epoch=floor_epoch)

    def _solve_retained(self, action, payload, cancelled, *, acquire_current=True, expected_epoch=None):
        with self._lock:
            if expected_epoch is None:
                expected_epoch = self._epoch
            if expected_epoch != self._epoch or self._cancelled(cancelled):
                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
            manual_alignment = self._manual_alignment
        if manual_alignment:
            return self._capture_manual_alignment(action, payload, cancelled, expected_epoch=expected_epoch)
        with self._lock:
            if expected_epoch != self._epoch or self._cancelled(cancelled):
                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
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
                return self._capture_and_solve(action, payload, cancelled, expected_epoch=epoch)
            entry_candidate = None
            if not selected and self._observation_stream_enabled(engine) and not self._manual_selection_unlocked():
                snapshot = getattr(observation['captured'], 'frame_snapshot', None)
                if snapshot is None or snapshot.pixels is not observation['frame']:
                    return self._receipt(action, payload, success=False,
                        reason="retained-frame-snapshot-unavailable", stale=True)
                return self._stream_observation(action, payload, cancelled, engine, epoch,
                    observation['captured'], observation['frame'], floor, observation['digest'],
                    observation['capture_generation'], generation, frame_id, cycle,
                    snapshot)
            if not selected:
                if self._manual_selection_unlocked():
                    return self._receipt(action, payload, success=True,
                        reason="manual-map-selection-pending", posePending=True,
                        manualSelectionPending=True)
                if engine.pack.mode == "difficult" and str(floor) == "2":
                    return self._receipt(action, payload, success=False,
                        reason="difficult-f2-entry-recognition-disabled", posePending=True)
                selected, entry_candidate, stale = self._collect_legacy_entry_evidence(engine, observation["frame"], floor,
                    epoch, frame_id, cancelled)
                if stale:
                    return self._receipt(action, payload, success=False, reason=str(stale), stale=True)
                if not selected:
                    if self._season == "s1":
                        return self._s1_structure_pending_receipt(action, payload)
                    grouped = self._identity_fields()["groupLocked"]
                    self._terminal_failure_sample("similar-group-pending" if grouped else "identity-not-uniquely-verified",
                        payload, observation["captured"], lambda: not self._cancelled(cancelled) and epoch == self._epoch
                            and frame_id == self._frame_id)
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
            report = capabilities[self._mode]
            saved_ready = report["available"]
            live_gaps = tuple(report.get("liveGaps", ()))
            live_ready = report.get("liveReady", self._season == "s0") is True
            if not live_ready and not live_gaps:
                live_gaps = (f"season-live-contract-unavailable:{self._season}",)
            candidate_ready = self._structure_candidate_available(report)
            ready = bool((saved_ready or candidate_ready and report.get('dataReady'))
                and (not live_gaps or candidate_ready))
            guarded = (self._evidence_kind != "live-client-capture"
                       or callable(getattr(self._capture, "is_current", None)))
            # The picker is a separate intent from the advertised confirm_route
            # command. The latter stays tied to a selected candidate so the
            # existing "确认地图" buttons keep their auto/manual behavior.
            # A pick is allowed in both selection modes and with the map shut;
            # confirm_route(mapId) seeds identity/floor and leaves pose pending
            # until a current map frame exists.
            manual_pick_available = bool(
                self._active and (ready or self._manual_alignment) and self._engine is not None and self._gate is not None
                and not self._round_preparing and not self._capture_pending
                and self._group_choice_inflight is None and self._pending_sibling is None
                and not self._s1_round_locked_map())
            commands = list(offline["availableCommands"])
            commands += ["end_round", "close_map", "toggle_sheet_visibility"]
            if ready:
                commands.append("reselect_map")
            manual_ready = bool(self._season == "s1" and saved_ready and guarded and self._capture is not None)
            if manual_ready and not self._active and not self._round_preparing:
                commands.append("prepare_manual_alignment")
            if self._manual_alignment and self._active:
                commands.append("reselect_map")
                if self._map_id and (self._manual_floor or self._s1_floor_observer_available()):
                    commands.append("fit_manual_frame")
                    if (self._map_open or self.map_open_recovery_status().get("pending")) and self._s1_floor_observer_available():
                        commands.append("observe_manual_floor")
            if self._candidate_edit and self._gate is not None:
                commands += ["select_candidate", "previous_candidate", "next_candidate"]
            if self._engine is not None:
                commands.append("set_floor")
            if self._active and self._map_open:
                commands.append("cycle_floor")
                if self._map_id or self._manual_selection_unlocked():
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
                    commands.append("capture_now")
                    if self._active or self._round_preparing:
                        commands.append("map")
                    if self._map_open:
                        commands.append("refresh_map")
                        if self._candidate_probe_available():
                            commands.append("observe_candidate_floor")
            recovery = self.map_open_recovery_status()
            if recovery.get("pending"):
                commands.append("recover_map_open")
            stream_busy = bool(self._candidate_provisional_enabled() and self._capture_pending)
            if stream_busy:
                commands = [cmd for cmd in commands if cmd not in ("select_candidate", "previous_candidate", "next_candidate", "confirm_route")]
            capture_state = getattr(self._capture, "snapshot", {}) if self._capture is not None else {}
            if callable(capture_state):
                capture_state = capture_state()
            metadata = ({key: copy.deepcopy(value) for key, value in self._render.items()
                         if key not in ("pixels",)} if self._render is not None else {})
            identity_fields = self._identity_fields()
            if self._s1_round_locked_map():
                commands = [cmd for cmd in commands if cmd not in (
                    "reselect_map", "map_reselection", "select_candidate", "previous_candidate",
                    "next_candidate", "confirm_route", "select_group_member")]
            return dict(**self._context(), epoch=self._epoch, cycleId=self._cycle, mapId=identity_fields["durableMapId"], floor=self._floor,
                generation=self._frame_generation, preferredProfileId=self._profile,
                effectiveProfileId=metadata.get("effectiveProfileId", ""),
                displayFallback=metadata.get("fallback", False), fallbackReason=metadata.get("fallbackReason", ""),
                engineConnected=True,
                liveEngineAvailable=bool(ready and guarded and self._capture is not None
                    and live_ready),
                recordedStructureCandidateAvailable=candidate_ready and self._evidence_kind == "recorded",
                recordedProvisionalDisplayEnabled=self._s1_provisional_enabled() and self._evidence_kind == "recorded",
                recordedStreamingEnabled=bool(self._recorded_s1_streaming and self._s1_provisional_enabled()),
                nativeCandidatePreviewAvailable=candidate_ready and self._native_s1_candidate_preview,
                candidateStructureAvailable=candidate_ready,
                candidateSupportedFloors=list(self._floor_contract().floor_ids) if candidate_ready else [],
                candidateUnvalidatedFloors=["-3", "-4"] if self._season == "s1" else [],
                candidateProvisionalDisplayEnabled=self._candidate_provisional_enabled(),
                candidateStreamingEnabled=bool(self._candidate_provisional_enabled()
                    and (self._season == 's0' or self._candidate_streaming_requested())),
                structureScanInProgress=stream_busy,
                candidateSwitchUnavailableReason="current-scan-in-progress" if stream_busy else "",
                liveUnavailableReason=(capabilities[self._mode]["reason"] if not saved_ready else
                                       ";".join(live_gaps) if live_gaps else
                                       "capture-context-recheck-unavailable" if not guarded else
                                       "capture-provider-unavailable" if self._capture is None else
                                       capabilities[self._mode]["reason"] if not ready else ""),
                roundActive=self._active, roundPreparing=self._round_preparing, mapOpen=self._map_open,
                fitReportingWaiting=(self._fit_reporting.scope == self._fit_reporting_scope()
                    and self._fit_reporting.waiting and not self._fit_reporting.succeeded),
                mapRecoveryPending=recovery.get("pending", False), mapRecovery=recovery,
                captureInProgress=self._capture_pending,
                mapSelectionMode=self._effective_map_selection_mode(),
                manualSelectionPending=bool(self._active and self._manual_selection_unlocked()),
                manualMapPickAvailable=manual_pick_available,
                manualAlignmentSession=self._manual_alignment,
                manualAlignmentAvailable=manual_ready,
                automaticFloorObservationAvailable=bool(self._manual_alignment and self._s1_floor_observer_available()),
                automaticFloorSupportedFloors=["-1", "-2", "-3", "-4"] if self._s1_floor_observer_available() else [],
                unvalidatedFloorPositions=["-3", "-4"] if self._season == "s1" else [],
                automaticIdentityAvailable=False if self._season == "s1" else bool(ready),
                targetFloor=self._floor, rawObservedFloor=self._s1_raw_floor,
                floorObservationReason=self._s1_floor_reason,
                mapObservationSource=("s1-current-frame" if self._manual_alignment and self._s1_floor_observer_available()
                                      else "manual-declaration" if self._manual_alignment else "automatic"),
                alignedVisible=self._aligned_visible,
                structureEvidence=(self._s1_structure_session.snapshot() if self._s1_structure_session is not None else {}),
                automaticCandidateRows=self._candidate_summary(),
                candidateRows=copy.deepcopy(self._candidate_rows), selectedCandidateIndex=selected,
                selectedCandidate=copy.deepcopy(self._candidate_rows[selected]) if selected >= 0 else {},
                **identity_fields, candidateEditMode=self._candidate_edit,
                poseAttempt=copy.deepcopy(self._pose_attempt), poseResult=copy.deepcopy(self._pose_result),
                siblingPending=self._pending_sibling is not None,
                pendingSiblingToken=self._pending_sibling["render"]["token"] if self._pending_sibling is not None else "",
                availableFloors=self._available_floors(), manualFloor=self._manual_floor,
                observedFloor=self._observed_floor,
                floorSource=self._current_floor_source(),
                floorRequired=self._floor_required, floorPolicyReason=self._last_floor_policy,
                manualFloorHoldRemaining=max(0.0, self._manual_hold_until - self._monotonic())
                    if self._manual_override and not self._floor_hard_locked else 0.0,
                pendingRenderToken=metadata.get("token", ""), eventDeliveryError=self._event_delivery_error,
                availableCommands=sorted(set(commands)), savedFrameCapabilities=capabilities,
                capture=copy.deepcopy(capture_state), render=metadata,
                displayAuthorized=bool(metadata), liveAuthorization=bool(metadata.get("liveAuthorization")),
                privateExperiment=(dict(self._s1_private_pool.snapshot(),
                    privateExperimental=self._native_s1_private_experiment,
                    privateResourceDigest=self._s1_private_pool.scope[4].resource_key) if getattr(self, "_s1_private_pool", None)
                    else dict(privateExperimental=bool(self._native_s1_private_experiment and self._season == "s1" and not self._manual_alignment),
                        privateExperimentalLocked=False, privateLockMapId="", privatePoolMapIds=[])),
                message=self._message, core=self._core.snapshot(), evidenceKind=self._evidence_kind)

    def _candidate_summary(self):
        """UI identity rows, without repeated solver evidence trees.

        Ranking retains its full evidence internally. The Qt state transport is
        read repeatedly by controls/status bindings; copying every candidate's
        nested fit diagnostics here used to stall both presentation and floor
        events even after the worker had finished. Keep order, identities and
        verdicts unchanged, and never feed this UI projection back to ranking.
        """
        fields = ("key", "mapId", "identityKind", "groupId", "memberIds", "name",
                  "score", "identityHardGatePassed", "strongSuggestion", "reason",
                  "mode", "entryKind", "direction", "inliers", "identityElapsedMs",
                  "identityBudgetExceeded")
        return [{key: copy.deepcopy(row[key]) for key in fields if key in row}
                for row in self._automatic_candidate_rows]

    def _fit_reporting_scope(self, map_id=None, floor=None):
        return (self._epoch, self._mode, self._round, self._cycle,
                self._map_id if map_id is None else map_id,
                str(self._floor if floor is None else floor))

    def _fence_earlier_dispatches(self):
        """Under _lock, keep queued requests with their original boundary."""
        owners = getattr(self._dispatch_owners, "arrivals", ())
        # A command fences earlier arrivals. External settings/resource changes
        # have no command owner and fence everything that already arrived.
        boundary = owners[-1] if owners else next(self._dispatch_serials)
        self._dispatch_cutoff = max(self._dispatch_cutoff, boundary)

    def invalidate(self, reason="invalidated", *, source="", input_sequence=None):
        """Revoke immediately; return whether this close emitted a fit summary."""
        with self._lock:
            candidate_preview = self._candidate_provisional_enabled()
            # A static reference is explicitly historical, not a retained
            # pose. Normal reopen must not blank it while acquiring fresh
            # evidence; conflict/reselection/context boundaries still revoke.
            keep_static_floor_intent = (self._native_s1_final_round_lock
                and reason in ("game-floor-control-intent", "floor-changed", "floor-unknown"))
            if keep_static_floor_intent:
                self._floor_required = True
            if not keep_static_floor_intent and reason not in ("map-closed", "escape", "foreground-lost", "map-key-open",
                              "s1-provisional-open-intent"):
                self._clear_candidate_reference()
            if any(word in reason for word in ("resource", "context", "settings", "identity", "reselection", "candidate-selected", "end-round", "end_round", "reset_round", "stop_round")):
                self._s1_candidate_preference = None
                self._clear_private_pool()
            if any(word in reason for word in ("resource", "context", "end-round")):
                for handle in self._stream_priority_handles.values():
                    handle.invalidate()
                self._stream_priority_handles.clear()
            self._provisional_display_map_id = ""
            if candidate_preview or self._candidate_preview_requested() and self._season == "s1":
                self._capture_pending = False
                self._capture_pending_owner = None
            self._map_recovery = None
            if self._s1_structure_session is not None:
                self._s1_structure_session.revoke_current()
                self._s1_structure_ticket = None
                self._automatic_candidate_rows = []
                for row in self._candidate_rows:
                    row["structureSupported"] = False
            self._failure_capture_stamp = None
            self._failure_control_observation = None
            if self._diagnostics is not None:
                self._diagnostics.record("invalidate", reason=str(reason), source=source, epoch=self._epoch,
                    season=self._season, mode=self._mode, mapId=self._map_id, floor=str(self._floor), frameId=self._frame_id,
                    roundId=self._round, cycleId=self._cycle, probeMisses=self._probe_misses)
            # Finalize only a real close. Other boundaries discard an incomplete
            # segment; they are not evidence that an opening failed.
            self._fit_reporting.select(self._fit_reporting_scope())
            report = self._fit_reporting.close() if reason in ("map-closed", "escape") else None
            self._fit_reporting.discard()
            if report is not None:
                self._notify("map-fit-incomplete", reason="map-fit-incomplete", fitReport=report)
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
            sequenced_s1_close = (self._candidate_preview_requested() and self._season == "s1"
                and reason == "map-closed" and type(input_sequence) is int
                and input_sequence == self._last_map_input_sequence)
            if reason != "s1-provisional-open-intent" and not sequenced_s1_close:
                self._request_lifetime_generation += 1
                # Requests that arrived before this hard boundary cannot take a
                # fresh birth snapshot after waiting for the state lock.
                self._fence_earlier_dispatches()
            if self._manual_alignment and self._s1_floor_observer_available():
                self._observed_floor = ""
                self._s1_raw_floor = None
                self._s1_floor_source = "unknown"
                self._s1_floor_reason = str(reason)
                self._floor = ""
                self._floor_required = True
            self._core.close(reason=str(reason))
            self._render = None
            self._render_capture_context = None
            if reason in ("map-closed", "escape", "foreground-lost", "capture-context-changed", "floor-unknown"):
                self._map_open = False
            if "resource" in str(reason):
                self._manual_alignment = False
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
            self._publish_game_pointer_context_locked()
            # Only an actual map-close decision reconciles the physical key
            # toggle. An unsupported floor revokes authority, not physical
            # map-open intent; display/focus/resource invalidations are not closes.
            # Build/emit while holding this lifetime lock so a newer request
            # cannot lend its sequence or context to an older close decision.
            capture = getattr(self._capture, "snapshot", {})
            capture = capture() if callable(capture) else capture
            self._notify("render-revoked", reason=str(reason), token=previous_token,
                **({'invalidationOrigin': source} if source else {}),
                mapClosed=reason in ("map-closed", "escape"),
                inputSequence=self._last_map_input_sequence,
                inputWindow=str(capture.get("hwnd", "")))
            return report is not None

    def set_event_callback(self, callback):
        """Install a thread-safe emitter only; it must queue work to Qt's owner."""
        if callback is not None and not callable(callback):
            raise TypeError("event callback must be callable or None")
        with self._lock:
            self._event_callback = callback

    @staticmethod
    def _private_diagnostic_fields():
        return ("privateExperimental", "privateExperimentalLocked", "privateLockMapId",
            "privatePoolMapIds", "privateEliminatedMapIds", "privateObservationCount",
            "privatePoolBefore", "privatePoolAfter", "privateContradictedMapIds", "privateUnknownMapIds",
            "privateResourceDigest", "privateCoordinateReason", "privateCoordinateVerified", "privateCoordinateBasis",
            "privateScaleRange", "privateCoordinateErrorPx", "privateHypothesisCount", "privateGateStatus",
            "privateGateReason", "privateGateMs", "privateObservationMs", "privateCompareMs", "privateTotalMs",
            "privateFrameDigest", "privateScanMs", "privateMaterialMs", "privatePoseDomainNarrow",
            "privatePoolId", "privatePoolRevision", "privateRecoveryRevision", "privateRecoveryPending",
            "privatePreviousLockMapId")

    def _notify(self, event, *, reason="", token="", **extra):
        with self._lock:
            callback = self._event_callback
            packet = dict(event=event, reason=reason, token=token, epoch=self._epoch,
                **self._context(), mapId=self._map_id, floor=self._floor,
                cycleId=self._cycle, generation=self._frame_generation,
                alignedVisible=self._aligned_visible, **extra)
            if self._diagnostics is not None:
                packet["producedAt"] = time.monotonic()
                self._diagnostics.record("runtime-event", eventName=event, reason=reason, token=token,
                    producedAt=packet["producedAt"], season=self._season, mode=self._mode, mapId=self._map_id,
                    floor=str(self._floor), frameId=self._frame_id, epoch=self._epoch,
                    roundId=self._round, cycleId=self._cycle, generation=self._frame_generation,
                    **{key: extra[key] for key in ("candidateReuse", "scanSkipped", *self._private_diagnostic_fields()) if key in extra})
        if callback is not None:
            try:
                callback(packet)
                with self._lock:
                    self._event_delivery_error = ""
            except Exception as error:
                with self._lock:
                    self._event_delivery_error = f"{type(error).__name__}:{error}"[:256]

    def _revoke_current_candidates(self, *, preserve_reference=False):
        """Called under the lifetime lock before acquisition and on revocation."""
        if not preserve_reference:
            self._clear_candidate_reference()
        self._provisional_display_map_id = ""
        if self._s1_structure_session is not None:
            self._s1_structure_session.revoke_current()
            self._s1_structure_ticket = None
            self._automatic_candidate_rows = []
            for row in self._candidate_rows:
                row["structureSupported"] = False

    def _retire_candidate_preview(self, *, reason):
        """Retire preview pixels/proposals before an identity writer commits.

        The caller holds ``_lock`` and sends render-revoked after completing its
        atomic identity update. Preserve the ticket so the lock's pose consumer
        can independently validate/reuse the same current map/floor/frame fit.
        """
        row, reference = self._render, self._reference_sheet or {}
        owns_pixels = bool(row is not None and row.get("candidateDisplayOnly"))
        owns_context = owns_pixels or row is None and bool(self._provisional_display_map_id)
        owns_reference = bool(reference.get("candidateReference") or reference.get("s1CandidateReference"))
        # A new acquisition may already have revoked pixels. Candidate-only
        # memory still belongs to the retiring hypothesis, not the new identity.
        self._clear_candidate_reference()
        if owns_context or owns_reference:
            self._provisional_reference_context = None
        self._s1_candidate_preference = self._s1_candidate_pose_hint = None
        self._remember_proposals = {key: proposal for key, proposal in self._remember_proposals.items()
            if not proposal.get("candidateOnly")}
        self._provisional_display_map_id = ""
        if owns_context:
            self._transient_drag = self._transient_cycle = None
        if not owns_pixels:
            return ""
        self._core.revoke_display(reason=reason)
        self._render = self._render_capture_context = None
        return row["token"]

    def _revoke_render(self, reason):
        """Revoke presentation without invalidating the current worker lifetime."""
        with self._lock:
            self._revoke_current_candidates(
                preserve_reference=(reason in ("new-capture", "new-provisional-capture")
                    or self._season == "s1" and self._native_s1_final_round_lock
                    and reason not in ("s1-candidate-excluded", "private-contradiction-recovery")))
            token = self._render.get("token", "") if self._render is not None else ""
            self._discard_sibling()
            self._core.revoke_display(reason=reason)
            self._render = None
            self._render_capture_context = None
        self._notify("render-revoked", reason=reason, token=token)

    def latest_map_frame(self):
        """(BGR frame, mode, fixed floor read) of the last open-map frame, or None."""
        return self._latest_map_frame

    def configure_settings(self, settings):
        """Update explicit solver preferences; mode/profile use set_context."""
        with self._lock:
            candidate_preview = self._candidate_provisional_enabled()
            variant_changed = settings.get("zhan10CoffinVariantId", "variant-01") != self._settings.get("zhan10CoffinVariantId", "variant-01")
            numbers_changed = settings.get("routePointNumbers", False) != self._settings.get("routePointNumbers", False)
            numbers_display_changed = numbers_changed and self._profile.startswith("custom:")
            settings_changed = dict(settings) != self._settings
            solver_changed = self._s1_solver_policy(settings) != self._s1_solver_policy(self._settings)
            self._settings = copy.deepcopy(dict(settings))
            if solver_changed:
                self._s1_solver_policy_generation += 1
            if settings_changed:
                self._settings_generation += 1
                if candidate_preview:
                    self._fence_earlier_dispatches()
                if (solver_changed and self._candidate_preview_requested() and self._season == "s1"
                        and not self._durable_map_id):
                    # Solver changes revoke identity evidence. Display-only saves
                    # retire stale presentation work through its own generation,
                    # without clearing the native round pool or pose proposals.
                    self._s1_candidate_pose_hint = None
                    self.invalidate("s1-candidate-settings-changed")
            if variant_changed or numbers_display_changed:
                self._display_generation = getattr(self, "_display_generation", 0) + 1
                if (self._profile, self._map_id, str(self._floor)) == ("zhan10-coffin", "nightmare-01", "1"):
                    # Retain identity, floor and canonical pose. Only presentation
                    # authority expires; the next normal refresh revalidates it.
                    self._revoke_render("coffin-variant-changed")
            visible = bool(settings.get("sheetVisibilityEnabled", self._aligned_visible))
            changed = visible != self._aligned_visible
            self._aligned_visible = visible
            self._publish_game_pointer_context_locked()
        if changed:
            self._notify("aligned-visibility-changed", reason="settings-updated")
        if numbers_display_changed:
            # Retain the verified pose for display-only reprojection. Public
            # payloads reject the old generation immediately, including while
            # a slower worker is still preparing the replacement pixels.
            self._notify("render-revoked", reason="route-point-numbers-changed")

    def manual_map_catalog(self):
        """Read-only picker catalog for the current mode/profile.

        Identity ranking is deliberately not invoked. The returned identity is
        compared by the picker so a late result after a mode/resource change
        cannot present stale maps.

        Only the already-prepared engine is read. Constructing a mode factory
        here would be heavy, uncached and would serialize with capture under
        ``_work``; a pick before resources are ready reports that state.
        """
        # The engine is assigned under the state lock before a round becomes
        # active, and its pack is immutable afterwards. Copy the map sequence
        # under that lock, then build the read-only rows without holding either
        # the state lock or the capture/solver work lock.
        with self._lock:
            season, mode, profile, engine = self._season, self._mode, self._profile, self._engine
            pack = getattr(engine, "pack", None) if engine is not None else None
            if (pack is None or getattr(pack, "mode", mode) != mode
                    or not getattr(pack, "maps", None)):
                return dict(season=season, mode=mode, profileId=profile, available=False,
                            message="当前模式的地图资源未就绪，暂时不能选图")
            items = list(pack.maps)
            default_entry_floor = str(getattr(pack, "default_entry_floor", "1"))
            native = getattr(engine, "qt_native_context", None)
            scope = (season, mode, profile, tuple((str(item.id), str(getattr(item, "name", "") or ""),
                                           str(getattr(item, "direction", "") or ""))
                                          for item in items))
            cache = self._manual_map_thumbnail_cache
            if cache.get("pack") is not pack or cache.get("scope") != scope:
                cache.clear()
                cache.update(pack=pack, scope=scope, rows={})
            cached_thumbnails = dict(cache["rows"])
        # File checks are worker-side (this method is the picker's catalog
        # provider); the UI only receives URIs. Cache only resolved paths and
        # key them to the exact pack and catalog identity so a mode/resource
        # change can never reuse the previous pack's thumbnails.
        resolved = {}
        for item in items:
            map_id = str(item.id)
            if map_id in cached_thumbnails:
                continue
            path = manual_map_thumbnail_source(pack, item, mode, native)
            if path is not None:
                resolved[map_id] = str(path)
        if resolved:
            with self._lock:
                cache = self._manual_map_thumbnail_cache
                if (self._engine is engine and self._season == season and self._mode == mode and self._profile == profile
                        and cache.get("pack") is pack and cache.get("scope") == scope):
                    cache["rows"].update(resolved)
        maps = []
        for item in items:
            map_id = str(item.id)
            path = resolved.get(map_id) or cached_thumbnails.get(map_id) or ""
            if path and not Path(path).is_file():
                path = ""
            stamp = ""
            if path:
                try:
                    stat = Path(path).stat()
                    stamp = f"{stat.st_mtime_ns}-{stat.st_size}"
                except OSError:
                    path = ""
            maps.append(dict(id=map_id, name=str(getattr(item, "name", "") or ""),
                             direction=str(getattr(item, "direction", "") or ""),
                             thumbnailPath=path, thumbnailStamp=stamp))
        identity = dict(season=season, mode=mode, profileId=profile,
                        maps=[(row["id"], row["name"], row["direction"]) for row in maps])
        return dict(season=season, mode=mode, profileId=profile, available=True, identity=identity, maps=maps,
                    defaultEntryFloor=default_entry_floor)

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
                pose_current = bool(source and self._display_current(source)
                    and self._capture_is_current(self._render_capture_context, source.get("captureGeneration")))
                candidate_proposal = None
                if (not map_id and self._candidate_provisional_enabled() and pose_current
                        and source.get("candidateDisplayOnly") and not source.get("transient")
                        and source.get("mapId") == self._provisional_display_map_id):
                    proposal = self._remember_proposals.get(source["token"])
                    session = self._s1_structure_session
                    if (proposal is not None and proposal.get("candidateOnly")
                            and proposal.get("candidateSession") is session and session is not None
                            and proposal.get("candidateTicket") == self._s1_structure_ticket
                            and session.choose_candidate(source["mapId"], self._s1_structure_ticket).get("success")):
                        # Local display target only. Never write _map_id, a gate,
                        # durable identity or a fresh canonical pose from this.
                        map_id = source["mapId"]
                        candidate_proposal = proposal
                if (not map_id and sheet is not None and (sheet.get("candidateReference") or sheet.get("s1CandidateReference"))
                        and self._candidate_reference_current(sheet, check_display=False)):
                    # Closed-map author changes need no current pose and must
                    # not promote a candidate into the legacy identity owner.
                    map_id = sheet["mapId"]
                if not self._active or engine is None or not map_id or not floor:
                    return self._receipt(action, payload, success=True, reason="context-updated")
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
                pixels = _display_rgba(layer)
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
                if (sheet is not None and (sheet.get("candidateReference") or sheet.get("s1CandidateReference"))
                        and (self._reference_sheet is not sheet
                             or not self._candidate_reference_current(sheet, check_display=False))):
                    return self._receipt(action, payload, success=False, reason="stale-reference-context", stale=True)
                if candidate_proposal is not None:
                    session = candidate_proposal["candidateSession"]
                    if (session is not self._s1_structure_session
                            or candidate_proposal["candidateTicket"] != self._s1_structure_ticket
                            or not session.choose_candidate(map_id, self._s1_structure_ticket).get("success")):
                        return self._receipt(action, payload, success=False, reason="stale-candidate-display", stale=True)
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
                    if candidate_proposal is not None:
                        # New pixels require a new ACK, bound to the already
                        # current native frame/fit. The retired token cannot ACK.
                        self._register_material_proposal(self._render,
                            candidate_proposal["frame"], candidate_proposal["fit"],
                            candidate_proposal["captured"], None, candidate_only=True)
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
            season = self._season
        return coffin_variant_metadata(*context, season=season)

    def route_preferences_context(self):
        with self._lock:
            return dict(season=self._season, mode=self._mode, profileId=self._profile, mapId=self._map_id,
                        floor=str(self._floor), resourceIdentity=str(id(self._engine)))

    def configure_failure_sampling(self, *, event_path, build_fingerprint, source_commit=""):
        self._failure_samples.configure(event_path=event_path,
            build_fingerprint=build_fingerprint, source_commit=source_commit)

    def set_failure_sampling_enabled(self, enabled):
        self._failure_samples.enabled = enabled is True
        if not self._failure_samples.enabled:
            self._failure_capture_stamp = None

    def set_automatic_failure_sampling_enabled(self, enabled):
        self._failure_samples.automatic_enabled = enabled is True
        if not enabled:
            self._failure_capture_stamp = None
            self._failure_control_observation = None

    def _automatic_failure_sample(self, reason, *, captured=None, attempt_id=None, current=None, diagnostic=None):
        """Diagnostic-only reuse of the existing guarded capture; never recapture."""
        try:
            return self._record_automatic_failure_sample(reason, captured=captured, attempt_id=attempt_id, current=current, diagnostic=diagnostic)
        except Exception as error:
            result = dict(saved=False, sampling='automatic', missingReason='sample-preparation-error', error=type(error).__name__)
            try:
                self._notify('automatic-failure-sample', reason='metadata-only', failureSample=result)
            except Exception:
                pass
            return result


    def shutdown(self):
        self.stop_private_evidence()
        self.set_automatic_failure_sampling_enabled(False)
        self.set_failure_sampling_enabled(False)
        self.dispatch("end_round")
        if self._capture is not None:
            self._capture.shutdown()

    def render_payload(self, token):
        with self._lock:
            row = self._render
            if row is not None and not _engine_resource_current(self._engine):
                self.invalidate("resource-context-superseded")
                return None
            if (row is None or row["token"] != token or not self._display_current(row)
                    or row.get("displayGeneration") != getattr(self, "_display_generation", 0)):
                return None
            if not self._capture_is_current(self._render_capture_context, row.get("captureGeneration")):
                self.invalidate("capture-context-changed")
                return None
            return {key: value if key == "pixels" else copy.deepcopy(value) for key, value in row.items()}

    @staticmethod
    def _reference_owner_context(row):
        return tuple(row.get(key) for key in ("season", "mode", "roundId", "mapId", "floor",
            "engineIdentity", "captureGeneration"))

    def _remember_provisional_reference(self):
        row = self._reference_sheet
        if (row is not None and not self._durable_map_id and self._map_open
                and row.get("mapId") == self._map_id and row.get("floor") == self._floor
                and self._render is not None and self._display_current(self._render)):
            self._provisional_reference_context = self._reference_owner_context(row)

    def _clear_candidate_reference(self):
        """Revoke candidate static pixels, independent of confirmed references."""
        if ((self._reference_sheet or {}).get("candidateReference")
                or (self._reference_sheet or {}).get("s1CandidateReference")):
            self._reference_sheet = self._reference_capture_context = None

    def _prepare_s1_static_reference(self, engine, pool, captured, floor, current):
        """Prepare a selected map's static floor, without requesting a pose."""
        import numpy as np
        with self._lock:
            if not current() or self._s1_private_pool is not pool:
                return
            previous = self._reference_sheet or {}
            map_id = pool.locked_id or previous.get("mapId")
            if not map_id or map_id not in pool.remaining:
                return
            if previous.get("mapId") == map_id and self._candidate_reference_current(previous):
                return
            profile, generation = self._profile, getattr(self, "_display_generation", 0)
            variant = self._settings.get("zhan10CoffinVariantId", "variant-01")
        try:
            display = self._display_layer(engine, map_id, floor, profile, variant_id=variant)
            pixels = _display_rgba(display)
        except (ValueError, KeyError, OSError):
            return  # Missing static resources cannot be replaced by old-floor pixels.
        with self._lock:
            if (not current() or self._s1_private_pool is not pool or map_id not in pool.remaining
                    or profile != self._profile or generation != getattr(self, "_display_generation", 0)):
                return
            self._reference_sheet = dict(token=uuid4().hex, pixels=pixels,
                season=self._season, mode=self._mode, profileId=profile, mapId=map_id,
                floor=str(floor), roundId=self._round, engineIdentity=id(engine),
                captureGeneration=self._capture_generation(), displayGeneration=generation,
                variantId=display.get("variantId"), resourceIdentity=display["resourceIdentity"],
                referenceAuthorized=self._evidence_kind == "live-client-capture", evidenceKind=self._evidence_kind,
                preferredProfileId=profile, effectiveProfileId=display.get("effectiveProfileId", display["routeProfileId"]),
                displayKind=display["displayKind"], displayLabel=display["displayLabel"],
                routeProfileId=display["routeProfileId"], authorStatus=display.get("authorStatus", "none"),
                fallback=display.get("fallback", False), fallbackReason=display.get("fallbackReason", ""),
                s1CandidateReference=True, s1ReferencePoolIdentity=id(pool),
                s1ReferenceRecoveryRevision=pool.recovery_revision,
                referenceAuthorizationScope="current-candidate-static-reference", **self._identity_fields())
            self._reference_capture_context = captured.context
        self._notify("reference-ready", reason="reference-sheet")

    def _candidate_reference_current(self, row, *, check_display=True):
        """Display-only ownership, independent of the legacy identity gate.

        A successful current candidate material may be kept across map close.
        It is never a new pose or a durable map identity. Routine recapture
        retains it until replaced; explicit reselection, conflicting floor or
        candidate evidence and hard boundaries revoke it before blocking.
        """
        pool = self._s1_private_pool
        historical_floor = bool(self._season == "s1" and self._native_s1_final_round_lock
            and (self._floor_required or not self._observed_floor))
        candidate_reference = row.get("candidateReference") or row.get("s1CandidateReference")
        qualified = (self._candidate_preview_requested() if self._season == "s1"
            else self._candidate_provisional_enabled())
        return bool(candidate_reference and qualified and not self._manual_alignment
            and self._active and not self._selection_pending
            and (historical_floor or not self._floor_required
                and not self._current_frame_floor_conflicts(self._floor) and row.get("floor") == self._floor)
            and all(row.get(key) == expected for key, expected in (
                ("season", self._season), ("mode", self._mode),
                ("roundId", self._round), ("engineIdentity", id(self._engine)),
                ("captureGeneration", self._capture_generation()),
                ("s1ReferencePoolIdentity", id(pool) if pool is not None else None),
                ("s1ReferenceRecoveryRevision", pool.recovery_revision if pool is not None else None)))
            and (self._evidence_kind != "live-client-capture" or row.get("captureGeneration") is not None)
            and (not check_display or (row.get("profileId") == self._profile
                and row.get("displayGeneration") == getattr(self, "_display_generation", 0)))
            and (pool is None or row.get("mapId") in pool.remaining))

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
            if row is not None and (row.get("candidateReference") or row.get("s1CandidateReference")):
                if not self._candidate_reference_current(row):
                    return None
                # Static pixels retain their owner; formal identity metadata is
                # current and must reflect a later scoped lock without repaint.
                identity = self._identity_fields()
                locked = bool(identity["s1FormalRoundLock"] and identity["identityLocked"]
                    and identity["durableMapId"] == row["mapId"])
                return dict({key: value if key == "pixels" else copy.deepcopy(value)
                        for key, value in row.items()}, identityLocked=locked,
                    durableMapId=row["mapId"] if locked else "", provisional=not locked,
                    identityPhase="MEMBER_VERIFIED" if locked else "CANDIDATE_DISPLAY",
                    referenceFloorCurrent=bool(not self._floor_required and self._observed_floor
                        and row.get("floor") == self._floor == self._observed_floor))
            if (row is None or not self._active or self._selection_pending or self._floor_required
                    or self._current_frame_floor_conflicts(self._floor)
                    or self._gate is None or self._gate.confirmed_id != self._map_id
                    or any(row.get(key) != expected for key, expected in (
                        ("season", self._season), ("mode", self._mode), ("profileId", self._profile), ("mapId", self._map_id),
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
                valid_provisional = (self._render is not None and self._display_current(self._render) if self._map_open
                    else self._provisional_reference_context == self._reference_owner_context(row))
                if not valid_provisional:
                    return None
            return {**{key: value if key == "pixels" else copy.deepcopy(value) for key, value in row.items()},
                    **self._identity_fields()}





    def _failure_capture_scope(self):
        # An image belongs to its originating lifetime, never the state at save time.
        return (self._epoch, self._round, self._season, self._mode, self._profile,
            id(self._engine), getattr(self, "_display_generation", 0))

    @staticmethod
    def _new_diagnostic_attempt(payload):
        payload["_diagnosticAttemptId"] = uuid4().hex
        return payload["_diagnosticAttemptId"]

    def _failure_diagnostic(self, payload):
        from client_input_diagnostics import number
        return dict(attemptId=payload.get("_diagnosticAttemptId"),
            openingId=payload.get("_diagnosticOpeningId"),
            inputSequence=number(payload.get("inputSequence")),
            requestGeneration=number(payload.get("generation")),
            epoch=self._epoch, roundId=self._round, cycleId=self._cycle,
            frameId=self._frame_id, sourceFrameGeneration=self._frame_generation)

    @staticmethod
    def _map_attempt_metadata(payload):
        from client_input_diagnostics import number
        import re
        result = {}
        for source, target in (("_diagnosticAttemptId", "attemptId"),
                ("_diagnosticOpeningId", "openingId"), ("recoveryId", "recoveryId")):
            value = payload.get(source)
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value):
                result[target] = value
        for source, target in (("inputSequence", "inputSequence"),
                ("generation", "requestGeneration"), ("inputCreatedAt", "inputCreatedAt")):
            value = number(payload.get(source))
            if value is not None:
                result[target] = value
        return result

    def _capture_diagnostic(self, payload, phase, reason, *, action=None, captured=None, error=None, observed=None):
        if action == "refresh_map":
            return
        try:
            from client_preparation_diagnostics import capture_metadata, exception_metadata, PreparationDiagnostics
            values = {}
            if phase == "capture-result":
                values["capture"] = capture_metadata(captured=captured, error=error)
            if error is not None:
                values["exception"] = exception_metadata(error)
            if observed is not None:
                values["observation"] = PreparationDiagnostics.observation(*observed)
            self._notify("map-capture-diagnostic", phase=phase, reason=reason,
                mapAttempt=self._map_attempt_metadata(payload), **values)
        except Exception:
            pass  # Diagnostics must never change capture/observation decisions.

    def _terminal_failure_sample(self, reason, payload, captured, current, diagnostic=None):
        return self._automatic_failure_sample(reason, captured=captured,
            attempt_id=payload.get("_diagnosticAttemptId"), current=current,
            diagnostic=dict(self._failure_diagnostic(payload), **(diagnostic or {})))

    def _record_automatic_failure_sample(self, reason, *, captured=None, attempt_id=None, current=None, diagnostic=None):
        stamp = self._failure_capture_stamp
        def unavailable():
            if captured is None:
                return 'legal-captured-frame-unavailable'
            if stamp is None or len(stamp) < 6 or stamp[0] is not captured:
                return 'capture-attempt-unavailable'
            if not attempt_id or stamp[4] != attempt_id:
                return 'capture-attempt-mismatch'
            if stamp[5] != self._failure_capture_scope():
                return 'capture-scope-changed'
            if (not self._active or not self._failure_samples.automatic_enabled
                    or current is not None and not current()
                    or self._failure_capture_stamp is not stamp
                    or not self._capture_is_current(captured.context, stamp[3])):
                return 'capture-not-current'
            return ''
        def valid():
            return not unavailable()
        missing = unavailable()
        frame = None
        if not missing:
            try:
                frame = self._full_frame(captured)
            except Exception:
                missing = 'capture-frame-invalid'
        data = dict(diagnostic or {}, evidenceKind=self._evidence_kind,
                    season=self._season, mode=self._mode, contextKey=context_key(self._mode, self._season),
                    attemptId=attempt_id, frameUnavailableReason=missing,
                    frameCapturedAtUtc=stamp[1] if frame is not None else None,
                    frameCapturedMonotonicAt=stamp[2] if frame is not None else None,
                    captureGeneration=stamp[3] if frame is not None else None,
                    **self._failure_samples.metadata)
        root = getattr(getattr(self._engine, 'memory_store', None), 'root', None)
        controls = getattr(self, '_failure_control_observation', None)
        control_bounds = controls[1] if controls is not None and controls[0] is captured else None
        metadata_current = lambda: bool(self._active and self._failure_samples.automatic_enabled
            and (current is None or current()))
        if reason == 'current-floor-unavailable':
            # Floor failures need the searched control region, not unrelated map pixels.
            control_frame = frame if control_bounds is not None else None
            if frame is not None and control_bounds is None:
                data['frameUnavailableReason'] = 'detector-control-band-unavailable'
            result = self._failure_samples.record_automatic(root=root, reason=reason, frame=control_frame,
                diagnostic=dict(data, sampleRole='detector-control-band'),
                current=valid if control_frame is not None else metadata_current,
                crop_bounds=control_bounds, crop_provenance='detector-control-band')
        else:
            result = self._failure_samples.record_automatic(root=root, reason=reason, frame=frame,
                diagnostic=data, current=valid if frame is not None else metadata_current)
            if (reason not in ('identity-not-uniquely-verified', 'similar-group-pending', 'candidate-awaiting-confirmation')
                    and control_bounds is not None and frame is not None):
                result['controlBand'] = self._failure_samples.record_automatic(root=root, reason=reason, frame=frame,
                    diagnostic=dict(data, sampleRole='detector-control-band'), current=valid,
                    crop_bounds=control_bounds, crop_provenance='detector-control-band')
        saved_any = result['saved'] or result.get('controlBand', {}).get('saved', False)
        key = (self._season, self._mode, reason, result.get('missingReason'),
            result.get('controlBand', {}).get('missingReason'))
        now = time.monotonic()
        notices = self._failure_notice_limits
        previous = notices.get(key)
        if not saved_any and previous is not None and now < previous[0]:
            notices[key] = (previous[0], min(2**31-1, previous[1]+1))
            return result
        if len(notices) >= 64 and key not in notices:
            notices.pop(next(iter(notices)))
        notices[key] = (now+60., 0)
        self._notify('automatic-failure-sample', reason='saved' if saved_any else 'metadata-only',
                     failureSample=dict(result, suppressed=previous[1] if previous else 0),
                     mapAttempt={key: data[key] for key in
                         ('attemptId', 'openingId', 'inputSequence') if data.get(key) is not None})
        return result

    def _recognition_diagnostic(self, stage, reason, **values):
        _test_evidence("diagnostic", stage, reason, values)
        try:
            from recognition_diagnostics import bounded_recognition, scale_observations
            if stage == 'floor-observation':
                observation = values.pop('observation')
                values.update(mapOpen=observation.get('mapOpen'), fixedFloor=observation.get('floor'),
                    floor=(values.get('floorRead') or {}).get('selectedFloor'),
                    scales=scale_observations(observation), thresholds=observation.get('thresholds'))
            evidence = bounded_recognition(dict(stage=stage, reason=reason, **values))
            self._notify('recognition-diagnostic', reason=reason, recognitionDiagnostics=evidence)
        except Exception:
            pass  # Diagnostics cannot replace the algorithm's result/exception.

    def _remember_control_observation(self, frame, mode, observation, floor_read, *, captured=None):
        _test_evidence("control-input", frame, mode, observation, floor_read)
        try:
            stamp = self._failure_capture_stamp
            if (self._failure_samples.automatic_enabled and stamp is not None
                    and (getattr(stamp[0], 'frame_bgr', None) is frame or stamp[0] is captured)
                    and stamp[5] == self._failure_capture_scope()):
                from recognition_diagnostics import control_band
                self._failure_control_observation = (stamp[0], control_band(frame, mode, observation, floor_read))
        except Exception:
            self._failure_control_observation = None

    def _probe_candidate_floor(self, action, payload, cancelled, *, expected_epoch, request_id):
        """Observe the opening without withdrawing its successful material.

        Unchanged pixels or a verified residual need no catalogue scan. This
        observation never renews candidate/identity evidence. Changed geometry
        uses the normal scanner on these captured pixels before replacement.
        """
        with self._work:
            with self._lock:
                if (expected_epoch != self._epoch or self._cancelled(cancelled)
                        or not self._candidate_probe_available()
                        or self._manual_alignment or not self._active or not self._map_open):
                    return self._receipt(action, payload, success=False, reason="candidate-probe-not-current", stale=True)
                engine, epoch, frame_id = self._engine, self._epoch, self._frame_id
                if not _engine_resource_current(engine):
                    self.invalidate("resource-context-superseded")
                    return self._receipt(action, payload, success=False, reason="resource-context-superseded", stale=True)
                if self._transient_drag is not None:
                    return self._receipt(action, payload, success=True, reason="refresh-coalesced", coalesced=True)
                transient = self._transient_cycle_current()
                render_owner = self._refresh_render_owner()
                owner = self._capture_pending_owner = (epoch, request_id)
                self._capture_pending = True
                capture_generation = self._capture_generation()
            replacement_started = False
            obsolete = lambda: (self._cancelled(cancelled) or epoch != self._epoch
                or engine is not self._engine or not replacement_started and frame_id != self._frame_id)
            try:
                if transient:
                    return self._probe_map_after_drag(payload, cancelled)
                captured = self._capture_frame(cancelled, preserve_candidate=True)
                frame = self._full_frame(captured)
                with self._lock:
                    if obsolete():
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                    if not _engine_resource_current(engine):
                        self.invalidate("resource-context-superseded")
                        return self._receipt(action, payload, success=False, reason="resource-context-superseded", stale=True)
                    if not self._capture_is_current(captured.context, capture_generation):
                        self.invalidate("capture-context-changed")
                        return self._receipt(action, payload, success=False, reason="capture-context-changed", stale=True)
                observation, floor, read = self._observe(engine, frame)
                with self._lock:
                    if obsolete():
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                    if not _engine_resource_current(engine):
                        self.invalidate("resource-context-superseded")
                        return self._receipt(action, payload, success=False, reason="resource-context-superseded", stale=True)
                    if not self._capture_is_current(captured.context, capture_generation):
                        self.invalidate("capture-context-changed")
                        return self._receipt(action, payload, success=False, reason="capture-context-changed", stale=True)
                    if read.get("mapOpen") is False:
                        self.invalidate("map-closed")
                        return self._receipt(action, payload, success=False, reason="candidate-map-closed")
                    if floor is None:
                        return self._receipt(action, payload, success=False, reason="candidate-floor-unavailable")
                    row = self._render
                    same_floor = str(floor) == self._floor
                    current_material = bool(same_floor and row is not None and self._display_current(row)
                        and render_owner == self._refresh_render_owner()
                        and self._render_capture_context == captured.context
                        and row.get("captureGeneration") == capture_generation)
                    previous = self._last_observation or {}
                    same_pixels = bool(current_material and row.get("frameId") == previous.get("frame_id")
                        and captured.frame_snapshot.frame_key == previous.get("digest"))
                    if not same_floor:
                        self._revoke_render("candidate-floor-changed")
                verified = same_pixels
                if current_material and not verified:
                    import numpy as np
                    from alignment_observation import rendered_profile_residual_is_verified
                    host, _, _ = _prepare_alignment_host(engine, None, self._settings,
                        row["mapId"], str(floor), cancelled=obsolete)
                    basis = engine.qt_adapters.matrix_profile_to_canonical("shared-structure", row["mapId"], str(floor))
                    if basis is not None:
                        residual, _ = host._canonical_geometry_current_frame_residual(frame,
                            row["mapId"], str(floor), "shared-structure",
                            np.asarray(row["canonicalMatrix"]) @ np.asarray(basis), None)
                        verified = rendered_profile_residual_is_verified(residual) and _alignment_host_current(engine, host)
                with self._lock:
                    if obsolete() or render_owner != self._refresh_render_owner() and same_floor:
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                    if not self._capture_is_current(captured.context, capture_generation):
                        self.invalidate("capture-context-changed")
                        return self._receipt(action, payload, success=False, reason="capture-context-changed", stale=True)
                    if not _engine_resource_current(engine):
                        self.invalidate("resource-context-superseded")
                        return self._receipt(action, payload, success=False, reason="resource-context-superseded", stale=True)
                    if verified:
                        return self._receipt(action, payload, success=True, reason="candidate-floor-current", unchanged=True)
                # Reuse this full-client capture. A failed/cancelled scan owns
                # only its new evidence; the earlier successful display still
                # has its original opening-scoped core authorization.
                replacement_started = True
                return self._capture_and_solve("refresh_map", payload, cancelled, expected_epoch=epoch,
                    prepared=(captured, observation, floor, capture_generation, read))
            except Exception as error:
                with self._lock:
                    if engine is self._engine and not _engine_resource_current(engine):
                        self.invalidate("resource-context-superseded")
                        return self._receipt(action, payload, success=False, reason="resource-context-superseded", stale=True)
                    if obsolete():
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True,
                            errorType=type(error).__name__)
                    return self._receipt(action, payload, success=False,
                        reason=f"{type(error).__name__}:{error}", error=True,
                        **self._error_fields(error, action))
            finally:
                with self._lock:
                    if self._capture_pending_owner is owner:
                        self._capture_pending = False
                        self._capture_pending_owner = None

    def _capture_frame(self, cancelled, *, attempt_id=None, preserve_candidate=False):
        _test_evidence("capture-start")
        # Candidate freshness ends before entering a possibly blocked capture.
        with self._lock:
            candidate_preview = self._candidate_provisional_enabled()
            if not preserve_candidate and (candidate_preview or self._native_s1_candidate_preview
                    and self._season == "s1" and not self._manual_alignment):
                # A new candidate fit supersedes manual pixels, including after
                # a failed probe withdrew them. No persistent pose is updated.
                self._transient_drag = self._transient_cycle = None
            if preserve_candidate and self._map_open and (candidate_preview or self._native_s1_candidate_preview):
                pass  # A floor-only probe cannot renew pose or candidate evidence.
            elif self._candidate_provisional_enabled() and self._render is not None:
                # Notify the actual presentation consumer before pixels block.
                self._revoke_render("new-provisional-capture")
            else:
                self._revoke_current_candidates(preserve_reference=True)
        # Retire prior evidence BEFORE a new acquisition, including failures.
        self._failure_capture_stamp = None
        self._failure_control_observation = None
        try:
            sample_generation = self._capture_generation()
            sample_scope = self._failure_capture_scope()
            captured = self._capture.capture(cancelled)
            _test_evidence("capture", captured, sample_generation, sample_scope, attempt_id)
            if self._failure_samples.enabled or self._failure_samples.automatic_enabled:
                # Diagnostic validation cannot change the acquisition result or
                # exception. The original consumer still validates full frames.
                try:
                    self._full_frame(captured)
                    if not self._cancelled(cancelled) and sample_scope == self._failure_capture_scope():
                        from datetime import datetime, timezone
                        self._failure_capture_stamp = (captured, datetime.now(timezone.utc).isoformat(),
                            time.monotonic(), sample_generation, attempt_id, sample_scope)
                except Exception:
                    pass
            return captured
        except Exception as error:
            error.error_phase = "capture"
            raise

    def _error_fields(self, error, action):
        fields = dict(traceback="".join(traceback.format_exception(
            type(error), error, error.__traceback__, limit=-12, chain=False))[-8192:])
        fields.update(exceptionType=type(error).__name__, exceptionOrigin="runtime-dispatch",
                      exceptionAction=action)
        if getattr(error, "error_phase", None) == "capture" or action == "bind_capture":
            fields["errorPhase"] = "capture"
            raw_diagnostics = getattr(error, "capture_diagnostics", {})
            allowed = {"frameShape", "frameDtype", "roiClientBounds", "requestedClientBounds",
                       "fullClientSize", "clientBounds", "dpi", "expectedHwnd", "foregroundRole", "captureCapability", "fallbackTrigger"}
            diagnostics = {key:value for key,value in raw_diagnostics.items() if key in allowed} if isinstance(raw_diagnostics, dict) else {}
            from client_preparation_diagnostics import capture_capability, fallback_trigger
            for key, cleaner in (("captureCapability", capture_capability), ("fallbackTrigger", fallback_trigger)):
                if key in diagnostics:
                    diagnostics[key] = cleaner(diagnostics[key])
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
        pool = getattr(self, "_s1_private_pool", None)
        if (row.get("candidateDisplayOnly") and pool is not None
                and (not self._private_pool_scope_current(pool) or row.get("mapId") not in pool.remaining)):
            return False
        observation = self._core.active_observation
        # Material keeps its own observation/grant while a newer frame is
        # measured. It never supplies that newer frame's pose or identity proof.
        candidate = row.get("candidateDisplayOnly")
        return bool(self._active and self._map_open and row["epoch"] == self._epoch and observation
            and row["cycleId"] == self._cycle and row["floor"] == self._floor
            and row["roundId"] == self._round and (candidate or row["mapId"] == self._map_id)
            and observation.frame_generation == row["generation"]
            and self._core.display_authorization is not None
            and self._core.display_authorization.authorization_id == row["authorizationId"]
            and self._core.can_display(cycle_id=self._cycle, map_id=row["mapId"] if candidate else self._map_id,
                floor=self._floor, frame_generation=row["generation"])
            and row["mode"] == self._mode
            and row.get("season", "s0") == self._season)

    def map_input_observation_is_current(self, observation):
        """A visual receipt can synchronize intent, never revive an old epoch."""
        with self._lock:
            stored = self._map_input_observation
            return bool(isinstance(observation, dict) and stored is not None
                and observation == stored[0]
                and observation["epoch"] == self._epoch
                and observation["cycleId"] == self._cycle
                and observation["context"] == self._context()
                and observation["inputSequence"] == self._last_map_input_sequence
                and self._capture_is_current(stored[1], stored[2]))

    def _receipt(self, action, payload, *, success, reason, **extra):
        with self._lock:
            if self._diagnostics is not None:
                self._diagnostics.record("runtime-result", action=action, success=success, reason=reason,
                    season=self._season, mode=self._mode, mapId=self._map_id, floor=str(self._floor), frameId=self._frame_id,
                    epoch=self._epoch, roundId=self._round, cycleId=self._cycle, generation=self._frame_generation)
            context = self._context()
            request = {key: payload.get(key) for key in ("generation", "season", "mode", "profileId", "roundId", "frameId")}
            if not extra.get("stale"):
                from season_context import RETIRED_MODE_MESSAGE
                self._message = RETIRED_MODE_MESSAGE if reason == "retired-mode" else reason
            visual = payload.get("_mapInputVisual")
            if (visual is not None and visual["epoch"] == self._epoch
                    and visual["sequence"] == self._last_map_input_sequence
                    and self._capture_is_current(visual["captured"].context, visual["captureGeneration"])
                    and not extra.get("stale")):
                evidence = dict(mapOpen=visual["open"], inputSequence=visual["sequence"],
                    epoch=self._epoch, cycleId=self._cycle, context=context,
                    captureGeneration=visual["captureGeneration"])
                self._map_input_observation = (evidence, visual["captured"].context, visual["captureGeneration"])
                extra["mapInputObservation"] = dict(evidence)
            attempt = self._map_attempt_metadata(payload)
            if attempt:
                extra["mapAttempt"] = attempt
            _test_evidence("receipt", action, payload, success, reason, extra)
            return dict(action=action, success=success, reason=reason, message=reason,
                        requestContext=request, resultContext=context,
                        liveAuthorization=bool(self._render and self._render.get("liveAuthorization")), **extra)

    def execute(self, action, context=None, cancel_event=None):
        return self.dispatch(action, context or {}, cancel_event)

    def dispatch(self, action, payload=None, cancelled=None):
        return self._dispatch_with_ownership(action, payload, cancelled)

    def _dispatch_with_ownership(self, action, payload=None, cancelled=None):
        # Capture the operation kind before serial allocation/state waiting.
        # A context change cannot turn an old candidate command into new work.
        candidate_control_arrival = str(action) in CANDIDATE_CONTROL_ACTIONS
        # Allocation is one atomic next(), before any state-lock wait. The
        # birth tuple itself is read entirely under the state lock below.
        arrival_serial = next(self._dispatch_serials)
        request_id = uuid4().hex
        action, payload = str(action), dict(payload or {})
        owners = getattr(self._dispatch_owners, "arrivals", None)
        if owners is None:
            owners = self._dispatch_owners.arrivals = []
        owners.append(arrival_serial)
        try:
            def operation():
                return self._dispatch_after_arrival(action, payload, cancelled,
                    arrival_serial=arrival_serial, candidate_control_arrival=candidate_control_arrival,
                    request_id=request_id)
            # Even the recorder's accepting check can wait for a lock. Register
            # arrival first so diagnostic setup cannot move an old command past
            # a close/end/settings/resource cutoff while waiting for state.
            recorder = self.g_cycle_evidence
            if action not in ("map", "close_map", "recover_map_open") or recorder is None or not recorder.accepting:
                return operation()
            from client_g_cycle_evidence import dispatch_with_evidence
            return dispatch_with_evidence(self, recorder, action, payload, operation)
        finally:
            # Covers capability/activation refusal, cancelled early returns and
            # exceptions, not just the capture call. Never clear a newer owner.
            try:
                with self._lock:
                    owner = self._capture_pending_owner
                    if owner is not None and owner[1] == request_id:
                        self._capture_pending = False
                        self._capture_pending_owner = None
            finally:
                owners.pop()

    def _dispatch_after_arrival(self, action, payload, cancelled, *, arrival_serial,
                                candidate_control_arrival, request_id):
        with self._lock:
            # Birth fences govern work that may activate or republish, not
            # explicit termination/revocation or state-setting commands.
            # In particular an external settings/resource invalidation must
            # not eat a newer end/cancel already waiting for this lock.
            activation_request = (action in ("capture_now", "refresh_map", "recover_map_open",
                    "probe_map_after_drag", "fit_manual_frame", "observe_manual_floor",
                    "prepare_manual_alignment", "start_round", "observe_candidate_floor")
                or candidate_control_arrival
                or action == "map" and payload.get("openIntent") is not False
                or action == "toggle_round" and not (self._active or self._round_preparing))
            if activation_request and arrival_serial <= self._dispatch_cutoff:
                return self._receipt(action, payload, success=False,
                    reason="cancelled-or-stale", stale=True)
            if action == "map" and not (self._active or self._round_preparing):
                from season_context import is_retired_mode
                if is_retired_mode(self._mode, self._season):
                    return self._receipt(action, payload, success=False, reason="retired-mode")
                # Observing the game's map key is not an instruction to begin
                # a round. Admit its sequence without loading resources,
                # enabling capture or changing the round/display lifetime.
                if self._cancelled(cancelled):
                    return self._receipt(action, payload, success=False,
                        reason="cancelled-or-stale", stale=True)
                sequence = payload.get("inputSequence")
                if sequence is not None and (type(sequence) is not int or sequence <= self._last_map_input_sequence):
                    return self._receipt(action, payload, success=False,
                        reason="map-input-sequence-expired", stale=True)
                if "openIntent" in payload and type(payload["openIntent"]) is not bool:
                    return self._receipt(action, payload, success=False, reason="map-input-intent-invalid")
                if sequence is not None:
                    self._last_map_input_sequence = sequence
                return self._receipt(action, payload, success=True, reason="round-not-started")
            birth_ticket = (self._epoch, self._request_lifetime_generation, self._settings_generation)
        return self._dispatch_owned(action, payload, cancelled,
            birth_ticket=birth_ticket, request_id=request_id)

    def _dispatch_owned(self, action, payload, cancelled, *, birth_ticket, request_id):
        capture_request_epoch, request_lifetime_generation, request_settings_generation = birth_ticket
        capture_request_id = request_id
        payload = dict(payload or {})
        action = str(action)
        with self._lock:
            if self._s1_round_locked_map() and (action in (
                    "reselect_map", "map_reselection", "select_candidate", "previous_candidate",
                    "next_candidate", "confirm_route", "select_group_member")
                    or action == "capture_now" and payload.get("forceCatalogue") is True):
                return self._receipt(action, payload, success=False, reason="identity-locked")
        from season_context import is_retired_mode
        if is_retired_mode(self._mode, self._season) and action not in (
                "set_game_mode", "set_season", "set_route_profile", "set_context",
                "end_round", "cancel", "stop_round", "reset_round", "bind_capture"):
            return self._receipt(action, payload, success=False, reason="retired-mode")
        if action == "observe_candidate_floor":
            return self._probe_candidate_floor(action, payload, cancelled,
                expected_epoch=capture_request_epoch, request_id=request_id)
        if action in ("start_round", "toggle_round", "capture_now", "map", "refresh_map", "recover_map_open", "probe_map_after_drag"):
            report = self._offline.snapshot()["savedFrameCapabilities"][self._mode]
            if not report.get("liveReady", self._season == "s0") and not self._structure_candidate_available(report):
                return self._receipt(action, payload, success=False,
                    reason=";".join(report.get("liveGaps", ())) or f"{self._season}-live-contract-unavailable",
                    unavailable=True)
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
            with self._lock:
                candidate_operation = self._candidate_provisional_enabled() or self._season == "s1"
                candidate_owner = (birth_ticket, self._frame_id, self._frame_generation, id(self._engine),
                    self._s1_structure_session, self._s1_structure_ticket, self._group_intent_generation)
            try:
                return self._manual_action(action, payload, cancelled,
                    birth_ticket=birth_ticket, request_id=request_id)
            except Exception as error:
                with self._lock:
                    current_owner = ((self._epoch, self._request_lifetime_generation, self._settings_generation),
                        self._frame_id, self._frame_generation, id(self._engine), self._s1_structure_session,
                        self._s1_structure_ticket, self._group_intent_generation)
                    stale_candidate = candidate_operation and candidate_owner != current_owner
                    if not stale_candidate:
                        self.invalidate("manual-action-error")
                return self._receipt(action, payload, success=False,
                    reason=f"{type(error).__name__}:{error}", error=True,
                    unavailable=bool(getattr(error, "unavailable", False)),
                    **(dict(stale=True) if stale_candidate else {}),
                    **self._error_fields(error, action))
        if action == "toggle_sheet_visibility":
            with self._lock:
                self._aligned_visible = not self._aligned_visible
            self._notify("aligned-visibility-changed", reason="visibility-toggled")
            return self._receipt(action, payload, success=True, reason="visibility-toggled")
        if action == "capture_now" and payload.get("forceCatalogue") is True:
            with self._lock:
                if self._s1_round_locked_map():
                    return self._receipt(action, payload, success=False, reason="identity-locked")
                if self._candidate_provisional_enabled():
                    if (capture_request_epoch != self._epoch or self._cancelled(cancelled)
                            or request_settings_generation != self._settings_generation):
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                    self._s1_candidate_preference = None
                    self._clear_private_pool()
                    capture_request_epoch = self._epoch + 1
                    self.invalidate("candidate-reselection")
                    if capture_request_epoch != self._epoch:
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
        if action == "capture_now" and "inputObservationSequence" in payload:
            # A suspended binding can have accepted an edge while resources
            # blocked its worker dispatch. Synchronize the sequence only; the
            # captured pixels still decide open/closed and grant no key intent.
            with self._lock:
                sequence = payload["inputObservationSequence"]
                if type(sequence) is not int or sequence < self._last_map_input_sequence:
                    return self._receipt(action, payload, success=False,
                        reason="map-input-sequence-expired", stale=True)
                self._last_map_input_sequence = sequence
        if action == "map":
            from session_state_machine import map_hotkey_transition
            # Input sequence admission, physical intent and its lifetime ticket
            # are one atomic transaction. No older admitted open can overtake
            # a newer close/open between two state-lock sections.
            with self._lock:
                sequence = payload.get("inputSequence")
                # A newer sequenced S1 input may have been born while an older
                # open held this lock. It may supersede that input, but never a
                # unordered close/end/cancel/settings/resource boundary. A
                # sequenced close is superseded only by a larger input sequence.
                # S0 keeps
                # its original strict birth-epoch admission semantics.
                newer_s1_input = (self._candidate_preview_requested() and self._season == "s1"
                    and type(sequence) is int and sequence > self._last_map_input_sequence)
                if (self._cancelled(cancelled) or request_lifetime_generation != self._request_lifetime_generation
                        or request_settings_generation != self._settings_generation
                        or capture_request_epoch != self._epoch and not newer_s1_input):
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                if sequence is not None:
                    if type(sequence) is not int or sequence <= self._last_map_input_sequence:
                        return self._receipt(action, payload, success=False, reason="map-input-sequence-expired", stale=True)
                if "openIntent" in payload:
                    if type(payload["openIntent"]) is not bool:
                        return self._receipt(action, payload, success=False, reason="map-input-intent-invalid")
                    transition = "open" if payload["openIntent"] else "close"
                else:
                    transition = map_hotkey_transition(self._map_open, self._capture_pending)
                if sequence is not None:
                    self._last_map_input_sequence = sequence
                if transition == "close":
                    self.invalidate("map-closed", source=payload.get("inputSource", "dispatch-map"), input_sequence=sequence)
                    return self._receipt(action, payload, success=True, reason="map-closed")
                if transition == "ignore":
                    return self._receipt(action, payload, success=True, reason="map-open-verification-in-progress")
                if transition == "open" and self._s1_provisional_enabled():
                    # Never adopt whatever epoch an intervening/reentrant
                    # request owns. Only our single increment is admissible.
                    capture_request_epoch = self._epoch + 1
                    self.invalidate("s1-provisional-open-intent")
                    if self._epoch != capture_request_epoch:
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                    self._map_open = False
                    self._capture_pending = True
                    self._capture_pending_owner = (capture_request_epoch, capture_request_id)
        if action in ("end_round", "stop_round", "reset_round", "cancel", "close_map"):
            if action == "close_map" and type(payload.get("inputSequence")) is int:
                with self._lock:
                    self._last_map_input_sequence = max(self._last_map_input_sequence, payload["inputSequence"])
            self.invalidate("map-closed" if action == "close_map" else action,
                            source=payload.get("inputSource", "dispatch-" + action))
            with self._lock:
                if action != "close_map":
                    self._manual_alignment = False
                    self._active = self._map_open = self._round_preparing = False
                    self._prepared_round_ready = False
                    self._core.reset_round(reason=action)
                    self._group = self._engine = None
                    self._clear_candidates()
                    self._map_id, self._floor = "", ""
                    if self._capture is not None:
                        self._capture.disable()
            return self._receipt(action, payload, success=True, reason=action)
        if action in ("set_game_mode", "set_season", "set_route_profile", "set_context"):
            if self._cancelled(cancelled):
                return self._receipt(action, payload, success=False, reason="cancelled", cancelled=True)
            mode = payload.get("mode", self._mode)
            try:
                season = normalize_season(payload.get("season", self._season))
            except ValueError:
                return self._receipt(action, payload, success=False, reason="unsupported-season")
            # A convenience season change has no authority to carry another
            # season's author preference; explicit target preferences still win.
            profile = str(payload.get("profileId", self._profile if season == self._season else ""))
            from season_context import known_modes_for_season
            if mode not in known_modes_for_season(season):
                return self._receipt(action, payload, success=False, reason="unsupported-mode")
            with self._lock:
                # Cancellation may arrive while this mutator waits for state ownership.
                if self._cancelled(cancelled):
                    return self._receipt(action, payload, success=False, reason="cancelled", cancelled=True)
                changed_mode = mode != self._mode or season != self._season
                changed_profile = profile != self._profile
                if changed_mode and (self._active or self._round_preparing):
                    return self._receipt(action, payload, success=False,
                                         reason="end-round-before-changing-mode")
                if changed_mode:
                    self._manual_alignment = False
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
                self._season = season
                self._mode, self._profile = mode, profile
                self._publish_game_pointer_context_locked()
                self._offline.dispatch("set_context", dict(season=season, mode=mode, profileId=profile))
            if (changed_profile or payload.get("refreshDisplay") is True) and not changed_mode:
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
                if action == "prepare_manual_alignment":
                    if self._season != "s1":
                        return self._receipt(action, payload, success=False, reason="manual-session-not-required")
                    prepared = self._start(payload, cancelled, manual=True, expected_epoch=capture_request_epoch)
                    if not prepared["success"]:
                        return prepared
                    activated = self._ensure_round_active(payload, cancelled, expected_epoch=capture_request_epoch)
                    if not activated["success"]:
                        with self._lock:
                            if capture_request_epoch == self._epoch:
                                self.dispatch("end_round")
                    return self._receipt(action, payload, **activated)
                if action in ("fit_manual_frame", "observe_manual_floor"):
                    return self._capture_manual_alignment(action, payload, cancelled, expected_epoch=capture_request_epoch)
                if action == "recover_map_open":
                    return self._recover_map_open(payload, cancelled)
                if action == "probe_map_after_drag":
                    return self._probe_map_after_drag(payload, cancelled)
                if action == "remember_render_material":
                    return self._remember_render_material(str(payload.get("renderToken", "")), payload, cancelled)
                if action == 'validate_sibling_registry':
                    return self._validate_sibling_registry(str(payload.get('renderToken', '')), payload, cancelled)
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
                    with self._lock:
                        if capture_request_epoch != self._epoch or self._cancelled(cancelled):
                            return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                    self.cancel_map_open_recovery("new-capture-request")
                    # Carry the admission ticket across every blocking hand-off.
                    # Helpers must not adopt a later close/cancel/new-open epoch.
                    activated = self._ensure_round_active(payload, cancelled,
                        expected_epoch=capture_request_epoch, allow_prepare=action != "map")
                    if not activated["success"]:
                        return self._receipt(action, payload, success=False, reason=activated["reason"],
                            stale=bool(activated.get("stale")))
                    with self._lock:
                        if capture_request_epoch != self._epoch or self._cancelled(cancelled):
                            return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                        self._capture_pending = True
                        self._capture_pending_owner = (capture_request_epoch, capture_request_id)
                    return self._capture_and_solve(action, payload, cancelled, expected_epoch=capture_request_epoch)
            return self._receipt(action, payload, success=False, reason="unsupported-action", unavailable=True)
        except Exception as error:
            with self._lock:
                # The work lock does not own a newer input's lifetime. An old
                # acquisition error may report failure, never revoke its successor.
                stale_owner = action in ("map", "capture_now", "prepare_manual_alignment", "fit_manual_frame", "observe_manual_floor") and capture_request_epoch != self._epoch
                if not stale_owner and not self._retained_material_current(self._refresh_render_owner()):
                    self.invalidate("runtime-error", source=f'execute:{action}:{type(error).__name__}')
            return self._receipt(action, payload, success=False, reason=f"{type(error).__name__}:{error}", error=True,
                    stale=stale_owner,
                    unavailable=bool(getattr(error, "unavailable", False)),
                    **self._error_fields(error, action))

    def _get_engine(self, mode, season="s0", *, require_live=True):
        season = normalize_season(season)
        getter = getattr(self._factories, "get_engine", None)
        if season != "s0" and not callable(getter):
            from client_providers import ProviderUnavailable
            raise ProviderUnavailable(f"season-resources-unavailable:{season}")
        engine = getter(mode, season) if callable(getter) else self._factories[mode]()
        candidate_ready = bool(require_live and season == self._season and mode == self._mode
            and self._structure_candidate_available(engine=engine))
        if require_live and not getattr(engine, 'qt_rank_ready', True) and not candidate_ready:
            from client_providers import ProviderUnavailable
            raise ProviderUnavailable(';'.join(getattr(engine, 'qt_rank_gaps', ())) or 'recognition-rank-unavailable')
        if (require_live and not getattr(engine, "qt_live_ready", season == "s0")
                and not candidate_ready):
            from client_providers import ProviderUnavailable
            raise ProviderUnavailable(";".join(getattr(engine, "qt_live_gaps", ())) or f"season-live-contract-unavailable:{season}")
        return engine

    def _start(self, payload, cancelled, *, manual=False, expected_epoch=None):
        if self._capture is None:
            raise RuntimeError("capture-provider-unavailable")
        if self._evidence_kind == "live-client-capture" and not callable(getattr(self._capture, "is_current", None)):
            raise RuntimeError("capture-context-recheck-unavailable")
        with self._lock:
            if expected_epoch is not None and expected_epoch != self._epoch:
                return self._receipt("start_round", payload, success=False, reason="cancelled-or-stale", stale=True)
            epoch, mode, season = self._epoch, self._mode, self._season
            if self._active or (self._round_preparing and self._prepared_round_ready):
                return self._receipt("start_round", payload, success=True,
                    reason="round-already-active" if self._active else "round-already-prepared")
            preselected = self._gate.selected_id if self._candidate_edit and self._gate is not None else None
            self._round_preparing = True
            self._prepared_round_ready = False
        try:
            engine = self._get_engine(mode, season, require_live=not manual)
            if manual:
                from floor_contract import floor_contract
                contract = floor_contract(mode, season=season)
                if season != "s1" or contract is None or tuple(engine.identity_floors) != contract.floor_ids:
                    raise ValueError("manual-floor-resource-contract-mismatch")
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
            if epoch != self._epoch or mode != self._mode or season != self._season:
                return self._receipt("start_round", payload, success=False, reason="stale-context", stale=True)
            self._core.reset_round(reason="round-prepared")
            if season == "s1":
                from floor_contract import floor_contract
                contract = floor_contract(mode, season=season)
                if contract is None or tuple(engine.identity_floors) != contract.floor_ids:
                    raise ValueError("round-floor-resource-contract-mismatch")
                self._core = AlignmentCoreV2(clock=self._monotonic, floors=contract.floor_ids)
            elif self._core.floors != ("B1", "1", "2"):
                self._core = AlignmentCoreV2(clock=self._monotonic)
            self._round += 1
            self._cycle = self._frame_generation = 0
            self._probe_misses = 0
            self._active, self._map_open = False, False
            self._round_preparing = self._prepared_round_ready = True
            self._map_id = self._floor = self._frame_id = ""
            self._group = self._render = None
            self._engine = engine
            self._manual_alignment = manual
            self._attach_identity_engine(engine)
            from nightmare_candidate import RouteDisplayGate
            self._clear_candidates()
            self._gate = RouteDisplayGate(engine.pack)
            self._update_candidates([], "")
            if manual and not self._s1_floor_observer_available(engine):
                self._manual_floor = self._floor = str(engine.pack.default_entry_floor)
                self._manual_override = True
                self._floor_required = False
            elif manual:
                self._manual_floor = None
                self._manual_override = False
                self._observed_floor = self._floor = ""
                self._floor_required = True
            if preselected and preselected in engine.pack.by_id:
                self._gate.select(preselected)
                self._candidate_edit = self._selection_pending = True
        return self._receipt("start_round", payload, success=True, reason="round-prepared")

    def _ensure_round_active(self, payload, cancelled, *, expected_epoch=None, allow_prepare=True):
        with self._lock:
            if expected_epoch is not None and expected_epoch != self._epoch:
                return dict(success=False, reason="cancelled-or-stale", stale=True)
            if self._active:
                return dict(success=True, reason="round-already-active")
            if not allow_prepare and not (self._round_preparing and self._prepared_round_ready):
                return dict(success=False, reason="round-not-started")
        if allow_prepare:
            prepared = self._start(payload, cancelled, expected_epoch=expected_epoch)
            if not prepared["success"]:
                return prepared
        with self._lock:
            if expected_epoch is not None and expected_epoch != self._epoch:
                return dict(success=False, reason="cancelled-or-stale", stale=True)
            epoch, engine = self._epoch, self._engine
            capture = self._capture
            previous = getattr(self, "_capture_activation_owner", None)
            activation_owner = self._capture_activation_owner = dict(
                provider=capture, status="pending", cleanup=False)
            if previous is not None and previous["provider"] is capture:
                activation_owner["cleanup"] = previous["cleanup"]
        attempted = False
        try:
            if self._cancelled(cancelled):
                return dict(success=False, reason="cancelled-or-stale", stale=True)
            attempted = True
            if not capture.enable():
                return dict(success=False, reason="capture-enable-rejected")
            with self._lock:
                if (self._cancelled(cancelled) or epoch != self._epoch or engine is not self._engine
                        or capture is not self._capture or self._capture_activation_owner is not activation_owner
                        or not self._round_preparing or not self._prepared_round_ready):
                    return dict(success=False, reason="cancelled-or-stale", stale=True)
                self._active, self._round_preparing = True, False
                activation_owner["status"] = "committed"
        finally:
            with self._lock:
                if activation_owner["status"] != "committed":
                    activation_owner["status"] = "failed"
                    if attempted or activation_owner["cleanup"]:
                        current = self._capture_activation_owner
                        # An in-flight successor inherits cleanup responsibility.
                        # A failed successor cannot shield a late enable forever.
                        if (current is activation_owner or current["provider"] is not capture
                                or current["status"] == "failed"
                                or (current["status"] == "committed"
                                    and not (self._active and self._capture is capture))):
                            capture.disable()
                        elif current["status"] == "pending":
                            current["cleanup"] = True
        self._notify("round-started", reason="round-active")
        return dict(success=True, reason="round-active")

    def _map_recovery_scope(self):
        return (self._epoch, self._round, self._cycle, self._mode, self._profile,
            getattr(self, "_display_generation", 0), self._capture_generation(), self._last_map_input_sequence)

    @staticmethod
    def _map_observed(engine, observation, floor_read):
        from session_state_machine import verified_map_hotkey_frame
        return (observation.get("mapOpen") is True if engine.pack.mode == "difficult" else
            observation.get("detected") is True or verified_map_hotkey_frame(observation, floor_read))

    def map_open_recovery_status(self):
        """Metadata only; timer and worker share one finite, scoped observation budget."""
        with self._lock:
            row = self._map_recovery
            if row is None:
                return dict(pending=False)
            now = self._monotonic()
            current = self._active and row["scope"] == self._map_recovery_scope()
            pending = current and row["remaining"] > 0 and now < row["expiresAt"]
            return dict(id=row["id"], pending=pending, due=pending and now >= row["nextAt"],
                remaining=row["remaining"], reason="waiting" if pending else
                "scope-changed" if not current else "budget-exhausted")

    def cancel_map_open_recovery(self, reason="cancelled"):
        with self._lock:
            row, self._map_recovery = self._map_recovery, None
        if row is not None:
            self._notify("map-recovery-ended", reason=reason, recoveryId=row["id"],
                mapAttempt=dict(recoveryId=row["id"], openingId=row.get("openingId"), inputSequence=row.get("inputSequence")))

    def _arm_map_open_recovery(self, cancelled, epoch, payload=None):
        with self._lock:
            if self._cancelled(cancelled) or epoch != self._epoch:
                return False
            # No confirmed close has been observed. Withdraw pixels but retain
            # the physical open intent and a bounded opportunity to observe it.
            self.invalidate("map-preparation-unconfirmed")
            self._map_open = False
            now = self._monotonic()
            self._map_recovery = dict(id=uuid4().hex, scope=self._map_recovery_scope(),
                remaining=MAP_RECOVERY_PROBES, nextAt=now + MAP_RECOVERY_INTERVAL,
                expiresAt=now + MAP_RECOVERY_SECONDS,
                openingId=(payload or {}).get("_diagnosticOpeningId"),
                inputSequence=(payload or {}).get("inputSequence"))
            row = self._map_recovery
        self._notify("map-recovery-pending", reason="map-preparation-unconfirmed",
            recoveryId=row["id"],
            mapAttempt=dict(recoveryId=row["id"], openingId=row["openingId"], inputSequence=row["inputSequence"]))
        return True

    def _recover_map_open(self, payload, cancelled):
        action = "recover_map_open"
        payload.pop("_recoveryRenderOwner", None)
        payload.pop("_recoveryResultScope", None)
        with self._lock:
            row = self._map_recovery
            state = self.map_open_recovery_status()
            if (self._cancelled(cancelled) or row is None or payload.get("recoveryId") != row["id"]):
                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
            payload["_diagnosticOpeningId"] = row.get("openingId")
            payload["inputSequence"] = row.get("inputSequence")
            if not state["pending"]:
                self.cancel_map_open_recovery(state["reason"])
                return self._receipt(action, payload, success=False, reason="map-open-recovery-exhausted")
            if not state["due"]:
                return self._receipt(action, payload, success=False, reason="map-open-recovery-waiting", recoveryPending=True)
            row["remaining"] -= 1
            row["nextAt"] = self._monotonic() + MAP_RECOVERY_INTERVAL
            epoch, engine, expected = self._epoch, self._engine, row["scope"]
            self._capture_pending = True
        obsolete = lambda: self._cancelled(cancelled) or self._map_recovery is not row or expected != self._map_recovery_scope()
        self._new_diagnostic_attempt(payload)
        payload["_diagnosticOpeningId"] = row.get("openingId")
        payload["inputSequence"] = row.get("inputSequence")
        host = _PreparationHost(self, engine, obsolete, payload)
        host.diagnostics.emit("begin", "map-open-recovery", recoveryId=row["id"])
        try:
            # Exactly one fresh probe; no second preparation loop or new input
            # sequence. Native/frame/floor checks remain in the original path.
            try:
                prepared, stopped = host._capture_map_open_full_probe(None, self._cycle + 1, epoch, None,
                    time.perf_counter() + max(0., row["expiresAt"] - self._monotonic()), _Cancellation(obsolete))
                prepared = prepared or host.last_unresolved
            except Exception as error:
                if obsolete():
                    host.diagnostics.finish("cancelled-or-stale")
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                if not isinstance(error, OSError):
                    raise
                prepared, stopped = None, False
            terminal_receipt = None
            with self._lock:
                if obsolete():
                    host.diagnostics.finish("cancelled-or-stale")
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                expired = self._monotonic() >= row["expiresAt"]
                if prepared is not None and not expired:
                    host.diagnostics.finish("recovery-current-frame")
                else:
                    pending = not expired and row["remaining"] > 0
                    reason = "map-open-recovery-pending" if pending else "map-open-recovery-exhausted"
                    if not pending:
                        self.cancel_map_open_recovery("budget-exhausted")
                    host.diagnostics.finish(reason)
                    terminal_receipt = self._receipt(action, payload, success=False, reason=reason, recoveryPending=pending)
            if terminal_receipt is not None:
                if not pending:
                    self._terminal_failure_sample("map-open-recovery-exhausted", payload,
                        host.last_capture[0] if host.last_capture is not None else None,
                        lambda: not self._cancelled(cancelled) and expected == self._map_recovery_scope())
                return terminal_receipt
            result = self._capture_and_solve(action, payload, cancelled, prepared=prepared, recovery_scope=expected)
            with self._lock:
                published_owner = payload.get("_recoveryRenderOwner")
                if (self._cancelled(cancelled) or payload.get("_recoveryResultScope", expected) != self._map_recovery_scope()
                        or published_owner is not None and published_owner != self._refresh_render_owner()):
                    self._retire_refresh_owner(payload.get("_recoveryRenderOwner"))
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                if result.get("stale") and self._retire_refresh_owner(payload.get("_recoveryRenderOwner")):
                    result = dict(result, liveAuthorization=False)
                # Main's floor policy can defer a current positive sample.
                # Retain this ticket's existing budget until it is accepted.
                if result.get("reason") != "floor-policy-deferred":
                    self.cancel_map_open_recovery("current-frame-recovered")
                return result
        except Exception as error:
            # A recovery may already have published before its worker finishes.
            # A later drag owns that row's new revision, even with the same token.
            with self._lock:
                published_owner = payload.get("_recoveryRenderOwner")
                if (self._cancelled(cancelled) or payload.get("_recoveryResultScope", expected) != self._map_recovery_scope()
                        or published_owner is not None and published_owner != self._refresh_render_owner()):
                    self._retire_refresh_owner(payload.get("_recoveryRenderOwner"))
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                self.invalidate("runtime-error", source=f'map-recovery:{action}:{type(error).__name__}')
            return self._receipt(action, payload, success=False, reason=f"{type(error).__name__}:{error}",
                error=True, **self._error_fields(error, action))
        finally:
            with self._lock:
                self._capture_pending = False

    def _refresh_render_owner(self):
        # A physical drag can keep token/frame/generation but advance revision.
        # Material acknowledgements alone do not transfer observation ownership.
        row = self._render
        return tuple(row.get(key) for key in ("epoch", "cycleId", "frameId", "generation",
            "token", "authorizationId", "transformRevision")) if row is not None else None

    def _retained_material_current(self, owner):
        """Retain a successful display, without renewing observation evidence."""
        row = self._render
        return bool(row is not None and (row.get("candidateDisplayOnly")
                or row.get("poseFinalVerified") is True and row.get("reviewStatus") == "complete"
                and not row.get("requiresMaterialAck"))
            and owner == self._refresh_render_owner() and self._display_current(row)
            and row["profileId"] == self._profile
            and row.get("displayGeneration") == getattr(self, "_display_generation", 0)
            and _engine_resource_current(self._engine)
            and self._capture_is_current(self._render_capture_context, row.get("captureGeneration")))

    def _retire_material_owner(self, owner, reason):
        with self._lock:
            if owner != self._refresh_render_owner():
                return False
            if self._retained_material_current(owner):
                return False
            self._revoke_render(reason)
            return True

    def _retire_refresh_owner(self, owner):
        if owner is None:
            return False
        return self._retire_material_owner(owner, "refresh-cancelled-or-stale")

    def _refresh_map(self, payload, cancelled):
        # Passive timer requests never build an unbounded worker backlog.
        if not self._work.acquire(blocking=False):
            return self._receipt("refresh_map", payload, success=True, reason="refresh-coalesced", coalesced=True)
        owns_capture = False
        epoch = self._epoch
        owner = None
        payload.pop("_refreshRenderOwner", None)
        try:
            with self._lock:
                if (self._capture_pending or self._group_choice_inflight is not None or self._transient_drag is not None
                        or self._pending_sibling is not None and self._pending_sibling.get("projection")):
                    return self._receipt("refresh_map", payload, success=True, reason="refresh-coalesced", coalesced=True)
                if not self._active or not self._map_open:
                    return self._receipt("refresh_map", payload, success=False, reason="map-refresh-not-active")
                candidate_probe = self._candidate_probe_available() and not self._transient_cycle_current()
                transient = self._transient_cycle_current()
                owner = self._refresh_render_owner()
                if not candidate_probe:
                    self._capture_pending = owns_capture = True
                    capture_owner = self._capture_pending_owner = (epoch, object())
            if candidate_probe:
                return self._probe_candidate_floor("refresh_map", payload, cancelled,
                    expected_epoch=epoch, request_id=object())
            if transient:
                receipt = self._probe_map_after_drag(payload, cancelled)
                if receipt.get("success"):
                    receipt = self._retry_desired_group_member("refresh_map", payload, cancelled, receipt)
            else:
                receipt = self._capture_and_solve("refresh_map", payload, cancelled)
            published_owner = payload.get("_refreshRenderOwner", owner)
            with self._lock:
                if (self._cancelled(cancelled)
                        or receipt.get("success") is True and published_owner is not None
                        and published_owner != self._refresh_render_owner()):
                    self._retire_refresh_owner(published_owner)
                    return self._receipt("refresh_map", payload, success=False,
                        reason="cancelled-or-stale", stale=True)
                if receipt.get("stale") and self._retire_refresh_owner(published_owner):
                    receipt = dict(receipt, liveAuthorization=False)
            return receipt
        except Exception as error:
            # Cancellation belongs to this worker. In-flight capture may raise
            # after the UI has already published a newer transient transform
            # (often with the same render token). It cannot invalidate that
            # current owner. Live failures from the current worker still revoke.
            with self._lock:
                published_owner = payload.get("_refreshRenderOwner", owner)
                current_owner = self._refresh_render_owner()
                # This worker may have revoked its old grant before a fallback
                # failed. No current grant is not a transfer to a newer owner.
                if (self._cancelled(cancelled) or epoch != self._epoch
                        or current_owner is not None and published_owner != current_owner):
                    self._retire_refresh_owner(published_owner)
                    return self._receipt("refresh_map", payload, success=False,
                        reason="cancelled-or-stale", stale=True)
                if not self._retained_material_current(current_owner):
                    self.invalidate("runtime-error", source=f'refresh-map:{type(error).__name__}')
            return self._receipt("refresh_map", payload, success=False,
                                 reason=f"{type(error).__name__}:{error}", error=True,
                    **self._error_fields(error, "refresh_map"))
        finally:
            if owns_capture:
                with self._lock:
                    if self._capture_pending_owner is capture_owner:
                        self._capture_pending = False
                        self._capture_pending_owner = None
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
            if obsolete():
                return self._receipt("refresh_map", payload, success=False, reason="cancelled-or-stale", stale=True)
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
            with self._lock:
                if obsolete():
                    return self._receipt("refresh_map", payload, success=False, reason="cancelled-or-stale", stale=True)
                self._retire_material_owner(self._refresh_render_owner(), reason)
            return None
        self._set_pose_phase("pending", "current-frame-refresh", map_id, floor, current_frame, self._frame_generation)
        host, _, _ = _prepare_alignment_host(engine, group, self._settings, map_id, floor,
            cancelled=obsolete)
        basis = engine.qt_adapters.matrix_profile_to_canonical("shared-structure", map_id, floor)
        if basis is None:
            with self._lock:
                if obsolete():
                    return self._receipt("refresh_map", payload, success=False, reason="cancelled-or-stale", stale=True)
                self._retire_material_owner(self._refresh_render_owner(), "refresh-reference-basis-unavailable")
            return None
        canonical = np.asarray(row["canonicalMatrix"], np.float64)
        source_matrix = canonical @ np.asarray(basis, np.float64)
        residual_options = {}
        if self._manual_alignment and self._s1_floor_observer_available(engine):
            from single_map_structure import current_geometry
            context = current_geometry(frame, observation_bounds=(0, 0, frame.shape[1], frame.shape[0]))
            if context is None or context.get("visible") is None:
                with self._lock:
                    if not obsolete():
                        self._retire_material_owner(self._refresh_render_owner(), "manual-current-geometry-unavailable")
                return None
            residual_options["visible_override"] = context["visible"]
        residual, _ = host._canonical_geometry_current_frame_residual(
            frame, map_id, floor, "shared-structure", source_matrix, None, **residual_options)
        with self._lock:
            # The cancellation check and any rejection below are one owner
            # transaction. A first drag delta must not land between them.
            if obsolete() or not _alignment_host_current(engine, host):
                return self._receipt("refresh_map", payload, success=False, reason="cancelled-or-stale", stale=True)
            if self._transient_drag is not None or self._transient_cycle_current():
                return self._receipt("refresh_map", payload, success=True, reason="refresh-coalesced", coalesced=True)
            if (obsolete() or self._render is None or self._render["token"] != row["token"]
                    or current_frame != self._frame_id or epoch != self._epoch
                    or cycle != self._cycle or self._floor != floor):
                return self._receipt("refresh_map", payload, success=False, reason="cancelled-or-stale", stale=True)
            if not rendered_profile_residual_is_verified(residual):
                self._retire_material_owner(self._refresh_render_owner(), "current-frame-residual-failed")
                return None
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
            core = self._copy_pose_core(self._core)
            core.begin_observation(cycle_id=cycle, map_id=map_id, floor=floor,
                frame_generation=generation, frame_signature=(digest,), reason="current-pose-refresh")
            candidate = AlignmentCandidate(map_id, floor, canonical,
                "same-floor-reopen-verified-refresh", generation, confidence=1.0, anchor_count=1,
                anchor_kind="global", current_frame_verified=True, residual_verified=True,
                global_residual_verified=True, translation_only=True,
                metadata=dict(routeProfileId="shared-structure"))
            decision = core.adjudicate(candidate)
            token = core.authorize(decision)
            if token is None or not core.commit(token, persist_pose=False):
                self._retire_material_owner(self._refresh_render_owner(), "refresh-core-commit-rejected")
                return self._receipt("refresh_map", payload, success=False, reason=decision.reason)
            self._discard_sibling()
            self._core = core
            self._frame_generation, self._frame_id = generation, frame_id
            self._last_observation = dict(captured=captured, frame=frame, floor=floor, digest=digest, created_at=self._monotonic(),
                capture_generation=capture_generation, frame_id=frame_id, observed_floor=self._observed_floor,
                cycle_id=self._cycle, generation=self._frame_generation, floor_read=copy.deepcopy(floor_read or {}))
            self._publish_game_pointer_context_locked()
            self._probe_misses = 0
            self._render = dict(row, authorizationId=token.authorization_id, frameId=frame_id,
                generation=generation, observedFloor=self._observed_floor,
                floorSource=self._current_floor_source())
            payload["_refreshRenderOwner"] = self._refresh_render_owner()
            self._render_capture_context = captured.context
            if self._reference_sheet is not None:
                self._reference_sheet.update(self._identity_fields())
            self._remember_provisional_reference()
            render_token = self._render["token"]
            self._fit_reporting.success(self._fit_reporting_scope())
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

    def _capture_manual_alignment(self, action, payload, cancelled, *, expected_epoch=None):
        with self._lock:
            if expected_epoch is None:
                expected_epoch = self._epoch
            if expected_epoch != self._epoch or self._cancelled(cancelled):
                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
            observed_floor = self._s1_floor_observer_available()
        if observed_floor:
            return self._capture_s1_observed_floor(action, payload, cancelled, expected_epoch=expected_epoch)
        return self._capture_operator_alignment(action, payload, cancelled, expected_epoch=expected_epoch)

    def _capture_s1_observed_floor(self, action, payload, cancelled, *, expected_epoch=None):
        """Manual identity plus a fresh, independent floor observation.

        A passive tick can maintain an open cycle, or consume an existing
        opening edge's finite recovery budget. It can never open a new cycle.
        All four positions may supply current-frame private-test evidence.
        B3/B4 remain synthetic-only, independently of automatic admission.
        """
        if expected_epoch is None:
            with self._lock:
                expected_epoch = self._epoch
        with self._work:
            with self._lock:
                if expected_epoch != self._epoch or self._cancelled(cancelled):
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                if self._cancelled(cancelled):
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                if not (self._manual_alignment and self._active and self._s1_floor_observer_available()
                        and self._engine is not None and self._map_id):
                    return self._receipt(action, payload, success=False, reason="manual-map-required")
                recovery_owner = recovery_scope = None
                if action == "observe_manual_floor" and (not self._map_open or "recoveryId" in payload):
                    row = self._map_recovery
                    status = self.map_open_recovery_status()
                    if (row is None or payload.get("recoveryId") != row["id"]
                            or not status.get("pending") or not status.get("due")):
                        return self._receipt(action, payload, success=False, reason="manual-floor-observation-not-active")
                    row["remaining"] -= 1
                    row["nextAt"] = self._monotonic() + MAP_RECOVERY_INTERVAL
                    recovery_owner, recovery_scope = row, row["scope"]
                sequence = payload.get("inputSequence") if payload.get("s1OpenEdge") is True else payload.get("inputObservationSequence")
                if sequence is not None:
                    if (type(sequence) is not int or sequence < self._last_map_input_sequence
                            or payload.get("s1OpenEdge") is True and sequence == self._last_map_input_sequence):
                        return self._receipt(action, payload, success=False, reason="stale-map-input", stale=True)
                    self._last_map_input_sequence = sequence
                epoch, engine, map_id = self._epoch, self._engine, self._map_id
                render_owner = self._refresh_render_owner()
                manual_target, old_floor = self._manual_floor, self._floor
                owner = self._manual_capture_owner = object()
                self._capture_pending = True
                if action != "observe_manual_floor":
                    self._retire_material_owner(self._refresh_render_owner(), "manual-current-frame-request")

            def obsolete():
                return (self._cancelled(cancelled) or epoch != self._epoch or engine is not self._engine
                        or not self._active or not self._manual_alignment or map_id != self._map_id
                        or manual_target != self._manual_floor or not self._s1_floor_observer_available(engine)
                        or recovery_owner is not None and (self._map_recovery is not recovery_owner
                            or recovery_scope != self._map_recovery_scope()
                            or self._monotonic() >= recovery_owner["expiresAt"]))

            try:
                if payload.get("s1OpenEdge") is True or payload.get("foregroundReturn") is True:
                    from map_open_preparation import prepare_map_open_frame
                    preparation = _PreparationHost(self, engine, obsolete, payload)
                    try:
                        prepared = prepare_map_open_frame(preparation, None, self._cycle + 1,
                            time.perf_counter() + preparation.CAPTURE_VISUAL_DEADLINE_SECONDS,
                            _Cancellation(obsolete), round_identity_locked=True,
                            capture_context_provider=lambda hwnd: None)
                    except Exception:
                        preparation.diagnostics.finish("preparation-exception")
                        raise
                    if prepared is None:
                        with self._lock:
                            if obsolete():
                                preparation.diagnostics.finish("cancelled-or-stale")
                                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                            self._observed_floor = self._floor = ""
                            self._s1_floor_source = "unknown"
                            self._floor_required = True
                            if not self._arm_map_open_recovery(cancelled, epoch, payload):
                                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                            if payload.get("bindingResume") is True and preparation.last_capture is not None:
                                captured, capture_generation = preparation.last_capture
                                payload["_mapInputVisual"] = dict(open=None, epoch=self._epoch,
                                    sequence=self._last_map_input_sequence, captured=captured,
                                    captureGeneration=capture_generation)
                            preparation.diagnostics.finish("map-open-preparation-deadline")
                            return self._receipt(action, payload, success=False, reason="map-open-preparation-deadline", recoveryPending=True)
                    preparation.diagnostics.finish("prepared-current-frame")
                    captured, observation, observed, capture_generation, read = prepared
                    frame = _test_input_copy(self._full_frame(captured))
                else:
                    capture_generation = self._capture_generation()
                    captured = self._capture_frame(obsolete)
                    frame = _test_input_copy(self._full_frame(captured))
                    with self._lock:
                        if obsolete() or not self._capture_is_current(captured.context, capture_generation):
                            self._retire_material_owner(render_owner, "manual-observation-context-expired")
                            return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
                    observation, observed, read = self._observe(engine, frame)
                frame.setflags(write=False)
                digest = hashlib.sha256(frame.tobytes()).hexdigest()
                with self._lock:
                    if obsolete() or not self._capture_is_current(captured.context, capture_generation):
                        self._retire_material_owner(render_owner, "manual-observation-context-expired")
                        return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
                    opened = read.get("mapOpen")
                    raw = read.get("rawSelectedFloor")
                    supported = (type(raw) is int and raw in (-1, -2, -3, -4)
                                 and read.get("automaticFloorEligible") is True)
                    floor = str(raw) if opened is True and supported else ""
                    self._observed_floor = str(raw) if supported and opened is True else ""
                    self._s1_raw_floor = raw
                    self._s1_floor_reason = read.get("reason", "s1-floor-unknown")
                    self._s1_floor_source = "automatic-current-frame" if floor else "unknown"
                    if supported and manual_target is not None:
                        # Retire an old operator fallback when trusted current
                        # evidence contradicts it; never reuse it on a later layer.
                        self._manual_floor = manual_target = None
                    if opened is False:
                        self.invalidate("map-closed")
                        self._last_observation = None
                        payload["_mapInputVisual"] = dict(open=False, epoch=self._epoch,
                            sequence=self._last_map_input_sequence, captured=captured,
                            captureGeneration=capture_generation)
                        return self._receipt(action, payload, success=False, reason="s1-map-closed", mapOpen=False)
                    if opened is True:
                        # The current-frame proof consumes this ticket. Its
                        # deadline cannot later cancel a valid ongoing fit.
                        recovery_owner = recovery_scope = None
                        if not self._map_open:
                            self._cycle += 1
                        self._map_open = True
                        self._map_recovery = None
                    payload["_mapInputVisual"] = dict(open=opened, epoch=self._epoch,
                        sequence=self._last_map_input_sequence, captured=captured, captureGeneration=capture_generation)
                    if not floor:
                        retained = self._retained_material_current(self._refresh_render_owner())
                        if not retained:
                            self._revoke_render("s1-floor-unknown")
                            self._group = None
                            self._core.reset_round(reason="s1-floor-unknown")
                            self._floor = ""
                        self._floor_required = True
                        self._frame_generation += 1
                        self._frame_id = f"{self._round}:{self._frame_generation}:{digest[:16]}"
                        self._last_observation = dict(captured=captured, frame=frame, floor=None, digest=digest,
                            created_at=self._monotonic(), capture_generation=capture_generation,
                            frame_id=self._frame_id, cycle_id=self._cycle, generation=self._frame_generation,
                            observed_floor="", floor_read=copy.deepcopy(read))
                        self._publish_game_pointer_context_locked()
                        return self._receipt(action, payload, success=False,
                            reason="s1-current-floor-unavailable")
                    self._floor_required = False
                    if floor != old_floor:
                        self._revoke_render("s1-observed-floor-changed")
                        self._group = None
                        self._core.reset_round(reason="s1-observed-floor-changed")
                        self._floor = floor
                        self._reference_sheet = self._reference_capture_context = None
                if action == "observe_manual_floor" and floor == old_floor:
                    refreshed = self._try_refresh_current(payload, captured, frame, floor, digest,
                        capture_generation, engine, epoch, obsolete, floor_read=read)
                    if refreshed is not None:
                        return dict(refreshed, action=action)
                with self._lock:
                    if obsolete() or not self._capture_is_current(captured.context, capture_generation):
                        self._retire_material_owner(render_owner, "manual-observation-context-expired")
                        return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
                    self._retire_material_owner(self._refresh_render_owner(), "s1-current-floor-fit")
                    if floor != old_floor:
                        self._group = None
                        self._core.reset_round(reason="s1-observed-floor-changed")
                    self._floor = floor
                    self._frame_generation += 1
                    generation, cycle = self._frame_generation, self._cycle
                    self._frame_id = frame_id = f"{self._round}:{generation}:{digest[:16]}"
                    self._last_observation = dict(captured=captured, frame=frame, floor=floor, digest=digest,
                        created_at=self._monotonic(), capture_generation=capture_generation, frame_id=frame_id,
                        cycle_id=cycle, generation=generation, observed_floor=self._observed_floor,
                        floor_read=copy.deepcopy(read))
                    self._publish_game_pointer_context_locked()
                    group = self._group
                return self._solve_observation(action, payload, cancelled, engine, group, epoch,
                    captured, frame, floor, digest, capture_generation, generation, frame_id, cycle, map_id)
            except Exception as error:
                with self._lock:
                    if obsolete():
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True,
                            errorType=type(error).__name__)
                    self._retire_material_owner(self._refresh_render_owner(), "s1-floor-observation-error")
                raise
            finally:
                with self._lock:
                    if self._manual_capture_owner is owner:
                        self._manual_capture_owner = None
                        self._capture_pending = False

    def _capture_operator_alignment(self, action, payload, cancelled, *, expected_epoch=None):
        """Human map/floor intent plus a fresh frame; never detector evidence.

        The existing fitter and canonical residual must establish the pose.
        The explicit full-client search domain carries no map-open authority.
        """
        if expected_epoch is None:
            with self._lock:
                expected_epoch = self._epoch
        with self._work:
            with self._lock:
                if expected_epoch != self._epoch or self._cancelled(cancelled):
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                if not (self._season == "s1" and self._manual_alignment and self._active
                        and self._engine is not None and self._map_id and self._manual_floor):
                    return self._receipt(action, payload, success=False, reason="manual-map-floor-required")
                epoch, engine, group = self._epoch, self._engine, self._group
                map_id, floor = self._map_id, self._manual_floor
                self._retire_material_owner(self._refresh_render_owner(), "manual-current-frame-request")
                owner = self._manual_capture_owner = object()
                self._capture_pending = True
            obsolete = lambda: self._cancelled(cancelled) or epoch != self._epoch
            try:
                capture_generation = self._capture_generation()
                captured = self._capture_frame(obsolete)
                frame = _test_input_copy(self._full_frame(captured))
                frame.setflags(write=False)
                digest = hashlib.sha256(frame.tobytes()).hexdigest()
                with self._lock:
                    if (obsolete() or not self._manual_alignment or not self._active
                            or engine is not self._engine or map_id != self._map_id
                            or floor != self._manual_floor or floor != self._floor
                            or not self._capture_is_current(captured.context, capture_generation)):
                        return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
                    if not self._map_open:
                        self._cycle += 1
                    self._map_open = True
                    self._observed_floor = ""
                    self._floor_required = False
                    self._frame_generation += 1
                    generation, cycle = self._frame_generation, self._cycle
                    self._frame_id = frame_id = f"{self._round}:{generation}:{digest[:16]}"
                    self._last_observation = dict(captured=captured, frame=frame, floor=None, digest=digest,
                        created_at=self._monotonic(), capture_generation=capture_generation, frame_id=frame_id,
                        cycle_id=cycle, generation=generation, observed_floor="",
                        floor_read=dict(selectedFloor=None, season="s1", mode=self._mode,
                            floorSource="unavailable", reason="manual-target-not-detector-evidence"))
                return self._solve_observation(action, payload, cancelled, engine, group, epoch,
                    captured, frame, floor, digest, capture_generation, generation, frame_id, cycle, map_id)
            except Exception as error:
                with self._lock:
                    if obsolete():
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True,
                            errorType=type(error).__name__)
                raise
            finally:
                with self._lock:
                    if self._manual_capture_owner is owner:
                        self._manual_capture_owner = None
                        self._capture_pending = False

    def _capture_and_solve(self, action, payload, cancelled, *, prepared=None, recovery_scope=None, expected_epoch=None):
        if prepared is None:
            self._new_diagnostic_attempt(payload)
        with self._lock:
            if expected_epoch is not None and expected_epoch != self._epoch:
                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
            if recovery_scope is not None:
                if self._cancelled(cancelled) or recovery_scope != self._map_recovery_scope() or self._map_recovery is None:
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
            if not self._active:
                return self._receipt(action, payload, success=False, reason="round-not-active")
            epoch, engine, group = self._epoch, self._engine, self._group
            input_sequence = self._last_map_input_sequence
        # A new acquisition has its own evidence. The previous completed
        # material remains owned by its original frame until replacement or a
        # real opening/floor/resource/capture boundary.
        foreground_return = action == "capture_now" and payload.get("foregroundReturn") is True
        if prepared is None and (action not in ("refresh_map", "capture_now") or foreground_return):
            with self._lock:
                self._game_floor_intent = None
                self._floor_click_epoch_span = None
            self._retire_material_owner(self._refresh_render_owner(), "new-capture")
        obsolete = lambda: self._cancelled(cancelled) or epoch != self._epoch
        preparing = action == "map" or foreground_return
        if prepared is not None:
            captured, observation, floor, capture_generation, floor_read = prepared
            opened = True
        elif preparing:
            from map_open_preparation import prepare_map_open_frame
            preparation = _PreparationHost(self, engine, obsolete, payload)
            try:
                prepared = prepare_map_open_frame(preparation, None, self._cycle + 1,
                    time.perf_counter() + preparation.CAPTURE_VISUAL_DEADLINE_SECONDS,
                    _Cancellation(obsolete), silent_hint=True, round_identity_locked=bool(self._map_id),
                    capture_context_provider=lambda hwnd: None)
            except Exception:
                preparation.diagnostics.finish("preparation-exception")
                raise
            preparation.diagnostics.stop_reason = preparation.stop_reason
            if prepared is None:
                if obsolete():
                    preparation.diagnostics.finish("cancelled-or-stale")
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                if preparation.last_unresolved is None:
                    diagnostic = preparation.observation_diagnostic()
                    self._automatic_failure_sample("map-open-preparation-deadline",
                        captured=preparation.last_capture[0] if preparation.last_capture is not None else None,
                        attempt_id=payload.get("_diagnosticAttemptId"), current=lambda: not obsolete(),
                        diagnostic=dict(self._failure_diagnostic(payload), preparationObservation=diagnostic))
                    preparation.diagnostics.finish("map-open-preparation-deadline")
                    if not self._arm_map_open_recovery(cancelled, epoch, payload):
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                    if payload.get("bindingResume") is True and preparation.last_capture is not None:
                        captured, capture_generation = preparation.last_capture
                        payload["_mapInputVisual"] = dict(open=None, epoch=self._epoch,
                            sequence=input_sequence, captured=captured, captureGeneration=capture_generation)
                    # Returning to the game may expose black/transition frames.
                    # No observation is not proof of a physical map close:
                    # keep display revoked without resetting the key intent.
                    return self._receipt(action, payload, success=False, reason="map-open-preparation-deadline",
                        preparationObservation=diagnostic)
                # A recognized map with unreadable floor stays available for
                # explicit manual correction; it grants no floor/pose authority.
                prepared = preparation.last_unresolved
                preparation.diagnostics.finish("current-floor-unavailable")
            else:
                preparation.diagnostics.finish("prepared-current-frame")
            captured, observation, floor, capture_generation, floor_read = prepared
            opened = True
        else:
            capture_generation = self._capture_generation()
            if action == "capture_now":
                preflight = getattr(self._capture, "preflight", None)
                if callable(preflight):
                    try:
                        preflight(obsolete)
                    except Exception as error:
                        error.error_phase = "capture"
                        from client_capture import CaptureForegroundUnavailable
                        if not isinstance(error, CaptureForegroundUnavailable):
                            raise
                        with self._lock:
                            row = self._render
                            retain = bool(not obsolete() and row is not None and self._display_current(row)
                                and capture_generation == self._capture_generation()
                                and row.get("captureGeneration") == error.capture_generation
                                and self._render_capture_context == error.capture_context)
                        if not retain:
                            self._revoke_render("capture-preflight-context-invalid")
                        # Only this metadata-only refusal can retain anything.
                        # The consumer's foreground/context guard is unchanged.
                        return self._receipt(action, payload, success=False,
                            reason=f"{type(error).__name__}:{error}", error=True,
                            **self._error_fields(error, action))
                with self._lock:
                    self._game_floor_intent = None
                    self._floor_click_epoch_span = None
                self._retire_material_owner(self._refresh_render_owner(), "new-capture")
            try:
                captured = self._capture_frame(obsolete, attempt_id=payload.get("_diagnosticAttemptId"))
                captured_frame = self._full_frame(captured)
            except Exception as error:
                self._capture_diagnostic(payload, "capture-result", "capture-exception", action=action,
                    error=error)
                raise
            self._capture_diagnostic(payload, "capture-result", "frame-captured", action=action, captured=captured)
            try:
                observation, floor, floor_read = self._observe(engine, captured_frame)
            except Exception as error:
                self._capture_diagnostic(payload, "observation-result", "observation-exception", action=action,
                    error=error)
                raise
            from session_state_machine import verified_map_hotkey_frame
            opened = (observation.get("mapOpen") if self._season == "s1"
                      else verified_map_hotkey_frame(observation, floor_read))
            self._capture_diagnostic(payload, "observation-result", "current-frame-observed", action=action,
                observed=(observation, floor, floor_read, opened))
        frame = self._full_frame(captured)
        width, height = captured.context.full_client_size
        capture_snapshot = captured.frame_snapshot
        if capture_snapshot is None or capture_snapshot.pixels is not frame:
            raise ValueError('capture-frame-snapshot-unavailable')
        stream_snapshot = None
        if self._observation_stream_enabled(engine):
            stream_snapshot = capture_snapshot
            opened = observation.get("mapOpen")
        _test_evidence("input-alias", captured.frame_bgr, frame)
        if obsolete():
            return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
        map_detected = observation.get("detected") is True
        floor_ready = map_detected
        with self._lock:
            if (floor_ready
                    and (obsolete() or engine is not self._engine or group is not self._group
                         or not self._capture_is_current(captured.context, capture_generation)
                         or capture_generation != self._capture_generation())):
                return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
        # The policy owns its lock and rechecks obsolescence. Holding the
        # caller lock across it would block new UI drag ownership/cancellation.
        policy_obsolete = lambda: (obsolete() or floor_ready and (
            engine is not self._engine or group is not self._group
            or not self._capture_is_current(captured.context, capture_generation)
            or capture_generation != self._capture_generation()))
        effective_floor = self._observe_floor_policy(floor_read, obsolete=policy_obsolete) if floor_ready else None
        if floor_ready:
            payload["_mapInputVisual"] = dict(open=True, epoch=epoch, sequence=input_sequence,
                captured=captured, captureGeneration=capture_generation)
        if floor is None and map_detected:
            with self._lock:
                if obsolete():
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                retained = self._retained_material_current(self._refresh_render_owner())
                self._retire_material_owner(self._refresh_render_owner(), "current-floor-unavailable")
            digest = capture_snapshot.frame_key
            with self._lock:
                if obsolete():
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                if not self._map_open:
                    self._cycle += 1
                self._map_open = True
                if not retained:
                    self._floor = effective_floor or ""
                if action == "recover_map_open":
                    payload["_recoveryResultScope"] = self._map_recovery_scope()
                if effective_floor is None:
                    self._floor_required = True
                self._frame_generation += 1
                self._frame_id = f"{self._round}:{self._frame_generation}:{digest[:16]}"
                self._last_observation = dict(captured=captured, frame=frame, floor=None, digest=digest, created_at=self._monotonic(),
                    capture_generation=capture_generation, frame_id=self._frame_id, observed_floor="",
                    cycle_id=self._cycle, generation=self._frame_generation, floor_read=copy.deepcopy(floor_read))
                self._publish_game_pointer_context_locked()
                for row in self._candidate_rows:
                    row["floor"] = self._floor
            if effective_floor is not None and (not retained or self._manual_override):
                return self._solve_retained(action, payload, cancelled, acquire_current=False, expected_epoch=epoch)
            self._terminal_failure_sample("current-floor-unavailable", payload, captured, lambda: not obsolete())
            return self._receipt(action, payload, success=False, reason="current-floor-unavailable", observation=observation,
                preparationObservation=preparation.observation_diagnostic() if preparing else {})
        if opened is not True:
            if action == "refresh_map" or self._retained_material_current(self._refresh_render_owner()):
                from session_state_machine import map_probe_transition
                probe_open = (observation.get("mapOpen") if engine.pack.mode == "difficult"
                              else bool(observation.get("detected")))
                with self._lock:
                    if obsolete():
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                    self._probe_misses, transition = map_probe_transition(
                        self._map_open, self._probe_misses, probe_open)
                    reason = "current-floor-unavailable" if probe_open is True and floor is None else "map-not-observed"
                    self._retire_material_owner(self._refresh_render_owner(), reason)
                    # While a failed-fit segment awaits the existing two-miss
                    # close decision, don't report its first probe miss as a
                    # separate operation failure. No segment: legacy reporting.
                    deferred = bool(reason == "map-not-observed"
                        and self._fit_reporting.scope == self._fit_reporting_scope()
                        and self._fit_reporting.report and not self._fit_reporting.succeeded)
                    if transition == "close":
                        deferred = self.invalidate("map-closed", source="passive-map-probe")
                        payload["_mapInputVisual"] = dict(open=False, epoch=self._epoch, sequence=input_sequence,
                            captured=captured, captureGeneration=capture_generation)
                    return self._receipt(action, payload, success=False, reason=reason,
                        observation=observation, fitReportingDeferred=deferred)
            with self._lock:
                if obsolete():
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                deferred = self.invalidate("map-closed", source="capture-map-not-observed")
                payload["_mapInputVisual"] = dict(open=False, epoch=self._epoch, sequence=input_sequence,
                    captured=captured, captureGeneration=capture_generation)
                return self._receipt(action, payload, success=False, reason="map-not-observed",
                    observation=observation, fitReportingDeferred=deferred)
        if floor is None or str(floor) not in tuple(map(str, engine.identity_floors)):
            with self._lock:
                if obsolete():
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                self._observed_floor = ""
                self._last_observation = None
                self._pointer_context_snapshot = None
                if action == "refresh_map":
                    self._retire_material_owner(self._refresh_render_owner(), "current-floor-unavailable")
                else:
                    self.invalidate("floor-unknown")
                failure_receipt = self._receipt(action, payload, success=False,
                    reason="current-floor-unavailable", observation=observation)
                failure_epoch = self._epoch
            self._terminal_failure_sample("current-floor-unavailable", payload,
                captured if action == "refresh_map" else None,
                lambda: not self._cancelled(cancelled) and self._active and failure_epoch == self._epoch)
            return failure_receipt
        if effective_floor is None:
            with self._lock:
                if obsolete():
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                self._retire_material_owner(self._refresh_render_owner(), "floor-policy-deferred")
                return self._receipt(action, payload, success=False, reason="floor-policy-deferred")
        floor = effective_floor
        digest = capture_snapshot.frame_key
        with self._lock:
            if obsolete():
                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
            pending_selection = self._selection_pending
            if pending_selection:
                self._revoke_render("candidate-awaiting-confirmation")
        if action == "refresh_map" and not pending_selection and not self._candidate_provisional_enabled():
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
            if action == "recover_map_open":
                payload["_recoveryResultScope"] = self._map_recovery_scope()
            self._reset_provisional_floor(floor)
            self._floor = floor
            if self._s1_structure_session is not None:
                self._s1_structure_session.revoke_current()
                self._s1_structure_ticket = None
                self._automatic_candidate_rows = []
                for row in self._candidate_rows:
                    row["structureSupported"] = False
            self._frame_generation += 1
            self._frame_id = f"{self._round}:{self._frame_generation}:{digest[:16]}"
            frame_generation, frame_id, cycle = self._frame_generation, self._frame_id, self._cycle
            map_id = self._map_id
            self._last_observation = dict(captured=captured, frame=frame, floor=floor, digest=digest, created_at=self._monotonic(),
                capture_generation=capture_generation, frame_id=frame_id, observed_floor=self._observed_floor,
                cycle_id=self._cycle, generation=self._frame_generation, floor_read=copy.deepcopy(floor_read or {}))
            self._publish_game_pointer_context_locked()
            for row in self._candidate_rows:
                row["floor"] = floor
        if pending_selection:
            self._terminal_failure_sample("candidate-awaiting-confirmation", payload, captured, lambda: not obsolete())
            return self._receipt(action, payload, success=False, reason="candidate-awaiting-confirmation")
        ranked = []
        entry_candidate = None
        if not map_id:
            if self._manual_selection_unlocked():
                # Capture, map-open detection and floor reading above already
                # happened; only the automatic identity request is skipped.
                return self._receipt(action, payload, success=True,
                    reason="manual-map-selection-pending", posePending=True,
                    manualSelectionPending=True, observation=observation)
            if self._observation_stream_enabled(engine):
                return self._stream_observation(action, payload, cancelled, engine, epoch,
                    captured, frame, floor, digest, capture_generation, frame_generation,
                    frame_id, cycle, stream_snapshot)
            if engine.pack.mode == "difficult" and str(floor) == "2":
                return self._receipt(action, payload, success=False,
                    reason="difficult-f2-entry-recognition-disabled", posePending=True)
            # Opening the map again refreshes the selected member's geometry.
            # Only an identity reset/new round starts discovery; later side
            # views must not silently replace a front-group member selection.
            map_id, entry_candidate, stale = self._collect_legacy_entry_evidence(engine, frame, floor, epoch, frame_id, cancelled)
            if stale:
                return self._receipt(action, payload, success=False, reason=str(stale), stale=True)
            if not map_id:
                if self._season == "s1":
                    return self._s1_structure_pending_receipt(action, payload)
                grouped = self._identity_fields()["groupLocked"]
                self._terminal_failure_sample("similar-group-pending" if grouped else "identity-not-uniquely-verified",
                    payload, captured, lambda: not obsolete() and frame_id == self._frame_id)
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
                immutable=np.ascontiguousarray(frame).copy();_test_evidence("input-alias", frame, immutable);immutable.setflags(write=False);frame=immutable
                digest=hashlib.sha256(frame.tobytes()).hexdigest()
                self._frame_generation+=1
                self._frame_id=f"{self._round}:{self._frame_generation}:{digest[:16]}"
                self._last_observation=dict(captured=captured,frame=frame,floor=floor,digest=digest,created_at=self._monotonic(),
                    capture_generation=capture_generation,frame_id=self._frame_id,observed_floor=self._observed_floor,
                    cycle_id=self._cycle,generation=self._frame_generation,floor_read=copy.deepcopy(floor_read or {}))
                self._publish_game_pointer_context_locked()
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
        rgba=_display_rgba(layer)
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
            metadata=dict(season=self._season, mode=self._mode,profileId=profile,preferredProfileId=profile,
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
                                  display, frame, captured, cycle, frame_generation, residual, *, component_override=None,
                                  observation_scope=None):
        """Reuse the original single-root relation and validation-pool writer."""
        from types import SimpleNamespace
        from round_layer_group_commit import (commit_alignment_v2_round_pose,
            prime_round_layer_group_relations, map_anchor_focus_point)
        if self._season == "s1":
            bounds = (component_override or {}).get("bounds")
            if (not isinstance(bounds, (tuple, list)) or len(bounds) != 4
                    or not (0 <= bounds[0] < bounds[2] <= frame.shape[1]
                            and 0 <= bounds[1] < bounds[3] <= frame.shape[0])):
                return None
        stage = group if group is not None and group.map_id == map_id else None
        owner = SimpleNamespace(pack=engine.pack, round_layer_group=stage,
            author_coordinate_adapters=engine.qt_adapters, active_map_cycle_id=cycle,
            current_frame_generation=frame_generation, display_environment=None,
            _native_similarity_resources_ready=lambda: True,
            _open_cycle_transient_motion_active=lambda _map: False,
            _shared_structure_mode_enabled=lambda: True, _log=lambda *args, **kwargs: None)
        if self._season == "s1":
            owner.round_floor_ids = tuple(engine.identity_floors)
        owner._active_round_layer_group = lambda mid, profile=None: (
            owner.round_layer_group if owner.round_layer_group is not None
            and owner.round_layer_group.map_id == mid
            else None)
        owner._prime_round_layer_group_relations = (lambda mid, anchor: {}) if self._season == "s1" else (
            lambda mid, anchor: prime_round_layer_group_relations(owner, mid, anchor))
        context = captured.context
        signature = (tuple(context.full_client_size), tuple(context.client_screen_bounds), context.dpi)
        accepted = commit_alignment_v2_round_pose(owner, map_id, floor, display["pixels"], frame,
            context.client_screen_bounds, render_matrix=render_matrix, canonical_matrix=canonical,
            route_profile_id=display.get("effectiveProfileId", "shared-structure"), source="initial-structure-v2", residual=residual,
            component_override=component_override, observation_scope=observation_scope,
            normalize_route_profile_id=lambda profile: profile,
            display_environment_signature=lambda _: signature, map_anchor_focus_point=map_anchor_focus_point)
        return owner.round_layer_group if accepted else None

    def _solve_observation(self, action, payload, cancelled, engine, group, epoch, captured, frame,
                           floor, digest, capture_generation, frame_generation, frame_id, cycle,
                           map_id, entry_candidate=None, *, identity_stage=None, manual_choice=None,
                           staged_observation=None, structure_ticket=None, structure_current=None):
        import numpy as np
        from alignment_core_v2 import AlignmentCandidate
        from alignment_observation import rendered_profile_residual_is_verified
        from client_providers import DisplayResourceChanged
        from round_layer_group import RoundLayerGroup
        from round_layer_group_commit import RoundPoseObservationScope
        with self._lock:
            provisional_operation = self._candidate_provisional_enabled(engine)
            operation_settings_generation = self._settings_generation
            registered_state = clone_similarity_state(identity_stage or self._identity_native)
        alignment_current = lambda: True
        obsolete = lambda: (self._cancelled(cancelled) or epoch != self._epoch or not alignment_current()
            or structure_current is not None and not structure_current()
            or provisional_operation and operation_settings_generation != self._settings_generation
            or staged_observation is not None and not self._staged_choice_current(staged_observation))
        def frame_current():
            if staged_observation is not None:
                return bool(self._staged_choice_current(staged_observation)
                    and (frame_id, frame_generation) ==
                        (staged_observation["frame_id"], staged_observation["generation"]))
            return (frame_id, frame_generation) == (self._frame_id, self._frame_generation)
        width, height = captured.context.full_client_size
        with self._lock:
            if obsolete() or not frame_current() or floor != self._floor:
                return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
            if self._current_frame_floor_conflicts(floor, frame_id=frame_id):
                reason = "current-frame-floor-conflict"
                observed_floor = self._observed_floor
                self._revoke_render(reason)
                self._reference_sheet = self._reference_capture_context = None
                self._set_pose_phase("rejected", reason, map_id, floor, frame_id, frame_generation,
                    staged_observation=staged_observation,
                    observedFloor=observed_floor, targetFloor=str(floor), fitAttempted=False)
                return self._receipt(action, payload, success=False, reason=reason,
                    observedFloor=observed_floor, targetFloor=str(floor), fitAttempted=False)
            display_generation = getattr(self, "_display_generation", 0)
            group_intent_generation = self._group_intent_generation
            profile = self._profile
            variant_id = self._settings.get("zhan10CoffinVariantId", "variant-01")
            material_owner = self._refresh_render_owner()

        def withdraw_changed_display(owner):
            # Resource proof belongs to this exact consumption, not a newer
            # material/drag or another member's separately owned source.
            with self._lock:
                row = self._render
                if (epoch == self._epoch and engine is self._engine
                        and profile == self._profile and floor == self._floor
                        and display_generation == getattr(self, "_display_generation", 0)
                        and owner == self._refresh_render_owner()
                        and (row is None or row["mapId"] == map_id)):
                    self.invalidate("display-resource-changed", source=action)

        # Static reference follows the independently confirmed map and floor;
        # a failed pose solve must not also remove the player's reference map.
        try:
            prepared_display = self._display_layer(engine, map_id, floor, profile, variant_id=variant_id)
        except DisplayResourceChanged:
            withdraw_changed_display(material_owner)
            raise
        # One solve owns its material. Detach only small metadata and the bridge;
        # neither a provider nor a later publication may rewrite this snapshot.
        display = {key: value if key in ("pixels", "rgbaPixels") else copy.deepcopy(value)
            for key, value in prepared_display.items()}
        display["matrixDisplayToReference"] = np.asarray(display["matrixDisplayToReference"], np.float64)
        display["matrixDisplayToReference"].setflags(write=False)
        reference_pixels = _display_rgba(display)
        with self._lock:
            if display_generation != getattr(self, "_display_generation", 0) or profile != self._profile:
                return self._receipt(action, payload, success=False, reason="stale-display-settings", stale=True)
            if (group_intent_generation != self._group_intent_generation
                    or manual_choice is None and self._group_choice_inflight is not None):
                return self._receipt(action, payload, success=False, reason="manual-group-intent-superseded-work", stale=True)
            if obsolete() or not frame_current() or floor != self._floor:
                return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
            if capture_generation != self._capture_generation():
                return self._receipt(action, payload, success=False, reason="capture-context-changed", stale=True)
            if (self._render is not None and self._render.get("mapId") == map_id
                    and self._render.get("floor") == floor and self._render.get("profileId") == profile
                    and self._render.get("resourceIdentity") != display["resourceIdentity"]):
                withdraw_changed_display(material_owner)
                return self._receipt(action, payload, success=False, reason="display-resource-changed", stale=True)
            reference_sheet = dict(token=uuid4().hex, pixels=reference_pixels,
                season=self._season, mode=self._mode, profileId=profile, mapId=map_id, floor=floor, roundId=self._round,
                variantId=display.get("variantId"), displayGeneration=display_generation,
                resourceIdentity=display["resourceIdentity"], captureGeneration=capture_generation, engineIdentity=id(engine),
                referenceAuthorized=self._evidence_kind == "live-client-capture", evidenceKind=self._evidence_kind,
                preferredProfileId=profile, effectiveProfileId=display.get("effectiveProfileId", display["routeProfileId"]),
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
        if transient and staged_observation is None:
            projected=self._try_transient_floor_projection(payload,cancelled,engine,captured,frame,floor,capture_generation)
            if projected is not None:return projected
            with self._lock:
                if not obsolete() and frame_id==self._frame_id:
                    self._reference_sheet,self._reference_capture_context=reference_sheet,captured.context
            return self._receipt(action,payload,success=False,reason="transient-projection-unavailable",fitAttempted=False)
        self._set_pose_phase("pending", "current-frame-fit", map_id, floor, frame_id, frame_generation,
            staged_observation=staged_observation)
        with self._lock:
            steady_context = self._steady_context()
        published_token = None
        review_core = None
        observation_scope = RoundPoseObservationScope(frame)

        def revoke_published(reason):
            with self._lock:
                if self._render is not None and self._render["token"] == published_token:
                    self._retire_material_owner(self._refresh_render_owner(), reason)

        def publish_fit(fit, *, review_pending=False):
            nonlocal published_token, review_core
            residual = fit.get("selectedRouteCurrentFrameResidual")
            if not rendered_profile_residual_is_verified(residual):
                raise ValueError("current-frame-canonical-verification-required")
            source_matrix = np.asarray(fit["matrixSourceToClient"], np.float64)
            basis = engine.qt_adapters.matrix_profile_to_canonical("shared-structure", map_id, floor)
            if basis is None:
                raise ValueError("shared-reference-canonical-basis-unavailable")
            canonical = source_matrix @ np.linalg.inv(np.asarray(basis))
            render_matrix = source_matrix @ display["matrixDisplayToReference"]
            try:
                validate_established_registered_family(engine, registered_state)
            except (OSError, ValueError) as error:
                with self._lock:
                    if obsolete() or not frame_current() or engine is not self._engine:
                        return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                    self.invalidate('registered-group-resource-unavailable', source=action)
                    return self._receipt(action, payload, success=False, reason='registered-group-resource-unavailable',
                                         messageDetail=str(error)[:512])
            with self._lock:
                proof_owner = self._refresh_render_owner()
            try:
                if profile == "shared-structure" and display["displayKind"] == "shared-structure":
                    from client_route_preferences import shared_structure_display_current
                    display_current = shared_structure_display_current(engine, map_id, floor, display)
                else:
                    # Author/custom selection and structural fallback retain
                    # their original policy, source and generation checks.
                    current_display = self._display_layer(engine, map_id, floor, profile, variant_id=variant_id)
                    display_current = current_display["resourceIdentity"] == display["resourceIdentity"]
            except DisplayResourceChanged:
                withdraw_changed_display(proof_owner)
                raise
            if not display_current:
                withdraw_changed_display(proof_owner)
                return self._receipt(action, payload, success=False, reason="display-resource-changed", stale=True)
            with self._lock:
                if registered_family_identity(registered_state) != registered_family_identity(self._identity_native):
                    return self._receipt(action, payload, success=False, reason='registered-group-context-changed', stale=True)
                previous_group = copy.deepcopy(group) if group is not None else None
            target_group = self._stage_round_group_commit(engine, previous_group, map_id, floor, canonical,
                render_matrix, display, frame, captured, cycle, frame_generation, residual,
                component_override=fit.get("observationComponent") if self._season == "s1" else None,
                observation_scope=observation_scope)
            if target_group is None:
                reason = "round-group-pose-commit-rejected"
                self._set_pose_phase("rejected", reason, map_id, floor, frame_id, frame_generation,
                    staged_observation=staged_observation)
                return self._receipt(action, payload, success=False, reason=reason)
            with self._lock:
                if display_generation != getattr(self, "_display_generation", 0) or profile != self._profile:
                    return self._receipt(action, payload, success=False, reason="stale-display-settings", stale=True)
                if (group_intent_generation != self._group_intent_generation
                        or manual_choice is None and self._group_choice_inflight is not None):
                    return self._receipt(action, payload, success=False, reason="manual-group-intent-superseded-work", stale=True)
                if (obsolete() or not frame_current() or cycle != self._cycle or floor != self._floor
                        or engine is not self._engine):
                    return self._receipt(action, payload, success=False, reason="stale-context", stale=True)
                if not self._capture_is_current(captured.context, capture_generation):
                    self.invalidate("capture-context-changed")
                    return self._receipt(action, payload, success=False, reason="capture-context-changed", stale=True)
                source_core = review_core if review_core is not None else self._core
                core = self._copy_pose_core(source_core) if (review_pending or identity_stage is not None
                    or provisional_operation and self._render is not None
                    or self._retained_material_current(self._refresh_render_owner())) else source_core
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
                    self._set_pose_phase("rejected", decision.reason, map_id, floor, frame_id, frame_generation,
                        staged_observation=staged_observation)
                    return self._receipt(action, payload, success=False, reason=decision.reason)
                token = core.authorize(decision)
                if token is None or not core.commit(token, persist_pose=not (review_pending or provisional_operation)):
                    return self._receipt(action, payload, success=False, reason="core-commit-rejected")
                if not review_pending and not provisional_operation:
                    self._seed_group_core(core,target_group)
                rgba = reference_pixels
                live = self._evidence_kind == "live-client-capture"
                target_render = dict(token=uuid4().hex, epoch=epoch, authorizationId=token.authorization_id,
                    season=self._season, mode=self._mode, profileId=profile, mapId=map_id, floor=floor,
                    roundId=self._round, cycleId=cycle, frameId=frame_id, generation=frame_generation,
                    matrix=render_matrix.tolist(), canonicalMatrix=canonical.tolist(), pixels=rgba,
                    routeWidth=rgba.shape[1], routeHeight=rgba.shape[0], clientWidth=width, clientHeight=height,
                    originX=captured.context.client_screen_bounds[0], originY=captured.context.client_screen_bounds[1],
                    captureGeneration=capture_generation,
                    poseReason=fit.get("reason"), poseStrategy=fit.get("strategy", "not-recorded"),
                    floorSource=self._current_floor_source(), observedFloor=self._observed_floor,
                    resourceIdentity=display["resourceIdentity"], displayKind=display["displayKind"],
                    referencePath=display.get("referencePath", ""),
                    displayLabel=display["displayLabel"], routeProfileId=display["routeProfileId"],
                    preferredProfileId=profile, effectiveProfileId=display.get("effectiveProfileId", display["routeProfileId"]),
                    authorStatus=display.get("authorStatus", "none"), fallback=display.get("fallback", False),
                    fallbackReason=display.get("fallbackReason", ""),
                    variantId=display.get("variantId"), displayGeneration=display_generation,
                    liveAuthorization=live, evidenceKind=self._evidence_kind,
                    reviewStatus="pending" if review_pending else "complete",
                    poseFinalVerified=not review_pending, **self._identity_fields())
                render_token = target_render["token"]
                if not review_pending:
                    self._register_material_proposal(target_render, frame, fit, captured, target_group,
                        candidate_only=provisional_operation)
                if review_pending:
                    # Only presentation authority advances. Identity, group and
                    # persistent pose remain with their existing owners until review.
                    review_core = self._core
                    self._core, self._render = core, target_render
                    self._render_capture_context = captured.context
                    published_token = render_token
                elif identity_stage is not None:
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
                        reference=reference_sheet, captureContext=captured.context, manualChoice=manual_choice,
                        stagedObservation=staged_observation)
                else:
                    self._core = core
                    if provisional_operation:
                        self._provisional_display_map_id = map_id
                        self._identity_source = f"{self._season}-current-structure-hypothesis"
                        self._gate.select(map_id)
                        # Neither _map_id nor confirmed_id acquires candidate state.
                        target_render.update(self._identity_fields())
                        target_render.update(identitySource=self._identity_source,
                            identityVerified=False, requiresManualConfirmation=False,
                            displayAuthorizationScope="current-native-frame-candidate" if live else "current-recorded-frame-candidate",
                            candidatePurpose=("native-s1-preview" if self._native_s1_candidate_preview else "recorded-s1-preview")
                                if self._season == "s1" else "current-s0-structure-preview")
                        self._group, self._render = None, target_render
                    else:
                        self._map_id = map_id
                        self._gate.select(map_id)
                        self._gate.confirm()
                        self._group, self._render = target_group, target_render
                    published_token = render_token
                    if action == "refresh_map":
                        payload["_refreshRenderOwner"] = self._refresh_render_owner()
                    self._fit_reporting.success(self._fit_reporting_scope())
                    if action == "recover_map_open":
                        payload["_recoveryRenderOwner"] = self._refresh_render_owner()
                        payload["_recoveryResultScope"] = self._map_recovery_scope()
                    self._render_capture_context = captured.context
                    if provisional_operation:
                        pool = self._s1_private_pool
                        reference_sheet.update(self._identity_fields(), candidateReference=True,
                            s1CandidateReference=self._season == "s1",
                            s1ReferencePoolIdentity=id(pool) if pool is not None else None,
                            s1ReferenceRecoveryRevision=pool.recovery_revision if pool is not None else None,
                            referenceAuthorizationScope="current-candidate-static-reference")
                    self._reference_sheet = reference_sheet
                    self._reference_capture_context = captured.context
                    self._remember_provisional_reference()
            self._set_pose_phase("provisional" if review_pending else "valid",
                "current-frame-review-pending" if review_pending else "current-frame-pose-authorized",
                map_id, floor, frame_id, frame_generation,
                staged_observation=staged_observation)
            if identity_stage is not None:
                self._notify("sibling-material-pending", reason="target-current-pose-awaiting-material", token=render_token,
                    sourceMapId=self._map_id, targetMapId=map_id)
                return self._receipt(action, payload, success=True, reason="sibling-material-pending",
                    renderToken=source_token, pendingSiblingToken=render_token, diagnosticOnly=not live)
            self._notify("render-ready",
                reason="current-frame-review-pending" if review_pending else "current-frame-pose-authorized",
                token=render_token, unchanged=False)
            if obsolete():
                return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
            return self._receipt(action, payload, success=True,
                                 reason="current-frame-review-pending" if review_pending else "current-frame-pose-authorized",
                                 renderToken=render_token, diagnosticOnly=not live,
                                 poseReason=fit.get("reason"), poseStrategy=fit.get("strategy", "not-recorded"))

        def offer_usable_fit(first):
            # Sibling material handoff and candidate tickets retain their
            # existing independent acknowledgment/eligibility protocols.
            if published_token is None and identity_stage is None and not provisional_operation:
                with self._lock:
                    if self._retained_material_current(self._refresh_render_owner()):
                        return  # Keep completed pixels while final review owns the new verdict.
                publish_fit(first, review_pending=True)

        host, preparation, steady_receipt = _prepare_alignment_host(engine, group, self._settings,
            map_id, floor, frame=frame, cancelled=obsolete,
            steady=None if staged_observation is not None else (self._steady, steady_context,
            lambda context: not obsolete() and self._steady_is_current(context, captured),
            self._steady_signature(captured)), usable_fit_callback=offer_usable_fit)
        alignment_current = lambda: _alignment_host_current(engine, host, steady_receipt)
        if obsolete():
            return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
        fit_started = time.monotonic()
        fit_timing = {}
        try:
            if provisional_operation or structure_ticket is not None:
                # Reuse only the current ticket's already verified fit. All residual,
                # material, floor, generation and capture checks below still run.
                with self._lock:
                    session, ticket = structure_ticket or (self._s1_structure_session, self._s1_structure_ticket)
                    current_ticket = session is self._s1_structure_session and ticket == self._s1_structure_ticket
                    choice = session.choose_candidate(map_id, ticket) if current_ticket and session is not None else {}
                if (not choice.get("success") or choice.get("frameKey") != digest
                        or choice.get("floor") != str(floor)
                        or choice.get("season") != self._season or choice.get("mode") != self._mode):
                    if not provisional_operation:
                        choice = None  # Entry identity may precede its geometry scan.
                    else:
                        return self._receipt(action, payload, success=False, reason="stale-structure-fit", stale=True)
                if choice is not None:
                    fit = choice["pose"]
                else:
                    fit = _fit_alignment_host(host, frame, map_id, floor, obsolete, entry_candidate,
                        on_usable_fit=offer_usable_fit, timing=fit_timing)
            elif self._season == "s1":
                fit = _fit_alignment_host(host, frame, map_id, floor, obsolete,
                    on_usable_fit=offer_usable_fit, timing=fit_timing,
                    observation_bounds=(0, 0, frame.shape[1], frame.shape[0]))
            else:
                fit = _fit_alignment_host(host, frame, map_id, floor, obsolete, entry_candidate,
                    on_usable_fit=offer_usable_fit, timing=fit_timing)
        except BaseException:
            revoke_published("pose-review-error")
            raise
        self._recognition_diagnostic('pose-fit', fit.get('reason', 'reason-not-provided'),
            success=fit.get('success'), fit=fit, solverEvents=host.events,
            preparation=preparation, calculation=fit_timing,
            elapsedMs=(time.monotonic()-fit_started)*1000,
            inputs=dict(mapId=map_id, floor=floor, frameId=frame_id,
                cycleId=cycle, captureGeneration=capture_generation, sourceFrameGeneration=frame_generation))
        if obsolete():
            revoke_published("pose-review-obsolete")
            return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
        if not fit.get("success"):
            revoke_published("pose-review-rejected")
            reason = str(fit.get("reason", "pose-fit-failed"))
            if reason in ('structure-current-geometry-failed', 'initial-structure-current-geometry-failed', 'current-geometry-failed'):
                self._automatic_failure_sample(reason, captured=captured, attempt_id=payload.get("_diagnosticAttemptId"),
                    current=lambda: not obsolete() and frame_current() and self._capture_is_current(captured.context, capture_generation),
                    diagnostic=dict(self._failure_diagnostic(payload), mapId=map_id, floor=floor,
                                    captureGeneration=capture_generation))
            try:
                stamp = self._failure_capture_stamp
                if (self._failure_samples.enabled and stamp is not None and stamp[0] is captured
                        and self._failure_samples.can_record(frame, (self._mode, map_id, self._profile, floor, reason))):
                    def sample_current():
                        with self._lock:
                            return bool(not obsolete() and self._active and self._map_open
                                and frame_current() and floor == self._floor
                                and cycle == self._cycle and engine is self._engine
                                and self._capture_is_current(captured.context, capture_generation))
                    reference = host._shared_structure_reference("shared-structure", map_id, floor)
                    resource = dict(metadata=reference.metadata,
                        cleanMaskSha256=hashlib.sha256(reference.clean_mask.tobytes()).hexdigest(),
                        validationMaskSha256=hashlib.sha256(reference.validation_mask.tobytes()).hexdigest()) if reference is not None else None
                    diagnostic = dict(season=self._season, mode=self._mode, profileId=self._profile, evidenceKind=self._evidence_kind,
                        frameId=frame_id, sourceFrameGeneration=frame_generation,
                        captureGeneration=capture_generation, requestGeneration=payload.get("generation"),
                        roundId=self._round, cycleId=cycle, epoch=epoch,
                        frameCapturedAtUtc=stamp[1], frameCapturedMonotonicAt=stamp[2],
                        timestampBasis="capture-return", fullClientSize=list(captured.context.full_client_size),
                        clientBounds=list(captured.context.client_screen_bounds), dpi=captured.context.dpi,
                        roiClientBounds=list(captured.roi_client_bounds), sharedResource=resource,
                        displayResourceIdentity=display.get("resourceIdentity"),
                        floorRead=copy.deepcopy((staged_observation or self._last_observation or {}).get("floor_read", {})))
                    saved = self._failure_samples.record(getattr(engine, "memory_store", None),
                        map_id=map_id, floor=floor, reason=reason, fit=fit, frame=frame,
                        client_bounds=captured.context.client_screen_bounds, cycle_id=cycle,
                        diagnostic=diagnostic, current=sample_current)
                    self._notify("failure-sample", reason="saved" if saved else "not-saved",
                        failureSample=dict(savedCount=self._failure_samples.saved_count,
                            error=self._failure_samples.last_error, frameCapturedAtUtc=stamp[1]))
            except Exception as error:
                # Optional sample preparation must not replace the fit verdict.
                self._failure_samples.last_error = type(error).__name__
                self._notify("failure-sample", reason="not-saved", failureSample=dict(
                    savedCount=self._failure_samples.saved_count, error=self._failure_samples.last_error))
            reopened = False if staged_observation is not None else self._consume_identity_conflict(fit, engine=engine, epoch=epoch, captured=captured,
                capture_generation=capture_generation, frame_id=frame_id, frame_generation=frame_generation,
                cycle=cycle, map_id=map_id, floor=floor)
            with self._lock:
                # Recheck the originating lifetime before touching reporting state.
                if not reopened and (obsolete() or not frame_current()
                                     or cycle != self._cycle or floor != self._floor):
                    return self._receipt(action, payload, success=False, reason="cancelled-or-stale", stale=True)
                from client_fit_reporting import RETRY_REASONS
                deferred = reason in RETRY_REASONS and not reopened and staged_observation is None
                if deferred:
                    # Only actual solver fields are retained; absent diagnostics
                    # are explicitly unavailable, never inferred from a later frame.
                    diagnostics = {key: copy.deepcopy(fit[key]) for key in
                        ("strategy", "elapsedMs", "selectedRouteCurrentFrameResidual") if key in fit}
                    if not diagnostics:
                        diagnostics = {"availability": "not-provided-by-solver"}
                    report, waiting = self._fit_reporting.failure(
                        self._fit_reporting_scope(map_id, floor), reason, self._monotonic(), diagnostics)
                    self._notify("map-fit-retry", reason=reason, fitReport=report)
                    if waiting:
                        self._notify("map-fit-waiting", reason=reason)
                self._set_pose_phase("rejected", reason, map_id, floor, frame_id, frame_generation,
                    staged_observation=staged_observation)
                return self._receipt(action, payload, success=False, reason=reason,
                    identityReopened=reopened, fitReportingDeferred=deferred)
        try:
            result = publish_fit(fit)
        except BaseException:
            revoke_published("pose-review-error")
            raise
        if not result.get("success"):
            revoke_published("pose-review-rejected")
        return result

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
        _test_evidence("full-input", captured, frame)
        return frame

    def _observe(self, engine, frame):
        started = time.monotonic()
        settings = dict(getattr(self, "_settings", None) or {}, gameSeason=self._season)
        if self._manual_alignment and self._s1_floor_observer_available(engine):
            from s1_floor_observation import detect_s1_floor
            read = detect_s1_floor(frame, season=self._season, mode=self._mode,
                                   controls=getattr(engine, 'qt_controls_profile', None))
            read = dict(read, rawSelectedFloor=read.get("selectedFloor"),
                        controlGeometryVerified=False, floorSource="current-frame")
            if read.get("automaticFloorEligible") is not True:
                read["selectedFloor"] = None
            contract = self._floor_contract()
            floor = contract.floor_id_for(read.get("selectedFloor")) if contract else None
            opened = read.get("mapOpen")
            observation = dict(read.get("mapObservation") or {}, detected=opened is True,
                mapOpen=opened, floor=floor, layoutFamily="s1-vertical")
            self._remember_control_observation(frame, engine.pack.mode, observation, read)
            self._recognition_diagnostic("map-floor-observation", read["reason"], observation=observation,
                floorRead=read, elapsedMs=(time.monotonic()-started)*1000)
            return observation, floor, read
        if self._season == "s1" and self._s1_candidate_available(engine=engine):
            from s1_floor_observation import detect_s1_floor
            floor_read = detect_s1_floor(frame, season=self._season, mode=engine.pack.mode,
                                         controls=getattr(engine, 'qt_controls_profile', None))
            selected = floor_read.get("selectedFloor")
            if selected is not None and (type(selected) is not int or selected not in (-1, -2, -3, -4)
                    or floor_read.get("observedFloorValidated") is not True
                    or floor_read.get("automaticFloorEligible") is not True
                    or floor_read.get("mapOpen") is not True):
                floor_read = dict(floor_read, selectedFloor=None, reason="s1-floor-current-evidence-unvalidated")
            floor_read.update(controlAxis="vertical", controlGeometryVerified=False,
                              floorSource="current-frame-structure-candidate")
            opened = floor_read.get("mapOpen") is True
            contract = self._floor_contract()
            floor = contract.floor_id_for(floor_read.get("selectedFloor")) if contract else None
            result = dict(floor_read.get("mapObservation") or {}, detected=opened, mapOpen=opened,
                          floor=floor, layoutFamily="s1-vertical", controlAxis="vertical")
            if opened:
                self._latest_map_frame = (frame, engine.pack.mode, floor_read)
            self._remember_control_observation(frame, engine.pack.mode, result, floor_read)
            self._recognition_diagnostic("map-floor-observation", floor_read["reason"],
                observation=result, floorRead=floor_read, elapsedMs=(time.monotonic()-started)*1000)
            return result, floor, floor_read
        if self._season == "s1":
            unavailable = dict(selectedFloor=None, reason="s1-floor-control-detector-unvalidated",
                confidence=0., season="s1", mode=engine.pack.mode, controlAxis="vertical",
                controlGeometryVerified=False, floorSource="unavailable")
            return dict(detected=False, mapOpen=None, reason=unavailable["reason"]), None, unavailable
        from s0_vertical_controls import observe_s0_map
        result, floor_read = observe_s0_map(frame, engine.pack.mode, settings=settings, season=self._season,
                                          controls=getattr(engine, 'qt_controls_profile', None))
        floor = result.get("floor")
        if result.get("detected") is True:
            self._latest_map_frame = (frame, engine.pack.mode, floor_read)
        if self._diagnostics is not None:
            self._diagnostics.record("observation", mode=engine.pack.mode, floor=floor,
                mapDetected=result.get("detected"), combinedSensor=False,
                floorSource=floor_read.get("floorSource"),
                elapsedMs=(time.monotonic()-started)*1000, roundId=self._round, cycleId=self._cycle)
        self._remember_control_observation(frame, engine.pack.mode, result, floor_read)
        self._recognition_diagnostic("map-floor-observation", result.get("reason", "reason-not-provided"),
            observation=result, floorRead=floor_read, elapsedMs=(time.monotonic()-started)*1000)
        return result, floor, floor_read

    @staticmethod
    def _display_layer(engine, map_id, floor, profile, *, variant_id="variant-01"):
        from client_route_preferences import prepare_display_layer
        return prepare_display_layer(engine, map_id, floor, profile, variant_id=variant_id)


SharedLiveRuntime = LiveRuntime
