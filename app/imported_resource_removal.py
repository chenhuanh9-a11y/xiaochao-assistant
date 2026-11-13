"""Remove one imported version under the catalog's existing commit witness.

The caller owns the Session idle lease. Only manifest-declared, unchanged,
single-link regular files are moved, then deleted through the existing Windows
same-handle primitives. Unknown/modified objects and their manifest stay put.
Only verified empty version directories are pruned on Windows; small transaction
provenance is intentionally retained.
Process-loss recovery is supported; Windows power-loss atomicity is not claimed.
"""
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

import imported_resource_activation as catalog_tx
from imported_resource_catalog import (PENDING_CATALOG, PENDING_REMOVAL, SCHEMA as CATALOG_SCHEMA,
                                       ResourceCatalogError)
from imported_resource_packages import ComponentRef, PackageView, MANIFEST_LIMIT
from local_path_safety import is_link_or_reparse
import map_package_upgrade as publication
import resource_transaction as locks
from resource_snapshot_io import read_stable_file
import star_map_import as files


SCHEMA = 'xingtu-imported-removal-v1'
# Legal package paths can expand into many owned directory prefixes. Include
# their stamps and the exact manifest, independently of the small catalog cap.
LIMIT = files.MAX_FILE_BYTES


def _reject(reason):
    raise ResourceCatalogError('resource-removal-' + reason)


def _encoded(value):
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
    if len(raw) > LIMIT:
        _reject('metadata-too-large')
    return raw


def removal_recovery_required(resource_root):
    # Lexical link checks also reject a replaced/reparse pending parent.
    return files._target(Path(resource_root).absolute(), PENDING_REMOVAL).exists()


def assert_removal_ready(resource_root):
    if removal_recovery_required(resource_root):
        _reject('recovery-required')


def _wire(ref):
    return dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
                componentId=ref.component_id, manifestSha256=ref.manifest_sha256)


