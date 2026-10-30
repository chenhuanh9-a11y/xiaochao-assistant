"""Shared original V2 group ownership/validation commit; UI supplies pure adapters."""
import time
import numpy as np
import cv2
from round_layer_group import RoundLayerGroup, FLOORS as ROUND_LAYER_FLOORS
from alignment_core_v2 import RoundPose
DEFAULT_ROUTE_PROFILE_ID = "wang-qingxin"
from map_observation import map_reuse_evidence
from nightmare_candidate import visible_map_component, live_stair_screen_candidates
from alignment_observation import rendered_profile_residual_is_verified

SHARED_STRUCTURE_PROFILE_ID = "shared-structure"

def commit_alignment_v2_round_pose(
        self,
        map_id: str,
        floor: str,
        overlay: np.ndarray,
        frame: np.ndarray,
        bounds,
        *,
        render_matrix: np.ndarray,
        canonical_matrix: np.ndarray,
        route_profile_id: str,
        source: str,
        residual: dict | None,
        component_override: dict | None = None,
        normalize_route_profile_id, display_environment_signature, map_anchor_focus_point,
    ) -> bool:
        """Commit V2's authorized root without entering a legacy writer."""
        if not self._native_similarity_resources_ready():
            return False
        if (
            map_id not in getattr(getattr(self, "pack", None), "by_id", {})
            or floor not in ROUND_LAYER_FLOORS
            or not isinstance(overlay, np.ndarray)
            or not isinstance(frame, np.ndarray)
        ):
            return False
        if self._open_cycle_transient_motion_active(map_id):
            return False
        render = np.asarray(render_matrix, dtype=np.float64)
        canonical = np.asarray(canonical_matrix, dtype=np.float64)
        if (
            render.shape != (3, 3) or canonical.shape != (3, 3)
            or not np.isfinite(render).all()
            or not np.isfinite(canonical).all()
        ):
            return False
        profile = normalize_route_profile_id(route_profile_id)
        group = self._active_round_layer_group(map_id, profile)
        created = group is None
        if (group is not None and floor not in group.relations
            and rendered_profile_residual_is_verified(residual)):
            # No static inter-floor transform exists. A fully checked independent
            # floor fit can become the sole new root; it cannot invent a relation
            # to the previous floor or authorize projecting that floor on return.
            created = True
        if created:
            group = RoundLayerGroup(
                map_id=map_id, route_profile_id=profile,
                anchor_floor=floor, root_pose=canonical,
                cycle_id=getattr(self, "active_map_cycle_id", None),
                source=source, pose_origin=(
                    "front-door" if source.endswith("door-current-frame-v2")
                    else "generic"
                ),
            )
            self.round_layer_group = group
            self._prime_round_layer_group_relations(map_id, floor)
        elif floor not in group.relations:
            self._prime_round_layer_group_relations(map_id, group.anchor_floor)
        if floor not in group.relations:
            return False
        if not created:
            result = group.adopt_floor_canonical_pose(
                floor, canonical, source=source,
                cycle_id=getattr(self, "active_map_cycle_id", None),
            )
            if not result.get("success"):
                return False
        evidence = dict(residual or {})
        evidence.setdefault("verified", True)
        source_structure_verified=bool(
            evidence.get('poseVerifiedAgainstSourceStructure') is True
            and evidence.get('validationBasis')=='source-structure-heldout-current-frame')
        evidence.setdefault("poseVerifiedAgainstCanonicalGeometry", not source_structure_verified)
        evidence.setdefault("verificationSource", "alignment-v2")
        evidence.setdefault("singlePoseOwner", True)
        visible = None
        if isinstance(component_override, dict):
            component_bounds = component_override.get('bounds')
            if isinstance(component_bounds, (tuple, list)) and len(component_bounds) == 4:
                x0, y0, x1, y1 = map(int, component_bounds)
                if 0 <= x0 < x1 <= frame.shape[1] and 0 <= y0 < y1 <= frame.shape[0]:
                    visible = (frame[y0:y1, x0:x1], component_bounds, component_override)
        if visible is None:
            visible = visible_map_component(frame)
        extracted = map_reuse_evidence(frame, visible_override=visible)
        player_author = None
        if visible is not None:
            marker_component = dict(visible[2], bounds=visible[1])
            marker_point, marker_detected = map_anchor_focus_point(
                frame, marker_component, return_detection=True)
            if marker_detected:
                try:
                    player_author = (np.linalg.inv(render) @ np.asarray(
                        [marker_point[0], marker_point[1], 1.0], dtype=np.float64))[:2]
                except np.linalg.LinAlgError:
                    pass
        tracking = {
            "component": extracted.get("component"),
            "componentShape": extracted.get("componentShape"),
            "bounds": tuple(bounds) if bounds else None,
            "translationMask": extracted.get("translationMask"),
            "translationMaskMatrix": render.tolist(),
            "translationAnchorHistory": [],
            "layoutAnchorMask": extracted.get("translationMask"),
            "layoutAnchorMatrix": render.tolist(),
            "poseVerifiedAgainstRenderedProfile": bool(
                evidence.get("poseVerifiedAgainstRenderedProfile") is True
            ),
            "poseVerifiedAgainstCanonicalGeometry": bool(evidence.get("poseVerifiedAgainstCanonicalGeometry") is True),
            "poseVerifiedAgainstSourceStructure":source_structure_verified,
            "poseVerifiedAgainstLiveGeometry": bool(source_structure_verified or evidence.get('poseVerifiedAgainstCanonicalGeometry')),
            "renderedProfileResidual": evidence,
            "poseOrigin": str(group.pose_origin),
            "liveStairCandidates": live_stair_screen_candidates(frame, visible_override=visible),
            "verifiedPlayerAuthorPoint": (
                player_author.tolist() if player_author is not None else None
            ),
            "verifiedPlayerSourceFrameGeneration": (
                int(getattr(self, 'current_frame_generation', 0))
                if player_author is not None else None
            ),
            "verifiedPlayerSourceCycleId": (
                getattr(self, 'active_map_cycle_id', None)
                if player_author is not None else None
            ),
            "verifiedPlayerCommittedMonotonicAt": (
                time.perf_counter() if player_author is not None else None
            ),
        }
        if self._shared_structure_mode_enabled():
            # Each floor may display a different fallback basis. Derive the
            # bridge from the matrices actually committed, not group metadata.
            shared_basis = self.author_coordinate_adapters.matrix_profile_to_canonical(
                SHARED_STRUCTURE_PROFILE_ID, map_id, floor)
            if shared_basis is not None:
                try:
                    bridge = np.linalg.solve(canonical @ np.asarray(shared_basis), render)
                    if bridge.shape == (3, 3) and np.isfinite(bridge).all():
                        tracking['routeDisplayBridge'] = bridge.tolist()
                except (ValueError, TypeError, np.linalg.LinAlgError):
                    pass
        group.set_tracking(
            floor, tracking, cycle_id=getattr(self, "active_map_cycle_id", None),
        )
        group.mark_displayed(
            floor, overlay, provisional=False,
            render_profile_verified=bool(
                evidence.get("poseVerifiedAgainstRenderedProfile") is True
            ),
            canonical_geometry_verified=bool(evidence.get("poseVerifiedAgainstCanonicalGeometry") is True),
            source_structure_verified=source_structure_verified,
        )
        group.pose_client_size = (int(frame.shape[1]), int(frame.shape[0]))
        group.pose_display_signature = display_environment_signature(
            getattr(self, "display_environment", None),
        )
        self._log(
            "alignment-v2-round-pose-committed",
            cycleId=getattr(self, "active_map_cycle_id", None),
            mapId=map_id, floor=floor, source=source,
            created=created, groupGeneration=group.generation,
            movedLayerCount=len(group.relations), singlePoseOwner=True,
            legacyRoundLayerWriter=False,
        )
        return True

