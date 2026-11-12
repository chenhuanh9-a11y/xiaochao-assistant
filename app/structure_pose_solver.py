"""Shared current-frame structure registration and verification.

This solver owns geometry work only. Runtime owns identity, pose persistence,
display authorization and material acknowledgments.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from structure_identity_recognition import StructureIdentityRecognition

from g_cycle_observation import emit as _test_evidence


class StructurePoseSolver:
    """Only explicit resource/pose inputs needed by the unchanged shared solvers."""
    def __init__(self, engine, group=None, settings=None, *, steady=None, pose_priors=None,
                 collect_fit_diagnostics=False, usable_fit_callback=None):
        self.engine, self.pack = engine, engine.pack
        self.author_coordinate_adapters = engine.qt_adapters
        self.group = group
        self.current_alignment_candidate = None
        self.settings = dict(settings or {})
        self.events = []
        self.steady = steady
        self.pose_priors = dict(pose_priors or {})
        self.collect_fit_diagnostics = collect_fit_diagnostics
        self.diagnostic_event_counts = {}
        self.usable_fit_callback = usable_fit_callback
        self._fit_resources = None

    def _offer_usable_fit(self, fit):
        """Offer current canonical evidence; remaining peer checks still own the verdict."""
        from alignment_observation import rendered_profile_residual_is_verified
        if (self.usable_fit_callback is not None and fit.get("success")
                and rendered_profile_residual_is_verified(fit.get("selectedRouteCurrentFrameResidual"))):
            if self._fit_resources is not None:
                self._fit_resources.check_current()
            self.usable_fit_callback(fit)

    def _offer_structure_pose(self, matrix, residual, context, strategy):
        """Carry a solver's current typed evidence through the same display handoff."""
        visible = context.get("visible")
        self._offer_usable_fit(dict(success=True, matrixSourceToClient=matrix,
            selectedRouteCurrentFrameResidual=residual, strategy=strategy,
            coordinateSpace="shared-structure-to-client", routeProfileId="shared-structure",
            observationBounds=context.get("observationBounds"),
            observationComponent=dict(visible[2], bounds=visible[1]) if visible is not None else None))

    def _log(self, event, **values):
        if self.collect_fit_diagnostics:
            self.diagnostic_event_counts[event] = min(1000000, self.diagnostic_event_counts.get(event, 0)+1)
        self.events.append(dict(event=event, **values))
        del self.events[:-24]

    def _shared_structure_mode_enabled(self):
        return True

    def _open_cycle_transient_motion_active(self, map_id):
        return False

    def _try_steady_alignment(self, frame, map_id, floor, cancelled):
        if self.steady is None:
            return None
        adapter, context, current, signature = self.steady
        return adapter.try_alignment(self, frame, map_id, floor, context=context,
            is_current=current, cancelled=cancelled, display_signature=signature,
            enabled=bool(self.settings.get("idvb0059Enabled", True)))

    def _shared_structure_reference(self, profile, map_id, floor):
        if self._fit_resources is not None:
            return self._fit_resources.reference(profile, map_id, floor)
        return self.engine.qt_shared_provider(profile, map_id, floor)

    def _load_fast_structure_prepared(self, reference, *, cancelled=None):
        if self._fit_resources is not None:
            return self._fit_resources.prepared(reference, cancelled=cancelled)
        from fast_structure_alignment import load_prepared
        source = getattr(self.engine, 'qt_fast_structure_source', None)
        return (load_prepared(reference) if source is None else
                load_prepared(reference, source=source, cancelled=cancelled))

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
        # Reuse only identical numerical work within one synchronous fit.
        # Never trust a residual supplied by a solver or retain one across
        # observations. The canonical evidence wrapper still runs below.
        import numpy as np
        cache = getattr(self, "_observation_residual_cache", None)
        visible = kwargs.get("visible_override")
        transform = np.asarray(matrix)
        key = (str(map_id), str(floor), id(frame), id(reference), id(reference.validation_mask),
               transform.dtype.str, transform.shape, transform.tobytes(),
               kwargs.get("maximum_dimension", 960), id(visible))
        cached = cache.get(key) if cache is not None else None
        if cached is None:
            residual = dict(selected_route_current_frame_residual(frame, None, matrix,
                maximum_dimension=kwargs.get("maximum_dimension", 960),
                visible_override=visible, route_profile_id="wang-qingxin",
                reference_override=reference.validation_mask, shared_reference=reference))
            if cache is not None:
                # Strong references prevent identity reuse; copies isolate any
                # diagnostic additions made by individual consumers.
                if len(cache) >= 2:
                    cache.pop(next(iter(cache)))
                cache[key] = (frame, reference, visible, copy.deepcopy(residual))
        else:
            residual = copy.deepcopy(cached[3])
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

    def _fit_fixed_entry_proposal(self, frame, map_id, floor, reference, observation, cancelled, context):
        """Recheck one qualified H without rerunning its global pose search.

        Only the completed work on these exact pixels supplies pose uniqueness.
        A raw entry row never owns the shortcut, even if all its flags pass.
        The original regional, all-fragment wall and canonical residual kernels
        consume the current prepared reference; none can be supplied by a row.
        """
        from alignment_observation import rendered_profile_residual_is_verified
        from entry_pose_proposals import completed_entry_pose_candidate, qualified_entry_pose_for_reference
        from single_map_structure import (current_part, initial_wall_raster_validation,
            projected_geometry, regional_validation, _static_validation_context)
        if cancelled and cancelled():
            return dict(success=False, reason="structure-work-obsolete")
        candidate = completed_entry_pose_candidate(observation, frame, map_id, floor)
        if candidate is None:
            return None
        side = candidate.get("locatorKind") == "side" or candidate.get("entryKind") == "side"
        locator = getattr(self.engine, "side_entry_identity" if side else "entry_identity", None)
        entries = getattr(locator, "entries", ()) or ()
        matrix = qualified_entry_pose_for_reference(candidate, map_id, floor, reference,
            self.author_coordinate_adapters, sources=(entry[0] for entry in entries))
        if matrix is None or context is None:
            return None
        observed_locator = getattr(observation.engine,
            "side_entry_identity" if side else "entry_identity", None)
        observed_sources = getattr(observed_locator, "entries", ()) or ()
        if qualified_entry_pose_for_reference(candidate, map_id, floor, reference,
                self.author_coordinate_adapters, sources=(entry[0] for entry in observed_sources)) is None:
            return None
        projected = projected_geometry(frame, reference.clean_mask, matrix, context)["mask"]
        regions = regional_validation(frame, context["query"], projected,
            _context=_static_validation_context(frame, context, matrix, reference.unknown_mask))
        if not regions.get("regionalEdgeVerified"):
            return None
        walls = initial_wall_raster_validation(current_part(frame, context, "lines"),
            reference.clean_mask, (matrix[0, 0], matrix[0, 2], matrix[1, 2]),
            source_unknown=reference.unknown_mask, source_wall_mask=reference.known_boundary)
        if not walls.get("verified"):
            return None
        residual = self._rendered_profile_current_frame_residual(frame, reference.clean_mask,
            matrix, profile_id="shared-structure", visible_override=context.get("visible"))
        if cancelled and cancelled():
            return dict(success=False, reason="structure-work-obsolete")
        if not rendered_profile_residual_is_verified(residual):
            return None
        wall_evidence = dict(verified=True, variants=[dict(name="source-present", result=walls, basis=None)],
            scope="all current observed fragments, not only fitted passport planes")
        checked = dict(success=True, reason="qualified-entry-fixed-current-frame",
            matrixSourceToClient=matrix, regions=regions, entryWalls=wall_evidence, residual=residual,
            structureVariants=[dict(name="source-present", basis=None, regions=regions, walls=walls, verified=True)],
            compatibleStructureVariants=["source-present"],
            referenceChannel="typed-shared-structure-validation-mask",
            validationPolicy="fixed-current-frame-shared-structure")
        return dict(success=True, reason="qualified-entry-fixed-current-frame",
            matrixSourceToClient=matrix, coordinateSpace="shared-structure-to-client",
            routeProfileId="shared-structure", selectedRouteCurrentFrameResidual=residual,
            difficultEntryValidation=checked, inliers=len(context.get("nodes", ())))

    def prepare_structure_scan(self, frame, observation_bounds, *, fit_callback=None):
        """One scan owns raw observations; no cache survives into another frame.

        Each candidate gets a fresh context containing only source-independent
        fields. Projected masks, unknown filtering and verdicts remain private
        to that candidate. Geometry extraction stays lazy for budget/cancel.
        """
        _test_evidence("solver-input", frame)
        from nightmare_candidate import visible_map_components
        from single_map_structure import current_geometry, current_part, observed_corner_chains, marker_occluded_corner_proposals
        from fast_structure_alignment import sample_edges
        bounds = tuple(observation_bounds)
        fit = self.fit if fit_callback is None else fit_callback
        observations, components = {}, None

        def geometry(visible=None):
            key = None if visible is None else id(visible)
            if key not in observations:
                context = current_geometry(frame, observation_bounds=bounds,
                                           visible_override=visible)
                if context is not None:
                    for part in ("nodes", "edge", "observed", "lines"):
                        current_part(frame, context, part)
                    context["_observed_corner_chains"] = (context["nodes"], context["observed"],
                                                          observed_corner_chains(frame, context))
                    marker_occluded_corner_proposals(frame, context, context["_observed_corner_chains"][2])
                    edge = context["observed"]
                    context["_sampled_observation_edges"] = (edge, sample_edges(edge, 300), sample_edges(edge, 96))
                observations[key] = context
            context = observations[key]
            return dict(context) if context is not None else None

        def islands():
            nonlocal components
            if components is None:
                components = visible_map_components(frame, observation_bounds=bounds)
            return components

        entry_unspecified = object()
        def fit_one(candidate_frame, map_id, floor, cancelled, *, observation_bounds=None,
                    entry_candidate=None, entry_observation=entry_unspecified):
            if candidate_frame is not frame or tuple(observation_bounds or ()) != bounds:
                raise ValueError("structure-scan-observation-context-mismatch")
            return fit(frame, map_id, floor, cancelled,
                            observation_bounds=bounds, _scan_observation=(geometry, islands),
                            **(dict(entry_candidate=entry_candidate) if entry_candidate is not None else {}),
                            **(dict(entry_observation=entry_observation) if entry_observation is not entry_unspecified else {}))
        return fit_one

    def fit(self, frame, map_id, floor, cancelled, entry_candidate=None, *, entry_observation=None,
            observation_bounds=None, _scan_observation=None):
        from structure_pose_resources import StructureFitResources
        preceding = self._fit_resources
        resources = StructureFitResources(self.engine)
        self._fit_resources = resources
        try:
            result = self._fit_regions(frame, map_id, floor, cancelled, entry_candidate,
                entry_observation=entry_observation, observation_bounds=observation_bounds,
                _scan_observation=_scan_observation)
            if not any(word in str(result.get('reason', '')) for word in ('obsolete', 'cancelled')):
                resources.check_current()
            return result
        finally:
            self._fit_resources = preceding

    def _fit_regions(self, frame, map_id, floor, cancelled, entry_candidate=None, *, entry_observation=None,
            observation_bounds=None, _scan_observation=None):
        _test_evidence("solver-input", frame)
        entry_options = dict(entry_observation=entry_observation) if entry_observation is not None else {}
        if observation_bounds is None:
            fit = self._fit_observation(frame, map_id, floor, cancelled, entry_candidate, **entry_options)
            self._offer_usable_fit(fit)
            if fit.get("success") and (fit.get("markerOccludedWallProposal") or fit.get("markerOccludedWallObservation")):
                rejected = self._marker_recovery_peer_rejection(frame, map_id, floor, fit,
                    cancelled, None, None)
                if rejected is not None:
                    return rejected
            return fit
        # Preserve the existing result whenever the largest island has usable
        # structure (including wall-only success). Only an unsupported failure
        # can trigger the wider search for map pixels behind brighter scenery.
        import numpy as np
        from nightmare_candidate import visible_map_components
        from single_map_structure import current_geometry, observed_corner_chains, marker_occluded_corner_proposals
        original = (_scan_observation[0]() if _scan_observation is not None else
                    current_geometry(frame, observation_bounds=observation_bounds))
        initial = (self._fit_observation(frame, map_id, floor, cancelled, entry_candidate,
            observation_bounds=observation_bounds, _context=original, **entry_options) if original is not None
            else dict(success=False, reason="manual-current-geometry-unavailable"))
        initial_reason = str(initial.get("reason", ""))
        if initial.get("success") and cancelled and cancelled():
            return dict(success=False, reason="structure-work-obsolete")
        self._offer_usable_fit(initial)
        if initial.get("success") and (initial.get("markerOccludedWallProposal") or initial.get("markerOccludedWallObservation")):
            rejected = self._marker_recovery_peer_rejection(frame, map_id, floor, initial,
                cancelled, observation_bounds, _scan_observation)
            if rejected is not None:
                return rejected
        if (initial.get("success") or any(word in initial_reason for word in
                ("obsolete", "ambiguous", "budget-unresolved"))
                or original is not None and observed_corner_chains(frame, original)):
            return initial
        islands = (_scan_observation[1]() if _scan_observation is not None else
                   visible_map_components(frame, observation_bounds=observation_bounds))
        if len(islands) > 32:
            return dict(success=False, reason="manual-component-budget-unresolved",
                        componentCount=len(islands), componentBudget=32)
        reference = self._shared_structure_reference("shared-structure", map_id, floor)
        if reference is None:
            return dict(success=False, reason="shared-reference-unavailable")
        h, w = reference.clean_mask.shape
        extent = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]])
        accepted, diagnostics, evidence_contexts, unresolved = [], [], [], False
        # A clean secondary pose still has to explain a failed marker-bearing
        # primary. Evidence belongs to the observation, not the chosen seed.
        marker_peer_evidence = bool(original is not None
            and marker_occluded_corner_proposals(frame, original, []))
        for island in islands:
            if cancelled and cancelled():
                return dict(success=False, reason="structure-work-obsolete")
            if original is not None and tuple(island[1]) == tuple(original["observationBounds"]):
                diagnostics.append(dict(bounds=list(island[1]), area=island[2]["area"],
                                        success=False, reason=initial_reason))
                continue
            context = (_scan_observation[0](island) if _scan_observation is not None else
                       current_geometry(frame, observation_bounds=observation_bounds, visible_override=island))
            if context is None:
                continue
            measured_chains = observed_corner_chains(frame, context)
            if measured_chains:
                evidence_contexts.append(context)
            elif marker_occluded_corner_proposals(frame, context, measured_chains):
                marker_peer_evidence = True
            fit = self._fit_observation(frame, map_id, floor, cancelled, entry_candidate,
                observation_bounds=observation_bounds, _context=context, **entry_options)
            reason = str(fit.get("reason", ""))
            diagnostics.append(dict(bounds=list(island[1]), area=island[2]["area"],
                                    success=bool(fit.get("success")), reason=reason))
            if "obsolete" in reason:
                return dict(success=False, reason="structure-work-obsolete")
            if "ambiguous" in reason or "budget-unresolved" in reason:
                unresolved = True
            if fit.get("success"):
                self._offer_usable_fit(fit)
                matrix = np.asarray(fit["matrixSourceToClient"])
                if not any(np.linalg.norm((extent @ (matrix - np.asarray(old["matrixSourceToClient"])).T)[:, :2], axis=1).max() <= 3
                           for old in accepted):
                    accepted.append(fit)
        if cancelled and cancelled():
            return dict(success=False, reason="structure-work-obsolete")
        if unresolved or len(accepted) > 1:
            return dict(success=False, reason="manual-component-pose-ambiguous",
                        componentFits=diagnostics, acceptedPoseCount=len(accepted), unresolved=unresolved)
        if len(accepted) == 1:
            fit = accepted[0]
            # Do not explain one convenient small island while contradicting
            # other independently observed structure. A conflict fails closed.
            for context in evidence_contexts:
                if cancelled and cancelled():
                    return dict(success=False, reason="structure-work-obsolete")
                if tuple(context["observationBounds"]) == tuple(fit.get("observationBounds") or ()):
                    continue
                consistent = self._component_pose_consistent(frame, reference, fit["matrixSourceToClient"], context)
                if cancelled and cancelled():
                    return dict(success=False, reason="structure-work-obsolete")
                if not consistent:
                    return dict(success=False, reason="manual-components-inconsistent",
                                componentFits=diagnostics, conflictingBounds=list(context["observationBounds"]))
            if cancelled and cancelled():
                return dict(success=False, reason="structure-work-obsolete")
            if marker_peer_evidence or fit.get("markerOccludedWallProposal") or fit.get("markerOccludedWallObservation"):
                rejected = self._marker_recovery_peer_rejection(frame, map_id, floor, fit,
                    cancelled, observation_bounds, _scan_observation)
                if rejected is not None:
                    return rejected
            return dict(fit, componentFits=diagnostics, acceptedPoseCount=1, manualComponentFallback=True)
        return dict(success=False, reason="manual-components-current-geometry-rejected" if diagnostics
                    else "manual-current-geometry-unavailable", componentFits=diagnostics)

    def _marker_recovery_peer_rejection(self, frame, map_id, floor, fit, cancelled, bounds, scan):
        """New marker-derived poses must explain independent peer structure.

        A few HUD fragments or unknown fog have no independent geometry and
        cannot veto a pose. Reuse the wall fitter's two separated planes on
        each axis as the evidence floor, then retain the strict validators.
        This checks the original primary too when a secondary was recovered.
        """
        from nightmare_candidate import visible_map_components
        from single_map_structure import (current_geometry, current_part,
            visible_wall_segments, _static_validation_context)
        import numpy as np
        islands = scan[1]() if scan is not None else visible_map_components(frame, observation_bounds=bounds)
        selected_bounds = fit.get("observationBounds")
        if bounds is None:
            # The implicit solver keeps its original largest-island search,
            # prior/steady order and default ROI. Do not broaden it to S1's
            # full-frame domain merely to validate a marker-derived success.
            h, w = frame.shape[:2]
            bounds = (int(w*.16), int(h*.22), int(w*.76), int(h*.80))
            if selected_bounds is None and islands:
                selected_bounds = islands[0][1]
        if len(islands) > 32:
            return dict(success=False, reason="manual-component-budget-unresolved",
                        componentCount=len(islands), componentBudget=32)
        reference = self._shared_structure_reference("shared-structure", map_id, floor)
        for island in islands:
            if cancelled and cancelled():
                return dict(success=False, reason="structure-work-obsolete")
            if tuple(island[1]) == tuple(selected_bounds or ()):
                continue
            # Enumeration adds an 8px image halo. It must not introduce walls
            # outside the caller's domain; the clipped edge remains unknown.
            x0, y0, x1, y1 = island[1]
            clipped = (max(x0, bounds[0]), max(y0, bounds[1]),
                       min(x1, bounds[2]), min(y1, bounds[3]))
            if clipped != tuple(island[1]):
                x0, y0, x1, y1 = clipped
                if x0 >= x1 or y0 >= y1:
                    continue
                visible = (frame[y0:y1, x0:x1], clipped, island[2])
                context = current_geometry(frame, observation_bounds=bounds, visible_override=visible)
            else:
                context = (scan[0](island) if scan is not None else
                           current_geometry(frame, observation_bounds=bounds, visible_override=island))
            if context is None:
                continue
            measured = _static_validation_context(frame, context, np.asarray(fit["matrixSourceToClient"]),
                getattr(reference, "unknown_mask", None))
            lines = (current_part(frame, context, "lines") if measured is context else
                     visible_wall_segments(context["query"], current_part(frame, measured, "observed"),
                                           nodes=current_part(frame, context, "nodes")))
            independent = True
            for axis in (0, 1):
                planes = []
                for line in lines:
                    if line["axis"] == axis and not any(abs(line["normal"]-old) <= 3 for old in planes):
                        planes.append(line["normal"])
                if len(planes) < 2 or max(planes)-min(planes) < 24:
                    independent = False
                    break
            if not independent:
                continue
            consistent = self._component_pose_consistent(frame, reference, fit["matrixSourceToClient"], context)
            if cancelled and cancelled():
                return dict(success=False, reason="structure-work-obsolete")
            if not consistent:
                return dict(success=False, reason="manual-components-inconsistent",
                            conflictingBounds=list(context["observationBounds"]))
        if cancelled and cancelled():
            return dict(success=False, reason="structure-work-obsolete")
        return None

    def _component_pose_consistent(self, frame, reference, matrix, context):
        from single_map_structure import projected_geometry, regional_validation, _static_validation_context
        from alignment_observation import rendered_profile_residual_is_verified
        import numpy as np
        projected = projected_geometry(frame, reference.clean_mask, np.asarray(matrix), context)["mask"]
        validation_context = _static_validation_context(frame, context, np.asarray(matrix),
            getattr(reference, "unknown_mask", None))
        if not regional_validation(frame, context["query"], projected, _context=validation_context).get("regionalEdgeVerified"):
            return False
        residual = self._rendered_profile_current_frame_residual(frame, reference.clean_mask, matrix,
            profile_id="shared-structure", visible_override=context.get("visible"))
        return rendered_profile_residual_is_verified(residual)

    def _fit_observation(self, frame, map_id, floor, cancelled, entry_candidate=None, *, entry_observation=None,
            observation_bounds=None, _context=None):
        previous = getattr(self, "_observation_residual_cache", None)
        self._observation_residual_cache = {}
        try:
            return self._fit_observation_uncached(frame, map_id, floor, cancelled, entry_candidate,
                entry_observation=entry_observation, observation_bounds=observation_bounds, _context=_context)
        finally:
            self._observation_residual_cache = previous

    def _fit_observation_uncached(self, frame, map_id, floor, cancelled, entry_candidate=None, *, entry_observation=None,
            observation_bounds=None, _context=None):
        from alignment_observation import rendered_profile_residual_is_verified
        from fast_structure_alignment import try_alignment
        from single_map_structure import current_geometry, solve, solve_initial_structure
        self.map_id, self.floor = map_id, str(floor)
        reference = self._shared_structure_reference("shared-structure", map_id, floor)
        if reference is None:
            return dict(success=False, reason="shared-reference-unavailable")
        explicit_domain = observation_bounds is not None
        context = (_context if _context is not None else current_geometry(frame, observation_bounds=observation_bounds)) if explicit_domain else None
        if explicit_domain and context is None:
            return dict(success=False, reason="manual-current-geometry-unavailable")
        # Original structure-fit order: default 0059 prior before entry/fast.
        if not explicit_domain:
            steady = self._try_steady_alignment(frame, map_id, floor, cancelled)
            if steady is not None:
                return steady
        # Recheck a complete entry proposal at fixed H before searching again.
        # Rejection retains the original fast/recovery/entry/initial order.
        context = context if explicit_domain else current_geometry(frame)
        hint = self.pose_priors.get((str(map_id), str(floor)))
        options = {"pose_prior": hint} if hint is not None else {}
        fit = self._fit_fixed_entry_proposal(frame, map_id, floor, reference,
            entry_observation, cancelled, context)
        strategy = "qualified-entry-fixed-current-frame"
        if fit is None:
            fit = try_alignment(self, frame, map_id, floor, cancelled=cancelled,
                                context=context, **options)
            strategy = "shared-fast-alignment"
        identity_profile = (self.engine.identity_profile_for_map(map_id)
            if callable(getattr(self.engine, "identity_profile_for_map", None))
            else getattr(self.engine, "identity_profile_id", None))
        entry_first = (identity_profile == "shared-structure"
            and callable(getattr(getattr(self.engine, "side_entry_identity", None), "rank_entry", None)))
        if (not explicit_domain and fit is None and entry_first and entry_candidate is None and self.group is not None
                and self.group.map_id == map_id and str(floor) in self.group.verified_floors
                and self.group.route_profile_id == "shared-structure"):
            # Legacy confirmed same-floor recovery is translation-only, even
            # on a miss. Do not restart unknown-scale initial estimation.
            fit = solve(self, frame, map_id, floor, "shared-structure", reference.clean_mask,
                        cancelled=cancelled, _context=context)
            strategy = "shared-established-group-recovery"
        def recover_existing_group():
            if self.group is not None and self.group.verified_floors and str(floor) in self.group.relations:
                recovery = solve(self, frame, map_id, floor, "shared-structure", reference.clean_mask,
                                 cancelled=cancelled, _context=context)
                if (recovery.get("success") or recovery.get("reason") != "structure-current-geometry-failed"
                        or recovery.get("sharedProjectionGeometryInsufficient")):
                    return recovery
            return None
        if not explicit_domain and fit is None and (not entry_first or (self.group is not None and str(floor) not in self.group.verified_floors)):
            fit = recover_existing_group()
            strategy = "shared-established-group-recovery"
        if not explicit_domain and fit is None:
            fit = self._reverify_entry(frame, map_id, floor, reference, entry_candidate, cancelled, context=context)
            strategy = "verified-entry-current-frame"
        if not explicit_domain and fit is None and entry_first:
            fit = recover_existing_group()
            strategy = "shared-established-group-recovery"
        if not explicit_domain and fit is None:
            from registered_floor_pose_recovery import recover_registered_floor
            fit = recover_registered_floor(self, frame, map_id, floor, reference, cancelled)
            strategy = "shared-registered-floor-recovery"
        if fit is None:
            from entry_pose_proposals import entry_pose_proposals_for_reference
            proposals = entry_pose_proposals_for_reference(entry_candidate, map_id, floor,
                reference, self.author_coordinate_adapters)
            fit = self._alignment_v2_independent_member_fit(frame, reference.clean_mask, map_id, floor,
                "shared-structure", cancelled=cancelled, context=context,
                **(dict(entry_pose_proposals=proposals) if proposals else {}))
            strategy = "shared-initial-structure"
        def record_fit(value):
            if self.collect_fit_diagnostics:
                try:
                    from s1_fit_diagnostics import observation_summary
                    self._log('s1-fit-observation', mapId=str(map_id), floor=str(floor),
                        **observation_summary(frame, context, value, strategy, reference))
                except Exception:
                    pass  # Optional evidence cannot change the actual fit verdict.
            return value
        if not fit.get("success"):
            return record_fit(fit)
        # Independently wrap/verify the final matrix in its actual typed canvas,
        # exactly as the production canonical consumer does after any sensor.
        residual = self._rendered_profile_current_frame_residual(
            frame, reference.clean_mask, fit["matrixSourceToClient"], profile_id="shared-structure",
            visible_override=context.get("visible") if context is not None else None)
        if not rendered_profile_residual_is_verified(residual):
            return record_fit(dict(success=False, reason="canonical-current-frame-residual-unverified", residual=residual))
        from single_map_structure import observed_corner_chains, marker_occluded_corner_proposals
        marker_wall_observation = False
        if context is not None and (explicit_domain or strategy in (
                "shared-fast-alignment", "qualified-entry-fixed-current-frame")):
            if fit.get("markerOccludedWallProposal"):
                marker_wall_observation = not (fit.get("diagnostics") or {}).get("queryChains")
            elif (fit.get("diagnostics") or {}).get("queryChains") is None:
                measured = observed_corner_chains(frame, context)
                marker_wall_observation = bool(not measured
                    and marker_occluded_corner_proposals(frame, context, measured))
        return record_fit(dict(fit, selectedRouteCurrentFrameResidual=residual,
                    markerOccludedWallObservation=marker_wall_observation,
                    observationBounds=context["observationBounds"] if explicit_domain else None,
                    observationComponent=dict(context["visible"][2], bounds=context["visible"][1]) if explicit_domain else None,
                    coordinateSpace="shared-structure-to-client", routeProfileId="shared-structure",
                    strategy=strategy))


