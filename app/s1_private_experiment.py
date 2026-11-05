"""Private experimental elimination ledger; never formal identity authority.

Only an owned current-frame common-coordinate report may eliminate IDs.
Unknown fits, omitted scans, timeouts, preferences and UI payloads cannot.
"""
from dataclasses import dataclass, field
import time
import numpy as np


@dataclass
class PrivateRoundPool:
    scope: object
    map_ids: tuple
    remaining: tuple = field(init=False)
    locked_id: str = ""
    eliminated: set = field(default_factory=set)
    observation_count: int = 0

    def __post_init__(self):
        self.remaining = tuple(self.map_ids)

    def restore(self):
        self.remaining = tuple(self.map_ids)
        self.eliminated.clear()
        self.locked_id = ""

    def observe(self, report):
        before = self.remaining
        if report.get("reason") == "coordinate-or-catalogue-conflict":
            self.restore()
            return "private-contradiction-recovery"
        bad = report.get("contradictedMapIds", [])
        valid = (report.get("coordinateVerified") is True
                 and set(report.get("evaluatedMapIds", ())) == set(before)
                 and type(bad) is list and all(type(mid) is str for mid in bad)
                 and set(bad) <= set(before))
        if not valid:
            # Missing gate/scale is not a contradiction. A previously locked
            # ID still needs its fresh current-frame fit below; only that fit's
            # failure or positive contradiction withdraws the private lock.
            return "private-coordinate-unknown"
        self.observation_count += 1
        remaining = tuple(mid for mid in before if mid not in bad)
        if not remaining or self.locked_id in bad:
            self.restore()
            return "private-contradiction-recovery"
        self.remaining = remaining
        self.eliminated.update(bad)
        return "private-pool-updated"

    def accept_material(self, map_id, *, coordinate_current):
        if (coordinate_current and self.remaining == (map_id,)
                and len(self.eliminated) == len(self.map_ids) - 1):
            changed = self.locked_id != map_id
            self.locked_id = map_id
            return changed
        return False

    def snapshot(self):
        return dict(privateExperimental=True, privateExperimentalLocked=bool(self.locked_id),
                    privateLockMapId=self.locked_id, privatePoolMapIds=list(self.remaining),
                    privateEliminatedMapIds=sorted(self.eliminated),
                    privateObservationCount=self.observation_count)


class PrivateCoordinateObserver:
    """Fresh-frame gate and common structure, immutable packaged resources."""
    def __init__(self, engine, catalog, asset_dir):
        from s1_gate_anchor import GateAnchorLocator
        from s1_common_coordinate_filter import CommonCoordinateFilter
        self.engine = engine
        self.locator = GateAnchorLocator(asset_dir=asset_dir)
        self.filter = CommonCoordinateFilter(
            lambda mid, floor: engine.qt_shared_provider("shared-structure", mid, floor),
            asset_dir / "s1_entrance_coordinate_index.json",
            mode=catalog.mode, resource_key=catalog.resource_key)

    def prepare(self, frame, pool_ids, floor, *, cancelled):
        from nightmare_candidate import visible_map_components
        from single_map_structure import current_geometry, observed_corner_chains
        start = time.perf_counter()
        anchor = self.locator.detect(frame)
        gate_ms = (time.perf_counter() - start) * 1000
        contexts = []
        bounds = (0, 0, frame.shape[1], frame.shape[0])
        # All islands are generated without any candidate map. The common
        # comparator accepts only islands with >=6 matching common chains,
        # rejects incompatible coordinate solutions, and never unions HUD edges.
        components = visible_map_components(frame, observation_bounds=bounds)
        if len(components) <= 32:
            for visible in components:
                if cancelled():
                    break
                context = current_geometry(frame, observation_bounds=bounds, visible_override=visible)
                if context is not None and len(observed_corner_chains(frame, context)) >= 6:
                    contexts.append(context)
        extraction_ms = (time.perf_counter() - start) * 1000 - gate_ms
        gate = None
        if anchor.status == "located":
            gate = dict(center=list(anchor.center_px), uncertaintyPx=anchor.localization_uncertainty_px)
        report = self.filter.evaluate(frame, contexts, pool_ids, str(floor), anchor=gate, cancelled=cancelled)
        selected = report.get("selectedContextIndex")
        if type(selected) is int and 0 <= selected < len(contexts):
            # Current-frame working memory only; never persisted in reports.
            report["_observationContext"] = contexts[selected]
        report["gateStatus"] = anchor.status
        report["gateReason"] = anchor.reason
        report["gateMs"] = gate_ms
        report["observationMs"] = extraction_ms
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