def _directory_identity(path):
    files._no_links(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        _reject('directory-changed')
    return publication._identity(info)


def _stamp(path, limit=files.MAX_FILE_BYTES):
    _raw, row = read_stable_file(path, limit, no_links=files._no_links,
                                 reject=_reject, identity=publication._identity)
    return row


def _read_removal_metadata(path):
    return read_stable_file(path, LIMIT, no_links=files._no_links,
                            reject=_reject, identity=publication._identity)


def _write_removal_metadata(path, raw):
    identity = publication._write_new(path, raw)
    actual, stamp = _read_removal_metadata(path)
    if actual != raw or stamp['identity'] != identity:
        _reject('staged-metadata-changed')
    return stamp


def _single_file_metadata(path, row):
    files._no_links(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or publication._identity(info) != row['identity']):
        _reject('confirmed-file-changed')


def _single_file(path, row):
    _single_file_metadata(path, row)
    if not publication._matches(path, row, row['identity']):
        _reject('confirmed-file-changed')


def _version_ref(package_id, resource_version, manifest_sha256):
    # Reuse the production segment/digest validation; the component is chosen
    # from the actual manifest below rather than inferred from a filename.
    return ComponentRef(package_id, resource_version, 'removal-probe', manifest_sha256)


def _manifest(raw, ref):
    if hashlib.sha256(raw).hexdigest() != ref.manifest_sha256:
        _reject('manifest-changed')
    value = files._json(raw)
    components = value.get('components') if isinstance(value, dict) else None
    if not isinstance(components, list) or not components or not isinstance(components[0], dict):
        _reject('invalid-manifest')
    primary = ComponentRef(ref.package_id, ref.resource_version,
                           components[0].get('componentId'), ref.manifest_sha256)
    return PackageView._validate_manifest(value, primary)


def _catalog_proposal(snapshot, ref, cancelled):
    root = snapshot.root
    target = (ref.package_id, ref.resource_version)

    def matches(candidate):
        return candidate is not None and (candidate.package_id, candidate.resource_version) == target

    dependencies = {}

    def check():
        files._check_cancelled(cancelled)
        snapshot.assert_current()

    def depends(candidate, visiting=None):
        check()
        if matches(candidate):
            return True
        if candidate is None:
            return False
        visiting = set() if visiting is None else visiting
        if candidate in visiting:
            return False
        visiting.add(candidate)
        if candidate not in dependencies:
            if len(dependencies) >= files.MAX_FILES:
                _reject('dependency-limit')
            view = PackageView(root, candidate, assert_current=check)
            dependencies[candidate] = tuple(view.dependencies.values())
        return any(depends(item, visiting) for item in dependencies[candidate])

    affected = [key for key, row in snapshot.contexts.items()
                if any(depends(candidate) for candidate in row.values() if candidate is not None)]
    removed = []
    for candidate in snapshot.displays:
        if depends(candidate):
            removed.append(candidate)
    contexts = {key: {role: None if candidate is None else _wire(candidate)
                      for role, candidate in row.items()}
                for key, row in snapshot.contexts.items() if key not in affected}
    check()
    return (dict(schema=CATALOG_SCHEMA, generation=snapshot.generation,
                 contexts=contexts, displays=[_wire(item) for item in snapshot.displays if item not in removed]),
            sorted(affected), [_wire(item) for item in removed])


def _inspect_locked(resolver, package_id, resource_version, manifest_sha256, cancelled):
    ref = _version_ref(package_id, resource_version, manifest_sha256)
    snapshot = resolver.snapshot()
    root = snapshot.root
    prefix = f'packages/{ref.package_id}/{ref.resource_version}'
    version = files._target(root, prefix)
    root_identity = _directory_identity(root)
    directory_rows = []
    for relative in ('packages', f'packages/{ref.package_id}', prefix):
        directory_rows.append(dict(path=relative, identity=_directory_identity(files._target(root, relative))))
    manifest_path = files._target(version, 'manifest.json')
    raw, manifest_stamp = read_stable_file(manifest_path, MANIFEST_LIMIT,
        no_links=files._no_links, reject=_reject, identity=publication._identity)
    if manifest_path.lstat().st_nlink != 1:
        _reject('manifest-linked')
    _components, inventory = _manifest(raw, ref)
    expected = {f'data/{component}/{name}': value for (component, name), value in inventory.items()}
    allowed_directories = set()
    for relative in expected:
        parts = relative.split('/')
        allowed_directories.update('/'.join(parts[:index]) for index in range(1, len(parts)))
    entries, preserved, observed, missing = [], [], set(), []
    count = 0
    entry_limit = len(expected) + len(allowed_directories) + 1 + files.MAX_FILES

    def check():
        files._check_cancelled(cancelled)
        if _directory_identity(root) != root_identity:
            _reject('root-changed')

    def walk(directory, parent=''):
        nonlocal count
        check()
        with os.scandir(directory) as children:
            for child in children:
                check()
                count += 1
                if count > entry_limit:
                    _reject('inventory-limit')
                relative = f'{parent}/{child.name}' if parent else child.name
                logical = prefix + '/' + relative
                path = Path(child.path)
                info = path.lstat()
                if relative in expected:
                    observed.add(relative)
                if is_link_or_reparse(path):
                    preserved.append(dict(path=logical, reason='linked-object'))
                elif stat.S_ISDIR(info.st_mode):
                    if relative not in allowed_directories:
                        preserved.append(dict(path=logical, reason='unknown-directory'))
                    else:
                        directory_rows.append(dict(path=logical, identity=publication._identity(info)))
                        walk(path, relative)
                elif relative == 'manifest.json':
                    continue
                elif stat.S_ISREG(info.st_mode):
                    observed.add(relative)
                    declared = expected.get(relative)
                    if declared is None:
                        preserved.append(dict(path=logical, reason='unknown-file'))
                    elif info.st_nlink != 1:
                        preserved.append(dict(path=logical, reason='linked-object'))
                    else:
                        actual = _stamp(path)
                        if (actual['bytes'], actual['sha256']) != (declared['bytes'], declared['sha256']):
                            preserved.append(dict(path=logical, reason='modified-file'))
                        else:
                            entries.append(dict(actual, path=logical))
                else:
                    preserved.append(dict(path=logical, reason='non-regular-object'))

    walk(version)
    missing = sorted(prefix + '/' + name for name in expected.keys() - observed)
    if not preserved:
        entries.append(dict(manifest_stamp, path=prefix + '/manifest.json'))
    proposal, affected, removed = _catalog_proposal(snapshot, ref, cancelled)
    check()
    if _stamp(manifest_path, MANIFEST_LIMIT) != manifest_stamp:
        _reject('manifest-changed')
    for row in directory_rows:
        if _directory_identity(files._target(root, row['path'])) != row['identity']:
            _reject('directory-changed')
    snapshot.assert_current()
    entries.sort(key=lambda item: item['path'])
    result = dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
        manifestSha256=ref.manifest_sha256, rootIdentity=root_identity,
        versionIdentity=directory_rows[2]['identity'], manifestStamp=manifest_stamp,
        manifestBase64=base64.b64encode(raw).decode('ascii'),
        directoryIdentities=sorted(directory_rows, key=lambda item: item['path']),
        catalogStamp=snapshot.catalog_stamp, proposedCatalog=proposal,
        entries=entries, preserved=sorted(preserved, key=lambda item: item['path']), missing=missing,
        affectedContexts=affected, removedDisplays=removed, fileCount=len(entries),
        bytes=sum(item['bytes'] for item in entries))
    if len(_encoded(result)) > LIMIT - 8192:
        _reject('plan-too-large')
    return result


