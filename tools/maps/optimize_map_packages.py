"""Build display-only Q90 candidates from current map-package ZIPs, offline.

This maintains the difficult-zhan10/native and nightmare/optional-author
contracts. It is not a general image converter or an installer.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

import cv2
import numpy as np
from PIL import Image, __version__ as pillow_version


PACKAGE_IDS = {"difficult-zhan10", "nightmare-suju", "nightmare-nightingale", "nightmare-wang-qingxin"}
BASE_CATALOG = "_internal/references/nightmare-author-route-catalog.json"
NATIVE_MANIFEST = "_internal/difficult-native-manifest.json"
FULL_REFERENCES = "_internal/references/difficult-native-full-references.json"
MAX_FILES = 2500
MAX_MEMBER_BYTES = 192 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jsonbytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def safe_path(value: str) -> str:
    """Accept only canonical portable file paths used inside current packages."""
    if (not isinstance(value, str) or not value or any(char in value for char in '\\:<>"|?*')
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"Non-portable package path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError(f"Unsafe package path: {value!r}")
    # Windows also reserves superscript 1/2/3 device suffixes, console devices,
    # and device stems with spaces before an extension (e.g. "CON .txt").
    if any(part.endswith((".", " ")) or re.fullmatch(
            r"(?i)(con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])",
            part.partition(".")[0].rstrip(" "))
           for part in path.parts):
        raise ValueError(f"Non-portable package path: {value!r}")
    return path.as_posix()


def read_zip(data: bytes) -> dict[str, bytes]:
    """Read without extracting; reject aliases, duplicates and oversized members."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        if len(entries) > MAX_FILES or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
            raise ValueError("Archive exceeds the supported resource size limits")
        result = {}
        folded = set()
        for entry in entries:
            name = safe_path(entry.orig_filename)
            if name != entry.filename:
                raise ValueError(f"Archive filename was normalized: {name}")
            if name.casefold() in folded or entry.file_size > MAX_MEMBER_BYTES:
                raise ValueError(f"Duplicate or oversized archive member: {name}")
            folded.add(name.casefold())
            result[name] = archive.read(entry)
        return result


def display_asset(path: str) -> bool:
    return (path.endswith((".png", ".webp"))
            and any(token in "/" + path for token in ("/assets/suju/", "/assets/zhan10-", "/assets/authors/")))


def encode_display(task, *, allow_webp: bool):
    """Keep canvas/alpha; retain the original if encoding would grow the image."""
    path, source = task
    with Image.open(io.BytesIO(source)) as image:
        source_format = image.format
        before = np.array(image.convert("RGBA"))
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError(f"Animated display is outside the current contract: {path}")
        expected_format = "WEBP" if path.endswith(".webp") else "PNG"
        if source_format != expected_format or (source_format == "WEBP" and not allow_webp):
            raise ValueError(f"Unexpected image format for current package: {path}")
        buffer = io.BytesIO()
        if source_format == "WEBP":
            image.save(buffer, format="WEBP", quality=90, method=6, exact=True)
            method = "WebP-q90-original-canvas"
        else:
            image.save(buffer, format="PNG", optimize=True, compress_level=9)
            method = "PNG-lossless-optimize"
        candidate = buffer.getvalue()
        output = candidate if len(candidate) < len(source) else source
        if output is source:
            method = "original-smaller-than-candidate"
        size = list(image.size)
    with Image.open(io.BytesIO(output)) as image:
        after = np.array(image.convert("RGBA"))
    decoded = cv2.imdecode(np.frombuffer(output, np.uint8), cv2.IMREAD_UNCHANGED)
    if (after.shape != before.shape or not np.array_equal(before[:, :, 3], after[:, :, 3])
            or decoded is None or tuple(decoded.shape[:2]) != tuple(before.shape[:2])):
        raise ValueError(f"Canvas/alpha/decoder validation failed: {path}")
    rgb_exact = bool(np.array_equal(before, after))
    if source_format == "PNG" and not rgb_exact:
        raise ValueError(f"PNG changed decoded RGBA: {path}")
    record = dict(path=path, method=method, sourceCanvas=size, outputCanvas=size,
                  alphaExact=True, rgbExact=rgb_exact, sourceBytes=len(source), sourceSHA256=sha(source),
                  bytes=len(output), sha256=sha(output), sourceToOutput=copy.deepcopy(IDENTITY),
                  outputToOriginal=copy.deepcopy(IDENTITY))
    return path, output, record


