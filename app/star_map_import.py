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
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from packaged_product_identity import CLOSED_EXE_NAMES
from generic_author_package import is_generic_profile
from resource_transaction import (ResourceTransactionError, resource_read_lock,
                                  resource_write_lock, assert_resources_ready,
                                  preflight_hardlinks, preflight_resource_publication)


MAX_FILES = 2000
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 1536 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MANIFEST = "map-package.json"
AUTHOR_PACKAGES = {
    "nightmare-zhan10": "zhan10-nightmare",
    "nightmare-suju-speed": "suju-speed-nightmare",
    "difficult-suju-speed": "suju-speed-difficult",
    "difficult-aichicu": "aichicu-difficult",
    "nightmare-wang-qingxin": "wang-qingxin",
    "nightmare-nightingale": "nightingale",
    "nightmare-lianghapi": "lianghapi-single",
}
S1_AUTHOR_PACKAGE_ID = "s1-jiaye-handnotes"
S1_AUTHOR_PACKAGE_IDS = frozenset({S1_AUTHOR_PACKAGE_ID, "s1-aichicu-routes"})
AUTHOR_SIDECARS = {"catalog.json", "display-bindings.json"}
LIVE_AUTHOR_IMAGE_ROOTS = {
    "s1-aichicu-routes": ("_internal/assets/authors/s1-aichicu-routes/",),
    S1_AUTHOR_PACKAGE_ID: ("_internal/assets/authors/s1-jiaye-handnotes/",),
    "nightmare-zhan10": ("_internal/assets/authors/zhan10-nightmare/",),
    "nightmare-suju-speed": ("_internal/assets/authors/suju-speed-nightmare/",),
    "difficult-suju-speed": ("_internal/assets/authors/suju-speed-difficult/",),
    "difficult-aichicu": ("_internal/assets/authors/aichicu-difficult/",),
    "nightmare-suju": ("_internal/assets/suju/",),
    "difficult-zhan10": ("_internal/assets/zhan10-native/", "_internal/assets/zhan10-legacy-clean/"),
    "nightmare-wang-qingxin": ("_internal/assets/authors/wang-qingxin/",),
    "nightmare-nightingale": ("_internal/assets/authors/nightingale/",),
    "nightmare-lianghapi": ("_internal/assets/authors/lianghapi-single/",),
}
PACKAGE_IDS = {"nightmare-suju", "difficult-zhan10", S1_AUTHOR_PACKAGE_ID} | set(AUTHOR_PACKAGES) | S1_AUTHOR_PACKAGE_IDS
def known_package_id(value):
    return isinstance(value, str) and (value in PACKAGE_IDS or is_generic_profile(value))


def package_ids(root):
    from generic_author_package import profile_ids
    return PACKAGE_IDS | set(profile_ids(Path(root) / '_internal', strict=True))


SUPPORTED_DISPLAY_VERSIONS = {"1.0", "1.0.1", "1.0.2", "1.0.3", "1.0.4", "1.0.5", "1.0.6", "1.0.7", "1.0.17", "1.1.0", "1.1.1", "1.1.2"}
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


def author_profiles_for_mode(mode, season="s0"):
    from season_context import normalize_season
    if normalize_season(season) != "s0":
        return ()
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

    def __init__(self, reason, *, code=None, details=None):
        super().__init__(reason)
        candidate = code or re.split(r': |; ', str(reason), maxsplit=1)[0]
        self.code = candidate if re.fullmatch(r'[a-z][a-z0-9-]{0,95}', candidate) else 'import-rejected'
        self.details = dict(details or {})


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
    if is_generic_profile(package_id):
        from generic_author_package import allowed
        return allowed(path, package_id)
    if package_id in S1_AUTHOR_PACKAGE_IDS:
        from s1_author_package import allowed
        return allowed(path, package_id)
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


