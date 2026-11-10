"""Read-only inventory of saved imported resource package versions.

This module lists only ``packages/<packageId>/<resourceVersion>``.  It does
not discover staging/transaction state, bundled resources, or legacy installs,
and package metadata never grants recognition authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

import resource_snapshot_io
import star_map_import as files
from author_resource_snapshots import _metadata
from imported_resource_catalog import ResourceCatalogError, ResourceResolver
from imported_resource_packages import ComponentRef, PackageView, MANIFEST_LIMIT


class ImportedResourceInventoryError(RuntimeError):
    """A stable, caller-safe inventory failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class InventorySnapshot:
    generation: str | None
    rows: tuple[dict, ...]
    catalog_issue: str | None


class _Budget:
    def __init__(self, cancelled):
        self.cancelled = cancelled
        self.used = 0

    def check(self):
        if self.cancelled is not None and self.cancelled():
            raise ImportedResourceInventoryError('resource-inventory-cancelled')

    def consume(self, amount=1):
        self.check()
        self.used += amount
        if self.used > files.MAX_FILES:
            raise ImportedResourceInventoryError('resource-inventory-limit')


def _stable_code(error, fallback):
    code = getattr(error, 'code', None)
    if isinstance(code, str) and code and all(ch.islower() or ch.isdigit() or ch == '-' for ch in code):
        return code[:96]
    return fallback


def _safe_segment(value):
    """Return whether a physical directory name is a package path segment."""
    try:
        ComponentRef(value, 'inventory-probe', 'inventory-probe', '0' * 64)
    except (TypeError, ValueError):
        return False
    return True


def _display_segment(value):
    if _safe_segment(value):
        return value
    return 'invalid-' + hashlib.sha256(os.fsencode(value)).hexdigest()[:16]


def _row_key(package_id, version):
    return f'{package_id}/{version}' if version else f'{package_id}/!unreadable'


def _base_row(package_id, version):
    return dict(
        key=_row_key(package_id, version),
        packageId=package_id,
        resourceVersion=version,
        manifestSha256=None,
        title=package_id,
        displayName=None,
        components=[],
        status='damaged',
        selectedContexts=[],
        issue=None,
        canRemove=False,
        canInspectRemoval=False,
        storageFormat='',
    )


def _damaged(package_id, version, issue):
    row = _base_row(package_id, version)
    row['issue'] = issue
    return row


def _catalog_token(root):
    """A bounded observation for invalid/pending catalogs with no snapshot API."""
    root = Path(root).absolute()
    files._no_links(root)
    if not root.exists():
        return (None, None)

    def signature(path):
        try:
            files._no_links(path)
        except files.ImportRejected:
            # A pending marker or catalog link is itself an observed damaged
            # state. Never open it just to make the observation token.
            for item in (path, *path.parents):
                try:
                    linked = files.is_link_or_reparse(item)
                except OSError:
                    continue
                if linked:
                    try:
                        info = item.lstat()
                        identity = (info.st_dev, info.st_ino, info.st_mode,
                                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    except OSError:
                        identity = None
                    return ('linked', item.name, identity)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns)

    pending = signature(root / 'transactions' / 'pending-catalog.json')
    catalog_path = root / 'catalog.json'
    catalog_signature = signature(catalog_path)
    if catalog_signature is None:
        catalog_content = None
    else:
        try:
            _raw, stamp = _metadata(catalog_path)
            catalog_content = (stamp['sha256'], tuple(stamp['identity']))
        except (OSError, ValueError):
            # Keep a stable observation of malformed objects without following
            # links or trying to interpret their contents.
            catalog_content = ('unreadable', catalog_signature)
    return pending, catalog_content


def _catalog_state(resolver, token_before):
    root = Path(resolver.root).absolute()
    if token_before[0] is not None:
        return None, 'resource-catalog-recovery-required'
    try:
        snapshot = resolver.snapshot()
    except (ResourceCatalogError, files.ImportRejected, OSError, ValueError) as error:
        return None, _stable_code(error, 'resource-catalog-invalid')
    return snapshot, None


