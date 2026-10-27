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
PACKAGE_IDS = {"nightmare-suju", "difficult-zhan10"}
CLIENT_COMPATIBILITY = {"displayVersion": "1.0", "numericLine": "1.0.0.N",
                        "minimumBuild": 1, "maximumBuild": 65535}
BASE_DEPENDENCIES = {
    "_internal/references/nightmare-author-route-catalog.json",
    "_internal/references/nightmare-canonical-coordinate-pack.json",
    "_internal/references/nightmare-shared-structure-index.json",
    "_internal/references/nightmare-structure-version-bindings.json",
}
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
    if not BASE_DEPENDENCIES <= {r["path"] for r in dependencies.values()}:
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
    if version.get("displayVersion") != "1.0" or build is None or int(build[1]) > 65535:
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
                raise ImportRejected(f"existing-data-conflict: {path}")
            existing += 1
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


def install_package(zip_path, install_root, *, application_stopped=False, expected_archive_sha256=None) -> dict:
    """Append verified data, never replace existing different data; restart after success.

    Caller must close the application first. UI must run this off its Tk thread.
    Exception rollback removes only files created by this call. On process/power
    loss, existing data stays untouched; a rerun completes identical partial data.
    """
    if application_stopped is not True:
        raise ImportRejected("close-application-first")
    root = Path(os.path.abspath(install_root))
    _no_links(root)
    if not root.is_dir():
        raise ImportRejected("installation-root-missing")
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
        created = []
        with tempfile.TemporaryDirectory(prefix="staging-", dir=state) as directory:
            stage = Path(directory)
            try:
                with _open_archive(zip_path, expected_archive_sha256) as (archive, digest):
                    result = _validate(archive, root, stage)
                    result["archiveSha256"] = digest
                for row in result["files"]:
                    target = _target(root, row["path"])
                    if target.exists():
                        # Recheck after staging: fail closed on concurrent changes.
                        if target.stat().st_size != row["bytes"] or _sha(target) != row["sha256"]:
                            raise ImportRejected("target-changed-during-import")
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _no_links(target.parent)
                    # Same-volume hard link publishes a complete file exclusively.
                    # It cannot overwrite a file concurrently created by another writer.
                    staged = stage.joinpath(*row["path"].split("/"))
                    published = staged.stat()  # Capture the source BEFORE link/target races.
                    identity = (published.st_dev, published.st_ino)
                    os.link(staged, target)
                    created.append((target, identity, row["sha256"]))
                result["added"] = len(created)
                result["status"] = "already-installed" if not created else "installed-restart-required"
                receipt = _target(root, f"work/map-packages/{result['packageId']}.json")
                temporary = stage / "receipt.json"
                temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temporary, receipt)
                return result
            except Exception:
                # Retain staged hard links through rollback: their file IDs cannot
                # be recycled after another writer replaces a destination.
                for path, identity, digest in reversed(created):
                    _rollback_published(path, identity, digest)
                raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inspect", "install"])
    parser.add_argument("archive")
    parser.add_argument("install_root")
    parser.add_argument("--application-closed", action="store_true")
    args = parser.parse_args()
    try:
        result = (inspect_package(args.archive, args.install_root) if args.action == "inspect" else
                  install_package(args.archive, args.install_root, application_stopped=args.application_closed))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ImportRejected, OSError, zipfile.BadZipFile) as error:
        print(json.dumps({"success": False, "reason": str(error)}, ensure_ascii=False))
        raise SystemExit(2)
