from __future__ import annotations

"""Fail-closed stair-portal handoff metadata and coordinate helpers.

A portal binds only the two endpoints used during a floor transition.  It does
not claim that B1 and F1 share a whole-floor affine transform.  Static stair
glyphs are candidate generators; current-frame wall structure is always the
display authority.
"""

import json
import sys
from pathlib import Path
from typing import Sequence

import numpy as np


SOURCE_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
DEFAULT_STAIR_INDEX = RESOURCE_ROOT / "references" / "nightmare-stair-anchors.json"


def stair_pair_group_translation(
    previous_rows: Sequence[dict] | None,
    current_rows: Sequence[dict] | None,
    *,
    expected_scale: float | None = None,
) -> dict:
    """Recover one layer-group jump from two shared stair observations.

    The rows are screen-space observations from the previously verified floor
    and the current B1/F1 frame.  A single stair is deliberately insufficient:
    repeated staircase glyphs cannot identify which portal moved.  Two
    separated, orientation-compatible stairs must agree on one locked-axis
    scale and produce a unique centroid displacement.

    The returned displacement is *only* the shared screen translation at the
    portal-pair centroid.  The scale belongs to the destination floor's render
    adapter and must never be copied into the other floor layers.
    """

    def normalized(rows: Sequence[dict] | None) -> list[dict]:
        result = []
        for index, row in enumerate(rows or []):
            if not isinstance(row, dict):
                continue
            center_value = row.get("screenCenter")
            if center_value is None:
                center_value = row.get("center")
            center = np.asarray(center_value, dtype=np.float64)
            score = float(row.get("score", 0.0))
            treads = int(row.get("treadCount", 0))
            orientation = row.get("orientation")
            if (
                center.shape != (2,)
                or not np.isfinite(center).all()
                or score < 0.72
                or treads < 3
                or orientation not in {"h", "v"}
            ):
                continue
            result.append({
                "index": index,
                "center": center,
                "orientation": orientation,
                "score": score,
                "treadCount": treads,
            })
        return result[:12]

    previous = normalized(previous_rows)
    current = normalized(current_rows)
    if len(previous) < 2 or len(current) < 2:
        return {
            "success": False,
            "reason": "two-distinct-stair-observations-required",
            "previousCandidateCount": len(previous),
            "currentCandidateCount": len(current),
        }
    scale_prior = (
        float(expected_scale)
        if expected_scale is not None and np.isfinite(expected_scale)
        else 1.0
    )
    candidates: list[dict] = []
    for first_index in range(len(previous) - 1):
        for second_index in range(first_index + 1, len(previous)):
            first = previous[first_index]
            second = previous[second_index]
            previous_vector = second["center"] - first["center"]
            previous_distance = float(np.linalg.norm(previous_vector))
            if previous_distance < 52.0:
                continue
            for current_first_index in range(len(current) - 1):
                for current_second_index in range(
                    current_first_index + 1, len(current)
                ):
                    raw_pair = (
                        current[current_first_index],
                        current[current_second_index],
                    )
                    for now_first, now_second in (raw_pair, raw_pair[::-1]):
                        if (
                            first["orientation"] != now_first["orientation"]
                            or second["orientation"] != now_second["orientation"]
                        ):
                            continue
                        current_vector = (
                            now_second["center"] - now_first["center"]
                        )
                        current_distance = float(np.linalg.norm(current_vector))
                        if current_distance < 52.0:
                            continue
                        scale = current_distance / previous_distance
                        if not 0.72 <= scale <= 1.38:
                            continue
                        vector_residual = float(np.linalg.norm(
                            current_vector - previous_vector * scale
                        ))
                        if vector_residual > max(12.0, current_distance * 0.055):
                            continue
                        dot = float(np.dot(previous_vector, current_vector))
                        cosine = np.clip(
                            dot / max(previous_distance * current_distance, 1e-9),
                            -1.0, 1.0,
                        )
                        angle_error = float(np.degrees(np.arccos(cosine)))
                        if angle_error > 7.5:
                            continue
                        previous_midpoint = (
                            first["center"] + second["center"]
                        ) * 0.5
                        current_midpoint = (
                            now_first["center"] + now_second["center"]
                        ) * 0.5
                        group_delta = current_midpoint - previous_midpoint
                        tread_error = (
                            abs(first["treadCount"] - now_first["treadCount"])
                            + abs(second["treadCount"] - now_second["treadCount"])
                        )
                        evidence = min(
                            first["score"], second["score"],
                            now_first["score"], now_second["score"],
                        )
                        rank_score = (
                            evidence
                            - vector_residual / 80.0
                            - angle_error / 45.0
                            - abs(scale - scale_prior) * 0.30
                            - min(tread_error, 8) * 0.012
                        )
                        candidates.append({
                            "score": float(rank_score),
                            "groupTranslation": group_delta.tolist(),
                            "commonScale": float(scale),
                            "pairResidualPixels": vector_residual,
                            "angleErrorDegrees": angle_error,
                            "previousIndices": [
                                int(first["index"]), int(second["index"]),
                            ],
                            "currentIndices": [
                                int(now_first["index"]), int(now_second["index"]),
                            ],
                            "previousMidpoint": previous_midpoint.tolist(),
                            "currentMidpoint": current_midpoint.tolist(),
                        })
    candidates.sort(key=lambda row: row["score"], reverse=True)
    distinct: list[dict] = []
    for row in candidates:
        delta = np.asarray(row["groupTranslation"], dtype=np.float64)
        if any(
            np.linalg.norm(
                delta - np.asarray(other["groupTranslation"], dtype=np.float64)
            ) < 18.0
            for other in distinct
        ):
            continue
        distinct.append(row)
        if len(distinct) >= 6:
            break
    if not distinct:
        return {
            "success": False,
            "reason": "no-two-stair-locked-axis-consensus",
            "previousCandidateCount": len(previous),
            "currentCandidateCount": len(current),
        }
    best = distinct[0]
    alternative = distinct[1] if len(distinct) > 1 else None
    score_margin = float(
        best["score"] - (alternative or {"score": -999.0})["score"]
    )
    unique = bool(alternative is None or score_margin >= 0.075)
    success = bool(best["score"] >= 0.54 and unique)
    return {
        "success": success,
        "reason": (
            "two-stair-group-translation-verified"
            if success else "two-stair-group-translation-ambiguous"
        ),
        "anchorCount": 2,
        "unique": unique,
        "score": round(float(best["score"]), 5),
        "scoreMargin": round(score_margin, 5),
        "groupTranslation": [
            round(float(value), 4) for value in best["groupTranslation"]
        ],
        "commonScale": round(float(best["commonScale"]), 6),
        "pairResidualPixels": round(
            float(best["pairResidualPixels"]), 4
        ),
        "angleErrorDegrees": round(float(best["angleErrorDegrees"]), 4),
        "previousIndices": best["previousIndices"],
        "currentIndices": best["currentIndices"],
        "previousMidpoint": best["previousMidpoint"],
        "currentMidpoint": best["currentMidpoint"],
        "previousCandidateCount": len(previous),
        "currentCandidateCount": len(current),
        "alternative": alternative,
    }


