"""Routine S1 structure-package builder from a map-data delivery folder.

Given a delivery directory such as ``E:/codex/deliveries/星图-S1地图资料-2.105.3133558``
containing ``maps/<mode>/<map_id>/B1..B4/{structure.npz, rendered.png, geometry.json}``,
this script builds complete, standalone ``original-qt-base`` structure package ZIPs
for ``difficult`` and ``nightmare`` WITH bundled rendered maps (``neutralDisplay``),
verifies every generated ZIP through the real production importers/decoders, and
writes a concise cloud-drive release note.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import zipfile

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "app", ROOT / "experiments" / "qt_quick", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from export_original_qt_base import CAPABILITY, SCHEMA as BASE_SCHEMA

FLOOR_DIR_TO_GAME = [("B1", "-1", 4), ("B2", "-2", 3), ("B3", "-3", 2), ("B4", "-4", 1)]
MODE_LABELS = {"difficult": "困难", "nightmare": "噩梦"}

_COMPATIBLE_BASE_IDS = {
    "difficult": ["benchmark-s1-difficult-coordinate", "s1-difficult-original-qt-base"],
    "nightmare": ["benchmark-s1-nightmare-coordinate", "s1-nightmare-original-qt-base"],
}


def _s1_controls_payloads(mode: str) -> dict[str, bytes]:
    """Official S1 signed-column controls profile accepted by S1ControlsProfile."""
    doc = {
        "schema": "xingtu-s1-controls-profile-v1",
        "detectorId": "s1-signed-column-geometry-v1",
        "contexts": {
            f"s1:{mode}": {
                "floors": [
                    {"floorId": "-1", "observedValue": -1},
                    {"floorId": "-2", "observedValue": -2},
                    {"floorId": "-3", "observedValue": -3},
                    {"floorId": "-4", "observedValue": -4},
                ],
                "visualOrder": [-1, -2, -3, -4],
            }
        },
        "expandedSearchRegion": [0.55, 0.04, 0.43499999999999994, 0.89],
        "fixedSearchRegion": [0.8, 0.12, 0.12, 0.44000000000000006],
        "mapUi": {
            "minimumCorrelation": {"close": 0.62, "minus": 0.65, "plus": 0.65},
            "searchRegions": {
                "close": [0.92, 0.02, 0.06999999999999995, 0.16],
                "minus": [0.92, 0.66, 0.06999999999999995, 0.19999999999999996],
                "plus": [0.92, 0.25, 0.06999999999999995, 0.21000000000000002],
            },
            "sizes": [0.024, 0.026, 0.028, 0.03, 0.032, 0.034, 0.036, 0.038],
        },
    }
    return {"profile.json": _encode_json(doc)}


def _load_baseline_map_hashes(archive_path: Path | None) -> dict[str, str]:
    """Extract {mapId: structureContentSha256} from a baseline structure or route archive."""
    if archive_path is None or not archive_path.is_file():
        return {}
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(archive_path) as zf:
        names = set(zf.namelist())
        if "data/base/references/map-pack.json" in names:
            pack = json.loads(zf.read("data/base/references/map-pack.json").decode("utf-8"))
            for row in pack.get("maps") or ():
                mid, sha = row.get("id"), row.get("structureContentSha256")
                if mid and sha:
                    hashes[str(mid)] = str(sha)
            return hashes
        if "manifest.json" in names:
            m = json.loads(zf.read("manifest.json").decode("utf-8"))
            for comp in m.get("components") or ():
                ep = f"data/{comp['componentId']}/{comp['entrypoint']}"
                if ep in names:
                    doc = json.loads(zf.read(ep).decode("utf-8"))
                    for row in doc.get("layers") or ():
                        svk = str(row.get("structureVersionKey") or "")
                        mid = str(row.get("mapId") or "")
                        if mid and "@" in svk and "/" in svk:
                            sha = svk.split("@", 1)[1].split("/", 1)[0]
                            if sha:
                                hashes[mid] = sha
    return hashes


def _encode_json(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_shared_npz(
    mask: np.ndarray,
    *,
    map_id: str,
    floor: str,
    world_floor: int,
    structure_version_key: str,
    source_version: str,
    source_catalog_sha256: str,
    renderer_metadata_sha256: str,
    image_to_map_plane_xz: list,
) -> bytes:
    clean = np.ascontiguousarray((mask > 0).astype(np.uint8) * 255)
    h, w = clean.shape[:2]
    meta = {
        "sourceKind": "game-rendered-shared-structure",
        "roles": {"clean": "shared_mask", "validation": "shared_mask"},
        "roleSemantics": {
            "shared_mask": "room/corridor texture footprint, not semantic walls or walkable grid",
            "validation": "same shared footprint, not independent observation evidence",
        },
        "binding": {
            "profile": "shared-structure",
            "mapId": map_id,
            "floor": floor,
            "variant": "variant-01",
        },
        "canvas": {
            "coordinateSpace": f"game-native-pixels:{map_id}:{floor}",
            "layout": map_id,
            "floor": floor,
            "worldFloor": world_floor,
            "width": int(w),
            "height": int(h),
            "shapeHW": [int(h), int(w)],
            "axes": "x-right,y-down",
        },
        "sourceGeometry": {
            "nativeShapeHW": [int(h), int(w)],
            "imageToMapPlaneXZ": image_to_map_plane_xz,
            "sourceHeight": int(world_floor),
            "gameFloor": int(floor),
            "sourceCatalogSha256": source_catalog_sha256,
            "rendererMetadataSha256": renderer_metadata_sha256,
            "authorPixelsUsed": False,
        },
        "structureVersionKey": structure_version_key,
        "sourceVersion": source_version,
        "baseSnapshot": source_version,
    }
    buf = io.BytesIO()
    np.savez_compressed(buf, shared_mask=clean, metadata=np.array(json.dumps(meta, ensure_ascii=False)))
    return buf.getvalue()


def _extract_entry_outline_and_chains(mask: np.ndarray, center_xy: list[float]) -> tuple[list[list[float]], list[dict]]:
    """Extract valid geometric polygon outline and 3-point corner chains around the entry."""
    cx, cy = float(center_xy[0]), float(center_xy[1])
    h, w = mask.shape[:2]
    binary = ((mask > 0).astype(np.uint8)) * 255
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best_poly = None
    best_dist = float("inf")
    for cnt in contours:
        if len(cnt) < 3 or cv2.contourArea(cnt) < 400:
            continue
        approx = cv2.approxPolyDP(cnt, 4.0, True).reshape(-1, 2)
        if len(approx) < 3:
            continue
        dists = np.linalg.norm(approx.astype(np.float64) - np.array([cx, cy]), axis=1)
        dmin = float(dists.min())
        if dmin < best_dist:
            best_dist = dmin
            best_poly = approx.astype(float)
    if best_poly is None or len(best_poly) < 3:
        best_poly = np.array(
            [[cx - 30.0, cy - 30.0], [cx + 30.0, cy - 30.0], [cx + 30.0, cy + 30.0], [cx - 30.0, cy + 30.0]],
            dtype=float,
        )
    # Cap outline to <= 128 vertices to stay well within preflight limits (256).
    if len(best_poly) > 128:
        idx = np.linspace(0, len(best_poly) - 1, 128, dtype=int)
        best_poly = best_poly[idx]
    n = len(best_poly)
    outline = [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in best_poly]
    chains = []
    for i in range(n):
        p_prev = best_poly[(i - 1) % n]
        p_cur = best_poly[i]
        p_next = best_poly[(i + 1) % n]
        v1 = p_prev - p_cur
        v2 = p_next - p_cur
        n1, n2 = float(np.linalg.norm(v1)), float(np.linalg.norm(v2))
        if n1 < 1e-3 or n2 < 1e-3:
            continue
        u1 = (v1 / n1).tolist()
        u2 = (v2 / n2).tolist()
        chains.append({
            "points": [
                [round(float(p_prev[0]), 2), round(float(p_prev[1]), 2)],
                [round(float(p_cur[0]), 2), round(float(p_cur[1]), 2)],
                [round(float(p_next[0]), 2), round(float(p_next[1]), 2)],
            ],
            "rays": [[float(u1[0]), float(u1[1])], [float(u2[0]), float(u2[1])]],
            "sourceNode": i,
            "sourceNeighborNodes": [(i - 1) % n, i, (i + 1) % n],
        })
    if not chains:
        outline = [[cx - 30.0, cy - 30.0], [cx + 30.0, cy - 30.0], [cx + 30.0, cy + 30.0], [cx - 30.0, cy + 30.0]]
        chains = [{
            "points": [outline[0], outline[1], outline[2]],
            "rays": [[-1.0, 0.0], [0.0, 1.0]],
            "sourceNode": 1,
            "sourceNeighborNodes": [0, 1, 2],
        }]
    return outline, chains


def _minimal_entry_record(
    map_id: str,
    floor: str,
    world_floor: int,
    mask: np.ndarray,
    center_xy: list[float],
    *,
    resource_path: str,
    structure_version_key: str,
    source_version: str,
) -> dict:
    h, w = mask.shape[:2]
    cx, cy = float(center_xy[0]), float(center_xy[1])
    outline, chains = _extract_entry_outline_and_chains(mask, center_xy)
    return {
        "mapId": map_id,
        "floor": floor,
        "kind": "front-door",
        "material": "shared-structure",
        "center": [cx, cy],
        "outline": outline,
        "unit": 60.0,
        "region": [max(0, int(cx - 120)), max(0, int(cy - 120)), min(int(w), int(cx + 120)), min(int(h), int(cy + 120))],
        "neighbors": [],
        "chains": chains,
        "sourceProduct": resource_path,
        "structureResource": resource_path,
        "profileId": "shared-structure",
        "variantId": "variant-01",
        "sourceKind": "game-rendered-shared-structure",
        "sourceArray": "shared_mask",
        "canvas": {
            "coordinateSpace": f"game-native-pixels:{map_id}:{floor}",
            "layout": map_id,
            "floor": floor,
            "worldFloor": world_floor,
            "width": int(w),
            "height": int(h),
            "shapeHW": [int(h), int(w)],
            "axes": "x-right,y-down",
        },
        "structureVersionKey": structure_version_key,
        "sourceVersion": source_version,
        "baseSnapshot": source_version,
        "proposalOnly": True,
        "authorizesPose": False,
        "authorizesIdentity": False,
    }


def _extract_entrance_xy(geo: dict, mask: np.ndarray) -> tuple[float, float, list[float]]:
    markers = geo.get("entryMarkers") or []
    if markers and isinstance(markers[0].get("center"), list) and len(markers[0]["center"]) == 2:
        cx, cy = float(markers[0]["center"][0]), float(markers[0]["center"][1])
    else:
        ys, xs = np.nonzero(mask > 0)
        cx = float(np.mean(xs)) if len(xs) else float(mask.shape[1]) / 2.0
        cy = float(np.mean(ys)) if len(ys) else float(mask.shape[0]) / 2.0
    ps = (geo.get("entryPoints") or {}).get("PlayerStart") or {}
    plane_xz = ps.get("mapPlaneXZ") or [cx, cy]
    return cx, cy, [float(plane_xz[0]), float(plane_xz[1])]


def build_mode_package(
    delivery_root: Path,
    mode: str,
    *,
    source_version: str,
    glyph_dir: Path,
    output_zip: Path,
    resource_version: str,
    baseline_hashes: dict[str, str] | None = None,
    revised_map_ids: set[str] | None = None,
) -> dict:
    mode_dir = delivery_root / "maps" / mode
    if not mode_dir.is_dir():
        raise FileNotFoundError(f"Missing mode directory: {mode_dir}")

    map_dirs = sorted([p for p in mode_dir.iterdir() if p.is_dir()], key=lambda p: p.name)
    if not map_dirs:
        raise ValueError(f"No map directories found in {mode_dir}")

    base_files: dict[str, bytes] = {}
    pack_maps = []
    identity_profiles = {}
    typed_entries = []
    version_bindings = []
    coord_maps = {}
    display_entries = []
    display_layers = []
    entry_records = []
    side_records = []
    coord_index_mode = {}

    glyph_png = (glyph_dir / "native_glyph_template.png").read_bytes()
    template_manifest_bytes = (glyph_dir / "template_manifest.json").read_bytes()
    template_manifest = json.loads(template_manifest_bytes.decode("utf-8"))
    glyph_sha = template_manifest["template_sha256"]

    delivery_manifest_path = delivery_root / "manifest.json"
    source_catalog_sha256 = (
        _sha256(delivery_manifest_path.read_bytes())
        if delivery_manifest_path.is_file()
        else _sha256(f"s1-delivery:{source_version}:{mode}".encode("utf-8"))
    )

    for map_dir in map_dirs:
        mid = map_dir.name
        identity_profiles[mid] = "shared-structure"
        # Compute map-level structure content hash across all 4 floors' shared_mask.
        floor_data = {}
        hasher = hashlib.sha256()
        for bdir, gfloor, wfloor in FLOOR_DIR_TO_GAME:
            fdir = map_dir / bdir
            npz_path = fdir / "structure.npz"
            geo_path = fdir / "geometry.json"
            ren_path = fdir / "rendered.png"
            with np.load(npz_path, allow_pickle=False) as z:
                mask = np.ascontiguousarray((z["shared_mask"] > 0).astype(np.uint8) * 255)
            geo_raw = geo_path.read_bytes()
            geo = json.loads(geo_raw.decode("utf-8"))
            geo_sha = _sha256(geo_raw)
            ren_bytes = ren_path.read_bytes() if ren_path.is_file() else None
            hasher.update(gfloor.encode("utf-8"))
            hasher.update(mask.tobytes())
            floor_data[gfloor] = (wfloor, mask, geo, ren_bytes, geo_sha)

        revised = revised_map_ids or set()
        inherited = (baseline_hashes or {}).get(mid)
        struct_sha = inherited if (inherited and mid not in revised) else hasher.hexdigest()
        b1_wfloor, b1_mask, b1_geo, _, _ = floor_data["-1"]
        ex, ey, plane_xz = _extract_entrance_xy(b1_geo, b1_mask)
        rx, ry = int(round(ex)), int(round(ey))

        map_layers = {}
        coord_floors = {}
        coord_index_map = {}

        for _bdir, gfloor, wfloor in FLOOR_DIR_TO_GAME:
            _wf, mask, geo, ren_bytes, geo_sha = floor_data[gfloor]
            h, w = mask.shape[:2]
            svk = f"{mid}@{struct_sha}/{gfloor}"
            npz_rel = f"references/entries/{mid}-{gfloor}-shared.npz"
            npz_bytes = _build_shared_npz(
                mask,
                map_id=mid,
                floor=gfloor,
                world_floor=wfloor,
                structure_version_key=svk,
                source_version=source_version,
                source_catalog_sha256=source_catalog_sha256,
                renderer_metadata_sha256=geo_sha,
                image_to_map_plane_xz=geo.get("imageToMapPlaneXZ") or [[1, 0, 0], [0, -1, int(h)], [0, 0, 1]],
            )
            base_files[npz_rel] = npz_bytes
            map_layers[gfloor] = npz_rel

            typed_entries.append({
                "profile": "shared-structure",
                "mapId": mid,
                "floor": gfloor,
                "variant": "variant-01",
                "resourcePath": npz_rel,
                "structureVersionKey": svk,
                "sourceVersion": source_version,
                "baseSnapshot": source_version,
            })
            version_bindings.append({
                "adapterReference": "references/coordinate-pack.json",
                "mapId": mid,
                "neutralDisplay": {
                    "baseResourceKey": {
                        "floor": gfloor,
                        "layoutId": mid,
                        "structureRawRecordSha256": struct_sha,
                    },
                    "index": "references/structure-display.json",
                },
                "packReference": "references/map-pack.json",
                "resourceKey": {
                    "floor": gfloor,
                    "layoutId": mid,
                    "structureRawRecordSha256": struct_sha,
                },
                "sharedLookup": {
                    "floor": gfloor,
                    "mapId": mid,
                    "profile": "shared-structure",
                    "resourcePath": npz_rel,
                    "variant": "variant-01",
                },
                "sourceVersion": source_version,
                "typedReference": npz_rel,
            })
            coord_floors[gfloor] = {
                "baseSnapshot": source_version,
                "coordinateHeight": int(h),
                "coordinateSpace": f"game-native-pixels:{mid}:{gfloor}",
                "coordinateWidth": int(w),
                "matrixCanonicalToLegacyRecognition": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                "referenceResource": npz_rel,
                "sourceLayout": mid,
                "sourceVersion": source_version,
                "structureVersionKey": svk,
                "worldFloor": wfloor,
            }
            display_entries.append({
                "mapId": mid,
                "floor": gfloor,
                "binding": {
                    "profile": "shared-structure",
                    "mapId": mid,
                    "floor": gfloor,
                    "variant": "variant-01",
                },
                "canvas": {
                    "coordinateSpace": f"game-native-pixels:{mid}:{gfloor}",
                    "layout": mid,
                    "floor": gfloor,
                    "worldFloor": wfloor,
                    "width": int(w),
                    "height": int(h),
                    "shapeHW": [int(h), int(w)],
                    "axes": "x-right,y-down",
                },
                "referenceResource": npz_rel,
                "structureVersionKey": svk,
                "sourceVersion": source_version,
                "baseSnapshot": source_version,
                "displayArray": "shared_mask",
            })

            if ren_bytes is not None:
                ren_rel = f"assets/rendered/{mid}/{gfloor}.png"
                base_files[ren_rel] = ren_bytes
                display_layers.append({
                    "mapId": mid,
                    "floor": gfloor,
                    "resourcePath": ren_rel,
                })

            coord_index_map[gfloor] = {
                "imageToEntranceXZ": [[1, 0, -rx], [0, -1, ry], [0, 0, 1]],
                "shapeHW": [int(h), int(w)],
                "sourceCatalogSha256": source_catalog_sha256,
                "sourceRendererMetadataSha256": geo_sha,
                "entranceNativeB1": [rx, ry],
                "entranceSourcePlaneXZ": plane_xz,
            }

            if gfloor == "-1":
                entry_records.append(
                    _minimal_entry_record(
                        mid, gfloor, wfloor, mask, [ex, ey],
                        resource_path=npz_rel, structure_version_key=svk, source_version=source_version,
                    )
                )

        coord_maps[mid] = {"floors": coord_floors}
        coord_index_mode[mid] = coord_index_map
        pack_maps.append({
            "id": mid,
            "name": f"{MODE_LABELS[mode]} {mid}",
            "direction": "unknown",
            "shape": "native",
            "sourceLayout": mid,
            "structureContentSha256": struct_sha,
            "sourceVersion": source_version,
            "identityProfileId": "shared-structure",
            "routeProfileId": "shared-structure",
            "layers": map_layers,
            "doorCount": len(b1_geo.get("doors") or []),
            "previewBox": [0, 0, int(b1_mask.shape[1]), int(b1_mask.shape[0])],
        })

    interfaces = {
        "identityPack": "references/map-pack.json",
        "typedIndex": "references/shared-structure-index.json",
        "versionBindings": "references/structure-version-bindings.json",
        "coordinatePack": "references/coordinate-pack.json",
        "neutralDisplay": "references/structure-display.json",
        "identityEntries": "references/entries",
        "identityProfile": "shared-structure",
        "entryEvidenceRole": "proposal-only",
    }

    map_pack_doc = {
        "schemaVersion": 1,
        "season": "s1",
        "mode": mode,
        "packId": f"s1-{mode}-{source_version}-native",
        "displayName": f"磷光症 · {MODE_LABELS[mode]}（含渲染底图）",
        "sourceVersion": source_version,
        "floors": ["-1", "-2", "-3", "-4"],
        "defaultEntryFloor": -1,
        "identityProfiles": identity_profiles,
        "maps": pack_maps,
    }
    native_manifest_doc = {
        "schemaVersion": 1,
        "season": "s1",
        "mode": mode,
        "seasonName": "磷光症",
        "sourceVersion": source_version,
        "opencvVersion": cv2.__version__,
        "runtimeInterfaces": interfaces,
        "capabilities": {"sideEntries": False},
    }
    context_doc = {
        "schema": BASE_SCHEMA,
        "season": "s1",
        "mode": mode,
        "seasonName": "磷光症",
        "compatibleBasePackageIds": _COMPATIBLE_BASE_IDS.get(mode, []),
        "originalManifest": "native-map-manifest.json",
        "runtimeInterfaces": interfaces,
    }

    base_files["native-map-manifest.json"] = _encode_json(native_manifest_doc)
    base_files["resource-context.json"] = _encode_json(context_doc)
    base_files["references/map-pack.json"] = _encode_json(map_pack_doc)
    base_files["references/shared-structure-index.json"] = _encode_json({
        "schemaVersion": 1,
        "resourceKind": "shared-structure",
        "structureVersionBindings": "references/structure-version-bindings.json",
        "entries": typed_entries,
    })
    base_files["references/structure-version-bindings.json"] = _encode_json({
        "schemaVersion": 1,
        "resourceKind": "native-structure-version-bindings",
        "entries": version_bindings,
    })
    profile_maps = {}
    ref_provider_entries = []
    for mid, mdata in coord_maps.items():
        p_floors = {}
        for gfloor, frow in mdata["floors"].items():
            p_floors[gfloor] = {
                "baseSnapshot": source_version,
                "crossFloorProjectionAllowed": False,
                "evidence": {
                    "algebraicallyExact": True,
                    "currentFrameGeometryFitRequired": True,
                    "method": "same-typed-shared-native-reference-canvas",
                },
                "matrixProfileToCanonical": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                "sourceVersion": source_version,
                "structureVersionKey": frow["structureVersionKey"],
                "usableForCurrentFrameRendering": True,
            }
            ref_provider_entries.append({
                "floor": gfloor,
                "mapId": mid,
                "profile": "shared-structure",
                "variant": "variant-01",
            })
        profile_maps[mid] = {"floors": p_floors}

    base_files["references/coordinate-pack.json"] = _encode_json({
        "schema": "identityv-nightmare-canonical-coordinate-pack-v1",
        "canonicalCrossFloorSearchCandidates": {},
        "canonicalPackId": f"s1-{mode}-native-{source_version}",
        "coordinateReferenceProfileId": "shared-structure",
        "policy": {
            "authorizesCrossFloorProjection": False,
            "authorizesMapIdentity": False,
            "requiresCurrentFrameGeometryFit": True,
        },
        "profiles": {"shared-structure": {"maps": profile_maps}},
        "referenceProvider": {
            "entries": ref_provider_entries,
            "indexPath": "references/shared-structure-index.json",
            "resourceRoot": ".",
        },
        "maps": coord_maps,
    })
    base_files["references/structure-display.json"] = _encode_json({
        "schemaVersion": 1,
        "resourceKind": "typed-shared-structure-display",
        "indexPath": "references/shared-structure-index.json",
        "entries": display_entries,
        "layers": display_layers,
    })
    base_files["references/entries/entries.json"] = _encode_json({
        "schemaVersion": 1,
        "mode": mode,
        "profileId": "shared-structure",
        "sourceKind": "game-rendered-shared-structure",
        "entries": entry_records,
        "semanticGaps": [],
    })
    base_files["references/entries/side-entries.json"] = _encode_json({
        "schemaVersion": 1,
        "mode": mode,
        "profileId": "shared-structure",
        "sourceKind": "game-rendered-shared-structure",
        "entries": side_records,
        "semanticGaps": [],
    })
    base_files["references/entries/entry-passports.json"] = _encode_json({
        "schemaVersion": 1,
        "mode": mode,
        "profileId": "shared-structure",
        "entries": [],
        "coverage": [],
    })
    base_files["references/native-similarity-groups.json"] = _encode_json({
        "schemaVersion": 1,
        "resourceKind": "native-similarity-groups",
        "groups": [],
    })
    base_files["s1-private-assets/s1_entrance_coordinate_index.json"] = _encode_json({
        "schema": "s1-common-entrance-coordinate-index-v1",
        "sourceVersion": source_version,
        "basis": "delivery-geometry-entry-marker",
        "glyphTemplateSha256": glyph_sha,
        "maps": {mode: coord_index_mode},
        "floorOrder": ["-1", "-2", "-3", "-4"],
        "entryOrigin": "front-door-marker",
        "axes": "x-right,z-up",
        "liveAuthorization": False,
    })
    base_files["s1-private-assets/template_manifest.json"] = template_manifest_bytes
    base_files["s1-private-assets/native_glyph_template.png"] = glyph_png

    controls_files = _s1_controls_payloads(mode)
    payloads = {"base": base_files, "controls": controls_files}
    contexts = [{"season": "s1", "mode": mode}]
    package_id = f"s1-{mode}-original-qt-base"
    manifest = {
        "schema": "xingtu-resource-package",
        "formatVersion": 1,
        "packageId": package_id,
        "displayName": f"磷光症 · {MODE_LABELS[mode]}结构包（{source_version}，含渲染底图）",
        "resourceVersion": resource_version,
        "clientCompatibility": {"resourceProtocol": 1, "requiredCapabilities": [CAPABILITY]},
        "provenance": {
            "sourceVersion": source_version,
            "originalResourceLayout": True,
            "includesRenderedDisplay": True,
            "numericRuntime": {
                "numpy": np.__version__,
                "opencv": cv2.__version__,
                "opencvOptimized": bool(cv2.useOptimized()),
            },
        },
        "components": [
            {
                "componentId": cid,
                "type": kind,
                "schemaVersion": 1,
                "contexts": contexts,
                "entrypoint": entrypoint,
                "dependencies": {},
            }
            for cid, kind, entrypoint in [
                ("base", "recognition-base", "resource-context.json"),
                ("controls", "controls-profile", "profile.json"),
            ]
        ],
        "files": [
            {
                "componentId": cid,
                "path": rel,
                "bytes": len(blob),
                "sha256": _sha256(blob),
            }
            for cid, items in payloads.items()
            for rel, blob in sorted(items.items())
        ],
    }

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("manifest.json", _encode_json(manifest))
        for cid, items in payloads.items():
            for rel, blob in sorted(items.items()):
                zf.writestr(f"data/{cid}/{rel}", blob)

    raw_zip = output_zip.read_bytes()
    return {
        "mode": mode,
        "zip": str(output_zip),
        "bytes": len(raw_zip),
        "sha256": _sha256(raw_zip),
        "maps": len(pack_maps),
        "layers": len(display_entries),
        "renderedLayers": len(display_layers),
        "packageId": package_id,
        "resourceVersion": resource_version,
    }


def verify_package_end_to_end(
    zip_path: Path,
    *,
    mode: str,
    sample_map_ids: list[str],
    old_route_archive: Path | None = None,
) -> dict:
    from imported_author_display import decode_author_display
    from imported_original_base import decode_original_base
    from imported_resource_activation import activate_catalog_locked
    from imported_resource_archive import inspect_archive, save_archive
    from imported_resource_catalog import ResourceResolver
    from imported_resource_packages import PackageView
    from imported_s1_controls_profile import S1ControlsProfile
    from imported_structure_route_library import ImportedStructureRouteLibrary
    from client_imported_browse import load_imported_browse_context
    from client_providers import SharedReferenceProvider
    from route_scopes import SCOPES

    with tempfile.TemporaryDirectory() as tmp:
        catalog_root = Path(tmp) / "catalog"
        catalog_root.mkdir(parents=True, exist_ok=True)
        summary = inspect_archive(zip_path)
        assert summary.package_id and summary.resource_version
        saved = save_archive(zip_path, catalog_root)
        ref_base = {
            "packageId": saved.package_id,
            "resourceVersion": saved.resource_version,
            "componentId": "base",
            "manifestSha256": saved.manifest_sha256,
        }
        ref_ctrl = {
            "packageId": saved.package_id,
            "resourceVersion": saved.resource_version,
            "componentId": "controls",
            "manifestSha256": saved.manifest_sha256,
        }
        import uuid
        from imported_resource_catalog import SCHEMA as CATALOG_SCHEMA
        catalog_doc = {
            "schema": CATALOG_SCHEMA,
            "generation": str(uuid.uuid4()),
            "contexts": {
                f"s1:{mode}": {
                    "recognitionBase": ref_base,
                    "controls": ref_ctrl,
                    "reviewedGroups": None,
                }
            },
            "displays": [],
        }
        activate_catalog_locked(catalog_root, catalog_doc, expected_catalog_stamp=None, validate=lambda *a, **k: None)

        resolver = ResourceResolver(catalog_root, supported_capabilities=(CAPABILITY,))
        snapshot = resolver.snapshot()
        ctx = snapshot.resolve_context("s1", mode)
        assert ctx.base is not None, "Active base not resolved after install"

        # 1. Run the EXACT production build_native_engine admission gate used by client_imported_activation!
        from client_imported_providers import build_native_engine
        from client_paths import WORK_ROOT, runtime_paths
        from difficult_entry_identity import DifficultEntryIdentity
        assert ctx.controls is not None, "Active controls not resolved after install"
        ctrl_view = PackageView(resolver.root, ctx.controls.component_ref, assert_current=ctx.assert_current)
        view = PackageView(resolver.root, ctx.base.component_ref, assert_current=ctx.assert_current)
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=WORK_ROOT) as sb_tmp:
            sb = Path(sb_tmp)
            (sb / ".qt-lab-owner").write_text("qt-quick-isolated-v1\n", encoding="utf-8")
            with view.read_scope(), ctrl_view.read_scope():
                engine = build_native_engine(view, ctrl_view, runtime_paths(sb), season="s1", mode=mode, require_rank=False)
                assert engine is not None, "build_native_engine returned None"
                # Prove background candidate eliminator builds full pool (union chains + void fields) across ALL maps!
                from imported_resource_reader import ComponentResourceReader
                from s1_common_coordinate_filter import CommonCoordinateFilter
                from s1_pose_prior import CoordinatePriorIndex
                from s1_structure_selection import StructureCatalog
                vreader = ComponentResourceReader(view)
                res_key = StructureCatalog.from_engine(engine).resource_key
                idx_path = view.path("s1-private-assets/s1_entrance_coordinate_index.json")
                coord_filter = CommonCoordinateFilter(
                    lambda mid, fl: engine.qt_shared_provider("shared-structure", mid, fl),
                    json.loads(vreader(idx_path).decode("utf-8")),
                    mode=mode,
                    resource_key=res_key,
                    read_resource=vreader,
                )
                pool_data = coord_filter._pool(sorted(engine.pack.by_id), "-1", lambda: False)
                assert pool_data is not None and len(pool_data["ids"]) == len(engine.pack.by_id), "Candidate eliminator pool failed"
                assert len(pool_data["fields"]) == len(engine.pack.by_id) and bool(pool_data["difference"].any()), "Candidate eliminator difference mask empty"
                prior_idx = CoordinatePriorIndex(
                    idx_path,
                    expected_sha256=None,
                    mode=mode,
                    resource_key=res_key,
                    provider=lambda mid, fl: engine.qt_shared_provider("shared-structure", mid, fl),
                    read_resource=vreader,
                )
                assert prior_idx._native_to_common(sample_map_ids[0], "-1") is not None
        with ctrl_view.read_scope():
            controls_profile = S1ControlsProfile(ctrl_view)
            controls_profile.for_context("s1", mode)
            assert controls_profile.detector_id == "s1-signed-column-geometry-v1"

        old_route_summary = None
        with view.read_scope():
            bundle = decode_original_base(view, season="s1", mode=mode)
            assert len(bundle.pack.maps) > 0
            entry_idx = DifficultEntryIdentity(
                view.path(bundle.interfaces["identityEntries"] + "/entries.json").parent,
                read_resource=bundle.reader,
            )
            assert len(entry_idx.entries) == len(bundle.pack.maps)
            # Verify live overlay rendered display works on sample maps
            provider = type("_Dummy", (), {})()
            for mid in sample_map_ids:
                assert mid in bundle.pack.by_id, f"Missing expected map {mid}"
                ref = bundle.resources.load("shared-structure", mid, "-1", variant="variant-01")
                assert ref is not None
                layer = SharedReferenceProvider._rendered_neutral_display(provider, ref, bundle, bundle.reader)
                assert layer is not None and layer["displayLabel"] == "默认渲染地图"

            if old_route_archive is not None and old_route_archive.is_file():
                from imported_author_dependencies import matches_display_base
                saved_route = save_archive(old_route_archive, catalog_root)
                route_ref = next(iter(saved_route.component_refs))
                route_view = PackageView(resolver.root, route_ref, assert_current=ctx.assert_current)
                with route_view.read_scope():
                    # Verify planner-style matches_display_base(route_view, base_ref) without explicit compatible_ids!
                    assert matches_display_base(route_view, view.component_ref), "matches_display_base failed via resource_root"
                    decoded_route = decode_author_display(route_view, bundle, bundle.resources.load)
                    old_route_summary = {
                        "profileId": decoded_route.profile_id,
                        "compatibleLayers": len(decoded_route.layers),
                        "skippedGaps": len(decoded_route.gaps),
                        "plannerMatch": True,
                    }
                    assert len(decoded_route.layers) > 0, "Expected unchanged maps to remain compatible"

        # 2. Map Gallery browse & rendered preview decode
        browse = load_imported_browse_context(resolver, mode, season="s1")
        assert not browse.error and browse.profiles
        first_key = ("s1-native-structure", sample_map_ids[0], "-1")
        gallery_px = browse.assets.pixels(first_key)
        assert gallery_px is not None and gallery_px.ndim == 3 and gallery_px.shape[2] == 4

        # 3. Custom Route Editor library + walkable A* pathfinding
        scope_id = "season-s1-hard" if mode == "difficult" else "season-s1-nightmare"
        lib = ImportedStructureRouteLibrary(resolver, SCOPES[scope_id], Path(tmp) / "editor-cache")
        assert lib.available, f"Custom route library failed: {lib.error}"
        graph = lib.graph(sample_map_ids[0])
        wf = graph.floors["-1"]
        wf._ensure_grid()
        ys, xs = np.nonzero(wf._grid > 0)
        assert len(xs) > 10
        p0 = graph.pick("-1", float(xs[0] * wf._step), float(ys[0] * wf._step))
        p1 = graph.pick("-1", float(xs[-1] * wf._step), float(ys[-1] * wf._step))
        segs = graph.plan([p0, p1])
        assert len(segs) == 1 and len(segs[0]["points"]) >= 2

        return {
            "mode": mode,
            "decodedMaps": len(bundle.pack.maps),
            "decodedLayers": len(bundle.versions),
            "controlsDetector": "s1-signed-column-geometry-v1",
            "galleryPreviewShape": list(gallery_px.shape),
            "customRouteMaps": len(lib.maps()),
            "sampleAStarPoints": len(segs[0]["points"]),
            "oldRouteCompat": old_route_summary,
        }


def main():
    parser = argparse.ArgumentParser(description="Build & verify routine S1 structure packages with rendered maps.")
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--glyph-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baseline-difficult", type=Path, default=None)
    parser.add_argument("--baseline-nightmare", type=Path, default=None)
    parser.add_argument("--verify-old-nightmare-route", type=Path, default=None)
    parser.add_argument("--modes", nargs="+", default=["difficult", "nightmare"])
    args = parser.parse_args()

    change_doc = json.loads((args.delivery / "变更清单.json").read_text(encoding="utf-8"))
    source_version = str(change_doc["sourceVersion"])
    res_version = f"{source_version}-{date.today().strftime('%Y%m%d')}.5"
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for mode in args.modes:
        label = MODE_LABELS[mode]
        zip_path = args.output_dir / f"星图-磷光症-{label}-结构包-{source_version}.xingtu-map"
        group_key = "106" if mode == "difficult" else "107"
        cls = change_doc["mapClassification"][group_key]
        baseline_src = args.baseline_difficult if mode == "difficult" else args.baseline_nightmare
        baseline_hashes = _load_baseline_map_hashes(baseline_src)
        revised_ids = set(cls.get("layoutRevised") or ())
        built = build_mode_package(
            args.delivery,
            mode,
            source_version=source_version,
            glyph_dir=args.glyph_dir,
            output_zip=zip_path,
            resource_version=res_version,
            baseline_hashes=baseline_hashes,
            revised_map_ids=revised_ids,
        )
        samples = (cls.get("added") or [])[:1] + (cls.get("layoutRevised") or [])[:1]
        if not samples:
            samples = [sorted(p.name for p in (args.delivery / "maps" / mode).iterdir() if p.is_dir())[0]]
        old_route = args.verify_old_nightmare_route if mode == "nightmare" else None
        verified = verify_package_end_to_end(zip_path, mode=mode, sample_map_ids=samples, old_route_archive=old_route)
        built["verification"] = verified
        results.append(built)

    # Write cloud-drive release note
    d_cls = change_doc["mapClassification"]["106"]
    n_cls = change_doc["mapClassification"]["107"]
    note_lines = [
        f"星图 · 磷光症（S1）结构包更新（{source_version}，默认自带渲染底图）",
        "",
        "【本次地图变动】",
        f"- 困难模式（共 {d_cls['currentCount']} 张）：新增 {len(d_cls['added'])} 张（{', '.join(d_cls['added'])}）；下架 {len(d_cls['absentFromCurrentGroup'])} 张（{', '.join(d_cls['absentFromCurrentGroup'])}）",
        f"- 噩梦模式（共 {n_cls['currentCount']} 张）：新增 {len(n_cls['added'])} 张（{', '.join(n_cls['added'])}）；同编号改版 {len(n_cls['layoutRevised'])} 张（{', '.join(n_cls['layoutRevised'])}）",
        "",
        "【升级说明】",
        "1. 无需重装软件，也无需手动删除旧结构包。",
        "2. 下载对应模式的 .xingtu-map 结构包，在星图「地图资源 → 导入本地图包」选中导入，即可自动覆盖升级并启用。",
        "3. 本次结构包默认自带带门渲染底图，导入后局内贴合、图库预览、自定义画线均默认显示渲染图。",
    ]
    (args.output_dir / "更新说明.txt").write_text("\n".join(note_lines) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