def inspect_saved_version_removal(resolver, package_id, resource_version, manifest_sha256, *, cancelled=None):
    """Read the actual saved version and return a confirmation-bound plan."""
    with locks.resource_read_lock(resolver.root, wait=True, cancelled=cancelled):
        return _inspect_locked(resolver, package_id, resource_version, manifest_sha256, cancelled)


def _assert_directories(root, plan):
    if _directory_identity(root) != plan['rootIdentity']:
        _reject('root-changed')
    for row in plan['directoryIdentities']:
        if _directory_identity(files._target(root, row['path'])) != row['identity']:
            _reject('directory-changed')


def _assert_retained_source_state(root, plan):
    """Refuse late new objects before the one catalog commit, without touching them."""
    _assert_directories(root, plan)
    known_directories = {row['path'] for row in plan['directoryIdentities']}
    preserved = {row['path'] for row in plan['preserved']}
    prefix = f"packages/{plan['packageId']}/{plan['resourceVersion']}"
    if plan['preserved']:
        preserved.add(prefix + '/manifest.json')

    def walk(directory, relative):
        with os.scandir(directory) as children:
            for child in children:
                logical = relative + '/' + child.name
                if logical in preserved:
                    continue
                path = Path(child.path)
                if logical not in known_directories or is_link_or_reparse(path):
                    _reject('source-changed-before-commit')
                walk(path, logical)

    walk(files._target(root, prefix), prefix)


def _validate_plan(plan):
    if not isinstance(plan, dict):
        _reject('invalid-plan')
    try:
        ref = _version_ref(plan['packageId'], plan['resourceVersion'], plan['manifestSha256'])
        raw = base64.b64decode(plan['manifestBase64'], validate=True)
        if len(raw) > MANIFEST_LIMIT:
            _reject('invalid-manifest')
        _components, inventory = _manifest(raw, ref)
        prefix = f'packages/{ref.package_id}/{ref.resource_version}'
        expected = {prefix + f'/data/{component}/{name}': row
                    for (component, name), row in inventory.items()}
        manifest = plan['manifestStamp']
        catalog_tx._object(manifest, 'manifest.json')
        if (manifest['bytes'], manifest['sha256']) != (len(raw), hashlib.sha256(raw).hexdigest()):
            _reject('invalid-manifest')
        expected[prefix + '/manifest.json'] = manifest
        publication._valid_identity(plan['rootIdentity'])
        publication._valid_identity(plan['versionIdentity'])
        seen = set()
        for row in plan['entries']:
            if not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256', 'identity'}:
                _reject('invalid-entry')
            declared = expected.get(row['path'])
            if (declared is None or row['path'] in seen
                    or (row['bytes'], row['sha256']) != (declared['bytes'], declared['sha256'])):
                _reject('entry-not-owned')
            publication._valid_identity(row['identity'])
            seen.add(row['path'])
        if plan['fileCount'] != len(seen) or plan['bytes'] != sum(row['bytes'] for row in plan['entries']):
            _reject('invalid-plan')
        if plan['preserved'] and prefix + '/manifest.json' in seen:
            _reject('manifest-provenance-required')
        for row in plan['directoryIdentities']:
            if (not isinstance(row, dict) or set(row) != {'path', 'identity'}
                    or row['path'] not in ('packages', f'packages/{ref.package_id}', prefix)
                    and not row['path'].startswith(prefix + '/')):
                _reject('invalid-directory')
            if files._relative(row['path']) != row['path']:
                _reject('invalid-directory')
            publication._valid_identity(row['identity'])
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ResourceCatalogError):
            raise
        raise ResourceCatalogError('resource-removal-invalid-plan') from error


