"""Copy a legacy store into an absent installation-local resource directory.

The caller owns the program/Session idle lease. Resource package formats and
selection stay unchanged. A private sibling is published once, on the target
volume; interrupted, unverified siblings are never adopted as resources.
Old-store deletion is a separate, explicitly requested per-version operation.
"""
import os
from pathlib import Path
import stat
import uuid

from imported_resource_catalog import (ResourceCatalogError, ResourceResolver,
                                       assert_catalog_ready)
from imported_resource_inventory import ImportedResourceInventoryError, list_saved_versions
from imported_resource_packages import ComponentRef, PackageView
from imported_resource_selection import inspect_selection
import imported_resource_removal as removal
import map_package_upgrade as publication
import resource_transaction as locks
from resource_snapshot_io import read_stable_file
import star_map_import as files


class ResourceMigrationError(ResourceCatalogError):
    def __init__(self, reason, **details):
        super().__init__('resource-migration-' + reason)
        self.details = details


def _reject(reason):
    raise ResourceMigrationError(reason)


def _check(cancelled):
    files._check_cancelled(cancelled)


def _paths(source, target, *, absent):
    source, target = (Path(os.path.abspath(path)) for path in (source, target))
    for path in (source, target):
        files._no_links(path)
    if not source.is_dir():
        _reject('source-unavailable')
    if source.is_relative_to(target) or target.is_relative_to(source):
        _reject('overlapping-directories')
    if not target.parent.is_dir():
        _reject('target-parent-unavailable')
    if absent and target.exists():
        _reject('target-exists')
    if not absent and not target.is_dir():
        _reject('target-unavailable')
    return source, target


def _resolver(root, capabilities, registries):
    return ResourceResolver(root, supported_capabilities=capabilities,
                            project_registry_refs=registries)


def _ready(root):
    locks.assert_resources_ready(root)
    assert_catalog_ready(root)


def _read(path, *, expected=None, allow_hardlinks=False):
    raw, stamp = read_stable_file(path, files.MAX_FILE_BYTES,
        no_links=files._no_links, reject=_reject, identity=publication._identity)
    if not allow_hardlinks and path.lstat().st_nlink != 1:
        _reject('linked-file')
    if expected is not None and any(stamp[key] != expected[key]
                                   for key in ('bytes', 'sha256', 'identity')):
        _reject('source-changed')
    return raw, stamp


def _snapshot(root, capabilities, registries, cancelled):
    """Return only complete saved packages and their exact existing catalog."""
    _check(cancelled)
    _ready(root)
    resolver = _resolver(root, capabilities, registries)
    inventory = list_saved_versions(resolver, verify=True, cancelled=cancelled)
    if inventory.catalog_issue:
        raise ResourceCatalogError(inventory.catalog_issue)
    entries, versions = {}, []
    for row in inventory.rows:
        _check(cancelled)
        if row['status'] == 'damaged':
            raise ResourceMigrationError('source-version-invalid',
                packageId=row['packageId'], resourceVersion=row['resourceVersion'],
                causeCode=row['issue'])
        ref = ComponentRef(row['packageId'], row['resourceVersion'],
                           row['components'][0]['componentId'], row['manifestSha256'])
        view = PackageView(root, ref)
        prefix = f'packages/{ref.package_id}/{ref.resource_version}'
        relative_names = [prefix + '/manifest.json']
        relative_names += [prefix + '/data/' + item['componentId'] + '/' + item['path']
                           for item in view.manifest['files']]
        size = 0
        for relative in relative_names:
            _check(cancelled)
            _raw, stamp = _read(files._target(root, relative))
            entries[relative] = stamp
            size += stamp['bytes']
        versions.append(dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
                             manifestSha256=ref.manifest_sha256, bytes=size))
    catalog = files._target(root, 'catalog.json')
    if catalog.exists():
        # The existing catalog transaction intentionally hard-links its commit
        # witness to catalog.json. Copying its stable bytes does not claim or
        # delete those aliases; the new store gets a standalone catalog file.
        raw, stamp = _read(catalog, allow_hardlinks=True)
        inspect_selection(root, files._json(raw), supported_capabilities=capabilities,
            project_registry_refs=registries, cancelled=cancelled)
        entries['catalog.json'] = stamp
    # Transient locks, retained transaction provenance and unknown user objects
    # are not package data. They stay at the old location, without recursion.
    preserved = []
    for child in root.iterdir():
        files._no_links(child)
        if child.name not in {'packages', 'catalog.json'}:
            preserved.append(dict(path=child.name, reason='outside-saved-resources'))
    _ready(root)
    return dict(entries=entries, versions=sorted(versions,
        key=lambda row: (row['packageId'], row['resourceVersion'])),
        preserved=sorted(preserved, key=lambda row: row['path']),
        rootIdentity=publication._identity(root.stat()))