class PreparedStructurePoseSolver(StructurePoseSolver):
    """The same numerical orchestration over previously prepared RAM inputs.

    Resource preparation and runtime result ownership are external. Progress
    callbacks receive numerical evidence; they do not prepare resources or
    confer identity, persistent pose or display authority.
    """

    def __init__(self, inputs, group=None, settings=None, *, steady_input=None,
                 pose_priors=None, collect_fit_diagnostics=False, usable_fit_callback=None):
        self.inputs = inputs
        self.steady_input = steady_input
        super().__init__(inputs.new_geometry_engine(), group, settings,
            pose_priors=pose_priors, collect_fit_diagnostics=collect_fit_diagnostics,
            usable_fit_callback=usable_fit_callback)

    def _try_steady_alignment(self, frame, map_id, floor, cancelled):
        if self.steady_input is None or not self.settings.get("idvb0059Enabled", True):
            return None
        return self.steady_input.try_alignment(self, frame, map_id, floor, cancelled=cancelled)

    def _shared_structure_reference(self, profile, map_id, floor):
        return self.inputs.reference(profile, map_id, floor)

    def _load_fast_structure_prepared(self, reference, *, cancelled=None):
        return self.inputs.prepared(reference, cancelled=cancelled)

    def fit(self, frame, map_id, floor, cancelled, entry_candidate=None, *,
            entry_observation=None, observation_bounds=None, _scan_observation=None):
        self.inputs.require_scope(map_id, floor)
        if cancelled is not None and cancelled():
            return dict(success=False, reason="structure-work-obsolete")
        return self._fit_regions(frame, map_id, floor, cancelled, entry_candidate,
            entry_observation=entry_observation, observation_bounds=observation_bounds,
            _scan_observation=_scan_observation)