def _snapshot_catalog_token(snapshot):
    stamp = snapshot.catalog_stamp
    if stamp is None:
        return None, None
    return None, (stamp['sha256'], tuple(stamp['identity']))


def _catalog_failure_code(error, root, token_before):
    """Hide filesystem paths and distinguish a changed snapshot from I/O."""
    if isinstance(error, ResourceCatalogError) and error.code == 'resource-catalog-superseded':
        return 'resource-catalog-superseded'
    try:
        changed = _catalog_token(root) != token_before
    except (OSError, files.ImportRejected):
        changed = True
    if changed:
        return 'resource-catalog-superseded'
    if isinstance(error, OSError):
        return 'resource-catalog-unreadable'
    return _stable_code(error, 'resource-catalog-invalid')


def _assert_observed_manifests_current(root, rows, budget):
    """Recheck only the bounded manifest objects whose rows we observed."""
    for row in rows:
        observation = row.get('_manifestObservation')
        if observation is None:
            continue
        budget.check()
        try:
            path = files._target(
                root, 'packages/' + row['packageId'] + '/' + row['resourceVersion'] + '/manifest.json')
            _raw, stamp = resource_snapshot_io.read_stable_file(
                path, MANIFEST_LIMIT, no_links=files._no_links,
                reject=_manifest_reject)
        except (OSError, files.ImportRejected) as error:
            raise ImportedResourceInventoryError('resource-inventory-superseded') from error
        if (stamp['sha256'] != observation['sha256']
                or tuple(stamp['identity']) != observation['identity']):
            raise ImportedResourceInventoryError('resource-inventory-superseded')


def _entrypoint_check(root, package_id, version, component):
    relative = ('packages/' + package_id + '/' + version + '/data/'
                + component['componentId'] + '/' + component['entrypoint'])
    target = files._target(root, relative)
    try:
        info = target.lstat()
    except OSError as error:
        raise files.ImportRejected('resource-package-entrypoint-unavailable') from error
    if not stat.S_ISREG(info.st_mode):
        raise files.ImportRejected('resource-package-entrypoint-not-regular')


def _manifest_reject(reason):
    raise files.ImportRejected('resource-package-manifest-' + reason)


def _read_version(root, package_id, version, *, verify, budget):
    row = _base_row(package_id, version)
    package_path = files._target(root, 'packages/' + package_id + '/' + version)
    if not package_path.is_dir():
        row['issue'] = 'resource-package-version-unavailable'
        return row
    manifest_path = files._target(package_path, 'manifest.json')
    try:
        raw, stamp = resource_snapshot_io.read_stable_file(
            manifest_path, MANIFEST_LIMIT, no_links=files._no_links,
            reject=_manifest_reject)
    except OSError as error:
        row['issue'] = 'resource-package-manifest-unavailable'
        return row
    try:
        manifest_sha = hashlib.sha256(raw).hexdigest()
        row['_manifestObservation'] = dict(
            sha256=manifest_sha, identity=tuple(stamp['identity']))
        manifest = files._json(raw)
        if not isinstance(manifest, dict):
            raise files.ImportRejected('resource-package-invalid-manifest')
        rows = manifest.get('components')
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise files.ImportRejected('resource-package-invalid-components')
        first_id = rows[0].get('componentId')
        primary = ComponentRef(package_id, version, first_id, manifest_sha)
        budget.check()
        components, _inventory = PackageView._validate_manifest(manifest, primary)
        budget.check()
        refs = {
            component_id: ComponentRef(package_id, version, component_id, manifest_sha)
            for component_id in components
        }
        if verify:
            budget.check()
            PackageView(root, refs[first_id]).validate_inventory()
            budget.check()
        else:
            for component in components.values():
                budget.check()
                _entrypoint_check(root, package_id, version, component)
        row.update(
            manifestSha256=manifest_sha,
            displayName=manifest.get('displayName'),
            title=manifest.get('displayName') or package_id,
            components=[dict(componentId=item['componentId'], type=item['type'],
                             contexts=[f"{context['season']}:{context['mode']}"
                                       for context in item['contexts']])
                        for item in components.values()],
            status='saved' if verify else 'saved-unverified',
            _componentRefs=tuple(refs.values()),
        )
        return row
    except (files.ImportRejected, OSError, ValueError, TypeError, KeyError) as error:
        row['issue'] = _stable_code(error, 'resource-package-invalid-manifest')
        return row