def _load_pending(root):
    marker = files._target(root, PENDING_REMOVAL)
    pointer = files._json(catalog_tx._read(marker)[0])
    if (not isinstance(pointer, dict) or set(pointer) != {'schema', 'transactionId', 'journal'}
            or pointer['schema'] != SCHEMA):
        _reject('invalid-pending')
    transaction = publication._transaction_id(pointer['transactionId'])
    directory = files._target(root, 'transactions/' + transaction)
    catalog_tx._object(pointer['journal'], 'removal-journal.json')
    raw, stamp = _read_removal_metadata(directory / 'removal-journal.json')
    if stamp != pointer['journal']:
        _reject('journal-changed')
    journal = files._json(raw)
    if (not isinstance(journal, dict)
            or set(journal) != {'schema', 'transactionId', 'directoryIdentity', 'objectsIdentity',
                                'plan', 'catalogJournal'}
            or journal['schema'] != SCHEMA or journal['transactionId'] != transaction):
        _reject('invalid-journal')
    _validate_plan(journal['plan'])
    if (_directory_identity(root) != journal['plan']['rootIdentity']
            or _directory_identity(directory) != journal['directoryIdentity']
            or _directory_identity(directory / 'removal-objects') != journal['objectsIdentity']):
        _reject('directory-changed')
    outcome = catalog_tx.retained_catalog_outcome_locked(root, transaction,
        expected_journal_stamp=journal['catalogJournal'])
    pin = directory / 'removal-pointer.json'
    if catalog_tx._read(pin)[0] != catalog_tx._encoded(pointer):
        _reject('pending-changed')
    publication._metadata_publication_identity(pin, marker, outcome['publicationMode'])
    return directory, journal, outcome


def _clear_pending(root, directory):
    actual_directory, journal, outcome = _load_pending(root)
    if actual_directory != directory:
        _reject('pending-changed')
    objects = directory / 'removal-objects'
    if any(objects.iterdir()):
        _reject('unknown-retained-object')
    marker = files._target(root, PENDING_REMOVAL)
    identity = publication._metadata_publication_identity(directory / 'removal-pointer.json',
        marker, outcome['publicationMode'])
    if not files._rollback_published(marker, tuple(identity), _stamp(marker)['sha256']):
        _reject('pending-changed')
    publication._directory_sync(marker.parent)


def _remove_empty_directory(root, row, plan):
    """Delete an empty original directory through one Windows DELETE handle.

    FileDispositionInfo rejects a nonempty directory in the kernel. There is no
    pathname rmdir fallback and no recursive deletion, even for private state.
    """
    prefix = f"packages/{plan['packageId']}/{plan['resourceVersion']}"
    relative = row['path']
    if relative != prefix and not relative.startswith(prefix + '/'):
        _reject('directory-not-owned')
    if os.name != 'nt':
        return 'empty-directory-platform-unsupported'
    import ctypes
    from ctypes import wintypes
    import msvcrt
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
        ctypes.c_void_p, wintypes.DWORD]
    api.SetFileInformationByHandle.restype = wintypes.BOOL
    native = None
    try:
        path = files._target(root, relative)
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        if _directory_identity(root) != plan['rootIdentity']:
            return 'directory-root-changed'
        # Bind every still-existing ancestor to the reviewed namespace. This
        # also refuses a foreign version directory that contains old children.
        for parent in plan['directoryIdentities']:
            if relative == parent['path'] or relative.startswith(parent['path'] + '/'):
                if _directory_identity(files._target(root, parent['path'])) != parent['identity']:
                    return 'directory-changed'
        native = api.CreateFileW(str(path), 0x80010000, 1, None, 3, 0x02200000, None)
        if native == ctypes.c_void_p(-1).value:
            native = None
            return 'directory-not-empty-or-unavailable'
        descriptor = msvcrt.open_osfhandle(int(native), os.O_RDONLY | os.O_BINARY)
        native = None
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISDIR(info.st_mode) or publication._identity(info) != row['identity']
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                return 'directory-changed'
            files._no_links(path)
            if _directory_identity(path) != row['identity']:
                return 'directory-changed'
            disposition = ctypes.c_ubyte(1)
            if not api.SetFileInformationByHandle(msvcrt.get_osfhandle(descriptor), 4,
                                                ctypes.byref(disposition), ctypes.sizeof(disposition)):
                return 'directory-not-empty-or-unavailable'
        finally:
            os.close(descriptor)
        publication._directory_sync(path.parent)
        return None
    except (OSError, files.ImportRejected):
        return 'directory-changed-or-unavailable'
    finally:
        if native is not None:
            api.CloseHandle(native)


