from __future__ import annotations

"""Validated author routes on the neutral Nightmare coordinate pack."""

import json
import sys
from pathlib import Path
from typing import Sequence

import numpy as np
import cv2


SOURCE_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
DEFAULT_ADAPTERS = (
    RESOURCE_ROOT / "references" / "nightmare-canonical-coordinate-pack.json"
)
CANONICAL_SCHEMA = "identityv-nightmare-canonical-coordinate-pack-v1"
LEGACY_SCHEMA = "identityv-nightmare-author-coordinate-adapters-v1"


class AuthorCoordinateAdapters:
    def __init__(self, path: Path = DEFAULT_ADAPTERS, *,
                 native_static_context: dict | None = None) -> None:
        self.path = Path(path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        schema = raw.get("schema")
        if schema not in {CANONICAL_SCHEMA, LEGACY_SCHEMA}:
            raise ValueError("unsupported author coordinate adapter schema")
        policy = raw.get("policy") or {}
        current_frame_required = bool(
            policy.get("requiresCurrentFrameGeometryFit") is True
            if schema == CANONICAL_SCHEMA else
            policy.get("requiresCurrentFrameRecognitionFit") is True
        )
        if not current_frame_required or (
            policy.get("authorizesMapIdentity") is not False
            or policy.get("authorizesCrossFloorProjection") is not False
        ):
            raise ValueError("unsafe author coordinate adapter policy")
        self._raw = raw
        self.schema = str(schema)
        self.policy = policy
        self.profiles = dict(raw.get("profiles") or {})
        self.canonical_pack_id = str(
            raw.get("canonicalPackId") or raw.get("recognitionPackId")
            or "wang-qingxin-nightmare-v1"
        )
        self.coordinate_reference_profile_id = str(
            raw.get("coordinateReferenceProfileId") or "wang-qingxin"
        )
        self.maps = dict(raw.get("maps") or {})
        self.canonical_cross_floor = dict(
            raw.get("canonicalCrossFloorSearchCandidates") or {}
        )
        self.native_static_context = native_static_context
        self._native_static_resource_cache = {}

    def _floor_row(self, profile_id: str, map_id: str, floor: str) -> dict | None:
        row = (
            self.profiles.get(profile_id, {})
            .get("maps", {})
            .get(map_id, {})
            .get("floors", {})
            .get(floor)
        )
        return row if isinstance(row, dict) else None

    @staticmethod
    def _matrix(value) -> np.ndarray | None:
        matrix = np.asarray(value, dtype=np.float64)
        if (
            matrix.shape != (3, 3)
            or not np.isfinite(matrix).all()
            or abs(float(np.linalg.det(matrix[:2, :2]))) <= 1e-9
        ):
            return None
        return matrix

    def matrix_profile_to_canonical(
        self,
        profile_id: str,
        map_id: str,
        floor: str,
    ) -> np.ndarray | None:
        row = self._floor_row(profile_id, map_id, floor)
        if not isinstance(row, dict) or not row.get("usableForCurrentFrameRendering"):
            return None
        if row.get("crossFloorProjectionAllowed") is not False:
            return None
        evidence = row.get("evidence") or {}
        evidence_type = evidence.get("type")
        native_hint = (
            evidence_type is not None
            or evidence.get("nativeStaticRegistration") is True
            or str(evidence.get("method") or "").startswith("native-")
        )
        if native_hint:
            if self.schema == LEGACY_SCHEMA or evidence_type != "native-static-corner-registration":
                return None
            context = self.native_static_context
            if not isinstance(context, dict):
                return None
            try:
                from native_static_corner_registration import (
                    NativeCornerRequest,
                    resolve_native_corner_context,
                    validate_native_static_corner_registration,
                )
                binding = evidence.get("binding") or {}
                request = NativeCornerRequest(
                    profile=str(binding["profile"]), map_id=str(binding["mapId"]),
                    floor=str(binding["floor"]), variant=str(binding["variant"]),
                    native_layout=str(binding["nativeLayout"]),
                    native_floor=int(binding["nativeFloor"]),
                )
                if (request.profile, request.map_id, request.floor) != (
                    str(profile_id), str(map_id), str(floor)
                ):
                    return None
                primary_profile = self.coordinate_reference_profile_id
                primary_row = self._floor_row(primary_profile, map_id, floor)
                if not isinstance(primary_row, dict):
                    return None
                resolved = resolve_native_corner_context(
                    request,
                    evidence=evidence,
                    canonical_profile_id=primary_profile,
                    primary_row=primary_row,
                    resource_cache=self._native_static_resource_cache,
                    **context,
                )
                result = validate_native_static_corner_registration(
                    row.get("matrixProfileToCanonical"), evidence, resolved,
                )
            except (KeyError, TypeError, ValueError, OverflowError, OSError, cv2.error):
                return None
            return self._matrix(row.get("matrixProfileToCanonical")) if result.get("accepted") else None
        if self.schema == LEGACY_SCHEMA:
            return self._validated_row_matrix(
                row, "matrixProfileToRecognition", floor,
            )
        if evidence.get("algebraicallyExact") is True:
            return self._matrix(row.get("matrixProfileToCanonical"))
        return self._validated_row_matrix(
            row, "matrixProfileToCanonical", floor,
        )

    def _validated_row_matrix(
        self, row: dict, matrix_key: str, floor: str,
    ) -> np.ndarray | None:
        evidence = row.get("evidence") or {}
        coverage = evidence.get("supportCoverage") or {}
        residual = evidence.get("residual") or {}
        summary = evidence.get("matrixSummary") or {}
        method = str(evidence.get("method") or "")
        processed_author = method in {
            "processed-author-direct-axis-locked-current-frame-required",
            "processed-author-axis-locked-via-lianghapi-current-frame-required",
            "processed-author-axis-locked-via-zhan10-speed-current-frame-required",
        }
        minimum_inliers = 8 if processed_author else 100
        minimum_quadrants = 2 if processed_author else 3
        minimum_grid_cells = 2 if processed_author else 6
        minimum_span = 0.12 if processed_author else 0.0
        if str(floor) == "B1" and not processed_author:
            minimum_inliers = 40
            minimum_grid_cells = 4
        if not (
            int(evidence.get("inlierMatches", 0)) >= minimum_inliers
            and int(coverage.get("coveredQuadrants", 0)) >= minimum_quadrants
            and int(coverage.get("coveredGridCells4x4", 0)) >= minimum_grid_cells
            and float(coverage.get("spanWidthRatio", 0.0)) >= minimum_span
            and float(coverage.get("spanHeightRatio", 0.0)) >= minimum_span
            and float(residual.get("p90Pixels", 999.0)) <= 20.5
            and abs(float(summary.get("rotationDegrees", 999.0))) <= 1e-8
            and abs(float(summary.get("shearDegrees", 999.0))) <= 1e-8
            and float(summary.get("anisotropy", 999.0)) <= 1.13
            and (
                not processed_author
                or evidence.get("currentFrameSelectedRouteResidualRequired") is True
            )
        ):
            return None
        return self._matrix(row.get(matrix_key))

    def matrix_profile_to_recognition(
        self,
        profile_id: str,
        map_id: str,
        floor: str,
    ) -> np.ndarray | None:
        if self.schema == LEGACY_SCHEMA:
            return self.matrix_profile_to_canonical(profile_id, map_id, floor)
        profile_to_canonical = self.matrix_profile_to_canonical(
            profile_id, map_id, floor,
        )
        canonical_to_recognition = self.matrix_canonical_to_recognition(
            map_id, floor,
        )
        if profile_to_canonical is None or canonical_to_recognition is None:
            return None
        return canonical_to_recognition @ profile_to_canonical

    def matrix_canonical_to_recognition(
        self, map_id: str, floor: str,
    ) -> np.ndarray | None:
        if self.schema == LEGACY_SCHEMA:
            return np.eye(3, dtype=np.float64)
        row = self.maps.get(map_id, {}).get("floors", {}).get(floor)
        return self._matrix(
            (row or {}).get("matrixCanonicalToLegacyRecognition")
        )

    def matrix_recognition_to_canonical(
        self, map_id: str, floor: str,
    ) -> np.ndarray | None:
        matrix = self.matrix_canonical_to_recognition(map_id, floor)
        if matrix is None:
            return None
        try:
            return np.linalg.inv(matrix)
        except np.linalg.LinAlgError:
            return None

    def canonical_from_recognition_to_client(
        self, map_id: str, floor: str, recognition_to_client,
    ) -> np.ndarray | None:
        recognition = self._matrix(recognition_to_client)
        canonical_to_recognition = self.matrix_canonical_to_recognition(
            map_id, floor,
        )
        if recognition is None or canonical_to_recognition is None:
            return None
        result = recognition @ canonical_to_recognition
        return result if np.isfinite(result).all() else None

    def recognition_from_canonical_to_client(
        self, map_id: str, floor: str, canonical_to_client,
    ) -> np.ndarray | None:
        canonical = self._matrix(canonical_to_client)
        recognition_to_canonical = self.matrix_recognition_to_canonical(
            map_id, floor,
        )
        if canonical is None or recognition_to_canonical is None:
            return None
        result = canonical @ recognition_to_canonical
        return result if np.isfinite(result).all() else None

    def compose_profile_to_client(
        self,
        profile_id: str,
        map_id: str,
        floor: str,
        recognition_to_client: Sequence[Sequence[float]] | np.ndarray,
    ) -> np.ndarray | None:
        adapter = self.matrix_profile_to_recognition(profile_id, map_id, floor)
        recognition = np.asarray(recognition_to_client, dtype=np.float64)
        if adapter is None or recognition.shape != (3, 3) or not np.isfinite(recognition).all():
            return None
        result = recognition @ adapter
        return result if np.isfinite(result).all() else None

    def compose_profile_to_client_from_canonical(
        self,
        profile_id: str,
        map_id: str,
        floor: str,
        canonical_to_client,
    ) -> np.ndarray | None:
        adapter = self.matrix_profile_to_canonical(profile_id, map_id, floor)
        canonical = self._matrix(canonical_to_client)
        if adapter is None or canonical is None:
            return None
        result = canonical @ adapter
        return result if np.isfinite(result).all() else None


    def cross_floor_search_seed(
        self,
        profile_id: str,
        map_id: str,
        from_floor: str,
        to_floor: str,
        source_profile_to_client: Sequence[Sequence[float]] | np.ndarray,
    ) -> dict | None:
        """Project one displayed author through the neutral floor relation."""
        source_profile = self._matrix(source_profile_to_client)
        source_profile_to_canonical = self.matrix_profile_to_canonical(
            profile_id, map_id, str(from_floor),
        )
        target_profile_to_canonical = self.matrix_profile_to_canonical(
            profile_id, map_id, str(to_floor),
        )
        if (
            source_profile is None
            or source_profile_to_canonical is None
            or target_profile_to_canonical is None
        ):
            return None
        try:
            source_canonical = (
                source_profile @ np.linalg.inv(source_profile_to_canonical)
            )
        except np.linalg.LinAlgError:
            return None
        seed = self.canonical_cross_floor_search_seed(
            map_id, from_floor, to_floor, source_canonical,
            authorization_profile_id=profile_id,
        )
        if seed is None:
            return None
        canonical_target = self._matrix(seed.get("matrixSourceToClient"))
        if canonical_target is None:
            return None
        profile_target = canonical_target @ target_profile_to_canonical
        result = dict(seed)
        result.update({
            "matrixSourceToClient": profile_target.tolist(),
            "canonicalMatrixSourceToClient": canonical_target.tolist(),
            "sourceMatrixToClient": source_profile.tolist(),
            "sourceCanonicalMatrixToClient": source_canonical.tolist(),
            "mappingProfileId": profile_id,
            "matrixCoordinateProfileId": profile_id,
            "matrixCoordinatePackId": self.canonical_pack_id,
        })
        return result

    def _cross_floor_row(
        self, map_id: str, from_floor: str, to_floor: str,
        profile_id: str | None = None,
    ) -> dict | None:
        candidates = (
            self.canonical_cross_floor.get(map_id, {}).get("pairs", [])
            if self.schema == CANONICAL_SCHEMA else
            self.profiles.get(str(profile_id), {})
            .get("crossFloorSearchCandidates", {})
            .get(map_id, {})
            .get("pairs", [])
        )
        row = next(
            (
                item for item in candidates
                if str(item.get("fromFloor")) == str(from_floor)
                and str(item.get("toFloor")) == str(to_floor)
            ),
            None,
        )
        return row if isinstance(row, dict) else None

    def canonical_cross_floor_search_seed(
        self,
        map_id: str,
        from_floor: str,
        to_floor: str,
        source_canonical_to_client,
        *,
        authorization_profile_id: str | None = None,
    ) -> dict | None:
        """Build one reviewed seed in the author-neutral coordinate frame."""
        authorization_profile_id = str(
            authorization_profile_id or self.coordinate_reference_profile_id
        )
        row = self._cross_floor_row(
            map_id, str(from_floor), str(to_floor),
            authorization_profile_id,
        )
        if row is None:
            return None
        evidence = row.get("evidence") or {}
        allowed_review_statuses = {
            "player-visual-pass-current-frame-required",
            "player-visual-marginal-pass-current-frame-required",
        }
        if not (
            row.get("currentFrameSearchSeedAllowed") is True
            and row.get("directProjectionAllowed") is False
            and row.get("zeroExplorationDisplayAllowed") is False
            and row.get("liveGameVerified") is False
            and row.get("reviewStatus") in allowed_review_statuses
            and evidence.get("directProjectionWithheldReason")
        ):
            return None
        trial = self.policy.get("playerReviewedCrossFloorProjectionTrial") or {}
        projection_trial_allowed = bool(
            trial.get("enabled") is True
            and authorization_profile_id in set(trial.get("profileIds") or [])
            and row.get("reviewStatus") in set(trial.get("reviewStatuses") or [])
            and trial.get("requiresConfirmedRoundMap") is True
            and trial.get("requiresStableTargetFloor") is True
            and trial.get("requiresVerifiedSourceFloorTransform") is True
            and trial.get("projectionOnlyUntilTargetStructureVerified") is True
        )
        target_to_source = np.asarray(
            row.get("targetToSourceMatrix"), dtype=np.float64,
        )
        source = self._matrix(source_canonical_to_client)
        if (
            target_to_source.shape != (3, 3)
            or not np.isfinite(target_to_source).all()
            or source is None
        ):
            return None
        predicted = source @ target_to_source
        if not np.isfinite(predicted).all():
            return None
        return {
            "mapId": map_id,
            "fromFloor": str(from_floor),
            "toFloor": str(to_floor),
            "matrixSourceToClient": predicted.tolist(),
            "sourceMatrixToClient": source.tolist(),
            "targetToSourceMatrix": target_to_source.tolist(),
            "confidence": 0.85,
            "runtimeVerified": projection_trial_allowed,
            "sourceFloorTransformVerified": True,
            "currentFrameDisplayValidated": False,
            "directProjectionAllowed": projection_trial_allowed,
            "zeroExplorationDisplayAllowed": projection_trial_allowed,
            "profileMappingVerified": projection_trial_allowed,
            "playerReviewedProjectionTrial": projection_trial_allowed,
            "requiresStableTargetFloor": True,
            "projectionOnlyUntilTargetStructureVerified": True,
            "boundedCrossFloorOnly": not projection_trial_allowed,
            "profileCurrentFrameSearchSeed": True,
            "mappingProfileId": self.canonical_pack_id,
            "matrixCoordinatePackId": self.canonical_pack_id,
            "matrixCoordinateProfileId": self.canonical_pack_id,
            "reviewStatus": row["reviewStatus"],
            "candidateId": row.get("candidateId"),
            "transferSource": (
                "offline-player-reviewed-coordinate-binding-trial"
                if projection_trial_allowed else
                "offline-profile-reviewed-current-frame-search-seed"
            ),
            "evidence": evidence,
        }

    def audit(self) -> dict:
        usable = 0
        rejected = []
        for profile_id, profile in self.profiles.items():
            for map_id, map_row in (profile.get("maps") or {}).items():
                for floor in ("1", "2", "B1"):
                    matrix = self.matrix_profile_to_recognition(
                        profile_id, map_id, floor
                    )
                    if matrix is None:
                        rejected.append(
                            {"profileId": profile_id, "mapId": map_id, "floor": floor}
                        )
                    else:
                        usable += 1
        return {
            "success": not rejected,
            "schema": self.schema,
            "canonicalPackId": self.canonical_pack_id,
            "coordinateReferenceProfileId": self.coordinate_reference_profile_id,
            "profileCount": len(self.profiles),
            "usableAdapterCount": usable,
            "rejected": rejected,
            "migrationAudit": self._raw.get("migrationAudit") or {},
        }

    def recompute_migration_audit(self, legacy_path: Path | None = None) -> dict:
        """Recompute migration from canonical rows and embedded legacy snapshots.

        The canonical pack is auditable without a neighboring legacy file.  A
        legacy file, when present, is only an independent key/matrix cross-check.
        """
        metadata = self._raw.get("migrationAudit") or {}
        try:
            expected = int(metadata["adapterCount"]) - int(metadata["floorCount"])
        except (KeyError, TypeError, ValueError):
            return dict(success=False, source="embedded", reason="migration-metadata-invalid")
        max_round_trip = 0.0
        max_legacy = 0.0
        embedded_rows = []
        failures = []
        for map_id, map_row in self.maps.items():
            for floor, floor_row in (map_row.get("floors") or {}).items():
                chart = self._matrix((floor_row or {}).get("matrixCanonicalToLegacyRecognition"))
                if chart is None:
                    failures.append({"mapId": map_id, "floor": floor, "reason": "canonical-chart-invalid"})
                    continue
                try:
                    with np.errstate(over="raise", invalid="raise"):
                        chart_inverse = np.linalg.inv(chart)
                        round_trip = chart @ chart_inverse
                    if not np.isfinite(chart_inverse).all() or not np.isfinite(round_trip).all():
                        raise ValueError("nonfinite")
                    max_round_trip = max(max_round_trip, float(np.max(np.abs(round_trip - np.eye(3)))))
                except (FloatingPointError, ValueError, np.linalg.LinAlgError):
                    failures.append({"mapId": map_id, "floor": floor, "reason": "canonical-chart-arithmetic-invalid"})
        for profile_id, profile in self.profiles.items():
            for map_id, map_row in (profile.get("maps") or {}).items():
                for floor, floor_row in (map_row.get("floors") or {}).items():
                    snapshot = (floor_row or {}).get("migration")
                    if not isinstance(snapshot, dict):
                        continue
                    embedded_rows.append((profile_id, map_id, floor, snapshot))
                    old = self._matrix(snapshot.get("legacyMatrixProfileToRecognition"))
                    canonical = self._matrix(
                        self.maps.get(map_id, {}).get("floors", {}).get(floor, {})
                        .get("matrixCanonicalToLegacyRecognition")
                    )
                    current = self._matrix((floor_row or {}).get("matrixProfileToCanonical"))
                    if old is None or canonical is None or current is None:
                        failures.append({"profileId": profile_id, "mapId": map_id, "floor": floor, "reason": "embedded-snapshot-invalid"})
                        continue
                    try:
                        with np.errstate(over="raise", invalid="raise"):
                            composed = canonical @ current
                        if not np.isfinite(composed).all():
                            raise ValueError("nonfinite")
                        max_legacy = max(max_legacy, float(np.max(np.abs(composed - old))))
                    except (FloatingPointError, ValueError):
                        failures.append({"profileId": profile_id, "mapId": map_id, "floor": floor, "reason": "embedded-snapshot-arithmetic-invalid"})
        if len(embedded_rows) != expected:
            failures.append({"reason": "embedded-snapshot-count-mismatch", "expected": expected, "actual": len(embedded_rows)})
        source = "embedded"
        legacy_path = Path(legacy_path or self.path.parent / "nightmare-author-coordinate-adapters.json")
        if legacy_path.is_file():
            source = "embedded+external-cross-check"
            try:
                legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
                external = {}
                for profile_id, profile_row in (legacy.get("profiles") or {}).items():
                    for map_id, maps_row in (profile_row.get("maps") or {}).items():
                        for floor, old_row in (maps_row.get("floors") or {}).items():
                            external[(profile_id, map_id, floor)] = self._matrix((old_row or {}).get("matrixProfileToRecognition"))
                embedded = {(p, m, f): self._matrix(s.get("legacyMatrixProfileToRecognition")) for p, m, f, s in embedded_rows}
                if set(external) != set(embedded):
                    failures.append({"reason": "external-migration-key-mismatch"})
                for key in set(external) & set(embedded):
                    if external[key] is None or embedded[key] is None or not np.allclose(external[key], embedded[key], rtol=0, atol=0):
                        failures.append({"reason": "external-migration-matrix-mismatch", "key": list(key)})
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                failures.append({"reason": "external-migration-unreadable"})
        if max_round_trip > 2e-9:
            failures.append({"reason": "canonical-chart-roundtrip-exceeded", "error": max_round_trip})
        if max_legacy > 2e-9:
            failures.append({"reason": "embedded-migration-composition-exceeded", "error": max_legacy})
        return dict(success=not failures, source=source, legacyProfileFloorCount=len(embedded_rows),
                    expectedLegacyProfileFloorCount=expected, maximumBaseRoundTripError=max_round_trip,
                    maximumLegacyCompositionError=max_legacy, failures=failures)

    def audit_external_profile_candidate(self, candidate: dict) -> dict:
        """Audit a future user-imported route without installing it.

        Imports are expressed only as author pixels -> neutral canonical
        pixels.  Passing this structural gate creates a review candidate; it
        never grants map identity, cross-floor display, or runtime visibility.
        """
        rejected = []
        accepted = []
        if candidate.get("schema") != (
            "identityv-nightmare-user-route-canonical-candidate-v1"
        ):
            return {
                "success": False,
                "canonicalPackId": self.canonical_pack_id,
                "rejected": [{"reason": "unsupported-import-schema"}],
                "directInstallAllowed": False,
            }
        if candidate.get("canonicalPackId") != self.canonical_pack_id:
            return {
                "success": False,
                "canonicalPackId": self.canonical_pack_id,
                "rejected": [{"reason": "wrong-canonical-pack"}],
                "directInstallAllowed": False,
            }
        for map_id, map_row in (candidate.get("maps") or {}).items():
            for floor, row in ((map_row or {}).get("floors") or {}).items():
                reason = None
                matrix = self._matrix((row or {}).get("matrixProfileToCanonical"))
                evidence = (row or {}).get("evidence") or {}
                if map_id not in self.maps or floor not in {"1", "2", "B1"}:
                    reason = "unknown-map-or-floor"
                elif matrix is None:
                    reason = "invalid-profile-to-canonical-matrix"
                else:
                    singular = np.linalg.svd(matrix[:2, :2], compute_uv=False)
                    rotation = abs(float(np.degrees(np.arctan2(
                        matrix[1, 0], matrix[0, 0],
                    ))))
                    if (
                        float(np.linalg.det(matrix[:2, :2])) <= 0.0
                        or singular.min() <= 0.0
                        or singular.max() / singular.min() > 1.20
                        or rotation > 3.0
                    ):
                        reason = "implausible-import-geometry"
                    elif int(evidence.get("anchorCount", 0)) < 3:
                        reason = "insufficient-multi-anchor-evidence"
                    elif float(evidence.get("leaveOneOutMaxPixels", 999.0)) > 10.0:
                        reason = "leave-one-out-error-too-large"
                    elif evidence.get("currentFrameResidualRequired") is not True:
                        reason = "current-frame-residual-not-required"
                    elif evidence.get("humanReviewStatus") not in {
                        "pass", "marginal-pass",
                    }:
                        reason = "human-review-pending"
                record = {"mapId": map_id, "floor": floor}
                if reason:
                    rejected.append({**record, "reason": reason})
                else:
                    accepted.append(record)
        return {
            "success": bool(accepted) and not rejected,
            "canonicalPackId": self.canonical_pack_id,
            "profileId": candidate.get("profileId"),
            "acceptedFloorCount": len(accepted),
            "accepted": accepted,
            "rejected": rejected,
            "directInstallAllowed": False,
            "nextState": (
                "review-candidate-only-current-frame-validation-still-required"
            ),
        }
