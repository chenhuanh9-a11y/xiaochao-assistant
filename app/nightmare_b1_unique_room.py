from __future__ import annotations

from dataclasses import dataclass
import time

import cv2
import numpy as np


@dataclass(frozen=True)
class B1UniqueRoomSpec:
    """A reviewed, map-local B1 room shape used only after map confirmation."""

    map_id: str
    source_bounds: tuple[int, int, int, int]
    neck_direction: str
    source_size: tuple[int, int]


# This is deliberately a whitelist, not a claim that every B1 layer has one
# safe room anchor.  The reviewed map-05 altar/coffin room and map-10 altar
# room each survive the outline gates at their own source rectangle; other
# visible room outlines are a different room or remain unreviewed.
B1_UNIQUE_ROOM_SPECS: tuple[B1UniqueRoomSpec, ...] = (
    B1UniqueRoomSpec(
        map_id="nightmare-05",
        source_bounds=(343, 371, 153, 123),
        neck_direction="right",
        source_size=(692, 612),
    ),
    B1UniqueRoomSpec(
        map_id="nightmare-10",
        source_bounds=(175, 402, 132, 156),
        neck_direction="down",
        source_size=(594, 602),
    ),
)

_SPEC_BY_MAP = {spec.map_id: spec for spec in B1_UNIQUE_ROOM_SPECS}


def b1_unique_room_anchor_allowed(
    floor: str,
    wide_recovery_evidence: str | None,
    *,
    worker_wide_first: bool,
    allow_wide_fallback: bool,
    residual_verified: bool,
) -> bool:
    """Keep the room probe off stable local reopen and settled motion paths."""
    return bool(
        str(floor) == "B1"
        and str(wide_recovery_evidence or "") in {
            "floor-transition", "position-jump", "display-reprojection",
            "continuous-local-failure",
        }
        and (bool(worker_wide_first) or bool(allow_wide_fallback))
        and not bool(residual_verified)
    )


def b1_unique_room_result_is_consumable(result: dict | None) -> bool:
    """Only a verified matcher result may reach the route residual gate."""
    if not isinstance(result, dict) or result.get("success") is not True:
        return False
    matrix = np.asarray(result.get("matrixSourceToClient"), dtype=np.float64)
    return bool(
        matrix.shape == (3, 3)
        and np.isfinite(matrix).all()
        and result.get("translationOnly") is True
    )


def _yellow_outline(image: np.ndarray) -> np.ndarray:
    if image is None or image.ndim != 3 or image.shape[2] < 3:
        return np.zeros((0, 0), dtype=np.uint8)
    hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2HSV)
    return cv2.inRange(
        hsv,
        np.asarray([18, 100, 120], dtype=np.uint8),
        np.asarray([40, 255, 255], dtype=np.uint8),
    )


def _binary_edges(image: np.ndarray) -> np.ndarray:
    if image is None:
        return np.zeros((0, 0), dtype=np.uint8)
    if image.ndim == 2:
        # Binary masks are also used by the deterministic transform tests;
        # retaining them avoids introducing a Canny edge offset into the
        # translation result.  Real runtime frames are BGR and use Canny.
        values = np.unique(image) if image.size else np.asarray([])
        if values.size <= 3:
            return (image > 0).astype(np.uint8) * 255
        gray = image
    elif image.ndim == 3 and image.shape[2] >= 3:
        gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    else:
        return np.zeros((0, 0), dtype=np.uint8)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.Canny(gray, 35, 105)


def _side_span_profile(mask: np.ndarray) -> dict[str, float]:
    """Measure how much of each outer side is occupied by the outline."""
    if mask is None or mask.size == 0:
        return {side: 0.0 for side in ("up", "down", "left", "right")}
    small = cv2.resize(
        (mask > 0).astype(np.uint8), (64, 64), interpolation=cv2.INTER_AREA,
    ) >= 0.35
    result: dict[str, float] = {}
    for side in ("up", "down"):
        band = small[:16] if side == "up" else small[-16:]
        result[side] = float(np.any(band, axis=0).sum()) / 64.0
    for side in ("left", "right"):
        band = small[:, :16] if side == "left" else small[:, -16:]
        result[side] = float(np.any(band, axis=1).sum()) / 64.0
    return result


