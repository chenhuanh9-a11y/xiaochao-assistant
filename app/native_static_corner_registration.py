"""Fail-closed native static corner evidence for author-coordinate candidates."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from nightmare_candidate import author_route_structure_mask, imread_unicode
from single_map_structure import contour_graph


EVIDENCE_TYPE = "native-static-corner-registration"
MIN_SUPPORT = 20
MIN_HOLDOUT = 20


class NumericValidationError(ValueError):
    pass


def _numeric(label, operation):
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise", under="ignore"):
            return operation()
    except (FloatingPointError, OverflowError, np.linalg.LinAlgError) as error:
        raise NumericValidationError(f"{label}-arithmetic-invalid") from error


def _finite_array(value, label):
    try:
        array = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as error:
        raise NumericValidationError(f"{label}-invalid") from error
    if not np.isfinite(array).all():
        raise NumericValidationError(f"{label}-nonfinite")
    return array


@dataclass(frozen=True)
class NativeCornerRequest:
    profile: str
    map_id: str
    floor: str
    variant: str
    native_layout: str
    native_floor: int


def _matrix(value) -> np.ndarray:
    matrix = _finite_array(value, "matrix")
    if matrix.shape != (3, 3):
        raise ValueError("matrix-not-finite-3x3")
    if not np.array_equal(matrix[2], [0, 0, 1]):
        raise ValueError("matrix-not-affine")
    if abs(matrix[0, 1]) > 1e-8 or abs(matrix[1, 0]) > 1e-8:
        raise ValueError("matrix-rotation-or-shear")
    if matrix[0, 0] <= 0 or matrix[1, 1] <= 0:
        raise ValueError("matrix-nonpositive-axis")
    determinant = float(_numeric("matrix-determinant", lambda: np.linalg.det(matrix)))
    if not np.isfinite(determinant) or determinant <= 1e-9:
        raise NumericValidationError("matrix-determinant-not-finite-or-at-most-1e-9")
    return matrix.copy()


def _inverse(matrix, label):
    value = _matrix(matrix)
    return _finite_array(_numeric(f"{label}-inverse", lambda: np.linalg.inv(value)), f"{label}-inverse")


def _compose(left, right, label):
    left = _finite_array(left, f"{label}-left")
    right = _finite_array(right, f"{label}-right")
    return _finite_array(_numeric(label, lambda: left @ right), f"{label}-result")


def _apply(matrix, points):
    matrix = _finite_array(matrix, "projection-matrix")
    points = _finite_array(points, "projection-points")
    if matrix.shape != (3, 3) or not np.array_equal(matrix[2], [0, 0, 1]):
        raise NumericValidationError("projection-illegal-homogeneous-matrix")
    points = points.reshape(-1, 2)
    projected = _finite_array(
        _numeric("point-projection", lambda: (matrix @ np.c_[points, np.ones(len(points))].T).T),
        "projection-output",
    )
    if not np.array_equal(projected[:, 2], np.ones(len(projected))):
        raise NumericValidationError("projection-illegal-homogeneous-output")
    return projected[:, :2]


def _residual_norm(predicted, observed, label):
    predicted = _finite_array(predicted, f"{label}-predicted")
    observed = _finite_array(observed, f"{label}-observed")
    difference = _finite_array(_numeric(f"{label}-difference", lambda: predicted - observed), f"{label}-difference")
    return _finite_array(_numeric(f"{label}-norm", lambda: np.linalg.norm(difference, axis=1)), f"{label}-norm")


def _stats(values):
    values = _finite_array(values, "residual-stat-input")
    if values.ndim != 1 or not len(values):
        raise NumericValidationError("residual-stat-empty-or-shape")
    result = _numeric("residual-stat", lambda: dict(
        count=len(values), median=float(np.median(values)),
        p90=float(np.percentile(values, 90)), max=float(np.max(values)),
        rms=float(np.sqrt(np.mean(values * values))),
    ))
    if not all(np.isfinite(value) for value in result.values()):
        raise NumericValidationError("residual-stat-nonfinite")
    return dict(
        count=int(result["count"]), median=result["median"], p90=result["p90"],
        max=result["max"], rms=result["rms"],
    )


def _coverage(points, shape):
    if not len(points):
        return dict(coveredQuadrants=0, coveredGridCells4x4=0,
                    spanWidthRatio=0.0, spanHeightRatio=0.0)
    points = np.asarray(points, dtype=np.float64)
    height, width = shape
    normalized = points / np.asarray((width, height))
    quadrants = np.minimum(1, np.floor(normalized * 2).astype(int))
    cells = np.minimum(3, np.floor(normalized * 4).astype(int))
    return dict(
        coveredQuadrants=len({tuple(row) for row in quadrants}),
        coveredGridCells4x4=len({tuple(row) for row in cells}),
        spanWidthRatio=float(np.ptp(normalized, axis=0)[0]),
        spanHeightRatio=float(np.ptp(normalized, axis=0)[1]),
    )


def resolve_native_corner_context(request: NativeCornerRequest, *, pack, catalog,
                                  structure_store, canonical_profile_id,
                                  primary_row, evidence, resource_cache=None):
    """Resolve only trusted static sources; candidate payload never selects them."""
    if request.profile == canonical_profile_id or not isinstance(primary_row, dict):
        return None
    primary_evidence = primary_row.get("evidence") or {}
    if (
        primary_evidence.get("type") is not None
        or primary_evidence.get("nativeStaticRegistration") is True
        or str(primary_evidence.get("method") or "").startswith("native-")
    ):
        return None
    primary_matrix = primary_row.get("matrixProfileToCanonical")
    sources = evidence.get("sources") or {}
    author_source = sources.get("authorResource") or {}
    native_source = sources.get("nativeResource") or {}
    geometry_source = sources.get("canonicalGeometryReference") or {}
    author_kind = (
        "nightmare-pack-layer"
        if request.profile == catalog.default_profile_id
        else "author-route-catalog"
    )
    expected_author_source = dict(
        kind=author_kind, profile=request.profile, mapId=request.map_id,
        floor=request.floor, variant=request.variant,
    )
    expected_geometry_source = dict(
        profile=canonical_profile_id, mapId=request.map_id,
        floor=request.floor, role="independent-fixed-coordinate-chart",
    )
    expected_native_source = dict(
        kind="author-structure-native-mask", profile=canonical_profile_id,
        mapId=request.map_id, floor=request.floor, variant=request.variant,
        nativeLayout=request.native_layout, nativeFloor=request.native_floor,
    )
    if not isinstance(primary_matrix, list) or sources != {
        "authorResource": expected_author_source,
        "nativeResource": dict(
            expected_native_source,
            resourcePath=native_source.get("resourcePath"),
        ),
        "canonicalGeometryReference": expected_geometry_source,
    }:
        return None
    entry = structure_store.entries.get(
        (canonical_profile_id, request.map_id, request.variant, request.floor)
    )
    if not isinstance(entry, dict):
        return None
    try:
        entry_binding = (str(entry.get("sourceLayoutId")), int(entry.get("sourceGameFloor", -1)))
    except (TypeError, ValueError, OverflowError):
        return None
    if entry_binding != (request.native_layout, request.native_floor):
        return None
    native_relative = entry.get("gameNativeMaskResourcePath")
    if not native_relative:
        return None
    if native_source.get("resourcePath") != native_relative:
        return None
    resource_root = Path(structure_store.resource_root).resolve()
    native_path = (resource_root / native_relative).resolve()
    if not native_path.is_relative_to(resource_root) or not native_path.is_file():
        return None
    author_path = None
    if request.profile == catalog.default_profile_id:
        item = pack.by_id.get(request.map_id)
        if item is not None:
            author_path = item.layers.get(request.floor)
    else:
        author_path = catalog.route_path(
            request.profile, request.map_id, request.floor,
            variant_id=request.variant, include_inactive=True,
        )
    if author_path is None or not Path(author_path).is_file():
        return None
    def version(path):
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino

    author_path = Path(author_path)
    binding = (request, author_path.resolve(), version(author_path),
               native_path, version(native_path),
               tuple(entry.get("gameNativeMaskShape") or ()),
               int(entry.get("gameNativeMaskBytes", -1)), author_route_structure_mask)
    cached = resource_cache if resource_cache is not None else {}
    if (cached.get("binding") == binding and cached.get("pack") is pack
            and cached.get("catalog") is catalog and cached.get("structure_store") is structure_store):
        source_mask = cached["source_mask"].copy()
        native_mask = cached["native_mask"].copy()
    else:
        source = imread_unicode(author_path, -1)
        native = imread_unicode(native_path, -1)
        if source is None or native is None or native.ndim != 2 or native.dtype != np.uint8:
            return None
        if not np.isin(native, [0, 255]).all():
            return None
        if tuple(native.shape) != binding[5] or binding[6] != native_path.stat().st_size:
            return None
        source_mask = author_route_structure_mask(source, profile_id=request.profile)
        native_mask = (native > 0).astype(np.uint8)
        if (resource_cache is not None and version(author_path) == binding[2]
                and version(native_path) == binding[4]):
            # One resource pair per adapter instance, never a frame verdict.
            # Private copies prevent returned contexts from poisoning the slot.
            resource_cache.clear()
            resource_cache.update(binding=binding, pack=pack, catalog=catalog,
                structure_store=structure_store, source_mask=source_mask.copy(), native_mask=native_mask.copy())
    expected_sources = {
        "authorResource": expected_author_source,
        "nativeResource": dict(expected_native_source, resourcePath=native_relative),
        "canonicalGeometryReference": expected_geometry_source,
    }
    return dict(
        request=request, entry=dict(entry), source_path=author_path,
        native_path=native_path, source_mask=source_mask, native_mask=native_mask,
        primary_profile_to_canonical=_matrix(primary_matrix),
        expected_sources=expected_sources,
    )


def reconstruct_native_corner_pairs(context):
    source = context["source_mask"]
    target = context["native_mask"]
    source_nodes = contour_graph(source)
    target_nodes = contour_graph(target)
    source_ids = np.array([i for i, node in enumerate(source_nodes) if node["valid"]])
    target_ids = np.array([i for i, node in enumerate(target_nodes) if node["valid"]])
    if len(source_ids) < 2 or len(target_ids) < 2:
        return None
    source_points = np.array([source_nodes[i]["point"] for i in source_ids])
    target_points = np.array([target_nodes[i]["point"] for i in target_ids])
    source_rays = np.array([source_nodes[i]["rays"] for i in source_ids])
    target_rays = np.array([target_nodes[i]["rays"] for i in target_ids])
    sy, sx = np.nonzero(source)
    ty, tx = np.nonzero(target)
    source_bounds = [int(sx.min()), int(sy.min()), int(sx.max()) + 1, int(sy.max()) + 1]
    target_bounds = [int(tx.min()), int(ty.min()), int(tx.max()) + 1, int(ty.max()) + 1]
    scale_x = (target_bounds[2] - target_bounds[0]) / (source_bounds[2] - source_bounds[0])
    scale_y = (target_bounds[3] - target_bounds[1]) / (source_bounds[3] - source_bounds[1])
    seed = np.array([[scale_x, 0, target_bounds[0] - scale_x * source_bounds[0]],
                     [0, scale_y, target_bounds[1] - scale_y * source_bounds[1]],
                     [0, 0, 1.]], dtype=np.float64)
    predicted = _apply(seed, source_points)
    oriented = np.min(np.sum(source_rays[:, None] * target_rays[None, :], axis=3), axis=2) >= .95
    distances = np.where(oriented,
                         np.linalg.norm(predicted[:, None] - target_points[None, :], axis=2),
                         np.inf)
    nearest = distances.argmin(axis=1)
    reverse = distances.argmin(axis=0)
    ordered = np.sort(distances, axis=1)
    margin = np.full(len(source_points), -np.inf)
    finite = np.isfinite(ordered[:, 0])
    margin[finite] = ordered[finite, 1] - ordered[finite, 0]
    kept = (reverse[nearest] == np.arange(len(source_points))) & (ordered[:, 0] <= 16) & (margin >= 8)
    indices = np.flatnonzero(kept)
    source = source_points[indices]
    target = target_points[nearest[indices]]
    cells = np.minimum(
        3,
        np.floor(
            (source - np.asarray(source_bounds[:2]))
            / np.asarray((source_bounds[2] - source_bounds[0],
                          source_bounds[3] - source_bounds[1]))
            * 4
        ).astype(int),
    )
    support = (cells[:, 0] + cells[:, 1]) % 2 == 0
    rows = [dict(
        sourceNode=int(source_ids[i]), gameNode=int(target_ids[j]),
        sourcePoint=source_points[i].tolist(), gamePoint=target_points[j].tolist(),
        sourceRays=source_rays[i].tolist(), gameRays=target_rays[j].tolist(),
        partition="support" if support[n] else "holdout", cell=cells[n].tolist(),
    ) for n, (i, j) in enumerate(zip(indices, nearest[indices]))]
    return dict(
        rows=rows, source=source, target=target, support=support,
        source_nodes=source_nodes, target_nodes=target_nodes,
        source_shape=list(context["source_mask"].shape),
        target_shape=list(context["native_mask"].shape),
        support_coverage=_coverage(source[support], context["source_mask"].shape),
        holdout_coverage=_coverage(source[~support], context["source_mask"].shape),
        support_spread=np.ptp(source[support], axis=0).tolist() if support.any() else [0, 0],
    )


def validate_native_static_corner_registration(candidate_profile_to_canonical, evidence, context):
    result = dict(accepted=False, type=evidence.get("type"), candidateOnly=True,
                  authorizesIdentity=False, authorizesRoot=False,
                  authorizesCrossFloorDisplay=False, reasons=[])
    if context is None or evidence.get("type") != EVIDENCE_TYPE:
        result["reasons"].append("native-context-or-type-unavailable")
        return result
    trusted_request = context.get("request")
    expected_binding = None
    if isinstance(trusted_request, NativeCornerRequest):
        expected_binding = {
            "profile": trusted_request.profile,
            "mapId": trusted_request.map_id,
            "floor": trusted_request.floor,
            "variant": trusted_request.variant,
            "nativeLayout": trusted_request.native_layout,
            "nativeFloor": trusted_request.native_floor,
        }
    if not isinstance(expected_binding, dict) or evidence.get("binding") != expected_binding:
        result["reasons"].append("native-corner-binding-mismatch")
        return result
    expected_sources = context.get("expected_sources")
    if not isinstance(expected_sources, dict) or evidence.get("sources") != expected_sources:
        result["reasons"].append("native-corner-source-binding-mismatch")
        return result
    try:
        reconstruction = reconstruct_native_corner_pairs(context)
    except Exception as error:
        result["reasons"].append(f"native-corner-reconstruction-invalid: {error}")
        return result
    if reconstruction is None:
        result["reasons"].append("native-corner-reconstruction-unavailable")
        return result
    expected = {(r["sourceNode"], r["gameNode"]): r for r in reconstruction["rows"]}
    rows = evidence.get("pairs")
    if not isinstance(rows, list) or len(rows) != len(expected):
        result["reasons"].append("native-corner-pairs-not-reconstructed")
        return result
    actual_keys = []
    partitions = {"support": [set(), set()], "holdout": [set(), set()]}
    for actual in rows:
        try:
            source_node, game_node = actual["sourceNode"], actual["gameNode"]
            if type(source_node) is not int or type(game_node) is not int:
                raise ValueError("node-id-not-int")
            source_nodes, target_nodes = reconstruction["source_nodes"], reconstruction["target_nodes"]
            if not (0 <= source_node < len(source_nodes) and 0 <= game_node < len(target_nodes)):
                raise ValueError("node-out-of-range")
            source_point = _finite_array(actual["sourcePoint"], "pair-source-point")
            game_point = _finite_array(actual["gamePoint"], "pair-game-point")
            if source_point.shape != (2,) or game_point.shape != (2,):
                raise ValueError("pair-coordinate-shape")
            if not (np.array_equal(source_point, source_nodes[source_node]["point"])
                    and np.array_equal(game_point, target_nodes[game_node]["point"])
                    and np.array_equal(actual["sourceRays"], source_nodes[source_node]["rays"])
                    and np.array_equal(actual["gameRays"], target_nodes[game_node]["rays"])):
                raise ValueError("pair-node-data-mismatch")
            partition = actual["partition"]
            if partition not in partitions:
                raise ValueError("invalid-partition")
            partitions[partition][0].add(source_node); partitions[partition][1].add(game_node)
            key = (source_node, game_node)
            actual_keys.append(key)
            if key not in expected or any(actual.get(field) != expected[key].get(field)
                for field in ("sourcePoint", "gamePoint", "sourceRays", "gameRays", "partition", "cell")):
                raise ValueError("pair-data-or-split-not-reconstructed")
        except Exception:
            result["reasons"].append("native-corner-pair-node-binding-invalid")
            return result
    if len(set(actual_keys)) != len(actual_keys) or set(actual_keys) != set(expected):
        result["reasons"].append("native-corner-pairs-not-reconstructed")
    if partitions["support"][0] & partitions["holdout"][0] or partitions["support"][1] & partitions["holdout"][1]:
        result["reasons"].append("native-corner-support-holdout-overlap")
    if (int(reconstruction["support"].sum()) < MIN_SUPPORT
            or int((~reconstruction["support"]).sum()) < MIN_HOLDOUT):
        result["reasons"].append("native-corner-support-or-holdout-insufficient")
    if min(reconstruction["support_spread"]) <= 200:
        result["reasons"].append("native-corner-support-spread-insufficient")
    for part in ("support_coverage", "holdout_coverage"):
        coverage = reconstruction[part]
        if coverage["coveredQuadrants"] < 3 or coverage["coveredGridCells4x4"] < 6:
            result["reasons"].append(f"native-corner-{part}-insufficient")
    try:
        measured = _matrix(candidate_profile_to_canonical)
        if "matrixProfileToCanonical" in evidence and not np.array_equal(measured, _matrix(evidence["matrixProfileToCanonical"])):
            result["reasons"].append("native-corner-evidence-matrix-mismatch")
        anisotropy = float(_numeric("matrix-anisotropy", lambda: max(measured[0, 0], measured[1, 1]) / min(measured[0, 0], measured[1, 1])))
        result["matrixAnisotropy"] = anisotropy
        if not np.isfinite(anisotropy) or anisotropy > 1.13:
            result["reasons"].append("native-corner-matrix-anisotropy-exceeded")
        primary = _matrix(context["primary_profile_to_canonical"])
        chain = _compose(primary, _inverse(context["entry"]["matrixAuthorProfileToGamePixel"], "primary-Hs"), "primary-Ps-inverseHs-chain")
        target_canonical = _apply(chain, reconstruction["target"])
        predicted = _apply(measured, reconstruction["source"])
        actual_author = _apply(_inverse(measured, "candidate-P"), target_canonical)
        support = reconstruction["support"]
        result["measured"] = {
            part: dict(
                canonical=_stats(_residual_norm(predicted[mask], target_canonical[mask], f"{part}-canonical")),
                referenceAuthor=_stats(_residual_norm(actual_author[mask], reconstruction["source"][mask], f"{part}-author")),
            ) for part, mask in (("support", support), ("holdout", ~support))
        }
    except Exception as error:
        result["reasons"].append(f"native-corner-residual-arithmetic-invalid: {error}")
        return result
    if any(result["measured"][part]["referenceAuthor"]["p90"] > 20.5 for part in ("support", "holdout")):
        result["reasons"].append("native-corner-reference-author-residual-exceeded")
    result["reconstruction"] = {k: reconstruction[k] for k in (
        "source_shape", "target_shape", "support_coverage", "holdout_coverage", "support_spread",
    )}
    result["accepted"] = not result["reasons"]
    return result