def is_portal_transition(from_floor: str, to_floor: str) -> bool:
    """Only B1<->F1 is a portal; B1 must never jump through F2 geometry."""

    return {str(from_floor), str(to_floor)} == {"B1", "1"}


def portal_candidate_matrix(
    source_linear: Sequence[Sequence[float]] | np.ndarray,
    target_center: Sequence[float],
    live_center: Sequence[float],
    *,
    common_scale: float = 1.0,
) -> np.ndarray | None:
    """Place one target-floor stair endpoint on one observed live endpoint.

    Rotation and anisotropy come from the verified source-floor transform and
    remain locked during a single map opening.  Only a small common zoom sweep
    and translation may change.
    """

    linear = np.asarray(source_linear, dtype=np.float64)
    target = np.asarray(target_center, dtype=np.float64)
    live = np.asarray(live_center, dtype=np.float64)
    if (
        linear.shape != (2, 2)
        or target.shape != (2,)
        or live.shape != (2,)
        or not np.isfinite(linear).all()
        or not np.isfinite(target).all()
        or not np.isfinite(live).all()
        # Different floors are independently auto-fitted by the game UI.  A
        # saved real map-01 F1->B1 handoff measured about -8.2%, so the old
        # +/-6% sweep was too narrow even though the portal location was right.
        # This remains a bounded scale hypothesis; current-frame walls still
        # decide whether anything may be shown.
        or not 0.82 <= float(common_scale) <= 1.18
    ):
        return None
    locked = linear * float(common_scale)
    if abs(float(np.linalg.det(locked))) <= 1e-9:
        return None
    matrix = np.eye(3, dtype=np.float64)
    matrix[:2, :2] = locked
    matrix[:2, 2] = live - locked @ target
    return matrix


