"""Versioned author displays: immutable payloads, one guarded activation record.

Snapshots never supply recognition or coordinate authority. Their bindings are
rechecked against the current base per layer. Old snapshots and legacy files
are retained, not silently garbage-collected or rewritten during an update.
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

import generic_author_package as generic
import map_package_upgrade as transaction
import star_map_import as files

STORE = 'work/map-packages/author-snapshots'
PENDING = 'work/map-packages/pending-author-snapshot.json'
SNAPSHOT_SCHEMA = 'xingtu-author-resource-snapshot-v1'
ACTIVATION_SCHEMA = 'xingtu-author-resource-activation-v1'
JOURNAL_SCHEMA = 'xingtu-author-resource-switch-v1'
MAX_METADATA = 4 * 1024 * 1024


def _reject(reason):
    raise files.ImportRejected('author-snapshot-' + reason)


def _encoded(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    if len(data) > MAX_METADATA:
        _reject('metadata-limit')
    return data


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        _reject('invalid-id')
    return value


def _transaction_id(value):
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value or parsed.version != 4:
            _reject('invalid-transaction')
    except (ValueError, TypeError, AttributeError):
        _reject('invalid-transaction')
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        _reject('invalid-digest')
    return value


def activation_path(package):
    if not generic.is_generic_profile(package):
        _reject('invalid-package')
    return STORE + '/active/' + package + '.json'


def _version_path(package, snapshot_id):
    activation_path(package)
    return STORE + '/versions/' + package + '/' + _id(snapshot_id)


def _metadata(path):
    files._no_links(path)
    flags = (os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
             | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_METADATA:
            _reject('invalid-metadata-file')
        raw = handle.read(MAX_METADATA + 1)
        after = os.fstat(handle.fileno())
    signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    files._no_links(path)
    # CPython 3.13 on Windows reports ChangeTime in fstat().st_ctime,
    # but birthtime in stat().st_ctime. Reopen the pathname and use the same
    # handle-based observation, preserving every identity and change guard.
    current_descriptor = os.open(path, flags)
    try:
        current = os.fstat(current_descriptor)
    finally:
        os.close(current_descriptor)
    files._no_links(path)
    if (len(raw) > MAX_METADATA or len(raw) != after.st_size or not stat.S_ISREG(current.st_mode)
            or signature(before) != signature(after) or signature(current) != signature(after)):
        _reject('metadata-changed')
    row = dict(path=path.name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
               identity=transaction._identity(after))
    return raw, row


def _read_json(path):
    return files._json(_metadata(path)[0])


def _read_activation(root, package):
    path = files._target(Path(root), activation_path(package))
    if not path.exists():
        return None, None
    raw, stamp = _metadata(path)
    value = files._json(raw)
    _validate_activation(value, package)
    return value, stamp['sha256']


def _validate_activation(value, package):
    if (set(value) != {'schema', 'packageId', 'season', 'snapshotId', 'manifestSha256', 'generation'}
            or value['schema'] != ACTIVATION_SCHEMA or value['packageId'] != package
            or not generic.valid_season(value['season'])):
        _reject('invalid-activation')
    _id(value['generation'])
    if value['snapshotId'] is None:
        if value['manifestSha256'] is not None:
            _reject('invalid-activation')
    else:
        _id(value['snapshotId']); _sha(value['manifestSha256'])
    return value


def managed(root, package):
    """Presence is not trust; a broken record must not revive an old flat copy."""
    path = files._target(Path(root), activation_path(package))
    return path.exists()


def profile_ids(root, *, strict=False):
    directory = files._target(Path(root), STORE + '/active')
    if not directory.exists():
        return ()
    found = []
    for path in sorted(directory.iterdir()):
        package = path.stem.casefold()
        if path.suffix.casefold() == '.json' and generic.is_generic_profile(package):
            try:
                files._no_links(path)
                if path.name != package + '.json' or not path.is_file():
                    _reject('invalid-activation-file')
                found.append(package)
            except (OSError, ValueError):
                if strict:
                    raise
                continue  # One unsafe optional record must not hide healthy authors.
    return tuple(found)


def _stored_name(index, row):
    return 'files/' + str(index).zfill(4) + Path(row['path']).suffix


def _manifest(root, package, activation):
    if activation['snapshotId'] is None:
        raise FileNotFoundError('author-snapshot-inactive')
    directory = files._target(Path(root), _version_path(package, activation['snapshotId']))
    path = files._target(directory, 'manifest.json')
    raw, _ = _metadata(path)
    if hashlib.sha256(raw).hexdigest() != activation['manifestSha256']:
        _reject('manifest-changed')
    value = files._json(raw)
    if (set(value) != {'schema', 'packageId', 'snapshotId', 'receipt', 'contentId'}
            or value['schema'] != SNAPSHOT_SCHEMA or value['packageId'] != package
            or value['snapshotId'] != activation['snapshotId']):
        _reject('invalid-manifest')
    receipt = value['receipt']
    if (not isinstance(receipt, dict) or receipt.get('packageId') != package
            or receipt.get('season') != activation['season']):
        _reject('invalid-receipt')
    _sha(receipt.get('archiveSha256'))
    owned, dependencies = generic.receipt_rows(receipt)
    if value['contentId'] != _digest(receipt):
        _reject('content-identity-mismatch')
    return directory, value, owned, dependencies


class SnapshotPackage:
    """A pinned version view. Reads reject a changed active generation."""
    def __init__(self, root, package):
        self.root, self.package = Path(root), package
        self.activation, self.activation_sha = _read_activation(self.root, package)
        if self.activation is None:
            raise FileNotFoundError('author-snapshot-not-installed')
        self.directory, self.manifest, self.owned, self.dependencies = _manifest(self.root, package, self.activation)
        self.paths = {row['path'].casefold(): _stored_name(index, row)
                      for index, row in enumerate(sorted(self.owned.values(), key=lambda row: row['path']))}

    def assert_active(self):
        current, digest = _read_activation(self.root, self.package)
        if current != self.activation or digest != self.activation_sha:
            _reject('activation-changed')

    def path(self, relative):
        self.assert_active()
        stored = self.paths.get(relative.casefold())
        return (files._target(self.directory, stored) if stored else files._target(self.root, relative))

    def read(self, relative):
        from generic_author_base import check_browse_cancelled
        check_browse_cancelled()
        row = self.owned.get(relative.casefold()) or self.dependencies.get(relative.casefold())
        if row is None:
            _reject('undeclared-file')
        path = self.path(relative)
        with path.open('rb') as stream:
            data = stream.read(row['bytes'] + 1)
        check_browse_cancelled()
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            _reject('file-changed')
        self.assert_active()
        return data

    def document(self, dependency_paths=None):
        return generic.validate_bindings(self.read(generic.bindings_path(self.package)),
            self.owned, self.dependencies, self.read, root=self.root,
            dependency_paths=dependency_paths, isolate_incompatible_layers=True, tolerate_damaged_images=True)


def _previous(root, package, season):
    activation, activation_sha = _read_activation(root, package)
    legacy = files._target(Path(root), f'work/map-packages/{package}.json')
    legacy_sha, receipt, old_document = None, None, None
    if activation is not None:
        if activation['season'] != season:
            _reject('package-season-cannot-change')
        if legacy.exists():
            _reject('dual-active-records')
        if activation['snapshotId'] is not None:
            try:
                package_view = SnapshotPackage(root, package)
                receipt = package_view.manifest['receipt']
                old_document = files._json(package_view.read(generic.bindings_path(package)))
            except (OSError, ValueError):
                # Existing snapshots are not overwritten or deleted. A fresh
                # verified version may supersede a damaged display generation.
                receipt = None
    elif legacy.exists():
        receipt = _read_json(legacy)
        files._receipt_inventory(receipt, package)
        legacy_sha = files._sha(legacy)
        binding = files._target(Path(root), generic.bindings_path(package))
        try:
            owned, _ = generic.receipt_rows(receipt)
            row = owned[generic.bindings_path(package).casefold()]
            if transaction._matches(binding, row):
                old_document = files._json(binding.read_bytes())
        except (OSError, ValueError):
            pass
    if receipt is not None and receipt.get('season') != season:
        _reject('package-season-cannot-change')
    return activation, activation_sha, legacy_sha, receipt, old_document


def inspect_validated_archive(metadata, archive, root, rows, dependencies):
    """Called only after the shared ZIP/header/scope/size validation succeeds."""
    def read(relative):
        row = rows.get(relative.casefold()) or dependencies.get(relative.casefold())
        if row is None:
            generic._reject('undeclared-file')
        if relative.casefold() in rows:
            data = archive.read('data/' + relative)
        else:
            with files._target(Path(root), relative).open('rb') as stream:
                data = stream.read(row['bytes'] + 1)
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            generic._reject('file-changed')
        return data
    document = generic.validate_bindings(read(generic.bindings_path(metadata['packageId'])), rows,
        dependencies, read, root=root, isolate_incompatible_layers=True)
    if document['profileId'] != metadata['packageId'] or document['season'] != metadata['season']:
        generic._reject('package-binding-mismatch')
    activation, activation_sha, legacy_sha, previous, old_document = _previous(root, metadata['packageId'], metadata['season'])
    old_rows = files._rows(previous['files']) if previous else {}
    active_stamp = _stamp(files._target(Path(root), activation_path(metadata['packageId']))) if activation else None
    legacy_stamp = _stamp(files._target(Path(root), f"work/map-packages/{metadata['packageId']}.json")) if legacy_sha else None
    if ((active_stamp and active_stamp['sha256'] != activation_sha)
            or (legacy_stamp and legacy_stamp['sha256'] != legacy_sha)):
        _reject('plan-changed-reinspect-required')
    key = lambda layer: (layer['season'], layer['mode'], layer['mapId'], layer['floor'])
    # Delta is explanatory only; inactive layers are read from the original
    # binding too, never silently dropped from the immutable payload.
    raw_document = files._json(read(generic.bindings_path(metadata['packageId'])))
    incoming_layers = {key(row): row for row in raw_document['layers']}
    old_layers = {key(row): row for row in (old_document or {}).get('layers', [])}
    changed = [row for path, row in rows.items() if path in old_rows and row != old_rows[path]]
    added = [row for path, row in rows.items() if path not in old_rows]
    removed = [row for path, row in old_rows.items() if path not in rows]
    plan = dict(packageId=metadata['packageId'], season=metadata['season'], files=list(rows.values()),
                requiresFiles=list(dependencies.values()), expectedActivationSha256=activation_sha,
                previousReceiptSha256=legacy_sha, layerStates=document['layerStates'],
                expectedActivationIdentity=active_stamp['identity'] if active_stamp else None,
                previousReceiptIdentity=legacy_stamp['identity'] if legacy_stamp else None,
                removedLayers=[list(value) for value in sorted(old_layers.keys() - incoming_layers.keys())],
                formatChangedLayers=[list(value) for value in sorted(old_layers.keys() & incoming_layers.keys())
                    if Path(old_layers[value]['displayPath']).suffix != Path(incoming_layers[value]['displayPath']).suffix])
    return dict(plan, schema=generic.SCHEMA, revision=1, storageFormat=SNAPSHOT_SCHEMA,
                authorName=document['authorName'], displayName=document['displayName'],
                inactiveLayers=document['inactiveLayers'], fileCount=len(rows), bytes=sum(row['bytes'] for row in rows.values()),
                replaceFiles=changed, missingFiles=added, retiredFiles=removed,
                alreadyPresent=sum(row == old_rows.get(path) for path, row in rows.items()),
                requiresConfirmation=activation is not None or legacy_sha is not None or bool(document['inactiveLayers']),
                snapshotPlanSha256=_digest(plan), restartRequired=False,
                authentication='not-signed; SHA256 integrity only', legacyFilesPreserved=legacy_sha is not None)


def _stamp(path):
    return _metadata(path)[1]


def _write_journal(directory, journal):
    data = _encoded(journal)
    temporary = files._target(directory, 'journal-' + uuid.uuid4().hex + '.tmp')
    transaction._write_new(temporary, data)
    os.replace(temporary, files._target(directory, 'journal.json'))
    transaction._directory_sync(directory)


def _journal_digest(journal):
    return _digest({key: value for key, value in journal.items() if key != 'status'})


def _validate_pointer(root, directory, transaction_id, journal=None):
    path = files._target(Path(root), PENDING)
    if journal is None:
        journal = _read_json(files._target(directory, 'journal.json'))
    expected = _encoded(dict(schema=JOURNAL_SCHEMA, transactionId=transaction_id, planSha256=_journal_digest(journal)))
    witness = _stamp(files._target(directory, 'pending-pointer.json'))
    actual = _stamp(path)
    if (actual['identity'] != witness['identity'] or actual['sha256'] != hashlib.sha256(expected).hexdigest()
            or witness['sha256'] != actual['sha256'] or actual['bytes'] != len(expected)):
        _reject('pending-changed')
    return actual


def _clear_pointer(root, directory, transaction_id):
    path = files._target(Path(root), PENDING)
    actual = _validate_pointer(root, directory, transaction_id)
    if not files._rollback_published(path, tuple(actual['identity']), actual['sha256']):
        _reject('pending-changed')
    transaction._directory_sync(path.parent)


def _load_pending(root):
    pointer = _read_json(files._target(Path(root), PENDING))
    if set(pointer) != {'schema', 'transactionId', 'planSha256'} or pointer['schema'] != JOURNAL_SCHEMA:
        _reject('invalid-pending')
    _sha(pointer['planSha256'])
    directory = files._target(Path(root), STORE + '/transactions/' + _transaction_id(pointer['transactionId']))
    journal = _read_json(files._target(directory, 'journal.json'))
    _validate_pointer(root, directory, pointer['transactionId'], journal)
    return directory, _validated_journal(root, directory, pointer['transactionId'], journal)


def _validated_journal(root, directory, transaction_id, journal):
    """Shared strict envelope/outcome check for recovery and retained history."""
    if (set(journal) != {'schema', 'transactionId', 'rootIdentity', 'packageId', 'status', 'changes'}
            or journal['schema'] != JOURNAL_SCHEMA or journal['transactionId'] != transaction_id
            or journal['rootIdentity'] != transaction._identity(Path(root).stat())
            or journal['status'] not in {'prepared', 'committed', 'rolled-back'}
            or not isinstance(journal['changes'], list) or not 1 <= len(journal['changes']) <= 2):
        _reject('invalid-journal')
    active = activation_path(journal['packageId'])
    targets = {active, f'work/map-packages/{journal["packageId"]}.json'}
    seen = set()
    for entry in journal['changes']:
        if (not isinstance(entry, dict) or set(entry) != {'path', 'old', 'new'}
                or entry['path'] not in targets or entry['path'] in seen
                or (entry['path'] == active and entry['new'] is None)
                or (entry['path'] != active and (entry['old'] is None or entry['new'] is not None))):
            _reject('invalid-journal-target')
        seen.add(entry['path'])
        for row in (entry['old'], entry['new']):
            if row is not None:
                if not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256', 'identity'} or row['path'] != Path(entry['path']).name:
                    _reject('invalid-journal-object')
                files._rows([{key: row[key] for key in ('path', 'bytes', 'sha256')}])
                transaction._valid_identity(row['identity'])
    if active not in seen:
        _reject('invalid-journal-target')
    committed = files._target(directory, 'committed.json')
    if committed.exists():
        expected = _encoded(dict(schema=JOURNAL_SCHEMA, transactionId=journal['transactionId'],
                                 planSha256=_journal_digest(journal), outcome='committed'))
        witness = _stamp(files._target(directory, 'commit-pointer.json'))
        actual = _stamp(committed)
        if (actual['identity'] != witness['identity'] or actual['sha256'] != hashlib.sha256(expected).hexdigest()
                or actual['sha256'] != witness['sha256'] or actual['bytes'] != len(expected)):
            _reject('commit-outcome-changed')
        # A damaged mutable status cannot downgrade the independent durable outcome.
        journal = dict(journal, status='committed')
    elif journal['status'] == 'committed':
        _reject('commit-outcome-missing')
    return journal


def _rollback_preflight(root, directory, journal):
    from managed_base_resources import _validate_target_ancestors
    _validate_pointer(root, directory, journal['transactionId'], journal)
    for index, entry in enumerate(journal['changes']):
        target = files._target(Path(root), entry['path'])
        _validate_target_ancestors(target)
        old, new = entry['old'], entry['new']
        backup = files._target(directory, 'old-' + str(index))
        if backup.exists() and (old is None or not transaction._matches(backup, old, old['identity'])):
            _reject('rollback-backup-changed')
        already_old = old is not None and transaction._matches(target, old, old['identity'])
        if (not already_old and new is not None and transaction._matches(target, new, new['identity'])
                and not transaction._matches(files._target(directory, 'new-activation.json'), new, new['identity'])):
            _reject('rollback-new-pin-changed')
        if old is not None and not already_old and not transaction._matches(directory / ('old-' + str(index)), old, old['identity']):
            _reject('rollback-blocked')
        if target.exists() and not already_old and not (new is not None and transaction._matches(target, new, new['identity'])):
            _reject('rollback-blocked')


def _rollback(root, directory, journal):
    from managed_base_resources import _validate_target_ancestors
    _rollback_preflight(root, directory, journal)
    for index, entry in reversed(list(enumerate(journal['changes']))):
        target = files._target(Path(root), entry['path'])
        _validate_target_ancestors(target)
        old, new = entry['old'], entry['new']
        if old is not None and transaction._matches(target, old, old['identity']):
            continue
        if target.exists():
            if new is None or not files._rollback_published(target, tuple(new['identity']), new['sha256']):
                _reject('rollback-blocked')
            transaction._directory_sync(target.parent)
        if old is not None:
            transaction._move_owned(directory / ('old-' + str(index)), target, old, old['identity'])
    journal['status'] = 'rolled-back'
    _write_journal(directory, journal)
    _clear_pointer(root, directory, journal['transactionId'])


def _committed_preflight(root, directory, journal):
    from managed_base_resources import _validate_target_ancestors
    _validate_pointer(root, directory, journal['transactionId'], journal)
    for entry in journal['changes']:
        target = files._target(Path(root), entry['path'])
        _validate_target_ancestors(target)
        if entry['new'] is None:
            if target.exists():
                _reject('committed-target-changed')
        elif not transaction._matches(target, entry['new'], entry['new']['identity']):
            _reject('committed-target-changed')


def recover_locked(root):
    """Called under the shared writer/idle lease, before new work."""
    directory, journal = _load_pending(root)
    if journal['status'] == 'committed':
        _committed_preflight(root, directory, journal)
        _clear_pointer(root, directory, journal['transactionId'])
        return dict(status='author-snapshot-committed')
    _rollback(root, directory, journal)
    return dict(status='author-snapshot-rolled-back')


def _commit_outcome(directory, journal):
    data = _encoded(dict(schema=JOURNAL_SCHEMA, transactionId=journal['transactionId'],
                         planSha256=_journal_digest(journal), outcome='committed'))
    pin = files._target(directory, 'commit-pointer.json')
    transaction._write_new(pin, data)
    stamp = _stamp(pin)
    if stamp['sha256'] != hashlib.sha256(data).hexdigest() or stamp['bytes'] != len(data):
        _reject('commit-outcome-changed')
    os.link(pin, files._target(directory, 'committed.json'))
    transaction._directory_sync(directory)


def _activate_locked(root, package, value, *, expected_activation_sha, expected_legacy_sha, expected_activation_identity, expected_legacy_identity, cancelled=None, before_commit=None):
    current, current_sha = _read_activation(root, package)
    legacy = files._target(Path(root), f'work/map-packages/{package}.json')
    legacy_sha = files._sha(legacy) if legacy.exists() else None
    if current_sha != expected_activation_sha or legacy_sha != expected_legacy_sha:
        _reject('plan-changed-reinspect-required')
    from managed_base_resources import _validate_target_ancestors
    for relative in (activation_path(package), f'work/map-packages/{package}.json', PENDING):
        _validate_target_ancestors(files._target(Path(root), relative))
    activation = files._target(Path(root), activation_path(package))
    old_active = _stamp(activation) if activation.exists() else None
    old_legacy = _stamp(legacy) if legacy.exists() else None
    if ((old_active and (old_active['sha256'] != expected_activation_sha or old_active['identity'] != expected_activation_identity))
            or (old_legacy and (old_legacy['sha256'] != expected_legacy_sha or old_legacy['identity'] != expected_legacy_identity))
            or bool(old_active) != (expected_activation_sha is not None)
            or bool(old_legacy) != (expected_legacy_sha is not None)):
        _reject('plan-changed-reinspect-required')
    transaction_id = str(uuid.uuid4())
    directory = files._target(Path(root), STORE + '/transactions/' + transaction_id)
    directory.mkdir(parents=True, exist_ok=False)
    activation = files._target(Path(root), activation_path(package))
    new_pin = files._target(directory, 'new-activation.json')
    expected = _encoded(value)
    transaction._write_new(new_pin, expected)
    new_stamp = _stamp(new_pin)
    if new_stamp['sha256'] != hashlib.sha256(expected).hexdigest() or new_stamp['bytes'] != len(expected):
        _reject('staged-activation-changed')
    changes = []
    if old_legacy is not None:
        changes.append(dict(path=f'work/map-packages/{package}.json', old=old_legacy, new=None))
    changes.append(dict(path=activation_path(package), old=old_active,
                        new=dict(new_stamp, path=activation.name)))
    journal = dict(schema=JOURNAL_SCHEMA, transactionId=transaction_id,
                   rootIdentity=transaction._identity(Path(root).stat()), packageId=package,
                   status='prepared', changes=changes)
    _write_journal(directory, journal)
    pointer = files._target(directory, 'pending-pointer.json')
    transaction._write_new(pointer, _encoded(dict(schema=JOURNAL_SCHEMA, transactionId=transaction_id,
                                                   planSha256=_journal_digest(journal))))
    os.link(pointer, files._target(Path(root), PENDING))
    transaction._directory_sync(files._target(Path(root), PENDING).parent)
    try:
        files._check_cancelled(cancelled)
        for entry in changes:
            target = files._target(Path(root), entry['path'])
            _validate_target_ancestors(target)
            if entry['old'] is not None and not transaction._matches(target, entry['old'], entry['old']['identity']):
                _reject('previous-activation-changed')
            if entry['new'] is not None and not transaction._matches(new_pin, entry['new'], entry['new']['identity']):
                _reject('staged-activation-changed')
        for index, entry in enumerate(changes):
            target = files._target(Path(root), entry['path'])
            if entry['old'] is not None:
                transaction._move_owned(target, directory / ('old-' + str(index)), entry['old'], entry['old']['identity'])
            if entry['new'] is not None:
                if not transaction._matches(new_pin, entry['new'], entry['new']['identity']):
                    _reject('staged-activation-changed')
                target.parent.mkdir(parents=True, exist_ok=True)
                files._no_links(target.parent)
                files._check_cancelled(cancelled)
                os.link(new_pin, target)
                transaction._directory_sync(target.parent)
                if not transaction._matches(target, entry['new'], entry['new']['identity']):
                    _reject('activation-changed-during-publish')
        files._check_cancelled(cancelled)
        if before_commit is not None:
            before_commit()
        # The callback may perform substantial validation. Do not let changes
        # during that interval become a committed activation or claimed backup.
        for index, entry in enumerate(changes):
            target = files._target(Path(root), entry['path'])
            _validate_target_ancestors(target)
            if entry['old'] is not None and not transaction._matches(
                    directory / ('old-' + str(index)), entry['old'], entry['old']['identity']):
                _reject('backup-changed-before-commit')
            if entry['new'] is None:
                if target.exists():
                    _reject('activation-changed-before-commit')
            elif (not transaction._matches(target, entry['new'], entry['new']['identity'])
                    or not transaction._matches(new_pin, entry['new'], entry['new']['identity'])):
                _reject('activation-changed-before-commit')
        files._check_cancelled(cancelled)
        _commit_outcome(directory, journal)
        journal['status'] = 'committed'
        _write_journal(directory, journal)
    except Exception:
        directory, durable = _load_pending(root)
        if durable['status'] == 'committed':
            _reject('commit-outcome-uncertain')
        _rollback(root, directory, durable)
        raise
    _clear_pointer(root, directory, transaction_id)
    return transaction_id


def install_archive_locked(archive, digest, root, result, *, cancelled=None):
    """The shared archive parser already validated all bytes, CRCs and bindings."""
    files._check_cancelled(cancelled)
    package = result['packageId']
    receipt = {key: result[key] for key in ('schema', 'packageId', 'season', 'revision', 'files', 'requiresFiles')}
    receipt['archiveSha256'] = digest
    content_id = _digest(receipt)
    if managed(root, package):
        try:
            previous = SnapshotPackage(root, package)
            if previous.manifest['contentId'] == content_id:
                for row in previous.owned.values():
                    previous.read(row['path'])
                return dict(result, archiveSha256=digest, added=0, replaced=0, receiptPreserved=True,
                            alreadyPresent=len(result['files']), status='author-already-installed', liveImport=True)
        except (OSError, ValueError):
            pass
    if shutil.disk_usage(root).free < result['bytes'] + MAX_METADATA:
        _reject('insufficient-free-space')
    snapshot_id = uuid.uuid4().hex
    directory = files._target(Path(root), _version_path(package, snapshot_id))
    directory.mkdir(parents=True, exist_ok=False)
    from resource_transaction import preflight_hardlinks
    preflight_hardlinks(root, [activation_path(package), PENDING])
    for index, row in enumerate(sorted(result['files'], key=lambda row: row['path'])):
        files._check_cancelled(cancelled)
        data = archive.read('data/' + row['path'])
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            _reject('archive-changed')
        transaction._write_new(files._target(directory, _stored_name(index, row)), data)
    manifest = dict(schema=SNAPSHOT_SCHEMA, packageId=package, snapshotId=snapshot_id,
                    receipt=receipt, contentId=content_id)
    transaction._write_new(files._target(directory, 'manifest.json'), _encoded(manifest))
    activation = dict(schema=ACTIVATION_SCHEMA, packageId=package, season=result['season'], snapshotId=snapshot_id,
                      manifestSha256=_digest(manifest), generation=uuid.uuid4().hex)
    # Recheck the dependency view and old activation after durable staging, not
    # merely before a potentially long archive copy. A changed plan needs consent.
    current = inspect_validated_archive(receipt, archive, root,
        files._rows(result['files']), generic.dependency_rows(result['requiresFiles'], result['season']))
    if current['snapshotPlanSha256'] != result['snapshotPlanSha256']:
        _reject('plan-changed-reinspect-required')
    transaction_id = _activate_locked(root, package, activation,
        expected_activation_sha=result['expectedActivationSha256'],
        expected_legacy_sha=result['previousReceiptSha256'],
        expected_activation_identity=result['expectedActivationIdentity'],
        expected_legacy_identity=result['previousReceiptIdentity'], cancelled=cancelled)
    return dict(result, archiveSha256=digest, snapshotId=snapshot_id, contentId=content_id,
                snapshotTransactionId=transaction_id, added=len(result['missingFiles']),
                replaced=len(result['replaceFiles']), status='author-installed-live', liveImport=True,
                restartRequired=False, retainedPreviousVersions=True)


def verified(root, package, expected_sha256=None, *, cancelled=None):
    activation, _ = _read_activation(root, package)
    if activation['snapshotId'] is None:
        return dict(status='not-installed', retainedPreviousVersions=True)
    view = SnapshotPackage(root, package)
    files._check_cancelled(cancelled)
    document = view.document()
    files._check_cancelled(cancelled)
    view.assert_active()
    digest = view.manifest['receipt']['archiveSha256']
    status = ('update-available' if expected_sha256 and digest != expected_sha256 else
              'partially-compatible' if document['inactiveLayers'] and document['layers'] else
              'incompatible' if not document['layers'] else 'installed')
    return dict(status=status, archiveSha256=digest, snapshotId=activation['snapshotId'],
                layerStates=document['layerStates'], inactiveLayers=document['inactiveLayers'],
                storageFormat=SNAPSHOT_SCHEMA, retainedPreviousVersions=True)


def inspect_deactivation(root, package):
    activation, digest = _read_activation(root, package)
    if activation is None or activation['snapshotId'] is None:
        _reject('not-active')
    # Deactivation only switches the record. Corrupt bytes and unknown additions
    # are retained verbatim; they never gain deletion authority from a receipt.
    plan = dict(packageId=package, title=package, receiptSha256=digest, files=[],
        missingFiles=[], preservedCoreFiles=0, fileCount=0, bytes=0, modifiedFiles=[],
        restartRequired=False, storageFormat=SNAPSHOT_SCHEMA, retainedPreviousVersions=True,
        snapshotId=activation['snapshotId'], season=activation['season'],
        activationIdentity=_stamp(files._target(Path(root), activation_path(package)))['identity'])
    plan['planSha256'] = _digest(plan)
    return plan


def deactivate_locked(root, plan, *, cancelled=None):
    if inspect_deactivation(root, plan['packageId']) != plan:
        _reject('plan-changed-reinspect-required')
    transaction_id = _activate_locked(root, plan['packageId'],
        dict(schema=ACTIVATION_SCHEMA, packageId=plan['packageId'], season=plan['season'],
             snapshotId=None, manifestSha256=None, generation=uuid.uuid4().hex),
        expected_activation_sha=plan['receiptSha256'], expected_legacy_sha=None,
        expected_activation_identity=plan['activationIdentity'], expected_legacy_identity=None, cancelled=cancelled)
    return dict(status='author-removed-live', packageId=plan['packageId'], removedFiles=0,
        preservedCoreFiles=0, backupPath=str(Path(root)/STORE), restartRequired=False,
        snapshotTransactionId=transaction_id, retainedPreviousVersions=True)


def recovery_plan_locked(root):
    directory, journal = _load_pending(root)
    if journal['status'] != 'committed':
        _rollback_preflight(root, directory, journal)
    else:
        _committed_preflight(root, directory, journal)
    relatives = [PENDING, (directory/'journal.json').relative_to(root).as_posix(),
        (directory/'pending-pointer.json').relative_to(root).as_posix(),
        (directory/'new-activation.json').relative_to(root).as_posix(),
        (directory/'committed.json').relative_to(root).as_posix(),
        (directory/'commit-pointer.json').relative_to(root).as_posix()]
    for index, entry in enumerate(journal['changes']):
        relatives.extend([entry['path'], (directory/('old-'+str(index))).relative_to(root).as_posix()])
    from managed_base_resources import _validate_target_ancestors
    stamps = {}
    for relative in relatives:
        path = files._target(Path(root), relative)
        _validate_target_ancestors(path)
        stamps[relative] = _stamp(path) if path.exists() else None
    return dict(transactionId=journal['transactionId'], packageId=journal['packageId'],
        status='author-snapshot-pending', pending=True, fileCount=len(journal['changes']),
        blockedPaths=[], planSha256=_digest(stamps))