def prime_round_layer_group_relations(
        self,
        map_id: str,
        source_floor: str,
        source_profile: dict | None = None,
        *, legacy_relations=None,
    ) -> dict[str, bool]:
        """Build the immutable three-layer relation graph for this round.

        B1 and F1 are authored as one registered layer stack.  F1 and F2 use
        the reviewed canonical coordinate adapter.  These relations never
        contain live client coordinates; only ``group.root_pose`` may move.
        """
        group = self._active_round_layer_group(map_id)
        if group is None or source_floor != group.anchor_floor:
            return {floor: False for floor in ROUND_LAYER_FLOORS}
        adapters = getattr(self, "author_coordinate_adapters", None)
        graph: dict[str, list[tuple[str, np.ndarray, dict]]] = {
            floor: [] for floor in ROUND_LAYER_FLOORS
        }

        def add_edge(
            from_floor: str,
            to_floor: str,
            relation,
            metadata: dict,
        ) -> None:
            matrix = np.asarray(relation, dtype=np.float64)
            if (
                matrix.shape == (3, 3)
                and np.isfinite(matrix).all()
                and abs(float(np.linalg.det(matrix[:2, :2]))) >= 1e-8
            ):
                graph[from_floor].append((to_floor, matrix, dict(metadata)))

        # Preserve relations learned from a current-frame-verified target fit.
        # They are immutable coordinate adapters inside this round, not a
        # second mutable pose.  In particular, never assume B1 == F1: their
        # author canvases have different scale and origin.
        for known_floor, known_relation in tuple(group.relations.items()):
            if known_floor == group.anchor_floor:
                continue
            metadata = dict(group.relation_metadata.get(known_floor) or {})
            metadata.update({
                "singlePoseOwner": True,
                "roundGroupSharedLinear": True,
            })
            add_edge(group.anchor_floor, known_floor, known_relation, metadata)
            try:
                inverse = np.linalg.inv(known_relation)
            except np.linalg.LinAlgError:
                inverse = None
            if inverse is not None:
                add_edge(known_floor, group.anchor_floor, inverse, metadata)
        # This legacy edge is measured in author C canvases, not the independent
        # game-native floor canvases. Current-frame learned relations stay above.
        b1_to_f1 = None
        if not self._shared_structure_mode_enabled():
            b1_to_f1 = (legacy_relations or {}).get(map_id)
        if b1_to_f1 is not None:
            b1_metadata = {
                "transferSource": "candidate-b1-f1-static-layer-relation",
                "profileCurrentFrameSearchSeed": True,
                "requiresCurrentFrameDisplayValidation": True,
                "directProjectionAllowed": False,
                "runtimeVerified": False,
                "singlePoseOwner": True,
                "roundGroupSharedLinear": True,
            }
            add_edge("1", "B1", b1_to_f1, b1_metadata)
            try:
                f1_to_b1 = np.linalg.inv(b1_to_f1)
            except np.linalg.LinAlgError:
                f1_to_b1 = None
            if f1_to_b1 is not None:
                add_edge("B1", "1", f1_to_b1, b1_metadata)
        if adapters is not None:
            for from_floor, to_floor in (("1", "2"), ("2", "1")):
                seed = adapters.canonical_cross_floor_search_seed(
                    map_id, from_floor, to_floor, np.eye(3),
                    authorization_profile_id=group.route_profile_id,
                )
                relation = (seed or {}).get("targetToSourceMatrix")
                metadata = {
                    key: value for key, value in dict(seed or {}).items()
                    if not str(key).endswith("ToClient")
                }
                metadata.update({
                    "roundGroupSharedLinear": True,
                    "singlePoseOwner": True,
                })
                add_edge(from_floor, to_floor, relation, metadata)

        relations = {source_floor: np.eye(3, dtype=np.float64)}
        relation_metadata: dict[str, dict] = {
            source_floor: {
                "fromFloor": source_floor,
                "toFloor": source_floor,
                "transferSource": "round-group-anchor",
                "singlePoseOwner": True,
            }
        }
        queue_floors = [source_floor]
        while queue_floors:
            current = queue_floors.pop(0)
            for target_floor, edge, metadata in graph[current]:
                if target_floor in relations:
                    continue
                relations[target_floor] = relations[current] @ edge
                relation_metadata[target_floor] = {
                    **metadata,
                    "fromFloor": source_floor,
                    "viaFloor": current,
                    "toFloor": target_floor,
                    "singlePoseOwner": True,
                }
                queue_floors.append(target_floor)
        for target_floor, relation in relations.items():
            group.set_relation(
                target_floor, relation,
                relation_metadata.get(target_floor),
            )
        availability = {
            floor: floor in group.relations for floor in ROUND_LAYER_FLOORS
        }
        self._log(
            "round-layer-group-relations-primed",
            cycleId=getattr(self, "active_map_cycle_id", None),
            mapId=map_id, anchorFloor=source_floor,
            availableFloors=[floor for floor, ok in availability.items() if ok],
            unavailableFloors=[floor for floor, ok in availability.items() if not ok],
            oneRuntimePose=True,
        )
        return availability


