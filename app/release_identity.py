from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence


VERSION_MANIFEST_NAME = "version.json"
VERSION_SCHEMA = "identityv-app-version-v1"
IDENTITY_SCHEMA = "identityv-release-identity-v1"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


class ReleaseIdentityError(RuntimeError):
    """Raised when a build cannot be bound to an exact source identity."""


class DirtySourceError(ReleaseIdentityError):
    """Raised when a formal build is attempted from a dirty Git tree."""


def _load_version_manifest(root: Path) -> dict[str, object]:
    manifest_path = Path(root) / VERSION_MANIFEST_NAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseIdentityError(
            f"Unable to read version manifest {manifest_path}: {error}",
        ) from error
    if payload.get("schema") != VERSION_SCHEMA:
        raise ReleaseIdentityError(
            f"Unsupported version manifest schema: {payload.get('schema')!r}",
        )
    version = payload.get("version")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ReleaseIdentityError(f"Invalid application version: {version!r}")
    return payload


def load_app_version(root: Path) -> str:
    return _load_version_manifest(root)["version"]


def _display_version(payload: dict[str, object]) -> str:
    display = payload.get("displayVersion", payload["version"])
    if not isinstance(display, str) or not display.strip():
        raise ReleaseIdentityError(f"Invalid display version: {display!r}")
    return display


def load_display_version(root: Path) -> str:
    """Read the public label without replacing the internal application version."""
    return _display_version(_load_version_manifest(root))


def load_version_metadata(root: Path) -> dict[str, str]:
    """Read all release version fields from one manifest snapshot."""
    payload = _load_version_manifest(root)
    version = payload["version"]
    # Legacy manifests used the first three internal components followed by 0.
    # New candidates maintain an explicit Windows revision in version.json.
    legacy_numeric = version.split("-", 1)[0] + ".0"
    numeric = payload.get("numericVersion", legacy_numeric)
    if (not isinstance(numeric, str)
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+", numeric)
            or any(int(part) > 65535 for part in numeric.split("."))):
        raise ReleaseIdentityError(f"Invalid Windows numeric version: {numeric!r}")
    return {
        "version": version,
        "displayVersion": _display_version(payload),
        "numericVersion": numeric,
    }


def load_runtime_app_version(
    *,
    source_root: Path,
    resource_root: Path,
    frozen: bool,
) -> str:
    """Read the same manifest from the source checkout or frozen resources."""
    return load_app_version(resource_root if frozen else source_root)


def load_runtime_display_version(
    *,
    source_root: Path,
    resource_root: Path,
    frozen: bool,
) -> str:
    """Use the same source/frozen resource root as the internal version loader."""
    return load_display_version(resource_root if frozen else source_root)


def _run_git(
    repository: Path,
    arguments: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        ["git", "-c", "core.quotepath=false", *arguments],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseIdentityError(
            f"Git command failed ({' '.join(arguments)}): {detail}",
        )
    return completed


def _git_text(repository: Path, *arguments: str) -> str:
    return _run_git(repository, arguments).stdout.decode(
        "utf-8", errors="strict",
    ).strip()


def _decode_git_path(raw_path: bytes) -> str:
    return os.fsdecode(raw_path).replace("\\", "/")


def _dirty_files(repository: Path) -> list[str]:
    raw_status = _run_git(
        repository,
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
    ).stdout
    records = raw_status.split(b"\0")
    files: set[str] = set()
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise ReleaseIdentityError("Unable to parse Git porcelain status")
        status = record[:2]
        files.add(_decode_git_path(record[3:]))
        if status[:1] in (b"R", b"C") or status[1:2] in (b"R", b"C"):
            if index >= len(records) or not records[index]:
                raise ReleaseIdentityError("Missing original path in Git rename status")
            files.add(_decode_git_path(records[index]))
            index += 1
    return sorted(files)


def _update_hash_section(digest: object, label: bytes, payload: bytes) -> None:
    digest.update(len(label).to_bytes(4, "big"))
    digest.update(label)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def _source_patch_sha256(repository: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"identityv-source-patch-v1\0")
    tracked_patch = _run_git(
        repository,
        (
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--diff-algorithm=myers",
            "HEAD",
            "--",
        ),
    ).stdout
    _update_hash_section(digest, b"tracked-patch", tracked_patch)
    untracked = _run_git(
        repository,
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ).stdout.split(b"\0")
    for raw_path in sorted(path for path in untracked if path):
        relative_path = _decode_git_path(raw_path)
        absolute_path = repository / Path(relative_path)
        if absolute_path.is_symlink():
            payload = os.readlink(absolute_path).encode("utf-8")
            kind = b"symlink"
        else:
            try:
                payload = absolute_path.read_bytes()
            except OSError as error:
                raise ReleaseIdentityError(
                    f"Unable to hash untracked source {relative_path}: {error}",
                ) from error
            kind = b"file"
        _update_hash_section(digest, b"untracked-path", raw_path)
        _update_hash_section(digest, b"untracked-kind", kind)
        _update_hash_section(digest, b"untracked-content", payload)
    return digest.hexdigest()


def _source_ref(repository: Path) -> str:
    branch = _run_git(
        repository,
        ("symbolic-ref", "--quiet", "--short", "HEAD"),
        check=False,
    )
    if branch.returncode == 0:
        return branch.stdout.decode("utf-8", errors="strict").strip()
    exact_tag = _run_git(
        repository,
        ("describe", "--tags", "--exact-match", "HEAD"),
        check=False,
    )
    if exact_tag.returncode == 0:
        return exact_tag.stdout.decode("utf-8", errors="strict").strip()
    return "HEAD"


def collect_release_identity(
    repository: Path,
    *,
    version_root: Path | None = None,
    experimental: bool = False,
) -> dict[str, object]:
    repository = Path(repository).resolve()
    version_root = repository if version_root is None else Path(version_root).resolve()
    source_commit = _git_text(repository, "rev-parse", "--verify", "HEAD")
    source_tree_hash = _git_text(repository, "rev-parse", "--verify", "HEAD^{tree}")
    dirty_files = _dirty_files(repository)
    source_dirty = bool(dirty_files)
    if source_dirty and not experimental:
        listed = ", ".join(dirty_files)
        raise DirtySourceError(
            "Formal release builds require a clean Git tree; dirty files: " + listed,
        )
    return {
        "schema": IDENTITY_SCHEMA,
        **load_version_metadata(version_root),
        "buildMode": "experimental" if experimental else "release",
        "experimental": bool(experimental),
        "sourceCommit": source_commit,
        # This is the committed tree for sourceCommit. Dirty experimental
        # content is identified separately by sourcePatchSha256.
        "sourceTreeHash": source_tree_hash,
        "sourceDirty": source_dirty,
        "sourceRef": _source_ref(repository),
        "sourceDirtyFiles": dirty_files,
        "sourcePatchSha256": (
            _source_patch_sha256(repository) if source_dirty else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Emit a traceable release identity for this source checkout",
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--version-root", type=Path)
    parser.add_argument("--experimental", action="store_true")
    arguments = parser.parse_args()
    try:
        identity = collect_release_identity(
            arguments.repo_root,
            version_root=arguments.version_root,
            experimental=arguments.experimental,
        )
    except ReleaseIdentityError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(identity, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
