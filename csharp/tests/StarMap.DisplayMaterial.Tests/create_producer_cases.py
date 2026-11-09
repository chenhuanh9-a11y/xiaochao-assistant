"""Small supplemental original-producer oracle; never recomputes the 229-case oracle.

The installed resource tree and private runtime are read-only. Actual JSON and
one typed NPZ are copied from an existing oracle fixture. Only display art,
icon, image-to-native matrix and entrance positions are synthetic. Original
EngineFactories / display intent / reprojection and render_game_layer execute.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
import zlib

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "experiments/qt_quick"), str(REPO / "app")]
sys.path.insert(0, str(REPO / "csharp/tests/StarMap.DisplaySelection.Tests"))
import cv2
import numpy as np
import PIL
from PIL import Image
from client_providers import EngineFactories
from client_custom_display import CustomGalleryDisplay
from client_route_preferences import prepare_display_layer
from client_route_reprojection import prepare_route_reprojection
from custom_maps import CustomMapLibrary, render_game_layer
from types import SimpleNamespace
from create_python_oracle import Oracle


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def rgba(width, height):
    y, x = np.mgrid[:height, :width]
    alpha = np.asarray([0, 1, 2, 17, 63, 127, 128, 191, 254, 255], np.uint8)[(x * 3 + y * 7) % 10]
    return np.stack(((x * 97 + y * 29) % 256, (x * 13 + y * 83) % 256,
                     (x * 53 + y * 17) % 256, alpha), axis=2).astype(np.uint8)


def png16(path, samples, transparency=None):
    """Write a standard lossless PNG; original Pillow independently decodes it."""
    channels = 1 if samples.ndim == 2 else samples.shape[2]
    color_type = {1: 0, 2: 4, 3: 2, 4: 6}[channels]
    height, width = samples.shape[:2]
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    rows = b"".join(b"\0" + np.asarray(row, dtype=">u2").tobytes() for row in samples)
    header = struct.pack(">IIBBBBB", width, height, 16, color_type, 0, 0, 0)
    trns = b"" if transparency is None else chunk(b"tRNS", struct.pack(">" + "H" * len(transparency), *transparency))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + trns + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("existing_oracle", type=Path)
    parser.add_argument("new_output", type=Path)
    parser.add_argument("--extend-producer", type=Path, help="Retain existing producer cases unchanged and generate only absent supplemental cases")
    args = parser.parse_args()
    if args.new_output.exists():
        raise ValueError("Use a new evidence directory")
    original = json.loads(args.existing_oracle.read_text("utf-8"))
    basis = next(r for r in original["cases"] if r["label"] == "synthetic/generic-rgba")
    original_install = Path(basis["resourceRoot"]).parent
    helper = Oracle(Path(original["resourceRoot"]), args.new_output)
    original_engine = helper.factories.get_engine("nightmare", "s0")
    result = dict(schema="original-display-material-producer-v1", pillow=PIL.__version__,
                  opencv=cv2.__version__, numpy=np.__version__, cases=[], counterexamples=[])
    existing_names = ("rgba-boundary-exits", "rgba-art-includes-exits", "rgb-art-gray-icon",
                      "gray-art-rgb-icon", "tall-rgba-icon", "same-size-rgba-icon", "unreadable-icon")
    sixteen_names = ("gray16-icon", "rgb16-icon", "rgba16-icon", "gray-alpha16-icon", "gray16-trns-icon", "rgb16-trns-icon")
    transparency_names = ("gray16-trns-icon", "rgb16-trns-icon", "gray8-trns-icon", "rgb8-trns-icon")
    new_names = sixteen_names + transparency_names[2:]
    names = existing_names + new_names
    if args.extend_producer is not None:
        previous = json.loads(args.extend_producer.read_text("utf-8"))
        assert previous["pillow"] == PIL.__version__ and previous["opencv"] == cv2.__version__
        result["cases"] = copy.deepcopy(previous["cases"])
        result["counterexamples"] = copy.deepcopy(previous["counterexamples"])
        labels = {case["label"] for case in previous["cases"]}
        names = tuple(name for name in new_names if "synthetic/producer/" + name not in labels)
    for name in names:
        install = args.new_output / "fixtures" / name
        shutil.copytree(original_install, install)
        root = install / "_internal"
        manifest_path = root / "assets/custom-maps/manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        map_id, floor = basis["mapId"], basis["floor"]
        map_row = next(row for row in manifest["maps"] if row["mapId"] == map_id)
        row = map_row["floors"][floor]
        native_h, native_w = row["nativeShape"]
        art = rgba(37, 29)
        if name == "rgb-art-gray-icon":
            art = art[:, :, :3]
        elif name == "gray-art-rgb-icon":
            art = art[:, :, 0]
        library_root = root / "assets/custom-maps"
        image = library_root / "material-art.png"
        Image.fromarray(art).save(image)
        topology = library_root / row["topology"]
        topology.parent.mkdir(parents=True, exist_ok=True)
        topology_source = Path(original["resourceRoot"]) / "assets/custom-maps" / row["topology"]
        shutil.copyfile(topology_source, topology)
        matrix = np.asarray([[1.125, -.1875, -3.375], [.0625, .9375, 4.25], [0., 0., 1.]])
        row.update(image=image.name, imageSize=[37, 29], imageSha256=sha(image.read_bytes()),
                   matrixImageToNative=matrix.tolist())
        row["entrances"] = []
        # Aim icons at all clipped canvas edges and two fully off-canvas positions.
        inverse = np.linalg.inv(matrix)
        for i, (x, y) in enumerate(((1.25, 2.75), (native_w - .25, 12.5), (17.5, native_h - .25),
                                    (native_w - 1.5, native_h - 1.5), (-40., 0.), (native_w + 40., native_h + 40.))):
            u, v, _ = inverse @ [x, y, 1]
            row["entrances"].append(dict(label=str(i), x=float(u), y=float(v)))
        includes = name == "rgba-art-includes-exits"
        manifest["art"] = dict(includesExits=includes)
        write_json(manifest_path, manifest)
        icon = rgba(43, 31)
        if name == "rgb-art-gray-icon":
            icon = icon[:, :, 0]
        elif name == "gray-art-rgb-icon":
            icon = icon[:, :, :3]
        elif name == "tall-rgba-icon":
            icon = rgba(1, 401)
        elif name == "same-size-rgba-icon":
            icon = rgba(20, 20)
        Image.fromarray(icon).save(library_root / "exit-icon.png")
        if name == "unreadable-icon":
            # Original CustomMapLibrary.exit_icon catches image-open OSError and
            # returns None. The atlas remains usable without entrance icons.
            (library_root / "exit-icon.png").write_bytes(b"not a decodable exit icon")
        icon_conversion = None
        if name in sixteen_names or name in transparency_names:
            levels = np.asarray([0, 128, 255, 256, 1024, 65535], np.uint16)
            gray = np.stack([np.roll(levels, shift) for shift in range(4)])
            alpha = np.stack([np.roll(np.asarray([0, 256, 32767, 32768, 65280, 65535], np.uint16), shift) for shift in range(4)])
            transparency = None
            if name in ("gray16-icon", "gray16-trns-icon"):
                samples = gray
                if name == "gray16-trns-icon":
                    transparency = [256]
            elif name == "gray-alpha16-icon":
                samples = np.stack((gray, alpha), axis=2)
            elif name == "gray8-trns-icon":
                samples = np.stack([np.roll(np.asarray([0, 128, 255, 1, 64, 129], np.uint8), shift) for shift in range(4)])
                transparency = [128]
            else:
                samples = np.stack((gray, np.roll(gray, 1, axis=1), np.roll(gray, 2, axis=1)), axis=2)
                if name == "rgba16-icon":
                    samples = np.concatenate((samples, alpha[:, :, None]), axis=2)
                elif name == "rgb16-trns-icon":
                    transparency = [0, 65535, 1024]
                    # A different original 16-bit color shares the low-byte key
                    # after Pillow's RGB8 decode, so both transparency outcomes
                    # must be reproduced rather than forcing alpha opaque.
                    extra = np.tile(np.asarray([0, 65280, 0], np.uint16), (4, 1))[:, None, :]
                    samples = np.concatenate((samples, extra), axis=1)
                elif name == "rgb8-trns-icon":
                    samples = (samples >> 8).astype(np.uint8)
                    transparency = samples[0, 0].tolist()
            if samples.dtype == np.uint16:
                png16(library_root / "exit-icon.png", samples, transparency)
            else:
                key = transparency[0] if len(transparency) == 1 else tuple(transparency)
                Image.fromarray(samples).save(library_root / "exit-icon.png", transparency=key)
            with Image.open(library_root / "exit-icon.png") as decoded_icon:
                converted = np.asarray(decoded_icon.convert("RGBA"))
                icon_conversion = dict(pillowMode=decoded_icon.mode, sampleDtype=str(samples.dtype), sourceSamples=samples.tolist(),
                    pillowTransparency=decoded_icon.info.get("transparency"), rgba=converted.tolist(), rgbaSha256=sha(converted.tobytes()))
            raw_icon = cv2.imdecode(np.frombuffer((library_root / "exit-icon.png").read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
            icon_conversion.update(opencvDtype=str(raw_icon.dtype), opencvShape=list(raw_icon.shape), opencvPixels=raw_icon.tolist())
            expected_gray = np.minimum(samples, 255).astype(np.uint8) if samples.ndim == 2 else (
                (samples >> 8).astype(np.uint8) if samples.dtype == np.uint16 else samples)
            if samples.ndim == 2:
                expected_rgba = np.stack((expected_gray, expected_gray, expected_gray, np.full_like(expected_gray, 255)), axis=2)
            elif samples.shape[2] == 2:
                expected_rgba = np.stack((expected_gray[:, :, 0], expected_gray[:, :, 0], expected_gray[:, :, 0], expected_gray[:, :, 1]), axis=2)
            elif samples.shape[2] == 3:
                expected_rgba = np.concatenate((expected_gray, np.full((*samples.shape[:2], 1), 255, np.uint8)), axis=2)
            else:
                expected_rgba = expected_gray
            if transparency is not None:
                # Pillow convert_transparent compares its converted 8-bit
                # channels to the low bytes of the PNG's original color key.
                key = np.asarray(transparency, np.uint16).astype(np.uint8)
                transparent = expected_gray == key[0] if samples.ndim == 2 else np.all(expected_gray == key, axis=2)
                expected_rgba[:, :, 3] = np.where(transparent, 0, 255).astype(np.uint8)
            assert np.array_equal(converted, expected_rgba), icon_conversion

        # The independent expected bytes come only from original entry points.
        library = CustomMapLibrary(library_root)
        custom = CustomGalleryDisplay(SimpleNamespace(base_display_library=lambda: library))
        # Same original fixture adapter: only explicit resource roots change;
        # the real provider/intent/reprojection implementations are preserved.
        fixture = SimpleNamespace(install=install, root=root, context_root=root, original=original_engine,
            season="s0", mode="nightmare", map_id=map_id, floor=floor,
            catalog_path=root / original_engine.qt_catalog.catalog_path.relative_to(helper.root),
            coordinates=root / original_engine.qt_adapters.path.relative_to(helper.root))
        engine = helper.fixture_engine(fixture)
        engine.qt_base_display = lambda m, f, r: custom.base_layer(m, f, r, "nightmare")
        preferred = "author-missing"
        layer = prepare_display_layer(engine, map_id, floor, preferred)
        assert layer["displayKind"] == "base-map", layer
        reference_to_client = np.asarray(basis["referenceToClient"], np.float64)
        reference_to_canonical = engine.qt_adapters.matrix_profile_to_canonical("shared-structure", map_id, floor)
        canonical_to_client = reference_to_client @ np.linalg.inv(reference_to_canonical)
        reprojection = prepare_route_reprojection(engine, map_id, floor, preferred, canonical_to_client)
        pixels = np.asarray(layer["pixels"])
        assert np.array_equal(pixels, render_game_layer(library, map_id, floor, []))
        raw_path = args.new_output / "expected" / (name + ".bgra")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(pixels.tobytes())
        expected = {key: layer.get(key) for key in ("preferredProfileId", "effectiveProfileId", "authorStatus", "fallback",
                    "fallbackReason", "variantId", "displayKind", "displayLabel")}
        expected.update(shape=list(pixels.shape), pixelSha256=sha(pixels.tobytes()),
            originalProducerPixelSha256=sha(pixels.tobytes()), originalProducerPixelsPath=str(raw_path),
            displayToReference=np.asarray(layer["matrixDisplayToReference"]).tolist(),
            displayToClient=np.asarray(reprojection["matrixSourceToClient"]).tolist(),
            referenceToCanonical=reference_to_canonical.tolist(), canonicalToClient=canonical_to_client.tolist(), imagePath=str(image))
        test = dict(label="synthetic/producer/" + name, resourceRoot=str(root), season="s0", mode="nightmare", mapId=map_id,
            floor=floor, preferred=preferred, variant="variant-01", referenceToClient=reference_to_client.tolist(), prepared=True, expected=expected)
        if icon_conversion is not None:
            test["iconConversion"] = icon_conversion
        if name == "rgba-boundary-exits":
            reference = engine.qt_shared_provider("shared-structure", map_id, floor)
            with np.load(reference.path, allow_pickle=False) as archive:
                arrays = {key: archive[key] for key in archive.files}
            metadata = json.loads(str(arrays["metadata"]))
            mask = arrays[metadata["roles"]["clean"]].copy()
            mask.flat[0] = 0 if mask.flat[0] else 255
            arrays[metadata["roles"]["clean"]] = mask
            changed_reference = args.new_output / "changed-reference.npz"
            np.savez_compressed(changed_reference, **arrays)
            test["changedReferencePath"] = str(changed_reference)
        result["cases"].append(test)

        if not includes and name == "rgba-boundary-exits":
            expected_icon = np.asarray(Image.fromarray(icon).convert("RGBA").resize((20, 20), Image.Resampling.LANCZOS))
            cv_icon = cv2.resize(icon, (20, 20), interpolation=cv2.INTER_LANCZOS4)
            difference = int(np.count_nonzero(expected_icon != cv_icon))
            assert difference > 0
            result["counterexamples"].append(dict(label="OpenCV-Lanczos4-is-not-Pillow-LANCZOS", differentChannelBytes=difference,
                pillowSha256=sha(expected_icon.tobytes()), opencvSha256=sha(cv_icon.tobytes())))
    write_json(args.new_output / "producer-cases.json", result)
    print(json.dumps(dict(cases=len(result["cases"]), pillow=PIL.__version__, counterexamples=result["counterexamples"]), indent=2))


if __name__ == "__main__":
    main()
