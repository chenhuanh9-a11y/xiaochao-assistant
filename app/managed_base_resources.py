"""Manifest-owned base repair, before resource consumers and under the shared lease.

Setup installs candidates only. A complete preflight precedes any active-file
change. Durable journals retain old/new objects for guarded rollback after an
exception or process loss; this is not a power-loss atomic filesystem claim.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import stat
import uuid

import map_package_upgrade as transaction
import star_map_import as files
from s0_legacy_base_releases import REVIEWED_LEGACY_S0_BASE_RELEASES
from s1_reviewed_base_releases import REVIEWED_S1_BASE_RELEASES

SCHEMA = "xingtu-managed-base-v1"
CANDIDATE = "_internal/managed-base-resources"
STATE = "work/map-packages/managed-base-state.json"
PENDING = "work/map-packages/pending-base-repair.json"
JOURNAL_SCHEMA = "xingtu-base-repair-v1"
RETIREMENT_JOURNAL_SCHEMA = "xingtu-base-repair-retirement-v2"
ACTIVE_JOURNAL_SCHEMAS = {JOURNAL_SCHEMA, RETIREMENT_JOURNAL_SCHEMA}
HISTORY = "work/map-packages/base-repairs"
RETIRED_SCHEMA = "xingtu-base-repair-retired-v1"
MAX_RETIRED_RECORDS = 16
MAX_RETIRED_BYTES = 1024

# Source-owned inventories verified against final Windows native bytes, never
# installation profiles or incoming metadata. Each entry is
# (resourceVersion, ((exact path, bytes, sha256), ...)).
# Retain reviewed predecessor inventories while their state/journals may exist;
# a candidate rollout toggle must never make recovery history unreadable.
_REVIEWED_S1_BASE_RELEASES = REVIEWED_S1_BASE_RELEASES
_UNSPECIFIED_RESOURCE_VERSION = object()


@lru_cache(maxsize=1)
def _s1_release_catalogue(releases):
    """Immutable exact locks; parsing old/new inventory is not live authority."""
    if type(releases) is not tuple or len(releases) > 16:
        raise ManagedBaseResourceError("invalid-reviewed-s1-release-policy")
    result, paths, versions = [], set(), set()
    for release in releases:
        if type(release) is not tuple or len(release) != 2:
            raise ManagedBaseResourceError("invalid-reviewed-s1-release-policy")
        version, values = release
        if (type(version) is not str or not version.strip() or len(version) > 256
                or version in versions or type(values) is not tuple or not 0 < len(values) <= 20000):
            raise ManagedBaseResourceError("invalid-reviewed-s1-release-policy")
        versions.add(version)
        rows, seen, modes = set(), set(), set()
        for value in values:
            if type(value) is not tuple or len(value) != 3:
                raise ManagedBaseResourceError("invalid-reviewed-s1-release-policy")
            path, size, digest = value
            row = next(iter(files._rows([dict(path=path, bytes=size, sha256=digest)]).values()))
            parts = row["path"].split("/")
            if (len(parts) < 5 or parts[:3] != ["_internal", "seasons", "s1"]
                    or parts[3] not in {"difficulty2", "difficult", "nightmare"}
                    or not ((len(parts) == 5 and parts[4] == "native-map-manifest.json")
                            or (len(parts) > 5 and parts[4] in {"assets", "references", "recognition"}))
                    or Path(path).suffix.lower() not in {".json", ".gz", ".npz", ".png", ".webp", ".jpg",
                        ".jpeg", ".txt", ".md", ".ico", ".svg"}
                    or row["path"].casefold() in seen):
                raise ManagedBaseResourceError("invalid-reviewed-s1-release-path")
            seen.add(row["path"].casefold())
            if len(parts) == 5:
                modes.add(parts[3])
            rows.add((row["path"], row["bytes"], row["sha256"]))
        if modes != {"difficulty2", "difficult", "nightmare"}:
            raise ManagedBaseResourceError("incomplete-reviewed-s1-release-policy")
        result.append((version, frozenset(rows)))
        paths.update(row[0] for row in rows)
    from s1_resource_retirement import projected_release
    for version, rows in tuple(result):
        projection = projected_release(version, rows)
        if projection is not None:
            if projection[0] in versions:
                raise ManagedBaseResourceError("duplicate-s1-retirement-target")
            result.append(projection)
            versions.add(projection[0])
    return tuple(result), frozenset(paths)


def _reviewed_s1_catalogue():
    try:
        return _s1_release_catalogue(_REVIEWED_S1_BASE_RELEASES)
    except (TypeError, ValueError) as error:
        if isinstance(error, ManagedBaseResourceError):
            raise
        raise ManagedBaseResourceError("invalid-reviewed-s1-release-policy") from error


def _verify_reviewed_s1_inventory(rows, resource_version=_UNSPECIFIED_RESOURCE_VERSION):
    selected = frozenset((row["path"], row["bytes"], row["sha256"]) for row in rows
                        if row["path"].casefold().startswith("_internal/seasons/"))
    if not selected:
        # S0 remains unchanged, but a known S1 release cannot drop its entire
        # S1 inventory while retaining the reviewed resource-version label.
        if resource_version is not _UNSPECIFIED_RESOURCE_VERSION:
            releases, _ = _reviewed_s1_catalogue()
            if any(resource_version == version for version, _ in releases):
                raise ManagedBaseResourceError("unreviewed-s1-base-inventory-or-version")
        return
    releases, _ = _reviewed_s1_catalogue()
    if not any(selected == inventory and (resource_version is _UNSPECIFIED_RESOURCE_VERSION
                or type(resource_version) is str and resource_version == version)
               for version, inventory in releases):
        raise ManagedBaseResourceError("unreviewed-s1-base-inventory-or-version")

OPTIONAL_AUTHOR_SIDECARS = frozenset(
    f"_internal/references/author-packs/{author}/{name}"
    for author in files.AUTHOR_PACKAGES.values() for name in files.AUTHOR_SIDECARS)


class ManagedBaseResourceError(ValueError):
    def __init__(self, reason, issues=()):
        self.reason, self.issues = reason, list(issues)
        self.code = reason.split(";", 1)[0].split(":", 1)[0]
        self.details = {"issues": [{key: row[key] for key in ("path", "reason", "expected", "actual") if key in row}
                                   for row in self.issues[:12]], "issueCount": len(self.issues)}
        detail = "; ".join(f"{row.get('reason')}: {row.get('path')}" for row in self.issues)
        super().__init__(reason + (": " + detail if detail else ""))


def optional_author_sidecar(path):
    """Exact optional display metadata; never a mandatory shared dependency."""
    return files._relative(path).casefold() in OPTIONAL_AUTHOR_SIDECARS


def _base_path(path):
    # Keep A066 manifests and recovery journals parseable, including their
    # mistaken optional-author entries. Planning separates ownership below;
    # historical rollback/retirement must still verify the original records.
    path = files._relative(path)
    if path.startswith("_internal/seasons/"):
        _, reviewed_paths = _reviewed_s1_catalogue()
        return path in reviewed_paths
    return (path == "_internal/difficult-native-manifest.json" or
            path.startswith(("_internal/assets/", "_internal/references/", "_internal/recognition/"))) and not (
                path.startswith("_internal/assets/custom-maps/") or
                path == "_internal/references/build-fingerprint.txt")


def _rows(values):
    # The base bundle can exceed an individual author's 2,000-file limit.
    if not isinstance(values, list) or not 0 < len(values) <= 20000:
        raise ManagedBaseResourceError("invalid-base-inventory")
    rows = {}
    for value in values:
        row = next(iter(files._rows([value]).values()))
        key = row["path"].casefold()
        if key in rows or not _base_path(row["path"]):
            raise ManagedBaseResourceError("invalid-base-path", [dict(path=row["path"], reason="not-base-or-duplicate")])
        rows[key] = row
    # Complete exact S1 release, including historical journal loads. A union of
    # approved paths, partial release or mixed resource bytes is insufficient.
    _verify_reviewed_s1_inventory(rows.values())
    return rows


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def manifest_for_rows(rows, resource_version=""):
    rows = sorted(_rows(rows).values(), key=lambda row: row["path"])
    _verify_reviewed_s1_inventory(rows, resource_version)
    return dict(schema=SCHEMA, resourceVersion=resource_version,
                bundleId=hashlib.sha256(_encoded(rows)).hexdigest(), files=rows)


def _manifest(path):
    value = transaction._read_json(path)
    if value.get("schema") != SCHEMA:
        raise ManagedBaseResourceError("invalid-base-manifest-schema")
    expected = manifest_for_rows(value.get("files"), value.get("resourceVersion", ""))
    if value.get("bundleId") != expected["bundleId"]:
        raise ManagedBaseResourceError("invalid-base-manifest-bundle-id")
    return expected


def managed_base_inventory(install_root):
    """Protected paths from current and candidate base manifests; call under lease.

    Preserve both generations while repair may be blocked. Invalid state is an
    error, never an empty ownership claim for a removal operation.
    """
    root = Path(os.path.abspath(install_root))
    protected = {}
    for name in (STATE, CANDIDATE + "/manifest.json"):
        path = files._target(root, name)
        if path.exists():
            for row in _manifest(path)["files"]:
                if not optional_author_sidecar(row["path"]):
                    protected.setdefault(row["path"].casefold(), []).append(row)
    return protected


def _actual(path):
    files._no_links(path)
    _validate_target_ancestors(path)
    if not path.exists():
        return dict(status="missing")
    if not path.is_file():
        return dict(status="not-file")
    return dict(status="hashed", bytes=path.stat().st_size, sha256=files._sha(path))


def _same(actual, expected):
    return actual.get("bytes") == expected["bytes"] and actual.get("sha256") == expected["sha256"]


def _issue(row, actual, reason, **extra):
    return dict(path=row["path"], expected=dict(bytes=row["bytes"], sha256=row["sha256"]),
                actual=actual, reason=reason, **extra)


def _retained_legacy_constraints(root, package):
    """Read old metadata as preservation constraints, never import authority.

    Dependency-free receipts can reserve files but cannot authorize replacing
    any existing base byte. Strict import/removal receipt parsing is unchanged.
    Unknown authors are retained without registering a profile or executable.
    """
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,95}", package):
        raise files.ImportRejected("invalid-installed-package-receipt")
    path = files._target(root, "work/map-packages/" + package + ".json")
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise files.ImportRejected("invalid-installed-package-receipt")
    receipt = files._json(path.read_bytes())
    from generic_author_package import SCHEMA as GENERIC_SCHEMA
    if receipt.get("schema") == GENERIC_SCHEMA:
        # V2 has an explicit complete contract. A rejected V2 receipt cannot
        # fall back to the older preservation-only envelope.
        raise files.ImportRejected("invalid-installed-package-receipt")
    if (receipt.get("packageId") != package or type(receipt.get("revision")) is not int
            or receipt["revision"] < 1 or not isinstance(receipt.get("archiveSha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", receipt["archiveSha256"])):
        raise files.ImportRejected("invalid-installed-package-receipt")
    rows = files._rows(receipt.get("files"))
    known = package in files.PACKAGE_IDS
    if any(not (files._allowed(package, row["path"]) if known else _base_path(row["path"]))
           for row in rows.values()):
        raise files.ImportRejected("invalid-installed-package-receipt")
    if known and "requiresFiles" in receipt:
        # Present but invalid dependencies are not the old missing-field format.
        raise files.ImportRejected("invalid-installed-package-dependencies")
    dependencies = (files._rows(receipt["requiresFiles"], dependencies=True)
                    if "requiresFiles" in receipt else {})
    return rows, dependencies


def _package_constraints(root, *, prospective_base_root=None):
    owned, dependencies, unrecorded = {}, [], []
    directory = files._target(root, "work/map-packages")
    if not directory.exists():
        return owned, dependencies, unrecorded
    # This directory also carries handoff/result metadata. These exact writer
    # namespaces are not ownership receipts; ignore their contents here, leave
    # them intact, and let their own consumers validate them. Other records must
    # satisfy the bounded legacy envelope/path checks to constrain preservation;
    # they never silently become a registered or writable installed author.
    reserved = {Path(STATE).name, Path(PENDING).name, "pending-upgrade.json",
                "pending-additions.json", "pending-removal.json", "pending-author-snapshot.json", "qt-import-pending.json",
                "qt-recovery-pending.json", "last-import-result.json", "last-recovery-result.json",
                "qt-import-notice-seen.json", "main-import-request.json"}
    for path in sorted(directory.glob("*.json")):
        if (path.name in reserved or re.fullmatch(r"recovery-request-[A-Za-z0-9-]{1,64}\.json", path.name)
                or re.fullmatch(r"helper-result-[a-z0-9_]{8}\.json", path.name)):
            continue
        from generic_author_package import is_generic_profile
        package = path.stem.casefold() if is_generic_profile(path.stem.casefold()) else path.stem
        try:
            if files.known_package_id(package):
                try:
                    if is_generic_profile(package):
                        inventory, package_dependencies, _ = files._installed_inventory(
                            root, package, base_root=prospective_base_root)
                    else:
                        inventory, package_dependencies, _ = files._installed_inventory(root, package)
                except files.ImportRejected as error:
                    if error.code == "invalid-reviewed-author-receipt-policy":
                        raise
                    inventory, package_dependencies = _retained_legacy_constraints(root, package)
            else:
                inventory, package_dependencies = _retained_legacy_constraints(root, package)
                # Even a structurally valid unknown declaration cannot prove
                # the entire dependency closure. Keep every existing byte.
                unrecorded.append(package)
        except files.ImportRejected as error:
            # The startup reporter deliberately omits arbitrary exception text.
            # Identify the registered receipt without adopting or rewriting it.
            reason = (error.code if error.code in {
                "invalid-installed-package-receipt", "invalid-installed-package-dependencies"
            } else "receipt-validation-rejected")
            raise ManagedBaseResourceError("installed-package-receipt-rejected", [dict(
                path="work/map-packages/" + path.name, reason=reason)]) from error
        for key, row in inventory.items():
            owned.setdefault(key, []).append((package, row))
        # The shared receipt parser validates the package's exact season scope.
        # Missing legacy metadata constrains repair to adding missing files;
        # it grants no replacement, deletion, import or display capability.
        if not package_dependencies:
            if package not in unrecorded:
                unrecorded.append(package)
        else:
            if is_generic_profile(package):
                from generic_author_package import effective_dependencies
                # Ownership still covers the full imported author payload. Only
                # source-retired base dependencies cease constraining runtime.
                package_dependencies = effective_dependencies(package_dependencies, 's1')
            dependencies.extend((package, row) for row in package_dependencies.values())
    return owned, dependencies, unrecorded


def _legacy_s0_previous(root, candidate):
    """Recognize only an entire source-reviewed predecessor, before any write."""
    if type(REVIEWED_LEGACY_S0_BASE_RELEASES) is not tuple:
        raise ManagedBaseResourceError("invalid-reviewed-legacy-s0-policy")
    seen, catalogue = set(), []
    for release in REVIEWED_LEGACY_S0_BASE_RELEASES:
        if (type(release) is not tuple or len(release) != 3
                or type(release[0]) is not str or not re.fullmatch("[0-9a-f]{40}", release[0])
                or type(release[1]) is not str or not re.fullmatch("[0-9a-f]{64}", release[1])
                or release[1] in seen or type(release[2]) is not tuple or not release[2]):
            raise ManagedBaseResourceError("invalid-reviewed-legacy-s0-policy")
        seen.add(release[1])
        rows = []
        for value in release[2]:
            if type(value) is not tuple or len(value) != 3:
                raise ManagedBaseResourceError("invalid-reviewed-legacy-s0-policy")
            rows.append(dict(zip(("path", "bytes", "sha256"), value)))
        previous = _rows(rows)
        if any(key.startswith("_internal/seasons/") or optional_author_sidecar(row["path"])
               for key, row in previous.items()):
            raise ManagedBaseResourceError("invalid-reviewed-legacy-s0-policy")
        catalogue.append(previous)
    for previous in catalogue:
        # This recognition adds no retirement authority or partial adoption.
        if (set(previous) <= set(candidate) and all(
                _same(_actual(files._target(root, row["path"])), row)
                for row in previous.values())):
            return previous
    return {}


def _retirement_rows(source_version, target_version, previous, candidate):
    from s1_resource_retirement import retirement_inventory
    select = lambda rows: frozenset((row['path'], row['bytes'], row['sha256']) for row in rows.values()
                                    if row['path'].startswith('_internal/seasons/s1/'))
    values = retirement_inventory(source_version, target_version, select(previous), select(candidate))
    return {path.casefold(): dict(path=path, bytes=size, sha256=digest) for path, size, digest in values}


def plan_managed_base_resources(install_root):
    """Read-only complete preflight. Call under a resource lease for a stable view."""
    root = Path(os.path.abspath(install_root))
    candidate = files._target(root, CANDIDATE + "/manifest.json")
    if not candidate.exists():
        for name in ("installation-profile.json", "_internal/installation-profile.json"):
            profile = files._target(root, name)
            if profile.is_file():
                policy = transaction._read_json(profile).get("resourceInstallationPolicy")
                if isinstance(policy, dict) and policy.get("existingBasePlane") == "manifest-owned-transaction-before-runtime":
                    raise ManagedBaseResourceError("base-candidate-manifest-missing", [dict(path=CANDIDATE + "/manifest.json", reason="missing")])
        return dict(status="not-managed", files=[], changes=[], issues=[])
    original = _manifest(candidate)
    # A066 included optional author metadata in its baseline. Validate that
    # complete legacy document, then relinquish only these registered display
    # paths. Never adopt, hash-repair, resurrect or claim their active bytes;
    # the optional provider and author package operations own their validation.
    manifest = manifest_for_rows([row for row in original["files"]
                                  if not optional_author_sidecar(row["path"])], original["resourceVersion"])
    inventory = _rows(manifest["files"])
    state = files._target(root, STATE)
    _validate_target_ancestors(state)
    previous_manifest = _manifest(state) if state.exists() else None
    previous = _rows(previous_manifest["files"]) if previous_manifest else {}
    if not state.exists():
        previous = _legacy_s0_previous(root, inventory)
    # A V2 closure needs source/index/typed metadata, which may be the very
    # missing files this transaction repairs. Validate the complete prospective
    # plane before using it for closure discovery; receipt hashes still constrain
    # every desired byte below. No incomplete/corrupt candidate supplies authority.
    candidate_root = files._target(root, CANDIDATE + "/payload")
    candidate_issues = []
    for row in inventory.values():
        source = files._target(candidate_root, row["path"])
        if not transaction._matches(source, row):
            candidate_issues.append(_issue(row, _actual(source), "candidate-integrity-mismatch"))
    if candidate_issues:
        return dict(status="blocked", manifest=manifest, files=manifest["files"],
                    changes=[], issues=candidate_issues, retainedLegacyPackages=[])
    retirement = (_retirement_rows(previous_manifest['resourceVersion'], manifest['resourceVersion'], previous, inventory)
                  if previous_manifest else {})
    owned, dependencies, unrecorded = _package_constraints(root, prospective_base_root=candidate_root)
    # Do not drop S1 ownership while leaving retired bytes active. Removal,
    # rename or S1-to-S0 downgrade needs a separately reviewed retirement
    # transaction; until then block the whole preflight without changing files.
    issues = [_issue(row, _actual(files._target(root, row["path"])), "s1-base-retirement-unreviewed")
              for key, row in previous.items()
               if key.startswith("_internal/seasons/s1/") and key not in inventory and key not in retirement]
    changes = []
    retirement_deferred = []
    retired_dependencies = {row['path'].casefold() for _package, row in dependencies}
    for key, row in retirement.items():
        actual = _actual(files._target(root, row['path']))
        if key in owned or key in retired_dependencies or unrecorded:
            retirement_deferred.append(_issue(row, actual, 'retired-mode-still-required'))
        elif actual['status'] != 'missing' and not _same(actual, row):
            retirement_deferred.append(_issue(row, actual, 'retired-mode-local-content-preserved'))
    for key, row in inventory.items():
        actual = _actual(files._target(root, row["path"]))
        if _same(actual, row):
            continue
        for package, author_row in owned.get(key, []):
            if author_row != row:
                issues.append(_issue(row, actual, "installed-package-owned-conflict", packageId=package))
        old = previous.get(key)
        if actual["status"] == "missing":
            changes.append(dict(new=row, old=None))
        elif old is not None and _same(actual, old):
            if unrecorded:
                issues.append(_issue(row, actual, "installed-package-dependencies-unrecorded", packages=unrecorded))
            changes.append(dict(new=row, old=old))
        else:
            issues.append(_issue(row, actual, "base-ownership-unknown"))
    # Check every receipt constraint against the complete prospective set, even
    # for paths not directly changed by this repair.
    for package, row in dependencies:
        desired = inventory.get(row["path"].casefold())
        actual = desired or _actual(files._target(root, row["path"]))
        if not _same(actual, row):
            issues.append(_issue(row, actual, "installed-package-dependency-conflict", packageId=package))
    # A whole-mode retirement is deferred, not partially executed, when a
    # package or changed local bytes still needs preservation. A clean unchanged
    # active plane may run with the old ownership state intact. Never use this
    # exception to skip repair or a mismatch in a supported mode.
    if retirement and retirement_deferred and not issues and not changes:
        return dict(status='retirement-deferred', manifest=manifest, files=manifest['files'], changes=[], issues=[],
                    retainedLegacyPackages=unrecorded, retirementDeferred=retirement_deferred)
    if retirement_deferred:
        issues.extend(retirement_deferred)
    if retirement and not retirement_deferred:
        changes.extend(dict(new=None, old=row) for row in retirement.values()
                       if files._target(root, row['path']).exists())
    return dict(status="blocked" if issues else "repair-needed" if changes else "healthy",
                manifest=manifest, files=manifest["files"], changes=changes, issues=issues,
                retainedLegacyPackages=unrecorded,
                **({'retirement': dict(sourceVersion=previous_manifest['resourceVersion'],
                                       targetVersion=manifest['resourceVersion'], files=list(retirement.values()))}
                   if retirement else {}))


def _write_journal(directory, journal):
    transaction._write_journal(directory, journal)


def _pending_stamp(journal):
    """The move protocol binds its marker before publication, including retirement."""
    if journal.get('publication') != 'move-v1':
        if 'pendingStamp' in journal:
            raise ManagedBaseResourceError('invalid-base-pending-stamp')
        return None
    stamp = journal.get('pendingStamp')
    expected = _encoded(dict(schema=JOURNAL_SCHEMA, transactionId=journal['transactionId']))
    if (not isinstance(stamp, dict) or set(stamp) != {'bytes', 'sha256', 'identity'}
            or type(stamp['bytes']) is not int or stamp['bytes'] != len(expected)
            or stamp['sha256'] != hashlib.sha256(expected).hexdigest()):
        raise ManagedBaseResourceError('invalid-base-pending-stamp')
    try:
        transaction._valid_identity(stamp['identity'])
    except ValueError as error:
        raise ManagedBaseResourceError('invalid-base-pending-stamp') from error
    return stamp


def _clear_pending(root, transaction_id):
    pointer = files._target(root, PENDING)
    if transaction._read_json(pointer) != dict(schema=JOURNAL_SCHEMA, transactionId=transaction_id):
        raise ManagedBaseResourceError("base-pending-pointer-changed")
    journal = transaction._read_json(files._target(root, HISTORY + '/' + transaction_id + '/journal.json'))
    stamp = _pending_stamp(journal)
    if stamp is not None:
        if not transaction._matches(pointer, stamp, stamp['identity']):
            raise ManagedBaseResourceError('base-pending-pointer-changed')
        row, identity = stamp, stamp['identity']
    else:
        row = dict(path=PENDING, bytes=pointer.stat().st_size, sha256=files._sha(pointer))
        identity = transaction._capture(pointer, row)
    if not files._rollback_published(pointer, tuple(identity), row["sha256"]):
        raise ManagedBaseResourceError("base-pending-pointer-changed")
    transaction._directory_sync(pointer.parent)


def _cleanup_error(path, reason):
    if any(word in reason for word in ("unknown", "unrecognized", "unwitnessed", "invalid", "missing", "not-terminal")):
        public_reason = "cleanup-object-unknown"
    elif any(word in reason for word in ("changed", "linked", "different")):
        public_reason = "cleanup-object-changed"
    else:
        public_reason = "cleanup-object-unwritable"
    error = ManagedBaseResourceError("base-cleanup-incomplete", [dict(path=path, reason=public_reason)])
    error.cleanup_reason = reason
    error.details["pendingCleanup"] = True
    return error


def _ensure_pending(root, transaction_id, *, journal=None):
    try:
        pointer = files._target(root, PENDING)
        value = dict(schema=JOURNAL_SCHEMA, transactionId=transaction_id)
        directory = files._target(root, HISTORY + '/' + transaction_id)
        if journal is None:
            source = files._target(directory, 'journal.json')
            # Preserve the legacy error/rearm path for malformed old journals.
            # A recognizable move journal must never downgrade to that path.
            try:
                header = transaction._read_json(source)
            except (OSError, ValueError):
                header = {}
            if header.get('publication') == 'move-v1':
                _, journal = _load_history(root, transaction_id)
        if journal is not None and journal.get('publication') == 'move-v1':
            stamp = _pending_stamp(journal) if 'pendingStamp' in journal else None
            if pointer.exists():
                if stamp is None or not transaction._matches(pointer, stamp, stamp['identity']):
                    raise _cleanup_error(PENDING, 'pending-marker-object-changed')
                return
            staged = files._target(directory, 'pending-pointer.json')
            if staged.exists():
                if stamp is None or not transaction._matches(staged, stamp, stamp['identity']):
                    raise _cleanup_error(PENDING, 'pending-marker-source-unwitnessed-or-changed')
            else:
                data = _encoded(value)
                transaction._write_new(staged, data)
                row = dict(path='pending-pointer.json', bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                stamp = dict(bytes=row['bytes'], sha256=row['sha256'], identity=transaction._capture(staged, row))
                journal['pendingStamp'] = stamp
                # This durable identity precedes making the complete marker
                # visible. A torn preparation remains private and fails closed.
                _write_journal(directory, journal)
            transaction._move_owned(staged, pointer, stamp, stamp['identity'], linkless=True)
            if not transaction._matches(pointer, stamp, stamp['identity']):
                raise _cleanup_error(PENDING, 'pending-marker-object-changed')
            return
        if pointer.exists():
            if transaction._read_json(pointer) != value:
                raise _cleanup_error(PENDING, "different-pending-transaction")
        else:
            transaction._write_new(pointer, _encoded(value))
    except ManagedBaseResourceError:
        raise
    except OSError as error:
        raise _cleanup_error(PENDING, "pending-marker-unwritable") from error
    except ValueError as error:
        raise _cleanup_error(PENDING, "pending-marker-invalid") from error


def _is_transaction_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", value))


def _history_directories(root):
    directory = files._target(root, HISTORY)
    if not directory.exists():
        return []
    found = []
    for path in sorted(directory.iterdir()):
        try:
            files._no_links(path)
        except (OSError, ValueError) as error:
            raise _cleanup_error(HISTORY + "/" + path.name, "linked-history-object") from error
        if not _is_transaction_id(path.name) or not path.is_dir():
            raise _cleanup_error(HISTORY + "/" + path.name, "unrecognized-history-object")
        found.append(path)
    return found


def _cleanup_inventory(directory, journal):
    """Validate the complete private tree before deleting even one known pin.

    Terminal cleanup tolerates already absent pins, never an unexpected object.
    Preparing journals can record absent/unwitnessed files; existing unwitnessed
    bytes remain blocked instead of guessing ownership after process loss.
    """
    expected = {}
    if journal.get('publication') == 'move-v1':
        stamp = _pending_stamp(journal)
        expected['pending-pointer.json'] = (stamp, stamp['identity'])
    if journal["schema"] in ACTIVE_JOURNAL_SCHEMAS:
        for entry in journal["entries"]:
            if entry['new'] is not None:
                expected["new/" + entry["new"]["path"]] = (entry["new"], entry["newIdentity"])
            if entry["old"] is not None:
                expected["backup/" + entry["old"]["path"]] = (entry["old"], entry["oldIdentity"])
    allowed_dirs = set()
    for name in expected:
        parent = Path(name).parent
        while parent.as_posix() != ".":
            allowed_dirs.add(parent.as_posix())
            parent = parent.parent
    pending, payloads, directories = [directory], [], []
    count = 0
    while pending:
        parent = pending.pop()
        files._no_links(parent)
        for path in parent.iterdir():
            relative = path.relative_to(directory).as_posix()
            count += 1
            if count > 200000:
                raise _cleanup_error(HISTORY + "/" + directory.name, "history-object-limit")
            try:
                files._no_links(path)
            except (OSError, ValueError) as error:
                raise _cleanup_error(HISTORY + "/" + directory.name + "/" + relative, "linked-history-object") from error
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                if relative not in allowed_dirs:
                    raise _cleanup_error(HISTORY + "/" + directory.name + "/" + relative, "unknown-directory")
                directories.append((path, transaction._identity(info)))
                pending.append(path)
            elif relative == "journal.json":
                if not stat.S_ISREG(info.st_mode) or transaction._read_json(path) != journal:
                    raise _cleanup_error(HISTORY + "/" + directory.name + "/journal.json", "journal-changed")
            elif relative in expected:
                row, identity = expected[relative]
                if identity is None:
                    raise _cleanup_error(HISTORY + "/" + directory.name + "/" + relative, "retained-object-unwitnessed")
                if not transaction._matches(path, row, identity):
                    raise _cleanup_error(HISTORY + "/" + directory.name + "/" + relative, "retained-object-changed")
                payloads.append((path, row, identity))
            else:
                raise _cleanup_error(HISTORY + "/" + directory.name + "/" + relative, "unknown-object")
    return payloads, sorted(directories, key=lambda value: len(value[0].parts), reverse=True)


def _remove_empty_directory(path, identity):
    files._no_links(path)
    if transaction._identity(path.stat()) != identity:
        raise _cleanup_error(HISTORY + "/" + path.name, "directory-identity-changed")
    path.rmdir()  # Empty only; never recursively remove a path or follow a link.
    transaction._directory_sync(path.parent)


def _prune_retired(root, protected):
    records = []
    for directory in _history_directories(root):
        path = files._target(directory, "journal.json")
        if not path.exists():
            # A crash after removing an old tiny audit record may leave its
            # empty directory. rmdir cannot delete any unknown contents.
            if not any(directory.iterdir()):
                _remove_empty_directory(directory, transaction._identity(directory.stat()))
                continue
            raise _cleanup_error(HISTORY + "/" + directory.name, "history-journal-missing")
        value = transaction._read_json(path)
        if value.get("schema") != RETIRED_SCHEMA:
            continue
        _, value = _load_history(root, directory.name)
        _cleanup_inventory(directory, value)
        records.append((path.stat().st_mtime_ns, directory.name, directory))
    candidates = [record for record in sorted(records) if record[1] != protected]
    for _, transaction_id, directory in candidates[:max(0, len(records) - MAX_RETIRED_RECORDS)]:
        path = files._target(directory, "journal.json")
        _, journal = _load_history(root, transaction_id)
        payloads, _ = _cleanup_inventory(directory, journal)
        # A rearm can stop after recording a complete private marker but before
        # moving it to PENDING. Retire that known object before its last stamp.
        for retained, retained_row, retained_identity in payloads:
            if not files._rollback_published(retained, tuple(retained_identity), retained_row['sha256']):
                raise _cleanup_error(HISTORY + '/' + transaction_id, 'audit-marker-changed')
            transaction._directory_sync(directory)
        row = dict(path="journal.json", bytes=path.stat().st_size, sha256=files._sha(path))
        identity = transaction._capture(path, row)
        directory_identity = transaction._identity(directory.stat())
        if not files._rollback_published(path, tuple(identity), row["sha256"]):
            raise _cleanup_error(HISTORY + "/" + transaction_id + "/journal.json", "audit-record-changed")
        transaction._directory_sync(directory)
        _remove_empty_directory(directory, directory_identity)


def _finish_cleanup(root, directory, journal, *, prune=True):
    """Irreversible terminal phase. No branch here restores an active resource."""
    transaction_id = journal["transactionId"]
    _ensure_pending(root, transaction_id, journal=journal)
    try:
        payloads, directories = _cleanup_inventory(directory, journal)
        if journal["schema"] in ACTIVE_JOURNAL_SCHEMAS:
            if journal["status"] not in {"committed", "rolled-back"}:
                raise _cleanup_error(HISTORY + "/" + transaction_id, "transaction-not-terminal")
            if journal.get("cleanupStatus") != "retiring":
                journal["cleanupStatus"] = "retiring"
                _write_journal(directory, journal)
        for path, row, identity in payloads:
            if not files._rollback_published(path, tuple(identity), row["sha256"]):
                raise _cleanup_error(HISTORY + "/" + transaction_id + "/" + path.relative_to(directory).as_posix(), "retained-object-delete-failed")
            transaction._directory_sync(path.parent)
        for path, identity in directories:
            _remove_empty_directory(path, identity)
        if journal["schema"] in ACTIVE_JOURNAL_SCHEMAS:
            if transaction._read_json(files._target(directory, "journal.json")) != journal:
                raise _cleanup_error(HISTORY + "/" + transaction_id + "/journal.json", "journal-changed")
            compact = dict(schema=RETIRED_SCHEMA, transactionId=transaction_id,
                           rootIdentity=journal["rootIdentity"], outcome=journal["status"],
                           fileCount=len(journal["files"]),
                           **({'publication': 'move-v1', 'pendingStamp': journal['pendingStamp']}
                              if journal.get('publication') == 'move-v1' else {}))
            if len(_encoded(compact)) > MAX_RETIRED_BYTES:
                raise _cleanup_error(HISTORY + "/" + transaction_id, "audit-record-too-large")
            _write_journal(directory, compact)
        if prune:
            _prune_retired(root, transaction_id)
        _clear_pending(root, transaction_id)
    except Exception as error:
        if isinstance(error, ManagedBaseResourceError) and error.code == "base-cleanup-incomplete":
            raise
        raise _cleanup_error(HISTORY + "/" + transaction_id, "cleanup-write-or-validation-failed") from error


def _validate_target_ancestors(path, reason='base-target-parent-not-directory'):
    # On Windows a descendant under a regular file can look absent, and the
    # directory-sync adapter is a no-op. Neither is proof of a valid parent.
    # Check all lexical ancestors so a missing immediate parent cannot conceal
    # an unknown file higher up. Truly missing directories remain valid for
    # planning, publication and rollback; existing non-directories never do.
    for parent in path.parents:
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISDIR(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ManagedBaseResourceError(reason)


def _rollback(root, directory, journal):
    blocked = []
    # Preflight the entire restoration; changed unknown files remain untouched.
    for entry in journal["entries"]:
        new, old = entry["new"], entry["old"]
        row = new or old
        path = files._target(root, row["path"])
        _validate_target_ancestors(path, 'base-rollback-parent-not-directory')
        if old is not None and transaction._matches(path, old, entry["oldIdentity"]):
            continue
        if old is not None and not transaction._matches(files._target(directory, "backup/" + row["path"]), old, entry["oldIdentity"]):
            blocked.append(row["path"])
        if path.exists() and (new is None or not transaction._matches(path, new, entry["newIdentity"])):
            blocked.append(row["path"])
    if not blocked:
        for entry in reversed(journal["entries"]):
            new, old = entry["new"], entry["old"]
            row = new or old
            path = files._target(root, row["path"])
            _validate_target_ancestors(path, 'base-rollback-parent-not-directory')
            if old is not None and transaction._matches(path, old, entry["oldIdentity"]):
                continue
            if old is None and not path.exists() and not path.parent.exists():
                # No old object to restore and no existing parent to flush,
                # e.g. publication never reached this directory. Do not make
                # missing paths merely to sync a no-op. If the parent exists,
                # still sync it: a prior rollback may have unlinked the target
                # before being interrupted. Real sync errors remain blocking.
                continue
            try:
                if path.exists() and (new is None or not files._rollback_published(path, tuple(entry["newIdentity"]), new["sha256"])):
                    blocked.append(row["path"])
                    continue
                if old is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    files._no_links(path.parent)
                    backup = files._target(directory, "backup/" + row["path"])
                    if journal.get('publication') == 'move-v1':
                        transaction._move_owned(backup, path, old, entry['oldIdentity'], linkless=True)
                    else:
                        os.link(backup, path)
                transaction._directory_sync(path.parent)
            except OSError:
                blocked.append(row["path"])
    journal.update(status="rollback-incomplete" if blocked else "rolled-back", blockedPaths=blocked)
    _write_journal(directory, journal)
    if blocked:
        raise ManagedBaseResourceError("base-rollback-incomplete; retained-backups-and-pending-marker",
                                       [dict(path=path, reason="changed-or-unwritable") for path in blocked])
    _finish_cleanup(root, directory, journal)


def _load_history(root, transaction_id):
    if not _is_transaction_id(transaction_id):
        raise ManagedBaseResourceError("invalid-base-transaction-id")
    directory = files._target(root, HISTORY + "/" + transaction_id)
    path = files._target(directory, "journal.json")
    journal = transaction._read_json(path)
    if (journal.get("transactionId") != transaction_id
            or journal.get("rootIdentity") != transaction._identity(root.stat())
            or journal.get('publication') not in (None, 'move-v1')
            or journal.get("schema") not in ACTIVE_JOURNAL_SCHEMAS | {RETIRED_SCHEMA}):
        raise ManagedBaseResourceError("invalid-base-repair-journal")
    _pending_stamp(journal)
    if journal["schema"] == RETIRED_SCHEMA:
        expected = {'schema', 'transactionId', 'rootIdentity', 'outcome', 'fileCount'}
        if journal.get('publication') == 'move-v1':
            expected |= {'publication', 'pendingStamp'}
        if (set(journal) != expected
                or journal["outcome"] not in {"committed", "rolled-back"}
                or type(journal["fileCount"]) is not int or not 0 < journal["fileCount"] <= 20000
                or path.stat().st_size > MAX_RETIRED_BYTES):
            raise ManagedBaseResourceError("invalid-base-retired-record")
        return directory, journal
    if (journal.get("status") not in {"preparing", "prepared", "committed", "rolled-back", "rollback-incomplete"}
            or set(journal) - {"schema", "transactionId", "rootIdentity", "status", "files", "entries", "blockedPaths", "cleanupStatus", "retirement", "publication", "pendingStamp"}
            or journal.get('publication') not in (None, 'move-v1')
            or not isinstance(journal.get("blockedPaths"), list)
            or journal.get("cleanupStatus") not in {None, "retiring"}
            or (journal.get("cleanupStatus") == "retiring" and journal["status"] not in {"committed", "rolled-back"})):
        raise ManagedBaseResourceError("invalid-base-repair-journal")
    expected = _rows(journal.get("files"))
    retired = {}
    if journal['schema'] == RETIREMENT_JOURNAL_SCHEMA:
        retirement = journal.get('retirement')
        if not isinstance(retirement, dict) or set(retirement) != {'sourceVersion', 'targetVersion', 'files'}:
            raise ManagedBaseResourceError('invalid-base-retirement-contract')
        releases, _ = _reviewed_s1_catalogue()
        source = next((rows for version, rows in releases if version == retirement['sourceVersion']), None)
        if source is None:
            raise ManagedBaseResourceError('invalid-base-retirement-source')
        source_rows = {path.casefold(): dict(path=path, bytes=size, sha256=digest) for path, size, digest in source}
        retired = _retirement_rows(retirement['sourceVersion'], retirement['targetVersion'], source_rows, expected)
        if not retired or files._rows(retirement['files']) != retired:
            raise ManagedBaseResourceError('invalid-base-retirement-inventory')
    elif 'retirement' in journal:
        raise ManagedBaseResourceError('invalid-legacy-base-retirement')
    seen = set()
    entries = journal.get("entries")
    if not isinstance(entries, list) or not entries or len(entries) > len(expected) + len(retired) + 1:
        raise ManagedBaseResourceError("invalid-base-repair-entries")
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'new', 'old', 'newIdentity', 'oldIdentity'}:
            raise ManagedBaseResourceError('invalid-base-repair-entry')
        removing = entry['new'] is None
        row = next(iter(files._rows([entry.get('old') if removing else entry['new']]).values()))
        key = row["path"].casefold()
        if key in seen or (retired.get(key) != row if removing else row["path"] != STATE and expected.get(key) != row):
            raise ManagedBaseResourceError("invalid-base-repair-target")
        seen.add(key)
        if removing:
            if entry['newIdentity'] is not None:
                raise ManagedBaseResourceError('invalid-base-retirement-file-identity')
        elif entry.get("newIdentity") is not None:
            transaction._valid_identity(entry["newIdentity"])
        elif journal["status"] not in {"preparing", "rolled-back"}:
            raise ManagedBaseResourceError("invalid-base-repair-file-identity")
        old = entry.get("old")
        if old is not None:
            files._rows([old])
            transaction._valid_identity(entry.get("oldIdentity"))
            if old["path"] != row["path"]:
                raise ManagedBaseResourceError("invalid-base-repair-old-target")
    if entries[-1]['new'] is None or entries[-1]["new"]["path"] != STATE:
        raise ManagedBaseResourceError("invalid-base-repair-state-entry")
    if any(not isinstance(path, str) or path.casefold() not in seen for path in journal["blockedPaths"]):
        raise ManagedBaseResourceError("invalid-base-repair-blocked-path")
    return directory, journal


def _load_pending(root):
    pointer = transaction._read_json(files._target(root, PENDING))
    transaction_id = pointer.get("transactionId")
    if pointer != dict(schema=JOURNAL_SCHEMA, transactionId=transaction_id) or not _is_transaction_id(transaction_id):
        raise ManagedBaseResourceError("invalid-base-pending-pointer")
    directory, journal = _load_history(root, transaction_id)
    stamp = _pending_stamp(journal)
    if stamp is not None and not transaction._matches(files._target(root, PENDING), stamp, stamp['identity']):
        raise ManagedBaseResourceError('base-pending-pointer-changed')
    return directory, journal


def _recover(root):
    if not files._target(root, PENDING).exists():
        return dict(status="no-pending-base-repair")
    directory, journal = _load_pending(root)
    if journal["schema"] == RETIRED_SCHEMA or journal["status"] in {"committed", "rolled-back"}:
        outcome = journal.get("outcome", journal.get("status"))
        _finish_cleanup(root, directory, journal)
        return dict(status=outcome, cleanupComplete=True)
    if journal["status"] == "preparing":
        # No active target was touched. Validate every retained preparation
        # object before changing the outcome, never invent an unknown identity.
        _cleanup_inventory(directory, journal)
        journal["status"] = "rolled-back"
        _write_journal(directory, journal)
        _finish_cleanup(root, directory, journal)
        return dict(status="rolled-back", cleanupComplete=True)
    for entry in journal["entries"]:
        row = entry["new"]
        if row is None:
            continue
        retained = files._target(directory, "new/" + row["path"])
        # Move publication transfers the original staged object to its target.
        # An absent pin is expected, not permission to adopt replacement data.
        # _rollback validates every active object and old backup before mutation;
        # it also handles a second crash after removal/restoration on rollback.
        if journal.get('publication') == 'move-v1' and not retained.exists():
            continue
        if not transaction._matches(retained, row, entry["newIdentity"]):
            raise ManagedBaseResourceError("base-repair-retained-object-changed")
    _rollback(root, directory, journal)
    return dict(status="rolled-back", cleanupComplete=True)


def _retire_completed(root):
    retired = 0
    for directory in _history_directories(root):
        if not directory.exists():
            continue  # A previous iteration pruned this verified audit record.
        path = files._target(directory, "journal.json")
        if not path.exists() and not any(directory.iterdir()):
            _remove_empty_directory(directory, transaction._identity(directory.stat()))
            continue
        try:
            _, journal = _load_history(root, directory.name)
            if journal["schema"] == RETIRED_SCHEMA:
                # Already compact and clean is a read-only no-op. Do not make
                # a healthy launch rewrite every audit's pending marker.
                _cleanup_inventory(directory, journal)
                continue
            _ensure_pending(root, directory.name, journal=journal)
            if journal["schema"] != RETIRED_SCHEMA and journal["status"] not in {"committed", "rolled-back"}:
                raise _cleanup_error(HISTORY + "/" + directory.name, "historical-transaction-not-terminal")
            _finish_cleanup(root, directory, journal, prune=False)
            retired += int(journal["schema"] != RETIRED_SCHEMA)
        except Exception as error:
            _ensure_pending(root, directory.name)
            if isinstance(error, ManagedBaseResourceError) and error.code == "base-cleanup-incomplete":
                raise
            raise _cleanup_error(HISTORY + "/" + directory.name, "historical-record-invalid") from error
    audits = []
    for directory in _history_directories(root):
        path = files._target(directory, "journal.json")
        _, journal = _load_history(root, directory.name)
        if journal["schema"] == RETIRED_SCHEMA:
            audits.append((path.stat().st_mtime_ns, directory.name, directory, journal))
    if len(audits) > MAX_RETIRED_RECORDS:
        _, _, directory, journal = max(audits, key=lambda item: (item[0], item[1]))
        _finish_cleanup(root, directory, journal)
    return dict(status="cleanup-complete", retiredTransactions=retired)


def retire_managed_base_resources(install_root):
    """Retire verified completed history only; never restore old active data."""
    from resource_transaction import resource_write_lock
    with resource_write_lock(install_root) as root:
        root = Path(root)
        if files._target(root, PENDING).exists():
            directory, journal = _load_pending(root)
            if journal["schema"] != RETIRED_SCHEMA and journal["status"] not in {"committed", "rolled-back"}:
                raise _cleanup_error(PENDING, "transaction-not-terminal")
            _finish_cleanup(root, directory, journal)
        return _retire_completed(root)


def recover_managed_base_resources(install_root):
    """Recover only verified objects; never overwrite an unknown post-crash edit."""
    from resource_transaction import resource_write_lock
    with resource_write_lock(install_root) as root:
        root = Path(root)
        result = _recover(root)
        _retire_completed(root)
        return result


def repair_managed_base_resources(install_root):
    """Repair once before creating providers/caches, while the runtime owns its lease."""
    from resource_transaction import resource_write_lock, preflight_resource_publication
    with resource_write_lock(install_root) as root:
        root = Path(root)
        if files._target(root, 'work/pending-installer-upgrade.json').exists():
            raise ManagedBaseResourceError('installer-upgrade-recovery-required')
        if files._target(root, 'work/map-packages/pending-resource-reset.json').exists():
            raise ManagedBaseResourceError('resource-reset-recovery-required')
        if files._target(root, PENDING).exists():
            _recover(root)
        for name, code in (("pending-upgrade.json", "author-upgrade-recovery-required"),
                           ("pending-additions.json", "additions-recovery-required"),
                           ("pending-removal.json", "removal-recovery-required"),
                           ("pending-author-snapshot.json", "author-snapshot-recovery-required")):
            if files._target(root, "work/map-packages/" + name).exists():
                raise ManagedBaseResourceError(code)
        _retire_completed(root)
        plan = plan_managed_base_resources(root)
        if plan["status"] in {"not-managed", "retirement-deferred"}:
            return plan
        if plan["issues"]:
            raise ManagedBaseResourceError("base-repair-preflight-blocked", plan["issues"])
        state = files._target(root, STATE)
        encoded = _encoded(plan["manifest"])
        if not plan["changes"] and state.exists() and state.read_bytes() == encoded:
            return dict(status="healthy", bundleId=plan["manifest"]["bundleId"], checked=len(plan["files"]), changed=0,
                        retainedLegacyPackages=plan["retainedLegacyPackages"])
        state_row = dict(path=STATE, bytes=len(encoded), sha256=hashlib.sha256(encoded).hexdigest())
        old_state = dict(path=STATE, bytes=state.stat().st_size, sha256=files._sha(state)) if state.exists() else None
        changes = [*plan["changes"], dict(new=state_row, old=old_state)]
        publication = preflight_resource_publication(root, [(entry['new'] or entry['old'])["path"] for entry in changes])
        transaction_id = str(uuid.uuid4())
        directory = files._target(root, HISTORY + "/" + transaction_id)
        directory.mkdir(parents=True, exist_ok=False)
        entries = []
        for change in changes:
            row, old = change["new"], change["old"]
            target = files._target(root, (row or old)["path"])
            entries.append(dict(new=row, old=old, newIdentity=None,
                                oldIdentity=transaction._capture(target, old) if old else None))
            if old is None and target.exists():
                raise ManagedBaseResourceError("base-target-appeared-before-commit")
        journal = dict(schema=RETIREMENT_JOURNAL_SCHEMA if plan.get('retirement') else JOURNAL_SCHEMA,
                       transactionId=transaction_id, rootIdentity=transaction._identity(root.stat()),
                       status="preparing", files=plan["files"], entries=entries, blockedPaths=[],
                       **({'publication': publication} if publication == 'move-v1' else {}),
                       **({'retirement': plan['retirement']} if plan.get('retirement') else {}))
        if publication == 'move-v1':
            _ensure_pending(root, transaction_id, journal=journal)
        else:
            _write_journal(directory, journal)
            _ensure_pending(root, transaction_id)
        try:
            for entry in entries:
                row = entry["new"]
                if row is None:
                    continue
                retained = files._target(directory, "new/" + row["path"])
                data = encoded if row["path"] == STATE else files._target(root, CANDIDATE + "/payload/" + row["path"]).read_bytes()
                transaction._write_new(retained, data)
                entry["newIdentity"] = transaction._capture(retained, row)
                _write_journal(directory, journal)
            journal["status"] = "prepared"
            _write_journal(directory, journal)
        except Exception as error:
            try:
                _recover(root)
            except Exception as recovery_error:
                if isinstance(recovery_error, ManagedBaseResourceError) and recovery_error.code == "base-cleanup-incomplete":
                    raise
                raise _cleanup_error(HISTORY + "/" + transaction_id, "preparation-incomplete-retained-for-recovery") from recovery_error
            raise
        try:
            # Preparation can take time. Recheck the complete destination set
            # before changing even the first active object, not only as each
            # target is published. A blocked late path must not cause a partial
            # publication that the deliberately strict rollback cannot undo.
            for entry in entries:
                row = entry['new'] or entry['old']
                _validate_target_ancestors(files._target(root, row['path']))
            for entry in entries:
                row = entry["new"]
                relative = (row or entry['old'])['path']
                target = files._target(root, relative)
                _validate_target_ancestors(target)
                if relative == STATE:
                    for current in plan["files"]:
                        if not transaction._matches(files._target(root, current["path"]), current):
                            raise ManagedBaseResourceError("base-verification-before-state-failed")
                    for retired_entry in entries:
                        if retired_entry['new'] is None and files._target(root, retired_entry['old']['path']).exists():
                            raise ManagedBaseResourceError('retired-base-reappeared-before-state')
                if entry["old"]:
                    transaction._move_owned(target, files._target(directory, "backup/" + relative),
                                            entry["old"], entry["oldIdentity"],
                                            **({'linkless': True} if publication == 'move-v1' else {}))
                if row is None:
                    transaction._directory_sync(target.parent)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                files._no_links(target.parent)
                retained = files._target(directory, "new/" + row["path"])
                if publication == 'move-v1':
                    transaction._move_owned(retained, target, row, entry['newIdentity'], linkless=True)
                else:
                    os.link(retained, target)
                if not transaction._matches(target, row, entry["newIdentity"]):
                    raise ManagedBaseResourceError("base-published-object-changed")
                transaction._directory_sync(target.parent)
            journal["status"] = "committed"
            _write_journal(directory, journal)
        except Exception as error:
            # A write can raise after replacing the journal. Once the durable
            # record says committed, even an uncertain flush may not resurrect
            # old active data. Recovery can only finish the terminal cleanup.
            try:
                recorded = transaction._read_json(files._target(directory, "journal.json"))
            except Exception as journal_error:
                raise _cleanup_error(HISTORY + "/" + transaction_id, "commit-state-unreadable") from journal_error
            if recorded.get("status") == "committed":
                raise _cleanup_error(HISTORY + "/" + transaction_id, "commit-record-write-uncertain") from error
            try:
                _rollback(root, directory, journal)
            except Exception as rollback_error:
                if isinstance(rollback_error, ManagedBaseResourceError) and rollback_error.code == "base-cleanup-incomplete":
                    raise
                raise ManagedBaseResourceError(f"base-repair-failed-and-rollback-incomplete: {rollback_error}") from error
            raise
        _finish_cleanup(root, directory, journal)
        return dict(status="repaired" if plan["changes"] else "healthy", bundleId=plan["manifest"]["bundleId"],
                    checked=len(plan["files"]), changed=len(plan["changes"]), transactionId=transaction_id,
                    publication=publication,
                    retainedLegacyPackages=plan["retainedLegacyPackages"])