def _summary(source, target, snapshot):
    return dict(source=str(source), target=str(target), versions=snapshot['versions'],
                totalBytes=sum(row['bytes'] for row in snapshot['entries'].values()),
                preserved=snapshot['preserved'], sourceRetained=True)


def inspect_migration(source, target, *, supported_capabilities=(), project_registry_refs=(),
                      cancelled=None):
    """Inspect a stable old store; never create or merge the target directory."""
    source, target = _paths(source, target, absent=True)
    with locks.resource_write_lock(source, cancelled=cancelled):
        snapshot = _snapshot(source, supported_capabilities, project_registry_refs, cancelled)
        _paths(source, target, absent=True)
        return dict(status='ready', **_summary(source, target, snapshot))


class _OwnedStage:
    def __init__(self, target):
        self.path = target.parent / ('.' + target.name + '.migration-' + uuid.uuid4().hex)
        self.path.mkdir()
        self.identity = publication._identity(self.path.stat())
        self.directories = {'': self.identity}
        self.entries = {}

    def write(self, relative, raw):
        path = files._target(self.path, relative)
        missing = []
        parent = path.parent
        while parent != self.path:
            if not parent.exists():
                missing.append(parent)
            parent = parent.parent
        for parent in reversed(missing):
            parent.mkdir()
            self.directories[parent.relative_to(self.path).as_posix()] = publication._identity(parent.stat())
        # Capture identity from the exclusive create handle, including a partial
        # write. If writing fails, only exactly observed partial bytes are owned.
        with path.open('xb') as handle:
            identity = publication._identity(os.fstat(handle.fileno()))
            try:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.flush()
                actual, stamp = _read(path)
                if stamp['identity'] == identity:
                    self.entries[relative] = (stamp, identity)
        if actual != raw:
            _reject('staged-file-changed')

    def verify(self):
        files._no_links(self.path)
        if publication._identity(self.path.stat()) != self.identity:
            _reject('staging-replaced')
        planned, directories = publication._private_tree_plan(self.path, self.entries)
        if {path.relative_to(self.path).as_posix() for path, _, _ in planned} != set(self.entries):
            _reject('staging-incomplete')
        actual = {path.relative_to(self.path).as_posix(): publication._identity(path.stat())
                  for path in directories}
        if actual != {name: identity for name, identity in self.directories.items() if name}:
            _reject('staging-directories-changed')
        return planned, directories

    def cleanup(self):
        def validate_namespace(path):
            relative = path.relative_to(self.path).as_posix()
            for name, identity in self.directories.items():
                if name and relative != name and not relative.startswith(name + '/'):
                    continue
                ancestor = locks._safe(self.path / name)
                info = ancestor.lstat()
                if not stat.S_ISDIR(info.st_mode) or publication._identity(info) != identity:
                    return 'directory-changed'
            return None

        try:
            planned, directories = self.verify()
            for path, row, identity in planned:
                if not files._rollback_published(path, tuple(identity), row['sha256']):
                    return False
            for path in directories:
                identity = self.directories[path.relative_to(self.path).as_posix()]
                if locks.remove_owned_empty_directory(path, identity,
                        validate_namespace=lambda: validate_namespace(path)) is not None:
                    return False
            return locks.remove_owned_empty_directory(self.path, self.identity,
                validate_namespace=lambda: validate_namespace(self.path)) is None
        except (OSError, ValueError):
            return False


