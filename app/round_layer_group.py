from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


FLOORS = ("B1", "1", "2")


def centered_client_reprojection(
    source_size: tuple[int, int] | list[int] | None,
    target_size: tuple[int, int] | list[int] | None,
) -> np.ndarray | None:
    """Return a uniform, centered client-space resize transform.

    The game keeps its map canvas in a 16:9-safe viewport when a window is
    narrower or taller than the reference client.  Reprojecting around the
    viewport centre therefore preserves the canonical map pose while leaving
    the current-frame verifier to make the final decision.  This is a seed,
    never direct display authorization.
    """
    try:
        source_width, source_height = (int(value) for value in source_size)
        target_width, target_height = (int(value) for value in target_size)
    except (TypeError, ValueError):
        return None
    if min(source_width, source_height, target_width, target_height) <= 0:
        return None
    scale = min(
        float(target_width) / float(source_width),
        float(target_height) / float(source_height),
    )
    offset_x = (float(target_width) - float(source_width) * scale) / 2.0
    offset_y = (float(target_height) - float(source_height) * scale) / 2.0
    result = np.asarray(
        [[scale, 0.0, offset_x], [0.0, scale, offset_y], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    return result if np.isfinite(result).all() else None


def _matrix(value) -> np.ndarray | None:
    candidate = np.asarray(value, dtype=np.float64)
    if candidate.shape != (3, 3) or not np.isfinite(candidate).all():
        return None
    return candidate


@dataclass
class RoundLayerGroup:
    """One authoritative screen pose shared by every floor in a round.

    Floor relations are immutable coordinate adapters. They may place each
    source asset at a different origin, but no floor owns a live screen pose.
    Every drag, reopen correction, or edge calibration updates ``root_pose``
    once and all floor matrices are derived again from that single value.
    """

    map_id: str
    route_profile_id: str
    anchor_floor: str
    root_pose: np.ndarray
    relations: dict[str, np.ndarray] = field(default_factory=dict)
    relation_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    generation: int = 0
    cycle_id: int | None = None
    source: str = "fresh-alignment"
    pose_origin: str = "generic"
    pose_client_size: tuple[int, int] | None = None
    pose_display_signature: tuple = field(default_factory=tuple)
    tracking_floor: str | None = None
    tracking: dict[str, Any] = field(default_factory=dict)
    tracking_by_floor: dict[str, dict[str, Any]] = field(default_factory=dict)
    displayed_floor: str | None = None
    displayed_overlay: np.ndarray | None = None
    verified_floors: set[str] = field(default_factory=set)
    render_profile_verified_floors: set[str] = field(default_factory=set)
    canonical_geometry_verified_floors: set[str] = field(default_factory=set)
    source_structure_verified_floors: set[str] = field(default_factory=set)
    provisional_floors: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        root = _matrix(self.root_pose)
        if root is None:
            raise ValueError("round layer group requires a finite 3x3 root pose")
        if self.anchor_floor not in FLOORS:
            raise ValueError(f"unsupported anchor floor: {self.anchor_floor}")
        self.root_pose = root.copy()
        normalized: dict[str, np.ndarray] = {
            self.anchor_floor: np.eye(3, dtype=np.float64),
        }
        for floor, relation in self.relations.items():
            parsed = _matrix(relation)
            if floor in FLOORS and parsed is not None:
                normalized[str(floor)] = parsed.copy()
        self.relations = normalized
        self.verified_floors.add(self.anchor_floor)
        if self.pose_client_size is not None:
            try:
                width, height = (int(value) for value in self.pose_client_size)
            except (TypeError, ValueError):
                width = height = 0
            self.pose_client_size = (
                (width, height) if min(width, height) > 0 else None
            )
        self.pose_display_signature = tuple(self.pose_display_signature or ())

    def set_relation(
        self,
        floor: str,
        relation,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        parsed = _matrix(relation)
        if floor not in FLOORS or parsed is None:
            return False
        self.relations[str(floor)] = parsed.copy()
        if metadata is not None:
            self.relation_metadata[str(floor)] = dict(metadata)
        return True

    def canonical_pose_for_floor(self, floor: str) -> np.ndarray | None:
        relation = self.relations.get(str(floor))
        if relation is None:
            return None
        result = self.root_pose @ relation
        return result if np.isfinite(result).all() else None

    def profile_pose_for_floor(self, adapters, floor: str) -> np.ndarray | None:
        canonical = self.canonical_pose_for_floor(floor)
        if canonical is None:
            return None
        if self.route_profile_id == "source-structure":
            return _matrix(canonical)
        if adapters is None:
            return None
        result = adapters.compose_profile_to_client_from_canonical(
            self.route_profile_id, self.map_id, str(floor), canonical,
        )
        return _matrix(result)

    def recognition_pose_for_floor(self, adapters, floor: str) -> np.ndarray | None:
        canonical = self.canonical_pose_for_floor(floor)
        if canonical is None:
            return None
        if self.route_profile_id == "source-structure":
            return _matrix(canonical)
        if adapters is None:
            return None
        result = adapters.recognition_from_canonical_to_client(
            self.map_id, str(floor), canonical,
        )
        return _matrix(result)

    def apply_translation(
        self,
        dx: float,
        dy: float,
        *,
        source: str,
        cycle_id: int | None,
    ) -> bool:
        if not np.isfinite([dx, dy]).all() or float(np.hypot(dx, dy)) < 1e-6:
            return False
        self.root_pose = self.root_pose.copy()
        self.root_pose[0, 2] += float(dx)
        self.root_pose[1, 2] += float(dy)
        self.generation += 1
        self.source = str(source)
        self.cycle_id = cycle_id
        self.displayed_overlay = None
        return True

    def reproject_client_pose(
        self,
        target_size: tuple[int, int] | list[int] | None,
        *,
        source: str,
        cycle_id: int | None,
    ) -> dict[str, Any]:
        """Move the shared canonical pose into a new client coordinate space.

        Resolution changes alter both the linear scale and the viewport
        offset.  Treating that change as translation-only strands the route
        outside the new client.  All floor layers keep one shared root; old
        pixel masks/residuals are discarded and must be revalidated on the new
        frame before any route is displayed.
        """
        try:
            target = tuple(int(value) for value in target_size)
        except (TypeError, ValueError):
            return {"success": False, "reason": "target-client-size-invalid"}
        if len(target) != 2 or min(target) <= 0:
            return {"success": False, "reason": "target-client-size-invalid"}
        source_size = self.pose_client_size
        reprojection = centered_client_reprojection(source_size, target)
        if reprojection is None:
            self.pose_client_size = target
            self.displayed_overlay = None
            return {
                "success": False,
                "reason": "source-client-size-unavailable",
                "targetSize": list(target),
            }
        self.root_pose = reprojection @ self.root_pose
        self.generation += 1
        self.source = str(source)
        self.cycle_id = cycle_id
        self.pose_client_size = target
        self.displayed_overlay = None
        # A mask is tied to the old raster dimensions; retaining it would make
        # the next phase/anchor search report a false client-size mismatch.
        for tracking in self.tracking_by_floor.values():
            tracking["translationMask"] = None
            tracking["translationMaskMatrix"] = None
            tracking["layoutAnchorMask"] = None
            tracking["layoutAnchorMatrix"] = None
            tracking["translationAnchorHistory"] = []
            tracking["bounds"] = None
            tracking["poseVerifiedAgainstRenderedProfile"] = False
            tracking["poseVerifiedAgainstCanonicalGeometry"] = False
            tracking["poseVerifiedAgainstLiveGeometry"] = False
            tracking["renderedProfileResidual"] = None
        self.tracking = dict(self.tracking)
        return {
            "success": True,
            "sourceSize": list(source_size),
            "targetSize": list(target),
            "scale": float(reprojection[0, 0]),
            "offset": [float(reprojection[0, 2]), float(reprojection[1, 2])],
            "generation": self.generation,
        }

    def adopt_floor_canonical_pose(
        self,
        floor: str,
        floor_pose,
        *,
        source: str,
        cycle_id: int | None,
        maximum_linear_delta_ratio: float = 0.65,
    ) -> dict[str, Any]:
        candidate = _matrix(floor_pose)
        relation = self.relations.get(str(floor))
        if candidate is None or relation is None:
            return {"success": False, "reason": "floor-relation-unavailable"}
        try:
            root_candidate = candidate @ np.linalg.inv(relation)
        except np.linalg.LinAlgError:
            return {"success": False, "reason": "floor-relation-singular"}
        current_linear = self.root_pose[:2, :2]
        candidate_linear = root_candidate[:2, :2]
        denominator = max(float(np.linalg.norm(current_linear)), 1e-6)
        linear_delta_ratio = float(
            np.linalg.norm(candidate_linear - current_linear) / denominator
        )
        # This method is called only after the current frame has passed the
        # full display validation.  A zoomed or long-unseen map can therefore
        # legitimately move and rescale the shared root by much more than the
        # old 5.5% guard.  Reject only an extreme/implausible jump; accepting a
        # verified fit still updates exactly one root and consequently moves
        # B1/F1/F2 together.
        if linear_delta_ratio > maximum_linear_delta_ratio:
            return {
                "success": False,
                "reason": "shared-linear-transform-disagrees",
                "linearDeltaRatio": linear_delta_ratio,
            }
        delta = root_candidate[:2, 2] - self.root_pose[:2, 2]
        changed = bool(
            float(np.hypot(delta[0], delta[1])) >= 0.25
            or linear_delta_ratio >= 1e-5
        )
        if changed:
            self.root_pose = root_candidate
            self.generation += 1
            self.source = str(source)
            self.cycle_id = cycle_id
            self.displayed_overlay = None
        self.verified_floors.add(str(floor))
        self.provisional_floors.discard(str(floor))
        return {
            "success": True,
            "changed": changed,
            "dx": float(delta[0]),
            "dy": float(delta[1]),
            "linearDeltaRatio": linear_delta_ratio,
            "generation": self.generation,
        }

    def set_tracking(
        self,
        floor: str,
        evidence: dict[str, Any],
        *,
        cycle_id: int | None,
    ) -> None:
        """Keep floor-scoped visual evidence without creating floor poses.

        A floor tracker contains only captured pixels and the group-derived
        matrix that was verified with those pixels.  It can relocalize the one
        shared root later, but it never owns an independently mutable pose.
        """
        self.tracking_floor = str(floor)
        self.tracking = dict(evidence)
        self.tracking_by_floor[str(floor)] = self.tracking
        self.cycle_id = cycle_id

    def tracking_for_floor(self, floor: str) -> dict[str, Any]:
        stored = self.tracking_by_floor.get(str(floor))
        if isinstance(stored, dict):
            return stored
        if self.tracking_floor == str(floor) and isinstance(self.tracking, dict):
            return self.tracking
        return {}

    def mark_displayed(
        self,
        floor: str,
        overlay: np.ndarray,
        *,
        provisional: bool,
        render_profile_verified: bool = False,
        canonical_geometry_verified: bool = False,
        source_structure_verified: bool = False,
    ) -> None:
        self.displayed_floor = str(floor)
        self.displayed_overlay = overlay.copy()
        if provisional:
            self.provisional_floors.add(str(floor))
        else:
            self.verified_floors.add(str(floor))
            self.provisional_floors.discard(str(floor))
        if render_profile_verified:
            self.render_profile_verified_floors.add(str(floor))
        else:
            self.render_profile_verified_floors.discard(str(floor))
        if canonical_geometry_verified:
            self.canonical_geometry_verified_floors.add(str(floor))
        else:
            self.canonical_geometry_verified_floors.discard(str(floor))
        if source_structure_verified:
            self.source_structure_verified_floors.add(str(floor))
        else:
            self.source_structure_verified_floors.discard(str(floor))

    def snapshot(self) -> dict[str, Any]:
        return {
            "mapId": self.map_id,
            "routeProfileId": self.route_profile_id,
            "anchorFloor": self.anchor_floor,
            "rootPose": self.root_pose.tolist(),
            "availableFloors": sorted(self.relations),
            "generation": self.generation,
            "cycleId": self.cycle_id,
            "source": self.source,
            "poseOrigin": self.pose_origin,
            "poseClientSize": list(self.pose_client_size)
            if self.pose_client_size is not None else None,
            "poseDisplaySignature": list(self.pose_display_signature),
            "trackingFloor": self.tracking_floor,
            "trackingEvidenceFloors": sorted(self.tracking_by_floor),
            "displayedFloor": self.displayed_floor,
            "verifiedFloors": sorted(self.verified_floors),
            "renderProfileVerifiedFloors": sorted(
                self.render_profile_verified_floors
            ),
            "canonicalGeometryVerifiedFloors": sorted(
                self.canonical_geometry_verified_floors
            ),
            "sourceStructureVerifiedFloors": sorted(
                self.source_structure_verified_floors
            ),
            "provisionalFloors": sorted(self.provisional_floors),
            "singlePoseOwner": True,
        }
