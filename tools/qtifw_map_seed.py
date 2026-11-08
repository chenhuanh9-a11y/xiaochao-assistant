"""Install the immutable author-map set into a newly claimed QtIFW target.

This adapter deliberately delegates ZIP validation, resource repair, ownership,
and publication to the application's existing production APIs. It does not
recover unfinished author transactions or reinterpret receipts.
"""
from __future__ import annotations

import re
import stat
import zipfile
from pathlib import Path

import managed_base_resources
import star_map_import
from resource_transaction import (_safe, assert_resources_ready,
                                  resource_runtime_lease)


_MAP_SUPPORT_ROOT = ("_installer-support", "maps")


def _archive_path(target: Path, relative: str) -> Path:
    """Resolve one policy path without accepting traversal or filesystem links."""
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or ":" in relative or relative.startswith("/")
            or any(ord(char) < 32 for char in relative)):
        raise ValueError("bundled-map-invalid-path")
    parts = relative.split("/")
    if (len(parts) < 3 or tuple(parts[:2]) != _MAP_SUPPORT_ROOT
            or any(part in {"", ".", ".."} for part in parts)):
        raise ValueError("bundled-map-path-outside-support-root")
    maps_root = target.joinpath(*_MAP_SUPPORT_ROOT)
    path = target.joinpath(*parts)
    _safe(maps_root)
    _safe(path)
    try:
        if not path.resolve(strict=True).is_relative_to(maps_root.resolve(strict=True)):
            raise ValueError("bundled-map-path-outside-support-root")
        info = path.lstat()
    except FileNotFoundError as error:
        raise ValueError("bundled-map-archive-missing") from error
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("bundled-map-archive-not-regular-file")
    return path


def _check_package_row(row):
    if not isinstance(row, dict):
        raise ValueError("bundled-map-invalid-entry")
    path = row.get("path")
    size = row.get("bytes")
    digest = row.get("sha256")
    package_id = row.get("packageId")
    season = row.get("season")
    versioned = row.get("versionedAuthor")
    if (not isinstance(path, str) or not path
            or type(size) is not int or size < 0
            or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or not isinstance(package_id, str) or not package_id
            or not isinstance(season, str) or season not in {"s0", "s1"}
            or type(versioned) is not bool
            or not star_map_import.known_package_id(package_id)
            or (season == "s1" and star_map_import.is_generic_profile(package_id)
                and versioned is not True)):
        raise ValueError("bundled-map-invalid-entry")
    return dict(path=path, bytes=size, sha256=digest, packageId=package_id,
                season=season, versionedAuthor=versioned)


def _preflight_archive(target: Path, row):
    """Hash every source ZIP and bind its embedded identity before base writes."""
    path = _archive_path(target, row["path"])
    archive_size = path.stat().st_size
    if archive_size != row["bytes"]:
        raise ValueError("bundled-map-size-mismatch: " + row["packageId"])
    if archive_size > star_map_import.MAX_ARCHIVE_BYTES:
        raise ValueError("bundled-map-archive-size-limit: " + row["packageId"])
    digest = star_map_import._sha(path)
    if digest != row["sha256"]:
        raise ValueError("bundled-map-sha256-mismatch: " + row["packageId"])
    try:
        with star_map_import._open_archive(path, row["sha256"]) as (archive, actual_digest):
            if actual_digest != row["sha256"]:
                raise ValueError("bundled-map-sha256-mismatch: " + row["packageId"])
            metadata = star_map_import._json(archive.read(star_map_import.MANIFEST))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
        raise ValueError("bundled-map-manifest-invalid: " + row["packageId"]) from error
    if (metadata.get("packageId") != row["packageId"]
            or metadata.get("season", "s0") != row["season"]):
        raise ValueError("bundled-map-manifest-identity-mismatch: " + row["packageId"])
    return path


def _verified_existing(program: Path, row):
    """Accept only a complete, exact-archive receipt for an interrupted retry."""
    package_id = row["packageId"]
    verified = star_map_import.verified_installed_package(
        program, package_id, row["sha256"])
    if verified.get("status") == "not-installed":
        return False
    if (verified.get("status") != "installed"
            or verified.get("archiveSha256") != row["sha256"]):
        raise ValueError("bundled-map-existing-content-unverified: " + package_id)
    return True


def install_bundled_maps(target, packages):
    """Seed verified author ZIPs into ``target/program`` under one runtime lease.

    Exact installed packages are accepted on retry only after the production
    verifier checks the owned bytes and dependency closure. All other receipts,
    partial states, and pending transactions fail closed for explicit recovery.
    """
    if not isinstance(packages, list):
        raise ValueError("bundled-map-package-list-required")
    if not packages:
        return dict(installed=0, packages=[])

    root = Path(target).absolute()
    _safe(root)
    if not root.is_dir():
        raise ValueError("bundled-map-target-missing")
    program = _safe(root / "program")
    if not program.is_dir():
        raise ValueError("bundled-map-program-missing")

    rows, package_ids, paths = [], set(), set()
    for raw in packages:
        row = _check_package_row(raw)
        path_key = row["path"].casefold() if isinstance(row["path"], str) else None
        if row["packageId"] in package_ids:
            raise ValueError("bundled-map-duplicate-package-id: " + row["packageId"])
        if path_key in paths:
            raise ValueError("bundled-map-duplicate-archive-path")
        package_ids.add(row["packageId"])
        paths.add(path_key)
        rows.append(row)

    # Validate every archive before the managed-base repair can write anything.
    archives = [_preflight_archive(root, row) for row in rows]
    with resource_runtime_lease(program):
        managed_base_resources.repair_managed_base_resources(program)
        assert_resources_ready(program)

        inspections = []
        for row, archive in zip(rows, archives):
            inspection = star_map_import.inspect_package(
                archive, program, season=row["season"], versioned=row["versionedAuthor"])
            if (inspection.get("packageId") != row["packageId"]
                    or inspection.get("season") != row["season"]
                    or inspection.get("archiveSha256") != row["sha256"]):
                raise ValueError("bundled-map-inspection-identity-mismatch: " + row["packageId"])
            inspections.append((row, inspection))
        summaries = []
        for row, archive, inspection in zip(rows, archives, (item[1] for item in inspections)):
            if _verified_existing(program, row):
                status = "already-installed"
            else:
                installed = star_map_import.install_package(
                    archive, program, application_stopped=True,
                    expected_archive_sha256=row["sha256"], season=row["season"],
                    versioned=row["versionedAuthor"])
                if (installed.get("packageId") != row["packageId"]
                        or installed.get("season") != row["season"]
                        or installed.get("archiveSha256") != row["sha256"]):
                    raise ValueError("bundled-map-install-receipt-mismatch: " + row["packageId"])
                if not _verified_existing(program, row):
                    raise ValueError("bundled-map-install-not-verified: " + row["packageId"])
                status = "installed"
            summaries.append(dict(packageId=row["packageId"], season=row["season"],
                                  archiveSha256=row["sha256"], fileCount=inspection["fileCount"],
                                  status=status))

    return dict(installed=len(summaries), packages=summaries)
