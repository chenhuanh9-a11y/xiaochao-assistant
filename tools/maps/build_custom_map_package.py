"""Build the custom map package: legacy editor art bound to game road data.

Inputs (all local, paths are arguments):
  --legacy     the earlier PC route editor assets (catalog.json, blank/, maps/,
               navigation/, exit-icon.png)
  --game-data  the nightmare 24-map handoff (index.json, maps/<layout>/topology-*.json)
  --structures the assembled public structure release (structure-index.json)

Output: a new directory with manifest.json, images/, topology/ and exit-icon.png.
Images are re-encoded as WebP (lossless alpha) and topology keeps only the
road graph the app reads, so the installed package stays small.

Each legacy floor image is registered onto its native structure canvas: an
initial centroid/area similarity refined by ECC (affine) on the image alpha
against the structure footprint. Template-to-map pairing is fixed below and
was chosen by footprint overlap across all candidate maps. Legacy verified
stairs are converted to native pixels and kept as cross-floor links because
the game data has no verified transfers yet.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import cv2
import numpy as np
from PIL import Image

SCHEMA = "xingtu-custom-maps-v1"
# Legacy template number -> runtime nightmare map id (templates 17-19 have no map).
TEMPLATE_MAPS = {
    1: "nightmare-03", 2: "nightmare-09", 3: "nightmare-11", 4: "nightmare-10", 5: "nightmare-14",
    6: "nightmare-15", 7: "nightmare-04", 8: "nightmare-12", 9: "nightmare-06", 10: "nightmare-18",
    11: "nightmare-19", 12: "nightmare-01", 13: "nightmare-13", 14: "nightmare-02", 15: "nightmare-16",
    16: "nightmare-17", 20: "nightmare-07", 21: "nightmare-08", 22: "nightmare-20", 23: "nightmare-21",
    24: "nightmare-05", 25: "33956dfb-8e0d-4627-a26c-e1904d687084",
    26: "4f5d6ac0-b1ae-4338-8d6e-b67cd1e3af51", 27: "37d291a1-529d-4a0e-8938-e4754e5f7a0d",
}
FLOORS = {"minus-1": "B1", "1": "1", "2": "2"}
MIN_OVERLAP = 0.8
QUALITY = 90
# Topology fields the app reads; the rest is provenance kept in the game data.
TOPOLOGY_KEYS = ("mapId", "floor", "shape", "maskSha256", "points", "pointRooms", "links")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mask_sha256(mask: np.ndarray) -> str:
    """Hash the native footprint as C-order binary uint8, shared with runtime."""
    return hashlib.sha256(np.ascontiguousarray(mask > 0, dtype=np.uint8).tobytes()).hexdigest()


def checked_mask_sha256(topology: dict, native: np.ndarray, map_id: str, floor: str) -> str:
    """Bind the road graph to this exact canonical footprint, not just its size."""
    if list(topology["shape"]) != list(native.shape):
        raise ValueError(f"topology shape mismatch for {map_id}/{floor}")
    digest = mask_sha256(native)
    if topology.get("maskSha256") != digest:
        raise ValueError(f"topology mask hash mismatch for {map_id}/{floor}")
    return digest


def read_image(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim != 3 or image.shape[2] != 4:
        raise ValueError(f"legacy image must be RGBA: {path}")
    return image


def iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum() / max(1, (a | b).sum()))


def register(image_alpha: np.ndarray, native: np.ndarray) -> tuple[np.ndarray, float]:
    """3x3 matrix from legacy image pixels to native pixels, and its overlap."""
    oy, ox = np.nonzero(image_alpha)
    ny, nx = np.nonzero(native)
    s = np.sqrt(len(nx) / len(ox))
    initial = np.array([[s, 0, nx.mean() - s * ox.mean()], [0, s, ny.mean() - s * oy.mean()], [0, 0, 1]])
    height, width = native.shape
    def overlap(matrix):
        warped = cv2.warpAffine(image_alpha.astype(np.uint8), matrix[:2], (width, height), flags=cv2.INTER_NEAREST)
        return iou(warped > 0, native)
    k = .25
    so = k * s
    template = cv2.GaussianBlur(cv2.resize(native.astype(np.float32), None, fx=k, fy=k,
                                           interpolation=cv2.INTER_AREA), (0, 0), 2)
    source = cv2.GaussianBlur(cv2.resize(image_alpha.astype(np.float32), None, fx=so, fy=so,
                                         interpolation=cv2.INTER_AREA), (0, 0), 2)
    scale_native, scale_source = np.diag([k, k, 1.]), np.diag([so, so, 1.])
    # ECC's warp maps template (native) coordinates into the source image.
    warp = (scale_source @ np.linalg.inv(initial) @ np.linalg.inv(scale_native))[:2].astype(np.float32)
    best, best_overlap = initial, overlap(initial)
    try:
        _, warp = cv2.findTransformECC(template, source, warp, cv2.MOTION_AFFINE,
                                       (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 300, 1e-6), None, 5)
        refined = (np.linalg.inv(scale_native) @ np.linalg.inv(np.vstack([warp, [0, 0, 1]]).astype(np.float64))
                   @ scale_source)
        refined_overlap = overlap(refined)
        if refined_overlap > best_overlap:
            best, best_overlap = refined, refined_overlap
    except cv2.error:
        pass
    return best, best_overlap


def write_image(source: Path, target: Path, quality: int) -> None:
    """WebP with exact alpha; quality 0 keeps the colours lossless too."""
    with Image.open(source) as image:
        options = dict(lossless=True) if quality == 0 else dict(quality=quality, alpha_quality=100)
        image.convert("RGBA").save(target, "WEBP", method=6, **options)


def write_topology(source: Path, target: Path) -> None:
    data = json.loads(source.read_text("utf-8"))
    slim = {key: data[key] for key in TOPOLOGY_KEYS}
    slim["points"] = [[round(float(x), 1), round(float(y), 1)] for x, y in slim["points"]]
    target.write_text(json.dumps(slim, ensure_ascii=False, separators=(",", ":")), "utf-8")


def cell_to_image(nav: dict, cell: int) -> tuple[float, float]:
    step = nav["step"]
    return (min(nav["width"] - 1, cell % nav["cols"] * step + step / 2),
            min(nav["height"] - 1, cell // nav["cols"] * step + step / 2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--legacy", required=True, type=Path)
    parser.add_argument("--game-data", required=True, type=Path)
    parser.add_argument("--structures", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--version", required=True, help="package version label, e.g. 20261028-1")
    parser.add_argument("--review", type=Path, help="optional folder for registration overlays")
    parser.add_argument("--zip", type=Path, help="also write the release archive and its SHA256SUMS here")
    parser.add_argument("--quality", type=int, default=QUALITY, help="WebP colour quality 1-100, 0 for lossless")
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error("--out must be a new directory")
    catalog = json.loads((args.legacy / "catalog.json").read_text("utf-8"))
    structures = {(row["mapId"], row["floor"]): row for row in
                  json.loads((args.structures / "structure-index.json").read_text("utf-8"))["entries"]
                  if row["mode"] == "nightmare"}
    topologies = {}
    for path in sorted(args.game_data.glob("maps/*/topology-*.json")):
        data = json.loads(path.read_text("utf-8"))
        topologies[(data["mapId"], str(data["floor"]))] = path
    out = args.out
    (out / "images").mkdir(parents=True)
    (out / "topology").mkdir()
    shutil.copyfile(args.legacy / "exit-icon.png", out / "exit-icon.png")
    if args.review:
        args.review.mkdir(parents=True, exist_ok=True)
    maps = []
    for template in catalog["templates"]:
        map_id = TEMPLATE_MAPS.get(template["number"])
        if map_id is None:
            continue
        navs = {floor["id"]: json.loads((args.legacy / floor["navigation"]).read_text("utf-8"))
                for floor in template["floors"]}
        floors, matrices = {}, {}
        for floor in template["floors"]:
            key = FLOORS[floor["id"]]
            # The legacy editor shows blank art; its basement uses the map image.
            source = (args.legacy / floor["image"] if floor["id"] == "minus-1"
                      else args.legacy / "blank" / template["id"] / f"{floor['id']}.png")
            image = read_image(source)
            row = structures[(map_id, key)]
            with np.load(args.structures / row["resourcePath"], allow_pickle=False) as resource:
                native = resource["clean_mask"] > 0
            topology_path = topologies[(map_id, key)]
            topology = json.loads(topology_path.read_text("utf-8"))
            mask_hash = checked_mask_sha256(topology, native, map_id, key)
            matrix, overlap = register(image[:, :, 3] > 0, native)
            if overlap < MIN_OVERLAP:
                raise ValueError(f"registration too weak for {template['id']}/{floor['id']}: {overlap:.3f}")
            matrices[floor["id"]] = matrix
            image_rel = f"images/{map_id}/{key}.webp"
            topology_rel = f"topology/{map_id}/{key}.json"
            (out / image_rel).parent.mkdir(exist_ok=True)
            (out / topology_rel).parent.mkdir(exist_ok=True)
            write_image(source, out / image_rel, args.quality)
            write_topology(topology_path, out / topology_rel)
            floors[key] = dict(
                image=image_rel, imageSha256=sha256(out / image_rel), imageSize=[image.shape[1], image.shape[0]],
                matrixImageToNative=[[round(float(v), 6) for v in r] for r in matrix],
                overlap=round(overlap, 4), nativeShape=list(native.shape),
                topology=topology_rel, topologySha256=sha256(out / topology_rel), maskSha256=mask_hash,
                entrances=[dict(label=e["label"], x=float(e["x"]), y=float(e["y"])) for e in floor.get("entrances", [])])
            if args.review:
                warped = cv2.warpAffine(image, matrix[:2], (native.shape[1], native.shape[0]),
                                        borderValue=(0, 0, 0, 0))
                canvas = np.full((*native.shape, 3), 255, np.uint8)
                canvas[native] = (200, 200, 200)
                alpha = warped[:, :, 3:4] / 255 * .75
                canvas = (canvas * (1 - alpha) + warped[:, :, :3] * alpha).astype(np.uint8)
                edge = cv2.morphologyEx(native.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((5, 5), np.uint8)) > 0
                canvas[edge] = (0, 0, 230)
                cv2.imwrite(str(args.review / f"{map_id}-{key}.jpg"),
                            cv2.resize(canvas, None, fx=.4, fy=.4, interpolation=cv2.INTER_AREA))
        stairs = []
        for stair in template.get("stairs", []):
            if stair.get("verified") is not True or stair.get("kind") != "stairs":
                continue
            ends = []
            for end in (stair["from"], stair["to"]):
                x, y = cell_to_image(navs[end["floor"]], end["cell"])
                u, v, _ = matrices[end["floor"]] @ [x, y, 1]
                ends.append(dict(floor=FLOORS[end["floor"]], x=round(float(u), 1), y=round(float(v), 1)))
            scale = np.sqrt(abs(np.linalg.det(matrices[stair["from"]["floor"]][:2, :2])))
            stairs.append(dict(id=stair["id"], **{"from": ends[0], "to": ends[1]},
                               cost=round(float(stair["cost"]) * scale, 1),
                               bidirectional=stair.get("bidirectional") is not False))
        maps.append(dict(mapId=map_id, templateId=template["id"], templateNumber=template["number"],
                         name=template["name"], aliases=template.get("aliases", []), floors=floors, stairs=stairs))
        print(template["number"], map_id, {k: v["overlap"] for k, v in floors.items()}, "stairs", len(stairs), flush=True)
    manifest = dict(schema=SCHEMA, version=args.version, mapCount=len(maps),
                    sources=dict(legacyCatalogSha256=sha256(args.legacy / "catalog.json"),
                                 gameDataVersion=json.loads((args.game_data / "index.json").read_text("utf-8")).get("sourceVersion"),
                                 exitIconSha256=sha256(out / "exit-icon.png")),
                    maps=maps)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    print(f"wrote {len(maps)} maps to {out}")
    if args.zip:
        archive = args.zip / f"xingtu-custom-maps-{args.version}.zip"
        args.zip.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
            for path in sorted(out.rglob("*")):
                if path.is_file():
                    # Images are already compressed; only the JSON benefits from deflate.
                    kind = zipfile.ZIP_STORED if path.suffix in (".webp", ".png") else zipfile.ZIP_DEFLATED
                    handle.write(path, path.relative_to(out).as_posix(), compress_type=kind)
        (args.zip / f"SHA256SUMS-custom-maps-{args.version}.txt").write_text(
            f"{sha256(archive)}  {archive.name}\n", "utf-8")
        print(f"archive {archive} ({archive.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
