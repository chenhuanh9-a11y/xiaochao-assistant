"""Append source-rendered maps to a verified editor package without realigning old art.

Native room occupancy and reciprocal room_paths provide static same-floor roads.
They do not establish interior collision, live door state or cross-floor stairs.
The caller supplies an explicit new-floor plan and matching source geometry.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import cv2
import numpy as np
from PIL import Image

from build_custom_map_package import SCHEMA, mask_sha256, sha256, write_image


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def contained(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("resource path escapes package")
    return path


def verify_package(root, manifest):
    if manifest.get("schema") != SCHEMA or not manifest.get("maps"):
        raise ValueError("invalid or empty baseline package")
    seen = set()
    for item in manifest["maps"]:
        if item["mapId"] in seen:
            raise ValueError("duplicate baseline map")
        seen.add(item["mapId"])
        for floor in item["floors"].values():
            for key in ("image", "topology"):
                if sha256(contained(root, floor[key])) != floor[key + "Sha256"]:
                    raise ValueError("baseline resource hash mismatch")
    if sha256(root / "exit-icon.png") != manifest["sources"]["exitIconSha256"]:
        raise ValueError("baseline icon hash mismatch")


def topology_from_geometry(geometry, metadata, mask, map_id, floor):
    world = int(metadata["floor"])
    if str(geometry["mapId"]) != str(metadata["levelId"]):
        raise ValueError("geometry layout mismatch")
    bounds = metadata["bounds"]
    pitch = float(metadata["targetPixelsPerVoxel"])
    if pitch != 60:
        raise ValueError("unsupported native pixel pitch")
    ox, oy = -bounds["minX"] * pitch / 128, -bounds["minY"] * pitch / 128
    project = lambda v: [round((v[0] + .5) * pitch + ox, 6), round((-v[2] - .5) * pitch + oy, 6)]
    placed = {p["roomIndex"] for p in metadata["placements"]}
    points, rooms, links, lookup = [], [], [], {}

    def add(room, voxel):
        point = project(voxel)
        x, y = map(round, point)
        if not (0 <= y < mask.shape[0] and 0 <= x < mask.shape[1] and mask[y, x]):
            return None
        key = room, tuple(voxel)
        if key not in lookup:
            lookup[key] = len(points)
            points.append(point)
            rooms.append(room)
        return lookup[key]

    def link(a, b, kind):
        if a is None or b is None:
            return False
        x0, y0 = map(round, points[a]); x1, y1 = map(round, points[b])
        left, right = min(x0, x1), max(x0, x1)
        top, bottom = min(y0, y1), max(y0, y1)
        line = np.zeros((bottom-top+1, right-left+1), np.uint8)
        cv2.line(line, (x0-left, y0-top), (x1-left, y1-top), 1, 1)
        if np.any((line > 0) & ~mask[top:bottom+1, left:right+1]):
            return False
        links.append([a, b, kind])
        return True

    for room in geometry["rooms"]:
        if room["roomIndex"] not in placed:
            continue
        rid = f"{room['roomIndex']}:{world}"
        cells = [tuple(v) for v in room["worldVoxels"] if v[1] == world]
        if room["roomConfigId"] == 10024 and world == room["position"][1]:
            # The existing reviewed Y-room centreline; do not infer other diagonals.
            nav = []
            for x, z in [(0, .5), (0, 4.5), (5, 2.5), (3, 2.5)]:
                a, b = [(x,z),(z,-x),(-x,-z),(-z,x)][room["rotation"]]
                nav.append([a+room["position"][0]-.5, world, b+room["position"][2]-.5])
            ids = [add(rid, p) for p in nav]
            for a, b in [(0,3),(1,3),(2,3)]:
                link(ids[a], ids[b], "source-diagonal")
        else:
            indices = {v: add(rid, v) for v in cells}
            for v, a in indices.items():
                for dx, dz in [(1,0),(0,1)]:
                    link(a, indices.get((v[0]+dx, v[1], v[2]+dz)), "room-grid")

    def doorway(rid, voxel):
        exact = lookup.get((rid, tuple(voxel)))
        if exact is not None:
            return exact
        point = project(voxel)
        matches = [i for i, r in enumerate(rooms) if r == rid and
                   abs(points[i][0]-point[0]) <= 30 and abs(points[i][1]-point[1]) <= 30]
        return matches[0] if len(matches) == 1 else None

    missing = []
    for door in geometry["pairedDoors"]:
        if door["worldFloor"] != world:
            continue
        a = doorway(f"{door['a']['roomIndex']}:{world}", door["worldVoxels"][0])
        b = doorway(f"{door['b']['roomIndex']}:{world}", door["worldVoxels"][1])
        if not link(a, b, "source-door"):
            missing.append(door)
    if not points or not links:
        raise ValueError("empty new-floor road graph")
    return dict(mapId=map_id, floor=floor, shape=list(mask.shape), maskSha256=mask_sha256(mask),
                points=points, pointRooms=rooms, links=links), missing


def check_native_binding(metadata, row, binding, native_meta):
    expected = dict(mapId=row["runtimeMapId"], floor=row["floor"], profile="zhan10-speed")
    if any(native_meta.get("binding", {}).get(k) != v for k, v in expected.items()):
        raise ValueError("native identity binding mismatch")
    canvas = native_meta.get("canvas", {})
    source = native_meta.get("sourceGeometry", {})
    if (str(canvas.get("layout")) != row["sourceLayout"] or
            canvas.get("worldFloor") != row["sourceWorldFloor"] or
            source.get("bounds") != metadata["bounds"] or
            source.get("matrixRendererPixelToProduct") != np.eye(3).tolist() or
            native_meta.get("structureVersionKey") != binding["structureVersionKey"] or
            native_meta.get("sourceVersion") != binding["sourceVersion"]):
        raise ValueError("native coordinate or source binding mismatch")


def extend(baseline, plan_path, geometry_root, resource_root, out, version):
    if out.exists():
        raise ValueError("output must not exist")
    manifest = read(baseline / "manifest.json")
    verify_package(baseline, manifest)
    plan = read(plan_path)["entries"]
    identities = {(r["runtimeMapId"], r["floor"]) for r in plan}
    old_ids = {m["mapId"] for m in manifest["maps"]}
    if len(identities) != len(plan) or any(m in old_ids for m, _ in identities):
        raise ValueError("new-floor identities collide")
    index = read(resource_root / "references/nightmare-shared-structure-index.json")["entries"]
    index = {(r["mapId"], str(r["floor"])): r for r in index if r["profile"] == "zhan10-speed"}
    prepared, report = [], []
    for row in plan:
        mid, floor = row["runtimeMapId"], row["floor"]
        metadata = read(row["nativeSourceMetadata"])
        if str(metadata["levelId"]) != row["sourceLayout"] or metadata["floor"] != row["sourceWorldFloor"]:
            raise ValueError("plan renderer identity mismatch")
        source = Path(row["nativeSourcePNG"])
        with Image.open(source) as image:
            alpha = np.asarray(image.convert("RGBA"))[:, :, 3] > 0
        binding = index[mid, floor]
        with np.load(contained(resource_root, binding["resourcePath"]), allow_pickle=False) as resource:
            mask = resource["shared_mask"] > 0
            native_meta = json.loads(str(resource["metadata"].item()))
        check_native_binding(metadata, row, binding, native_meta)
        if alpha.shape != mask.shape or not np.array_equal(alpha, mask):
            raise ValueError("normal render differs from current structure footprint")
        geometry = read(geometry_root / f"{row['sourceLayout']}.geometry.json")
        topology, missing = topology_from_geometry(geometry, metadata, mask, mid, floor)
        prepared.append((row, source, binding, topology))
        report.append(dict(mapId=mid, floor=floor, sourceLayout=row["sourceLayout"],
                           points=len(topology["points"]), links=len(topology["links"]),
                           missingPairedDoors=len(missing), crossFloorTransfers="unverified-not-generated"))
    shutil.copytree(baseline, out)
    additions = {}
    for row, source, binding, topology in prepared:
        mid, floor = row["runtimeMapId"], row["floor"]
        image_rel, topology_rel = f"images/{mid}/{floor}.webp", f"topology/{mid}/{floor}.json"
        (out / image_rel).parent.mkdir(exist_ok=True)
        (out / topology_rel).parent.mkdir(exist_ok=True)
        write_image(source, out / image_rel, 90)
        (out / topology_rel).write_text(json.dumps(topology, separators=(",", ":")), "utf-8")
        item = additions.setdefault(mid, dict(mapId=mid, templateId=mid, templateNumber=100+int(row["sourceLayout"][-3:]),
            name=f"新增地图 {row['sourceLayout']}", aliases=[], sourceLayout=row["sourceLayout"],
            sourceVersion=binding["sourceVersion"], floors={}, stairs=[], crossFloorRoutingVerified=False))
        item["floors"][floor] = dict(image=image_rel, imageSha256=sha256(out/image_rel),
            imageSize=list(reversed(topology["shape"])), matrixImageToNative=np.eye(3).tolist(), overlap=1.0,
            nativeShape=topology["shape"], topology=topology_rel, topologySha256=sha256(out/topology_rel),
            maskSha256=topology["maskSha256"], entrances=[], sourceVersion=binding["sourceVersion"],
            structureVersionKey=binding["structureVersionKey"])
    manifest["maps"].extend(additions.values())
    manifest["mapCount"], manifest["version"] = len(manifest["maps"]), version
    manifest["sources"]["incrementalNativeMaps"] = dict(planSha256=sha256(plan_path),
        baselineManifestSha256=sha256(baseline/"manifest.json"), addedFloors=len(plan),
        normalArt="source-rendered; current texture bytes not re-extracted",
        crossFloorTransfers="new maps unknown; existing verified stairs retained")
    (out/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    verify_package(out, manifest)
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "plan", "geometry", "resources", "out"):
        p.add_argument("--"+name, type=Path, required=True)
    p.add_argument("--version", required=True)
    a=p.parse_args()
    print(json.dumps(extend(a.baseline,a.plan,a.geometry,a.resources,a.out,a.version),indent=2))