def map_anchor_focus_point(
    frame: np.ndarray,
    component: dict,
    *, return_detection: bool = False,
) -> np.ndarray | tuple[np.ndarray, bool]:
    """Prefer the live player marker, otherwise the visible-map centre.

    The yellow player marker is normally close to the auto-centred viewport
    focus. Yellow room borders and door ticks are rejected by compact-component
    geometry; if the marker is unavailable or ambiguous, the current visible
    map centre remains a safe ranking hint only. This point never authorizes a
    translation by itself.
    """
    fallback = np.asarray(component.get("center", [
        frame.shape[1] / 2.0, frame.shape[0] / 2.0,
    ]), dtype=np.float32).reshape(2)
    bounds = component.get("bounds")
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 4:
        return (fallback, False) if return_detection else fallback
    x0, y0, x1, y1 = (int(value) for value in bounds)
    patch = frame[y0:y1, x0:x1]
    if patch.size == 0 or patch.ndim != 3:
        return (fallback, False) if return_detection else fallback
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(
        hsv,
        np.asarray([15, 105, 115], dtype=np.uint8),
        np.asarray([42, 255, 255], dtype=np.uint8),
    )
    yellow = cv2.morphologyEx(
        yellow, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8),
    )
    count, _labels, stats, centers = cv2.connectedComponentsWithStats(
        yellow, 8,
    )
    candidates: list[tuple[float, np.ndarray]] = []
    scale = max(frame.shape[:2]) / 2560.0
    for index in range(1, count):
        _x, _y, width, height, area = (
            int(value) for value in stats[index]
        )
        if not (
            max(3, round(5 * scale)) <= area <= max(120, round(1000 * scale * scale))
            and width <= max(18, round(52 * scale))
            and height <= max(18, round(52 * scale))
            and max(width, height) / max(min(width, height), 1) <= 2.8
        ):
            continue
        point = np.asarray([
            float(centers[index][0] + x0),
            float(centers[index][1] + y0),
        ], dtype=np.float32)
        candidates.append((float(np.linalg.norm(point - fallback)), point))
    if not candidates:
        return (fallback, False) if return_detection else fallback
    candidates.sort(key=lambda row: row[0])
    point = candidates[0][1]
    return (point, True) if return_detection else point