def _installed_inventory(root: Path, package_id: str, *, base_root=None):
    """Read ownership and dependency constraints from the same receipt bytes."""
    path = _target(root, f"work/map-packages/{package_id}.json")
    if not path.exists():
        return {}, {}, None
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ImportRejected("invalid-installed-package-receipt")
    raw = path.read_bytes()
    receipt = _json(raw)
    rows, dependencies = _receipt_inventory(receipt, package_id)
    if is_generic_profile(package_id):
        from generic_author_package import validate_receipt_closure
        # Startup alone may supply its fully verified prospective base plane;
        # ordinary mutation and display readers always validate the active root.
        validate_receipt_closure(root if base_root is None else base_root, receipt, rows, dependencies)
    return rows, dependencies, hashlib.sha256(raw).hexdigest()


def _reviewed_pre_dependency_contract(receipt, package_id, rows):
    """Restore only complete source-reviewed old archive contracts, read-only."""
    from legacy_author_receipt_contracts import (
        REVIEWED_PRE_DEPENDENCY_AUTHOR_RELEASES, REVIEWED_AUTHOR_DEPENDENCY_CONTRACTS)

    def inventory_digest(inventory):
        return hashlib.sha256(json.dumps(sorted(inventory.values(), key=lambda row: row['path']),
            sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()

    contracts = {}
    if (type(REVIEWED_AUTHOR_DEPENDENCY_CONTRACTS) is not tuple
            or type(REVIEWED_PRE_DEPENDENCY_AUTHOR_RELEASES) is not tuple):
        raise ImportRejected('invalid-reviewed-author-receipt-policy')
    for record in REVIEWED_AUTHOR_DEPENDENCY_CONTRACTS:
        if (type(record) is not tuple or len(record) != 2 or type(record[0]) is not str
                or not re.fullmatch(r'[a-f0-9]{64}', record[0]) or record[0] in contracts
                or type(record[1]) is not tuple
                or any(type(row) is not tuple or len(row) != 3 for row in record[1])):
            raise ImportRejected('invalid-reviewed-author-receipt-policy')
        dependencies = _rows([dict(zip(('path', 'bytes', 'sha256'), row))
                              for row in record[1]], dependencies=True)
        if inventory_digest(dependencies) != record[0]:
            raise ImportRejected('invalid-reviewed-author-receipt-policy')
        contracts[record[0]] = dependencies
    reviewed, seen = [], set()
    for record in REVIEWED_PRE_DEPENDENCY_AUTHOR_RELEASES:
        if (type(record) is not tuple or len(record) != 4 or type(record[0]) is not str
                or record[0] not in PACKAGE_IDS
                or record[0] in S1_AUTHOR_PACKAGE_IDS
                or any(type(value) is not str or not re.fullmatch(r'[a-f0-9]{64}', value)
                       for value in record[1:])
                or (record[0], record[1]) in seen or record[3] not in contracts):
            raise ImportRejected('invalid-reviewed-author-receipt-policy')
        seen.add((record[0], record[1]))
        reviewed.append(record)
    for package, archive, inventory, contract in reviewed:
        if (package == package_id and archive == receipt['archiveSha256']
                and inventory == inventory_digest(rows)):
            required = (DIFFICULT_AUTHOR_DEPENDENCIES if package in
                        {'difficult-aichicu', 'difficult-suju-speed'} else BASE_DEPENDENCIES)
            if not required <= {row['path'] for row in contracts[contract].values()}:
                raise ImportRejected('invalid-reviewed-author-receipt-policy')
            return contracts[contract]
    return None


def _receipt_inventory(receipt, package_id):
    """Shared strict receipt contract for import, ownership and removal."""
    if (receipt.get("packageId") != package_id or receipt.get("revision") != 1
            or not isinstance(receipt.get("archiveSha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["archiveSha256"])):
        raise ImportRejected("invalid-installed-package-receipt")
    if is_generic_profile(package_id):
        from generic_author_package import receipt_rows
        return receipt_rows(receipt)
    if package_id in S1_AUTHOR_PACKAGE_IDS:
        from s1_author_package import receipt_rows
        return receipt_rows(receipt)
    rows = _rows(receipt.get("files"))
    if any(not _allowed(package_id, row["path"]) for row in rows.values()):
        raise ImportRejected("invalid-installed-package-receipt")
    if "requiresFiles" not in receipt:
        reviewed_dependencies = _reviewed_pre_dependency_contract(receipt, package_id, rows)
        if reviewed_dependencies is not None:
            return rows, reviewed_dependencies
        # Other missing contracts must match the separate display-only legacy
        # adoption format. An unreviewed receipt cannot erase dependencies.
        from author_package_management import LEGACY_AUTHOR_INVENTORY
        release = LEGACY_AUTHOR_INVENTORY.get(package_id)
        legacy = receipt.get("legacyAdoption")
        expected_legacy = (dict(schemaVersion=1, scope="author-display-only",
            sourceArchive=release["archiveName"], completeDisplaySet=True) if release else None)
        approved = (_rows([{key: row[key] for key in ("path", "bytes", "sha256")}
                            for row in release["files"]]) if release else None)
        if (set(receipt) != {"packageId", "revision", "archiveSha256", "files", "legacyAdoption"}
                or not release or legacy != expected_legacy or rows != approved
                or receipt["archiveSha256"] != release["archiveSha256"]):
            raise ImportRejected("invalid-installed-package-dependencies")
        dependencies = {}
    else:
        dependencies = _rows(receipt["requiresFiles"], dependencies=True)
        required = (DIFFICULT_AUTHOR_DEPENDENCIES if package_id in
                    {"difficult-aichicu", "difficult-suju-speed"} else BASE_DEPENDENCIES)
        if not required <= {row["path"] for row in dependencies.values()}:
            raise ImportRejected("invalid-installed-package-dependencies")
    return rows, dependencies


def _owned_inventory(root: Path, package_id: str):
    """Only an installed receipt proves ownership; dependencies never grant it."""
    rows, _dependencies, digest = _installed_inventory(root, package_id)
    return rows, digest


def _check_other_package_constraints(root, package_id, incoming, replacements):
    """Preserve other packages' ownership and exact dependency contracts.

    Call under the resource lock during inspection and again before any commit
    or legacy adoption. A dependency is a constraint, not ownership: keeping
    its bytes unchanged must not prevent updating unrelated author images.
    """
    replaced = {row["path"].casefold() for row in replacements}
    for other in package_ids(root) - {package_id}:
        import author_resource_snapshots as snapshots
        if is_generic_profile(other) and snapshots.managed(root, other):
            activation, _ = snapshots._read_activation(root, other)
            owned = {}  # Logical image paths are not owned flat installation files.
            dependencies = (snapshots.SnapshotPackage(root, other).dependencies
                            if activation['snapshotId'] is not None else {})
        else:
            owned, dependencies, _digest = _installed_inventory(root, other)
        shared = replaced & owned.keys()
        if shared:
            path = owned[min(shared)]["path"]
            raise ImportRejected(f"shared-package-ownership-conflict: {path}")
        for key in incoming.keys() & dependencies.keys():
            new, required = incoming[key], dependencies[key]
            if (new["bytes"], new["sha256"]) != (required["bytes"], required["sha256"]):
                raise ImportRejected(f"shared-package-dependency-conflict: {new['path']}")


def _pending_upgrade(root: Path):
    try:
        assert_resources_ready(root)
    except ResourceTransactionError as error:
        raise ImportRejected(str(error), code=error.code, details=error.details) from error


def _validate(archive: zipfile.ZipFile, root: Path, stage: Path | None = None, *, season="s0", versioned=False) -> dict:
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
    _require_season(season)
    if metadata.get("season", "s0") != season:
        raise ImportRejected("unsupported-package-season" if season == "s0" else "package-season-mismatch")
    if (type(metadata.get("schemaVersion")) is not int or metadata["schemaVersion"] != 1
            or type(metadata.get("revision")) is not int or metadata["revision"] != 1
            or not known_package_id(metadata.get("packageId"))):
        raise ImportRejected("unsupported-package-version")
    package_id = metadata["packageId"]
    generic = is_generic_profile(package_id)
    if not generic and season != ("s1" if package_id in S1_AUTHOR_PACKAGE_IDS else "s0"):
        raise ImportRejected("package-season-mismatch")
    compatibility = metadata.get("clientCompatibility")
    if ((generic and compatibility != {"authorDisplayProtocol": 2}) or (not generic and
            (not isinstance(compatibility, dict) or compatibility != CLIENT_COMPATIBILITY
            or type(compatibility.get("minimumBuild")) is not int
            or type(compatibility.get("maximumBuild")) is not int))):
        raise ImportRejected("unsupported-client-compatibility")
    rows = _rows(metadata.get("files"))
    if generic:
        from generic_author_package import receipt_rows, validate_import_modes
        _, dependencies = receipt_rows(metadata)
        validate_import_modes(metadata, rows)
        required = set()  # Exact trusted base closure checked by the V2 validator.
    elif season == "s1":
        from s1_author_package import dependency_rows, required_dependencies
        dependencies = dependency_rows(metadata.get("requiresFiles"))
        required = required_dependencies()
    else:
        dependencies = _rows(metadata.get("requiresFiles"), dependencies=True)
        required = DIFFICULT_AUTHOR_DEPENDENCIES if package_id in {"difficult-aichicu", "difficult-suju-speed"} else BASE_DEPENDENCIES
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
    supported_versions = SUPPORTED_DISPLAY_VERSIONS | ({f"1.0.{n}" for n in range(8, 16)} if season == "s1" else set())
    if not generic and (version.get("displayVersion") not in supported_versions or build is None or int(build[1]) > 65535):
        raise ImportRejected("unsupported-client-version")
    from generic_author_package import effective_dependencies
    live_dependencies = effective_dependencies(dependencies, season) if generic else dependencies
    for row in (() if generic and versioned else live_dependencies.values()):
        dependency = _target(root, row["path"])
        missing = not dependency.exists()
        actual_bytes = dependency.stat().st_size if dependency.is_file() else None
        # Bound reads even when an unrelated/modified file occupies this path.
        actual_sha = _sha(dependency) if actual_bytes is not None and actual_bytes <= MAX_FILE_BYTES else None
        if actual_bytes != row["bytes"] or actual_sha != row["sha256"]:
            raise ImportRejected(f"missing-or-incompatible-dependency: {row['path']}",
                details=dict(path=row['path'], expectedBytes=row['bytes'], actualBytes=actual_bytes,
                             expectedSha256=row['sha256'], actualSha256=actual_sha, missing=missing))
    expected = {"data/" + row["path"] for row in rows.values()}
    if set(entries) != {MANIFEST, *(p.casefold() for p in expected)}:
        raise ImportRejected("archive-inventory-mismatch")
    total = sum(row["bytes"] for row in rows.values())
    if total > MAX_TOTAL_BYTES:
        raise ImportRejected("archive-expanded-size-limit")
    if generic:
        import author_resource_snapshots as snapshots
        if versioned:
            for row in rows.values():
                member = entries[("data/" + row["path"]).casefold()]
                if member.filename != "data/" + row["path"] or member.file_size != row["bytes"]:
                    raise ImportRejected("file-size-or-case-mismatch")
            return snapshots.inspect_validated_archive(metadata, archive, root, rows, dependencies)
        if snapshots.managed(root, package_id):
            raise ImportRejected("author-snapshot-use-versioned-import")
    if generic:
        from generic_author_package import validate_archive
        display_document = validate_archive(metadata, archive, root, rows, dependencies)
        previous = _target(root, f"work/map-packages/{package_id}.json")
        if previous.exists():
            if not previous.is_file() or previous.stat().st_size > 2 * 1024 * 1024:
                raise ImportRejected("invalid-installed-package-receipt")
            if _json(previous.read_bytes()).get('season') != season:
                raise ImportRejected("author-v2-package-season-cannot-change")
    elif season == "s1":
        from s1_author_package import validate_archive
        validate_archive(metadata, archive, root, rows, dependencies)
    existing = 0
    replacements, missing = [], []
    owned, receipt_digest = None, None
    legacy_receipt_digest = None
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
                    if receipt_digest is None:
                        # A supported old release can be identified without a
                        # prior UI refresh. Inspection stays read-only; the full
                        # approved set must match before it gains any authority.
                        from author_package_management import _legacy_author_plan_locked
                        legacy = _legacy_author_plan_locked(root, package_id)
                        if legacy['status'] == 'legacy-verified':
                            legacy_rows = _rows(legacy['receipt']['files'])
                            # Do not orphan verified old files by replacing the
                            # full-set receipt with a partial incoming package.
                            if legacy_rows.keys() <= rows.keys():
                                owned = legacy_rows
                                legacy_receipt_digest = legacy['receiptSha256']
                old = owned.get(path.casefold())
                if (old is None or not target.is_file() or target.stat().st_size != old["bytes"]
                        or _sha(target) != old["sha256"]):
                    raise ImportRejected(f"existing-data-conflict: {path}")
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
    installed, installed_dependencies, installed_digest = _installed_inventory(root, package_id)
    if generic and not installed.keys() <= rows.keys():
        # Existing transactions replace/add; they do not retire omitted payloads.
        # Do not silently orphan older images by dropping their receipt ownership.
        raise ImportRejected("author-v2-shrinking-package-requires-remove")
    if (installed_digest is not None and not replacements and not missing
            and installed == rows and installed_dependencies != dependencies):
        raise ImportRejected("dependency-contract-changed-reimport-unsupported")
    # Bind confirmations even when all payload bytes already match: a receipt
    # can change its dependency contract independently of the image files.
    if installed_digest is not None:
        receipt_digest = installed_digest
    _check_other_package_constraints(root, package_id, rows, replacements)
    return {**({"schema": metadata["schema"], "authorName": display_document["authorName"],
                "displayName": display_document["displayName"],
                "inactiveLayers": display_document.get('inactiveLayers', [])} if generic else
               ({"schema": metadata["schema"], "sourceVersion": metadata["sourceVersion"]} if season == "s1" else {})),
            "season": season, "packageId": package_id, "revision": 1, "files": list(rows.values()),
            "requiresFiles": list(dependencies.values()),
            "fileCount": len(rows), "bytes": total, "alreadyPresent": existing,
            "replaceFiles": replacements, "missingFiles": missing,
            "previousReceiptSha256": receipt_digest,
            **({'legacyReceiptSha256': legacy_receipt_digest} if legacy_receipt_digest else {}),
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


def _require_season(season):
    from generic_author_package import valid_season
    if not valid_season(season):
        raise ImportRejected("unsupported-package-season")


def inspect_package(zip_path, install_root, *, season="s0", versioned=False) -> dict:
    """Validate bytes/CRC/dependencies under the coordination lock; no resource writes."""
    _require_season(season)
    root = Path(os.path.abspath(install_root))
    _no_links(root)
    try:
        with resource_read_lock(root):
            _pending_upgrade(root)
            with _open_archive(zip_path) as (archive, digest):
                result = _validate(archive, root, season=season, versioned=versioned)
                result["archiveSha256"] = digest
                result["season"] = season
                return result
    except ResourceTransactionError as error:
        raise ImportRejected(str(error), code=error.code, details=error.details) from error
    except (zipfile.BadZipFile, OSError, KeyError, TypeError) as error:
        raise ImportRejected(f"package-unreadable: {error}") from error


def _check_closed(root):
    for name in CLOSED_EXE_NAMES:
        _check_executable_closed(_target(root, name))


def _check_executable_closed(exe):
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
    state = _target(root, "work/map-packages")
    try:
        with resource_write_lock(root, wait=True):
            # Recheck after lock acquisition; no new conforming runtime can
            # start between application closure and resource publication.
            if require_closed:
                _check_closed(root)
            yield root, state
    except ResourceTransactionError as error:
        reason = 'another-import-is-running' if error.code == 'resource-busy' else str(error)
        raise ImportRejected(reason, code=error.code, details=error.details) from error


@contextmanager
def _locked_installation(install_root, application_stopped):
    if application_stopped is not True:
        raise ImportRejected("close-application-first")
    with _writer_locked_installation(install_root, require_closed=True) as installation:
        yield installation


def recover_package_upgrade(install_root, *, application_stopped=False):
    """Recover an interrupted owned-resource transaction, preserving unknown files."""
    from map_package_upgrade import recover_pending_upgrade, recover_pending_additions
    with _locked_installation(install_root, application_stopped) as (root, _):
        import author_resource_snapshots as snapshots
        if _target(root, snapshots.PENDING).exists():
            return snapshots.recover_locked(root)
        result = recover_pending_additions(root)
        if result['status'] != 'no-pending-additions':
            return result
        return recover_pending_upgrade(root)


def rollback_package_upgrade(install_root, transaction_id, *, application_stopped=False):
    from map_package_upgrade import rollback_upgrade
    with _locked_installation(install_root, application_stopped) as (root, _):
        return rollback_upgrade(root, transaction_id)


def _live_author_path(package_id, path):
    """Only display pixels and the two exact sidecars of each optional author."""
    if package_id in S1_AUTHOR_PACKAGE_IDS or is_generic_profile(package_id):
        return _allowed(package_id, path)
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
    if (not isinstance(package_id, str) or (package_id not in LIVE_AUTHOR_IMAGE_ROOTS and not is_generic_profile(package_id))
            or not isinstance(replacements, list)
            or not isinstance(missing, list)):
        return False
    return all(isinstance(row, dict) and isinstance(row.get('path'), str)
               and _live_author_path(package_id, row['path']) for row in replacements + missing)


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise ImportRejected("import-cancelled")


def _set_addition_outcome(result, added, *, live):
    result['added'] = added
    if live:
        result.update(restartRequired=False, liveImport=True, authorFilesAdded=added,
            authorFilesAlreadyPresent=sum(_live_author_path(result['packageId'], row['path'])
                                         for row in result['files']) - added,
            status='author-installed-live' if added else 'author-already-installed')
    else:
        result['status'] = 'installed-restart-required' if added else 'already-installed'


def _commit_additions(root, result, stage, *, live=False, cancelled=None, publication_mode="hardlink-v1"):
    """The shared additive transaction; staged hard links remain rollback witnesses."""
    created = []
    import map_package_upgrade as transaction
    journal = (transaction._addition_journal(root)[2]
               if publication_mode == "move-v1" and result['missingFiles'] else None)
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
            if row not in result['missingFiles']:
                raise ImportRejected('target-changed-during-import')
            target.parent.mkdir(parents=True, exist_ok=True)
            _no_links(target.parent)
            # Same-volume hard link publishes a complete file exclusively.
            # It cannot overwrite a file concurrently created by another writer.
            staged = stage.joinpath(*row["path"].split("/"))
            published = staged.stat()  # Capture the source BEFORE link/target races.
            identity = (published.st_dev, published.st_ino)
            if publication_mode == "move-v1":
                # Use the identity persisted before pending publication, never
                # re-adopt a same-byte replacement from the current pathname.
                identity = tuple(journal['stagedIdentities'][row['path']])
            _check_cancelled(cancelled)
            if publication_mode == "move-v1":
                transaction._move_owned(staged, target, row, list(identity), linkless=True)
            else:
                os.link(staged, target)
            created.append((target, identity, row["sha256"]))
        _check_cancelled(cancelled)
        if len(created) != len(result['missingFiles']):
            raise ImportRejected('target-changed-during-import')
        _set_addition_outcome(result, len(created), live=live)
        receipt = _target(root, f"work/map-packages/{result['packageId']}.json")
        if not created and receipt.exists():
            owned, dependencies, _ = _installed_inventory(root, result['packageId'])
            if owned == _rows(result['files']):
                incoming_dependencies = (_receipt_inventory(result, result['packageId'])[1]
                    if result['packageId'] in S1_AUTHOR_PACKAGE_IDS or is_generic_profile(result['packageId']) else _rows(result['requiresFiles'], dependencies=True))
                if dependencies != incoming_dependencies:
                    raise ImportRejected('dependency-contract-changed-reimport-unsupported')
                # A no-op must retain the receipt inode/transaction witness
                # so retirement history keeps its exact receipt binding.
                previous = _json(receipt.read_bytes())
                if previous.get('upgradeTransactionId'):
                    result['upgradeTransactionId'] = previous['upgradeTransactionId']
                result['receiptPreserved'] = True
                return result
        temporary = stage / "receipt.json"
        if journal is None:
            temporary.write_bytes(json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8'))
        elif (transaction._read_json(temporary) != result
              or not transaction._matches(temporary, journal['newReceipt'], journal['newReceiptIdentity'])):
            raise ImportRejected('additions-staged-receipt-changed')
        _check_cancelled(cancelled)
        # Receipt replacement is the commit point. Cancellation before it
        # rolls back additions; cancellation observed by the caller afterward
        # cannot turn a committed import into a reported transaction failure.
        if journal is not None:
            for entry in journal['entries']:
                _check_cancelled(cancelled)
                if not transaction._matches(_target(root, entry['new']['path']), entry['new'], entry['newIdentity']):
                    raise ImportRejected('additions-data-changed-before-receipt-publication')
            old = journal['oldReceipt']
            if old is not None:
                transaction._move_owned(receipt, stage / 'old-receipt.json', old, old['identity'], linkless=True)
            transaction._move_owned(temporary, receipt, journal['newReceipt'], journal['newReceiptIdentity'], linkless=True)
        else:
            os.replace(temporary, receipt)
        return result
    except Exception:
        # Retain staged hard links through rollback: their file IDs cannot
        # be recycled after another writer replaces a destination.
        if publication_mode != "move-v1":
            for path, identity, digest in reversed(created):
                _rollback_published(path, identity, digest)
        # move-v1 recovery returns original new objects to the private stage,
        # retaining their identities until the durable rollback witness.
        raise


def _install_staged(zip_path, root, state, *, expected_archive_sha256=None, live=False, cancelled=None, season="s0", versioned=False):
    _require_season(season)
    _pending_upgrade(root)
    _check_cancelled(cancelled)
    stage = Path(tempfile.mkdtemp(prefix='staging-', dir=state))
    prepared = False
    try:
        with _open_archive(zip_path, expected_archive_sha256) as (archive, digest):
            result = _validate(archive, root, stage, season=season, versioned=versioned)
            if result.get('storageFormat'):
                import author_resource_snapshots as snapshots
                return snapshots.install_archive_locked(archive, digest, root, result, cancelled=cancelled)
            result["archiveSha256"] = digest
            result["season"] = season
        if live and not supports_live_author_import(result):
            raise ImportRejected("live-import-requires-restart")
        _check_cancelled(cancelled)
        publication_mode = preflight_resource_publication(root, [r['path'] for r in result['files']]
                            + [f"work/map-packages/{result['packageId']}.json"])
        _check_other_package_constraints(root, result['packageId'],
                                         _rows(result['files']), result['replaceFiles'])
        if result.get('legacyReceiptSha256'):
            # Every ZIP entry and dependency has now passed validation and the
            # writer/idle lease still holds. Register only this proven old set,
            # then reuse the normal rollback-safe upgrade and copy retirement.
            from author_package_management import _adopt_legacy_author_package_locked
            _adopt_legacy_author_package_locked(root, state, result['packageId'], cancelled=cancelled,
                expected_receipt_sha256=result['legacyReceiptSha256'])
            result['previousReceiptSha256'] = result['legacyReceiptSha256']
        if result["replaceFiles"]:
            from map_package_upgrade import commit_upgrade
            return commit_upgrade(root, result, stage, live=live, cancelled=cancelled, publication_mode=publication_mode)
        from map_package_upgrade import prepare_additions, recover_pending_additions
        prepared = prepare_additions(root, result, stage, live=live, publication_mode=publication_mode)
        try:
            output = _commit_additions(root, result, stage, live=live, cancelled=cancelled, publication_mode=publication_mode)
        except Exception:
            if prepared:
                recover_pending_additions(root)
            raise
        if prepared:
            recover_pending_additions(root)  # Verifies committed receipt/data before clearing marker.
        return output
    finally:
        # Process loss, incomplete rollback, or cleanup keeps witnesses for explicit
        # recovery; startup refuses resources while that marker is present.
        if (not prepared and stage.exists()
                and not _target(root, 'work/map-packages/pending-additions.json').exists()
                and not _target(root, 'work/map-packages/pending-upgrade.json').exists()
                and not _target(stage, 'addition-journal.json').exists()):
            shutil.rmtree(stage)


def install_package(zip_path, install_root, *, application_stopped=False, expected_archive_sha256=None, season="s0", versioned=False) -> dict:
    """Append data, or transactionally upgrade unchanged files owned by this package.

    UI must run this off its Tk thread with the application closed. Unknown or
    edited files remain conflicts. A failed owned upgrade rolls back; interruption
    retains its journal/backups for explicit recovery before the next inspection.
    """
    _require_season(season)
    with _locked_installation(install_root, application_stopped) as (root, state):
        return _install_staged(zip_path, root, state, expected_archive_sha256=expected_archive_sha256, season=season, versioned=versioned)


def install_author_package_live(zip_path, install_root, *, application_idle=False,
                                expected_archive_sha256=None, cancelled=None, season="s0", versioned=False) -> dict:
    """Install optional author art while the caller owns an idle application.

    The caller must keep its idle/generation token valid until completion and
    expose invalidation through ``cancelled``. No EXE-exclusive-open test is
    used here; the same OS writer lock and archive checks still apply. Owned,
    unchanged author pixels/sidecars may use the journaled upgrade transaction;
    core repair/replacement and interrupted-transaction recovery remain closed-only.
    """
    _require_season(season)
    if application_idle is not True:
        raise ImportRejected("live-import-requires-idle")
    with _writer_locked_installation(install_root, require_closed=False) as (root, state):
        return _install_staged(zip_path, root, state, expected_archive_sha256=expected_archive_sha256,
                               live=True, cancelled=cancelled, season=season, versioned=versioned)


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
    parser.add_argument("--season", choices=("s0", "s1"), default="s0")
    args = parser.parse_args()
    try:
        if args.action == "inspect":
            result = inspect_package(args.archive, args.install_root, season=args.season)
        elif args.action == "recover":
            result = recover_package_upgrade(args.install_root, application_stopped=args.application_closed)
        elif args.action == "rollback":
            result = rollback_package_upgrade(args.install_root, args.archive, application_stopped=args.application_closed)
        else:
            result = install_package(args.archive, args.install_root, application_stopped=args.application_closed, season=args.season)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ImportRejected, OSError, zipfile.BadZipFile) as error:
        print(json.dumps({"success": False, "reason": str(error)}, ensure_ascii=False))
        raise SystemExit(2)


def verified_installed_package(root, package_id, expected_sha256=None, *, cancelled=None):
    """Read receipt and actual contents off the UI thread; never trust download completion."""
    try:
        with resource_read_lock(root):
            return _verified_installed_package_locked(root, package_id, expected_sha256, cancelled=cancelled)
    except (OSError, ValueError):
        return dict(status='unverified')


def _verified_installed_package_locked(root, package_id, expected_sha256=None, *, cancelled=None):
    root = Path(root).resolve()
    try:
        _pending_upgrade(root)
        if is_generic_profile(package_id):
            import author_resource_snapshots as snapshots
            if snapshots.managed(root, package_id):
                return snapshots.verified(root, package_id, expected_sha256, cancelled=cancelled)
        rows, dependencies, receipt_digest = _installed_inventory(root, package_id)
        if not rows:
            return dict(status='not-installed')
        receipt = _json(_target(root, f"work/map-packages/{package_id}.json").read_bytes())
        # A complete author payload still depends on the exact installed base.
        # Dependencies are compatibility constraints, never ownership grants.
        if is_generic_profile(package_id):
            from generic_author_package import effective_dependencies
            dependencies = effective_dependencies(dependencies, receipt['season'])
        for row in (*rows.values(), *dependencies.values()):
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