def _prune_version_directories(root, plan):
    if plan['preserved']:
        return []  # Keep the original manifest and all provenance namespaces.
    prefix = f"packages/{plan['packageId']}/{plan['resourceVersion']}"
    preserved = []
    rows = [row for row in plan['directoryIdentities']
            if row['path'] == prefix or row['path'].startswith(prefix + '/')]
    for row in sorted(rows, key=lambda item: len(item['path'].split('/')), reverse=True):
        reason = _remove_empty_directory(root, row, plan)
        if reason is not None:
            preserved.append(dict(path=row['path'], reason=reason))
    return preserved


def _finish_locked(root, *, on_stage=None):
    directory, journal, outcome = _load_pending(root)
    # Recover only this removal's catalog transaction, never an unrelated one.
    if files._target(root, PENDING_CATALOG).exists():
        pointer = files._json(catalog_tx._read(files._target(root, PENDING_CATALOG))[0])
        if pointer.get('transactionId') != directory.name or pointer.get('journal') != journal['catalogJournal']:
            _reject('catalog-pending-changed')
        catalog_tx.recover_catalog_locked(root)
        directory, journal, outcome = _load_pending(root)
    plan, objects = journal['plan'], directory / 'removal-objects'
    expected_names = {str(index) for index in range(len(plan['entries']))}
    if any(item.name not in expected_names for item in objects.iterdir()):
        _reject('unknown-retained-object')
    if not outcome['committed']:
        _assert_directories(root, plan)
        # Validate the whole recovery before restoring any object. A newly
        # created target is never overwritten even when its bytes are equal.
        for index, row in enumerate(plan['entries']):
            source, retained = files._target(root, row['path']), objects / str(index)
            if retained.exists():
                _single_file(retained, row)
                if source.exists() or source.is_symlink():
                    _reject('restore-conflict')
            else:
                _single_file(source, row)
        for index, row in enumerate(plan['entries']):
            retained = objects / str(index)
            if retained.exists():
                publication._move_owned(retained, files._target(root, row['path']), row, row['identity'],
                    linkless=True, before_move=lambda: _assert_directories(root, plan))
                if on_stage is not None:
                    on_stage('file-restored')
        status = 'removal-rolled-back'
    else:
        # Committed recovery owns quarantine only. Later objects or empty
        # directories at the old resource paths are never revalidated/pruned.
        for index, row in enumerate(plan['entries']):
            retained = objects / str(index)
            if retained.exists():
                _single_file(retained, row)
        if on_stage is not None:
            on_stage('before-cleanup')
        for index, row in enumerate(plan['entries']):
            retained = objects / str(index)
            if retained.exists():
                _single_file(retained, row)
                if not files._rollback_published(retained, tuple(row['identity']), row['sha256']):
                    _reject('cleanup-required')
                publication._directory_sync(objects)
                if on_stage is not None:
                    on_stage('file-cleaned')
        late_preserved = _prune_version_directories(root, plan)
        status = 'version-partially-removed' if plan['preserved'] or late_preserved else 'version-removed'
    _clear_pending(root, directory)
    return dict(status=status, packageId=plan['packageId'], resourceVersion=plan['resourceVersion'],
        removedFiles=len(plan['entries']) if outcome['committed'] else 0,
        removedBytes=plan['bytes'] if outcome['committed'] else 0,
        preserved=deepcopy(plan['preserved']) + (late_preserved if outcome['committed'] else []), missing=list(plan['missing']),
        affectedContexts=list(plan['affectedContexts']), removedDisplays=deepcopy(plan['removedDisplays']),
        transactionId=directory.name, recoveryRequired=False,
        generation=files._json(catalog_tx._read(root / 'catalog.json')[0])['generation']
            if (root / 'catalog.json').exists() else None)