def _catalog_references(snapshot, budget):
    expected = {}
    if snapshot is None:
        return expected
    references = 0
    for context_row in snapshot.contexts.values():
        for ref in context_row.values():
            budget.check()
            if ref is not None:
                references += 1
                if references > files.MAX_FILES:
                    raise ImportedResourceInventoryError('resource-inventory-catalog-limit')
                expected.setdefault((ref.package_id, ref.resource_version), []).append(ref)
    for ref in snapshot.displays:
        budget.check()
        references += 1
        if references > files.MAX_FILES:
            raise ImportedResourceInventoryError('resource-inventory-catalog-limit')
        expected.setdefault((ref.package_id, ref.resource_version), []).append(ref)
    return expected


def _selected_contexts(snapshot, budget):
    selected = {}
    if snapshot is None:
        return selected
    for season in ('s0', 's1'):
        for mode in ('difficult', 'nightmare'):
            budget.check()
            context_key = f'{season}:{mode}'
            context = snapshot.resolve_context(season, mode)
            views = [context.base, context.controls, context.groups, *context.displays]
            for view in views:
                if view is None:
                    continue
                ref = view.component_ref
                key = (ref.package_id, ref.resource_version, ref.manifest_sha256)
                selected.setdefault(key, set()).add(context_key)
            budget.check()
    return selected


def _scan_packages(root, *, verify, budget):
    rows = []
    packages_path = files._target(root, 'packages')
    try:
        info = packages_path.lstat()
    except FileNotFoundError:
        return rows
    except OSError as error:
        raise ImportedResourceInventoryError('resource-package-inventory-unreadable') from error
    if not stat.S_ISDIR(info.st_mode):
        raise ImportedResourceInventoryError('resource-package-inventory-invalid')

    try:
        with os.scandir(packages_path) as package_entries:
            for package_entry in package_entries:
                budget.consume()
                package_folder = _display_segment(package_entry.name)
                try:
                    files._no_links(Path(package_entry.path))
                    package_info = package_entry.stat(follow_symlinks=False)
                except (OSError, files.ImportRejected):
                    rows.append(_damaged(package_folder, None,
                                         'resource-inventory-linked-or-unreadable-object'))
                    continue
                if not stat.S_ISDIR(package_info.st_mode):
                    rows.append(_damaged(package_folder, None, 'resource-inventory-unexpected-object'))
                    continue
                if not _safe_segment(package_entry.name):
                    rows.append(_damaged(package_folder, None, 'resource-package-invalid-package-id'))
                    continue

                package_path = Path(package_entry.path)
                try:
                    version_entries = os.scandir(package_path)
                except OSError:
                    rows.append(_damaged(package_folder, None,
                                         'resource-package-inventory-unreadable'))
                    continue
                with version_entries:
                    for version_entry in version_entries:
                        budget.consume()
                        version_folder = _display_segment(version_entry.name)
                        try:
                            files._no_links(Path(version_entry.path))
                            version_info = version_entry.stat(follow_symlinks=False)
                        except (OSError, files.ImportRejected):
                            rows.append(_damaged(package_folder, version_folder,
                                                 'resource-inventory-linked-or-unreadable-object'))
                            continue
                        if not stat.S_ISDIR(version_info.st_mode):
                            rows.append(_damaged(package_folder, version_folder,
                                                 'resource-inventory-unexpected-object'))
                            continue
                        if not _safe_segment(version_entry.name):
                            rows.append(_damaged(package_folder, version_folder,
                                                 'resource-package-invalid-resource-version'))
                            continue
                        budget.check()
                        try:
                            row = _read_version(root, package_entry.name, version_entry.name,
                                                verify=verify, budget=budget)
                        except (OSError, files.ImportRejected) as error:
                            row = _damaged(package_folder, version_folder,
                                           _stable_code(error, 'resource-package-unreadable'))
                        rows.append(row)
    except ImportedResourceInventoryError:
        raise
    except OSError as error:
        raise ImportedResourceInventoryError('resource-package-inventory-unreadable') from error
    return rows