class StairPortalIndex:
    """Load candidate-only stair endpoints without granting display rights."""

    def __init__(self, path: Path = DEFAULT_STAIR_INDEX) -> None:
        self.path = Path(path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if raw.get("schema") != "identityv-nightmare-stair-anchors-v1":
            raise ValueError("unsupported stair portal index schema")
        policy = raw.get("policy") or {}
        if not (
            policy.get("candidateOnly") is True
            and policy.get("runtimeRequiresCurrentFrameStructureValidation") is True
            and policy.get("singleStairGlyphNeverAuthorizesDisplay") is True
        ):
            raise ValueError("unsafe stair portal index policy")
        self.policy = policy
        self.maps = {
            str(row.get("mapId")): row
            for row in (raw.get("maps") or [])
            if isinstance(row, dict) and row.get("mapId")
        }

    def candidates(
        self,
        map_id: str,
        floor: str,
        *,
        limit: int = 12,
        minimum_score: float = 0.74,
    ) -> list[dict]:
        rows = (
            self.maps.get(str(map_id), {})
            .get("floors", {})
            .get(str(floor), {})
            .get("candidates", [])
        )
        accepted = []
        for row in rows:
            center = np.asarray(row.get("center"), dtype=np.float64)
            if (
                center.shape != (2,)
                or not np.isfinite(center).all()
                or float(row.get("score", 0.0)) < minimum_score
                or int(row.get("treadCount", 0)) < 3
                or row.get("orientation") not in {"h", "v"}
            ):
                continue
            accepted.append({
                **row,
                "center": center.tolist(),
                "candidateOnly": True,
                "authorizesDisplay": False,
            })
        accepted.sort(
            key=lambda row: (
                float(row.get("score", 0.0)),
                int(row.get("treadCount", 0)),
            ),
            reverse=True,
        )
        return accepted[:max(1, int(limit))]

    def handoff_seed(
        self,
        map_id: str,
        from_floor: str,
        to_floor: str,
        source_recognition_to_client,
    ) -> dict | None:
        if not is_portal_transition(from_floor, to_floor):
            return None
        source = np.asarray(source_recognition_to_client, dtype=np.float64)
        targets = self.candidates(map_id, to_floor, limit=12)
        if source.shape != (3, 3) or not np.isfinite(source).all() or not targets:
            return None
        return {
            "mapId": str(map_id),
            "fromFloor": str(from_floor),
            "toFloor": str(to_floor),
            "sourceMatrixToClient": source.tolist(),
            # Kept separately because the round cache uses
            # sourceMatrixToClient to validate the original verified anchor.
            # A chained F2->reviewed-F1->B1 handoff must still retain the F1
            # recognition linear transform consumed by the portal solver.
            "sourceRecognitionMatrixToClient": source.tolist(),
            "targetStairCandidates": targets,
            "transferSource": "stair-portal-current-frame-enumeration",
            "stairPortalHandoff": True,
            "runtimeVerified": False,
            "directProjectionAllowed": False,
            "zeroExplorationDisplayAllowed": False,
            "boundedCrossFloorOnly": False,
            "sourceFloorTransformVerified": True,
            "requiresStableTargetFloor": True,
            "requiresUniqueCurrentFrameStructure": True,
            "singleStairGlyphNeverAuthorizesDisplay": True,
            "currentFrameDisplayValidated": False,
            "matrixCoordinateProfileId": "wang-qingxin",
            "mappingProfileId": "wang-qingxin",
            "confidence": 0.0,
        }