def classify_b1_room_outline(mask: np.ndarray) -> dict:
    """Classify one outline, rejecting rectangles and corridor fragments.

    The direction names the side containing the narrow rectangular neck.  The
    classifier intentionally uses only the local outline; room color and route
    paint are not identity evidence.
    """
    profile = _side_span_profile(mask)
    if mask is None or mask.size == 0:
        return {
            "qualified": False, "direction": None, "reason": "empty-outline",
            "profile": profile,
        }
    height, width = mask.shape[:2]
    if min(height, width) < 24:
        return {
            "qualified": False, "direction": None,
            "reason": "outline-too-small", "profile": profile,
        }
    aspect = max(width / max(height, 1), height / max(width, 1))
    if aspect > 2.35:
        return {
            "qualified": False, "direction": None,
            "reason": "corridor-like-outline", "profile": profile,
        }
    narrow = sorted(profile.items(), key=lambda item: item[1])
    direction, narrow_span = narrow[0]
    runner_span = narrow[1][1]
    qualified = bool(
        narrow_span <= 0.58
        and runner_span >= narrow_span + 0.10
        and max(profile.values()) >= 0.72
    )
    return {
        "qualified": qualified,
        "direction": direction if qualified else None,
        "reason": "single-neck-room" if qualified else "ordinary-or-ambiguous-outline",
        "profile": profile,
        "aspectRatio": float(aspect),
        "neckSpan": float(narrow_span),
        "runnerUpSpan": float(runner_span),
    }


def _crop_bounds(mask: np.ndarray, bounds: tuple[int, int, int, int]) -> np.ndarray | None:
    if mask is None or mask.ndim != 2:
        return None
    x, y, width, height = (int(value) for value in bounds)
    if width < 24 or height < 24 or x < 0 or y < 0:
        return None
    if x + width > mask.shape[1] or y + height > mask.shape[0]:
        return None
    crop = mask[y:y + height, x:x + width]
    if int(np.count_nonzero(crop)) < 120:
        return None
    # Yellow room borders can touch a route arrow or a nearby annotation.
    # Keep the largest connected local outline instead of allowing those
    # pixels to change the neck direction.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(crop, 8)
    if count > 1:
        label = max(range(1, count), key=lambda item: int(stats[item, cv2.CC_STAT_AREA]))
        crop = (labels == label).astype(np.uint8) * 255
    return crop


def inspect_b1_unique_room_reference(
    image: np.ndarray | None,
    spec: B1UniqueRoomSpec,
) -> dict:
    """Return auditable evidence for one enabled reference spec."""
    if image is None or image.ndim != 3:
        return {"qualified": False, "reason": "reference-missing"}
    actual_size = (int(image.shape[1]), int(image.shape[0]))
    if actual_size != tuple(spec.source_size):
        return {
            "qualified": False, "reason": "reference-size-mismatch",
            "expectedSize": list(spec.source_size), "actualSize": list(actual_size),
        }
    outline = _crop_bounds(_yellow_outline(image), spec.source_bounds)
    classification = classify_b1_room_outline(outline)
    direction_match = classification.get("direction") == spec.neck_direction
    result = {
        "mapId": spec.map_id,
        "sourceBounds": list(spec.source_bounds),
        "expectedDirection": spec.neck_direction,
        # This is a human-reviewed whitelist cardinality, not a claim that a
        # full-map automatic inventory proved uniqueness. Runtime uniqueness
        # is re-established by the local matcher's second-peak margin.
        "configuredCandidateCount": 1 if direction_match else 0,
        "uniquenessEvidence": "reviewed-reference-whitelist-local-shape",
        "directionMatch": direction_match,
        "outlinePixels": int(np.count_nonzero(outline)) if outline is not None else 0,
        **classification,
    }
    result["qualified"] = bool(classification.get("qualified") and direction_match)
    if not result["qualified"] and result.get("reason") == "single-neck-room":
        result["reason"] = "neck-direction-conflict"
    return result


