"""Prepare a NEW isolated Qt LIGHT resource input from an authorized editor base.

No discovery, package rebuilding, downloads, installation, or existing-target writes.
Actual art/graphs remain in ignored work/, never in the source checkout's assets/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "maps"))
from package_profiles import LIGHT_PROFILE, partition_resources
from extend_custom_map_package import contained, disjoint_output, verify_package


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")


def checked_file(root, relative):
    path = contained(root, relative)
    # Resolved containment alone would still accept an internal symlink/junction.
    raw = root / relative
    for member in (raw, *raw.parents):
        if member.is_symlink() or getattr(member, "is_junction", lambda: False)():
            raise ValueError(f"linked input is unsupported: {relative}")
        if member == root:
            break
    if not path.is_file():
        raise ValueError(f"missing regular resource: {relative}")
    return path


def editor_inventory(source, expected_manifest):
    manifest_path = checked_file(source, "manifest.json")
    if digest(manifest_path) != expected_manifest:
        raise ValueError("editor manifest does not match the authorized input hash")
    manifest = read(manifest_path)
    verify_package(source, manifest)  # Existing producer's path/hash contract.
    names = {"manifest.json", "exit-icon.png"}
    for item in manifest["maps"]:
        for floor_id, floor in item["floors"].items():
            for kind in ("image", "topology"):
                names.add(floor[kind])
            topology = read(checked_file(source, floor["topology"]))
            if (str(topology.get("mapId")) != str(item["mapId"])
                    or str(topology.get("floor")) != str(floor_id)
                    or topology.get("shape") != floor["nativeShape"]
                    or topology.get("maskSha256") != floor["maskSha256"]):
                raise ValueError(f"editor topology binding mismatch: {item['mapId']}/{floor_id}")
            import numpy as np
            from PIL import Image
            matrix = np.asarray(floor["matrixImageToNative"], dtype=float)
            if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-9:
                raise ValueError("invalid editor/native transform")
            with Image.open(checked_file(source, floor["image"])) as image:
                if list(image.size) != floor["imageSize"]:
                    raise ValueError("editor image size binding mismatch")
    rows = {name: dict(bytes=checked_file(source, name).stat().st_size,
                       sha256=digest(checked_file(source, name))) for name in sorted(names)}
    return manifest, rows


def prepare(source, resources, target, expected_manifest, resource_version):
    source, resources = Path(source).resolve(), Path(resources).resolve()
    target_path = Path(target)
    if target_path.is_symlink() or getattr(target_path, "is_junction", lambda: False)():
        raise ValueError("linked output is unsupported")
    target = target_path.resolve()
    work = (ROOT / "work").resolve()
    if target == work or not target.is_relative_to(work):
        raise ValueError("target must be a NEW child of this checkout's ignored work directory")
    disjoint_output(target, source, resources)
    if not resource_version.strip():
        raise ValueError("exact input resource version is required")
    manifest, editor_rows = editor_inventory(source, expected_manifest)
    input_rows = {}
    for path in resources.rglob("*"):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("linked runtime input is unsupported")
        if path.is_file():
            name = path.relative_to(resources).as_posix()
            checked_file(resources, name)
            input_rows[name] = dict(bytes=path.stat().st_size)
    retained, omitted = partition_resources(input_rows)
    if not retained or any(name.startswith("assets/custom-maps/") for name in retained):
        raise ValueError("runtime input must be nonempty and have no preexisting editor package")
    # Omitted author images are classified by path without opening their bytes.
    rows = {name: dict(row, sha256=digest(resources / name)) for name, row in retained.items()}
    rows.update({"assets/custom-maps/" + name: row for name, row in editor_rows.items()})
    target.mkdir(parents=True, exist_ok=False)
    output = target / "_internal"
    for name, row in rows.items():
        destination = output / name
        origin = (source / name.removeprefix("assets/custom-maps/")) if name.startswith("assets/custom-maps/") else resources / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination)
        if destination.stat().st_size != row["bytes"] or digest(destination) != row["sha256"]:
            raise ValueError(f"resource changed during copy: {name}; incomplete target retained for inspection")
    inventory = dict(schema="xingtu-qt-resource-inventory-v1", profileId=LIGHT_PROFILE,
                     resourceVersion=resource_version + "+editor-" + str(manifest["version"]), files=rows)
    write(target / "resource-inventory.json", inventory)
    receipt = dict(schema="xingtu-qt-editor-base-input-v1", sourceKind="existing-registered-editor-basic-rendering",
                   sourceRoot=str(source), runtimeInputRoot=str(resources), editorManifestSha256=expected_manifest,
                   editorVersion=manifest["version"], mapCount=len(manifest["maps"]),
                   floorCount=sum(len(item["floors"]) for item in manifest["maps"]),
                   editorFiles=len(editor_rows), editorBytes=sum(row["bytes"] for row in editor_rows.values()),
                   omittedAuthorFiles=len(omitted), omittedAuthorBytes=sum(row["bytes"] for row in omitted.values()),
                   producer="tools/maps/build_custom_map_package.py + extend_custom_map_package.py",
                   provenanceDocument="docs/custom-maps.md", sources=manifest.get("sources", {}),
                   permission="User permits bundling existing editor basic rendering; excludes multi-author originals and raw game unpack. No new public asset license is asserted.",
                   limitation="Existing package verification status is preserved. Incremental source stair associations are not a new gameplay/internal-polyline verification.",
                   runtimeResourceRoot=str(output), resourceInventory=str(target / "resource-inventory.json"))
    write(target / "editor-base-provenance.json", receipt)
    (target / ".qt-lab-owner").write_text("qt-quick-isolated-v1\n", "utf-8")
    return receipt


def validate_representative(target, map_id):
    """Saved resources only: existing typed provider, graph and renderer, two floors.

    Does not construct a Qt application, capture frames or register hotkeys.
    """
    target = Path(target).resolve()
    if not target.is_relative_to((ROOT / "work").resolve()):
        raise ValueError("validation output must remain in checkout/work")
    if (target / ".qt-lab-owner").read_text("utf-8").strip() != "qt-quick-isolated-v1":
        raise ValueError("validation requires an owned sandbox")
    sys.path.insert(0, str(ROOT / "experiments" / "qt_quick"))
    sys.path.insert(0, str(ROOT / "app"))
    import numpy as np
    from PIL import Image
    from client_providers import build_engine_factories
    from custom_maps import CustomMapLibrary, render_game_layer, mask_sha256
    resources = target / "_internal"
    library = CustomMapLibrary(resources / "assets/custom-maps")
    if not library.available:
        raise ValueError(library.error)
    engine = build_engine_factories(resources)["nightmare"]()
    graph = library.graph(map_id)
    pair = next(((start, goal) for start, edges in graph.links.items() for goal, _ in edges
                 if start[0] == "B1" and goal[0] == "1"), None)
    if pair is None:
        raise ValueError("representative map needs a registered B1/F1 stair pair")
    points = []
    for floor, index in pair:
        # Include a same-floor road edge on each side of the registered stairs.
        neighbours = graph.floors[floor].neighbours[index]
        pick = neighbours[0][0] if neighbours else index
        x, y = graph.floors[floor].points[pick]
        points.append(dict(floor=floor, x=float(x), y=float(y), kind="via"))
    segments = graph.plan(points)
    checks = []
    for floor in ("B1", "1"):
        reference = engine.qt_shared_provider("shared-structure", map_id, floor)
        if reference is None:
            raise ValueError(f"missing typed reference: {map_id}/{floor}")
        library.validate_reference(map_id, floor, reference.clean_mask)
        rendered = render_game_layer(library, map_id, floor, points)
        if rendered.dtype != np.uint8 or rendered.shape != (*reference.clean_mask.shape, 4) or not np.any(rendered[:, :, 3]):
            raise ValueError("renderer did not produce a nonempty native-canvas BGRA layer")
        output = target / f"representative-{map_id}-{floor}.png"
        if output.exists():
            raise ValueError("representative output exists; refusing overwrite")
        Image.fromarray(rendered[:, :, [2, 1, 0, 3]], "RGBA").save(output)
        checks.append(dict(mapId=map_id, floor=floor, nativeShape=list(reference.clean_mask.shape),
                           maskSha256=mask_sha256(reference.clean_mask), typedPath=str(reference.path),
                           rendered=str(output), renderSha256=digest(output),
                           graphPoints=len(graph.floors[floor].points),
                           lineCount=len(graph.floor_lines(segments, floor))))
    result = dict(schema="xingtu-qt-editor-representative-check-v1", checks=checks,
                  routePoints=points, segmentCount=len(segments),
                  routeCells=len(segments[0]["cells"]), routeCost=segments[0]["cost"],
                  scope="Two-floor saved-resource binding/graph/render check; no live game or all-map recognition acceptance")
    report = target / "representative-check.json"
    if report.exists():
        raise ValueError("representative report exists; refusing overwrite")
    write(report, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Exact already-authorized editor package root")
    parser.add_argument("--resources", type=Path, required=True, help="Exact isolated runtime _internal input")
    parser.add_argument("--target", type=Path, required=True, help="NEW checkout/work child; never overwritten")
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--resource-version", required=True)
    parser.add_argument("--validate-map", help="Optional representative nightmare map with registered B1/F1 stairs")
    args = parser.parse_args()
    receipt = prepare(args.source, args.resources, args.target, args.expected_manifest_sha256, args.resource_version)
    if args.validate_map:
        receipt["representativeCheck"] = validate_representative(args.target, args.validate_map)
    print(json.dumps(receipt, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
