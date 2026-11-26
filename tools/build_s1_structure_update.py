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

from export_original_qt_base import CAPABILITY, SCHEMA as BASE_SCHEMA, _generic_controls_payloads

FLOOR_DIR_TO_GAME = [("B1", "-1", 4), ("B2", "-2", 3), ("B3", "-3", 2), ("B4", "-4", 1)]
MODE_LABELS = {"difficult": "困难", "nightmare": "噩梦"}


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
        "structureVersionKey": structure_version_key,
        "sourceVersion": source_version,
        "baseSnapshot": source_version,
    }
    buf = io.BytesIO()
    np.savez_compressed(buf, shared_mask=clean, metadata=np.array(json.dumps(meta, ensure_ascii=False)))
    return buf.getvalue()


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
    outline = [[cx - 30.0, cy - 30.0], [cx + 30.0, cy - 30.0], [cx + 30.0, cy + 30.0], [cx - 30.0, cy + 30.0]]
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
        "chains": [],
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
            geo = json.loads(geo_path.read_text(encoding="utf-8"))
            ren_bytes = ren_path.read_bytes() if ren_path.is_file() else None
            hasher.update(gfloor.encode("utf-8"))
            hasher.update(mask.tobytes())
            floor_data[gfloor] = (wfloor, mask, geo, ren_bytes)

        struct_sha = hasher.hexdigest()
        b1_wfloor, b1_mask, b1_geo, _ = floor_data["-1"]
        ex, ey, plane_xz = _extract_entrance_xy(b1_geo, b1_mask)
        rx, ry = int(round(ex)), int(round(ey))

        map_layers = {}
        coord_floors = {}
        coord_index_map = {}

        for _bdir, gfloor, wfloor in FLOOR_DIR_TO_GAME:
            _wf, mask, geo, ren_bytes = floor_data[gfloor]
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

    controls_files = _generic_controls_payloads("s1", mode, ["-1", "-2", "-3", "-4"])
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


def verify_package_end_to_end(zip_path: Path, *, mode: str, sample_map_ids: list[str]) -> dict:
    from imported_original_base import decode_original_base
    from imported_resource_activation import activate_catalog_locked
    from imported_resource_archive import inspect_archive, save_archive
    from imported_resource_catalog import ResourceResolver
    from imported_resource_packages import PackageView
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

        # 1. Strict production decode of ALL maps & floors
        view = PackageView(resolver.root, ctx.base.component_ref, assert_current=ctx.assert_current)
        with view.read_scope():
            bundle = decode_original_base(view, season="s1", mode=mode)
            assert len(bundle.pack.maps) > 0
            # Verify live overlay rendered display works on sample maps
            provider = type("_Dummy", (), {})()
            for mid in sample_map_ids:
                assert mid in bundle.pack.by_id, f"Missing expected map {mid}"
                ref = bundle.resources.load("shared-structure", mid, "-1", variant="variant-01")
                assert ref is not None
                layer = SharedReferenceProvider._rendered_neutral_display(provider, ref, bundle, bundle.reader)
                assert layer is not None and layer["displayLabel"] == "默认渲染地图"

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
            "galleryPreviewShape": list(gallery_px.shape),
            "customRouteMaps": len(lib.maps()),
            "sampleAStarPoints": len(segs[0]["points"]),
        }


def main():
    parser = argparse.ArgumentParser(description="Build & verify routine S1 structure packages with rendered maps.")
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--glyph-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--modes", nargs="+", default=["difficult", "nightmare"])
    args = parser.parse_args()

    change_doc = json.loads((args.delivery / "变更清单.json").read_text(encoding="utf-8"))
    source_version = str(change_doc["sourceVersion"])
    res_version = f"{source_version}-{date.today().strftime('%Y%m%d')}.1"
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for mode in args.modes:
        label = MODE_LABELS[mode]
        zip_path = args.output_dir / f"星图-磷光症-{label}-结构包-{source_version}.xingtu-map"
        built = build_mode_package(
            args.delivery,
            mode,
            source_version=source_version,
            glyph_dir=args.glyph_dir,
            output_zip=zip_path,
            resource_version=res_version,
        )
        group_key = "106" if mode == "difficult" else "107"
        cls = change_doc["mapClassification"][group_key]
        samples = (cls.get("added") or [])[:1] + (cls.get("layoutRevised") or [])[:1]
        if not samples:
            samples = [sorted(p.name for p in (args.delivery / "maps" / mode).iterdir() if p.is_dir())[0]]
        verified = verify_package_end_to_end(zip_path, mode=mode, sample_map_ids=samples)
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