def validate_fixed_structure_identity_pose(inputs, snapshot, proposal, *, frame_key,
        shape_snapshot_id, resource_binding, cancelled=None, deadline=None):
    """Check one direct proposal at its unchanged H against current pixels.

    This is a geometry-only consumer of prepared inputs. It performs no fit,
    resource read, identity commit or comparison with another pose family.
    Frame/resource metadata comes from the producing computation, never from a
    successful residual. Its caller still owns source/observation qualification.
    """
    import numpy as np
    from alignment_observation import rendered_profile_residual_is_verified
    from entry_evidence_work import FrameSnapshot
    from structure_identity_index import NumericPoseUnresolved, PoseProposal
    from structure_pose_inputs import PreparedStructurePoseInputs

    if not isinstance(inputs, PreparedStructurePoseInputs) or not isinstance(snapshot, FrameSnapshot):
        raise TypeError("prepared-pose-inputs-and-frame-snapshot-required")
    if not isinstance(proposal, PoseProposal):
        raise TypeError("structure-identity-pose-proposal-required")

    def failure(reason, **values):
        return dict(success=False, reason=reason, mapId=proposal.key.map_id,
                    floor=proposal.key.floor, frameKey=snapshot.frame_key,
                    shapeSnapshotId=snapshot.sha256, **values)

    def stopped():
        if cancelled is not None and cancelled():
            return "structure-work-obsolete"
        if deadline is not None and time.perf_counter() >= deadline:
            return "structure-work-time-budget-exhausted"
        return None

    reason = stopped()
    if reason:
        return failure(reason)
    if frame_key != snapshot.frame_key or shape_snapshot_id != snapshot.sha256:
        return failure("direct-proposal-frame-mismatch")
    if resource_binding != inputs.binding:
        return failure("direct-proposal-resource-binding-mismatch")
    key = proposal.key
    if (key.mode != inputs.binding.season + ":" + inputs.binding.mode
            or key.map_id != inputs.map_id or key.floor not in inputs.floors
            or (key.map_id, key.floor) not in inputs.references):
        return failure("direct-proposal-prepared-scope-mismatch")
    # Each invocation has its own host. Candidate fitting cannot change the
    # direct consumer's map/floor or its mutable residual cache/resource member.
    host = PreparedStructurePoseSolver(inputs)
    reference = host._shared_structure_reference("shared-structure", key.map_id, key.floor)
    if reference is None:
        return failure("shared-reference-unavailable")
    binding = reference.metadata.get("binding", {})
    if (binding.get("mapId") != key.map_id or str(binding.get("floor")) != key.floor
            or binding.get("variant", "variant-01") != key.variant):
        return failure("direct-proposal-reference-binding-mismatch")
    # Compare the producer's existing FileRef/metadata serialization with the
    # actual prepared reference. Path.relative_to is lexical; this boundary
    # must never resolve/stat a path or consult the live manifest/reader.
    import json
    from imported_resource_packages import FileRef
    from structure_identity_resources import _binding_text
    try:
        declared = json.loads(proposal.resource_binding)
        component = inputs.binding.component_ref
        reference_resource = FileRef(component,
            reference.path.relative_to(reference.resource_root).as_posix())
        # The preparation owner retained this FileRef from the initial actual
        # payload read. A proposal cannot choose its own expected resource.
        prepared_resource = inputs.preparation_resources[(key.map_id, key.floor)]
        expected = json.loads(_binding_text(reference_resource, prepared_resource, reference.metadata))
        if declared != expected:
            return failure("direct-proposal-source-binding-mismatch")
    except (ValueError, TypeError, KeyError, AttributeError):
        return failure("direct-proposal-source-binding-mismatch")
    try:
        original_matrix = proposal.matrix_source_to_query
        matrix = np.asarray(original_matrix, dtype=np.float64)
    except (ValueError, TypeError, OverflowError, NumericPoseUnresolved):
        return failure("direct-proposal-matrix-unresolved")
    if (matrix.shape != (3, 3) or not np.isfinite(matrix).all() or matrix[0, 0] <= 0
            or matrix[1, 1] != matrix[0, 0] or matrix[0, 1] != 0 or matrix[1, 0] != 0
            or not np.array_equal(matrix[2], (0., 0., 1.))):
        return failure("direct-proposal-matrix-invalid")
    reason = stopped()
    if reason:
        return failure(reason)
    residual, context = host._canonical_geometry_current_frame_residual(snapshot.pixels,
        key.map_id, key.floor, "shared-structure", original_matrix, reference.clean_mask)
    reason = stopped()
    if reason:
        return failure(reason, residual=residual, matrixSourceToClient=original_matrix)
    if not rendered_profile_residual_is_verified(residual):
        return failure("canonical-current-frame-residual-unverified", residual=residual,
                       matrixSourceToClient=original_matrix)
    return dict(success=True, mapId=key.map_id, floor=key.floor,
        frameKey=snapshot.frame_key, shapeSnapshotId=snapshot.sha256,
        matrixSourceToClient=original_matrix, selectedRouteCurrentFrameResidual=residual,
        coordinateSpace="shared-structure-to-client", routeProfileId="shared-structure",
        coordinatePackId=context["coordinatePackId"], strategy="direct-structure-fixed-proposal")