def inventory_b1_unique_room_anchors(
    references: dict[str, np.ndarray | None],
) -> dict:
    """Validate the small whitelist without scanning other map identities."""
    rows = []
    for spec in B1_UNIQUE_ROOM_SPECS:
        row = inspect_b1_unique_room_reference(references.get(spec.map_id), spec)
        row["mapId"] = spec.map_id
        rows.append(row)
    enabled = [row["mapId"] for row in rows if row.get("qualified")]
    return {
        "enabledMapIds": enabled,
        "rows": rows,
        "whitelistCount": len(B1_UNIQUE_ROOM_SPECS),
        "qualifiedCount": len(enabled),
        "sourcePolicy": "external-readonly",
    }


def _projected_room_box(
    matrix: np.ndarray,
    bounds: tuple[int, int, int, int],
) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
    x, y, width, height = (float(value) for value in bounds)
    linear = np.asarray(matrix, dtype=np.float64)
    if (
        linear.shape != (3, 3) or not np.isfinite(linear).all()
        or abs(float(np.linalg.det(linear[:2, :2]))) < 1e-8
    ):
        return None
    scale_x = float(np.linalg.norm(linear[:2, 0]))
    scale_y = float(np.linalg.norm(linear[:2, 1]))
    if min(scale_x, scale_y) < 0.03 or max(scale_x, scale_y) > 6.0:
        return None
    # The fast path is translation-only.  Rotation/shear and free scale are
    # rejected; display/layout scale is already present in the shared matrix.
    if abs(float(linear[0, 1])) > 0.025 * scale_x or abs(float(linear[1, 0])) > 0.025 * scale_y:
        return None
    if abs(scale_x / max(scale_y, 1e-6) - 1.0) > 0.06:
        return None
    points = np.asarray(
        [[x, y], [x + width, y], [x + width, y + height], [x, y + height]],
        dtype=np.float32,
    )
    projected = cv2.transform(points[None, :, :], linear[:2, :])[0]
    x0 = int(np.floor(np.min(projected[:, 0])))
    y0 = int(np.floor(np.min(projected[:, 1])))
    x1 = int(np.ceil(np.max(projected[:, 0])))
    y1 = int(np.ceil(np.max(projected[:, 1])))
    return projected, (x0, y0, x1, y1)