def rebase_group_display_tracking(self, group, map_id, effective):
    if group is not None and group.map_id == map_id:
                for tracked_floor, tracking in group.tracking_by_floor.items():
                    rebased = dict(tracking)
                    try:
                        cached_bridge = tracking.get('routeDisplayBridge')
                        old_bridge = (np.asarray(cached_bridge, dtype=np.float64) if cached_bridge is not None
                                      else self._shared_display_bridge(group.route_profile_id, map_id, tracked_floor))
                        new_profile = self._effective_route_profile(map_id, tracked_floor)
                        new_layer = self._shared_display_layer(new_profile, map_id, tracked_floor)
                        if (old_bridge is None or old_bridge.shape != (3, 3)
                            or not np.isfinite(old_bridge).all() or new_layer is None):
                            raise ValueError('display bridge unavailable')
                        transport = np.linalg.inv(old_bridge) @ new_layer['matrixDisplayToReference']
                        rebased['routeDisplayBridge'] = np.asarray(new_layer['matrixDisplayToReference']).tolist()
                        for key in ('translationMaskMatrix', 'layoutAnchorMatrix'):
                            if rebased.get(key) is None:
                                continue
                            matrix = np.asarray(rebased[key], dtype=np.float64)
                            if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
                                raise ValueError('invalid display tracking matrix')
                            rebased[key] = (matrix @ transport).tolist()
                        point = rebased.get('verifiedPlayerAuthorPoint')
                        if point is not None:
                            point = np.asarray(point, dtype=np.float64)
                            if point.shape != (2,) or not np.isfinite(point).all():
                                raise ValueError('invalid display tracking point')
                            projected = np.linalg.inv(transport) @ np.array([*point, 1.0])
                            if abs(projected[2]) < 1e-12 or not np.isfinite(projected).all():
                                raise ValueError('invalid transported display point')
                            rebased['verifiedPlayerAuthorPoint'] = (projected[:2] / projected[2]).tolist()
                    except (OSError, ValueError, TypeError, KeyError, np.linalg.LinAlgError, cv2.error):
                        # A render cache cannot undo an already-saved selection.
                        # Drop only its author basis; retain canonical evidence.
                        for key in ('translationMaskMatrix', 'layoutAnchorMatrix', 'verifiedPlayerAuthorPoint', 'routeDisplayBridge'):
                            rebased.pop(key, None)
                    tracking.clear()
                    tracking.update(rebased)
                    tracking['poseVerifiedAgainstRenderedProfile'] = False
                    tracking.pop('renderedProfileResidual', None)
                group.render_profile_verified_floors.clear()
                group.route_profile_id = effective
                group.displayed_overlay = None


