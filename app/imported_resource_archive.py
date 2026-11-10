"""Inspect and retain immutable xingtu-resource-package ZIP versions.

This module owns only container validation and inert version storage. It does
not activate packages, write a catalog, or establish semantic usability.
Staged files are fsynced before no-replace publication. Directory sync follows
the shared upgrade helper; it is a no-op on Windows because Python cannot
portably flush a Windows directory handle.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import uuid
import zipfile
import zlib

import imported_resource_packages as packages
import map_package_upgrade as package_upgrade
import resource_transaction
import star_map_import as files


MANIFEST_NAME = packages.MANIFEST_NAME
MANIFEST_LIMIT = packages.MANIFEST_LIMIT
MAX_FILES = files.MAX_FILES
MAX_FILE_BYTES = files.MAX_FILE_BYTES
MAX_TOTAL_BYTES = files.MAX_TOTAL_BYTES
MAX_ARCHIVE_BYTES = files.MAX_ARCHIVE_BYTES
_READ_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ArchiveSummary:
    package_id: str
    resource_version: str
    archive_sha256: str
    manifest_sha256: str
    component_refs: tuple[packages.ComponentRef, ...]
    status: str


def _reject(reason: str) -> None:
    raise files.ImportRejected('resource-archive-' + reason)


def _reject_legacy() -> None:
    raise files.ImportRejected('legacy-format-unsupported', code='legacy-format-unsupported')


def _directory_sync(path: Path) -> None:
    """Use the shared directory durability helper (a no-op on Windows)."""
    package_upgrade._directory_sync(path)


def _check_cancelled(cancelled) -> None:
    if cancelled is not None and cancelled():
        _reject('cancelled')


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _current_path_info(path: Path):
    """Reopen the selected pathname so identity uses fstat on every platform."""
    files._no_links(path)
    flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0)
             | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    descriptor = os.open(path, flags)
    try:
        return os.fstat(descriptor)
    finally:
        os.close(descriptor)


def _same_archive(path: Path, handle, initial_stamp) -> None:
    try:
        current_handle = os.fstat(handle.fileno())
        current_path = _current_path_info(path)
    except OSError as error:
        raise files.ImportRejected('resource-archive-changed') from error
    if (not stat.S_ISREG(current_handle.st_mode)
            or _stamp(current_handle) != initial_stamp
            or _stamp(current_path) != initial_stamp):
        _reject('changed')
    final_handle = os.fstat(handle.fileno())
    try:
        final_path = _current_path_info(path)
    except OSError as error:
        raise files.ImportRejected('resource-archive-changed') from error
    if _stamp(final_handle) != initial_stamp or _stamp(final_path) != initial_stamp:
        _reject('changed')


@contextmanager
def _open_archive(zip_path, expected_sha256=None):
    """Hash and read one open regular file, then detect replacement or writes."""
    path = Path(os.path.abspath(zip_path))
    files._no_links(path)
    flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0)
             | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        descriptor = os.open(path, flags)
        handle_context = os.fdopen(descriptor, 'rb')
    except OSError as error:
        raise files.ImportRejected('resource-archive-unavailable') from error
    with handle_context as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            _reject('not-regular-file')
        if before.st_size > MAX_ARCHIVE_BYTES:
            _reject('archive-size-limit')
        stamp = _stamp(before)
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        after_hash = os.fstat(handle.fileno())
        if _stamp(after_hash) != stamp:
            _reject('changed')
        if expected_sha256 is not None and digest != expected_sha256:
            raise files.ImportRejected('selected-archive-changed')
        try:
            current_path = _current_path_info(path)
        except OSError as error:
            raise files.ImportRejected('resource-archive-changed') from error
        if _stamp(current_path) != stamp:
            _reject('changed')
        handle.seek(0)
        try:
            with zipfile.ZipFile(handle) as archive:
                yield archive, digest
        except (zipfile.BadZipFile, EOFError) as error:
            raise files.ImportRejected('resource-archive-invalid-zip') from error
        _same_archive(path, handle, stamp)


def _parse_manifest(raw):
    try:
        manifest = files._json(raw)
    except (TypeError, ValueError, UnicodeError) as error:
        raise files.ImportRejected('resource-archive-invalid-manifest') from error
    if not isinstance(manifest, dict):
        _reject('invalid-manifest')
    if manifest.get('schema') != packages.SCHEMA:
        _reject_legacy()
    if type(manifest.get('formatVersion')) is not int or manifest['formatVersion'] != packages.FORMAT_VERSION:
        _reject('unsupported-manifest-format')
    try:
        package_id = manifest['packageId']
        resource_version = manifest['resourceVersion']
        rows = manifest['components']
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            _reject('invalid-components')
        manifest_sha = hashlib.sha256(raw).hexdigest()
        first_component_id = rows[0]['componentId']
        ref = packages.ComponentRef(package_id, resource_version,
                                    first_component_id, manifest_sha)
        components, inventory = packages.PackageView._validate_manifest(manifest, ref)
    except files.ImportRejected:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise files.ImportRejected('resource-archive-invalid-manifest') from error
    refs = tuple(packages.ComponentRef(package_id, resource_version, component_id, manifest_sha)
                 for component_id in components)
    return manifest, inventory, refs, manifest_sha


def _archive_inventory(archive: zipfile.ZipFile, cancelled):
    infos = archive.infolist()
    has_new_manifest = any(info.filename.casefold() == MANIFEST_NAME.casefold()
                           for info in infos)
    has_legacy_manifest = any(info.filename.casefold() == 'map-package.json'
                              for info in infos)
    if not has_new_manifest and has_legacy_manifest:
        _reject_legacy()
    if not 2 <= len(infos) <= MAX_FILES + 1:
        _reject('invalid-entry-count')
    entries = {}
    total = 0
    for entry in infos:
        _check_cancelled(cancelled)
        if entry.is_dir():
            _reject('unsupported-entry')
        try:
            path = files._relative(entry.filename)
        except (TypeError, ValueError) as error:
            raise files.ImportRejected('resource-archive-unsafe-path') from error
        kind = stat.S_IFMT(entry.external_attr >> 16)
        if (entry.orig_filename != entry.filename
                or kind not in {0, stat.S_IFREG} or entry.flag_bits & 1
                or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or entry.external_attr & (0x10 | 0x400)):
            _reject('unsupported-entry')
        if path == MANIFEST_NAME:
            if entry.file_size > MANIFEST_LIMIT:
                _reject('manifest-too-large')
        elif entry.file_size > MAX_FILE_BYTES:
            _reject('file-size-limit')
        total += entry.file_size
        if total > MAX_TOTAL_BYTES + MANIFEST_LIMIT:
            _reject('expanded-size-limit')
        key = path.casefold()
        if key in entries:
            _reject('duplicate-archive-path')
        entries[key] = entry
    manifest_entry = entries.get(MANIFEST_NAME.casefold())
    if manifest_entry is None and 'map-package.json' in entries:
        _reject_legacy()
    if manifest_entry is None or manifest_entry.filename != MANIFEST_NAME:
        _reject('manifest-missing')
    raw_manifest = _read_member(archive, manifest_entry, MANIFEST_LIMIT, cancelled)
    manifest, inventory, refs, manifest_sha = _parse_manifest(raw_manifest)

    expected = {MANIFEST_NAME: None}
    for (component_id, relative), row in inventory.items():
        path = f'data/{component_id}/{relative}'
        expected[path] = row
    if set(entries) != {path.casefold() for path in expected}:
        _reject('archive-inventory-mismatch')
    for path, row in expected.items():
        entry = entries.get(path.casefold())
        if entry is None or entry.filename != path:
            _reject('file-size-or-case-mismatch')
        if row is not None and entry.file_size != row['bytes']:
            _reject('file-size-mismatch')
    _reject_file_prefix_conflicts(expected)
    return raw_manifest, manifest, manifest_sha, refs, inventory, entries


def _reject_file_prefix_conflicts(expected):
    folded = {tuple(part.casefold() for part in path.split('/')) for path in expected}
    for path in folded:
        for index in range(1, len(path)):
            if path[:index] in folded:
                _reject('file-directory-conflict')


def _identity(info):
    return (info.st_dev, info.st_ino)


def _directory_identity(path: Path):
    files._no_links(path)
    try:
        info = path.lstat()
    except OSError as error:
        raise files.ImportRejected('resource-archive-stage-changed') from error
    if not stat.S_ISDIR(info.st_mode):
        _reject('stage-changed')
    return _identity(info)


def _new_stage_ownership(stage: Path):
    identity = _directory_identity(stage)
    return {'root_identity': identity, 'directories': {'': identity},
            'files': {}, 'published': False, 'published_prefix': None}


def _ensure_owned_directories(stage: Path, target: Path, ownership) -> None:
    try:
        relative = target.relative_to(stage)
    except ValueError as error:
        raise files.ImportRejected('resource-archive-stage-path-invalid') from error
    current = stage
    for part in relative.parts:
        current = current / part
        key = current.relative_to(stage).as_posix()
        files._no_links(current)
        try:
            current.mkdir()
        except FileExistsError:
            identity = _directory_identity(current)
            if ownership['directories'].get(key) != identity:
                _reject('staging-object-conflict')
        except OSError as error:
            raise files.ImportRejected('resource-archive-storage-unavailable') from error
        else:
            identity = _directory_identity(current)
            if key in ownership['directories']:
                _reject('staging-object-conflict')
            ownership['directories'][key] = identity
            _directory_sync(current.parent)


def _write_owned_file(stage: Path, target: Path, data: bytes, ownership) -> None:
    try:
        relative = target.relative_to(stage).as_posix()
    except ValueError as error:
        raise files.ImportRejected('resource-archive-stage-path-invalid') from error
    digest = hashlib.sha256(data).hexdigest()
    files._no_links(target)
    try:
        identity = tuple(package_upgrade._write_new(target, data))
    except FileExistsError as error:
        raise files.ImportRejected('resource-archive-staging-conflict') from error
    except OSError as error:
        raise files.ImportRejected('resource-archive-storage-unavailable') from error
    row = {'path': relative, 'bytes': len(data), 'sha256': digest}
    if not package_upgrade._matches(target, row, list(identity)):
        _reject('staged-file-changed')
    ownership['files'][relative] = (row, identity)


def _read_member(archive, info, limit, cancelled, sink=None, expected=None):
    digest = hashlib.sha256()
    count = 0
    chunks = [] if sink is None else None
    try:
        with archive.open(info, 'r') as source:
            while True:
                _check_cancelled(cancelled)
                bound = limit if expected is None else expected
                block = source.read(min(_READ_BYTES, bound - count + 1))
                if not block:
                    break
                count += len(block)
                if count > limit or (expected is not None and count > expected):
                    _reject('expanded-file-size-limit')
                digest.update(block)
                if sink is not None:
                    try:
                        sink.write(block)
                    except OSError as error:
                        raise files.ImportRejected('resource-archive-storage-unavailable') from error
                else:
                    chunks.append(block)
        _check_cancelled(cancelled)
    except (zipfile.BadZipFile, EOFError, RuntimeError, OSError, zlib.error) as error:
        if isinstance(error, files.ImportRejected):
            raise
        raise files.ImportRejected('resource-archive-corrupt-entry') from error
    if expected is not None and count != expected:
        _reject('file-size-mismatch')
    return digest.digest() if sink is not None else b''.join(chunks)


def _extract_archive(archive, root: Path, parsed, cancelled, ownership):
    raw_manifest, _manifest, _manifest_sha, _refs, inventory, entries = parsed
    package_id = _manifest['packageId']
    resource_version = _manifest['resourceVersion']
    package_root = files._target(root, f'packages/{package_id}/{resource_version}')
    _ensure_owned_directories(root, package_root, ownership)
    manifest_path = package_root / MANIFEST_NAME
    _write_owned_file(root, manifest_path, raw_manifest, ownership)
    for (component_id, relative), row in inventory.items():
        _check_cancelled(cancelled)
        archive_path = f'data/{component_id}/{relative}'
        member = entries[archive_path.casefold()]
        target = files._target(package_root, archive_path)
        _ensure_owned_directories(root, target.parent, ownership)
        try:
            with target.open('xb') as sink:
                observed = _read_member(archive, member, row['bytes'], cancelled,
                                        sink=sink, expected=row['bytes'])
                sink.flush()
                os.fsync(sink.fileno())
                identity = _identity(os.fstat(sink.fileno()))
        except FileExistsError as error:
            raise files.ImportRejected('resource-archive-staging-conflict') from error
        except OSError as error:
            raise files.ImportRejected('resource-archive-storage-unavailable') from error
        relative_to_stage = target.relative_to(root).as_posix()
        staged_row = {'path': relative_to_stage, 'bytes': row['bytes'],
                      'sha256': observed.hex()}
        if not package_upgrade._matches(target, staged_row, list(identity)):
            _reject('staged-file-changed')
        ownership['files'][relative_to_stage] = (staged_row, identity)
        _directory_sync(target.parent)
        if observed.hex() != row['sha256']:
            _reject('file-integrity-mismatch')
    _check_cancelled(cancelled)
    return package_root


def _validate_staged(root: Path, refs):
    if not refs:
        _reject('invalid-components')
    packages.PackageView(root, refs[0]).validate_inventory()


def _summary(parsed, archive_sha256, status):
    manifest = parsed[1]
    return ArchiveSummary(
        package_id=manifest['packageId'],
        resource_version=manifest['resourceVersion'],
        archive_sha256=archive_sha256,
        manifest_sha256=parsed[2],
        component_refs=parsed[3],
        status=status,
    )


class _HashOnlySink:
    """Discard streamed payload bytes while letting the reader hash and CRC-check."""

    def write(self, block: bytes) -> int:
        return len(block)


def _validate_archive_payloads(archive, parsed, cancelled) -> None:
    _manifest_raw, _manifest, _manifest_sha, _refs, inventory, entries = parsed
    sink = _HashOnlySink()
    for (component_id, relative), row in inventory.items():
        _check_cancelled(cancelled)
        archive_path = f'data/{component_id}/{relative}'
        member = entries[archive_path.casefold()]
        observed = _read_member(archive, member, row['bytes'], cancelled,
                                sink=sink, expected=row['bytes'])
        if observed.hex() != row['sha256']:
            _reject('file-integrity-mismatch')
    _check_cancelled(cancelled)


def inspect_archive(zip_path, *, expected_archive_sha256=None, cancelled=None) -> ArchiveSummary:
    """Read and fully validate a ZIP without creating or changing ResourceRoot."""
    _check_cancelled(cancelled)
    try:
        with _open_archive(zip_path, expected_archive_sha256) as (archive, archive_sha256):
            parsed = _archive_inventory(archive, cancelled)
            _validate_archive_payloads(archive, parsed, cancelled)
        _check_cancelled(cancelled)
        return _summary(parsed, archive_sha256, 'inspected')
    except files.ImportRejected:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise files.ImportRejected('resource-archive-io-error') from error


def _ensure_resource_root(resource_root: Path) -> Path:
    """Create a missing root path one verified, link-free directory at a time."""
    root = Path(os.path.abspath(resource_root))
    chain = list(reversed((root, *root.parents)))
    for path in chain:
        files._no_links(path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            try:
                path.mkdir()
            except FileExistsError:
                # A concurrent creator is acceptable only if it created a real
                # directory at this exact path.
                files._no_links(path)
                try:
                    info = path.lstat()
                except OSError as error:
                    raise files.ImportRejected('resource-root-unavailable') from error
            except OSError as error:
                raise files.ImportRejected('resource-root-unavailable') from error
            else:
                _directory_sync(path.parent)
                files._no_links(path)
                try:
                    info = path.lstat()
                except OSError as error:
                    raise files.ImportRejected('resource-root-unavailable') from error
        except OSError as error:
            raise files.ImportRejected('resource-root-unavailable') from error
        if not stat.S_ISDIR(info.st_mode):
            _reject('resource-root-not-directory')
    return root


def _ensure_directory(root: Path, relative: str) -> Path:
    try:
        normalized = files._relative(relative)
    except (TypeError, ValueError) as error:
        raise files.ImportRejected('resource-archive-storage-path-invalid') from error
    if normalized != relative:
        _reject('resource-archive-storage-path-invalid')
    current = root
    for part in relative.split('/'):
        current = current / part
        files._no_links(current)
        try:
            current.mkdir()
        except FileExistsError:
            files._no_links(current)
            try:
                info = current.lstat()
            except OSError as error:
                raise files.ImportRejected('resource-archive-storage-unavailable') from error
        except OSError as error:
            raise files.ImportRejected('resource-archive-storage-unavailable') from error
        else:
            _directory_sync(current.parent)
            try:
                info = current.lstat()
            except OSError as error:
                raise files.ImportRejected('resource-archive-storage-unavailable') from error
        if not stat.S_ISDIR(info.st_mode):
            _reject('resource-archive-storage-conflict')
    return current


def _existing_version(root: Path, parsed):
    manifest = parsed[1]
    package_id, version = manifest['packageId'], manifest['resourceVersion']
    target = files._target(root, f'packages/{package_id}/{version}')
    try:
        info = target.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise files.ImportRejected('resource-archive-version-conflict') from error
    if not stat.S_ISDIR(info.st_mode):
        _reject('version-conflict')
    try:
        _validate_staged(root, parsed[3])
    except (files.ImportRejected, OSError, ValueError) as error:
        raise files.ImportRejected('resource-archive-version-conflict') from error
    return target


def _walk_stage(stage: Path):
    directories, regular_files = [], []

    def walk(directory, prefix):
        with os.scandir(directory) as children:
            for entry in children:
                path = Path(entry.path)
                files._no_links(path)
                info = path.lstat()
                relative = f'{prefix}/{entry.name}' if prefix else entry.name
                if stat.S_ISDIR(info.st_mode):
                    directories.append((relative, path, _identity(info)))
                    walk(path, relative)
                elif stat.S_ISREG(info.st_mode):
                    regular_files.append((relative, path))
                else:
                    _reject('stage-contains-unknown-object')

    walk(stage, '')
    return directories, regular_files


def _verify_owned_stage(stage: Path, root: Path, ownership):
    files._no_links(stage)
    parsed_id = uuid.UUID(stage.name)
    if (stage.parent != root / 'staging' or parsed_id.hex != stage.name
            or parsed_id.version != 4
            or _directory_identity(stage) != ownership['root_identity']):
        _reject('stage-changed')
    if ownership.get('published_prefix') is not None:
        _reject('stage-already-published')
    expected = {
        name: (row, list(identity))
        for name, (row, identity) in ownership['files'].items()
    }
    planned_files, planned_dirs = package_upgrade._private_tree_plan(stage, expected)
    if ({path.relative_to(stage).as_posix() for path, _, _ in planned_files}
            != set(expected)):
        _reject('stage-incomplete')
    expected_dirs = set(ownership['directories']) - {''}
    actual_dirs = {path.relative_to(stage).as_posix(): path for path in planned_dirs}
    if set(actual_dirs) != expected_dirs:
        _reject('stage-directory-inventory-mismatch')
    for name, path in actual_dirs.items():
        if _directory_identity(path) != ownership['directories'][name]:
            _reject('stage-directory-changed')
    if _directory_identity(stage) != ownership['root_identity']:
        _reject('stage-changed')
    return planned_files, actual_dirs


def _remove_owned_stage(stage: Path, root: Path, ownership) -> bool:
    """Delete only the original, complete staging tree captured by this call."""
    try:
        files._no_links(stage)
        parsed_id = uuid.UUID(stage.name)
        if (stage.parent != root / 'staging' or parsed_id.hex != stage.name
                or parsed_id.version != 4
                or _directory_identity(stage) != ownership['root_identity']):
            return False

        transfer_prefix = ownership.get('published_prefix')
        if ownership.get('published') != (transfer_prefix is not None):
            return False
        if transfer_prefix is not None:
            prefix = transfer_prefix.rstrip('/')
            remaining_dirs = {
                name: identity for name, identity in ownership['directories'].items()
                if name and name != prefix and not name.startswith(prefix + '/')
            }
            if any(not (name == prefix or name.startswith(prefix + '/'))
                   for name in ownership['files']):
                return False
            actual_dirs, actual_files = _walk_stage(stage)
            if actual_files or {name for name, _, _ in actual_dirs} != set(remaining_dirs):
                return False
            for name, path, identity in actual_dirs:
                if remaining_dirs.get(name) != identity:
                    return False
            for _name, path, identity in sorted(
                    actual_dirs, key=lambda item: len(Path(item[0]).parts), reverse=True):
                if _directory_identity(path) != identity:
                    return False
                path.rmdir()
                _directory_sync(path.parent)
            if _directory_identity(stage) != ownership['root_identity']:
                return False
            stage.rmdir()
            _directory_sync(stage.parent)
            return True

        planned_files, actual_dirs = _verify_owned_stage(stage, root, ownership)

        for path, row, identity in planned_files:
            if not files._rollback_published(path, tuple(identity), row['sha256']):
                return False
            _directory_sync(path.parent)
        for name, path in sorted(actual_dirs.items(),
                                 key=lambda item: len(Path(item[0]).parts), reverse=True):
            if _directory_identity(path) != ownership['directories'][name]:
                return False
            path.rmdir()
            _directory_sync(path.parent)
        if _directory_identity(stage) != ownership['root_identity']:
            return False
        stage.rmdir()
        _directory_sync(stage.parent)
        return True
    except (OSError, ValueError, files.ImportRejected):
        # Unknown, replaced, incomplete or otherwise uncertain objects stay put.
        return False


def save_archive(zip_path, resource_root, *, expected_archive_sha256=None, cancelled=None) -> ArchiveSummary:
    """Validate, stage and atomically save one complete immutable version."""
    _check_cancelled(cancelled)
    root = _ensure_resource_root(Path(resource_root))
    try:
        with resource_transaction.resource_write_lock(
                root, wait=True, cancelled=lambda: cancelled is not None and cancelled()):
            files._no_links(root)
            stage = None
            ownership = None
            try:
                with _open_archive(zip_path, expected_archive_sha256) as (archive, archive_sha256):
                    parsed = _archive_inventory(archive, cancelled)
                    package_id = parsed[1]['packageId']
                    version = parsed[1]['resourceVersion']
                    stage_parent = _ensure_directory(root, 'staging')
                    packages_parent = _ensure_directory(root, 'packages')
                    _ensure_directory(packages_parent, package_id)
                    target = files._target(root, f'packages/{package_id}/{version}')

                    stage = stage_parent / uuid.uuid4().hex
                    try:
                        stage.mkdir()
                    except OSError as error:
                        raise files.ImportRejected('resource-archive-staging-unavailable') from error
                    _directory_sync(stage.parent)
                    ownership = _new_stage_ownership(stage)
                    _extract_archive(archive, stage, parsed, cancelled, ownership)
                    _validate_staged(stage, parsed[3])
                    _check_cancelled(cancelled)

                # A same-version retry still validates every ZIP payload and the
                # complete existing tree before it is reported as already saved.
                if target.exists() or target.is_symlink():
                    if _existing_version(root, parsed) is None:
                        _reject('version-conflict')
                    _check_cancelled(cancelled)
                    return _summary(parsed, archive_sha256, 'already-saved')

                # The original open handle was checked against its starting
                # identity and digest. Recheck the selected path immediately
                # before publication so an intervening replacement is refused.
                with _open_archive(zip_path, expected_archive_sha256) as (_check_archive, check_sha):
                    if check_sha != archive_sha256:
                        _reject('changed')
                    _check_cancelled(cancelled)
                candidate_relative = f'packages/{package_id}/{version}'
                candidate = stage / candidate_relative
                candidate_identity = ownership['directories'].get(candidate_relative)
                if candidate_identity is None:
                    _reject('stage-candidate-missing')

                def before_move():
                    _check_cancelled(cancelled)
                    _verify_owned_stage(stage, root, ownership)
                    if _directory_identity(candidate) != candidate_identity:
                        _reject('stage-candidate-changed')
                    _check_cancelled(cancelled)

                try:
                    resource_transaction.rename_owned_directory_no_replace(
                        candidate, target, list(candidate_identity), before_move=before_move)
                except FileExistsError as error:
                    try:
                        existing = _existing_version(root, parsed)
                    except files.ImportRejected as conflict:
                        raise conflict from error
                    if existing is None:
                        raise files.ImportRejected('resource-archive-version-conflict') from error
                    return _summary(parsed, archive_sha256, 'already-saved')
                except OSError as error:
                    if getattr(error, 'errno', None) in {17, 80, 183}:
                        try:
                            existing = _existing_version(root, parsed)
                        except files.ImportRejected as conflict:
                            raise conflict from error
                        if existing is None:
                            raise files.ImportRejected('resource-archive-version-conflict') from error
                        return _summary(parsed, archive_sha256, 'already-saved')
                    raise files.ImportRejected('resource-archive-publish-failed') from error
                except Exception:
                    # The helper may report a post-rename target check failure.
                    # Mark our original directory as transferred only if the
                    # destination still has its creation-time identity.
                    try:
                        if _directory_identity(target) == candidate_identity:
                            ownership['published'] = True
                            ownership['published_prefix'] = candidate_relative
                    except (OSError, ValueError):
                        pass
                    raise
                ownership['published'] = True
                ownership['published_prefix'] = candidate_relative
                _directory_sync(stage / 'packages' / package_id)
                _directory_sync(target.parent)
                if _directory_identity(target) != candidate_identity:
                    _reject('published-package-identity-mismatch')
                _validate_staged(root, parsed[3])
                if _directory_identity(target) != candidate_identity:
                    _reject('published-package-identity-mismatch')
            finally:
                if stage is not None and ownership is not None:
                    _remove_owned_stage(stage, root, ownership)
            return _summary(parsed, archive_sha256, 'saved')
    except resource_transaction.ResourceTransactionError as error:
        raise files.ImportRejected(str(error), code=error.code, details=error.details) from error
    except files.ImportRejected:
        raise
    except OSError as error:
        raise files.ImportRejected('resource-archive-io-error') from error