def list_saved_versions(resolver: ResourceResolver, *, verify=False, cancelled=None) -> InventorySnapshot:
    """List saved immutable versions without changing or activating any files.

    ``verify=False`` reads each bounded manifest and checks that every declared
    component entrypoint is a regular file. ``verify=True`` additionally runs
    one full ``PackageView.validate_inventory`` pass per valid version.
    """
    if not isinstance(resolver, ResourceResolver):
        raise TypeError('resolver must be a ResourceResolver')
    if type(verify) is not bool:
        raise TypeError('verify must be bool')
    root = Path(resolver.root).absolute()
    budget = _Budget(cancelled)
    budget.check()
    try:
        files._no_links(root)
        if root.exists() and not root.is_dir():
            raise ImportedResourceInventoryError('resource-root-not-directory')
        token_before = _catalog_token(root)
    except ImportedResourceInventoryError:
        raise
    except (OSError, files.ImportRejected) as error:
        raise ImportedResourceInventoryError('resource-root-unreadable') from error

    snapshot, catalog_issue = _catalog_state(resolver, token_before)
    if snapshot is not None:
        try:
            # Anchor subsequent race checks to the exact CatalogSnapshot that
            # will resolve roles, rather than an earlier pre-open observation.
            token_before = _snapshot_catalog_token(snapshot)
        except (ResourceCatalogError, files.ImportRejected, OSError) as error:
            raise ImportedResourceInventoryError(
                _catalog_failure_code(error, root, token_before)) from error
    generation = snapshot.generation if snapshot is not None else None
    try:
        rows = _scan_packages(root, verify=verify, budget=budget) if root.exists() else []
    except ImportedResourceInventoryError:
        raise
    except (OSError, files.ImportRejected) as error:
        raise ImportedResourceInventoryError(_stable_code(error, 'resource-package-inventory-unreadable')) from error

    expected = _catalog_references(snapshot, budget)
    try:
        selected = _selected_contexts(snapshot, budget)
    except (ResourceCatalogError, files.ImportRejected, OSError) as error:
        raise ImportedResourceInventoryError(
            _catalog_failure_code(error, root, token_before)) from error
    for row in rows:
        budget.check()
        package_key = (row['packageId'], row['resourceVersion'])
        digest = row['manifestSha256']
        if digest:
            stale = any(ref.manifest_sha256 != digest for ref in expected.get(package_key, ()))
            if stale:
                row['status'] = 'damaged'
                row['issue'] = 'resource-package-manifest-changed'
                row['selectedContexts'] = []
            elif row['status'] != 'damaged':
                contexts = set()
                for ref in row.pop('_componentRefs', ()):
                    contexts.update(selected.get((ref.package_id, ref.resource_version,
                                                  ref.manifest_sha256), ()))
                row['selectedContexts'] = sorted(contexts)
                if contexts:
                    row['status'] = 'selected'
        row.pop('_componentRefs', None)

    budget.check()
    _assert_observed_manifests_current(root, rows, budget)
    if snapshot is not None:
        try:
            snapshot.assert_current()
        except (ResourceCatalogError, files.ImportRejected, OSError) as error:
            raise ImportedResourceInventoryError(
                _catalog_failure_code(error, root, token_before)) from error
    else:
        try:
            token_after = _catalog_token(root)
        except (OSError, files.ImportRejected) as error:
            raise ImportedResourceInventoryError('resource-catalog-superseded') from error
        if token_after != token_before:
            raise ImportedResourceInventoryError('resource-catalog-superseded')
    for row in rows:
        row.pop('_manifestObservation', None)
    rows.sort(key=lambda row: row['key'])
    return InventorySnapshot(generation, tuple(rows), catalog_issue)

