"""Bounded display declarations, never content verification or deletion authority."""
from pathlib import Path
import json
import re

import star_map_import as files
import map_package_removal as removal
from author_package_management import PACKAGE_NAMES
from resource_transaction import resource_read_lock, assert_resources_ready


def _json(path):
    files._no_links(path)
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('summary-metadata-size-or-type')
    with path.open('rb') as stream:
        payload = stream.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError('summary-metadata-size-or-type')
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError('summary-metadata-object-required')
    return value


def declared_packages(install_root, *, cancelled=None):
    """List receipt/history hints; inspection still validates every selected byte."""
    with resource_read_lock(install_root, wait=True, cancelled=cancelled) as root:
        root = Path(root)
        assert_resources_ready(root)
        known = set(files.package_ids(root))
        history = set()
        for directory in removal._children(root, 'work/map-package-removals'):
            files._check_cancelled(cancelled)
            package = removal._legacy_owner(directory.name)
            if package:
                history.add(package)
        for relative, filename in (('work/map-packages/upgrades', 'journal.json'),
                                   ('work/map-packages', 'addition-journal.json')):
            for directory in removal._children(root, relative):
                files._check_cancelled(cancelled)
                if filename == 'addition-journal.json' and not re.fullmatch(r'staging-[A-Za-z0-9_-]+', directory.name):
                    continue
                path = files._target(directory, filename)
                if not path.is_file():
                    continue
                package = _json(path).get('packageId')
                if files.known_package_id(package):
                    history.add(package)
        result = []
        for package in sorted(known | history):
            files._check_cancelled(cancelled)
            import author_resource_snapshots as snapshots
            if files.is_generic_profile(package) and snapshots.managed(root, package):
                row = dict(packageId=package, title=package, status='declared', canRemove=False,
                    canInspectRemoval=False, verified=False, mode='unknown',
                    storageFormat=snapshots.SNAPSHOT_SCHEMA, retainedPreviousVersions=True)
                try:
                    activation, _ = snapshots._read_activation(root, package)
                    row['season'] = activation['season']
                    if activation['snapshotId'] is None:
                        row.update(status='not-installed', canInspectRemoval=True, compatibilityNotice='已停用；可检查并删除保留的图包版本。')
                    else:
                        row['canInspectRemoval'] = True
                        view = snapshots.SnapshotPackage(root, package)
                        row['declaredFileCount'] = len(view.owned)
                        from generic_author_package import bindings_path, _label
                        declaration = files._json(view.read(bindings_path(package)))
                        if declaration.get('profileId') == package and _label(declaration.get('displayName')):
                            row['title'] = declaration['displayName']
                except (OSError, ValueError, TypeError, KeyError):
                    row['status'] = 'unverified'
                result.append(row)
                continue
            path = files._target(root, f'work/map-packages/{package}.json')
            active = path.exists()
            if not active and package not in history:
                continue
            row = dict(packageId=package, title=PACKAGE_NAMES.get(package, package),
                       status='declared' if active else 'history-unchecked',
                       canRemove=False, canInspectRemoval=True, verified=False,
                       mode='both' if package in ('s1-jiaye-handnotes', 's1-aichicu-routes')
                       else 'difficult' if package.startswith('difficult-') else 'nightmare')
            if active:
                try:
                    receipt = _json(path)
                    inventory, _ = files._receipt_inventory(receipt, package)
                    row['declaredFileCount'] = len(inventory)
                    row['season'] = receipt.get('season', 'unknown')
                    if files.is_generic_profile(package):
                        row['mode'] = 'unknown'
                        from generic_author_package import bindings_path, _label
                        try:
                            declaration = _json(files._target(root, bindings_path(package)))
                            if (declaration.get('profileId') == package
                                    and _label(declaration.get('displayName'))):
                                row['title'] = declaration['displayName']
                                modes = {layer.get('mode') for layer in declaration.get('layers', []) if isinstance(layer, dict)}
                                if modes and modes <= {'nightmare', 'difficult', 'difficulty2'}:
                                    row['mode'] = next(iter(modes)) if len(modes) == 1 else 'both'
                        except (OSError, ValueError, KeyError, TypeError):
                            pass  # A title hint grants no content/ownership authority.
                except (OSError, ValueError, TypeError, KeyError):
                    row.update(status='unverified', canInspectRemoval=False)
            result.append(row)
        return result


def declared_builtins(resource_root, bundled_root=None, *, cancelled=None):
    """Labels from local small manifests only; no image existence/hash promise."""
    root = Path(resource_root)
    rows = []
    for mode, label in (('nightmare', '噩梦'), ('difficult', '困难'), ('difficulty2', '难度二')):
        files._check_cancelled(cancelled)
        path = files._target(root, f'seasons/s1/{mode}/native-map-manifest.json')
        if not path.exists():
            continue
        try:
            raw = _json(path)
            if (raw.get('schemaVersion'), raw.get('season'), raw.get('mode')) != (1, 's1', mode):
                raise ValueError('summary-native-context-mismatch')
            status = 'declared'
        except (OSError, ValueError, TypeError):
            status = 'unverified'
        rows.append(dict(packageId=f'builtin-s1-{mode}', title=f'S1 · {label} · 内置原生底图',
                         builtin=True, canRemove=False, status=status, verified=False))
    if bundled_root is not None:
        path = files._target(Path(bundled_root), 'inventory.json')
        if path.exists():
            try:
                raw = _json(path)
                if (raw.get('schema'), raw.get('packageId')) != ('xingtu-bundled-author-v1', 'difficult-zhan10'):
                    raise ValueError('summary-bundled-context-mismatch')
                status = 'declared'
            except (OSError, ValueError, TypeError):
                status = 'unverified'
            rows.append(dict(packageId='builtin-difficult-zhan10', title='展十 · 困难（内置）',
                             builtin=True, canRemove=False, status=status, verified=False))
    return rows