def _progress(callback, stage, **values):
    if callback is not None:
        callback(dict(stage=stage, **values))


def migrate_resources(source, target, *, supported_capabilities=(), project_registry_refs=(),
                      cancelled=None, on_progress=None):
    """Copy and atomically publish a complete store, retaining all source data.

    Cancellation is effective before publication. After publication this returns
    ``status=migrated`` even when cancellation or a notification fails. A failed
    pre-publication operation includes an unverified retained staging path in the
    exception's details, rather than deleting or adopting uncertain objects.
    """
    source, target = _paths(source, target, absent=True)
    stage, published = None, False
    with locks.resource_write_lock(source, cancelled=cancelled):
        snapshot = _snapshot(source, supported_capabilities, project_registry_refs, cancelled)
        _paths(source, target, absent=True)
        try:
            stage = _OwnedStage(target)
            copied = 0
            total = sum(row['bytes'] for row in snapshot['entries'].values())
            for relative, expected in sorted(snapshot['entries'].items()):
                _check(cancelled)
                raw, _stamp = _read(files._target(source, relative), expected=expected,
                                    allow_hardlinks=relative == 'catalog.json')
                stage.write(relative, raw)
                copied += len(raw)
                _progress(on_progress, 'copying', path=relative, copiedBytes=copied,
                          totalBytes=total)

            def before_move():
                _check(cancelled)
                stage.verify()
                # Validate the actual copied payloads and unchanged selection,
                # not just the fact that every copy call returned.
                copied_snapshot = _snapshot(stage.path, supported_capabilities,
                                             project_registry_refs, cancelled)
                expected_bytes = {name: (row['bytes'], row['sha256'])
                                  for name, row in snapshot['entries'].items()}
                actual_bytes = {name: (row['bytes'], row['sha256'])
                                for name, row in copied_snapshot['entries'].items()}
                if expected_bytes != actual_bytes:
                    _reject('staging-content-changed')
                current_inventory = list_saved_versions(_resolver(source, supported_capabilities,
                    project_registry_refs), verify=False, cancelled=cancelled)
                version_keys = lambda rows: {(row['packageId'], row['resourceVersion'],
                                              row['manifestSha256']) for row in rows}
                if (current_inventory.catalog_issue
                        or version_keys(current_inventory.rows) != version_keys(snapshot['versions'])):
                    _reject('source-changed')
                for relative, expected in snapshot['entries'].items():
                    _check(cancelled)
                    _read(files._target(source, relative), expected=expected,
                          allow_hardlinks=relative == 'catalog.json')
                if publication._identity(source.stat()) != snapshot['rootIdentity']:
                    _reject('source-replaced')
                _paths(source, target, absent=True)
                _ready(source)

            locks.rename_owned_directory_no_replace(stage.path, target, stage.identity,
                                                    before_move=before_move)
            published = True
        except Exception as error:
            # A native rename may complete before a postcheck reports an error.
            # Never clean the published directory or call that state cancelled.
            if (stage is not None and target.is_dir()
                    and publication._identity(target.stat()) == stage.identity):
                published = True
            if not published:
                retained = stage is not None and not stage.cleanup()
                details = dict(getattr(error, 'details', {}))
                if retained:
                    details.update(stagingPath=str(stage.path), stagingRetained=True)
                if hasattr(error, 'details'):
                    error.details = details
                elif retained:
                    raise ResourceMigrationError('interrupted', **details) from error
                raise
            warning = str(error)
        else:
            warning = ''
        result = dict(status='migrated', migrated=True, **_summary(source, target, snapshot))
        if warning:
            result['warning'] = warning
        try:
            _progress(on_progress, 'migrated', copiedBytes=result['totalBytes'],
                      totalBytes=result['totalBytes'])
        except Exception:
            result['notificationFailed'] = True
        return result


