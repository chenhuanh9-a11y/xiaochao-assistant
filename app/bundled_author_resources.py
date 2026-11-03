"""Read-only, hash-bound display defaults; installed user files always win."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat

PREFIXES = ("assets/zhan10-native/", "assets/zhan10-legacy-clean/")


def _regular(path):
    for parent in (path, *path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            return False
    return path.is_file()


def display_path(path, resource_root, fallback_root, expected_sha256, expected_bytes=None):
    """Resolve only absent Zhan display files, never replace an existing object."""
    path, resource_root = Path(path), Path(resource_root)
    try:
        path.lstat()
        return path
    except FileNotFoundError:
        pass
    if fallback_root is None:
        return path
    try:
        relative = path.relative_to(resource_root).as_posix()
        normalized = PurePosixPath(relative)
        if (not relative.startswith(PREFIXES) or ".." in normalized.parts
                or normalized.as_posix() != relative
                or not isinstance(expected_sha256, str) or len(expected_sha256) != 64):
            return path
        candidate = Path(fallback_root) / relative
        if (not _regular(candidate) or not candidate.resolve().is_relative_to(Path(fallback_root).resolve())
                or expected_bytes is not None and candidate.stat().st_size != expected_bytes):
            return path
        with candidate.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha256.lower():
                return path
        return candidate
    except (OSError, ValueError):
        return path


def bundled_summary(root):
    """Verify the separately owned default inventory, without adopting receipts."""
    if root is None:
        return None
    root = Path(root)
    try:
        manifest = root / "inventory.json"
        if not _regular(manifest):
            return None
        data = json.loads(manifest.read_text("utf-8"))
        if data.get("schema") != "xingtu-bundled-author-v1" or data.get("packageId") != "difficult-zhan10":
            return None
        rows = data["files"]
        if not isinstance(rows, dict) or not rows:
            return None
        for name, row in rows.items():
            # Use a deliberately absent local root: this validates each default.
            original = root / ".absent" / name
            if display_path(original, root / ".absent", root, row["sha256"], row["bytes"]) == original:
                return None
        return dict(packageId="builtin-difficult-zhan10", title="展十 · 困难（内置）",
                    status="bundled", canRemove=False, bundledFiles=len(rows))
    except (OSError, ValueError, KeyError, TypeError):
        return None
