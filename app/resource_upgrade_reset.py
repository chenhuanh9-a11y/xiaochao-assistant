"""Explicit, recoverable resource reset for an installation upgrade.

Unlike automatic base repair, this operation requires a reviewed, exact plan.
Observed old files (including changed or unknown package data) are quarantined,
never deleted. Settings and custom-map/route stores are outside its scope.
The caller must finish this before constructing any resource consumers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

import managed_base_resources as base
import map_package_upgrade as transaction
import star_map_import as files
from resource_transaction import (assert_resources_ready, assert_installer_resource_action,
                                  resource_read_lock, resource_write_lock)

SCHEMA = 'xingtu-resource-upgrade-reset-v1'
PENDING = 'work/map-packages/pending-resource-reset.json'
HISTORY = 'work/resource-upgrades'
GENERATION = 'work/resource-generation.json'
MAX_FILES = 20000
MAX_BYTES = 16 * 1024 ** 3
MAX_METADATA_BYTES = 16 * 1024 ** 2
RESOURCE_ROOTS = (
    '_internal/assets', '_internal/references', '_internal/recognition',
    '_internal/seasons', '_internal/qt-author-base',
    '_internal/difficult-native-manifest.json', 'work/map-packages',
)
PRESERVED = ('_internal/assets/custom-maps', '_internal/references/build-fingerprint.txt',
             'work/map-packages/import.lock', PENDING)
PRESERVED_USER_ROOTS = ('assets/custom-maps', '_internal/assets/custom-maps',
                      'work/user-routes', 'work/nightmare-memory', 'work/difficult-memory',
                      'work/seasons', '_internal/qt-editor-base')
IDENTITY_FILES = ('version.json', '_internal/version.json', 'release-report.json',
                  'installation-profile.json', '_internal/installation-profile.json')


class ResourceResetError(ValueError):
    def __init__(self, code, *, paths=()):
        self.code = code
        self.details = {'paths': list(paths)[:12], 'pathCount': len(paths)}
        super().__init__(code)


def _inside(relative, parent):
    return relative == parent or relative.startswith(parent + '/')


def _selected(relative):
    relative = files._relative(relative).casefold()
    return (any(_inside(relative, root) for root in RESOURCE_ROOTS)
            and not any(_inside(relative, root) for root in PRESERVED))


def _encoded(value):
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(data) > MAX_METADATA_BYTES:
        raise ResourceResetError('resource-reset-metadata-limit')
    return data


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _root(install_root):
    root = Path(os.path.abspath(install_root))
    files._no_links(root)
    if not root.is_dir():
        raise ResourceResetError('resource-reset-root-not-directory')
    return root


def _stamp(path):
    files._no_links(path)
    base._validate_target_ancestors(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
        raise ResourceResetError('resource-reset-not-regular-or-size-limit')
    digest = files._sha(path)
    after = path.stat()
    observed = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if observed(before) != observed(after):
        raise ResourceResetError('resource-reset-file-changed-during-inspection')
    return dict(bytes=before.st_size, sha256=digest, identity=transaction._identity(before))


def _scan(root):
    result, seen, total, count = [], set(), 0, 0
    pending = [(relative, 0) for relative in reversed(RESOURCE_ROOTS)]
    while pending:
        relative, depth = pending.pop()
        if not _selected(relative):
            continue
        if depth > 32:
            raise ResourceResetError('resource-reset-tree-limit')
        path = files._target(root, relative)
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        count += 1
        if count > MAX_FILES:
            raise ResourceResetError('resource-reset-tree-limit')
        key = relative.casefold()
        if key in seen:
            raise ResourceResetError('resource-reset-case-collision', paths=[relative])
        seen.add(key)
        if stat.S_ISDIR(info.st_mode):
            # Enumeration failures are not an empty directory. No followlinks.
            pending.extend((child.relative_to(root).as_posix(), depth + 1)
                           for child in sorted(path.iterdir(), reverse=True))
        elif stat.S_ISREG(info.st_mode):
            row = dict(path=relative, **_stamp(path))
            total += row['bytes']
            if total > MAX_BYTES:
                raise ResourceResetError('resource-reset-total-size-limit')
            result.append(row)
        else:
            raise ResourceResetError('resource-reset-special-object', paths=[relative])
    generation = files._target(root, GENERATION)
    if generation.exists():
        result.append(dict(path=GENERATION, **_stamp(generation)))
    return sorted(result, key=lambda row: row['path'])


def _identity_observations(root):
    result = []
    for relative in IDENTITY_FILES:
        path = files._target(root, relative)
        if not path.exists():
            continue
        stamp = _stamp(path)
        value = {}
        if stamp['bytes'] <= 1024 * 1024:
            try:
                raw = files._json(path.read_bytes())
                value = {key: raw[key] for key in (
                    'schema', 'version', 'displayVersion', 'numericVersion', 'sourceCommit',
                    'compiledPayloadSourceCommit', 'installerSourceCommit', 'resourceVersion',
                    'resourceInventorySha256', 'product', 'instance') if key in raw}
            except (OSError, ValueError):
                pass
        result.append(dict(path=relative, **stamp, observed=value,
                           authority='observed-label-only; not ownership proof'))
    return result


def _candidate(root, candidate_directory=None):
    directory = _root(candidate_directory or files._target(root, base.CANDIDATE))
    manifest = base._manifest(files._target(directory, 'manifest.json'))
    if sum(row['bytes'] for row in manifest['files']) > MAX_BYTES:
        raise ResourceResetError('resource-reset-candidate-size-limit')
    _validate_candidate_scope(manifest)
    for row in manifest['files']:
        source = files._target(directory, 'payload/' + row['path'])
        if not transaction._matches(source, row):
            raise ResourceResetError('resource-reset-candidate-integrity-mismatch', paths=[row['path']])
    return directory, manifest


def _validate_candidate_scope(manifest):
    if any(not _selected(row['path']) or base.optional_author_sidecar(row['path'])
           for row in manifest['files']):
        raise ResourceResetError('resource-reset-candidate-outside-base-scope')


def _target_identity(value):
    if value is None:
        return None
    expected = {'sourceCommit', 'displayVersion', 'numericVersion', 'executableSha256', 'resourceInventorySha256'}
    if (not isinstance(value, dict) or set(value) != expected
            or not all(isinstance(value[key], str) for key in expected)
            or not re.fullmatch('[0-9a-f]{40}', value['sourceCommit'])
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?', value['displayVersion'])
            or len(value['displayVersion']) > 64
            or len(value['numericVersion']) > 23
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+', value['numericVersion'])
            or any(int(part) > 65535 for part in value['numericVersion'].split('.'))
            or not all(re.fullmatch('[0-9a-f]{64}', value[key])
                       for key in ('executableSha256', 'resourceInventorySha256'))):
        raise ResourceResetError('resource-reset-invalid-target-identity')
    return dict(value)


def _snapshot_package_hints(root, rows, prior_hints):
    """Read active snapshot identities for the reinstall notice, not ownership.

    The snapshot reader validates activation, pinned manifest and receipt and
    rechecks the active generation. Payload damage does not hide a package that
    needs reinstalling. A tombstone or malformed activation suppresses stale
    flat-receipt hints, just as it prevents legacy runtime fallback.
    """
    import author_resource_snapshots as snapshots
    from generic_author_package import is_generic_profile

    active = set()
    # The scanner inventories files, not empty directories. Mirror the runtime
    # presence guard too: a damaged activation that became a directory must
    # not revive a stale flat receipt merely because it has no file row.
    shadowed = {package for package in prior_hints
                if is_generic_profile(package) and snapshots.managed(root, package)}
    prefix = snapshots.STORE + '/active/'
    for row in rows:
        relative = row['path']
        name = relative.casefold()[len(prefix):] if relative.casefold().startswith(prefix) else ''
        if not name.endswith('.json') or '/' in name:
            continue
        package = name[:-5]
        if not is_generic_profile(package):
            continue
        shadowed.add(package)
        if relative != snapshots.activation_path(package):
            continue  # Case/path variants are not an authenticated activation.
        try:
            view = snapshots.SnapshotPackage(root, package)
            view.assert_active()
        except (OSError, ValueError):
            continue  # Unknown bytes remain in the same recoverable inventory.
        active.add(package)
    return active, shadowed


def _classify(root, rows):
    reviewed, declared = {}, {}
    def register(target, row, source):
        target.setdefault(row['path'].casefold(), []).append((row['bytes'], row['sha256'], source))
    for source, _inventory_digest, values in base.REVIEWED_LEGACY_S0_BASE_RELEASES:
        for path, size, digest in values:
            register(reviewed, dict(path=path, bytes=size, sha256=digest), 'source:' + source)
    releases, _ = base._reviewed_s1_catalogue()
    for version, values in releases:
        for path, size, digest in values:
            register(reviewed, dict(path=path, bytes=size, sha256=digest), 'resource:' + version)
    from legacy_author_inventory import LEGACY_AUTHOR_INVENTORY
    for package, release in LEGACY_AUTHOR_INVENTORY.items():
        for row in release['files']:
            register(reviewed, row, 'reviewed-author:' + package)
    package_ids = set()
    for item in rows:
        relative = item['path']
        if relative == base.STATE:
            try:
                old = base._manifest(files._target(root, relative))
                for row in old['files']:
                    register(declared, row, 'base-state:' + old['resourceVersion'])
            except (OSError, ValueError):
                pass
        elif relative.startswith('work/map-packages/') and relative.count('/') == 2 and relative.endswith('.json'):
            package = Path(relative).stem
            try:
                value = transaction._read_json(files._target(root, relative))
                if value.get('packageId') != package or not files.known_package_id(package):
                    continue
                inventory = files._rows(value.get('files'))
                if any(not files._allowed(package, row['path']) for row in inventory.values()):
                    continue
                package_ids.add(package)
                for row in inventory.values():
                    register(declared, row, 'package-receipt:' + package)
            except (OSError, ValueError):
                pass
    result = []
    for row in rows:
        exact = lambda item: item[:2] == (row['bytes'], row['sha256'])
        proof = [source for size, digest, source in reviewed.get(row['path'].casefold(), []) if (size, digest) == (row['bytes'], row['sha256'])]
        package_ids.update(source.split(':', 1)[1] for source in proof if source.startswith('reviewed-author:'))
        declarations = declared.get(row['path'].casefold(), [])
        matching = [item[2] for item in declarations if exact(item)]
        kind = ('source-reviewed' if proof else 'receipt-matched' if matching else
                'modified-declared-resource' if declarations else 'unclassified-resource')
        if row['path'].startswith('work/map-packages/'):
            kind = 'package-state-or-history'
        result.append(dict(row, classification=kind, sources=proof + matching))
    active_snapshots, shadowed_packages = _snapshot_package_hints(root, rows, package_ids)
    package_ids.difference_update(shadowed_packages)
    package_ids.update(active_snapshots)
    return result, sorted(package_ids)


def _plan_locked(root, candidate_directory=None, target_identity=None):
    assert_resources_ready(root)
    if files._target(root, PENDING).exists():
        raise ResourceResetError('resource-reset-recovery-required')
    # Unfinished helper handoffs cannot be silently archived as stale receipts.
    handoffs = ('qt-import-pending.json', 'qt-recovery-pending.json', 'main-import-request.json')
    blocked = [name for name in handoffs if files._target(root, 'work/map-packages/' + name).exists()]
    if blocked:
        raise ResourceResetError('resource-reset-handoff-pending', paths=blocked)
    _directory, manifest = _candidate(root, candidate_directory)
    rows, packages = _classify(root, _scan(root))
    # Structural collisions need a separate directory migration contract. Do
    # not partially move files and only discover them while publishing later.
    blocked = []
    for row in manifest['files']:
        target = files._target(root, row['path'])
        base._validate_target_ancestors(target)
        if target.exists() and not target.is_file():
            blocked.append(row['path'])
    if blocked:
        raise ResourceResetError('resource-reset-target-not-file', paths=blocked)
    plan = dict(schema=SCHEMA, rootIdentity=transaction._identity(root.stat()),
                targetIdentity=_target_identity(target_identity),
                targetManifest=manifest, oldFiles=rows, packagesToDownload=packages,
                observedIdentity=_identity_observations(root),
                preservedUserRoots=list(PRESERVED_USER_ROOTS),
                fileCount=len(rows), bytes=sum(row['bytes'] for row in rows),
                modifiedCount=sum(row['classification'] == 'modified-declared-resource' for row in rows),
                unclassifiedCount=sum(row['classification'] == 'unclassified-resource' for row in rows),
                requiresExplicitConfirmation=True)
    plan['planSha256'] = _digest(plan)
    return plan


def inspect_resource_upgrade(install_root, *, candidate_directory=None, target_identity=None):
    """Exact read-only preview. Metadata labels never grant deletion authority."""
    root = _root(install_root)
    with resource_read_lock(root):
        return _plan_locked(root, candidate_directory, target_identity)


def _row(value):
    return {key: value[key] for key in ('path', 'bytes', 'sha256')}


def _active_inventory(rows):
    # Existing Windows directories retain their original spelling even when a
    # new file was published through a differently cased lexical parent path.
    # _scan rejects collisions; keep every content and object-identity check.
    return sorted((dict(row, path=row['path'].casefold()) for row in rows), key=lambda row: row['path'])


def _read_json(path):
    files._no_links(path)
    if not path.is_file() or path.stat().st_size > MAX_METADATA_BYTES:
        raise ResourceResetError('resource-reset-invalid-metadata')
    return files._json(path.read_bytes())


def _write_journal(directory, journal):
    # All bytes are checked before creating a temporary, so oversized metadata
    # cannot interrupt a transaction after its first old-file move.
    data = _encoded(journal)
    temporary = files._target(directory, 'journal-' + uuid.uuid4().hex + '.tmp')
    transaction._write_new(temporary, data)
    os.replace(temporary, files._target(directory, 'journal.json'))
    transaction._directory_sync(directory)


def _new_payloads(plan, transaction_id):
    encoded_state = base._encoded(plan['targetManifest'])
    generation = _encoded(dict(schema=SCHEMA, transactionId=transaction_id,
                               targetIdentity=plan['targetIdentity'],
                               targetBundleId=plan['targetManifest']['bundleId'],
                               targetResourceVersion=plan['targetManifest']['resourceVersion'],
                               previousPlanSha256=plan['planSha256']))
    generated = {base.STATE: encoded_state, GENERATION: generation}
    rows = [_row(row) for row in plan['targetManifest']['files']]
    rows.extend(dict(path=path, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                for path, data in generated.items())
    return sorted(rows, key=lambda row: row['path']), generated


def _validate_plan(plan):
    if not isinstance(plan, dict) or plan.get('schema') != SCHEMA:
        raise ResourceResetError('resource-reset-invalid-plan')
    content = {key: value for key, value in plan.items() if key != 'planSha256'}
    if _digest(content) != plan.get('planSha256') or plan.get('requiresExplicitConfirmation') is not True:
        raise ResourceResetError('resource-reset-invalid-plan')
    transaction._valid_identity(plan.get('rootIdentity'))
    manifest = plan.get('targetManifest', {})
    if base.manifest_for_rows(manifest.get('files'), manifest.get('resourceVersion')) != manifest:
        raise ResourceResetError('resource-reset-invalid-target-manifest')
    _validate_candidate_scope(manifest)
    _target_identity(plan.get('targetIdentity'))
    seen, total = set(), 0
    old = plan.get('oldFiles')
    if not isinstance(old, list) or len(old) > MAX_FILES:
        raise ResourceResetError('resource-reset-invalid-old-inventory')
    for row in old:
        if (not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256', 'identity', 'classification', 'sources'}
                or not (_selected(row['path']) or row['path'] == GENERATION)
                or row['path'].casefold() in seen or type(row['bytes']) is not int
                or not 0 <= row['bytes'] <= MAX_BYTES or not isinstance(row['sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            raise ResourceResetError('resource-reset-invalid-old-inventory')
        transaction._valid_identity(row['identity'])
        seen.add(row['path'].casefold()); total += row['bytes']
    if total > MAX_BYTES:
        raise ResourceResetError('resource-reset-total-size-limit')


def _load(root, transaction_id=None):
    pointer_path = files._target(root, PENDING)
    pointer = (_read_json(pointer_path) if pointer_path.exists() else
               dict(schema=SCHEMA, transactionId=transaction_id))
    if (set(pointer) != {'schema', 'transactionId'} or pointer['schema'] != SCHEMA
            or not isinstance(pointer['transactionId'], str)
            or not base._is_transaction_id(pointer['transactionId'])
            or transaction_id is not None and pointer['transactionId'] != transaction_id):
        raise ResourceResetError('resource-reset-invalid-pending')
    directory = files._target(root, HISTORY + '/' + pointer['transactionId'])
    journal = _read_json(files._target(directory, 'journal.json'))
    if (set(journal) != {'schema', 'transactionId', 'status', 'plan', 'newFiles'}
            or journal['schema'] != SCHEMA or journal['transactionId'] != pointer['transactionId']
            or journal['status'] not in {'preparing', 'moving', 'prepared-resources', 'committed', 'rolled-back'}):
        raise ResourceResetError('resource-reset-invalid-journal')
    _validate_plan(journal['plan'])
    if journal['plan']['rootIdentity'] != transaction._identity(root.stat()):
        raise ResourceResetError('resource-reset-root-identity-changed')
    expected, _generated = _new_payloads(journal['plan'], journal['transactionId'])
    if (not isinstance(journal['newFiles'], list) or len(journal['newFiles']) != len(expected)):
        raise ResourceResetError('resource-reset-invalid-new-inventory')
    for row, desired in zip(journal['newFiles'], expected):
        if not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256', 'identity'} or _row(row) != desired:
            raise ResourceResetError('resource-reset-invalid-new-inventory')
        if row['identity'] is not None:
            transaction._valid_identity(row['identity'])
        elif journal['status'] not in {'preparing', 'rolled-back'}:
            raise ResourceResetError('resource-reset-missing-prepared-identity')
    return directory, journal


def _clear_pending(root, transaction_id):
    path = files._target(root, PENDING)
    if not path.exists():
        return
    expected = _encoded(dict(schema=SCHEMA, transactionId=transaction_id))
    digest = hashlib.sha256(expected).hexdigest()
    retained = files._target(root, HISTORY + '/' + transaction_id + '/pending-pointer.json')
    witness = _stamp(retained)
    stamp = _stamp(path)
    # Semantic inspection followed by an unrelated second stamp could adopt a
    # foreign replacement marker. Bind bytes and identity to our retained pin.
    if (witness['sha256'] != digest or witness['bytes'] != len(expected)
            or stamp != witness):
        raise ResourceResetError('resource-reset-pending-changed')
    if not files._rollback_published(path, tuple(stamp['identity']), stamp['sha256']):
        raise ResourceResetError('resource-reset-pending-changed')
    transaction._directory_sync(path.parent)


def _verify_unchanged_plan(root, plan, candidate_directory):
    observed = [{key: row[key] for key in ('path', 'bytes', 'sha256', 'identity')} for row in plan['oldFiles']]
    if (_scan(root) != observed or _identity_observations(root) != plan['observedIdentity']
            or transaction._identity(root.stat()) != plan['rootIdentity']
            or _candidate(root, candidate_directory)[1] != plan['targetManifest']):
        raise ResourceResetError('resource-reset-plan-changed-reinspect-required')


def _rollback_locked(root, directory, journal):
    old_by_path = {row['path'].casefold(): (index, row) for index, row in enumerate(journal['plan']['oldFiles'])}
    new_by_path = {row['path'].casefold(): row for row in journal['newFiles']}
    blocked = []
    groups = sorted(old_by_path.keys() | new_by_path.keys())
    for key in groups:
        old, new = old_by_path.get(key), new_by_path.get(key)
        paths = {row['path'] for row in ((old[1] if old else None), new) if row}
        old_target = files._target(root, old[1]['path']) if old else None
        already_old = bool(old and transaction._matches(old_target, old[1], old[1]['identity']))
        if old and not already_old and not transaction._matches(
                files._target(directory, 'old/' + str(old[0])), old[1], old[1]['identity']):
            blocked.append(old[1]['path'])
        for relative in paths:
            target = files._target(root, relative)
            base._validate_target_ancestors(target)
            if target.exists() and not (old and transaction._matches(target, old[1], old[1]['identity'])) and not (
                    new and new['identity'] is not None and transaction._matches(target, new, new['identity'])):
                blocked.append(relative)
    if blocked:
        raise ResourceResetError('resource-reset-rollback-blocked', paths=sorted(set(blocked)))
    for key in reversed(groups):
        old, new = old_by_path.get(key), new_by_path.get(key)
        # Distinct spellings may be one Windows object or two POSIX paths. Do
        # not restore an old alias then treat it as an unrelated new-only file.
        paths = {row['path'] for row in ((old[1] if old else None), new) if row}
        for relative in sorted(paths):
            target = files._target(root, relative)
            base._validate_target_ancestors(target)
            if not target.exists() or old and transaction._matches(target, old[1], old[1]['identity']):
                continue
            if new is None or new['identity'] is None or not files._rollback_published(target, tuple(new['identity']), new['sha256']):
                raise ResourceResetError('resource-reset-rollback-blocked', paths=[relative])
            transaction._directory_sync(target.parent)
        if old and not transaction._matches(files._target(root, old[1]['path']), old[1], old[1]['identity']):
            target = files._target(root, old[1]['path'])
            base._validate_target_ancestors(target)
            transaction._move_owned(files._target(directory, 'old/' + str(old[0])), target,
                                    old[1], old[1]['identity'])
    journal['status'] = 'rolled-back'
    _write_journal(directory, journal)
    _clear_pending(root, journal['transactionId'])
    # No recursive cleanup. Retained new pins / partial preparation evidence are
    # outside active roots and remain inspectable; unknown bytes are never purged.
    return dict(status='rolled-back', transactionId=journal['transactionId'],
                retainedDirectory=str(directory))


def prepare_resource_upgrade(install_root, *, expected_plan_sha256, confirmed=False,
                             candidate_directory=None, target_identity=None):
    """Prepare resources, but do NOT report Setup committed.

    The installer helper must retain its runtime/verified inherited lease across
    this call, program replacement and commit/rollback. This API alone is not an
    installer integration, and must never be invoked against a running client.
    """
    if confirmed is not True or not isinstance(expected_plan_sha256, str):
        raise ResourceResetError('resource-reset-explicit-confirmation-required')
    root = _root(install_root)
    with resource_write_lock(root):
        plan = _plan_locked(root, candidate_directory, target_identity)
        if plan['planSha256'] != expected_plan_sha256:
            raise ResourceResetError('resource-reset-plan-changed-reinspect-required')
        _validate_plan(plan)
        source, _manifest = _candidate(root, candidate_directory)
        transaction_id = str(uuid.uuid4())
        new_rows, generated = _new_payloads(plan, transaction_id)
        if shutil.disk_usage(root).free < sum(row['bytes'] for row in new_rows) + MAX_METADATA_BYTES:
            raise ResourceResetError('resource-reset-insufficient-free-space')
        from resource_transaction import preflight_hardlinks
        preflight_hardlinks(root, [row['path'] for row in plan['oldFiles']] + [row['path'] for row in new_rows])
        directory = files._target(root, HISTORY + '/' + transaction_id)
        directory.mkdir(parents=True, exist_ok=False)
        journal = dict(schema=SCHEMA, transactionId=transaction_id, status='preparing', plan=plan,
                       newFiles=[dict(row, identity=None) for row in new_rows])
        _write_journal(directory, journal)
        pointer = files._target(directory, 'pending-pointer.json')
        transaction._write_new(pointer, _encoded(dict(schema=SCHEMA, transactionId=transaction_id)))
        # Publish only a complete fsynced pointer. A torn exclusive write at the
        # public path would otherwise block both recovery and all later resets.
        os.link(pointer, files._target(root, PENDING))
        transaction._directory_sync(files._target(root, PENDING).parent)
        try:
            for index, row in enumerate(journal['newFiles']):
                data = generated.get(row['path'])
                if data is None:
                    data = files._target(source, 'payload/' + row['path']).read_bytes()
                retained = files._target(directory, 'new/' + str(index))
                transaction._write_new(retained, data)
                row['identity'] = transaction._capture(retained, row)
                _write_journal(directory, journal)
            _verify_unchanged_plan(root, plan, source)
            journal['status'] = 'moving'
            _write_journal(directory, journal)
            for index, row in enumerate(plan['oldFiles']):
                transaction._move_owned(files._target(root, row['path']),
                                        files._target(directory, 'old/' + str(index)), row, row['identity'])
            # No old object can reappear under a path about to be published.
            for row in journal['newFiles']:
                target = files._target(root, row['path'])
                base._validate_target_ancestors(target)
                if target.exists():
                    raise ResourceResetError('resource-reset-target-appeared', paths=[row['path']])
            for index, row in enumerate(journal['newFiles']):
                target = files._target(root, row['path'])
                base._validate_target_ancestors(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                files._no_links(target.parent)
                os.link(files._target(directory, 'new/' + str(index)), target)
                if not transaction._matches(target, row, row['identity']):
                    raise ResourceResetError('resource-reset-published-file-changed', paths=[row['path']])
                transaction._directory_sync(target.parent)
            journal['status'] = 'prepared-resources'
            _write_journal(directory, journal)
        except Exception as original:
            try:
                # A journal replace may have succeeded before a sync error.
                # Load the durable view; never guess a different transaction.
                directory, journal = _load(root, transaction_id)
                _rollback_locked(root, directory, journal)
            except Exception as rollback_error:
                raise ResourceResetError('resource-reset-failed-recovery-required') from rollback_error
            raise original
        return dict(status='prepared-resources', transactionId=transaction_id,
                    planSha256=plan['planSha256'], targetBundleId=plan['targetManifest']['bundleId'],
                    retainedDirectory=str(directory), packagesToDownload=plan['packagesToDownload'])


def finish_resource_upgrade(install_root, transaction_id, *, setup_succeeded=False):
    """Installer event boundary: success commits; cancellation/failure restores.

    setup_succeeded must come from the current installer's verified completion,
    not from process exit, a label or first startup. Native integration owns it.
    """
    root = _root(install_root)
    with resource_write_lock(root):
        directory, journal = _load(root, transaction_id)
        assert_installer_resource_action(root, 'commit' if setup_succeeded is True else 'rollback',
                                         transaction_id, journal['plan']['planSha256'])
        if journal['status'] == 'committed':
            _clear_pending(root, transaction_id)
            return dict(status='committed', transactionId=transaction_id, retainedDirectory=str(directory))
        if journal['status'] == 'rolled-back' and setup_succeeded is not True:
            _clear_pending(root, transaction_id)
            return dict(status='rolled-back', transactionId=transaction_id, retainedDirectory=str(directory))
        if setup_succeeded is not True:
            return _rollback_locked(root, directory, journal)
        if journal['status'] != 'prepared-resources':
            raise ResourceResetError('resource-reset-not-prepared')
        for row in journal['newFiles']:
            if not transaction._matches(files._target(root, row['path']), row, row['identity']):
                raise ResourceResetError('resource-reset-new-plane-changed', paths=[row['path']])
        for index, row in enumerate(journal['plan']['oldFiles']):
            if not transaction._matches(files._target(directory, 'old/' + str(index)), row, row['identity']):
                raise ResourceResetError('resource-reset-quarantine-changed', paths=[row['path']])
        expected_active = [{key: row[key] for key in ('path', 'bytes', 'sha256', 'identity')}
                           for row in journal['newFiles']]
        if _active_inventory(_scan(root)) != _active_inventory(expected_active):
            raise ResourceResetError('resource-reset-active-plane-changed')
        journal['status'] = 'committed'
        _write_journal(directory, journal)
        _clear_pending(root, transaction_id)
        return dict(status='committed', transactionId=transaction_id,
                    retainedDirectory=str(directory), packagesToDownload=journal['plan']['packagesToDownload'])


def recover_resource_upgrade(install_root):
    """Interrupted, uncommitted reset restores; committed reset never rewinds."""
    root = _root(install_root)
    with resource_write_lock(root):
        # Recovery outside the coordinator must never rewind just the resource
        # half of a native installation, even if a caller owns the OS lease.
        if files._target(root, 'work/pending-installer-upgrade.json').exists():
            raise ResourceResetError('installer-upgrade-recovery-required')
        if not files._target(root, PENDING).exists():
            return dict(status='not-needed')
        directory, journal = _load(root)
        if journal['status'] in {'committed', 'rolled-back'}:
            _clear_pending(root, journal['transactionId'])
            return dict(status=journal['status'], transactionId=journal['transactionId'], retainedDirectory=str(directory))
        return _rollback_locked(root, directory, journal)


def resource_upgrade_status(install_root, transaction_id=None):
    """Read durable commit state after an uncertain helper result, without I/O writes.

    The caller must not roll back program files while the commit state is
    unknown. A committed resource generation cannot be uncommitted by a lost
    response. The normal lock-file acquisition may create its own empty lock.
    """
    root = _root(install_root)
    with resource_read_lock(root):
        if transaction_id is None and not files._target(root, PENDING).exists():
            return dict(status='not-needed')
        directory, journal = _load(root, transaction_id)
        return dict(status=journal['status'], transactionId=journal['transactionId'],
                    planSha256=journal['plan']['planSha256'], retainedDirectory=str(directory),
                    targetIdentity=journal['plan']['targetIdentity'])