def seed_group_pose(self, map_id: str) -> bool:
        """Import only the existing shared root/relation facts into V2."""
        core = getattr(self, "alignment_core_v2", None)
        group = self._active_round_layer_group(map_id)
        if core is None or group is None:
            return False
        existing = core.pose_by_map.get(str(map_id))
        if existing is not None:
            for floor, relation in getattr(group, "relations", {}).items():
                if str(floor) not in existing.floor_relations:
                    existing.floor_relations[str(floor)] = np.asarray(
                        relation, dtype=np.float64,
                    ).copy()
            return True
        root = np.asarray(getattr(group, "root_pose", None), dtype=np.float64)
        if root.shape != (3, 3) or not np.isfinite(root).all():
            return False
        return core.set_initial_pose(RoundPose(
            str(map_id),
            str(getattr(group, "route_profile_id", DEFAULT_ROUTE_PROFILE_ID)),
            str(getattr(group, "anchor_floor", "1")),
            root,
            getattr(group, "relations", {}),
            generation=int(getattr(group, "generation", 0)),
            reliable=True,
            source="round-layer-group-v2-seed",
        ))


def show_shared_projection(self, map_id, floor, previous_floor, event, *, display_environment_signature):
        """Present a normal-floor root projection; never promote target geometry."""
        group = self._active_round_layer_group(map_id)
        frame = getattr(self, 'current_frame', None)
        current = getattr(self, 'current_alignment_candidate', None) or {}
        # A same-floor probe advances the observation, not the projection's
        # trusted source. Only an actual floor transition supplies a new source.
        transition_source = previous_floor if str(previous_floor) != str(floor) else None
        source_floor = str(transition_source or current.get('projectionSourceFloor')
                           or getattr(group, 'tracking_floor', None)
                           or getattr(group, 'anchor_floor', ''))
        adapters = getattr(self, 'author_coordinate_adapters', None)
        seed = (adapters.canonical_cross_floor_search_seed(
            map_id, source_floor, floor, np.eye(3),
            authorization_profile_id=group.route_profile_id)
            if group is not None and adapters is not None else None)
        # Relation evidence, rather than game mode, defines whether a source
        # may only seed a same-open provisional display. Both modes otherwise
        # use the same root projection, observation and worker transaction.
        search_only_projection = bool((seed or {}).get('requiresCurrentFrameDisplayValidation'))
        if (group is None or {source_floor, str(floor)} != {'1', '2'}
            or group.tracking_floor == 'B1'
            or (source_floor not in group.canonical_geometry_verified_floors
                if search_only_projection else not (group.canonical_geometry_verified_floors & {'1', '2'}))
            or (search_only_projection and transition_source is None
                and not (current.get('floor') == str(floor)
                         and current.get('cycleId') == self.active_map_cycle_id
                         and current.get('mapId') == str(map_id)
                         and current.get('provisional') is True))
            or (search_only_projection and (
                group.cycle_id != self.active_map_cycle_id
                or (current.get('floor') != source_floor
                    or current.get('cycleId') != self.active_map_cycle_id
                    or current.get('mapId') != str(map_id)
                    or current.get('accepted') is not True)
                   and not (current.get('floor') == str(floor)
                            and current.get('cycleId') == self.active_map_cycle_id
                            and current.get('mapId') == str(map_id)
                            and current.get('provisional') is True)))
            or group.route_profile_id != getattr(self, 'route_profile_id', None)
            or str(getattr(getattr(self, 'gate', None), 'confirmed_id', '')) != str(map_id)
            or not isinstance(frame, np.ndarray)
            or group.pose_client_size != (frame.shape[1], frame.shape[0])
            or tuple(group.pose_display_signature) != display_environment_signature(getattr(self, 'display_environment', None))
            or bool(getattr(self, 'round_group_display_reprojection_active', False))
            or bool(getattr(self, 'mouse_drag_prediction_pending', False))
            or any(event.get(key) for key in ('explicitDiscontinuity', 'contradiction', 'teleport', 'positionJump'))):
            return False
        if ('alignmentV2WorkerFit' in event
            and not (search_only_projection and (event.get('alignmentV2WorkerFit') or {}).get(
                'sharedProjectionGeometryInsufficient') is True)
            and not self._alignment_v2_current_frame_equivalent(event)):
            return False
        failure = getattr(self, '_alignment_v2_worker_failure_state', None) or {}
        if (failure.get('blocksSharedProjection')
            and self._alignment_v2_failed_input_unchanged(map_id, floor, frame)):
            # The same pixels already rejected this root projection. A new
            # capture must not repaint it while the retry policy suppresses
            # its solver. Changed pixels still permit ordinary recovery.
            return False
        # The reviewed canonical adapter owns normal-floor relations. B1's
        # search-only portal relation cannot authorize this display.
        if not isinstance(seed, dict) or seed.get('targetToSourceMatrix') is None:
            return False
        if str(getattr(self, 'current_floor', '')) != str(floor):
            return False
        if search_only_projection and seed.get('boundedCrossFloorOnly') is not True:
            return False
        core = self.alignment_core_v2
        if (search_only_projection and current.get('provisional') is True
            and current.get('floor') == str(floor)
            and current.get('cycleId') == self.active_map_cycle_id
            and self._spatial_route_surface_is_visible()):
            authorization = core.authorize_shared_projection()
            if authorization is not None:
                current['displayAuthorizationId'] = authorization.authorization_id
                self._log_core_chain_event('alignment-v2-shared-projection-preserved',
                    mapId=map_id, floor=floor, sourceFloor=source_floor,
                    currentFrameVerified=False, persistentPoseUpdated=False)
                return True
        view = self._round_group_floor_view(map_id, floor)
        if not isinstance(view, dict) or view.get('overlay') is None:
            return False
        authorization = core.authorize_shared_projection()
        if authorization is None:
            return False
        overlay = view['overlay']
        if not self._alignment_v2_show_overlay(overlay, tuple(self.current_bounds[:2]),
                authorization_id=authorization.authorization_id):
            core.revoke_display(reason='shared-projection-compositor-rejected')
            return False
        self.drag_transform = np.asarray(view['matrixSourceToClient'], dtype=np.float64)
        self.drag_route_source = self._aligned_author_route_image(group.route_profile_id, map_id, floor)
        self.drag_route_profile_id = group.route_profile_id
        self.drag_current_overlay = overlay.copy()
        self.drag_overlay_base = overlay.copy()
        self.drag_overlay_offset = np.zeros(2, dtype=np.float64)
        self.current_alignment_candidate = {
            'cycleId': self.active_map_cycle_id, 'mapId': map_id, 'floor': floor,
            'accepted': False, 'provisional': True, 'projectionSourceFloor': source_floor,
            'fit': {'projectionOnly': True},
            'source': 'shared-root-provisional', 'displayAuthorizationId': authorization.authorization_id,
            'matrixSourceToClient': self.drag_transform.tolist()}
        # This compatibility flag enables pointer following. Geometry truth
        # remains in the explicit candidate/floor authorization and core pose.
        self.alignment_succeeded = True
        self._v2_compositor_active = True
        try:
            self._mark_overlay_display_verified('shared-root-provisional',
                pose_verified=False, current_floor_authorized=True)
        finally:
            self._v2_compositor_active = False
        self.show_sheet()
        self._log_core_chain_event('alignment-v2-shared-projection-displayed',
            mapId=map_id, floor=floor, sourceFloor=source_floor,
            currentFrameVerified=False, persistentPoseUpdated=False,
            relationEvidenceLevel=seed.get('evidenceLevel'),
            displayAuthorizationId=authorization.authorization_id)
        return True


def translated_source_matrix(
    matrix_source_to_client: list[list[float]] | np.ndarray | None,
    dx: float,
    dy: float,
) -> np.ndarray | None:
    """Apply a client-space pan to an original-sheet transform."""
    if matrix_source_to_client is None:
        return None
    matrix = np.asarray(matrix_source_to_client, dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        return None
    translated = matrix.copy()
    translated[0, 2] += float(dx)
    translated[1, 2] += float(dy)
    return translated