def recover_saved_version_removal_locked(resource_root, *, on_stage=None):
    """Recover under the caller's write lock, before ordinary catalog recovery."""
    root = locks._safe(Path(resource_root).absolute())
    if not removal_recovery_required(root):
        return dict(status='removal-ready', recoveryRequired=False)
    return _finish_locked(root, on_stage=on_stage)


def remove_saved_version(resolver, plan, *, validate_selection, cancelled=None, on_stage=None):
    """Execute a reviewed plan; the caller already owns the Session idle lease.

    validate_selection receives the prospective document twice. Like ordinary
    catalog activation it must use prospective readers, not resolver.snapshot()
    while the pending marker blocks runtime readers. Cancellation after the
    catalog commit cannot reverse deletion. Recovery never adopts replacements.
    """
    if not callable(validate_selection):
        raise TypeError('removal requires a semantic selection validator')
    plan = deepcopy(plan)
    _validate_plan(plan)
    with locks.resource_write_lock(resolver.root, wait=True, cancelled=cancelled) as root:
        actual = _inspect_locked(resolver, plan['packageId'], plan['resourceVersion'],
                                  plan['manifestSha256'], cancelled)
        if actual != plan:
            _reject('plan-changed-reinspect-required')
        document = deepcopy(plan['proposedCatalog'])
        document['generation'] = str(uuid.uuid4())
        # Check actual filesystem capabilities before any resource mutation.
        locks.preflight_resource_publication(root, [row['path'] for row in plan['entries']] + [PENDING_REMOVAL])
        prepared = False

        def validate(value):
            validate_selection(value)
            if prepared:
                _assert_retained_source_state(root, plan)

        def stage(name):
            nonlocal prepared
            if name == 'pending':
                pending = files._json(catalog_tx._read(files._target(root, PENDING_CATALOG))[0])
                directory = files._target(root, 'transactions/' + pending['transactionId'])
                objects = directory / 'removal-objects'
                objects.mkdir()
                journal = dict(schema=SCHEMA, transactionId=directory.name,
                    directoryIdentity=_directory_identity(directory), objectsIdentity=_directory_identity(objects),
                    plan=plan, catalogJournal=pending['journal'])
                journal_stamp = _write_removal_metadata(directory / 'removal-journal.json', _encoded(journal))
                pointer = dict(schema=SCHEMA, transactionId=directory.name, journal=journal_stamp)
                catalog_tx._write_bound(directory / 'removal-pointer.json', catalog_tx._encoded(pointer))
                outcome = catalog_tx.retained_catalog_outcome_locked(root, directory.name,
                    expected_journal_stamp=pending['journal'])
                publication._publish_metadata(directory / 'removal-pointer.json',
                    files._target(root, PENDING_REMOVAL), outcome['publicationMode'],
                    expected_data=catalog_tx._encoded(pointer))
                prepared = True
                if on_stage is not None:
                    on_stage('removal-pending')
                for index, row in enumerate(plan['entries']):
                    files._check_cancelled(cancelled)
                    _assert_directories(root, plan)
                    source = files._target(root, row['path'])
                    _single_file(source, row)
                    publication._move_owned(source, objects / str(index), row, row['identity'], linkless=True,
                        before_move=lambda: (_assert_directories(root, plan), _single_file_metadata(source, row)))
                    if on_stage is not None:
                        on_stage('file-retained')
            if on_stage is not None:
                on_stage(name)

        try:
            catalog_tx.activate_catalog_locked(root, document,
                expected_catalog_stamp=plan['catalogStamp'], validate=validate,
                cancelled=cancelled, on_stage=stage)
        except Exception:
            # A publication can succeed before a sync error is raised. Always
            # read its one durable witness; do not infer outcome from an error.
            if prepared or removal_recovery_required(root):
                result = _finish_locked(root, on_stage=on_stage)
                if result['status'] != 'removal-rolled-back':
                    return result
            raise
        return _finish_locked(root, on_stage=on_stage)
