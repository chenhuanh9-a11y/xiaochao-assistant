"""Single-owner alignment state for the V2 round-pose pipeline.

This module deliberately does not recognize maps, render images, or own UI
widgets.  It is the small stateful boundary between those concerns: workers
may propose a transform, but only this object can turn a current-frame
proposal into a display authorization and a new shared round pose.

The older assistant contains several historical recovery paths.  Keeping the
V2 contract here makes those paths safe to retire or adapt one at a time:
identity and route selection remain outside this module, while stale results,
cross-floor relations, close/reopen lifetimes, and display authorization are
handled in one place.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import numpy as np


FLOORS = ("B1", "1", "2")
HIGH_CONFIDENCE = 0.80
MIN_DYNAMIC_ANCHORS = 3
EVENT_HISTORY_LIMIT = 256


def _finite_matrix(value: Any) -> np.ndarray | None:
    try:
        matrix = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        return None
    return matrix.copy()


def _finite_vector(value: Any) -> np.ndarray | None:
    try:
        vector = np.asarray(value, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError):
        return None
    if vector.size != 2 or not np.isfinite(vector).all():
        return None
    return vector.copy()


@dataclass
class RoundPose:
    """The one live pose: standard map coordinates to client coordinates.

    ``root_matrix`` is the only mutable screen pose.  Floor relations are
    static map-coordinate adapters, so ``pose_for_floor`` always derives a
    target floor from the same root rather than storing a second live pose.
    """

    map_id: str
    route_profile_id: str
    anchor_floor: str
    root_matrix: np.ndarray
    floor_relations: dict[str, np.ndarray] = field(default_factory=dict)
    generation: int = 0
    reliable: bool = False
    source: str = "unknown"

    def __post_init__(self) -> None:
        root = _finite_matrix(self.root_matrix)
        if root is None:
            raise ValueError("RoundPose requires a finite 3x3 matrix")
        if self.anchor_floor not in FLOORS:
            raise ValueError(f"unsupported anchor floor: {self.anchor_floor}")
        self.root_matrix = root
        normalized = {self.anchor_floor: np.eye(3, dtype=np.float64)}
        for floor, relation in (self.floor_relations or {}).items():
            parsed = _finite_matrix(relation)
            if str(floor) in FLOORS and parsed is not None:
                normalized[str(floor)] = parsed
        self.floor_relations = normalized
        self.map_id = str(self.map_id)
        self.route_profile_id = str(self.route_profile_id)
        self.source = str(self.source)

    def pose_for_floor(self, floor: str) -> np.ndarray | None:
        relation = self.floor_relations.get(str(floor))
        if relation is None:
            return None
        return self.root_matrix @ relation

    def with_translation(self, dx: float, dy: float, *, source: str) -> "RoundPose":
        if not np.isfinite([dx, dy]).all():
            raise ValueError("translation must be finite")
        matrix = self.root_matrix.copy()
        matrix[0, 2] += float(dx)
        matrix[1, 2] += float(dy)
        return RoundPose(
            self.map_id,
            self.route_profile_id,
            self.anchor_floor,
            matrix,
            self.floor_relations,
            generation=self.generation + 1,
            reliable=self.reliable,
            source=source,
        )

    def adopt_floor_pose(
        self,
        floor: str,
        floor_matrix: Any,
        *,
        source: str,
        reliable: bool = True,
    ) -> "RoundPose" | None:
        candidate = _finite_matrix(floor_matrix)
        relation = self.floor_relations.get(str(floor))
        if candidate is None or relation is None:
            return None
        try:
            root = candidate @ np.linalg.inv(relation)
        except np.linalg.LinAlgError:
            return None
        return RoundPose(
            self.map_id,
            self.route_profile_id,
            self.anchor_floor,
            root,
            self.floor_relations,
            generation=self.generation + 1,
            reliable=bool(reliable),
            source=source,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "mapId": self.map_id,
            "routeProfileId": self.route_profile_id,
            "anchorFloor": self.anchor_floor,
            "rootMatrix": self.root_matrix.tolist(),
            "floorRelations": {
                floor: relation.tolist()
                for floor, relation in sorted(self.floor_relations.items())
            },
            "generation": self.generation,
            "reliable": self.reliable,
            "source": self.source,
            "singlePoseOwner": True,
        }


@dataclass
class Observation:
    observation_id: int
    cycle_id: int | str
    map_id: str
    floor: str
    frame_generation: int
    opened_at: float
    frame_signature: tuple = field(default_factory=tuple)
    stages: dict[str, float] = field(default_factory=dict)
    terminal_verdict: str | None = None
    terminal_reason: str | None = None
    closed: bool = False

    def current(self, *, cycle_id: int | str, frame_generation: int) -> bool:
        return bool(
            not self.closed
            and self.cycle_id == cycle_id
            and self.frame_generation == int(frame_generation)
        )

    def stage(self, name: str, elapsed_ms: float) -> None:
        if self.terminal_verdict is None:
            self.stages[str(name)] = max(0.0, float(elapsed_ms))


@dataclass(frozen=True)
class AlignmentCandidate:
    """A pure worker result; it cannot mutate the round by itself."""

    map_id: str
    floor: str
    matrix_source_to_client: np.ndarray
    source: str
    frame_generation: int
    confidence: float = 0.0
    anchor_count: int = 0
    anchor_kind: str = "global"
    current_frame_verified: bool = False
    residual_verified: bool = False
    global_residual_verified: bool = False
    translation_only: bool = False
    cross_floor: bool = False
    deterministic_relation: bool = False
    target_local_correction_verified: bool = False
    identity_rerank: bool = False
    explicit_discontinuity: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        matrix = _finite_matrix(self.matrix_source_to_client)
        if matrix is None:
            raise ValueError("alignment candidate requires a finite 3x3 matrix")
        object.__setattr__(self, "matrix_source_to_client", matrix)
        object.__setattr__(self, "map_id", str(self.map_id))
        object.__setattr__(self, "floor", str(self.floor))
        object.__setattr__(self, "source", str(self.source))


@dataclass(frozen=True)
class Adjudication:
    accepted: bool
    reason: str
    observation_id: int | None
    candidate: AlignmentCandidate | None = None
    pose: RoundPose | None = None
    terminal: bool = True


@dataclass
class DisplayAuthorization:
    authorization_id: int
    observation_id: int
    cycle_id: int | str
    map_id: str
    floor: str
    frame_generation: int
    pose_generation: int
    source: str
    issued_at: float
    active: bool = True
    continuity_until: float = 0.0

    def current(
        self,
        *,
        observation_id: int,
        cycle_id: int | str,
        map_id: str,
        floor: str,
        frame_generation: int,
    ) -> bool:
        return bool(
            self.active
            and self.observation_id == observation_id
            and self.cycle_id == cycle_id
            and self.map_id == str(map_id)
            and self.floor == str(floor)
            and self.frame_generation == int(frame_generation)
        )


class AlignmentCoreV2:
    """Own observations, one shared pose, and one display authorization.

    The optional ``clock`` and ``event_sink`` make the contract deterministic
    in tests while keeping production integration independent of Tk logging.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        import time

        self._clock = clock or time.perf_counter
        self._event_sink = event_sink
        self._next_observation_id = 0
        self._next_authorization_id = 0
        self.active_observation: Observation | None = None
        self.pose_by_map: dict[str, RoundPose] = {}
        self.display_authorization: DisplayAuthorization | None = None
        self._authorized_poses: dict[int, RoundPose] = {}
        self.anchor_pools: dict[tuple[str, str], dict[str, Any]] = {}
        self.candidate_cache: dict[tuple[str, str], list[AlignmentCandidate]] = {}
        self.pending_candidates: dict[int, list[AlignmentCandidate]] = {}
        self.events: list[dict[str, Any]] = []
        self.continuity_window_seconds = 2.5

    @property
    def active_pose(self) -> RoundPose | None:
        observation = self.active_observation
        return self.pose_by_map.get(observation.map_id) if observation else None

    def _emit(self, event: str, **values: Any) -> None:
        payload = {"event": event, **values}
        self.events.append(payload)
        # Local diagnostics keep a recent tail; a supplied sink still receives
        # every event. This history is never alignment or display authority.
        del self.events[:-EVENT_HISTORY_LIMIT]
        if self._event_sink is not None:
            self._event_sink(event, dict(payload))

    def begin_observation(
        self,
        *,
        cycle_id: int | str,
        map_id: str,
        floor: str,
        frame_generation: int,
        frame_signature: Any = (),
        reason: str = "open",
        now: float | None = None,
    ) -> Observation:
        if str(floor) not in FLOORS:
            raise ValueError(f"unsupported floor: {floor}")
        previous = self.active_observation
        previous_token = self.display_authorization
        preserve_continuity = bool(
            previous is not None
            and not previous.closed
            and previous.cycle_id == cycle_id
            and previous.map_id == str(map_id)
            and previous.floor == str(floor)
            and previous_token is not None
            and previous_token.active
        )
        if previous is not None:
            previous.closed = True
        now_value = float(self._clock() if now is None else now)
        if preserve_continuity and float(
            getattr(previous_token, "continuity_until", 0.0)
        ) > 0.0 and float(previous_token.continuity_until) <= now_value:
            self.revoke_display(reason="continuity-expired")
            preserve_continuity = False
        if preserve_continuity:
            if float(getattr(previous_token, "continuity_until", 0.0)) <= now_value:
                previous_token.continuity_until = (
                    now_value + float(self.continuity_window_seconds)
                )
            self._emit(
                "alignment-v2-display-continuity-started",
                authorizationId=previous_token.authorization_id,
                cycleId=cycle_id,
                mapId=str(map_id), floor=str(floor),
                fromFrameGeneration=previous_token.frame_generation,
                until=previous_token.continuity_until,
            )
        else:
            self.revoke_display(reason="new-observation")
        self._next_observation_id += 1
        observation = Observation(
            observation_id=self._next_observation_id,
            cycle_id=cycle_id,
            map_id=str(map_id),
            floor=str(floor),
            frame_generation=int(frame_generation),
            opened_at=now_value,
            frame_signature=tuple(frame_signature or ()),
        )
        # Pending proposals and uncommitted poses belong only to one open
        # observation. Round poses, anchors and late-candidate caches do not.
        self.pending_candidates.clear()
        self._authorized_poses.clear()
        self.active_observation = observation
        self.pending_candidates[observation.observation_id] = []
        self._emit(
            "alignment-v2-observation-opened",
            observationId=observation.observation_id,
            cycleId=cycle_id,
            mapId=str(map_id),
            floor=str(floor),
            frameGeneration=int(frame_generation),
            reason=str(reason),
        )
        return observation

    def update_frame(
        self,
        *,
        frame_generation: int,
        floor: str | None = None,
        frame_signature: Any = (),
    ) -> bool:
        observation = self.active_observation
        if observation is None or observation.closed:
            return False
        observation.frame_generation = int(frame_generation)
        if floor is not None:
            if str(floor) not in FLOORS:
                return False
            observation.floor = str(floor)
        if frame_signature:
            observation.frame_signature = tuple(frame_signature)
        observation.terminal_verdict = None
        observation.terminal_reason = None
        self.revoke_display(reason="new-frame")
        self.pending_candidates[observation.observation_id] = []
        return True

    def record_stage(self, name: str, elapsed_ms: float) -> bool:
        observation = self.active_observation
        if observation is None or observation.terminal_verdict is not None:
            return False
        observation.stage(name, elapsed_ms)
        return True

    def set_initial_pose(self, pose: RoundPose, *, reason: str = "initial") -> bool:
        if not pose.reliable:
            return False
        self.pose_by_map[pose.map_id] = pose
        self._emit(
            "alignment-v2-pose-seeded",
            mapId=pose.map_id,
            generation=pose.generation,
            source=str(reason),
            singlePoseOwner=True,
        )
        return True

    def update_anchor_pool(
        self,
        *,
        map_id: str,
        floor: str,
        anchors: Any,
        frame_generation: int,
        success: bool,
    ) -> dict[str, Any]:
        key = (str(map_id), str(floor))
        previous = self.anchor_pools.get(key)
        if success:
            # The committed observation owns its pixel/pose pair. Mutable
            # compatibility trackers must not alias this retained basis.
            parsed = copy.deepcopy(dict(anchors or {})) if isinstance(anchors, Mapping) else {}
            parsed.update({
                "mapId": str(map_id),
                "floor": str(floor),
                "frameGeneration": int(frame_generation),
            })
            self.anchor_pools[key] = parsed
        elif previous is None:
            # A first failed observation is still represented as an empty
            # pool; subsequent failures must not erase a useful pool.
            self.anchor_pools[key] = {
                "mapId": str(map_id),
                "floor": str(floor),
                "frameGeneration": int(frame_generation),
                "anchors": [],
            }
        else:
            self._emit(
                "alignment-v2-anchor-pool-retained",
                mapId=str(map_id),
                floor=str(floor),
                frameGeneration=int(frame_generation),
                previousFrameGeneration=previous.get("frameGeneration"),
            )
        return dict(self.anchor_pools[key])

    def cache_candidate(
        self, candidate: AlignmentCandidate, *, reason: str = "late-background-result",
    ) -> bool:
        """Retain a late sensor result without granting display authority."""
        key = (str(candidate.map_id), str(candidate.floor))
        rows = self.candidate_cache.setdefault(key, [])
        rows.append(candidate)
        del rows[:-8]
        self._emit(
            "alignment-v2-candidate-cached",
            mapId=key[0], floor=key[1], frameGeneration=candidate.frame_generation,
            source=candidate.source, reason=str(reason), displayAuthorized=False,
        )
        return True

    def _reject(
        self,
        reason: str,
        candidate: AlignmentCandidate | None = None,
        *,
        terminal: bool = False,
    ) -> Adjudication:
        observation = self.active_observation
        observation_id = observation.observation_id if observation else None
        if terminal and observation is not None and observation.terminal_verdict is None:
            observation.terminal_verdict = "rejected"
            observation.terminal_reason = str(reason)
            self._emit(
                "alignment-v2-terminal-verdict",
                observationId=observation.observation_id,
                verdict="rejected",
                reason=str(reason),
            )
        return Adjudication(False, str(reason), observation_id, candidate, terminal=terminal)

    def adjudicate(self, candidate: AlignmentCandidate) -> Adjudication:
        observation = self.active_observation
        if observation is None or observation.closed:
            return Adjudication(False, "observation-not-active", None, candidate)
        if observation.terminal_verdict is not None:
            return Adjudication(
                False, "observation-already-terminal", observation.observation_id,
                candidate,
            )
        if candidate.map_id != observation.map_id:
            return self._reject("candidate-map-stale", candidate)
        if candidate.floor != observation.floor:
            return self._reject("candidate-floor-stale", candidate)
        if candidate.frame_generation != observation.frame_generation:
            return self._reject("candidate-frame-stale", candidate)
        if candidate.identity_rerank:
            return self._reject("identity-rerank-not-alignment", candidate)
        if not candidate.current_frame_verified:
            return self._reject("current-frame-verification-required", candidate)
        direct_anchor = candidate.anchor_kind in {"door", "dynamic-anchor"}
        high_confidence_anchor = bool(
            direct_anchor
            and candidate.confidence >= HIGH_CONFIDENCE
            and candidate.anchor_count >= (
                1 if candidate.anchor_kind == "door" else MIN_DYNAMIC_ANCHORS
            )
        )
        if candidate.cross_floor and not (
            candidate.deterministic_relation
            and candidate.target_local_correction_verified
        ):
            return self._reject("cross-floor-local-correction-required", candidate)
        if not high_confidence_anchor and not candidate.residual_verified:
            return self._reject("current-frame-residual-unverified", candidate)
        if not candidate.cross_floor and "same-floor-reopen" in candidate.source:
            if not candidate.translation_only:
                return self._reject("same-floor-reopen-must-be-translation-only", candidate)
            previous = self.pose_by_map.get(candidate.map_id)
            if previous is not None:
                old = previous.pose_for_floor(candidate.floor)
                if old is None or not np.allclose(
                    old[:2, :2], candidate.matrix_source_to_client[:2, :2],
                    atol=1e-5,
                ):
                    return self._reject("same-floor-reopen-linear-change", candidate)
        previous_pose = self.pose_by_map.get(candidate.map_id)
        independent_floor = bool(previous_pose is not None
            and previous_pose.pose_for_floor(candidate.floor) is None
            and candidate.metadata.get('independentFloorAnchor') is True
            and candidate.source == 'initial-structure-v2'
            and candidate.anchor_kind == 'global'
            and candidate.global_residual_verified and candidate.residual_verified
            and not candidate.cross_floor)
        if previous_pose is None or independent_floor:
            # A first pose is anchored at the candidate floor.  Future floors
            # must supply their deterministic relation before they can adopt.
            pose = RoundPose(
                candidate.map_id,
                str(candidate.metadata.get("routeProfileId") or "default"),
                candidate.floor,
                candidate.matrix_source_to_client,
                {candidate.floor: np.eye(3, dtype=np.float64)},
                reliable=True,
                source=candidate.source,
            )
            if independent_floor:
                pose.generation = previous_pose.generation + 1
        else:
            pose = previous_pose.adopt_floor_pose(
                candidate.floor,
                candidate.matrix_source_to_client,
                source=candidate.source,
                reliable=True,
            )
            if pose is None:
                return self._reject("floor-relation-unavailable", candidate)
        observation.terminal_verdict = "accepted"
        observation.terminal_reason = str(candidate.source)
        self.pending_candidates[observation.observation_id].append(candidate)
        self._emit(
            "alignment-v2-terminal-verdict",
            observationId=observation.observation_id,
            verdict="accepted",
            reason=str(candidate.source),
            anchorKind=candidate.anchor_kind,
            globalResidualIgnored=bool(high_confidence_anchor and not candidate.global_residual_verified),
        )
        return Adjudication(
            True, "accepted", observation.observation_id, candidate, pose,
        )

    def authorize(
        self,
        adjudication: Adjudication,
        *,
        source: str | None = None,
        now: float | None = None,
    ) -> DisplayAuthorization | None:
        observation = self.active_observation
        if (
            not adjudication.accepted
            or observation is None
            or adjudication.observation_id != observation.observation_id
            or adjudication.pose is None
            or observation.closed
        ):
            return None
        self.revoke_display(reason="new-authorization")
        self._next_authorization_id += 1
        authorization = DisplayAuthorization(
            authorization_id=self._next_authorization_id,
            observation_id=observation.observation_id,
            cycle_id=observation.cycle_id,
            map_id=observation.map_id,
            floor=observation.floor,
            frame_generation=observation.frame_generation,
            pose_generation=adjudication.pose.generation,
            source=str(source or adjudication.candidate.source),
            issued_at=float(self._clock() if now is None else now),
        )
        self.display_authorization = authorization
        self._authorized_poses[authorization.authorization_id] = adjudication.pose
        self._emit(
            "alignment-v2-display-authorized",
            authorizationId=authorization.authorization_id,
            observationId=authorization.observation_id,
            cycleId=authorization.cycle_id,
            mapId=authorization.map_id,
            floor=authorization.floor,
            frameGeneration=authorization.frame_generation,
            poseGeneration=authorization.pose_generation,
            source=authorization.source,
        )
        return authorization

    def commit(self, authorization: DisplayAuthorization, *, persist_pose: bool = True) -> bool:
        """Commit a current display; open-cycle mouse motion is not a round pose."""
        observation = self.active_observation
        if observation is None or not authorization.current(
            observation_id=observation.observation_id,
            cycle_id=observation.cycle_id,
            map_id=observation.map_id,
            floor=observation.floor,
            frame_generation=observation.frame_generation,
        ):
            return False
        pose = self._authorized_poses.pop(authorization.authorization_id, None)
        if pose is None:
            return False
        if observation.terminal_verdict is not None:
            self.pending_candidates.pop(authorization.observation_id, None)
        # The pose belongs to the adjudication that issued this token.  It is
        # stored only here, never by a worker or by a floor-specific adapter.
        if persist_pose:
            self.pose_by_map[pose.map_id] = pose
        self._emit(
            "alignment-v2-commit",
            authorizationId=authorization.authorization_id,
            observationId=observation.observation_id,
            mapId=pose.map_id,
            floor=pose.anchor_floor,
            poseGeneration=pose.generation,
            singlePoseOwner=True,
            persistentPoseUpdated=bool(persist_pose),
        )
        return True

    def authorize_shared_projection(self) -> DisplayAuthorization | None:
        """Display the reliable root on a prepared normal floor without learning a pose.

        The caller owns relation/profile and display-environment eligibility.
        This leaves the observation open for the current-frame solver verdict.
        """
        observation = self.active_observation
        pose = self.pose_by_map.get(observation.map_id) if observation else None
        if (observation is None or observation.closed or pose is None
                or not pose.reliable or observation.floor not in {"1", "2"}
                or pose.pose_for_floor(observation.floor) is None):
            return None
        authorization = self.authorize(Adjudication(
            True, "shared-root-provisional", observation.observation_id,
            pose=pose, terminal=False), source="shared-root-provisional")
        if authorization is None or not self.commit(authorization, persist_pose=False):
            return None
        return authorization

    def authorize_provisional_target(self) -> DisplayAuthorization | None:
        """Authorize one current target display without learning its position.

        The UI must separately supply a source-anchored provisional matrix;
        this token only binds that display to the current observation. The
        verified root remains the sole persistent screen pose.
        """
        observation = self.active_observation
        pose = self.pose_by_map.get(observation.map_id) if observation else None
        if (observation is None or observation.closed or pose is None
                or not pose.reliable or observation.floor not in {"1", "2"}):
            return None
        authorization = self.authorize(Adjudication(
            True, "source-marker-provisional", observation.observation_id,
            pose=pose, terminal=False), source="source-marker-provisional")
        if authorization is None or not self.commit(authorization, persist_pose=False):
            return None
        return authorization

    def record_verified_display(
        self,
        *,
        map_id: str,
        floor: str,
        matrix_source_to_client: Any,
        source: str,
        frame_generation: int,
        pose_verified: bool = True,
    ) -> DisplayAuthorization | None:
        """Adapt one already-verified compositor result into V2 ownership.

        This is a migration seam, not a second adjudicator.  The legacy
        renderer may finish the raster work, but the result is still entered
        through the same current-frame candidate, authorization, and commit
        sequence.  A stale callback therefore returns ``None`` and cannot
        make its pixels current again.
        """
        observation = self.active_observation
        if (
            observation is None
            or not observation.current(
                cycle_id=observation.cycle_id,
                frame_generation=frame_generation,
            )
            or observation.map_id != str(map_id)
            or observation.floor != str(floor)
        ):
            return None
        candidate = AlignmentCandidate(
            map_id=str(map_id),
            floor=str(floor),
            matrix_source_to_client=matrix_source_to_client,
            source=str(source),
            frame_generation=int(frame_generation),
            confidence=1.0 if pose_verified else 0.0,
            anchor_count=1,
            anchor_kind="verified-display",
            current_frame_verified=bool(pose_verified),
            residual_verified=bool(pose_verified),
            metadata={"routeProfileId": (
                self.pose_by_map.get(str(map_id)).route_profile_id
                if self.pose_by_map.get(str(map_id)) is not None else "default"
            )},
        )
        adjudication = self.adjudicate(candidate)
        authorization = self.authorize(adjudication, source=str(source))
        if authorization is None or not self.commit(authorization):
            if authorization is not None:
                self.revoke_display(reason="verified-display-commit-failed")
            return None
        return authorization

    def preserve_equivalent_display(self) -> bool:
        """Rebind an active display after the caller compared its measured pixels.

        No pose or anchor is changed. This is only a same-cycle/floor renewal
        of an unexpired authorization, never recovery of a revoked display.
        """
        observation = self.active_observation
        token = self.display_authorization
        if observation is None or observation.closed or token is None:
            return False
        if not self.can_display(cycle_id=observation.cycle_id,
            map_id=observation.map_id, floor=observation.floor,
            frame_generation=observation.frame_generation):
            return False
        token.observation_id = observation.observation_id
        token.frame_generation = observation.frame_generation
        token.continuity_until = 0.0
        observation.terminal_verdict = 'accepted'
        observation.terminal_reason = 'equivalent-verified-pixels'
        return True

    def can_display(
        self,
        *,
        authorization: DisplayAuthorization | None = None,
        cycle_id: int | str,
        map_id: str,
        floor: str,
        frame_generation: int,
    ) -> bool:
        observation = self.active_observation
        token = authorization or self.display_authorization
        if bool(
            observation is not None
            and token is not None
            and token.current(
                observation_id=observation.observation_id,
                cycle_id=cycle_id,
                map_id=map_id,
                floor=floor,
                frame_generation=frame_generation,
            )
        ):
            return True
        return self.continuity_current(
            authorization=token,
            cycle_id=cycle_id,
            map_id=map_id,
            floor=floor,
            frame_generation=frame_generation,
        )

    def continuity_current(
        self,
        *,
        authorization: DisplayAuthorization | None = None,
        cycle_id: int | str,
        map_id: str,
        floor: str,
        frame_generation: int,
    ) -> bool:
        """Return whether a same-floor prior display may remain visible briefly."""
        observation = self.active_observation
        token = authorization or self.display_authorization
        if observation is None or token is None or not token.active:
            return False
        now = float(self._clock())
        if now > float(getattr(token, "continuity_until", 0.0)):
            self.revoke_display(reason="continuity-expired")
            return False
        return bool(
            token.continuity_until > 0.0
            and token.observation_id != observation.observation_id
            and token.cycle_id == cycle_id
            and token.map_id == str(map_id)
            and token.floor == str(floor)
            and observation.cycle_id == cycle_id
            and observation.map_id == str(map_id)
            and observation.floor == str(floor)
            and int(frame_generation) >= int(token.frame_generation)
        )

    def revoke_display(self, *, reason: str = "hidden") -> None:
        token = self.display_authorization
        if token is None:
            return
        token.active = False
        self._authorized_poses.pop(token.authorization_id, None)
        observation = self.active_observation
        if (observation is None or observation.observation_id != token.observation_id
                or observation.closed or observation.terminal_verdict is not None):
            self.pending_candidates.pop(token.observation_id, None)
        self._emit(
            "alignment-v2-display-revoked",
            authorizationId=token.authorization_id,
            observationId=token.observation_id,
            reason=str(reason),
        )

    def close(self, *, cycle_id: int | str | None = None, reason: str = "map-closed") -> bool:
        observation = self.active_observation
        if observation is None:
            self.revoke_display(reason=reason)
            return False
        if cycle_id is not None and observation.cycle_id != cycle_id:
            return False
        observation.closed = True
        self.pending_candidates.pop(observation.observation_id, None)
        if observation.terminal_verdict is None:
            observation.terminal_verdict = "closed"
            observation.terminal_reason = str(reason)
            self._emit(
                "alignment-v2-terminal-verdict",
                observationId=observation.observation_id,
                verdict="closed",
                reason=str(reason),
            )
        self.revoke_display(reason=reason)
        self._emit(
            "alignment-v2-observation-closed",
            observationId=observation.observation_id,
            cycleId=observation.cycle_id,
            mapId=observation.map_id,
            floor=observation.floor,
            posePreserved=observation.map_id in self.pose_by_map,
            anchorPoolPreserved=(observation.map_id, observation.floor) in self.anchor_pools,
            reason=str(reason),
        )
        return True

    def reset_round(self, *, reason: str = "round-reset") -> None:
        """Clear all pose/candidate state at a true round boundary."""
        self.revoke_display(reason=reason)
        self.active_observation = None
        self.pose_by_map.clear()
        self._authorized_poses.clear()
        self.anchor_pools.clear()
        self.candidate_cache.clear()
        self.pending_candidates.clear()
        self._emit(
            "alignment-v2-round-reset",
            reason=str(reason), singlePoseOwner=True,
        )

    def snapshot(self) -> dict[str, Any]:
        observation = self.active_observation
        return {
            "activeObservation": (
                {
                    "observationId": observation.observation_id,
                    "cycleId": observation.cycle_id,
                    "mapId": observation.map_id,
                    "floor": observation.floor,
                    "frameGeneration": observation.frame_generation,
                    "terminalVerdict": observation.terminal_verdict,
                    "closed": observation.closed,
                    "stages": dict(observation.stages),
                }
                if observation is not None else None
            ),
            "poses": {
                map_id: pose.snapshot()
                for map_id, pose in sorted(self.pose_by_map.items())
            },
            "displayAuthorized": bool(
                self.display_authorization is not None
                and self.display_authorization.active
            ),
            "anchorPoolKeys": sorted(self.anchor_pools),
            "singlePoseOwner": True,
        }
