from __future__ import annotations

"""Candidate-only three-floor coordinate calibration for Nightmare maps.

The canonical plane is floor 2.  A transform maps *every* point on a source
floor into that plane; image occupancy is deliberately not part of the mapping
domain.  Shared rooms, stairs and other structures are calibration evidence,
not a restriction on which coordinates may be projected.

Nothing in this module grants runtime display permission.  Saved records are
offline candidates and keep both runtime flags false by construction.
"""

import hashlib
import json
import math
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "references" / "nightmare-map-pack.json"
DEFAULT_F1_REVIEW = (
    ROOT / "work" / "all-map-cross-floor-review-20260827"
    / "all-map-review-summary.json"
)
DEFAULT_STAIR_ANCHORS = ROOT / "references" / "nightmare-stair-anchors.json"
DEFAULT_AUTHOR_ASSET_ROOT = ROOT / "outputs" / "噩梦多作者路线资产-v1"
DEFAULT_RESULT_ROOT = ROOT / "work" / "nightmare-floor-coordinate-calibration"
DEFAULT_CROSS_AUTHOR_BRIDGE = (
    DEFAULT_RESULT_ROOT / "cross-author-bridge" / "bridge-audit.json"
)
DEFAULT_CROSS_AUTHOR_HUMAN_REVIEW = (
    DEFAULT_RESULT_ROOT / "cross-author-bridge" / "human-review-feedback.json"
)
DEFAULT_ZHAN10_FIRST_AUDIT = (
    DEFAULT_RESULT_ROOT / "zhan10-first" / "zhan10-first-audit.json"
)
DEFAULT_LIANGHAPI_TRANSPORT_HUMAN_REVIEW = (
    DEFAULT_RESULT_ROOT / "zhan10-first"
    / "lianghapi-transport-human-review-feedback.json"
)
DEFAULT_LIANGHAPI_FALLBACK_HUMAN_REVIEW = (
    DEFAULT_RESULT_ROOT / "zhan10-first"
    / "lianghapi-rejected-fallback-human-review-feedback.json"
)
DEFAULT_LIANGHAPI_AXIS_AUDIT = (
    DEFAULT_RESULT_ROOT / "zhan10-first" / "lianghapi-01-07-alternative-audit.json"
)
DEFAULT_LIANGHAPI_AXIS_HUMAN_REVIEW = (
    DEFAULT_RESULT_ROOT / "zhan10-first"
    / "lianghapi-01-07-axis-human-review-feedback.json"
)
DEFAULT_REGISTRY = DEFAULT_RESULT_ROOT / "candidates.json"
DEFAULT_DRAFT = DEFAULT_RESULT_ROOT / "draft.json"

SCHEMA = "identityv-nightmare-canonical-floor-calibration-v1"
CANONICAL_FLOOR = "2"
SUPPORTED_FLOORS = ("1", "2", "B1")

ASSET_SOURCE_SPECS = {
    "lianghapi-single": {
        "displayName": "凉哈皮·单人路线（实机素材）",
        "author": "lianghapi",
        "family": "single",
        "reviewFamily": "single",
        "sourceType": "real-game-based-author-crops",
    },
    "lianghapi-duo": {
        "displayName": "凉哈皮·双人路线",
        "author": "lianghapi",
        "family": "duo",
        "reviewFamily": "duo",
        "sourceType": "real-game-based-author-crops",
    },
    "zhan10-speed": {
        "displayName": "展十·速刷路线（当前推荐）",
        "author": "zhan10",
        "family": "speed",
        "reviewFamily": None,
        "sourceType": "author-processed-route-crops",
    },
    "zhan10-coffin": {
        "displayName": "展十·全棺路线",
        "author": "zhan10",
        "family": "coffin",
        "reviewFamily": None,
        "sourceType": "author-processed-route-crops",
    },
    "wang-qingxin": {
        "displayName": "王清心（旧素材，比例差较大）",
        "author": "wang-qingxin",
        "family": "default",
        "reviewFamily": None,
        "sourceType": "legacy-external-readonly-pack",
    },
}
DEFAULT_ASSET_SOURCE = "lianghapi-single"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def identity_matrix() -> np.ndarray:
    return np.eye(3, dtype=np.float64)


def as_matrix(value: Sequence[Sequence[float]] | np.ndarray) -> np.ndarray:
    matrix = np.asarray(value, dtype=np.float64)
    if matrix.shape == (2, 3):
        matrix = np.vstack((matrix, [0.0, 0.0, 1.0]))
    if matrix.shape != (3, 3):
        raise ValueError(f"expected a 3x3 affine matrix, got {matrix.shape}")
    return matrix


def matrix_list(matrix: Sequence[Sequence[float]] | np.ndarray) -> list[list[float]]:
    return np.round(as_matrix(matrix), 10).tolist()