@dataclass(frozen=True)
class FixedStructureIdentityValidation:
    """Complete recognition plus one geometry result per original B family."""
    recognition: StructureIdentityRecognition
    pose_checks: tuple[dict, ...]


def validate_fixed_structure_identity_poses(inputs, snapshot, recognition, *,
        shape_snapshot_id, resource_binding, cancelled=None, deadline=None):
    """Retain the full recognition and every B family in its original order.

    An unresolved sibling remains present even if another family has verified
    B and canonical geometry. Neither list length nor a successful pose grants
    identity uniqueness; the original recognition retains its full verdict.
    """
    from structure_identity_recognition import StructureIdentityRecognition
    if not isinstance(recognition, StructureIdentityRecognition):
        raise TypeError("structure-identity-recognition-required")
    checks = []
    for evidence in recognition.pose_checks:
        proposal, held_out = evidence.proposal, evidence.held_out
        if held_out.verified:
            fit = validate_fixed_structure_identity_pose(inputs, snapshot, proposal,
                frame_key=recognition.frame_key, shape_snapshot_id=shape_snapshot_id,
                resource_binding=resource_binding, cancelled=cancelled, deadline=deadline)
        else:
            # B has not validated this family, so canonical geometry must not
            # silently turn it into a contender or erase its unresolved state.
            fit = dict(success=False, reason="direct-heldout-" + held_out.status,
                mapId=proposal.key.map_id, floor=proposal.key.floor,
                frameKey=recognition.frame_key, shapeSnapshotId=shape_snapshot_id)
        checks.append(dict(fit, heldOutStatus=held_out.status, heldOutReason=held_out.reason,
                           qualificationGaps=list(recognition.qualification_gaps)))
    return FixedStructureIdentityValidation(recognition, tuple(checks))