def cleanup_migrated_resources(source, target, *, supported_capabilities=(), project_registry_refs=(),
                               cancelled=None, on_progress=None):
    """Explicitly clean only versions completely present at the target.

    Existing per-version removal transactions detach the old catalog and own
    deletion/recovery. Unknown or damaged versions stay intact. The shared root,
    lock, historical transaction records and user files are always retained.
    """
    source, target = _paths(source, target, absent=False)
    result = dict(status='cleanup-incomplete', source=str(source), target=str(target),
                  removedVersions=[], preserved=[], removedBytes=0, sourceRetained=True)
    resolver = _resolver(source, supported_capabilities, project_registry_refs)

    def preserve(path, reason):
        result['preserved'].append(dict(path=path, reason=reason))

    try:
        # A target resource operation cannot remove our last verified copy while
        # old versions are being deleted. Never acquire a nested source writer:
        # the existing removal API owns and rechecks that lock for each version.
        with locks.resource_read_lock(target, cancelled=cancelled):
            _ready(target)
            target_catalog = target / 'catalog.json'
            if target_catalog.exists():
                raw, _ = _read(target_catalog, allow_hardlinks=True)
                inspect_selection(target, files._json(raw), supported_capabilities=supported_capabilities,
                    project_registry_refs=project_registry_refs, cancelled=cancelled)
            with locks.resource_read_lock(source, cancelled=cancelled):
                _ready(source)
                inventory = list_saved_versions(resolver, verify=False, cancelled=cancelled)
                if inventory.catalog_issue:
                    raise ResourceCatalogError(inventory.catalog_issue)
                outside = []
                for child in source.iterdir():
                    if child.name in {'packages', 'catalog.json', 'work', 'transactions'}:
                        continue
                    files._no_links(child)
                    if child.name == 'staging' and child.is_dir() and not any(child.iterdir()):
                        continue  # Normal empty infrastructure is not unknown user data.
                    outside.append(child.name)
            for name in outside:
                preserve(name, 'outside-saved-resources')
            for row in inventory.rows:
                _check(cancelled)
                relative = 'packages/' + str(row['packageId']) + '/' + str(row['resourceVersion'])
                try:
                    if row['status'] == 'damaged':
                        preserve(relative, row['issue'])
                        continue
                    plan = removal.inspect_saved_version_removal(resolver, row['packageId'],
                        row['resourceVersion'], row['manifestSha256'], cancelled=cancelled)
                    if plan['preserved'] or plan['missing']:
                        preserve(relative, 'source-version-changed-or-unknown')
                        continue
                    ref = ComponentRef(row['packageId'], row['resourceVersion'],
                        row['components'][0]['componentId'], row['manifestSha256'])

                    def target_still_complete():
                        _ready(target)
                        PackageView(target, ref).validate_inventory()

                    target_still_complete()

                    def validate(document):
                        target_still_complete()
                        return inspect_selection(source, document,
                            supported_capabilities=supported_capabilities,
                            project_registry_refs=project_registry_refs, cancelled=cancelled)

                    outcome = removal.remove_saved_version(resolver, plan, validate_selection=validate,
                                                            cancelled=cancelled)
                    result['removedBytes'] += outcome['removedBytes']
                    if outcome['status'] != 'version-removed':
                        preserve(relative, outcome['status'])
                    else:
                        result['removedVersions'].append(dict(packageId=row['packageId'],
                            resourceVersion=row['resourceVersion'], removedBytes=outcome['removedBytes']))
                    try:
                        _progress(on_progress, 'cleaning', packageId=row['packageId'],
                                  resourceVersion=row['resourceVersion'], removedBytes=result['removedBytes'])
                    except Exception:
                        result['notificationFailed'] = True
                except (OSError, ValueError, ImportedResourceInventoryError) as error:
                    preserve(relative, getattr(error, 'code', None) or str(error))
                    # A pending removal needs its existing explicit recovery;
                    # no later version may hide that incomplete transaction.
                    if removal.removal_recovery_required(source):
                        result['recoveryRequired'] = True
                        break
            result['status'] = ('cleanup-incomplete' if result.get('recoveryRequired') else
                                'cleanup-partial' if result['preserved'] else 'cleanup-complete')
    except (OSError, ValueError, ImportedResourceInventoryError) as error:
        preserve('.', getattr(error, 'code', None) or str(error))
        result['cancelled'] = bool(cancelled and cancelled())
    return result
