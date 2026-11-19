"""Private experimental elimination ledger; never formal identity authority.

Only a scoped immutable-observation common-coordinate report may eliminate IDs.
Historical same-round evidence never grants current pose/display authority.
Unknown fits, omitted scans, timeouts, preferences and UI payloads cannot.
"""
from dataclasses import dataclass, field
import time
from uuid import uuid4
import numpy as np


@dataclass
class PrivateRoundPool:
    scope: object
    map_ids: tuple
    remaining: tuple = field(init=False)
    locked_id: str = ""
    retain_locked_identity: bool = False
    eliminated: set = field(default_factory=set)
    observation_count: int = 0
    revision: int = 0
    pool_id: str = field(default_factory=lambda: uuid4().hex, init=False)
    recovery_revision: int = 0
    recovery_pending: bool = False

    def __post_init__(self):
        self.remaining = tuple(self.map_ids)

    def restore(self):
        self.revision += 1
        self.remaining = tuple(self.map_ids)
        self.eliminated.clear()
        self.locked_id = ""

    def observe(self, report):
        # Production identity is final within this round. Historical evidence
        # can narrow an unconfirmed pool, but cannot reopen a committed ID.
        # Current-frame pose/material validation remains a separate authority.
        if self.retain_locked_identity and self.locked_id:
            return "private-coordinate-unknown"
        before = self.remaining
        if report.get("reason") == "coordinate-or-catalogue-conflict":
            # The comparator deliberately leaves coordinateVerified false when
            # every candidate conflicts. Only its complete, explicit per-ID
            # checks prove this case; a reason string/unknown report cannot.
            checks = report.get("candidateChecks")
            evaluated = report.get("evaluatedMapIds")
            valid = (type(evaluated) is list and all(type(mid) is str for mid in evaluated)
                and len(evaluated) == len(before) and set(evaluated) == set(before)
                and type(checks) is list and len(checks) == len(before)
                and all(type(row) is dict and type(row.get("mapId")) is str
                    and row.get("explicitContradiction") is True for row in checks)
                and {row["mapId"] for row in checks} == set(before))
            if not valid:
                return "private-coordinate-unknown"
            return self._recover_contradiction()
        bad = report.get("contradictedMapIds", [])
        evaluated = report.get("evaluatedMapIds")
        valid = (report.get("coordinateVerified") is True
                 and type(evaluated) is list and all(type(mid) is str for mid in evaluated)
                 and len(evaluated) == len(before) and set(evaluated) == set(before)
                 and type(bad) is list and all(type(mid) is str for mid in bad)
                 and set(bad) <= set(before))
        if not valid:
            # Missing gate/scale is not a contradiction. A previously locked
            # ID still needs a fresh current-frame fit for display. A missing
            # fit withdraws pixels, not this same-round identity ledger.
            return "private-coordinate-unknown"
        self.observation_count += 1
        # Revision fences authority changes, not repeated observations. A
        # verified no-contradiction report must not suppress the foreground's
        # same-pool unconfirmed singleton recovery merely by arriving first.
        if bad:
            self.revision += 1
        remaining = tuple(mid for mid in before if mid not in bad)
        if not remaining or self.locked_id in bad:
            return self._recover_contradiction()
        self.remaining = remaining
        self.eliminated.update(bad)
        return "private-pool-updated"

    def _recover_contradiction(self):
        self.restore()
        self.recovery_revision = self.revision
        self.recovery_pending = True
        return "private-contradiction-recovery"

    def accept_material(self, map_id, *, coordinate_current):
        if (coordinate_current and self.remaining == (map_id,)
                and len(self.eliminated) == len(self.map_ids) - 1):
            changed = self.locked_id != map_id
            self.locked_id = map_id
            if changed:
                self.revision += 1
            return changed
        return False

    def snapshot(self):
        return dict(privateExperimental=True, privateExperimentalLocked=bool(self.locked_id),
                    privateLockMapId=self.locked_id, privatePoolMapIds=list(self.remaining),
                    privateEliminatedMapIds=sorted(self.eliminated),
                    privateObservationCount=self.observation_count,
                    privatePoolId=self.pool_id, privatePoolRevision=self.revision,
                    privateRecoveryRevision=self.recovery_revision,
                    privateRecoveryPending=self.recovery_pending)