def replace_hashes(value, changes, edits, location=""):
    """Refresh display hash references and byte counts; never rewrite geometry."""
    if isinstance(value, dict):
        old = value.get("sha256")
        if isinstance(old, str) and old in changes and "bytes" in value:
            value["bytes"] = changes[old]["bytes"]
        for key, item in list(value.items()):
            if isinstance(item, str) and item in changes and item != changes[item]["sha256"]:
                value[key] = changes[item]["sha256"]
                edits.append(location + "/" + key)
            else:
                replace_hashes(item, changes, edits, location + "/" + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            replace_hashes(item, changes, edits, location + "/" + str(index))


def patch_json(source: bytes, updated) -> bytes:
    """Patch changed JSON values, retaining every unchanged value's exact bytes.

    In particular, matrices keep their original lexical representation instead
    of being reformatted by a full JSON dump. Object additions are appended.
    Existing array shapes/object keys cannot be removed by this workflow.
    """
    text = source.decode("utf-8")
    decoder = json.JSONDecoder()

    def skip(index):
        while index < len(text) and text[index].isspace():
            index += 1
        return index

    def rewrite(start, old, new):
        _, end = decoder.raw_decode(text, start)
        if old == new:
            return text[start:end], end
        if isinstance(old, dict) and isinstance(new, dict):
            if not old.keys() <= new.keys():
                raise ValueError("Display update attempted to remove JSON fields")
            cursor, index, pieces = start, skip(start + 1), []
            for _ in old:
                key, key_end = decoder.raw_decode(text, index)
                value_start = skip(skip(key_end) + 1)  # Colon and whitespace.
                replacement, value_end = rewrite(value_start, old[key], new[key])
                pieces.extend((text[cursor:value_start], replacement))
                cursor = value_end
                index = skip(value_end)
                if text[index] == ",":
                    index = skip(index + 1)
            additions = {key: value for key, value in new.items() if key not in old}
            pieces.append(text[cursor:end - 1])
            if additions:
                pieces.append(("," if old else "") + json.dumps(additions, ensure_ascii=False)[1:-1])
            pieces.append("}")
            return "".join(pieces), end
        if isinstance(old, list) and isinstance(new, list):
            if len(old) != len(new):
                raise ValueError("Display update attempted to change a JSON array's shape")
            cursor, index, pieces = start, skip(start + 1), []
            for before, after in zip(old, new):
                replacement, value_end = rewrite(index, before, after)
                pieces.extend((text[cursor:index], replacement))
                cursor = value_end
                index = skip(value_end)
                if text[index] == ",":
                    index = skip(index + 1)
            pieces.append(text[cursor:end])
            return "".join(pieces), end
        return json.dumps(new, ensure_ascii=False), end

    start = skip(0)
    replacement, end = rewrite(start, json.loads(text), updated)
    output = (text[:start] + replacement + text[end:]).encode("utf-8")
    if json.loads(output) != updated:
        raise ValueError("Internal JSON patch validation failed")
    return output


def load_package(data: bytes):
    entries = read_zip(data)
    manifest = json.loads(entries.pop("map-package.json"))
    pid = manifest.get("packageId")
    if pid not in PACKAGE_IDS:
        raise ValueError(f"Unsupported packageId: {pid!r}")
    contents = {}
    for name, payload in entries.items():
        if not name.startswith("data/"):
            raise ValueError(f"Unexpected package member: {name}")
        contents[safe_path(name[5:])] = payload
    declared = set()
    for record in manifest["files"]:
        path = safe_path(record["path"])
        if path in declared or path not in contents:
            raise ValueError(f"Duplicate or missing manifest file: {path}")
        declared.add(path)
        if record["bytes"] != len(contents[path]) or record["sha256"] != sha(contents[path]):
            raise ValueError(f"Source manifest hash/size mismatch: {path}")
    if declared != contents.keys():
        raise ValueError("Source ZIP has undeclared payload files")
    for dependency in manifest["requiresFiles"]:
        safe_path(dependency["path"])
    return manifest, contents


def optimize_package(source: bytes, *, workers: int = 3):
    """Return an updated manifest, payloads and audit without writing files."""
    manifest, contents = load_package(source)
    original_manifest = copy.deepcopy(manifest)
    original = dict(contents)
    pid = manifest["packageId"]
    tasks = [(path, data) for path, data in contents.items() if display_asset(path)]
    changes, rows, edits = {}, [], {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for path, data, record in pool.map(lambda task: encode_display(task, allow_webp=pid == "difficult-zhan10"), tasks):
            changes[sha(contents[path])] = dict(bytes=len(data), sha256=sha(data))
            contents[path] = data
            rows.append(record)
    for path, data in original.items():
        if not path.endswith(".json") or path == NATIVE_MANIFEST:
            continue
        if pid != "difficult-zhan10" and not path.endswith("/catalog.json"):
            continue
        value = json.loads(data)
        locations = []
        replace_hashes(value, changes, locations)
        if path == FULL_REFERENCES:
            for record in value["entries"]:
                record["policy"] = "same-canvas Q90 display-only compressed derivative; no registration or recognition role"
        patched = patch_json(data, value)
        if patched != data:
            contents[path] = patched
            edits[path] = locations
    encoding = "WebP-q90" if pid == "difficult-zhan10" else "PNG-lossless-optimize"
    if pid == "difficult-zhan10":
        native = json.loads(contents[NATIVE_MANIFEST])
        locations = []
        replace_hashes(native, changes, locations)
        for record in native["files"]:
            path = safe_path("_internal/" + record["path"])
            record.update(bytes=len(contents[path]), sha256=sha(contents[path]))
        native["resourceBytes"] = sum(record["bytes"] for record in native["files"])
        full = json.loads(contents[FULL_REFERENCES])
        native["fullReferences"] = full["entries"]
        native["fullReferenceBytes"] = sum(record["bytes"] for record in full["entries"])
        for floor in native["floors"]:
            path = safe_path(f"_internal/assets/zhan10-native/{floor['mapId']}/floor-{floor['floor']}.lossless.webp")
            floor["displayBytes"] = len(contents[path])
        native["displayCompressionCandidate"] = dict(
            encoding=encoding, canvas="unchanged", alpha="exact", sourceArchiveSHA256=sha(source),
            legacyPNG="lossless optimized; original format retained")
        contents[NATIVE_MANIFEST] = patch_json(original[NATIVE_MANIFEST], native)
        edits[NATIVE_MANIFEST] = locations
        manifest["canonicalManifestSHA256"] = sha(contents[NATIVE_MANIFEST])
    manifest["displayCompressionCandidate"] = dict(
        encoding=encoding, originalCanvas=True, originalPaths=True, sourceArchiveSHA256=sha(source))
    for record in manifest["files"]:
        payload = contents[record["path"]]
        record.update(bytes=len(payload), sha256=sha(payload))
    changed = [path for path in contents if contents[path] != original[path]]
    forbidden = [path for path in changed if (not display_asset(path) and path not in edits)
                 or path.endswith(".npz") or "/recognition/" in path]
    if forbidden:
        raise ValueError(f"Protected structure/recognition payload changed: {forbidden}")
    report = dict(packageId=pid, sourceArchiveSHA256=sha(source), originalArchiveBytes=len(source),
                  originalExpandedBytes=sum(map(len, original.values())), candidateExpandedBytes=sum(map(len, contents.values())),
                  routeOriginalBytes=sum(len(data) for path, data in original.items() if display_asset(path)),
                  routeCandidateBytes=sum(len(data) for path, data in contents.items() if display_asset(path)),
                  changedFiles=changed, metadataEdits=edits, records=rows,
                  allStructureAndRecognitionNonDisplayPayloadUnchanged=True, allPathsUnchanged=True,
                  unchangedJSONValuesByteExact=True, requiresFilesUnchanged=manifest["requiresFiles"] == original_manifest["requiresFiles"])
    return manifest, contents, report


def write_package(path: Path, manifest, contents, *, manifest_source=None):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        # Stable member order/time; byte reproduction still depends on codec versions.
        manifest_bytes = patch_json(manifest_source, manifest) if manifest_source is not None else jsonbytes(manifest)
        for name, data in [("map-package.json", manifest_bytes)] + [("data/" + p, d) for p, d in sorted(contents.items())]:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError(f"Written archive failed CRC validation: {path.name}")


def build_packages(source_archive: Path, base_bundle: Path, output: Path, *, workers=3, expected_source_sha256=None):
    """Build into a new directory; every report path is relative to that directory."""
    source_archive, base_bundle, output = (Path(path).resolve() for path in (source_archive, base_bundle, output))
    if output.exists() or output == base_bundle or base_bundle in output.parents or output in base_bundle.parents:
        raise ValueError("Output must be a new directory outside the source base bundle")
    if workers < 1:
        raise ValueError("workers must be positive")
    source_bytes = source_archive.read_bytes()
    source_hash = sha(source_bytes)
    if expected_source_sha256 is not None and source_hash != expected_source_sha256.lower():
        raise ValueError("Source archive does not match --expected-source-sha256")
    outer = read_zip(source_bytes)
    packages = [(name, data, load_package(data)[0]["packageId"]) for name, data in outer.items() if name.endswith(".zip")]
    package_ids = [pid for _name, _data, pid in packages]
    if not packages or len(set(package_ids)) != len(package_ids) or "nightmare-suju" not in package_ids:
        raise ValueError("Expected unique current package IDs including nightmare-suju for the base catalog prerequisite")
    catalog_bytes = (base_bundle / BASE_CATALOG).read_bytes()
    fingerprint_bytes = (base_bundle / "_internal/references/build-fingerprint.txt").read_bytes()
    catalog = json.loads(catalog_bytes)
    for _name, data, _pid in packages:
        manifest, _contents = load_package(data)
        for dependency in manifest["requiresFiles"]:
            if dependency["path"] == BASE_CATALOG and (
                    dependency["sha256"] != sha(catalog_bytes) or dependency["bytes"] != len(catalog_bytes)):
                raise ValueError("Source package requires a different base catalog than --base-bundle")
    source_lock = dict(schema="xiaochao-display-optimization-source-lock-v1",
                       sourceArchive=dict(name=source_archive.name, bytes=len(source_bytes), sha256=source_hash),
                       packages=[dict(member=name, packageId=pid, bytes=len(data), sha256=sha(data)) for name, data, pid in packages],
                       baseCatalog=dict(path=BASE_CATALOG, bytes=len(catalog_bytes), sha256=sha(catalog_bytes)),
                       baseFingerprint=dict(path="_internal/references/build-fingerprint.txt", sha256=sha(fingerprint_bytes),
                                            value=fingerprint_bytes.decode("ascii").strip()),
                       toolSHA256=sha(Path(__file__).read_bytes()),
                       dependencies=dict(Pillow=pillow_version, numpy=np.__version__, OpenCV=cv2.__version__),
                       strategy=dict(webpQuality=90, webpMethod=6, exactAlpha=True, resize=False, png="lossless"))
    output.mkdir(parents=True)
    # A failed build leaves its source lock and partial directory for inspection;
    # reruns must use a different new destination, never overwrite evidence.
    (output / "source-lock.json").write_bytes(jsonbytes(source_lock))
    results, suju_changes = [], None
    for name, data, pid in packages:
        manifest, contents, report = optimize_package(data, workers=workers)
        if pid == "nightmare-suju":
            suju_changes = {record["sourceSHA256"]: dict(bytes=record["bytes"], sha256=record["sha256"]) for record in report["records"]}
        results.append((manifest, contents, report, read_zip(data)["map-package.json"]))
    edits = []
    replace_hashes(catalog, suju_changes, edits)
    new_catalog = patch_json(catalog_bytes, catalog)
    prerequisite = output / "base-prerequisite" / BASE_CATALOG
    prerequisite.parent.mkdir(parents=True)
    prerequisite.write_bytes(new_catalog)
    reports = []
    for manifest, contents, report, manifest_source in results:
        dependencies_before = copy.deepcopy(manifest["requiresFiles"])
        for dependency in manifest["requiresFiles"]:
            if dependency["path"] == BASE_CATALOG:
                if dependency["sha256"] != sha(catalog_bytes) or dependency["bytes"] != len(catalog_bytes):
                    raise ValueError("Source package requires a different base catalog than --base-bundle")
                dependency.update(bytes=len(new_catalog), sha256=sha(new_catalog))
        manifest["displayCompressionCandidate"]["basePrerequisiteCatalogSHA256"] = sha(new_catalog)
        package_name = manifest["packageId"] + "-display-optimized-r1.zip"
        package = output / package_name
        write_package(package, manifest, contents, manifest_source=manifest_source)
        stage = output / "staging" / manifest["packageId"]
        for path, data in contents.items():
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        # Portable path is resolved by consumers against the manifest directory.
        report.update(path=package_name, bytes=package.stat().st_size, sha256=sha(package.read_bytes()),
                      requiresFilesUnchanged=manifest["requiresFiles"] == dependencies_before,
                      basePrerequisite=dict(path=BASE_CATALOG, bytes=len(new_catalog), sha256=sha(new_catalog)))
        reports.append(report)
    audit = dict(sourceLock="source-lock.json", sourceFingerprint=source_lock["baseFingerprint"]["value"],
                 path=BASE_CATALOG, originalBytes=len(catalog_bytes), originalSha256=sha(catalog_bytes),
                 candidateBytes=len(new_catalog), candidateSha256=sha(new_catalog), changedFields=edits,
                 geometryAndPathsUnchanged=True, unchangedJSONValuesByteExact=True,
                 deliveryMode="base-build prerequisite overlay; not installable by the additive map importer")
    (output / "base-prerequisite-audit.json").write_bytes(jsonbytes(audit))
    (output / "build-manifest.json").write_bytes(jsonbytes(reports))
    sums = [report["sha256"] + "  " + report["path"] for report in reports]
    sums.append(sha(new_catalog) + "  base-prerequisite/" + BASE_CATALOG)
    (output / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return reports


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, required=True, help="Outer ZIP containing current map-package ZIPs")
    parser.add_argument("--base-bundle", type=Path, required=True, help="Read-only base root containing _internal/references")
    parser.add_argument("--output", type=Path, required=True, help="New candidate directory outside the base root")
    parser.add_argument("--workers", type=int, default=3, help="Concurrent image encoders (default: 3)")
    parser.add_argument("--expected-source-sha256", help="Optional pinned outer ZIP SHA256; mismatch stops before writing")
    args = parser.parse_args(argv)
    try:
        reports = build_packages(args.source_archive, args.base_bundle, args.output,
                                 workers=args.workers, expected_source_sha256=args.expected_source_sha256)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, cv2.error) as error:
        parser.error(str(error))
    print(json.dumps(dict(packages=len(reports), manifest="build-manifest.json", sourceLock="source-lock.json")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
