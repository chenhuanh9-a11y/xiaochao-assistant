"""Offline data-only map import adapter. Not yet bundled with the old client."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import unicodedata
import zipfile


MAX_FILES = 2000
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 1536 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MANIFEST = "map-package.json"
AUTHOR_PACKAGES = {
    "difficult-aichicu": "aichicu-difficult",
    "nightmare-wang-qingxin": "wang-qingxin",
    "nightmare-nightingale": "nightingale",
    "nightmare-lianghapi": "lianghapi-single",
}
AUTHOR_SIDECARS = {"catalog.json", "display-bindings.json"}
LIVE_AUTHOR_IMAGE_ROOTS = {
    "difficult-aichicu": ("_internal/assets/authors/aichicu-difficult/",),
    "nightmare-suju": ("_internal/assets/suju/",),
    "difficult-zhan10": ("_internal/assets/zhan10-native/", "_internal/assets/zhan10-legacy-clean/"),
    "nightmare-wang-qingxin": ("_internal/assets/authors/wang-qingxin/",),
    "nightmare-nightingale": ("_internal/assets/authors/nightingale/",),
    "nightmare-lianghapi": ("_internal/assets/authors/lianghapi-single/",),
}
PACKAGE_IDS = {"nightmare-suju", "difficult-zhan10"} | set(AUTHOR_PACKAGES)
SUPPORTED_DISPLAY_VERSIONS = {"1.0", "1.0.1", "1.0.2", "1.0.3", "1.0.4", "1.0.5"}
CLIENT_COMPATIBILITY = {"displayVersion": "1.0", "numericLine": "1.0.0.N",
                        "minimumBuild": 1, "maximumBuild": 65535}
BASE_DEPENDENCIES = {
    "_internal/references/nightmare-author-route-catalog.json",
    "_internal/references/nightmare-canonical-coordinate-pack.json",
    "_internal/references/nightmare-shared-structure-index.json",
    "_internal/references/nightmare-structure-version-bindings.json",
}
DIFFICULT_AUTHOR_DEPENDENCIES = {
    "_internal/references/difficult-shared-structure-index.json",
    "_internal/references/difficult-native-coordinate-pack.json",
    "_internal/references/difficult-native-source-manifest.json",
    "_internal/references/difficult-native-version-bindings.json",
}


def author_profiles_for_mode(mode):
    return tuple(profile for package, profile in AUTHOR_PACKAGES.items()
                 if package.startswith(str(mode) + "-"))


# Exact canonical r6 runtime closure: 58 native floors and two legacy-clean floors.
# Keep the older difficult package rules below for existing package compatibility.
DIFFICULT_R6_PATHS = frozenset({
    "_internal/assets/zhan10-legacy-clean/difficult-map-29/floor-1.png",
    "_internal/assets/zhan10-legacy-clean/difficult-map-29/floor-2.png",
    "_internal/assets/zhan10-legacy-clean/difficult-map-29/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-01/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-01/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-01/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-02/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-02/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-02/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-03/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-03/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-03/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-04/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-04/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-04/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-05/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-05/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-05/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-06/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-06/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-06/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-07/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-07/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-07/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-08/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-08/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-08/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-09-2/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-09-2/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-09-2/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-09/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-09/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-09/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-10/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-10/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-10/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-11/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-11/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-11/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-12/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-12/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-12/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-13/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-13/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-13/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-14/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-14/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-14/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-15/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-15/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-15/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-16/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-16/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-16/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-17/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-17/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-17/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-18/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-18/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-18/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-19/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-19/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-19/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-20/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-20/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-20/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-21/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-21/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-21/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-22/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-22/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-22/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-23/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-23/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-23/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-24/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-24/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-24/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-25/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-25/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-25/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-26/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-26/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-26/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-27/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-27/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-27/full-reference.webp",
    "_internal/assets/zhan10-native/difficult-map-28/floor-1.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-28/floor-2.lossless.webp",
    "_internal/assets/zhan10-native/difficult-map-28/full-reference.webp",
    "_internal/difficult-native-manifest.json",
    "_internal/difficult-native-validation.json",
    "_internal/recognition/legacy-clean-map29/entries.json",
    "_internal/recognition/legacy-clean-map29/entry-passports.json",
    "_internal/recognition/legacy-clean-map29/fixed-batch30/map-29-F1.npz",
    "_internal/recognition/legacy-clean-map29/fixed-batch30/map-29-F2.npz",
    "_internal/recognition/legacy-clean-map29/index.json",
    "_internal/recognition/legacy-clean-map29/side-entries.json",
    "_internal/recognition/native/entries.json",
    "_internal/recognition/native/side-entries.json",
    "_internal/references/difficult-exit-icon.json",
    "_internal/references/difficult-legacy-clean-coordinate-pack.json",
    "_internal/references/difficult-native-author-catalog.json",
    "_internal/references/difficult-native-coordinate-bridges.json",
    "_internal/references/difficult-native-coordinate-pack.json",
    "_internal/references/difficult-native-full-references.json",
    "_internal/references/difficult-native-identity-pack.json",
    "_internal/references/difficult-native-source-manifest.json",
    "_internal/references/difficult-native-structure-display.json",
    "_internal/references/difficult-native-version-bindings.json",
    "_internal/references/difficult-native/contours.json",
    "_internal/references/difficult-native/difficult-map-01-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-01-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-02-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-02-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-03-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-03-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-04-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-04-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-05-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-05-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-06-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-06-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-07-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-07-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-08-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-08-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-09-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-09-2-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-09-2-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-09-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-10-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-10-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-11-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-11-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-12-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-12-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-13-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-13-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-14-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-14-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-15-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-15-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-16-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-16-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-17-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-17-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-18-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-18-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-19-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-19-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-20-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-20-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-21-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-21-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-22-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-22-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-23-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-23-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-24-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-24-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-25-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-25-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-26-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-26-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-27-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-27-2-shared.npz",
    "_internal/references/difficult-native/difficult-map-28-1-shared.npz",
    "_internal/references/difficult-native/difficult-map-28-2-shared.npz",
    "_internal/references/difficult-shared-structure-index.json",
    "_internal/references/difficult-ui-controls.npz",
})
DIFFICULT_METADATA = {
    "difficult-author-structure-index.json", "difficult-exit-icon.json",
    "difficult-runtime.json", "difficult-ui-controls.npz",
}


class ImportRejected(ValueError):
    """Display the reason to the user; never retry with elevation."""


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ImportRejected("invalid-path")
    if "\\" in value or ":" in value or value.startswith("/") or "\x00" in value:
        raise ImportRejected("unsafe-path")
    if unicodedata.normalize("NFC", value) != value:
        raise ImportRejected("noncanonical-path")
    parts = value.split("/")
    for part in parts:
        if (not part or part in {".", ".."} or part.endswith((".", " "))
                or any(ord(c) < 32 or c in '<>"|?*' for c in part)
                or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])(?:\..*)?", part)):
            raise ImportRejected("unsafe-windows-path")
    return str(PurePosixPath(value))


def _allowed(package_id: str, path: str) -> bool:
    if package_id == "difficult-zhan10" and path in DIFFICULT_R6_PATHS:
        return True
    author = AUTHOR_PACKAGES.get(package_id)
    if author is not None:
        prefix = f"_internal/assets/authors/{author}/"
        if path.startswith(prefix):
            basename = path[len(prefix):]
            return bool(basename) and "/" not in basename and basename.endswith(".png")
        return path in {f"_internal/references/author-packs/{author}/{name}" for name in AUTHOR_SIDECARS}
    if package_id == "difficult-zhan10" and path == "_internal/assets/difficult-maps/README.md":
        return True  # The original release's one bundled, non-executable readme.
    if path.rsplit(".", 1)[-1].lower() not in {"json", "png", "jpg", "jpeg", "webp", "npz"}:
        return False
    if package_id == "nightmare-suju":
        return path.startswith("_internal/assets/suju/")
    return (path.startswith(("_internal/assets/difficult-maps/",
                             "_internal/references/difficult-structure/",
                             "_internal/references/difficult-tilemaps/",
                             "_internal/references/transparent-display/display-clean/difficult/"))
            or path == "_internal/references/transparent-display/index.json"
            or path in {"_internal/references/" + name for name in DIFFICULT_METADATA})


def _no_links(path: Path) -> None:
    # Check lexical ancestors too: resolving first would conceal junctions.
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ImportRejected(f"linked-target: {item}")


def _target(root: Path, relative: str) -> Path:
    path = root.joinpath(*_relative(relative).split("/"))
    _no_links(path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ImportRejected("target-outside-root")
    return path


def _sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _json(data: bytes) -> dict:
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ImportRejected("duplicate-json-key")
            result[key] = value
        return result
    try:
        result = json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique)
    except (ValueError, UnicodeError) as error:
        raise ImportRejected("invalid-json") from error
    if not isinstance(result, dict):
        raise ImportRejected("manifest-not-object")
    return result


def _rows(rows, *, dependencies=False):
    if not isinstance(rows, list) or not rows or len(rows) > MAX_FILES:
        raise ImportRejected("invalid-file-inventory")
    found = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise ImportRejected("invalid-file-record")
        path = _relative(row["path"])
        key = path.casefold()
        if (key in found or type(row["bytes"]) is not int or not 0 <= row["bytes"] <= MAX_FILE_BYTES
                or not isinstance(row["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", row["sha256"])):
            raise ImportRejected("invalid-or-duplicate-file-record")
        if dependencies and not path.startswith("_internal/references/"):
            raise ImportRejected("invalid-dependency-path")
        found[key] = row
    return found


def _owned_inventory(root: Path, package_id: str):
    """Only an installed receipt proves ownership; incoming archives cannot claim it."""
    path = _target(root, f"work/map-packages/{package_id}.json")
    if not path.exists():
        return {}, None
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ImportRejected("invalid-installed-package-receipt")
    raw = path.read_bytes()
    receipt = _json(raw)
    if (receipt.get("packageId") != package_id or receipt.get("revision") != 1
            or not isinstance(receipt.get("archiveSha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["archiveSha256"])):
        raise ImportRejected("invalid-installed-package-receipt")
    rows = _rows(receipt.get("files"))
    if any(not _allowed(package_id, row["path"]) for row in rows.values()):
        raise ImportRejected("invalid-installed-package-receipt")
    return rows, hashlib.sha256(raw).hexdigest()


def _pending_upgrade(root: Path):
    if _target(root, "work/map-packages/pending-upgrade.json").exists():
        raise ImportRejected("upgrade-recovery-required; run recover with application closed")


def _validate(archive: zipfile.ZipFile, root: Path, stage: Path | None = None) -> dict:
    infos = archive.infolist()
    if not 2 <= len(infos) <= MAX_FILES + 1:
        raise ImportRejected("invalid-archive-count")
    entries = {}
    for entry in infos:
        path = _relative(entry.filename)
        kind = stat.S_IFMT(entry.external_attr >> 16)
        if (entry.orig_filename != entry.filename or entry.is_dir() or kind not in {0, stat.S_IFREG}
                or entry.flag_bits & 1 or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or entry.external_attr & 0x400 or entry.file_size > MAX_FILE_BYTES):
            raise ImportRejected("unsupported-archive-entry")
        key = path.casefold()
        if key in entries:
            raise ImportRejected("duplicate-archive-path")
        entries[key] = entry
    info = entries.get(MANIFEST)
    if info is None or info.filename != MANIFEST or info.file_size > 2 * 1024 * 1024:
        raise ImportRejected("manifest-missing-or-too-large")
    metadata = _json(archive.read(info))
    if (type(metadata.get("schemaVersion")) is not int or metadata["schemaVersion"] != 1
            or type(metadata.get("revision")) is not int or metadata["revision"] != 1
            or metadata.get("packageId") not in PACKAGE_IDS):
        raise ImportRejected("unsupported-package-version")
    package_id = metadata["packageId"]
    compatibility = metadata.get("clientCompatibility")
    if (not isinstance(compatibility, dict) or compatibility != CLIENT_COMPATIBILITY
            or type(compatibility.get("minimumBuild")) is not int
            or type(compatibility.get("maximumBuild")) is not int):
        raise ImportRejected("unsupported-client-compatibility")
    rows = _rows(metadata.get("files"))
    dependencies = _rows(metadata.get("requiresFiles"), dependencies=True)
    required = DIFFICULT_AUTHOR_DEPENDENCIES if package_id == "difficult-aichicu" else BASE_DEPENDENCIES
    if not required <= {r["path"] for r in dependencies.values()}:
        raise ImportRejected("base-dependencies-not-declared")
    version_path = _target(root, "_internal/version.json")
    if not version_path.is_file() or version_path.stat().st_size > 1024 * 1024:
        raise ImportRejected("client-version-missing")
    version = _json(version_path.read_bytes())
    # Build revisions do not change the resource layout. Keep the observed
    # 1.0.0.N line compatible, while the actual 82 dependency hashes still bind
    # data schemas/coordinate versions. Unknown major/minor/patch fail closed.
    numeric = version.get("numericVersion")
    build = re.fullmatch(r"1\.0\.0\.([1-9][0-9]{0,4})", numeric) if isinstance(numeric, str) else None
    # 1.0.1 changes only the desktop UI and update notices, not the resource layout.
    if version.get("displayVersion") not in SUPPORTED_DISPLAY_VERSIONS or build is None or int(build[1]) > 65535:
        raise ImportRejected("unsupported-client-version")
    for row in dependencies.values():
        dependency = _target(root, row["path"])
        if (not dependency.is_file() or dependency.stat().st_size != row["bytes"]
                or _sha(dependency) != row["sha256"]):
            raise ImportRejected(f"missing-or-incompatible-dependency: {row['path']}")
    expected = {"data/" + row["path"] for row in rows.values()}
    if set(entries) != {MANIFEST, *(p.casefold() for p in expected)}:
        raise ImportRejected("archive-inventory-mismatch")
    total = sum(row["bytes"] for row in rows.values())
    if total > MAX_TOTAL_BYTES:
        raise ImportRejected("archive-expanded-size-limit")
    existing = 0
    replacements, missing = [], []
    owned, receipt_digest = None, None
    for row in rows.values():
        path = row["path"]
        if not _allowed(package_id, path):
            raise ImportRejected(f"path-not-in-data-allowlist: {path}")
        member = entries[("data/" + path).casefold()]
        if member.filename != "data/" + path or member.file_size != row["bytes"]:
            raise ImportRejected("file-size-or-case-mismatch")
        target = _target(root, path)
        if target.exists():
            if not target.is_file() or target.stat().st_size != row["bytes"] or _sha(target) != row["sha256"]:
                if owned is None:
                    owned, receipt_digest = _owned_inventory(root, package_id)
                old = owned.get(path.casefold())
                if (old is None or not target.is_file() or target.stat().st_size != old["bytes"]
                        or _sha(target) != old["sha256"]):
                    raise ImportRejected(f"existing-data-conflict: {path}")
                for other in PACKAGE_IDS - {package_id}:
                    other_rows, _ = _owned_inventory(root, other)
                    if path.casefold() in other_rows:
                        raise ImportRejected(f"shared-package-ownership-conflict: {path}")
                replacements.append(old)
            else:
                existing += 1
        else:
            missing.append(row)
        digest = hashlib.sha256()
        count = 0
        sink = None
        try:
            if stage is not None:
                staged = stage.joinpath(*path.split("/"))
                staged.parent.mkdir(parents=True, exist_ok=True)
                sink = staged.open("xb")
            with archive.open(member) as source:
                while block := source.read(1024 * 1024):
                    count += len(block)
                    if count > row["bytes"]:
                        raise ImportRejected("expanded-file-size-limit")
                    digest.update(block)
                    if sink:
                        sink.write(block)
        finally:
            if sink:
                sink.close()
        # Reading through EOF also performs zipfile's CRC check.
        if count != row["bytes"] or digest.hexdigest() != row["sha256"]:
            raise ImportRejected(f"file-integrity-mismatch: {path}")
    return {"packageId": package_id, "revision": 1, "files": list(rows.values()),
            "fileCount": len(rows), "bytes": total, "alreadyPresent": existing,
            "replaceFiles": replacements, "missingFiles": missing,
            "previousReceiptSha256": receipt_digest,
            "restartRequired": True, "authentication": "not-signed; SHA256 integrity only"}


@contextmanager
def _open_archive(zip_path, expected_sha256=None):
    path = Path(zip_path)
    with path.open("rb") as handle:
        if os.fstat(handle.fileno()).st_size > MAX_ARCHIVE_BYTES:
            raise ImportRejected("archive-size-limit")
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise ImportRejected("selected-archive-changed")
        handle.seek(0)
        # Hashing and ZIP reading use the same open handle, not a reopened path.
        with zipfile.ZipFile(handle) as archive:
            yield archive, digest


def inspect_package(zip_path, install_root) -> dict:
    """Validate all bytes/CRC/dependencies without writing to the installation."""
    root = Path(os.path.abspath(install_root))
    _no_links(root)
    _pending_upgrade(root)
    try:
        with _open_archive(zip_path) as (archive, digest):
            result = _validate(archive, root)
            result["archiveSha256"] = digest
            return result
    except (zipfile.BadZipFile, OSError, KeyError, TypeError) as error:
        raise ImportRejected(f"package-unreadable: {error}") from error


def _check_closed(root):
    exe = _target(root, "第五人格小抄助手.exe")
    if os.name != "nt" or not exe.exists():
        return
    from ctypes import wintypes
    create = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(exe), 0x40000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ImportRejected("application-running-or-not-writable; close it; do not elevate")
    close = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close(handle)


def _rollback_published(path, identity, digest):
    """Remove only the published object while its staged hard link still exists.

    Windows binds identity/content checks and deletion to one open handle. Its
    sharing mode prevents writes and renames during those checks. An unavailable
    or changed target is left untouched; retaining an additive file is safer than
    deleting another writer's data. The POSIX fallback has a path-unlink race and
    does not offer the Windows concurrent-deletion guarantee.
    """
    native_handle = None
    api = None
    try:
        _no_links(path)
        if os.name == "nt":
            from ctypes import wintypes
            import msvcrt
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            api.CreateFileW.restype = wintypes.HANDLE
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            api.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                ctypes.c_void_p, wintypes.DWORD]
            # READ|DELETE, share READ only, open reparse point itself. No writer
            # or rename can race the same-handle checks and disposition below.
            native_handle = api.CreateFileW(str(path), 0x80010000, 1, None, 3, 0x200000, None)
            if native_handle == ctypes.c_void_p(-1).value:
                native_handle = None
                return False
            fd = msvcrt.open_osfhandle(int(native_handle), os.O_RDONLY | os.O_BINARY)
            native_handle = None  # CRT fd now owns and closes this handle.
        else:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as current:
            info = os.fstat(current.fileno())
            if (info.st_dev, info.st_ino) != identity or not stat.S_ISREG(info.st_mode):
                return False
            if hashlib.file_digest(current, "sha256").hexdigest() != digest:
                return False
            if os.name == "nt":
                # FILE_DISPOSITION_INFO contains one BOOLEAN, not a Win32 BOOL.
                disposition = ctypes.c_ubyte(1)
                if not api.SetFileInformationByHandle(msvcrt.get_osfhandle(current.fileno()),
                        4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
                    return False
            else:
                latest = path.lstat()
                if (latest.st_dev, latest.st_ino) != identity:
                    return False
                path.unlink()  # POSIX limitation is explicit in the docstring.
        return True
    except OSError:
        return False
    finally:
        if native_handle is not None:
            api.CloseHandle(native_handle)


@contextmanager
def _writer_locked_installation(install_root, *, require_closed):
    """One OS writer lock for closed upgrades and additive idle author imports."""
    root = Path(os.path.abspath(install_root))
    _no_links(root)
    if not root.is_dir():
        raise ImportRejected("installation-root-missing")
    if require_closed:
        _check_closed(root)
    state = _target(root, "work/map-packages")
    state.mkdir(parents=True, exist_ok=True)
    lock = _target(root, "work/map-packages/import.lock")
    # OS-released exclusive lock avoids a stale lock after interpreter/power loss.
    with lock.open("a+b") as lock_file:
        try:
            if os.name == "nt":
                import msvcrt
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ImportRejected("another-import-is-running") from error
        yield root, state


@contextmanager
def _locked_installation(install_root, application_stopped):
    if application_stopped is not True:
        raise ImportRejected("close-application-first")
    with _writer_locked_installation(install_root, require_closed=True) as installation:
        yield installation


def recover_package_upgrade(install_root, *, application_stopped=False):
    """Recover an interrupted owned-resource transaction, preserving unknown files."""
    from map_package_upgrade import recover_pending_upgrade
    with _locked_installation(install_root, application_stopped) as (root, _):
        return recover_pending_upgrade(root)


def rollback_package_upgrade(install_root, transaction_id, *, application_stopped=False):
    from map_package_upgrade import rollback_upgrade
    with _locked_installation(install_root, application_stopped) as (root, _):
        return rollback_upgrade(root, transaction_id)


def _live_author_path(package_id, path):
    """Only display pixels and the two exact sidecars of each optional author."""
    if path.startswith(LIVE_AUTHOR_IMAGE_ROOTS.get(package_id, ())):
        return PurePosixPath(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    author = AUTHOR_PACKAGES.get(package_id)
    return author is not None and path in {
        f"_internal/references/author-packs/{author}/{name}" for name in AUTHOR_SIDECARS
    }


def supports_live_author_import(result) -> bool:
    """Classify an already-validated inspection snapshot without reading files.

    This is a UI readiness hint, not an installation authorization: the live
    installer still stages and validates the archive under the OS writer lock.
    """
    if not isinstance(result, dict):
        return False
    package_id = result.get('packageId')
    replacements, missing = result.get('replaceFiles'), result.get('missingFiles')
    if (not isinstance(package_id, str) or package_id not in LIVE_AUTHOR_IMAGE_ROOTS
            or not isinstance(replacements, list) or replacements
            or not isinstance(missing, list)):
        return False
    return all(isinstance(row, dict) and isinstance(row.get('path'), str)
               and _live_author_path(package_id, row['path']) for row in missing)


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise ImportRejected("import-cancelled")


def _commit_additions(root, result, stage, *, live=False, cancelled=None):
    """The shared additive transaction; staged hard links remain rollback witnesses."""
    created = []
    try:
        _check_cancelled(cancelled)
        for row in result["files"]:
            _check_cancelled(cancelled)
            target = _target(root, row["path"])
            if target.exists():
                # Recheck after staging: fail closed on concurrent changes.
                if target.stat().st_size != row["bytes"] or _sha(target) != row["sha256"]:
                    raise ImportRejected("target-changed-during-import")
                continue
            # A core file disappearing after live validation is never repaired
            # by an in-process author import, even if its staged hash is valid.
            if live and not _live_author_path(result['packageId'], row['path']):
                raise ImportRejected("live-import-requires-restart")
            target.parent.mkdir(parents=True, exist_ok=True)
            _no_links(target.parent)
            # Same-volume hard link publishes a complete file exclusively.
            # It cannot overwrite a file concurrently created by another writer.
            staged = stage.joinpath(*row["path"].split("/"))
            published = staged.stat()  # Capture the source BEFORE link/target races.
            identity = (published.st_dev, published.st_ino)
            _check_cancelled(cancelled)
            os.link(staged, target)
            created.append((target, identity, row["sha256"]))
        _check_cancelled(cancelled)
        result["added"] = len(created)
        if live:
            result["restartRequired"] = False
            result["liveImport"] = True
            result["authorFilesAdded"] = len(created)
            result["authorFilesAlreadyPresent"] = sum(
                _live_author_path(result['packageId'], row['path']) for row in result['files']
            ) - len(created)
            result["status"] = "author-already-installed" if not created else "author-installed-live"
        else:
            result["status"] = "already-installed" if not created else "installed-restart-required"
        receipt = _target(root, f"work/map-packages/{result['packageId']}.json")
        if not created and receipt.exists():
            owned, _ = _owned_inventory(root, result['packageId'])
            if owned == _rows(result['files']):
                # A no-op must retain the receipt inode/transaction witness
                # so an earlier owned upgrade can still be rolled back.
                previous = _json(receipt.read_bytes())
                if previous.get('upgradeTransactionId'):
                    result['upgradeTransactionId'] = previous['upgradeTransactionId']
                result['receiptPreserved'] = True
                return result
        temporary = stage / "receipt.json"
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        _check_cancelled(cancelled)
        # Receipt replacement is the commit point. Cancellation before it
        # rolls back additions; cancellation observed by the caller afterward
        # cannot turn a committed import into a reported transaction failure.
        os.replace(temporary, receipt)
        return result
    except Exception:
        # Retain staged hard links through rollback: their file IDs cannot
        # be recycled after another writer replaces a destination.
        for path, identity, digest in reversed(created):
            _rollback_published(path, identity, digest)
        raise


def _install_staged(zip_path, root, state, *, expected_archive_sha256=None, live=False, cancelled=None):
    _pending_upgrade(root)
    _check_cancelled(cancelled)
    with tempfile.TemporaryDirectory(prefix="staging-", dir=state) as directory:
        stage = Path(directory)
        with _open_archive(zip_path, expected_archive_sha256) as (archive, digest):
            result = _validate(archive, root, stage)
            result["archiveSha256"] = digest
        if live and not supports_live_author_import(result):
            raise ImportRejected("live-import-requires-restart")
        _check_cancelled(cancelled)
        if result["replaceFiles"]:
            from map_package_upgrade import commit_upgrade
            return commit_upgrade(root, result, stage)
        return _commit_additions(root, result, stage, live=live, cancelled=cancelled)


def install_package(zip_path, install_root, *, application_stopped=False, expected_archive_sha256=None) -> dict:
    """Append data, or transactionally upgrade unchanged files owned by this package.

    UI must run this off its Tk thread with the application closed. Unknown or
    edited files remain conflicts. A failed owned upgrade rolls back; interruption
    retains its journal/backups for explicit recovery before the next inspection.
    """
    with _locked_installation(install_root, application_stopped) as (root, state):
        return _install_staged(zip_path, root, state, expected_archive_sha256=expected_archive_sha256)


def install_author_package_live(zip_path, install_root, *, application_idle=False,
                                expected_archive_sha256=None, cancelled=None) -> dict:
    """Append optional author art while the caller owns an idle application.

    The caller must keep its idle/generation token valid until completion and
    expose invalidation through ``cancelled``. No EXE-exclusive-open test is
    used here; the same OS writer lock, archive checks and additive transaction
    still apply. Core repair and every owned replacement require closed import.
    """
    if application_idle is not True:
        raise ImportRejected("live-import-requires-idle")
    with _writer_locked_installation(install_root, require_closed=False) as (root, state):
        return _install_staged(zip_path, root, state, expected_archive_sha256=expected_archive_sha256,
                               live=True, cancelled=cancelled)


if __name__ == "__main__":
    # The transaction helper must reuse this module's exception and constants
    # when the adapter is launched directly instead of imported by the client.
    import sys
    sys.modules["star_map_import"] = sys.modules[__name__]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inspect", "install", "recover", "rollback"])
    parser.add_argument("archive")
    parser.add_argument("install_root")
    parser.add_argument("--application-closed", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "inspect":
            result = inspect_package(args.archive, args.install_root)
        elif args.action == "recover":
            result = recover_package_upgrade(args.install_root, application_stopped=args.application_closed)
        elif args.action == "rollback":
            result = rollback_package_upgrade(args.install_root, args.archive, application_stopped=args.application_closed)
        else:
            result = install_package(args.archive, args.install_root, application_stopped=args.application_closed)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ImportRejected, OSError, zipfile.BadZipFile) as error:
        print(json.dumps({"success": False, "reason": str(error)}, ensure_ascii=False))
        raise SystemExit(2)


def verified_installed_package(root, package_id, expected_sha256=None, *, cancelled=None):
    """Read receipt and actual contents off the UI thread; never trust download completion."""
    root = Path(root).resolve()
    try:
        _pending_upgrade(root)
        rows, receipt_digest = _owned_inventory(root, package_id)
        if not rows:
            return dict(status='not-installed')
        receipt = _json(_target(root, f"work/map-packages/{package_id}.json").read_bytes())
        for row in rows.values():
            if cancelled and cancelled():
                return dict(status='unverified')
            target = _target(root, row['path'])
            if not target.is_file() or target.stat().st_size != row['bytes'] or _sha(target) != row['sha256']:
                return dict(status='incomplete')
        _, after = _owned_inventory(root, package_id)
        if after != receipt_digest:
            return dict(status='unverified')
        digest = receipt['archiveSha256']
        return dict(status='update-available' if expected_sha256 and digest != expected_sha256 else 'installed',
                    archiveSha256=digest)
    except (OSError, ValueError, TypeError, KeyError):
        return dict(status='unverified')