class PrivateCoordinateObserver:
    """Fresh-frame gate and common structure, immutable packaged resources."""
    def __init__(self, engine, catalog, asset_dir, *, read_resource=None):
        from s1_gate_anchor import GateAnchorLocator
        from s1_common_coordinate_filter import CommonCoordinateFilter
        self.engine = engine
        self.locator = GateAnchorLocator(asset_dir=asset_dir, read_resource=read_resource)
        self.filter = CommonCoordinateFilter(
            lambda mid, floor: engine.qt_shared_provider("shared-structure", mid, floor),
            asset_dir / "s1_entrance_coordinate_index.json",
            mode=catalog.mode, resource_key=catalog.resource_key, read_resource=read_resource)

    def prepare(self, frame, pool_ids, floor, *, cancelled, stage=None):
        from nightmare_candidate import visible_map_components
        from single_map_structure import current_geometry, observed_corner_chains
        start = time.perf_counter()
        stage = stage or (lambda *args, **kwargs: None)
        def run_stage(name, fn):
            began = time.perf_counter()
            stage(name, "started", 0.)
            try:
                if cancelled():
                    stage(name, "cancelled", (time.perf_counter()-began)*1000)
                    return None
                value = fn()
                stage(name, "cancelled" if cancelled() else "finished", (time.perf_counter()-began)*1000)
                return value
            except BaseException as error:
                stage(name, "failed", (time.perf_counter()-began)*1000, exceptionType=type(error).__name__)
                raise
        def stopped():
            return dict(coordinateVerified=False, evaluatedMapIds=list(pool_ids),
                contradictedMapIds=[], unknownMapIds=list(pool_ids), reason="coordinate-work-obsolete")
        anchor = run_stage("gate", lambda: self.locator.detect(frame, cancelled=cancelled))
        gate_ms = (time.perf_counter() - start) * 1000
        if anchor is None or cancelled():
            return stopped()
        contexts = []
        bounds = (0, 0, frame.shape[1], frame.shape[0])
        # All islands are generated without any candidate map. The common
        # comparator accepts only islands with >=6 matching common chains,
        # rejects incompatible coordinate solutions, and never unions HUD edges.
        def extract():
            components = visible_map_components(frame, observation_bounds=bounds)
            if len(components) <= 32:
                for visible in components:
                    if cancelled():
                        break
                    context = current_geometry(frame, observation_bounds=bounds, visible_override=visible)
                    if context is not None and len(observed_corner_chains(frame, context)) >= 6:
                        contexts.append(context)
        run_stage("extract", extract)
        if cancelled():
            return stopped()
        extraction_ms = (time.perf_counter() - start) * 1000 - gate_ms
        gate = None
        if anchor.status == "located":
            gate = dict(center=list(anchor.center_px), uncertaintyPx=anchor.localization_uncertainty_px)
        resource_started = time.perf_counter()
        resources = run_stage("resources", lambda: self.filter._pool(tuple(sorted(set(pool_ids))), str(floor), cancelled))
        resource_ms = (time.perf_counter()-resource_started)*1000
        if resources is None or cancelled():
            return stopped()
        report = run_stage("compare", lambda: self.filter.evaluate(
            frame, contexts, pool_ids, str(floor), anchor=gate, cancelled=cancelled))
        if report is None or cancelled():
            return stopped()
        selected = report.get("selectedContextIndex")
        if type(selected) is int and 0 <= selected < len(contexts):
            # Current-frame working memory only; never persisted in reports.
            report["_observationContext"] = contexts[selected]
        report["gateStatus"] = anchor.status
        report["gateReason"] = anchor.reason
        report["gateMs"] = gate_ms
        report["observationMs"] = extraction_ms
        report["resourceMs"] = resource_ms
        report["totalMs"] = (time.perf_counter() - start) * 1000
        return report

    def fixed_fit(self, frame, map_id, floor, cancelled, *, report, observation_bounds=None):
        from client_live_runtime import _AlignmentHost
        from alignment_observation import rendered_profile_residual_is_verified
        if cancelled():
            return dict(success=False, reason="structure-work-obsolete")
        host = _AlignmentHost(self.engine)
        host.map_id, host.floor = map_id, str(floor)
        reference = host._shared_structure_reference("shared-structure", map_id, str(floor))
        if reference is None:
            return dict(success=False, reason="shared-reference-unavailable")
        matrix = self.filter.reference_to_client(report, map_id, str(floor))
        context = report.get("_observationContext")
        if matrix is None or context is None or context.get("visible") is None:
            return dict(success=False, reason="private-common-pose-unavailable")
        residual = host._rendered_profile_current_frame_residual(
            frame, reference.clean_mask, matrix, profile_id="shared-structure",
            visible_override=context["visible"])
        if cancelled():
            return dict(success=False, reason="structure-work-obsolete")
        if not rendered_profile_residual_is_verified(residual):
            return dict(success=False, reason="canonical-current-frame-residual-unverified")
        return dict(success=True, reason="private-shared-coordinate-fit", matrixSourceToClient=matrix,
                    selectedRouteCurrentFrameResidual=residual, coordinateSpace="shared-structure-to-client",
                    observationBounds=context["observationBounds"],
                    observationComponent=dict(context["visible"][2], bounds=context["visible"][1]),
                    routeProfileId="shared-structure", strategy="private-shared-coordinate-fit")


class LatestEvidenceQueue:
    """One running immutable-frame task plus one replaceable latest task."""
    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._pending = None
        self._thread = None
        self._closed = False
        self._latest_sequence = 0

    @property
    def busy(self):
        with self._lock:
            return self._thread is not None or self._pending is not None

    def submit(self, run, discarded=lambda: None, *, sequence=None):
        import threading
        with self._lock:
            sequence = self._latest_sequence + 1 if sequence is None else sequence
            if self._closed or sequence <= self._latest_sequence:
                rejected = (run, discarded)
                old = None
            else:
                rejected = None
                self._latest_sequence = sequence
                old, self._pending = self._pending, (run, discarded)
                if self._thread is None:
                    self._thread = threading.Thread(target=self._drain, name='s1-private-evidence', daemon=True)
                    self._thread.start()
        if old is not None:
            old[1]()
        if rejected is not None:
            rejected[1]()
        return rejected is None

    def _drain(self):
        while True:
            with self._lock:
                item, self._pending = self._pending, None
                if item is None:
                    self._thread = None
                    return
            try:
                item[0]()
            except BaseException:
                # A failed task never strands the bounded queue. Each runtime
                # task records its own exception before this cleanup boundary.
                pass

    def discard_pending(self):
        with self._lock:
            item, self._pending = self._pending, None
        if item is not None:
            item[1]()

    def shutdown(self):
        with self._lock:
            self._closed = True
        self.discard_pending()