def apply_matrix(
    matrix: Sequence[Sequence[float]] | np.ndarray,
    points: Iterable[Sequence[float]],
) -> np.ndarray:
    pts = np.asarray(list(points), dtype=np.float64)
    if pts.size == 0:
        return np.empty((0, 2), dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise ValueError("points must be an Nx2 sequence")
    homogeneous = np.column_stack((pts, np.ones(len(pts), dtype=np.float64)))
    projected = (as_matrix(matrix) @ homogeneous.T).T
    denominator = projected[:, 2]
    if np.any(np.abs(denominator) < 1e-12):
        raise ValueError("matrix maps a point to infinity")
    return projected[:, :2] / denominator[:, None]


def invert_matrix(matrix: Sequence[Sequence[float]] | np.ndarray) -> np.ndarray:
    return np.linalg.inv(as_matrix(matrix))


def compose_floor_to_floor(
    floor_to_canonical: Mapping[str, Sequence[Sequence[float]] | np.ndarray],
    from_floor: str,
    to_floor: str,
) -> np.ndarray:
    """Derive a complete-plane mapping without consulting floor occupancy."""
    if from_floor not in floor_to_canonical or to_floor not in floor_to_canonical:
        raise KeyError(f"missing transform for {from_floor!r} or {to_floor!r}")
    return invert_matrix(floor_to_canonical[to_floor]) @ as_matrix(
        floor_to_canonical[from_floor]
    )


def derive_all_pairs(
    floor_to_canonical: Mapping[str, Sequence[Sequence[float]] | np.ndarray],
) -> dict[str, list[list[float]]]:
    result: dict[str, list[list[float]]] = {}
    for source in SUPPORTED_FLOORS:
        for target in SUPPORTED_FLOORS:
            if source == target or source not in floor_to_canonical or target not in floor_to_canonical:
                continue
            result[f"{source}-to-{target}"] = matrix_list(
                compose_floor_to_floor(floor_to_canonical, source, target)
            )
    return result


def affine_from_parameters(
    *,
    translation_x: float = 0.0,
    translation_y: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    rotation_degrees: float = 0.0,
    center: Sequence[float] = (0.0, 0.0),
) -> np.ndarray:
    theta = math.radians(float(rotation_degrees))
    cosine, sine = math.cos(theta), math.sin(theta)
    linear = np.array(
        [[cosine * scale_x, -sine * scale_y],
         [sine * scale_x, cosine * scale_y]],
        dtype=np.float64,
    )
    cx, cy = (float(value) for value in center)
    center_vector = np.array([cx, cy], dtype=np.float64)
    translation = (
        center_vector - linear @ center_vector
        + np.array([translation_x, translation_y], dtype=np.float64)
    )
    matrix = identity_matrix()
    matrix[:2, :2] = linear
    matrix[:2, 2] = translation
    return matrix


def incremental_adjustment(
    matrix: Sequence[Sequence[float]] | np.ndarray,
    *,
    translation_x: float = 0.0,
    translation_y: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    rotation_degrees: float = 0.0,
    canonical_pivot: Sequence[float] = (0.0, 0.0),
) -> np.ndarray:
    adjustment = affine_from_parameters(
        translation_x=translation_x,
        translation_y=translation_y,
        scale_x=scale_x,
        scale_y=scale_y,
        rotation_degrees=rotation_degrees,
        center=canonical_pivot,
    )
    return adjustment @ as_matrix(matrix)


def fit_affine(
    source_points: Sequence[Sequence[float]],
    canonical_points: Sequence[Sequence[float]],
) -> np.ndarray:
    source = np.asarray(source_points, dtype=np.float64)
    target = np.asarray(canonical_points, dtype=np.float64)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 2:
        raise ValueError("source and canonical points must be matching Nx2 arrays")
    if len(source) < 3:
        raise ValueError("at least three corresponding points are required")

    design = np.zeros((len(source) * 2, 6), dtype=np.float64)
    values = np.zeros(len(source) * 2, dtype=np.float64)
    for index, ((x, y), (target_x, target_y)) in enumerate(zip(source, target)):
        design[index * 2] = [x, y, 1.0, 0.0, 0.0, 0.0]
        design[index * 2 + 1] = [0.0, 0.0, 0.0, x, y, 1.0]
        values[index * 2:index * 2 + 2] = [target_x, target_y]
    if np.linalg.matrix_rank(design) < 6:
        raise ValueError("corresponding points are collinear or otherwise degenerate")
    solution, *_ = np.linalg.lstsq(design, values, rcond=None)
    matrix = np.array(
        [
            [solution[0], solution[1], solution[2]],
            [solution[3], solution[4], solution[5]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return matrix


def matrix_diagnostics(
    matrix: Sequence[Sequence[float]] | np.ndarray,
) -> dict:
    candidate = as_matrix(matrix)
    linear = candidate[:2, :2]
    finite = bool(np.isfinite(candidate).all())
    determinant = float(np.linalg.det(linear)) if finite else float("nan")
    if finite:
        singular_values = np.linalg.svd(linear, compute_uv=False)
        minimum_scale = float(np.min(singular_values))
        maximum_scale = float(np.max(singular_values))
    else:
        minimum_scale = maximum_scale = float("nan")
    anisotropy = (
        maximum_scale / minimum_scale
        if finite and minimum_scale > 1e-12
        else float("inf")
    )
    column_x, column_y = linear[:, 0], linear[:, 1]
    scale_x = float(np.linalg.norm(column_x))
    scale_y = float(np.linalg.norm(column_y))
    rotation = math.degrees(math.atan2(linear[1, 0], linear[0, 0]))
    cosine = float(np.dot(column_x, column_y) / max(scale_x * scale_y, 1e-12))
    cosine = max(-1.0, min(1.0, cosine))
    shear_degrees = 90.0 - math.degrees(math.acos(cosine))

    errors: list[str] = []
    warnings: list[str] = []
    if not finite:
        errors.append("matrix contains a non-finite value")
    if finite and not np.allclose(candidate[2], [0.0, 0.0, 1.0], atol=1e-8):
        errors.append("perspective transforms are not supported")
    if finite and determinant <= 1e-9:
        errors.append("reflection or singular transform is not allowed")
    if finite and (minimum_scale < 0.20 or maximum_scale > 5.0):
        errors.append("scale is outside the candidate safety boundary")
    if finite and anisotropy > 2.5:
        warnings.append("X/Y stretch is unusually large")
    if finite and abs(shear_degrees) > 8.0:
        warnings.append("affine shear is unusually large")
    return {
        "validCandidateGeometry": not errors,
        "errors": errors,
        "warnings": warnings,
        "determinant": round(determinant, 8) if math.isfinite(determinant) else None,
        "scaleX": round(scale_x, 8) if math.isfinite(scale_x) else None,
        "scaleY": round(scale_y, 8) if math.isfinite(scale_y) else None,
        "minimumScale": round(minimum_scale, 8) if math.isfinite(minimum_scale) else None,
        "maximumScale": round(maximum_scale, 8) if math.isfinite(maximum_scale) else None,
        "anisotropy": round(anisotropy, 8) if math.isfinite(anisotropy) else None,
        "rotationDegrees": round(rotation, 8) if math.isfinite(rotation) else None,
        "shearDegrees": round(shear_degrees, 8) if math.isfinite(shear_degrees) else None,
        "translation": [round(float(candidate[0, 2]), 5), round(float(candidate[1, 2]), 5)],
    }


def correspondence_metrics(
    matrix: Sequence[Sequence[float]] | np.ndarray,
    source_points: Sequence[Sequence[float]],
    canonical_points: Sequence[Sequence[float]],
    *,
    include_leave_one_out: bool = True,
) -> dict:
    source = np.asarray(source_points, dtype=np.float64)
    target = np.asarray(canonical_points, dtype=np.float64)
    if source.size == 0 and target.size == 0:
        return {
            "pointCount": 0,
            "medianPixels": None,
            "p90Pixels": None,
            "maximumPixels": None,
            "withinThreePixelsRatio": None,
            "leaveOneOut": None,
        }
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 2:
        raise ValueError("source and canonical points must be matching Nx2 arrays")
    residuals = np.linalg.norm(apply_matrix(matrix, source) - target, axis=1)
    leave_one_out: dict | None = None
    if include_leave_one_out and len(source) >= 4:
        held_out_residuals: list[float] = []
        skipped = 0
        for held_out in range(len(source)):
            keep = np.arange(len(source)) != held_out
            try:
                fitted = fit_affine(source[keep], target[keep])
            except ValueError:
                skipped += 1
                continue
            predicted = apply_matrix(fitted, [source[held_out]])[0]
            held_out_residuals.append(float(np.linalg.norm(predicted - target[held_out])))
        if held_out_residuals:
            rows = np.asarray(held_out_residuals, dtype=np.float64)
            leave_one_out = {
                "testedCount": len(held_out_residuals),
                "skippedCount": skipped,
                "medianPixels": round(float(np.median(rows)), 5),
                "p90Pixels": round(float(np.percentile(rows, 90)), 5),
                "maximumPixels": round(float(np.max(rows)), 5),
            }
    return {
        "pointCount": int(len(source)),
        "medianPixels": round(float(np.median(residuals)), 5),
        "p90Pixels": round(float(np.percentile(residuals, 90)), 5),
        "maximumPixels": round(float(np.max(residuals)), 5),
        "withinThreePixelsRatio": round(float(np.mean(residuals <= 3.0)), 5),
        "leaveOneOut": leave_one_out,
    }


def round_trip_metrics(
    floor_to_canonical: Mapping[str, Sequence[Sequence[float]] | np.ndarray],
    image_sizes: Mapping[str, Sequence[int]],
) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for source in SUPPORTED_FLOORS:
        if source not in floor_to_canonical or source not in image_sizes:
            continue
        width, height = (float(value) for value in image_sizes[source])
        points = np.array(
            [[0.0, 0.0], [width, 0.0], [0.0, height], [width, height], [width / 2.0, height / 2.0]],
            dtype=np.float64,
        )
        for target in SUPPORTED_FLOORS:
            if source == target or target not in floor_to_canonical:
                continue
            forward = compose_floor_to_floor(floor_to_canonical, source, target)
            reverse = compose_floor_to_floor(floor_to_canonical, target, source)
            restored = apply_matrix(reverse, apply_matrix(forward, points))
            residuals = np.linalg.norm(restored - points, axis=1)
            result[f"{source}-to-{target}-to-{source}"] = {
                "maximumPixels": round(float(np.max(residuals)), 10),
                "medianPixels": round(float(np.median(residuals)), 10),
            }
    return result


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        Path(temp_name).replace(path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_map_pack(path: Path = DEFAULT_MANIFEST) -> dict:
    manifest = load_json(path)
    source_root = Path(manifest["sourceRoot"])
    maps: list[dict] = []
    for row in manifest["maps"]:
        layers = {floor: source_root / Path(relative) for floor, relative in row["layers"].items()}
        maps.append(
            {
                "mapId": row["id"],
                "mapName": row["name"],
                "layers": layers,
            }
        )
    return {
        "packId": manifest["packId"],
        "displayName": manifest["displayName"],
        "assetSourceId": "wang-qingxin",
        "assetSourceDisplayName": ASSET_SOURCE_SPECS["wang-qingxin"]["displayName"],
        "routeFamily": "default",
        "sourceType": ASSET_SOURCE_SPECS["wang-qingxin"]["sourceType"],
        "sourceRoot": source_root,
        "maps": maps,
    }


def load_author_asset_pack(
    asset_source_id: str,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    asset_root: Path = DEFAULT_AUTHOR_ASSET_ROOT,
) -> dict:
    spec = ASSET_SOURCE_SPECS.get(asset_source_id)
    if spec is None or asset_source_id == "wang-qingxin":
        raise ValueError(f"not an author asset source: {asset_source_id!r}")
    manifest = load_json(manifest_path)
    source_root = asset_root / spec["author"] / spec["family"]
    maps: list[dict] = []
    for row in manifest["maps"]:
        variant_root = source_root / row["id"] / "variant-01"
        layers = {
            "1": variant_root / "floor-1.png",
            "2": variant_root / "floor-2.png",
            "B1": variant_root / "floor-B1.png",
        }
        maps.append({"mapId": row["id"], "mapName": row["name"], "layers": layers})
    return {
        "packId": f"nightmare-multi-author-v1:{spec['author']}:{spec['family']}:variant-01",
        "displayName": spec["displayName"],
        "assetSourceId": asset_source_id,
        "assetSourceDisplayName": spec["displayName"],
        "routeFamily": spec["family"],
        "sourceType": spec["sourceType"],
        "sourceRoot": source_root,
        "maps": maps,
    }


def load_asset_pack(
    asset_source_id: str = DEFAULT_ASSET_SOURCE,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    asset_root: Path = DEFAULT_AUTHOR_ASSET_ROOT,
) -> dict:
    if asset_source_id == "wang-qingxin":
        return load_map_pack(manifest_path)
    return load_author_asset_pack(
        asset_source_id, manifest_path=manifest_path, asset_root=asset_root
    )


def draft_path_for_source(asset_source_id: str) -> Path:
    safe = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in asset_source_id
    )
    return DEFAULT_RESULT_ROOT / f"draft-{safe}.json"


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def _centered_seed(source_size: Sequence[int], target_size: Sequence[int]) -> np.ndarray:
    source_width, source_height = (float(value) for value in source_size)
    target_width, target_height = (float(value) for value in target_size)
    scale = min(target_width / source_width, target_height / source_height)
    tx = (target_width - source_width * scale) / 2.0
    ty = (target_height - source_height * scale) / 2.0
    return np.array([[scale, 0.0, tx], [0.0, scale, ty], [0.0, 0.0, 1.0]])


def _transfer_from_to_matrix(transfer: dict) -> np.ndarray:
    """Return fromFloor -> toFloor from the legacy target->source matrix."""
    return invert_matrix(transfer["targetToSourceMatrix"])


def build_seed_sessions(
    asset_source_id: str = DEFAULT_ASSET_SOURCE,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    review_path: Path = DEFAULT_F1_REVIEW,
    stair_path: Path = DEFAULT_STAIR_ANCHORS,
    author_asset_root: Path = DEFAULT_AUTHOR_ASSET_ROOT,
    cross_author_bridge_path: Path = DEFAULT_CROSS_AUTHOR_BRIDGE,
    cross_author_human_review_path: Path = DEFAULT_CROSS_AUTHOR_HUMAN_REVIEW,
    zhan10_first_audit_path: Path = DEFAULT_ZHAN10_FIRST_AUDIT,
    lianghapi_transport_human_review_path: Path = DEFAULT_LIANGHAPI_TRANSPORT_HUMAN_REVIEW,
    lianghapi_fallback_human_review_path: Path = DEFAULT_LIANGHAPI_FALLBACK_HUMAN_REVIEW,
    lianghapi_axis_audit_path: Path = DEFAULT_LIANGHAPI_AXIS_AUDIT,
    lianghapi_axis_human_review_path: Path = DEFAULT_LIANGHAPI_AXIS_HUMAN_REVIEW,
) -> dict[str, dict]:
    pack = load_asset_pack(
        asset_source_id,
        manifest_path=manifest_path,
        asset_root=author_asset_root,
    )
    review_by_id = {
        row["mapId"]: row for row in load_json(review_path).get("maps", [])
    } if review_path.is_file() else {}
    stair_by_id = {
        row["mapId"]: row for row in load_json(stair_path).get("maps", [])
    } if stair_path.is_file() else {}
    bridge_by_id = {
        row["mapId"]: row
        for row in load_json(cross_author_bridge_path).get("maps", [])
    } if cross_author_bridge_path.is_file() else {}
    human_review = (
        load_json(cross_author_human_review_path)
        if cross_author_human_review_path.is_file() else {}
    )
    human_passed = set(human_review.get("passedMaps", []))
    zhan_by_id = {
        row["mapId"]: row
        for row in load_json(zhan10_first_audit_path).get("maps", [])
    } if zhan10_first_audit_path.is_file() else {}
    transport_review = (
        load_json(lianghapi_transport_human_review_path)
        if lianghapi_transport_human_review_path.is_file() else {}
    )
    transport_passed = set(transport_review.get("passedMaps", []))
    transport_marginal = set(transport_review.get("marginalMaps", []))
    transport_rejected = set(transport_review.get("rejectedMaps", []))
    transport_allowed = transport_passed | transport_marginal
    fallback_review = (
        load_json(lianghapi_fallback_human_review_path)
        if lianghapi_fallback_human_review_path.is_file() else {}
    )
    fallback_decisions = fallback_review.get("decisions", {})
    author_direct_passed = {
        map_id
        for map_id, decision in fallback_decisions.items()
        if decision.get("reviewedSeedKind") == "author-internal-current-multipoint-candidate"
        and decision.get("status") == "pass"
    }
    author_direct_marginal = {
        map_id
        for map_id, decision in fallback_decisions.items()
        if decision.get("reviewedSeedKind") == "author-internal-current-multipoint-candidate"
        and decision.get("status") == "marginal-pass"
    }
    author_direct_rejected = {
        map_id
        for map_id, decision in fallback_decisions.items()
        if decision.get("reviewedSeedKind") == "author-internal-current-multipoint-candidate"
        and decision.get("status") == "rejected"
    }
    axis_audit = (
        load_json(lianghapi_axis_audit_path)
        if lianghapi_axis_audit_path.is_file() else {}
    )
    axis_candidates = {
        (row.get("mapId"), row.get("candidateId")): row
        for row in axis_audit.get("candidates", [])
    }
    axis_review = (
        load_json(lianghapi_axis_human_review_path)
        if lianghapi_axis_human_review_path.is_file() else {}
    )
    axis_passed = {
        map_id: decision.get("candidateId")
        for map_id, decision in axis_review.get("decisions", {}).items()
        if decision.get("status") == "pass" and decision.get("candidateId")
    }
    sessions: dict[str, dict] = {}
    for map_row in pack["maps"]:
        map_id = map_row["mapId"]
        layers: dict[str, Path] = map_row["layers"]
        missing = [floor for floor in SUPPORTED_FLOORS if not layers[floor].is_file()]
        if missing:
            raise FileNotFoundError(f"{map_id} missing layers: {', '.join(missing)}")
        sizes = {floor: _image_size(layers[floor]) for floor in SUPPORTED_FLOORS}
        transforms: dict[str, np.ndarray] = {CANONICAL_FLOOR: identity_matrix()}
        seed_sources: dict[str, dict] = {
            CANONICAL_FLOOR: {"kind": "canonical-identity", "reviewStatus": "definition"}
        }

        review = review_by_id.get(map_id, {})
        source_spec = ASSET_SOURCE_SPECS[asset_source_id]
        review_family = source_spec.get("reviewFamily")
        family_row = next(
            (
                row for row in review.get("families", [])
                if row.get("family") == review_family
            ),
            None,
        )
        bridge_floor1 = (
            bridge_by_id.get(map_id, {})
            .get("lianghapiSingleFloorToFloor2", {})
            .get("1", {})
            if asset_source_id == "lianghapi-single"
            else {}
        )
        author_direct_floor1 = (
            bridge_by_id.get(map_id, {})
            .get("withinAuthorCrossFloorAttempts", {})
            .get("lianghapi-single", {})
            .get("1-to-2", {})
            if asset_source_id == "lianghapi-single"
            else {}
        )

        def zhan_first_matrix(floor: str) -> tuple[np.ndarray | None, dict]:
            floor_row = zhan_by_id.get(map_id, {}).get("floors", {}).get(floor, {})
            consensus = floor_row.get("zhanCanonicalConsensus", {})
            if (
                not consensus.get("status", "").startswith("review-ready")
                or not consensus.get("matrix")
            ):
                return None, consensus
            zhan_matrix = as_matrix(consensus["matrix"])
            if asset_source_id == "zhan10-speed":
                return zhan_matrix, consensus
            same_floor = bridge_by_id.get(map_id, {}).get("sameFloorAuthorBridges", {})
            if asset_source_id in {
                "zhan10-coffin", "lianghapi-single", "lianghapi-duo"
            }:
                source_bridge = same_floor.get(floor, {}).get(asset_source_id, {})
                target_bridge = same_floor.get(CANONICAL_FLOOR, {}).get(asset_source_id, {})
                if (
                    source_bridge.get("acceptedAsEvidence")
                    and target_bridge.get("acceptedAsEvidence")
                    and source_bridge.get("matrix")
                    and target_bridge.get("matrix")
                ):
                    return (
                        invert_matrix(target_bridge["matrix"])
                        @ zhan_matrix
                        @ as_matrix(source_bridge["matrix"]),
                        consensus,
                    )
            return None, consensus

        zhan_floor1_matrix, zhan_floor1_evidence = zhan_first_matrix("1")
        if (
            map_id in human_passed
            and bridge_floor1.get("status", "").startswith("review-ready")
            and bridge_floor1.get("matrixFloorToCanonical")
        ):
            transforms["1"] = as_matrix(bridge_floor1["matrixFloorToCanonical"])
            seed_sources["1"] = {
                "kind": "cross-author-same-floor-bridge-human-reviewed",
                "assetSourceId": asset_source_id,
                "supportAuthors": bridge_floor1.get("supportAuthors", []),
                "supportLabels": bridge_floor1.get("supportLabels", []),
                "consensusSpread": bridge_floor1.get("consensusSpread"),
                "reviewStatus": "human-visual-review-passed-live-game-not-verified",
                "directProjectionAllowed": False,
            }
        elif (
            zhan_floor1_matrix is not None
            and (
                asset_source_id != "lianghapi-single"
                or map_id in transport_allowed
            )
        ):
            transforms["1"] = zhan_floor1_matrix
            seed_sources["1"] = {
                "kind": (
                    "zhan10-transport-human-reviewed"
                    if asset_source_id == "lianghapi-single" and map_id in transport_passed
                    else "zhan10-original-scale-global-coordinate-candidate"
                ),
                "assetSourceId": asset_source_id,
                "supportRoutes": zhan_floor1_evidence.get("supportRoutes", []),
                "routeAgreement": zhan_floor1_evidence.get("routeAgreement"),
                "reviewStatus": (
                    "human-visual-review-passed-live-game-not-verified"
                    if asset_source_id == "lianghapi-single" and map_id in transport_passed
                    else (
                        "human-visual-review-marginal-live-game-not-verified"
                        if asset_source_id == "lianghapi-single" and map_id in transport_marginal
                        else "offline-review-ready-candidate-not-human-approved"
                    )
                ),
                "directProjectionAllowed": False,
            }
        elif (
            bridge_floor1.get("status", "").startswith("review-ready")
            and bridge_floor1.get("matrixFloorToCanonical")
            and not (
                asset_source_id == "lianghapi-single"
                and map_id in transport_rejected
            )
        ):
            transforms["1"] = as_matrix(bridge_floor1["matrixFloorToCanonical"])
            seed_sources["1"] = {
                "kind": "cross-author-same-floor-bridge-consensus",
                "assetSourceId": asset_source_id,
                "supportAuthors": bridge_floor1.get("supportAuthors", []),
                "supportLabels": bridge_floor1.get("supportLabels", []),
                "consensusSpread": bridge_floor1.get("consensusSpread"),
                "reviewStatus": "offline-review-ready-candidate-not-human-approved",
                "directProjectionAllowed": False,
            }
        elif (
            asset_source_id == "lianghapi-single"
            and map_id in transport_rejected
            and map_id not in author_direct_rejected
            and author_direct_floor1.get("acceptedAsEvidence")
            and author_direct_floor1.get("matrix")
        ):
            transforms["1"] = as_matrix(author_direct_floor1["matrix"])
            seed_sources["1"] = {
                "kind": "author-internal-current-multipoint-candidate",
                "assetSourceId": asset_source_id,
                "mutualMatches": author_direct_floor1.get("mutualMatches"),
                "ransacInliers": author_direct_floor1.get("inlierMatches"),
                "spatialAnchorGroups": author_direct_floor1.get("spatialAnchorGroups"),
                "residual": author_direct_floor1.get("residual"),
                "reviewStatus": (
                    "human-visual-review-passed-live-game-not-verified"
                    if map_id in author_direct_passed
                    else (
                        "human-visual-review-marginal-live-game-not-verified"
                        if map_id in author_direct_marginal
                        else "replacement-candidate-after-zhan-transport-rejected"
                    )
                ),
                "directProjectionAllowed": False,
            }
        elif (
            asset_source_id == "lianghapi-single"
            and map_id in author_direct_rejected
            and (map_id, axis_passed.get(map_id)) in axis_candidates
        ):
            axis_candidate = axis_candidates[(map_id, axis_passed[map_id])]
            transforms["1"] = as_matrix(axis_candidate["matrixFloor1ToFloor2"])
            seed_sources["1"] = {
                "kind": "axis-locked-route-transport-human-reviewed",
                "assetSourceId": asset_source_id,
                "candidateId": axis_candidate["candidateId"],
                "matrixSummary": axis_candidate.get("matrixSummary"),
                "reviewStatus": "human-visual-review-passed-live-game-not-verified",
                "directProjectionAllowed": False,
            }
        elif (
            asset_source_id == "lianghapi-single"
            and map_id in author_direct_rejected
        ):
            transforms["1"] = _centered_seed(sizes["1"], sizes["2"])
            seed_sources["1"] = {
                "kind": "rejected-reviewed-candidates-safe-centered-fallback",
                "assetSourceId": asset_source_id,
                "reviewStatus": "all-reviewed-floor1-candidates-rejected-safe-floating-only",
                "directProjectionAllowed": False,
            }
        elif family_row and family_row.get("realMatrixFloor1ToFloor2"):
            transforms["1"] = as_matrix(family_row["realMatrixFloor1ToFloor2"])
            seed_sources["1"] = {
                "kind": "author-internal-multipoint-candidate",
                "assetSourceId": asset_source_id,
                "family": review_family,
                "mutualMatches": family_row.get("mutualMatches"),
                "ransacInliers": family_row.get("ransacInliers"),
                "reviewStatus": "automatic-author-internal-seed-not-human-approved",
                "directProjectionAllowed": False,
            }
        elif asset_source_id == "wang-qingxin" and review.get("selectedMatrixFloor1ToFloor2"):
            transforms["1"] = as_matrix(review["selectedMatrixFloor1ToFloor2"])
            seed_sources["1"] = {
                "kind": "all-map-review-selected-candidate",
                "classification": review.get("classification"),
                "selectedCandidate": review.get("selectedCandidate"),
                "reviewStatus": review.get("reviewStatus"),
                "directProjectionAllowed": False,
            }
        else:
            transforms["1"] = _centered_seed(sizes["1"], sizes["2"])
            seed_sources["1"] = {
                "kind": "centered-size-fallback",
                "assetSourceId": asset_source_id,
                "reviewStatus": "unverified-initialization-only",
                "directProjectionAllowed": False,
            }

        bridge_b1 = (
            bridge_by_id.get(map_id, {})
            .get("lianghapiSingleFloorToFloor2", {})
            .get("B1", {})
            if asset_source_id == "lianghapi-single"
            else {}
        )
        zhan_b1_matrix, zhan_b1_evidence = zhan_first_matrix("B1")
        transfers = (
            stair_by_id.get(map_id, {}).get("crossFloorTransfers", [])
            if asset_source_id == "wang-qingxin"
            else []
        )
        b1_to_one: np.ndarray | None = None
        transfer_evidence: dict | None = None
        for transfer in transfers:
            if not transfer.get("usable"):
                continue
            source, target = transfer.get("fromFloor"), transfer.get("toFloor")
            desired = _transfer_from_to_matrix(transfer)
            if source == "B1" and target == "1":
                b1_to_one = desired
                transfer_evidence = transfer
                break
            if source == "1" and target == "B1":
                b1_to_one = invert_matrix(desired)
                transfer_evidence = transfer
        if zhan_b1_matrix is not None:
            transforms["B1"] = zhan_b1_matrix
            seed_sources["B1"] = {
                "kind": "zhan10-original-scale-global-coordinate-candidate",
                "assetSourceId": asset_source_id,
                "supportRoutes": zhan_b1_evidence.get("supportRoutes", []),
                "routeAgreement": zhan_b1_evidence.get("routeAgreement"),
                "reviewStatus": "offline-review-ready-candidate-not-human-approved",
                "directProjectionAllowed": False,
            }
        elif (
            bridge_b1.get("status", "").startswith("review-ready")
            and bridge_b1.get("matrixFloorToCanonical")
        ):
            transforms["B1"] = as_matrix(bridge_b1["matrixFloorToCanonical"])
            seed_sources["B1"] = {
                "kind": "cross-author-same-floor-bridge-consensus",
                "assetSourceId": asset_source_id,
                "supportAuthors": bridge_b1.get("supportAuthors", []),
                "supportLabels": bridge_b1.get("supportLabels", []),
                "consensusSpread": bridge_b1.get("consensusSpread"),
                "reviewStatus": "offline-review-ready-candidate-not-human-approved",
                "directProjectionAllowed": False,
            }
        elif b1_to_one is not None:
            transforms["B1"] = transforms["1"] @ b1_to_one
            seed_sources["B1"] = {
                "kind": "legacy-shared-structure-via-floor-1",
                "fromLegacyPair": [transfer_evidence.get("fromFloor"), transfer_evidence.get("toFloor")],
                "confidence": transfer_evidence.get("confidence"),
                "medianResidualPixels": transfer_evidence.get("medianResidualPixels"),
                "p90ResidualPixels": transfer_evidence.get("p90ResidualPixels"),
                "reviewStatus": "unverified-initialization-only",
                "directProjectionAllowed": False,
            }
        else:
            transforms["B1"] = _centered_seed(sizes["B1"], sizes["2"])
            seed_sources["B1"] = {
                "kind": "centered-size-fallback",
                "assetSourceId": asset_source_id,
                "reviewStatus": "unverified-initialization-only",
                "directProjectionAllowed": False,
            }
        sessions[map_id] = {
            "mapId": map_id,
            "mapName": map_row["mapName"],
            "packId": pack["packId"],
            "assetSourceId": pack["assetSourceId"],
            "assetSourceDisplayName": pack["assetSourceDisplayName"],
            "routeFamily": pack["routeFamily"],
            "sourceType": pack["sourceType"],
            "layers": layers,
            "imageSizes": sizes,
            "floorToCanonical": transforms,
            "seedSources": seed_sources,
            "controlPoints": {"1": [], "B1": []},
        }
        seed_payload = {
            "packId": pack["packId"],
            "mapId": map_id,
            "matrices": {
                floor: matrix_list(matrix) for floor, matrix in transforms.items()
            },
            "kinds": {
                floor: row.get("kind") for floor, row in seed_sources.items()
            },
        }
        sessions[map_id]["seedRevision"] = hashlib.sha256(
            json.dumps(seed_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
    return sessions


def build_candidate_record(session: dict, *, note: str = "") -> dict:
    transforms = {
        floor: as_matrix(matrix)
        for floor, matrix in session["floorToCanonical"].items()
    }
    if CANONICAL_FLOOR not in transforms or not np.allclose(
        transforms[CANONICAL_FLOOR], identity_matrix(), atol=1e-9
    ):
        raise ValueError("floor 2 must remain the canonical identity transform")
    floor_rows: dict[str, dict] = {}
    all_valid = True
    for floor in SUPPORTED_FLOORS:
        matrix = transforms[floor]
        diagnostics = matrix_diagnostics(matrix)
        all_valid = all_valid and bool(diagnostics["validCandidateGeometry"])
        points = session.get("controlPoints", {}).get(floor, [])
        source_points = [row["source"] for row in points]
        canonical_points = [row["canonical"] for row in points]
        validation = correspondence_metrics(
            matrix, source_points, canonical_points
        ) if points else correspondence_metrics(matrix, [], [])
        layer_path = Path(session["layers"][floor])
        floor_rows[floor] = {
            "state": "canonical-definition" if floor == CANONICAL_FLOOR else "candidate",
            "runtimeEnabled": False,
            "approvedForRuntimeDirectProjection": False,
            "matrixFloorToCanonical": matrix_list(matrix),
            "imageSize": list(session["imageSizes"][floor]),
            "asset": {
                "path": str(layer_path),
                "sha256": file_sha256(layer_path),
            },
            "seedSource": session.get("seedSources", {}).get(floor),
            "controlPoints": points,
            "validation": validation,
            "geometry": diagnostics,
        }
    record_id = (
        f"{session.get('assetSourceId', 'unknown')}-{session['mapId']}-"
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    )
    return {
        "recordId": record_id,
        "createdAt": now_iso(),
        "mapId": session["mapId"],
        "mapName": session["mapName"],
        "packId": session["packId"],
        "assetSourceId": session.get("assetSourceId", "wang-qingxin"),
        "assetSourceDisplayName": session.get("assetSourceDisplayName", session["packId"]),
        "routeFamily": session.get("routeFamily"),
        "sourceType": session.get("sourceType"),
        "canonicalFloor": CANONICAL_FLOOR,
        "state": "candidate" if all_valid else "candidate-invalid-geometry",
        "runtimeEnabled": False,
        "approvedForRuntimeDirectProjection": False,
        "note": note,
        "policy": {
            "completeCoordinatePlaneAcrossFloors": True,
            "sharedStructuresAreCalibrationEvidenceOnly": True,
            "candidateOnly": True,
            "runtimeRequiresSeparateHumanReviewAndCurrentFrameValidation": True,
        },
        "floors": floor_rows,
        "derivedPairs": derive_all_pairs(transforms),
        "roundTrip": round_trip_metrics(transforms, session["imageSizes"]),
    }


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict:
    if not path.is_file():
        return {
            "schema": SCHEMA,
            "canonicalFloor": CANONICAL_FLOOR,
            "policy": {
                "candidateOnly": True,
                "runtimePermissionIsNeverGrantedHere": True,
            },
            "records": [],
            "recordCount": 0,
            "mapCount": 0,
        }
    payload = load_json(path)
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"unsupported registry schema: {payload.get('schema')!r}")
    return payload


def append_candidate(record: dict, path: Path = DEFAULT_REGISTRY) -> dict:
    if record.get("runtimeEnabled") is not False:
        raise ValueError("candidate registry refuses runtime-enabled records")
    if record.get("approvedForRuntimeDirectProjection") is not False:
        raise ValueError("candidate registry refuses direct-projection approval")
    for floor_row in record.get("floors", {}).values():
        if floor_row.get("runtimeEnabled") is not False:
            raise ValueError("candidate floor may not be runtime enabled")
        if floor_row.get("approvedForRuntimeDirectProjection") is not False:
            raise ValueError("candidate floor may not approve direct projection")
    payload = load_registry(path)
    records = list(payload.get("records", []))
    records.append(record)
    payload["records"] = records
    payload["recordCount"] = len(records)
    payload["mapCount"] = len({row.get("mapId") for row in records})
    payload["assetMapCount"] = len(
        {(row.get("assetSourceId", "wang-qingxin"), row.get("mapId")) for row in records}
    )
    payload["updatedAt"] = now_iso()
    atomic_write_json(path, payload)
    return payload


def latest_record_by_map(path: Path = DEFAULT_REGISTRY) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for record in load_registry(path).get("records", []):
        latest[record["mapId"]] = record
    return latest


def latest_record_by_source_map(
    path: Path = DEFAULT_REGISTRY,
) -> dict[tuple[str, str], dict]:
    latest: dict[tuple[str, str], dict] = {}
    for record in load_registry(path).get("records", []):
        source_id = record.get("assetSourceId", "wang-qingxin")
        latest[(source_id, record["mapId"])] = record
    return latest


def apply_record_to_session(session: dict, record: dict) -> None:
    if record.get("mapId") != session.get("mapId"):
        raise ValueError("record and session map IDs do not match")
    record_source = record.get("assetSourceId", "wang-qingxin")
    session_source = session.get("assetSourceId", "wang-qingxin")
    if record_source != session_source:
        raise ValueError("record and session asset sources do not match")
    for floor in SUPPORTED_FLOORS:
        row = record["floors"][floor]
        session["floorToCanonical"][floor] = as_matrix(row["matrixFloorToCanonical"])
        if floor != CANONICAL_FLOOR:
            session["controlPoints"][floor] = list(row.get("controlPoints", []))


def save_draft(sessions: Mapping[str, dict], path: Path = DEFAULT_DRAFT) -> None:
    maps: list[dict] = []
    source_ids = {session.get("assetSourceId", "wang-qingxin") for session in sessions.values()}
    if len(source_ids) > 1:
        raise ValueError("one draft file may contain only one asset source")
    asset_source_id = next(iter(source_ids), "wang-qingxin")
    for session in sessions.values():
        maps.append(
            {
                "mapId": session["mapId"],
                "baseSeedRevision": session.get("seedRevision"),
                "floorToCanonical": {
                    floor: matrix_list(matrix)
                    for floor, matrix in session["floorToCanonical"].items()
                },
                "controlPoints": session.get("controlPoints", {}),
            }
        )
    atomic_write_json(
        path,
        {
            "schema": SCHEMA + "-draft",
            "updatedAt": now_iso(),
            "canonicalFloor": CANONICAL_FLOOR,
            "assetSourceId": asset_source_id,
            "runtimeEnabled": False,
            "maps": maps,
        },
    )


def load_draft_into_sessions(
    sessions: Mapping[str, dict], path: Path = DEFAULT_DRAFT
) -> int:
    if not path.is_file():
        return 0
    payload = load_json(path)
    if payload.get("schema") != SCHEMA + "-draft":
        return 0
    expected_source = next(
        (session.get("assetSourceId", "wang-qingxin") for session in sessions.values()),
        "wang-qingxin",
    )
    if payload.get("assetSourceId", "wang-qingxin") != expected_source:
        return 0
    loaded = 0
    for row in payload.get("maps", []):
        session = sessions.get(row.get("mapId"))
        if session is None:
            continue
        if not row.get("baseSeedRevision") or row.get("baseSeedRevision") != session.get("seedRevision"):
            # Preserve stale drafts on disk but never silently apply edits made
            # against a different author asset or automatic seed revision.
            continue
        for floor, matrix in row.get("floorToCanonical", {}).items():
            if floor in SUPPORTED_FLOORS:
                session["floorToCanonical"][floor] = as_matrix(matrix)
        session["floorToCanonical"][CANONICAL_FLOOR] = identity_matrix()
        for floor in ("1", "B1"):
            session["controlPoints"][floor] = list(
                row.get("controlPoints", {}).get(floor, [])
            )
        loaded += 1
    return loaded