def match_b1_unique_room_anchor(
    frame: np.ndarray | None,
    reference: np.ndarray | None,
    base_matrix: np.ndarray,
    spec: B1UniqueRoomSpec,
    *,
    maximum_translation: float = 960.0,
) -> dict:
    """Find one reviewed room outline by translation around the group seed."""
    started = time.perf_counter()
    if frame is None or reference is None:
        return {"success": False, "reason": "room-reference-or-frame-missing"}
    reference_mask = _crop_bounds(_yellow_outline(reference), spec.source_bounds)
    classification = classify_b1_room_outline(reference_mask)
    if not classification.get("qualified") or classification.get("direction") != spec.neck_direction:
        return {
            "success": False, "reason": "reference-room-not-qualified",
            "configuredCandidateCount": 0,
            "direction": spec.neck_direction,
        }
    projected = _projected_room_box(base_matrix, spec.source_bounds)
    if projected is None:
        return {"success": False, "reason": "base-matrix-not-translation-only"}
    projected_points, expected_box = projected
    source_x, source_y, source_width, source_height = spec.source_bounds
    matrix = np.asarray(base_matrix, dtype=np.float64).copy()
    scale_x = float(np.linalg.norm(matrix[:2, 0]))
    scale_y = float(np.linalg.norm(matrix[:2, 1]))
    target_width = max(24, int(round(source_width * scale_x)))
    target_height = max(24, int(round(source_height * scale_y)))
    local_matrix = np.asarray([
        [matrix[0, 0], matrix[0, 1], matrix[0, 0] * source_x + matrix[0, 1] * source_y + matrix[0, 2]],
        [matrix[1, 0], matrix[1, 1], matrix[1, 0] * source_x + matrix[1, 1] * source_y + matrix[1, 2]],
    ], dtype=np.float32)
    # Runtime frames are textured BGR screenshots.  Put the reference into
    # the same edge domain before correlation; comparing a solid yellow mask
    # with double-sided screenshot edges creates a false low-score/offset.
    template_source = (
        reference_mask
        if frame.ndim == 2 else
        cv2.Canny(cv2.GaussianBlur(reference_mask, (3, 3), 0), 35, 105)
    )
    template = cv2.warpAffine(
        template_source, local_matrix, (frame.shape[1], frame.shape[0]),
        flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    # Restrict matching to the map's expected local neighbourhood.  This is
    # a confirmed-map pose search, never a 13-map identity search.
    _, _, expected_x1, expected_y1 = expected_box
    expected_x0, expected_y0 = expected_box[:2]
    pad = int(max(8.0, float(maximum_translation)))
    x0 = max(0, expected_x0 - pad)
    y0 = max(0, expected_y0 - pad)
    x1 = min(frame.shape[1], expected_x1 + pad)
    y1 = min(frame.shape[0], expected_y1 + pad)
    if x1 - x0 < target_width or y1 - y0 < target_height:
        return {"success": False, "reason": "room-search-window-invalid"}
    source_edges = _binary_edges(frame)
    # Crop the projected template to its non-zero extent before correlation.
    ys, xs = np.where(template > 0)
    if len(xs) < 50:
        return {"success": False, "reason": "room-template-empty"}
    tx0, tx1 = int(xs.min()), int(xs.max()) + 1
    ty0, ty1 = int(ys.min()), int(ys.max()) + 1
    template_crop = template[ty0:ty1, tx0:tx1]
    search = source_edges[y0:y1, x0:x1]
    if search.shape[0] < template_crop.shape[0] or search.shape[1] < template_crop.shape[1]:
        return {"success": False, "reason": "room-search-window-too-small"}
    response = cv2.matchTemplate(search, template_crop, cv2.TM_CCORR_NORMED)
    _, best_score, _, best_location = cv2.minMaxLoc(response)
    # ``best_location`` is the origin of ``template_crop`` in ``search``.
    # Compare it with the crop's baseline origin in the full frame.  The
    # crop offset is applied exactly once; this makes a zero-displacement
    # synthetic frame return [0, 0].
    found_x = int(x0 + best_location[0])
    found_y = int(y0 + best_location[1])
    baseline_x = int(tx0)
    baseline_y = int(ty0)
    translation = np.asarray(
        [found_x - baseline_x, found_y - baseline_y], dtype=np.float64,
    )
    distance = float(np.linalg.norm(translation))
    # A second separated response is evidence against a unique room position.
    suppressed = response.copy()
    radius = max(6, int(min(template_crop.shape[:2]) * 0.65))
    bx, by = best_location
    cv2.rectangle(suppressed, (max(0, bx - radius), max(0, by - radius)),
                  (min(suppressed.shape[1] - 1, bx + radius), min(suppressed.shape[0] - 1, by + radius)), 0, -1)
    second_score = float(suppressed.max()) if suppressed.size else 0.0
    margin = float(best_score - second_score)
    success = bool(
        best_score >= 0.43 and margin >= 0.035 and distance <= float(maximum_translation)
    )
    clipped = bool(
        found_x <= 1 or found_y <= 1
        or found_x + (tx1 - tx0) >= frame.shape[1] - 1
        or found_y + (ty1 - ty0) >= frame.shape[0] - 1
    )
    if clipped:
        success = False
    corrected = matrix.copy()
    corrected[:2, 2] += translation
    return {
        "success": success,
        "reason": (
            "unique-room-outline-matched" if success else
            "room-outline-clipped" if clipped else
            "room-outline-ambiguous"
        ),
        "mapId": spec.map_id,
        "direction": spec.neck_direction,
        "candidateCount": 1 if success else 2,
        "matchScore": float(best_score),
        "distinctMargin": margin,
        "translation": translation.tolist(),
        "translationPixels": distance,
        "matrixSourceToClient": corrected.tolist(),
        "referenceBounds": list(spec.source_bounds),
        "templateSize": [int(template_crop.shape[1]), int(template_crop.shape[0])],
        "elapsedMs": round((time.perf_counter() - started) * 1000.0, 1),
        "translationOnly": True,
        "currentFrameRouteResidualRequired": True,
        "authorizesDirectDisplay": False,
    }


def b1_unique_room_spec(map_id: str) -> B1UniqueRoomSpec | None:
    return _SPEC_BY_MAP.get(str(map_id))
